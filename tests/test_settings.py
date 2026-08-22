"""Tests for the settings registry, the settings API, and the assistant.

The registry tests are pure — no app import needed — but the endpoint tests
reuse the fixtures in conftest.py, so the module import order there applies.
"""
import json

import pytest

import settings_registry as reg
import app as app_module
from conftest import CSRF


H = {'X-CSRF-Token': CSRF}


def csrf_client(flask_app):
    client = flask_app.test_client()
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    return client


# ── Registry integrity ───────────────────────────────────────────────

def test_registry_keys_unique_and_wellformed():
    keys = [s.key for s in reg.SETTINGS]
    assert len(keys) == len(set(keys)), 'duplicate setting keys'
    for setting in reg.SETTINGS:
        assert setting.tier in ('simple', 'advanced', 'expert'), setting.key
        assert setting.type in ('bool', 'enum', 'multi', 'int', 'float', 'text'), setting.key
        if setting.type in ('enum', 'multi'):
            assert setting.choices, f'{setting.key} has no choices'
            # The default must itself be valid
            defaults = setting.default if isinstance(setting.default, list) else [setting.default]
            values = {c[0] for c in setting.choices}
            for d in defaults:
                assert d in values, f'{setting.key} default {d!r} not among choices'
        if setting.type in ('int', 'float'):
            assert setting.minimum is not None and setting.maximum is not None, setting.key
            assert setting.minimum <= setting.default <= setting.maximum, setting.key


def test_presets_only_touch_real_settings():
    for name, preset in reg.PRESETS.items():
        for key, value in preset['values'].items():
            setting = reg.SETTINGS_BY_KEY.get(key)
            assert setting is not None, f'preset {name} references unknown {key}'
            ok, _, error = reg.coerce(setting, value)
            assert ok, f'preset {name}: {key}={value!r} invalid: {error}'


def test_merge_drops_unknown_and_invalid_keys():
    merged = reg.merge({'appearance.theme': 'dark',
                        'retired.setting': 'x',
                        'appearance.font_size': 'not a number'})
    assert merged['appearance.theme'] == 'dark'
    assert 'retired.setting' not in merged
    assert merged['appearance.font_size'] == 15  # invalid value -> default


# ── Coercion ─────────────────────────────────────────────────────────

def test_coerce_bool_accepts_words():
    setting = reg.SETTINGS_BY_KEY['audio.enabled']
    assert reg.coerce(setting, 'off') == (True, False, None)
    assert reg.coerce(setting, 'ON') == (True, True, None)
    assert reg.coerce(setting, True) == (True, True, None)
    ok, _, error = reg.coerce(setting, 'maybe')
    assert not ok and 'switch' in error


def test_coerce_numbers_clamp_to_bounds():
    setting = reg.SETTINGS_BY_KEY['appearance.font_size']
    assert reg.coerce(setting, 99)[1] == setting.maximum
    assert reg.coerce(setting, -5)[1] == setting.minimum
    assert reg.coerce(setting, '18')[1] == 18


def test_coerce_enum_accepts_label_and_alias():
    setting = reg.SETTINGS_BY_KEY['appearance.accent']
    assert reg.coerce(setting, 'violet')[1] == 'violet'
    assert reg.coerce(setting, 'Violet')[1] == 'violet'
    assert reg.coerce(setting, 'purple')[1] == 'violet'  # CHOICE_ALIASES
    ok, _, error = reg.coerce(setting, 'chartreuse')
    assert not ok and 'must be one of' in error


def test_coerce_text_strips_markup():
    setting = reg.SETTINGS_BY_KEY['appearance.custom_css']
    ok, value, _ = reg.coerce(setting, 'body { color: red } </style><script>')
    assert ok
    assert '<' not in value and '>' not in value


def test_apply_changes_reports_only_real_changes():
    current = reg.defaults()
    accepted, rejected = reg.apply_changes(current, [
        {'key': 'appearance.theme', 'value': 'dark'},
        {'key': 'audio.enabled', 'value': True},   # already the default — no-op
        {'key': 'nope.nope', 'value': 1},
    ])
    assert [c['key'] for c in accepted] == ['appearance.theme']
    assert rejected and rejected[0]['key'] == 'nope.nope'


def test_describe_changes_built_from_registry_labels():
    current = reg.defaults()
    accepted, _ = reg.apply_changes(current, [{'key': 'appearance.theme', 'value': 'dark'}])
    assert reg.describe_changes(accepted) == 'Set theme to Dark.'
    assert reg.describe_changes(accepted, applied=False).startswith('This will ')
    assert reg.describe_changes([]) .startswith('Nothing needed changing')


# ── Deterministic intent matching ────────────────────────────────────

def _intents(query, current=None):
    return {p['key']: p['value']
            for p in reg.match_intent(query, current=current or reg.defaults())}


def test_match_intent_bool_with_negation():
    assert _intents('i hate the beeping') == {'audio.enabled': False}
    assert _intents('turn off all sound') == {'audio.enabled': False}


def test_match_intent_inverted_setting():
    # "Reduce motion" suppresses the thing named, so directions flip.
    assert _intents('turn off animations') == {'appearance.reduce_motion': True}
    assert _intents('turn animations back on') == {'appearance.reduce_motion': False}


def test_match_intent_numbers_with_units_and_words():
    assert _intents('rest 90 seconds between exercises') == {'timers.fixed_rest_seconds': 90.0}
    assert _intents('i keep losing my streak when i miss one day') == \
        {'goals.streak_grace_days': 1.0}


def test_match_intent_enum_longest_and_not_current():
    assert _intents('I want to log sets reps and weight') == \
        {'session.tracking_mode': 'sets_reps_weight'}
    # Both units named; the one not in effect is the request
    assert _intents('use pounds not kilograms') == {'units.weight': 'lb'}


def test_match_intent_multi_merges_not_replaces():
    current = reg.defaults()
    added = _intents('show the heatmap on my dashboard', current)
    assert 'heatmap' in added['dashboard.cards']
    assert set(current['dashboard.cards']) <= set(added['dashboard.cards'])

    removed = _intents('hide recent activity from the dashboard', current)
    assert 'recent' not in removed['dashboard.cards']
    assert 'today' in removed['dashboard.cards']


def test_match_intent_weak_matches_never_ride_along():
    # 'dark mode' must not also flip developer mode via the shared word 'mode'
    assert _intents('dark mode') == {'appearance.theme': 'dark'}
    assert _intents('use pounds not kilograms') == {'units.weight': 'lb'}


def test_match_intent_multi_intent_sentence():
    result = _intents('dark mode with bigger text and no sound')
    assert result['appearance.theme'] == 'dark'
    assert result['audio.enabled'] is False
    assert result['appearance.font_size'] > 15  # bigger, despite the "no" later


def test_match_intent_nonsense_returns_nothing():
    assert _intents('banana helicopter') == {}


# ── Settings API ─────────────────────────────────────────────────────

def test_settings_get_returns_schema(client):
    data = client.get('/api/settings').get_json()
    assert data['success'] and len(data['schema']) == len(reg.SETTINGS)
    assert data['values']['appearance.theme'] == 'system'


def test_settings_post_requires_csrf(client):
    assert client.post('/api/settings', json={'changes': []}).status_code == 403


def test_settings_roundtrip_guest(flask_app):
    client = csrf_client(flask_app)
    resp = client.post('/api/settings', headers=H,
                       json={'changes': [{'key': 'appearance.theme', 'value': 'dark'}]})
    data = resp.get_json()
    assert data['success'] and data['values']['appearance.theme'] == 'dark'
    assert data['summary'] == 'Set theme to Dark.'
    # And it reaches the rendered page before first paint
    assert b'data-theme="dark"' in client.get('/dashboard').data


def test_settings_roundtrip_logged_in(logged_in_client):
    with logged_in_client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    logged_in_client.post('/api/settings', headers=H,
                          json={'changes': [{'key': 'appearance.accent', 'value': 'violet'}]})
    assert logged_in_client.get('/api/settings').get_json()['values']['appearance.accent'] == 'violet'


def test_settings_invalid_value_rejected_with_reason(flask_app):
    client = csrf_client(flask_app)
    data = client.post('/api/settings', headers=H,
                       json={'changes': [{'key': 'appearance.theme', 'value': 'plaid'}]}).get_json()
    assert data['changed'] == []
    assert 'must be one of' in data['rejected'][0]['error']


def test_settings_reset_group_and_all(flask_app):
    client = csrf_client(flask_app)
    client.post('/api/settings', headers=H, json={'changes': [
        {'key': 'appearance.theme', 'value': 'dark'},
        {'key': 'audio.enabled', 'value': False},
    ]})
    client.post('/api/settings/reset', headers=H, json={'group': 'Appearance'})
    values = client.get('/api/settings').get_json()['values']
    assert values['appearance.theme'] == 'system'   # reset
    assert values['audio.enabled'] is False         # different group — kept
    client.post('/api/settings/reset', headers=H, json={})
    assert client.get('/api/settings').get_json()['values']['audio.enabled'] is True


def test_preset_applies_batch(flask_app):
    client = csrf_client(flask_app)
    data = client.post('/api/settings/preset', headers=H,
                       json={'preset': 'strength'}).get_json()
    assert data['success']
    values = client.get('/api/settings').get_json()['values']
    assert values['session.tracking_mode'] == 'sets_reps_weight'
    assert client.post('/api/settings/preset', headers=H,
                       json={'preset': 'nope'}).status_code == 400


def test_settings_search_ranks_by_relevance(client):
    results = client.get('/api/settings/search?q=dark').get_json()['results']
    assert results and results[0]['key'] == 'appearance.theme'


def test_landing_page_redirect(flask_app):
    client = csrf_client(flask_app)
    client.post('/api/settings', headers=H,
                json={'changes': [{'key': 'ui.landing_page', 'value': 'planner'}]})
    resp = client.get('/')
    assert resp.status_code == 302 and '/planner' in resp.headers['Location']
    assert client.get('/dashboard').status_code == 200  # never unreachable


# ── The assistant endpoint ───────────────────────────────────────────

def test_ask_proposes_without_applying(flask_app, monkeypatch):
    monkeypatch.setattr(app_module, 'settings_intent_via_llm', lambda *a, **k: None)
    client = csrf_client(flask_app)
    data = client.post('/api/settings/ask', headers=H,
                       json={'query': 'turn off all sound'}).get_json()
    assert data['success'] and data['applied'] is False
    assert data['engine'] == 'rules'
    assert data['proposals'][0]['key'] == 'audio.enabled'
    # Nothing changed until the user confirms
    assert client.get('/api/settings').get_json()['values']['audio.enabled'] is True


def test_ask_apply_persists(flask_app, monkeypatch):
    monkeypatch.setattr(app_module, 'settings_intent_via_llm', lambda *a, **k: None)
    client = csrf_client(flask_app)
    data = client.post('/api/settings/ask', headers=H,
                       json={'query': 'turn off all sound', 'apply': True}).get_json()
    assert data['applied'] is True
    assert client.get('/api/settings').get_json()['values']['audio.enabled'] is False


def test_ask_can_be_disabled(flask_app):
    client = csrf_client(flask_app)
    client.post('/api/settings', headers=H,
                json={'changes': [{'key': 'ai.allow_settings_changes', 'value': False}]})
    resp = client.post('/api/settings/ask', headers=H, json={'query': 'dark mode'})
    assert resp.status_code == 403


def test_ask_model_output_validated_like_any_input(flask_app, monkeypatch):
    """A model that proposes a bogus key or value gets filtered, not obeyed."""
    monkeypatch.setattr(app_module, 'settings_intent_via_llm',
                        lambda *a, **k: [{'key': 'appearance.theme', 'value': 'dark'},
                                         {'key': 'evil.key', 'value': 'x'}])
    # Force the model path by making the deterministic score weak
    monkeypatch.setattr(app_module.reg, 'match_intent', lambda *a, **k: [])
    client = csrf_client(flask_app)
    data = client.post('/api/settings/ask', headers=H,
                       json={'query': 'zzz unmatchable zzz'}).get_json()
    keys = [p['key'] for p in data['proposals']]
    assert keys == ['appearance.theme']
    assert data['rejected'] and data['rejected'][0]['key'] == 'evil.key'


def test_ask_summary_is_engine_built(flask_app, monkeypatch):
    """The sentence shown to the user comes from the registry, never the model.

    Same truth boundary as workout explanations: even a hostile model response
    can only influence which validated changes are listed, not the words.
    """
    fabricated = 'I have hacked your settings and cured your knee pain.'
    monkeypatch.setattr(app_module, 'settings_intent_via_llm',
                        lambda *a, **k: [{'key': 'appearance.theme', 'value': 'dark'}])
    monkeypatch.setattr(app_module.reg, 'match_intent', lambda *a, **k: [])
    client = csrf_client(flask_app)
    data = client.post('/api/settings/ask', headers=H,
                       json={'query': 'zzz'}).get_json()
    assert fabricated not in json.dumps(data)
    assert data['summary'] == 'This will set theme to Dark.'


def test_settings_intent_schema_has_no_free_text_field():
    """The grammar the model must follow admits keys and values, nothing else."""
    schema = app_module.SETTINGS_INTENT_SCHEMA['schema']
    assert set(schema['properties']) == {'changes'}
    assert schema['additionalProperties'] is False
    item = schema['properties']['changes']['items']
    assert set(item['properties']) == {'key', 'value'}
    assert item['additionalProperties'] is False


# ── Settings that drive the engine ───────────────────────────────────

def test_equipment_filter_narrows_generation(flask_app, monkeypatch):
    monkeypatch.setattr(app_module, 'generate_workout_via_llm', lambda *a, **k: None)
    client = csrf_client(flask_app)
    wide = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'}).get_json()
    client.post('/api/settings', headers=H,
                json={'changes': [{'key': 'workout.equipment', 'value': ['none']}]})
    narrow = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'}).get_json()
    assert narrow['pool_size'] < wide['pool_size']


def test_exclusion_keywords_remove_exercises(flask_app, monkeypatch):
    monkeypatch.setattr(app_module, 'generate_workout_via_llm', lambda *a, **k: None)
    client = csrf_client(flask_app)
    client.post('/api/settings', headers=H,
                json={'changes': [{'key': 'workout.exclude_keywords', 'value': 'push'}]})
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 20, 'difficulty': 'beginner'}).get_json()
    assert data['exercises']
    assert not any('push' in e['name'].lower() for e in data['exercises'])


def test_warmup_cooldown_carved_from_duration(flask_app, monkeypatch):
    monkeypatch.setattr(app_module, 'generate_workout_via_llm', lambda *a, **k: None)
    client = csrf_client(flask_app)
    client.post('/api/settings', headers=H, json={'changes': [
        {'key': 'workout.include_warmup', 'value': True},
        {'key': 'workout.include_cooldown', 'value': True}]})
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 20, 'difficulty': 'beginner'}).get_json()
    assert any('warm-up' in a for a in data['applied_settings'])
    assert any('cool-down' in a for a in data['applied_settings'])
    assert data['total_duration'] <= 20 * 1.05  # carved out, not added on


def test_generation_reports_engine(flask_app, monkeypatch):
    monkeypatch.setattr(app_module, 'generate_workout_via_llm', lambda *a, **k: None)
    client = csrf_client(flask_app)
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'}).get_json()
    assert data['engine'] == 'rules'


def test_ai_disabled_never_calls_the_model(flask_app, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, 'generate_workout_via_llm',
                        lambda *a, **k: calls.append(1))
    client = csrf_client(flask_app)
    client.post('/api/settings', headers=H,
                json={'changes': [{'key': 'ai.enabled', 'value': False}]})
    client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'})
    assert calls == []


# ── Streak semantics ─────────────────────────────────────────────────

def _iso(days_ago):
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')


def test_streak_plain_consecutive():
    assert app_module.streak_from_dates([_iso(0), _iso(1), _iso(2)]) == 3
    assert app_module.streak_from_dates([_iso(0), _iso(2)]) == 1  # gap breaks it


def test_streak_grace_day_survives_one_miss():
    assert app_module.streak_from_dates([_iso(0), _iso(2)], grace_days=1) == 2
    assert app_module.streak_from_dates([_iso(0), _iso(3)], grace_days=1) == 1


def test_streak_rest_days_do_not_break():
    from datetime import datetime, timedelta
    weekday_names = ('monday', 'tuesday', 'wednesday', 'thursday',
                     'friday', 'saturday', 'sunday')
    # The day between two sessions, declared a rest day, keeps the chain alive
    skipped = datetime.now() - timedelta(days=1)
    rest_day = weekday_names[skipped.weekday()]
    assert app_module.streak_from_dates([_iso(0), _iso(2)], rest_days=[rest_day]) == 2
    assert app_module.streak_from_dates([_iso(0), _iso(2)], rest_days=[]) == 1


# ── Pure engine helpers ──────────────────────────────────────────────

POOL = [
    {'name': 'A', 'category': 'Strength & Power', 'duration': 2, 'difficulty': 'beginner',
     'equipment': 'None'},
    {'name': 'B', 'category': 'Strength & Power', 'duration': 3, 'difficulty': 'advanced',
     'equipment': 'Dumbbells'},
    {'name': 'C', 'category': 'Endurance', 'duration': 1, 'difficulty': 'beginner',
     'equipment': 'Wall or chair'},
    {'name': 'D', 'category': 'Endurance', 'duration': 2, 'difficulty': 'beginner',
     'equipment': 'Mat, towel'},
]


def test_filter_by_equipment_alternatives_and_combinations():
    f = app_module.filter_by_equipment
    names = lambda result: [e['name'] for e in result]
    # "Wall or chair": either alternative is enough
    assert 'C' in names(f(POOL, ['chair']))
    assert 'C' in names(f(POOL, ['wall']))
    # "Mat, towel": both required
    assert 'D' not in names(f(POOL, ['mat']))
    assert 'D' in names(f(POOL, ['mat', 'towel']))
    # No dumbbells owned -> B excluded, bodyweight A always in
    result = names(f(POOL, ['none']))
    assert 'A' in result and 'B' not in result


def test_order_exercises_modes():
    ordered = app_module.order_exercises(POOL, 'hardest_first')
    assert ordered[0]['name'] == 'B'
    ordered = app_module.order_exercises(POOL, 'longest_first')
    assert ordered[0]['name'] == 'B'
    ordered = app_module.order_exercises(POOL, 'alternate')
    categories = [e['category'] for e in ordered]
    assert categories[0] != categories[1]  # round-robin across categories
    assert sorted(e['name'] for e in ordered) == ['A', 'B', 'C', 'D']


def test_select_rule_based_respects_spillover_setting():
    picks = app_module.select_exercises_rule_based(
        POOL, 10, 'intermediate', {'workout.difficulty_spillover': False})
    assert picks  # nothing matches 'intermediate' exactly; falls back to pool
    strict = app_module.select_exercises_rule_based(
        [POOL[1]], 10, 'beginner', {'workout.difficulty_spillover': False})
    assert strict and strict[0]['name'] == 'B'  # single-entry pool still returns work


def test_select_rule_based_strict_variety_avoids_recent():
    picks = app_module.select_exercises_rule_based(
        POOL, 3, 'beginner', {'workout.variety': 'strict'}, avoid=['a'])
    assert 'A' not in [e['name'] for e in picks]


# ── Data export ──────────────────────────────────────────────────────

def test_export_json_contains_settings(flask_app):
    client = csrf_client(flask_app)
    resp = client.get('/api/data/export')
    assert resp.status_code == 200
    payload = json.loads(resp.data)
    assert 'settings' in payload and 'sessions' in payload


def test_export_csv_shape(client):
    resp = client.get('/api/data/export?format=csv')
    assert resp.status_code == 200
    assert resp.data.splitlines()[0].startswith(b'session_date')


def test_import_settings_skips_unknown(flask_app):
    client = csrf_client(flask_app)
    data = client.post('/api/settings/import', headers=H, json={
        'settings': {'appearance.theme': 'dark', 'ancient.key': 1}}).get_json()
    assert data['success']
    assert [c['key'] for c in data['changed']] == ['appearance.theme']
    assert data['skipped'] == ['ancient.key']


# ── Favourites and set logs ──────────────────────────────────────────

def test_favorites_guest_roundtrip(flask_app):
    client = csrf_client(flask_app)
    data = client.post('/api/exercises/favorite', headers=H,
                       json={'name': 'Push-ups', 'category': 'Strength & Power'}).get_json()
    assert data['favorited'] is True
    assert 'Strength & Power|Push-ups' in client.get('/api/exercises/favorites').get_json()['favorites']
    data = client.post('/api/exercises/favorite', headers=H,
                       json={'name': 'Push-ups', 'category': 'Strength & Power'}).get_json()
    assert data['favorited'] is False


def test_exercise_logs_guest_not_persisted(flask_app):
    client = csrf_client(flask_app)
    data = client.post('/api/logs', headers=H, json={
        'logs': [{'name': 'Push-ups', 'set': 1, 'reps': 10}]}).get_json()
    assert data['success'] and data['persisted'] is False


def test_exercise_logs_and_records_logged_in(logged_in_client):
    with logged_in_client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    data = logged_in_client.post('/api/logs', headers=H, json={'logs': [
        {'name': 'Push-ups', 'category': 'Strength & Power', 'set': 1, 'reps': 12, 'weight': 20},
        {'name': 'Push-ups', 'category': 'Strength & Power', 'set': 2, 'reps': 10, 'weight': 22.5},
        {'bogus': 'row'},
    ]}).get_json()
    assert data['persisted'] is True and data['saved'] == 2
    records = logged_in_client.get('/api/logs/records').get_json()['records']
    push = next(r for r in records if r['exercise_name'] == 'Push-ups')
    assert push['best_weight'] == 22.5 and push['best_reps'] == 12


def test_settings_page_renders_every_group(client):
    html = client.get('/settings').data.decode('utf-8')
    for group in reg.GROUP_ORDER:
        escaped = group.replace('&', '&amp;')  # Jinja autoescaping
        assert escaped in html, f'group {group} missing from settings page'
