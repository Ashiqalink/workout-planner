"""The TypeSafe judge: rubric maths, ordering choice, week layout, and the routes.

Every test here fakes `workout_judge.system_one` — the suite never talks to
api.typesafe.ai — and sets a dummy key only when it wants the judge to run.
"""
import json

import pytest

import app as app_module
import workout_judge as judge
from conftest import CSRF


def ex(name, muscles, difficulty='beginner', sets=2, set_seconds=30, rest=30):
    return {'name': name, 'target_muscles': muscles, 'difficulty': difficulty,
            'category': 'Strength & Power', 'sets': sets, 'set_seconds': set_seconds,
            'rest_seconds': rest, 'duration': 1.0}


def score(value, levels, confidence=0.9):
    """A Score answer whose probability mass sits on one level."""
    probs = {str(i): 0.0 for i in range(len(levels))}
    probs[str(value)] = 1.0
    return {'type': 'score', 'score': float(value), 'confidence': confidence,
            'probabilities': probs, 'legend': {str(i): l for i, l in enumerate(levels)}}


# ── Reading exercises into phases ────────────────────────────────────


def test_exercise_phase_follows_the_textbook_order():
    f = app_module.exercise_phase
    assert f({'name': 'Squat Jumps', 'target_muscles': 'Quadriceps'}) == 'power'
    assert f({'name': 'Push-ups', 'target_muscles': 'Chest, triceps'}) == 'compound'
    assert f({'name': 'Glute Bridge', 'target_muscles': 'Glutes'}) == 'compound'
    assert f({'name': 'Plank', 'target_muscles': 'Core'}) == 'isolation'
    assert f({'name': 'Standing Calf Raises', 'target_muscles': 'Calves'}) == 'isolation'
    assert f({'name': 'Shuttle Runs', 'target_muscles': 'Cardio'}) == 'conditioning'
    assert f({'name': 'Circle Runs', 'target_muscles': 'Cardio'}) == 'conditioning'
    assert f({'name': 'Cardio Intervals', 'target_muscles': 'Legs'}) == 'conditioning'
    assert f({'name': 'Reverse Crunches', 'target_muscles': 'Core'}) == 'isolation'
    assert app_module.movement_pattern({'name': 'Circle Runs', 'target_muscles': 'Cardio'}) == 'locomotion'
    assert app_module.movement_pattern({'name': 'Reverse Crunches', 'target_muscles': 'Core'}) == 'core'
    assert f({'name': 'Marching in Place', 'target_muscles': 'Cardio'}) == 'conditioning'
    assert app_module.movement_pattern({'name': 'Marching in Place',
                                        'target_muscles': 'Cardio'}) != 'pull'
    assert f({'name': 'Arm Circles', 'target_muscles': 'Shoulders'}) == 'mobility'
    assert f({'name': 'N-Back Memory', 'target_muscles': 'Working memory'}) == 'cognitive'


def test_phased_ordering_puts_power_first_and_conditioning_last():
    items = [ex('Plank', 'Core'), ex('Shuttle Runs', 'Cardio'), ex('Push-ups', 'Chest'),
             ex('Squat Jumps', 'Quadriceps'), ex('Doorway Rows', 'Back')]
    ordered = app_module.order_exercises(items, 'phased')
    phases = [app_module.exercise_phase(e) for e in ordered]
    assert phases == ['power', 'compound', 'compound', 'isolation', 'conditioning']
    assert sorted(e['name'] for e in ordered) == sorted(e['name'] for e in items)


def test_phased_ordering_rests_the_region_across_a_phase_seam():
    """Two leg compounds then a leg accessory: the seam must not stack legs."""
    items = [ex('Squats', 'Quadriceps'), ex('Glute Bridge', 'Glutes'),
             ex('Push-ups', 'Chest'), ex('Standing Calf Raises', 'Calves'),
             ex('Plank', 'Core')]
    ordered = app_module.order_exercises(items, 'phased')
    phases = [app_module.exercise_phase(e) for e in ordered]
    assert phases == ['compound'] * 3 + ['isolation'] * 2
    regions = [app_module.muscle_regions(e) for e in ordered]
    assert not (regions[2] & regions[3]), [e['name'] for e in ordered]


def test_phased_ordering_still_alternates_regions_inside_a_phase():
    items = [ex('Push-ups', 'Chest'), ex('Diamond Push-ups', 'Chest, triceps'),
             ex('Doorway Rows', 'Back'), ex('Squats', 'Quadriceps')]
    ordered = app_module.order_exercises(items, 'phased')
    regions = [app_module.muscle_regions(e) for e in ordered]
    assert all(a != b for a, b in zip(regions, regions[1:]))


# ── Composite scoring ────────────────────────────────────────────────


def test_summarise_session_normalises_and_weights():
    answers = {
        'order': score(3, judge.ORDER_LEVELS),
        'balance': score(1, judge.BALANCE_LEVELS),
        'level_fit': score(2, judge.LEVEL_FIT_LEVELS),
        'recovery': {'type': 'noul', 'noul': 0.8},
        '_model': 'jev-test',
    }
    summary = judge.summarise_session(answers)
    dims = summary['dimensions']
    assert dims['order']['score'] == 1.0 and dims['order']['label'] == judge.ORDER_LEVELS[3]
    assert dims['balance']['score'] == 0.5
    assert dims['level_fit']['score'] == 1.0
    assert dims['recovery']['score'] == 0.8
    expected = (0.35 * 1.0 + 0.30 * 0.5 + 0.20 * 1.0 + 0.15 * 0.8) / 1.0
    assert summary['score'] == round(expected, 3)
    assert summary['band'] == 'solid'
    assert summary['model'] == 'jev-test'


def test_summarise_session_renormalises_without_history():
    answers = {'order': score(0, judge.ORDER_LEVELS),
               'balance': score(0, judge.BALANCE_LEVELS),
               'level_fit': score(2, judge.LEVEL_FIT_LEVELS)}
    summary = judge.summarise_session(answers)
    assert summary['score'] == round(0.20 / 0.85, 3)
    assert summary['band'] == 'weak'
    assert 'recovery' not in summary['dimensions']


def test_summarise_session_none_for_no_answers():
    assert judge.summarise_session(None) is None
    assert judge.summarise_session({'_model': 'x'}) is None


def test_describe_confidence_only_names_the_dimensions_that_fell_short():
    answers = {'order': score(3, judge.ORDER_LEVELS),
               'balance': score(0, judge.BALANCE_LEVELS),
               'level_fit': score(2, judge.LEVEL_FIT_LEVELS)}
    text = judge.describe_confidence(judge.summarise_session(answers))
    assert text.startswith('Worth a look:')
    assert 'Balance:' in text and 'Order:' not in text and 'Level:' not in text
    # The wording is the rubric's own text, never anything a model wrote.
    assert judge.BALANCE_LEVELS[0].lower() in text


def test_bands():
    assert judge.band_for(0.75) == 'solid'
    assert judge.band_for(0.5) == 'review'
    assert judge.band_for(0.49) == 'weak'


# ── Transport ────────────────────────────────────────────────────────


def test_system_one_is_a_noop_without_a_key(monkeypatch):
    called = []
    monkeypatch.setattr(judge, '_post', lambda *a, **k: called.append(1))
    assert judge.available() is False
    assert judge.system_one({'x': 1}, {'q': {'type': 'noul', 'instructions': '?'}}) is None
    assert called == []


def test_system_one_retries_overload_then_gives_up(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(judge.time, 'sleep', lambda s: None)
    attempts = []

    def overloaded(payload, timeout):
        attempts.append(json.loads(payload)['model'])
        return 529, b'{"detail": "overloaded"}'
    monkeypatch.setattr(judge, '_post', overloaded)
    assert judge.system_one({'x': 1}, {}, retries=2) is None
    assert attempts == [judge.TYPESAFE_MODEL] * 3


def test_system_one_parses_answers_and_caches_them(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    sent = []

    def fake(payload, timeout):
        sent.append(json.loads(payload))
        return 200, json.dumps({'model': 'jev-9',
                                'answers': {'q': {'type': 'noul', 'noul': 0.7}}}).encode()
    monkeypatch.setattr(judge, '_post', fake)
    questions = {'q': {'type': 'noul', 'instructions': '?'}}
    answers = judge.system_one({'x': 1}, questions)
    assert answers['q']['noul'] == 0.7 and answers['_model'] == 'jev-9'
    assert sent[0]['model'] == judge.TYPESAFE_MODEL and sent[0]['state'] == {'x': 1}
    # Same state and questions: answered from memory, and as a copy.
    answers['q']['noul'] = 0.1
    again = judge.system_one({'x': 1}, questions)
    assert again['q']['noul'] == 0.7 and len(sent) == 1
    # Different state: a new call.
    judge.system_one({'x': 2}, questions)
    assert len(sent) == 2


def test_system_one_gives_up_on_bad_status_and_bad_json(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(judge, '_post', lambda p, t: (401, b'{"detail": "no"}'))
    assert judge.system_one({'x': 1}, {}) is None
    monkeypatch.setattr(judge, '_post', lambda p, t: (200, b'not json'))
    assert judge.system_one({'x': 1}, {}) is None
    monkeypatch.setattr(judge, '_post', lambda p, t: (200, b'{"model": "jev", "answers": 3}'))
    assert judge.system_one({'x': 1}, {}) is None


def test_post_reuses_one_connection_and_replaces_a_stale_one(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.undo()  # get the real _post back for this test only
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    made, calls = [], []

    class Resp:
        status = 200

        def read(self):
            return b'{}'

    class Conn:
        def __init__(self, host, port=None, timeout=None):
            self.sock, self.host = None, host
            self.fail_once = len(made) == 0   # the first socket goes stale
            made.append(self)

        def request(self, method, path, body, headers):
            calls.append((method, path, headers['Authorization']))
            if self.fail_once:
                self.fail_once = False
                raise judge.http.client.RemoteDisconnected('stale')

        def getresponse(self):
            return Resp()

        def close(self):
            pass
    monkeypatch.setattr(judge.http.client, 'HTTPSConnection', Conn)
    judge._close()
    assert judge._post(b'{}', 5) == (200, b'{}')
    assert judge._post(b'{}', 5) == (200, b'{}')
    # One stale socket replaced, then the replacement served both calls.
    assert len(made) == 2 and made[0].host == 'api.typesafe.ai'
    assert [c[1] for c in calls] == ['/v1/systemone'] * 3
    assert calls[0][2] == 'Bearer test-key'
    judge._close()


# ── The engine's own reading of the rubric ───────────────────────────


def desc(phase, pattern, regions, difficulty='beginner'):
    return {'phase': phase, 'movement_pattern': pattern, 'regions': regions,
            'difficulty': difficulty}


def test_local_order_levels_follow_the_rubric():
    f = judge._order_level
    assert f([desc('power', 'jump', ['legs']), desc('compound', 'push', ['push']),
              desc('isolation', 'core', ['core']), desc('conditioning', 'locomotion', ['cardio'])]) == 3
    assert f([desc('compound', 'push', ['push']), desc('power', 'jump', ['legs']),
              desc('isolation', 'core', ['core'])]) == 2
    assert f([desc('compound', 'push', ['push']), desc('conditioning', 'locomotion', ['cardio']),
              desc('isolation', 'core', ['core'])]) == 2
    assert f([desc('compound', 'push', ['push']), desc('mobility', 'mobility', ['mobility']),
              desc('compound', 'pull', ['pull'])]) == 2
    assert f([desc('compound', 'push', ['push']), desc('isolation', 'core', ['core']),
              desc('compound', 'pull', ['pull'])]) == 1
    assert f([desc('conditioning', 'locomotion', ['cardio']), desc('compound', 'push', ['push'])]) == 0
    assert f([desc('conditioning', 'locomotion', ['legs']), desc('cognitive', 'cognitive', [])]) == 3


def test_local_balance_levels_follow_the_rubric():
    f = judge._balance_level
    assert f([desc('compound', 'push', ['push']), desc('compound', 'pull', ['pull']),
              desc('compound', 'squat', ['legs'])]) == 2
    assert f([desc('compound', 'push', ['push', 'core']), desc('isolation', 'core', ['core'])]) == 1
    assert f([desc('compound', 'push', ['push']), desc('compound', 'push', ['push'])]) == 0
    # Three of four on legs is one region taking most of the session.
    assert f([desc('compound', 'squat', ['legs']), desc('compound', 'push', ['push']),
              desc('compound', 'lunge', ['legs']), desc('compound', 'hinge', ['legs'])]) == 0
    # Neutral regions never count as a repeat.
    assert f([desc('conditioning', 'locomotion', ['neural']),
              desc('conditioning', 'run', ['neural', 'cardio'])]) == 2
    # Three drills in a row are a conditioning block, not a pattern repeat;
    # legs after legs is still a region repeat.
    assert f([desc('compound', 'push', ['push']), desc('conditioning', 'locomotion', ['legs']),
              desc('conditioning', 'locomotion', ['neural'])]) == 2
    assert f([desc('compound', 'push', ['push']), desc('compound', 'pull', ['pull']),
              desc('conditioning', 'locomotion', ['legs']),
              desc('conditioning', 'locomotion', ['legs'])]) == 1


def test_local_level_fit_allows_one_step_easier_only():
    f = judge._level_fit_level
    inter = [desc('compound', 'push', ['push'], 'intermediate'),
             desc('compound', 'pull', ['pull'], 'beginner')]
    assert f(inter, 'intermediate') == 2
    assert f(inter + [desc('compound', 'squat', ['legs'], 'advanced')], 'intermediate') == 1
    assert f(inter + [desc('compound', 'squat', ['legs'], 'advanced'),
                      desc('compound', 'hinge', ['legs'], 'advanced')], 'intermediate') == 0
    assert f([desc('compound', 'push', ['push'], 'beginner')], 'advanced') == 1


def test_rank_orderings_prefers_the_textbook_sequence():
    good = [desc('power', 'jump', ['legs']), desc('compound', 'push', ['push']),
            desc('isolation', 'core', ['core']), desc('conditioning', 'locomotion', ['cardio'])]
    bad = [good[3], good[1], good[0], good[2]]
    ranked, scores = judge.rank_orderings({'alternate': bad, 'phased': good}, 'beginner')
    assert ranked == ['phased', 'alternate']
    assert scores['phased'] == 1.0 and scores['alternate'] < 0.7
    assert judge.local_rubric([], 'beginner') is None


# ── Choosing an order ────────────────────────────────────────────────


def test_choose_order_keeps_the_current_order_on_a_near_tie(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    seen = {}

    def fake(state, questions, timeout=None):
        seen['state'], seen['questions'] = state, questions
        answers = {}
        for label in ('phased', 'alternate'):
            answers[f'{label}.order'] = score(2, judge.ORDER_LEVELS)
            answers[f'{label}.balance'] = score(2, judge.BALANCE_LEVELS)
            answers[f'{label}.level_fit'] = score(2, judge.LEVEL_FIT_LEVELS)
        # Identical answers: the tie must break towards the current order.
        return answers
    monkeypatch.setattr(judge, 'system_one', fake)
    candidates = {'phased': [{'position': 1, 'name': 'A'}],
                  'alternate': [{'position': 1, 'name': 'B'}]}
    picked = judge.choose_order(candidates, 'alternate', 'beginner', 12)
    assert picked['chosen'] is None and picked['preferred'] == 'alternate'
    assert picked['margin'] == 0.0
    assert set(picked['summaries']) == {'phased', 'alternate'}
    # Every candidate got the full rubric; no Choice over whole orderings.
    assert set(seen['state']['candidates']) == {'phased', 'alternate'}
    assert 'best_order' not in seen['questions']
    assert {q['type'] for q in seen['questions'].values()} <= {'score', 'noul'}
    assert '`candidates.phased`' in seen['questions']['phased.order']['instructions']


def test_choose_order_switches_when_another_order_scores_clearly_higher(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')

    def fake(state, questions, timeout=None):
        answers = {}
        for label in ('phased', 'alternate'):
            answers[f'{label}.order'] = score(3 if label == 'phased' else 1,
                                              judge.ORDER_LEVELS)
            answers[f'{label}.balance'] = score(2, judge.BALANCE_LEVELS)
            answers[f'{label}.level_fit'] = score(2, judge.LEVEL_FIT_LEVELS)
        return answers
    monkeypatch.setattr(judge, 'system_one', fake)
    picked = judge.choose_order({'phased': [], 'alternate': []}, 'alternate', 'beginner', 12)
    # order dim: 1.0 vs 0.333, weighted 0.35 over renormalised 0.85 → 0.275 apart.
    assert picked['chosen'] == 'phased' and picked['preferred'] == 'phased'
    assert picked['margin'] == pytest.approx(0.275, abs=0.01)
    assert picked['scores']['phased'] > picked['scores']['alternate']


def test_choose_order_needs_a_real_margin_not_a_hair(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')

    def fake(state, questions, timeout=None):
        answers = {}
        for label in ('phased', 'alternate'):
            answers[f'{label}.order'] = score(2, judge.ORDER_LEVELS)
            answers[f'{label}.balance'] = score(2, judge.BALANCE_LEVELS)
            answers[f'{label}.level_fit'] = score(2, judge.LEVEL_FIT_LEVELS)
        # A probability-weighted nudge: 0.1 of a level on order is
        # 0.35 × 0.1/3 / 0.85 ≈ 0.014 — real, but not worth a reorder.
        answers['phased.order']['score'] = 2.1
        return answers
    monkeypatch.setattr(judge, 'system_one', fake)
    picked = judge.choose_order({'phased': [], 'alternate': []}, 'alternate', 'beginner', 12)
    assert picked['preferred'] == 'phased' and picked['chosen'] is None
    assert 0 < picked['margin'] < judge.REORDER_MIN_MARGIN


def test_choose_order_ignores_answers_for_labels_the_engine_did_not_offer(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(judge, 'system_one', lambda *a, **k: {
        'random_order.order': score(3, judge.ORDER_LEVELS)})
    picked = judge.choose_order({'phased': [], 'alternate': []}, 'alternate', 'beginner', 12)
    assert picked is None


# ── The generate route ───────────────────────────────────────────────


def test_generate_workout_has_no_confidence_without_a_key(client):
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'}).get_json()
    assert data['success'] is True and data['confidence'] is None


def test_generate_workout_reports_confidence_and_reorders(client, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    # Make the engine's own reading a tie so the judge gets to decide.
    monkeypatch.setattr(judge, 'rank_orderings',
                        lambda c, d: (list(c), {l: 0.5 for l in c}))
    calls = []

    def fake(state, questions, timeout=None):
        calls.append(state)
        labels = list(state['candidates'])
        answers = {}
        # The rubric favours 'alternate' here, against the phased default.
        for label in labels:
            answers[f'{label}.order'] = score(3 if label == 'alternate' else 1,
                                              judge.ORDER_LEVELS)
            answers[f'{label}.balance'] = score(2, judge.BALANCE_LEVELS)
            answers[f'{label}.level_fit'] = score(2, judge.LEVEL_FIT_LEVELS)
        return answers
    monkeypatch.setattr(judge, 'system_one', fake)

    data = client.post('/api/generate-workout', json={
        'domains': ['Strength', 'Agility'], 'duration': 20, 'difficulty': 'beginner'
    }).get_json()

    assert data['success'] is True and len(calls) == 1
    assert 'phased' in calls[0]['candidates'] and 'alternate' in calls[0]['candidates']
    verdict = data['confidence']
    assert verdict['band'] == 'solid' and verdict['order_choice'] == 'alternate'
    assert verdict['reordered'] is True and verdict['order_decided_by'] == 'judge'

    assert verdict['summary'].startswith('Solid plan:')
    assert 'exercises' not in verdict
    assert verdict['order_margin'] >= judge.REORDER_MIN_MARGIN
    assert set(verdict['order_scores']) == set(calls[0]['candidates'])
    assert any('coaching judge' in line for line in data['applied_settings'])
    # The served order is the phased one the engine built, not something new.
    main = [e for e in data['exercises'] if e.get('category') != 'Speed & Mobility']
    names = [e['name'] for e in main]
    alternate = [e['name'] for e in calls[0]['candidates']['alternate']]
    assert names == alternate[:len(names)] or set(names) <= set(alternate)
    # State carried only engine facts: no free text left the server.
    first = calls[0]['candidates']['alternate'][0]
    assert set(first) == {'position', 'name', 'phase', 'movement_pattern', 'regions',
                          'difficulty', 'sets', 'set_seconds', 'rest_seconds'}


def test_generate_workout_keeps_engine_order_when_orders_tie(client, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(judge, 'rank_orderings',
                        lambda c, d: (list(c), {l: 0.5 for l in c}))

    def fake(state, questions, timeout=None):
        answers = {}
        for label in state['candidates']:
            answers[f'{label}.order'] = score(2, judge.ORDER_LEVELS)
            answers[f'{label}.balance'] = score(1, judge.BALANCE_LEVELS)
            answers[f'{label}.level_fit'] = score(1, judge.LEVEL_FIT_LEVELS)
        return answers
    monkeypatch.setattr(judge, 'system_one', fake)
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength', 'Agility'], 'duration': 20, 'difficulty': 'beginner'
    }).get_json()
    verdict = data['confidence']
    assert verdict['reordered'] is False and verdict['order_choice'] == 'phased'
    assert verdict['order_margin'] == 0.0
    # (0.35 × 2/3 + 0.30 × 1/2 + 0.20 × 1/2) / 0.85 ≈ 0.57: served, but flagged.
    assert verdict['band'] == 'review'


def test_generate_workout_asks_about_one_order_when_the_rubric_is_clear(client, monkeypatch):
    """The engine's rubric separates phased from alternate by itself, so the
    judge is asked to score one sequence — a third of the questions."""
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    seen = []

    def fake(state, questions, timeout=None):
        seen.append((state, questions))
        return {'order': score(3, judge.ORDER_LEVELS),
                'balance': score(2, judge.BALANCE_LEVELS),
                'level_fit': score(2, judge.LEVEL_FIT_LEVELS)}
    monkeypatch.setattr(judge, 'system_one', fake)
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength', 'Agility'], 'duration': 20, 'difficulty': 'beginner'
    }).get_json()
    verdict = data['confidence']
    assert len(seen) == 1 and 'session' in seen[0][0]
    assert set(seen[0][1]) == {'order', 'balance', 'level_fit'}
    assert verdict['order_decided_by'] == 'engine' and verdict['order_choice'] == 'phased'
    assert verdict['reordered'] is False and verdict['order_margin'] == 0.0
    assert verdict['order_scores']['phased'] >= max(verdict['order_scores'].values())


def test_generate_workout_scores_once_when_every_ordering_agrees(client, monkeypatch):
    """A three-exercise strength session sequences the same way under every
    ordering, so there is nothing to choose — the judge just scores it."""
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    seen = []

    def fake(state, questions, timeout=None):
        seen.append(state)
        return {'order': score(3, judge.ORDER_LEVELS),
                'balance': score(2, judge.BALANCE_LEVELS),
                'level_fit': score(2, judge.LEVEL_FIT_LEVELS)}
    monkeypatch.setattr(judge, 'system_one', fake)
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'}).get_json()
    verdict = data['confidence']
    assert len(seen) == 1 and 'session' in seen[0] and 'candidates' not in seen[0]
    assert verdict['reordered'] is False and verdict['order_choice'] == 'phased'
    assert list(verdict['order_scores']) == ['phased'] and verdict['band'] == 'solid'
    assert verdict['order_decided_by'] == 'engine'


def test_generate_workout_survives_a_dead_judge(client, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(judge, 'system_one', lambda *a, **k: None)
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'}).get_json()
    assert data['success'] is True and data['confidence'] is None


def test_judge_setting_turns_it_off(client, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    called = []
    monkeypatch.setattr(judge, 'system_one', lambda *a, **k: called.append(1))
    original = app_module.get_settings
    monkeypatch.setattr(app_module, 'get_settings',
                        lambda: dict(original(), **{'ai.judge_enabled': False}))
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'}).get_json()
    assert called == [] and data['confidence'] is None


# ── Region tags ──────────────────────────────────────────────────────


def noul(p):
    return {'type': 'noul', 'noul': p}


def fake_tagger(yes):
    """A system_one that answers yes for (index, tag) pairs in `yes`, no otherwise."""
    def fake(state, questions, timeout=None):
        return {qid: noul(0.9 if tuple(qid.split(':')) in
                          {(str(i), t) for i, t in yes} else 0.1)
                for qid in questions}
    return fake


def test_region_questions_fan_out_one_noul_per_region_per_exercise():
    questions = judge.region_questions(3)
    assert len(questions) == 3 * len(judge.REGION_TAGS)
    assert all(q['type'] == 'noul' for q in questions.values())
    assert '`exercises[2]`' in questions['2:push']['instructions']
    assert set(questions['0:core']['criteria']) == {'true', 'false'}


def test_tag_regions_batches_and_skips_a_failed_batch(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(judge, 'REGION_BATCH', 2)
    calls = []

    def fake(state, questions, timeout=None):
        calls.append([e['name'] for e in state['exercises']])
        if len(calls) == 2:
            return None  # second batch: the network died
        return fake_tagger({(0, 'push'), (1, 'legs'), (1, 'core')})(state, questions)
    monkeypatch.setattr(judge, 'system_one', fake)
    rows = [ex(n, '') for n in ('A', 'B', 'C', 'D', 'E')]
    tagged = judge.tag_regions(rows)
    assert calls == [['A', 'B'], ['C', 'D'], ['E']]
    assert set(tagged) == {0, 1, 4}
    assert judge.regions_from_probs(tagged[0]) == {'push'}
    assert judge.regions_from_probs(tagged[1]) == {'legs', 'core'}
    assert tagged[4]['push'] == 0.9 and set(tagged[4]) == set(judge.REGION_TAGS)


def test_tag_regions_drops_a_row_with_an_unreadable_answer(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')

    def fake(state, questions, timeout=None):
        answers = fake_tagger({(0, 'legs'), (1, 'legs')})(state, questions)
        answers['1:core'] = {'type': 'noul', 'noul': 'maybe'}
        return answers
    monkeypatch.setattr(judge, 'system_one', fake)
    tagged = judge.tag_regions([ex('A', ''), ex('B', '')])
    assert set(tagged) == {0}


def test_tag_regions_is_empty_without_a_key():
    assert judge.tag_regions([ex('A', 'Chest')]) == {}


def test_regions_from_probs_applies_the_threshold_in_code():
    probs = {'push': 0.5, 'pull': 0.49, 'legs': 'bad', 'bogus': 0.99}
    assert judge.regions_from_probs(probs) == {'push'}
    assert judge.regions_from_probs(probs, threshold=0.4) == {'push', 'pull'}
    assert judge.regions_from_probs(None) == set()
    assert judge.regions_from_probs('push') == set()


def test_muscle_regions_prefers_judge_tags_and_falls_back_without_them():
    f = app_module.muscle_regions
    side_plank = ex('Side Plank', 'Core, shoulders, obliques')
    assert f(side_plank) == {'core', 'push'}  # the heuristic reads shoulders as push
    side_plank['region_probs'] = {'core': 0.95, 'push': 0.1}
    assert f(side_plank) == {'core'}
    side_plank['region_probs'] = {'core': 0.2, 'push': 0.1}  # judge said nothing fits
    assert f(side_plank) == {'core', 'push'}


def test_library_for_hides_tags_when_the_setting_is_off(monkeypatch):
    library = [ex('A', 'Chest') | {'region_probs': {'push': 0.9}}]
    monkeypatch.setattr(app_module, 'get_all_exercises_from_db', lambda: library)
    assert app_module.library_for({'ai.judge_regions': True})[0]['region_probs']
    hidden = app_module.library_for({'ai.judge_regions': False})
    assert hidden[0]['region_probs'] is None and library[0]['region_probs']


def test_tag_library_regions_writes_probabilities_to_the_rows(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(judge, 'tag_regions',
                        lambda rows, timeout=None: {0: {'push': 0.9}, 2: {'legs': 0.8}})
    conn = app_module.get_db_connection()
    conn.execute('UPDATE exercises SET region_probs = NULL')
    conn.commit()
    conn.close()
    assert app_module.tag_library_regions() == 2
    library = app_module.get_all_exercises_from_db()
    tagged = [e for e in library if e['region_probs']]
    assert len(tagged) == 2
    assert app_module.muscle_regions(tagged[0]) == {'push'}
    # A second untagged-only run leaves the tagged rows alone.
    monkeypatch.setattr(judge, 'tag_regions',
                        lambda rows, timeout=None: dict.fromkeys(range(len(rows)), {'core': 1.0}))
    app_module.tag_library_regions()
    library = app_module.get_all_exercises_from_db()
    assert sum(1 for e in library if e['region_probs'] == {'push': 0.9}) == 1
    assert all(e['region_probs'] for e in library)
    conn = app_module.get_db_connection()
    conn.execute('UPDATE exercises SET region_probs = NULL')
    conn.commit()
    conn.close()
    app_module.invalidate_exercise_cache()


def test_tag_regions_route_requires_login_key_and_setting(client, logged_in_client,
                                                            monkeypatch):
    assert client.post('/api/exercises/tag-regions', json={},
                       headers={'X-CSRF-Token': CSRF}).status_code == 401
    assert logged_in_client.post('/api/exercises/tag-regions', json={},
                                 headers={'X-CSRF-Token': CSRF}).status_code == 503
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    original = app_module.get_settings
    monkeypatch.setattr(app_module, 'get_settings',
                        lambda: dict(original(), **{'ai.judge_regions': False}))
    assert logged_in_client.post('/api/exercises/tag-regions', json={},
                                 headers={'X-CSRF-Token': CSRF}).status_code == 400
    monkeypatch.setattr(app_module, 'get_settings', original)
    seen = {}

    def fake(only_untagged=True, timeout=None):
        seen['only_untagged'] = only_untagged
        return 7
    monkeypatch.setattr(app_module, 'tag_library_regions', fake)
    data = logged_in_client.post('/api/exercises/tag-regions', json={'all': True},
                                 headers={'X-CSRF-Token': CSRF}).get_json()
    assert data == {'success': True, 'tagged': 7} and seen['only_untagged'] is False


def test_tag_regions_route_reports_a_failure(logged_in_client, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')

    def boom(only_untagged=True, timeout=None):
        raise RuntimeError('disk full')
    monkeypatch.setattr(app_module, 'tag_library_regions', boom)
    resp = logged_in_client.post('/api/exercises/tag-regions', json={},
                                 headers={'X-CSRF-Token': CSRF})
    assert resp.status_code == 500 and 'disk full' in resp.get_json()['error']


def test_add_exercise_tags_the_new_row_when_the_judge_is_on(logged_in_client, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(judge, 'system_one', fake_tagger({(0, 'pull')}))
    resp = logged_in_client.post('/api/exercises/add', json={
        'name': 'Towel Row', 'category': 'Strength & Power', 'duration': 1,
        'difficulty': 'beginner', 'target_muscles': 'Chest'})
    assert resp.status_code == 200
    probs = resp.get_json()['exercise']['region_probs']
    assert probs['pull'] == 0.9
    row = next(e for e in app_module.get_all_exercises_from_db() if e['name'] == 'Towel Row')
    assert app_module.muscle_regions(row) == {'pull'}


def test_add_exercise_survives_a_dead_judge(logged_in_client, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(judge, 'system_one', lambda *a, **k: None)
    resp = logged_in_client.post('/api/exercises/add', json={
        'name': 'Towel Curl', 'category': 'Strength & Power', 'duration': 1,
        'difficulty': 'beginner', 'target_muscles': 'Biceps'})
    assert resp.status_code == 200
    assert resp.get_json()['exercise']['region_probs'] is None
    row = next(e for e in app_module.get_all_exercises_from_db() if e['name'] == 'Towel Curl')
    assert app_module.muscle_regions(row) == {'pull'}


def test_cli_tag_regions(flask_app, monkeypatch):
    runner = flask_app.test_cli_runner()
    assert 'nothing tagged' in runner.invoke(args=['tag-regions']).output
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(app_module, 'tag_library_regions',
                        lambda only_untagged=True, timeout=None: 3 if only_untagged else 9)
    assert 'Tagged 3' in runner.invoke(args=['tag-regions']).output
    monkeypatch.setenv('TAG_ALL', '1')
    assert 'Tagged 9' in runner.invoke(args=['tag-regions']).output


# ── Laying out a week ────────────────────────────────────────────────


def sess(name, regions, load='moderate'):
    return {'name': name, 'regions': regions, 'phases': ['compound'], 'load': load,
            'minutes': 15, 'exercise_count': 4}


def test_schedule_cost_charges_region_repeats_within_48_hours():
    a, b = sess('Push', ['push']), sess('Push again', ['push'])
    assert judge.schedule_cost(['Mon', 'Tue'], [a, b]) == 2.0
    assert judge.schedule_cost(['Mon', 'Wed'], [a, b]) == 0.0
    hard = [sess('A', ['legs'], 'hard'), sess('B', ['push'], 'hard')]
    assert judge.schedule_cost(['Mon', 'Tue'], hard) == 1.0
    assert judge.schedule_cost(['Mon', 'Tue'], [sess('M', ['mobility']),
                                                sess('N', ['mobility'])]) == 0.0


def test_candidate_schedules_are_cheapest_first_and_distinct():
    sessions = [sess('Push', ['push']), sess('Legs', ['legs']), sess('Push 2', ['push'])]
    laid = judge.candidate_schedules(['Mon', 'Tue', 'Wed'], sessions)
    assert laid and laid[0]['cost'] == 0.0
    assert [d['session']['name'] for d in laid[0]['days']][1] == 'Legs'
    assert len({tuple(d['session']['name'] for d in c['days']) for c in laid}) == len(laid)
    assert len(laid) <= judge.MAX_PLAN_CANDIDATES


def test_orchestrate_week_without_key_uses_the_rule():
    result = judge.orchestrate_week(['Mon', 'Wed', 'Fri'],
                                    [sess('A', ['push']), sess('B', ['legs'])])
    assert result['judged'] is False and result['chosen']['cost'] == 0.0


def test_orchestrate_week_applies_confident_pick(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')

    def fake(state, questions, timeout=None):
        labels = list(state['candidates'])
        answers = {'best_week': {'type': 'choice', 'choice': labels[-1],
                                 'probabilities': {}, 'confidence': 0.8},
                   'variety': score(1, judge.VARIETY_LEVELS)}
        for label in labels:
            answers[f'{label}.spacing'] = score(2, judge.SPACING_LEVELS)
        return answers
    monkeypatch.setattr(judge, 'system_one', fake)
    sessions = [sess('A', ['push']), sess('B', ['legs']), sess('C', ['pull'])]
    result = judge.orchestrate_week(['Mon', 'Wed', 'Fri'], sessions)
    assert result['judged'] is True and result['confidence'] == 0.8
    assert result['chosen']['label'] == f'plan_{chr(ord("a") + len(result["spacing"]) - 1)}'
    assert result['variety']['level'] == 1


def test_orchestrate_route_reads_inline_sessions(client, monkeypatch):
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    resp = client.post('/api/plan/orchestrate', json={
        'days': ['Mon', 'Wed', 'Fri'],
        'sessions': [{'name': 'Push day', 'exercises': ['Push-ups', 'Tricep Dips']},
                     {'name': 'Leg day', 'exercises': ['Squats', 'Lunges']},
                     {'name': 'Core', 'exercises': ['Plank', 'Dead Bug']}],
    }, headers={'X-CSRF-Token': CSRF})
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data['judged'] is False
    assert len(data['chosen']['days']) == 3
    assert data['sessions'][0]['regions'] == ['push']
    assert data['summary'].startswith('Laid out by the 48-hour rule alone')


def test_orchestrate_route_validates(client):
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    resp = client.post('/api/plan/orchestrate', json={'days': [], 'sessions': []},
                       headers={'X-CSRF-Token': CSRF})
    assert resp.status_code == 400
    resp = client.post('/api/plan/orchestrate', json={'days': ['Mon'], 'sessions': [{}]})
    assert resp.status_code == 403


def test_plan_confidence_route(client, monkeypatch):
    assert client.get('/api/plan/confidence?week=1').get_json()['confidence'] is None
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    app_module._program_week_cache.clear()
    seen = {}

    def fake(state, questions, timeout=None):
        seen['state'] = state
        answers = {'spacing': score(2, judge.SPACING_LEVELS),
                   'variety': score(2, judge.VARIETY_LEVELS)}
        if 'progression' in questions:
            answers['progression'] = score(1, judge.PROGRESSION_LEVELS)
        return answers
    monkeypatch.setattr(judge, 'system_one', fake)
    data = client.get('/api/plan/confidence?week=2').get_json()
    assert data['week'] == 2 and data['cached'] is False
    assert set(data['confidence']['dimensions']) == {'spacing', 'variety', 'progression'}
    assert data['confidence']['score'] == round((1 + 1 + 0.5) / 3, 3)
    assert seen['state']['previous_week'] and seen['state']['week']
    assert set(seen['state']['week'][0]) == {'Day', 'Duration', 'Type', 'Intensity',
                                             'Focus', 'Description'}
    assert client.get('/api/plan/confidence?week=2').get_json()['cached'] is True
    assert client.get('/api/plan/confidence?week=9').status_code == 404
