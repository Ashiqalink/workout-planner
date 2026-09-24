"""TypeSafe judge: typed confidence for one session and for a week of them.

This is the third model surface in FitTrack and, like the other two, it is
selection-only. TypeSafe's System One model (Jev) never writes prose: every
question here is a Score (a position on human-written levels), a Choice (one
of the orderings the engine already built) or a Noul (a probability of yes).
Code owns the rubric, the weights, the thresholds and every sentence the user
reads, so the truth boundary in learn/07-truth-boundary.md holds unchanged.

What the judge is for
---------------------
The rule engine already builds a complete session. What it cannot do is say
how *good* that session is by the standards a coach would apply — exercise
order, balance across patterns, whether it fits the requested tier, whether it
loads muscles that are still tired. Those are semantic judgments over the
engine's own output, which is exactly what a System One model is for.

The knowledge behind every rubric level is written down, with sources, in
learn/11-program-design.md. Change the levels there and here together.

Pure module: no Flask, no database. `app.py` builds the state from records it
already has and decides what to do with the numbers that come back.
"""
import collections
import hashlib
import http.client
import itertools
import json
import logging
import threading
import time
import urllib.parse

import config

logger = logging.getLogger(__name__)

TYPESAFE_URL = 'https://api.typesafe.ai/v1/systemone'
TYPESAFE_MODEL = 'jev-latest'
DEFAULT_TIMEOUT = 8.0

# Identical state + questions get identical answers back, so a repeated
# request (the same session generated twice, a week re-laid-out) costs nothing.
CACHE_SIZE = 256

# Confidence bands the app acts on. Bands, not one number: a session in
# 'review' is still served, it just says why it is not sure. Thresholds are
# a policy choice — see the "Thresholds scale with risk" note in the TypeSafe
# confidence docs — and they live here so a test can pin them.
BAND_SOLID = 0.75
BAND_REVIEW = 0.5

# The judge may replace the engine's ordering only when another candidate's
# composite beats the current one by at least this much. Measured live, a
# Choice over whole orderings came back nearly flat (0.35/0.39/0.26) while the
# per-candidate Scores separated the same orderings by 0.2 — so the decision
# rests on the rubric, not on a coin-flip Choice.
REORDER_MIN_MARGIN = 0.05

# A week layout is a Choice over a handful of engine-priced candidates; it
# needs this much concentration before it overrides the cheapest layout.
REORDER_MIN_CONFIDENCE = 0.6

# The order every session is measured against. Power first while the nervous
# system is fresh, then the multi-joint lifts that carry most of the stimulus,
# then single-joint and core accessories, then conditioning once form no longer
# matters as much, with mobility at the ends (NSCA; learn/11).
PHASE_ORDER = ('power', 'compound', 'isolation', 'conditioning', 'mobility',
               'cognitive')


# ── Transport ────────────────────────────────────────────────────────


def api_key():
    return config.TYPESAFE_API_KEY


def available():
    """True when a key is configured. Without one every judge call is a no-op."""
    return bool(api_key())


# One kept-alive HTTPS connection. Measured from here, the TCP + TLS handshake
# to api.typesafe.ai is ~640 ms of a ~1 000 ms cold call; reusing the socket
# brings a judge call down to ~400 ms. A stale socket (the server closed it
# while idle) fails on first use and is replaced once.
_conn = None
_conn_lock = threading.Lock()


def _close():
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None


def _post(payload, timeout):
    """POST the encoded body; return (status, body bytes). Raises on transport failure."""
    global _conn
    target = urllib.parse.urlparse(TYPESAFE_URL)
    headers = {'Authorization': f'Bearer {api_key()}',
               'Content-Type': 'application/json'}
    with _conn_lock:
        for attempt in (0, 1):
            try:
                if _conn is None:
                    _conn = http.client.HTTPSConnection(target.hostname, target.port,
                                                        timeout=timeout)
                elif _conn.sock is not None:
                    _conn.sock.settimeout(timeout)
                _conn.request('POST', target.path, payload, headers)
                response = _conn.getresponse()
                return response.status, response.read()
            except (http.client.HTTPException, OSError):
                _close()
                if attempt:
                    raise
    return None, b''


_cache = collections.OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(payload):
    return hashlib.sha1(payload).hexdigest()


def _cache_get(key):
    with _cache_lock:
        answers = _cache.get(key)
        if answers is not None:
            _cache.move_to_end(key)
            return json.loads(json.dumps(answers))  # callers mutate; hand out a copy
    return None


def _cache_put(key, answers):
    with _cache_lock:
        _cache[key] = json.loads(json.dumps(answers))
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


def system_one(state, questions, timeout=DEFAULT_TIMEOUT, retries=2):
    """POST one state and a map of questions; return the `answers` map or None.

    429 and 529 are retried with a short backoff, as the API docs ask. Any other
    failure logs and returns None, and callers treat None as "no judgment" —
    the engine's plan is served exactly as it would have been without a key.
    """
    if not available():
        return None
    payload = json.dumps({'state': state, 'model': TYPESAFE_MODEL,
                          'questions': questions}, sort_keys=True).encode('utf-8')
    key = _cache_key(payload)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    delay = 0.5
    for attempt in range(retries + 1):
        try:
            status, raw = _post(payload, timeout)
        except Exception as e:  # timeouts, DNS, reset sockets
            logger.warning('TypeSafe call failed, serving the engine plan unjudged: %s', e)
            return None
        if status in (429, 529) and attempt < retries:
            time.sleep(delay)
            delay *= 2
            continue
        if status != 200:
            logger.warning('TypeSafe HTTP %s: %s', status, raw[:200])
            return None
        try:
            body = json.loads(raw.decode('utf-8'))
        except ValueError as e:
            logger.warning('TypeSafe returned bad JSON, serving the engine plan unjudged: %s', e)
            return None
        answers = body.get('answers') if isinstance(body, dict) else None
        if isinstance(answers, dict):
            answers['_model'] = body.get('model', TYPESAFE_MODEL)
            _cache_put(key, answers)
            return answers
        logger.warning('TypeSafe returned no answers: %s', body)
        return None
    return None


# ── Session rubric ───────────────────────────────────────────────────
#
# Every level describes a situation the model can match the state against —
# never a degree, never a number (the Score docs are explicit that "level 2 of
# 3" means nothing to the model). Each Score measures one thing; the weights
# below combine them. The wording paraphrases the sourced guidance in learn/11.

ORDER_LEVELS = [
    'A conditioning block, cardio drill or single-joint accessory comes before '
    'the main multi-joint movement, so the exercise with the biggest stimulus '
    'is done tired',
    'Multi-joint movements open the session but accessories or conditioning '
    'are interleaved between them',
    'All multi-joint movements come before accessories and conditioning, but a '
    'jump or other explosive movement is placed after fatiguing work instead of '
    'first',
    'Explosive or jump work first when present, then multi-joint movements, '
    'then single-joint and core accessories, then conditioning last, with '
    'mobility only at the start or end',
]

BALANCE_LEVELS = [
    'Two consecutive strength exercises are the same movement pattern (two '
    'presses, two squats), or one body region is loaded by most of the '
    'exercises in the session',
    'No consecutive strength exercises share a movement pattern, but '
    'consecutive exercises share a body region at least once',
    'No consecutive exercises share a body region, no consecutive strength '
    'exercises share a movement pattern, and no single region takes most of '
    'the session',
]

LEVEL_FIT_LEVELS = [
    'Several exercises are a tier harder or easier than the requested '
    'difficulty, or the set counts are out of keeping with that tier',
    'Almost every exercise sits at the requested tier, with one that is a tier '
    'off',
    'Every exercise sits at the requested tier or one step easier, and the set '
    'counts match that tier',
]


def session_questions(with_history):
    questions = {
        'order': {
            'type': 'score',
            'instructions': 'How well is the sequence of exercises in `session` '
                            'ordered for a training session? Judge by each '
                            'exercise\'s `phase` and `position`.',
            'criteria': ORDER_LEVELS,
        },
        'balance': {
            'type': 'score',
            'instructions': 'How well does `session` spread its work across '
                            'movement patterns and body regions? When '
                            '`requested.focus` names a body area, only the '
                            'regions of that area need to be covered; when it '
                            'is "none", push, pull, legs and core all count.',
            'criteria': BALANCE_LEVELS,
        },
        'level_fit': {
            'type': 'score',
            'instructions': 'How well do the exercises\' `difficulty` labels and '
                            '`sets` match `requested.difficulty`?',
            'criteria': LEVEL_FIT_LEVELS,
        },
    }
    if with_history:
        questions['recovery'] = {
            'type': 'noul',
            'instructions': 'Does `session` put most of its working sets on body '
                            'regions that are NOT listed in '
                            '`recently_trained_regions`?',
            'criteria': {
                'true': 'Most sets go to regions that had a rest since the last '
                        'sessions',
                'false': 'Most sets go to regions listed as recently trained',
            },
        }
    return questions


SESSION_WEIGHTS = {'order': 0.35, 'balance': 0.30, 'level_fit': 0.20,
                   'recovery': 0.15}


TIER_RANK = {'beginner': 0, 'intermediate': 1, 'advanced': 2}
_NEUTRAL_REGIONS = {'full body', 'cardio', 'neural', 'cognitive', 'mobility'}
_STRENGTH_PHASES = {'power', 'compound', 'isolation'}


def _order_level(described):
    """The engine's reading of ORDER_LEVELS for a described sequence."""
    phases = [d.get('phase') for d in described]
    body = [(i, p) for i, p in enumerate(phases) if p not in ('mobility', 'cognitive')]
    compounds = [i for i, p in body if p == 'compound']
    accessories = [i for i, p in body if p in ('isolation', 'conditioning')]
    powers = [i for i, p in body if p == 'power']
    if compounds and any(a < compounds[0] for a in accessories):
        return 0
    if compounds and any(a < compounds[-1] for a in accessories):
        return 1
    mains = compounds or accessories
    if powers and mains and any(m < powers[-1] for m in mains):
        return 2
    # Conditioning before an isolation block is still "accessories first".
    conditioning = [i for i, p in body if p == 'conditioning']
    isolation = [i for i, p in body if p == 'isolation']
    if conditioning and isolation and conditioning[0] < isolation[-1]:
        return 2
    mobility = [i for i, p in enumerate(phases) if p == 'mobility']
    if mobility and any(0 < i < len(phases) - 1 for i in mobility):
        return 2
    return 3


def _balance_level(described):
    """The engine's reading of BALANCE_LEVELS for a described sequence."""
    if len(described) < 2:
        return 2
    # Pattern repeats count between strength blocks only: three agility
    # drills in a row are all "locomotion" and that is what a conditioning
    # block looks like. Region repeats count everywhere.
    patterns = [d.get('movement_pattern') if d.get('phase') in _STRENGTH_PHASES else None
                for d in described]
    regions = [set(d.get('regions') or ()) - _NEUTRAL_REGIONS for d in described]
    if any(a and a == b for a, b in zip(patterns, patterns[1:])):
        return 0
    counts = collections.Counter(r for rs in regions for r in rs)
    if len(described) >= 3 and counts and len(counts) > 1 and             max(counts.values()) > len(described) / 2:
        return 0
    if any(a & b for a, b in zip(regions, regions[1:])):
        return 1
    return 2


def _level_fit_level(described, difficulty):
    """The engine's reading of LEVEL_FIT_LEVELS: at tier or one step easier."""
    want = TIER_RANK.get(str(difficulty or 'beginner').lower(), 0)
    off = 0
    for d in described:
        have = TIER_RANK.get(d.get('difficulty') or '', want)
        if have > want or have < want - 1:
            off += 1
    return 2 if off == 0 else 1 if off == 1 else 0


def local_rubric(described, difficulty):
    """Score a described sequence the way the rubric would, in code.

    This is the engine's estimate, not the judge's: it can count phases and
    adjacent repeats exactly, but it cannot weigh how *much* two exercises
    overlap the way the model does. It exists so the engine can rank its own
    orderings before asking, and ask about one when they are far apart.
    """
    if not described:
        return None
    dims = {
        'order': _order_level(described) / (len(ORDER_LEVELS) - 1),
        'balance': _balance_level(described) / (len(BALANCE_LEVELS) - 1),
        'level_fit': _level_fit_level(described, difficulty) / (len(LEVEL_FIT_LEVELS) - 1),
    }
    weight_total = sum(SESSION_WEIGHTS[k] for k in dims)
    composite = sum(SESSION_WEIGHTS[k] * v for k, v in dims.items()) / weight_total
    return {'score': round(composite, 3), 'dimensions': dims}


def rank_orderings(candidates, difficulty):
    """Order candidate labels by the engine's rubric, best first; ties keep offer order."""
    scored = {label: (local_rubric(described, difficulty) or {}).get('score', 0.0)
              for label, described in candidates.items()}
    labels = list(candidates)
    return sorted(labels, key=lambda l: (-scored[l], labels.index(l))), scored


def describe_exercise(exercise, position, phase, pattern, regions):
    """One exercise as the judge sees it: engine facts only."""
    return {
        'position': position,
        'name': exercise.get('name'),
        'phase': phase,
        'movement_pattern': pattern,
        'regions': sorted(regions),
        'difficulty': str(exercise.get('difficulty') or '').lower() or None,
        'sets': int(exercise.get('sets') or 0) or None,
        'set_seconds': int(exercise.get('set_seconds') or 0) or None,
        'rest_seconds': int(exercise.get('rest_seconds') or 0) or None,
    }


def session_state(described, difficulty, minutes, focus='', history=None):
    return {
        'requested': {
            'difficulty': str(difficulty or 'beginner').lower(),
            'minutes': minutes,
            'focus': focus or 'none',
        },
        'recently_trained_regions': sorted(history or []),
        'session': described,
    }


def _score_reading(answer, levels):
    """Normalise one Score answer and name the level it lands nearest."""
    top = max(1, len(levels) - 1)
    score = float(answer.get('score') or 0.0)
    nearest = min(len(levels) - 1, max(0, int(round(score))))
    return {
        'score': round(score / top, 3),
        'level': nearest,
        'top': top,
        'confidence': round(float(answer.get('confidence') or 0.0), 3),
        'label': levels[nearest],
    }


def band_for(score):
    if score >= BAND_SOLID:
        return 'solid'
    if score >= BAND_REVIEW:
        return 'review'
    return 'weak'


def summarise_session(answers, prefix=''):
    """Combine one session's answers into a composite with its breakdown.

    Weights are the policy and live in SESSION_WEIGHTS; the raw readings are
    kept alongside so a different policy (or the settings page one day) can
    re-weight without re-asking. Returns None when the answers are missing.
    """
    if not answers:
        return None
    levels = {'order': ORDER_LEVELS, 'balance': BALANCE_LEVELS,
              'level_fit': LEVEL_FIT_LEVELS}
    dims = {}
    for key, rubric in levels.items():
        answer = answers.get(prefix + key)
        if answer and answer.get('type') == 'score':
            dims[key] = _score_reading(answer, rubric)
    recovery = answers.get(prefix + 'recovery')
    if recovery and recovery.get('type') == 'noul':
        p = float(recovery.get('noul') or 0.0)
        dims['recovery'] = {
            'score': round(p, 3), 'level': int(p >= 0.5), 'top': 1,
            'confidence': round(abs(p - 0.5) * 2, 3),
            'label': ('Most sets go to regions that had a rest'
                      if p >= 0.5 else 'Most sets hit regions trained recently'),
        }
    if not dims:
        return None
    weight_total = sum(SESSION_WEIGHTS[k] for k in dims)
    composite = sum(SESSION_WEIGHTS[k] * dims[k]['score'] for k in dims) / weight_total
    composite = round(composite, 3)
    return {
        'score': composite,
        'band': band_for(composite),
        'dimensions': dims,
        'model': answers.get('_model', TYPESAFE_MODEL),
    }


def describe_confidence(summary):
    """The sentence a user reads. Built from rubric text and engine numbers only."""
    if not summary:
        return ''
    pct = int(round(summary['score'] * 100))
    lead = {'solid': 'Solid plan', 'review': 'Worth a look',
            'weak': 'Weak plan'}[summary['band']]
    parts = [f'{lead}: {pct}% by the coaching rubric.']
    names = {'order': 'Order', 'balance': 'Balance', 'level_fit': 'Level',
             'recovery': 'Recovery'}
    for key in ('order', 'balance', 'level_fit', 'recovery'):
        dim = summary['dimensions'].get(key)
        if not dim:
            continue
        # Only the dimensions that pulled the score down get a sentence; a
        # perfect reading is not news.
        if dim['level'] < dim['top']:
            parts.append(f"{names[key]}: {dim['label'].lower()}.")
    return ' '.join(parts)


# ── Choosing an order ────────────────────────────────────────────────


def ordering_questions(labels, with_history):
    """The session rubric asked once per candidate ordering, in one request.

    The questions are independent and run in parallel, so judging three
    orderings costs about the same wall clock as judging one. Code then ranks
    the candidates by composite and reads only the winner's answers.
    """
    questions = {}
    for label in labels:
        for key, question in session_questions(with_history).items():
            scoped = dict(question)
            scoped['instructions'] = (question['instructions']
                                      .replace('`session`', f'`candidates.{label}`'))
            questions[f'{label}.{key}'] = scoped
    return questions


def choose_order(candidates, current, difficulty, minutes, focus='', history=None,
                 timeout=DEFAULT_TIMEOUT):
    """Score several orderings of the same exercises and say which to serve.

    `candidates` maps a label to an already-described exercise list (see
    describe_exercise); `current` is the label the engine would serve anyway.
    Returns None when the judge is unavailable, otherwise
    {'chosen': label or None, 'preferred': best label, 'margin', 'scores',
     'summaries': {label: summary}}. `chosen` is None unless another candidate
    beats `current` by REORDER_MIN_MARGIN — ties and near-ties keep the
    engine's order, because reordering for a hair's breadth is churn.
    """
    labels = list(candidates)
    if not labels or not available():
        return None
    state = session_state([], difficulty, minutes, focus, history)
    del state['session']
    state['candidates'] = candidates
    answers = system_one(state, ordering_questions(labels, bool(history)), timeout=timeout)
    if not answers:
        return None
    summaries = {label: summarise_session(answers, prefix=f'{label}.') for label in labels}
    scores = {label: s['score'] for label, s in summaries.items() if s}
    if not scores:
        return None
    # Ties break towards the current order, then towards the offer order.
    ranked = sorted(scores, key=lambda l: (-scores[l], l != current, labels.index(l)))
    preferred = ranked[0]
    baseline = scores.get(current, scores[preferred])
    margin = round(scores[preferred] - baseline, 3)
    return {
        'chosen': preferred if (preferred != current and margin >= REORDER_MIN_MARGIN) else None,
        'preferred': preferred,
        'margin': margin,
        'scores': scores,
        'summaries': summaries,
    }


def judge_session(described, difficulty, minutes, focus='', history=None,
                  timeout=DEFAULT_TIMEOUT):
    """Score one already-ordered session. None when the judge is unavailable."""
    if not described or not available():
        return None
    answers = system_one(session_state(described, difficulty, minutes, focus, history),
                         session_questions(bool(history)), timeout=timeout)
    return summarise_session(answers)


# ── Orchestrating a week ─────────────────────────────────────────────
#
# A plan is several sessions on named days. The engine enumerates the ways to
# lay them out, prices each with the 48-hour rule (the same region on
# consecutive training days is the thing to avoid) and hands the few cheapest
# to the judge as a Choice. The judge never invents a day or a session.

# ── Region tagging: one Noul per region per exercise, fanned out ──────
#
# The library's muscle labels are loose: "Core, shoulders, back" makes a side
# plank read as core + push + pull, and the balance rules then charge a press
# for following it. One yes/no per region, judged from the name, description
# and muscle list, gives the engine a region set it can trust. The
# probabilities are stored; the yes threshold is applied in code, so changing
# it never re-runs inference.

REGION_TAGS = ('push', 'pull', 'legs', 'core', 'cardio', 'neural', 'mobility',
               'cognitive')
REGION_DEFINITIONS = {
    'push': 'pressing or pushing with the arms — chest, front shoulders and '
            'triceps do the work (push-ups, dips, presses)',
    'pull': 'pulling with the arms or holding the back tight — lats, upper '
            'back, rear shoulders and biceps do the work (rows, pull-ups, '
            'supermans)',
    'legs': 'the hips, thighs or calves produce the movement or hold the load '
            '(squats, lunges, bridges, calf raises, running)',
    'core': 'the trunk resists or produces the movement — abs, obliques or '
            'lower back bracing is the point of the exercise, not an '
            'incidental stabiliser',
    'cardio': 'sustained or repeated effort whose purpose is to raise heart '
              'rate and breathing (runs, intervals, jumping jacks, circuits)',
    'neural': 'balance, coordination, agility, reaction or quick footwork is '
              'the main demand (ladder drills, cone drills, single-leg balance)',
    'mobility': 'moving a joint through its range or stretching is the point '
                '(arm circles, hip openers, stretches, foam rolling)',
    'cognitive': 'a mental task — memory, attention, arithmetic, pattern '
                 'recognition — with little physical load',
}
REGION_YES = 0.5
REGION_BATCH = 12  # exercises per request; 8 Nouls each


def region_questions(count):
    """count × len(REGION_TAGS) Nouls over `exercises[i]`; ids are `i:tag`."""
    questions = {}
    for i in range(count):
        for tag in REGION_TAGS:
            questions[f'{i}:{tag}'] = {
                'type': 'noul',
                'instructions': (
                    f'Read `exercises[{i}]` — its name, category, description and '
                    f'muscle list. Is this a "{tag}" exercise, meaning: '
                    f'{REGION_DEFINITIONS[tag]}? Say yes only when that is a real '
                    f'part of the work, not a muscle that merely stabilises.'),
                'criteria': {
                    'true': f'{tag} is a meaningful part of what this exercise trains',
                    'false': f'{tag} is not meaningfully trained by this exercise',
                },
            }
    return questions


def describe_for_tagging(exercise):
    return {
        'name': exercise.get('name'),
        'category': exercise.get('category'),
        'description': exercise.get('description') or '',
        'muscles': exercise.get('target_muscles') or '',
    }


def tag_regions(exercises, timeout=DEFAULT_TIMEOUT):
    """{index: {tag: probability}} for each exercise the judge answered.

    Batched REGION_BATCH at a time; a batch that fails is simply absent from
    the result, so callers tag what they can and keep the heuristic for the
    rest. No key → empty dict.
    """
    tagged = {}
    if not available():
        return tagged
    for start in range(0, len(exercises), REGION_BATCH):
        batch = exercises[start:start + REGION_BATCH]
        state = {'exercises': [describe_for_tagging(e) for e in batch]}
        answers = system_one(state, region_questions(len(batch)), timeout=timeout)
        if not answers:
            continue
        for i in range(len(batch)):
            probs = {}
            for tag in REGION_TAGS:
                answer = answers.get(f'{i}:{tag}') or {}
                try:
                    probs[tag] = round(float(answer.get('noul')), 3)
                except (TypeError, ValueError):
                    probs = None
                    break
            if probs is not None:
                tagged[start + i] = probs
    return tagged


def regions_from_probs(probs, threshold=REGION_YES):
    """The region set a stored probability map means, at today's threshold."""
    if not isinstance(probs, dict):
        return set()
    out = set()
    for tag in REGION_TAGS:
        try:
            if float(probs.get(tag, 0.0)) >= threshold:
                out.add(tag)
        except (TypeError, ValueError):
            continue
    return out


MAX_PLAN_CANDIDATES = 4
DAY_INDEX = {d: i for i, d in enumerate(
    ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'))}

SPACING_LEVELS = [
    'The same body region is loaded hard on two consecutive training days, '
    'or the hardest sessions are stacked without a rest day between them',
    'No region repeats on consecutive days, but the week front-loads or '
    'back-loads its hardest sessions',
    'Regions alternate day to day, every hard session follows a rest day or '
    'an easier day, and the week ends on a lighter session',
]

VARIETY_LEVELS = [
    'The week repeats one kind of session and leaves a main movement pattern '
    'or region untouched',
    'Most patterns and regions appear at least once, one is missing',
    'Push, pull, legs, core and conditioning each appear at least once and no '
    'session is a copy of another',
]

PROGRESSION_LEVELS = [
    'Intensity or duration jumps sharply, or drops for no reason, between '
    'consecutive weeks',
    'Weeks are mostly the same with no planned increase',
    'Each week is a small step up from the last in duration or intensity, '
    'with a lighter week before the next increase',
]


def _day_gap(day_a, day_b):
    a, b = DAY_INDEX.get(day_a[:3].lower(), 0), DAY_INDEX.get(day_b[:3].lower(), 0)
    return (b - a) % 7 or 7


def schedule_cost(days, sessions):
    """Deterministic price of one layout: region repeats within 48 hours."""
    cost = 0.0
    for (day_a, sess_a), (day_b, sess_b) in zip(zip(days, sessions), zip(days[1:], sessions[1:])):
        gap = _day_gap(day_a, day_b)
        shared = set(sess_a.get('regions') or ()) & set(sess_b.get('regions') or ())
        shared -= {'full body', 'mobility', 'cognitive'}
        if gap < 2 and shared:
            cost += 2.0 * len(shared)
        elif gap < 2 and (sess_a.get('load') == 'hard' and sess_b.get('load') == 'hard'):
            cost += 1.0
    return cost


def candidate_schedules(days, sessions, limit=MAX_PLAN_CANDIDATES):
    """The cheapest distinct layouts of `sessions` over `days`, cheapest first."""
    if not days or not sessions:
        return []
    n = min(len(days), len(sessions))
    days = list(days[:n])
    seen, scored = set(), []
    for perm in itertools.permutations(range(len(sessions)), n):
        key = tuple(sessions[i].get('name') for i in perm)
        if key in seen:
            continue
        seen.add(key)
        laid = [sessions[i] for i in perm]
        scored.append((schedule_cost(days, laid), perm, laid))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [
        {'label': f'plan_{chr(ord("a") + i)}', 'cost': cost,
         'days': [{'day': d, 'session': s} for d, s in zip(days, laid)]}
        for i, (cost, _perm, laid) in enumerate(scored[:limit])
    ]


def week_questions(labels):
    questions = {
        'best_week': {
            'type': 'choice',
            'instructions': 'Which candidate in `candidates` lays these sessions '
                            'out best across the week? The best layout never '
                            'loads the same body region on consecutive days, '
                            'puts the hardest sessions after a rest day, and '
                            'ends the week lighter.',
            'criteria': {label: None for label in labels},
        },
    }
    for label in labels:
        questions[f'{label}.spacing'] = {
            'type': 'score',
            'instructions': f'How well does `candidates.{label}` space its sessions '
                            'for recovery, judging by each session\'s `regions`, '
                            '`load` and its `day`?',
            'criteria': SPACING_LEVELS,
        }
    questions['variety'] = {
        'type': 'score',
        'instructions': 'Across the sessions in any one candidate (they all hold '
                        'the same sessions), how much of the body and how many '
                        'kinds of work does the week cover?',
        'criteria': VARIETY_LEVELS,
    }
    return questions


def orchestrate_week(days, sessions, timeout=DEFAULT_TIMEOUT):
    """Lay sessions over days; the judge picks among the engine's best layouts.

    Each session: {'name', 'regions': [...], 'phases': [...], 'load': 'hard'|
    'moderate'|'easy', 'minutes'}. Returns the chosen layout, its confidence,
    the alternatives, and per-layout spacing readings. Without a key the
    cheapest deterministic layout is returned with `judged: False`.
    """
    candidates = candidate_schedules(days, sessions)
    if not candidates:
        return None
    result = {'judged': False, 'chosen': candidates[0], 'alternatives': candidates[1:],
              'confidence': None, 'spacing': {}, 'variety': None}
    if not available():
        return result
    labels = [c['label'] for c in candidates]
    state = {'candidates': {c['label']: c['days'] for c in candidates}}
    answers = system_one(state, week_questions(labels), timeout=timeout)
    if not answers:
        return result
    choice = answers.get('best_week') or {}
    confidence = float(choice.get('confidence') or 0.0)
    by_label = {c['label']: c for c in candidates}
    pick = choice.get('choice')
    if pick in by_label and confidence >= REORDER_MIN_CONFIDENCE:
        result['chosen'] = by_label[pick]
        result['alternatives'] = [c for c in candidates if c['label'] != pick]
    result['judged'] = True
    result['confidence'] = round(confidence, 3)
    result['probabilities'] = {k: round(float(v), 3)
                               for k, v in (choice.get('probabilities') or {}).items()}
    for label in labels:
        answer = answers.get(f'{label}.spacing')
        if answer:
            result['spacing'][label] = _score_reading(answer, SPACING_LEVELS)
    if answers.get('variety'):
        result['variety'] = _score_reading(answers['variety'], VARIETY_LEVELS)
    result['model'] = answers.get('_model', TYPESAFE_MODEL)
    return result


def judge_program_week(week_days, previous_week=None, timeout=DEFAULT_TIMEOUT):
    """Confidence for one prescribed week of the 4-week program.

    `week_days` is the CSV rows for the week (Day, Duration, Type, Intensity,
    Focus, Description); `previous_week` the rows before it, for progression.
    """
    if not week_days or not available():
        return None
    state = {'week': week_days}
    questions = {
        'spacing': {
            'type': 'score',
            'instructions': 'How well does `week` space its sessions for recovery, '
                            'judging by each day\'s `Focus`, `Intensity` and '
                            '`Type`?',
            'criteria': SPACING_LEVELS,
        },
        'variety': {
            'type': 'score',
            'instructions': 'How much of the body and how many kinds of work does '
                            '`week` cover?',
            'criteria': VARIETY_LEVELS,
        },
    }
    if previous_week:
        state['previous_week'] = previous_week
        questions['progression'] = {
            'type': 'score',
            'instructions': 'How does `week` progress from `previous_week` in '
                            '`Duration` and `Intensity`?',
            'criteria': PROGRESSION_LEVELS,
        }
    answers = system_one(state, questions, timeout=timeout)
    if not answers:
        return None
    dims = {
        'spacing': _score_reading(answers['spacing'], SPACING_LEVELS),
        'variety': _score_reading(answers['variety'], VARIETY_LEVELS),
    }
    if 'progression' in answers:
        dims['progression'] = _score_reading(answers['progression'], PROGRESSION_LEVELS)
    composite = round(sum(d['score'] for d in dims.values()) / len(dims), 3)
    return {'score': composite, 'band': band_for(composite), 'dimensions': dims,
            'model': answers.get('_model', TYPESAFE_MODEL)}
