"""FITTRACK_MODE: what the hosted site drops, and what it refuses to do.

The ``hosted`` fixture sets the real environment variables and re-runs
``config.load()``, then re-filters the registry the way a hosted process
would have at import.
"""
import json
import urllib.request

import pytest

import app as app_module
import config
import settings_judge
import settings_registry as reg
from conftest import CSRF

H = {'X-CSRF-Token': CSRF}

HOSTED_HIDDEN = ('ai.server_url', 'ai.model', 'ai.enabled', 'ai.workout_generation',
                 'ai.timeout_seconds', 'ai.fallback_notice')


def _apply_mode():
    reg.apply_mode(config.MODE)
    settings_judge.refresh_options()


@pytest.fixture()
def hosted(monkeypatch):
    monkeypatch.setenv('FITTRACK_MODE', 'hosted')
    monkeypatch.setenv('SECRET_KEY', 'test-secret')
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    config.load()
    _apply_mode()
    yield
    monkeypatch.undo()
    config.load()
    _apply_mode()


@pytest.fixture()
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('hosted mode reached a model server')
    monkeypatch.setattr(urllib.request, 'urlopen', refuse)


def _guest(client, overrides=None):
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
        if overrides is not None:
            s['settings'] = overrides
    return client


# ── Mode plumbing ────────────────────────────────────────────────────

def test_local_is_the_default(monkeypatch):
    monkeypatch.delenv('FITTRACK_MODE', raising=False)
    config.load()
    assert config.MODE == 'local' and config.IS_LOCAL and not config.IS_HOSTED
    assert 'ai.server_url' in reg.SETTINGS_BY_KEY


def test_hosted_refuses_to_start_without_secret_key(monkeypatch):
    monkeypatch.setenv('FITTRACK_MODE', 'hosted')
    monkeypatch.delenv('SECRET_KEY', raising=False)
    config.load()
    try:
        with pytest.raises(config.ConfigError, match='SECRET_KEY'):
            config.validate()
    finally:
        monkeypatch.undo()
        config.load()


def test_unknown_mode_is_refused(monkeypatch):
    monkeypatch.setenv('FITTRACK_MODE', 'cloud')
    config.load()
    try:
        with pytest.raises(config.ConfigError, match='FITTRACK_MODE'):
            config.validate()
    finally:
        monkeypatch.undo()
        config.load()


def test_hosted_never_runs_the_debugger(hosted, monkeypatch):
    monkeypatch.setenv('FLASK_DEBUG', '1')
    config.load()
    assert config.FLASK_DEBUG is False


# ── Hidden settings: gone from every surface the registry feeds ──────

def test_hidden_settings_missing_from_registry(hosted):
    keys = {s.key for s in reg.SETTINGS}
    for key in HOSTED_HIDDEN:
        assert key not in keys and key not in reg.SETTINGS_BY_KEY
        assert key not in reg.defaults()
    # Still defined, just not in this mode.
    assert {s.key for s in reg.ALL_SETTINGS} >= set(HOSTED_HIDDEN)
    # Unhidden settings are untouched.
    assert 'appearance.theme' in keys and 'ai.judge_enabled' in keys


def test_hidden_settings_missing_from_api_and_palette(hosted, client):
    data = client.get('/api/settings').get_json()
    schema_keys = {s['key'] for s in data['schema']}
    for key in HOSTED_HIDDEN:
        assert key not in schema_keys and key not in data['values']


def test_hidden_settings_missing_from_settings_page(hosted, client):
    html = client.get('/settings').get_data(as_text=True)
    for key in HOSTED_HIDDEN:
        assert key not in html


def test_hidden_settings_rejected_by_apply_changes(hosted, client):
    _guest(client)
    resp = client.post('/api/settings', headers=H,
                       json={'changes': [{'key': 'ai.server_url',
                                          'value': 'http://169.254.169.254/latest'}]})
    data = resp.get_json()
    assert data['success'] and not data['changed']
    assert data['rejected'][0]['key'] == 'ai.server_url'


def test_hidden_settings_missing_from_assistant(hosted):
    targets = {t.split('=')[0] for t in settings_judge.OPTION_IDS.values()}
    for key in HOSTED_HIDDEN:
        assert key not in targets
    for query in ('change the model server address', 'use a different model name'):
        assert all(c['key'] not in HOSTED_HIDDEN for c in reg.match_intent(query))


def test_hidden_settings_missing_from_search(hosted, client):
    data = client.get('/api/settings/search?q=server url endpoint').get_json()
    assert 'ai.server_url' not in json.dumps(data)


def test_local_still_lists_lm_studio_options(client):
    data = client.get('/api/settings').get_json()
    schema_keys = {s['key'] for s in data['schema']}
    assert set(HOSTED_HIDDEN) <= schema_keys


# ── SSRF: a stored ai.server_url is never used in hosted mode ────────

def test_hosted_ignores_stored_server_url(hosted, client, no_network):
    evil = 'http://169.254.169.254/latest'
    stored = {'ai.server_url': evil, 'ai.enabled': True, 'ai.workout_generation': True}
    _guest(client, stored)
    with app_module.app.test_request_context():
        from flask import session
        session['settings'] = stored
        values = app_module.get_settings()
        assert 'ai.server_url' not in values
    # Even handed the raw value, the endpoint resolver keeps the server's own.
    resolved = app_module.ai_config(stored)
    assert resolved['base_url'] == app_module.LM_STUDIO_API_URL
    assert resolved['enabled'] is False
    assert app_module._post_chat_completion(
        [{'role': 'user', 'content': 'x'}],
        model_config={'base_url': evil, 'model': 'm'}) is None


def test_hosted_workout_uses_rules_with_stored_ai_settings(hosted, client, no_network):
    _guest(client, {'ai.server_url': 'http://10.0.0.1:1234/v1', 'ai.enabled': True,
                    'ai.workout_generation': True})
    resp = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'})
    data = resp.get_json()
    assert resp.status_code == 200 and data['exercises']
    assert data.get('engine', 'rules') == 'rules'


def test_local_still_honours_server_url():
    resolved = app_module.ai_config({'ai.server_url': 'http://127.0.0.1:9/v1',
                                     'ai.model': 'm', 'ai.enabled': True})
    assert resolved['base_url'] == 'http://127.0.0.1:9/v1' and resolved['enabled']


# ── Custom CSS stays inside its <style> block ────────────────────────

def test_custom_css_cannot_escape_style_block(client):
    """A value that skipped coerce (hand-edited row, old cookie) is still cleaned.

    Two layers: merge() re-coerces every stored value, stripping < and >, and
    Jinja autoescapes the rest. Either alone stops `</style>`.
    """
    payload = 'body{color:red}</style><script>alert(1)</script><style>'
    _guest(client, {'appearance.custom_css': payload})
    html = client.get('/').get_data(as_text=True)
    start = html.index('<style id="user-custom-css">')
    block = html[start:html.index('</style>', start) + len('</style>')]
    assert 'body{color:red}' in block
    assert '<script>alert(1)' not in html
    assert block.count('</style>') == 1 and block.endswith('</style>')
