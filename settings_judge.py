"""Find a setting by saying what you want, judged by TypeSafe (Jev).

The rules matcher in :mod:`settings_registry` finds settings by the words a
request shares with a keyword list. This module asks Jev instead: one Choice
over every setting in the registry ("which of these is the person asking
about?") plus one Choice for the kind of change (on, off, more, less, a
specific value). Both run in the same request. Jev picks; code fills in the
value with the registry's own helpers and the registry validates the result
exactly as it validates the rules matcher and the local model.

Latency is the request, not the thinking: measured from this machine the API
itself reports 70-140 ms, the round trip is ~350 ms, and the rest tracks how
much option text was sent. Halving the body (25 KB to 8 KB) roughly halved the
call, so every option is kept to one short line:

* ``criteria`` keys are opaque ids (``o41``), not setting keys — a settings
  key is a dotted path the model does not need to read, and 85 of them cost
  ~2.8 KB and ~70 ms. :data:`OPTION_IDS` maps back.
* the text of an option is "Group: Label", with no help sentence. The group
  name carries the context the help sentence used to.
* an enum is one option per choice ("Appearance: Theme = Dark"), so the value
  comes back in the same request. A second request happens only for a list
  setting whose sentence names none of its choices.

Everything above is built once at import: the questions do not depend on the
user's current values, so one person's answer is a cache hit for the next.

Nothing here applies anything. Output is the same ``[{'key', 'value'}]`` shape
as :func:`settings_registry.match_intent`, so :func:`settings_registry.apply_changes`
is the single gate for all three engines.
"""
import settings_registry as reg
import workout_judge as judge

NONE = 'none_of_these'
MIN_SETTING_PROB = 0.35   # below this the pick is a guess; say nothing
MULTI_YES = 0.6

DIRECTIONS = {
    'turn_on': 'Enable, show or allow something',
    'turn_off': 'Disable, hide, mute or stop something',
    'increase': 'More, bigger, longer, louder (no exact number)',
    'decrease': 'Less, smaller, shorter, quieter (no exact number)',
    'set_value': 'A specific named option or exact number',
    'unclear': 'Names a topic but not the change',
}


def _plain_label(choice_label):
    return choice_label.split('—')[0].strip()


def _build_options():
    """(criteria, id -> target) — one option per setting, one per enum choice.

    A target is ``'appearance.font_size'`` or ``'appearance.theme=dark'``; the
    id sent to the model is just its position, which is what keeps the body
    small. Built once at import, because nothing here varies by user.
    """
    criteria, targets = {}, {}
    for setting in reg.SETTINGS:
        if setting.type == 'enum':
            entries = [(f'{setting.key}={value}',
                        f'{setting.group}: {setting.label} = {_plain_label(label)}')
                       for value, label in setting.choices]
        else:
            entries = [(setting.key, f'{setting.group}: {setting.label}')]
        for target, text in entries:
            option_id = f'o{len(criteria)}'
            criteria[option_id] = text
            targets[option_id] = target
    criteria[NONE] = 'Not about any setting above, or not a request to change one'
    targets[NONE] = NONE
    return criteria, targets


OPTIONS, OPTION_IDS = {}, {}


def refresh_options():
    """Rebuild the option list after ``reg.apply_mode``; in place, like the registry."""
    criteria, targets = _build_options()
    OPTIONS.clear()
    OPTIONS.update(criteria)
    OPTION_IDS.clear()
    OPTION_IDS.update(targets)


refresh_options()

QUESTIONS = {
    'setting': {
        'type': 'choice',
        'instructions': 'Which one application setting is `request` asking to '
                        'change? Choose by what the person wants to happen, not '
                        'by which words they used; a setting may be described '
                        'without using its name. Where a setting is listed once '
                        'per option ("Theme = Dark"), pick the option they want.',
        'criteria': OPTIONS,
    },
    'direction': {
        'type': 'choice',
        'instructions': 'What kind of change does `request` ask for, regardless '
                        'of which setting it concerns?',
        'criteria': DIRECTIONS,
    },
}


def questions():
    """The two questions, identical for every user and every request."""
    return QUESTIONS


def split_option(option_id):
    """An answered option id as ``(setting key, chosen enum value or None)``."""
    key, sep, value = str(OPTION_IDS.get(option_id, option_id)).partition('=')
    return key, (value if sep else None)


def _value_questions(setting):
    """Round two, lists only: one Noul per choice."""
    return {f'want:{v}': {
        'type': 'noul',
        'instructions': f'Does `request` ask for "{_plain_label(label)}" to be included '
                        f'among the {setting.label.lower()}?',
    } for v, label in setting.choices}


def _direction_to_value(setting, direction, query, current):
    """Fill the value in code wherever code can; None means it cannot."""
    if setting.type == 'bool':
        if direction in ('turn_on', 'increase', 'set_value', 'unclear'):
            wanted = True
        elif direction in ('turn_off', 'decrease'):
            wanted = False
        else:
            return None
        if direction == 'unclear' and not setting.intent_inverted:
            return True  # naming a toggle is asking for it, as the rules matcher assumes
        return (not wanted) if setting.intent_inverted else wanted

    if setting.type in ('int', 'float'):
        number = reg._extract_number(query, setting)
        if number is not None:
            return number
        if direction not in ('increase', 'decrease'):
            return None
        base = current.get(setting.key, setting.default)
        try:
            base = float(base)
        except (TypeError, ValueError):
            base = float(setting.default)
        return base + (setting.step or 1) * (2 if direction == 'increase' else -2)

    if setting.type == 'multi':
        padded = f' {reg._normalise(query)} '
        hits = [v for needle, v in reg._choice_needles(setting)
                if len(needle) >= 3 and f' {needle} ' in padded]
        if not hits:
            return None
        base = current.get(setting.key, setting.default)
        base = list(base) if isinstance(base, (list, tuple)) else []
        if direction == 'turn_off':
            return [v for v in base if v not in hits]
        return base + [h for h in hits if h not in base]

    return None  # text — never inferred


def normalise(query):
    """One cache entry for every spelling of the same wish."""
    return ' '.join(str(query or '').lower().split())


def resolve(query, current=None, timeout=judge.DEFAULT_TIMEOUT):
    """Jev's reading of a request as ``[{'key', 'value'}]``, or None when it
    had no answer (no key, network failure, or nothing matched confidently).

    Attaches ``judge`` metadata (setting probability, direction, rounds) on
    the returned list for the comparison script and the API to report.
    """
    query = normalise(query)
    if not query or not judge.available():
        return None
    current = current or reg.defaults()
    state = {'request': query}
    answers = judge.system_one(state, questions(), timeout=timeout)
    if not answers:
        return None

    pick = answers.get('setting') or {}
    option_id = pick.get('choice')
    prob = float((pick.get('probabilities') or {}).get(option_id, 0.0))
    direction = (answers.get('direction') or {}).get('choice', 'unclear')
    key, chosen = split_option(option_id)
    meta = {'setting': key, 'probability': round(prob, 3), 'direction': direction,
            'rounds': 1, 'model': answers.get('_model')}

    setting = reg.SETTINGS_BY_KEY.get(key)
    if setting is None or key == NONE or prob < MIN_SETTING_PROB:
        return _Result([], meta)

    if setting.type == 'enum':
        # The sentence's own wording wins when it names a choice ("use pounds
        # not kilograms" relies on the current value to disambiguate); the
        # judge's option answers the rest.
        value = reg._best_choice(setting, query, current.get(setting.key))
        if value is None and chosen in {str(v) for v in setting.choice_values}:
            value = chosen
    else:
        value = _direction_to_value(setting, direction, query, current)

    if value is None and setting.type == 'multi':
        meta['rounds'] = 2
        second = judge.system_one(state, _value_questions(setting), timeout=timeout)
        if second:
            base = current.get(setting.key, setting.default)
            base = list(base) if isinstance(base, (list, tuple)) else []
            hits = [v for v, _ in setting.choices
                    if float((second.get(f'want:{v}') or {}).get('probability', 0)) >= MULTI_YES]
            if hits:
                value = ([v for v in base if v not in hits] if direction == 'turn_off'
                         else base + [h for h in hits if h not in base])

    return _Result([{'key': key, 'value': value}] if value is not None else [], meta)


def warm(timeout=judge.DEFAULT_TIMEOUT):
    """Open the kept-alive socket ahead of the first request.

    The TLS handshake is ~640 ms of a cold call; done once at start-up (in a
    thread, failures ignored) the first person to ask pays only the round trip.
    """
    if judge.available():
        try:
            judge.system_one({'request': 'warm up'},
                             {'q': {'type': 'noul', 'instructions': 'Is `request` a greeting?'}},
                             timeout=timeout, retries=0)
        except Exception:  # never let start-up depend on the network
            pass


class _Result(list):
    """A list of proposals that also carries the judge's reading of the request."""

    def __init__(self, proposals, meta):
        super().__init__(proposals)
        self.judge = meta
