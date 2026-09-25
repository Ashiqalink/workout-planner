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
from conftest import CSRF, register_user

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


# ── Local mode: saved secret key, single user ────────────────────────

def test_local_keeps_its_secret_key_across_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'SECRET_KEY', '')
    first, source = config.secret_key(str(tmp_path))
    assert source == 'created' and len(first) == 64
    # A restart re-reads the environment and finds the saved file.
    config.load()
    monkeypatch.setattr(config, 'SECRET_KEY', '')
    second, source = config.secret_key(str(tmp_path))
    assert (second, source) == (first, 'file')


def test_secret_key_env_wins_over_saved_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'SECRET_KEY', '')
    config.secret_key(str(tmp_path))
    monkeypatch.setattr(config, 'SECRET_KEY', 'from-env')
    assert config.secret_key(str(tmp_path)) == ('from-env', 'env')


def test_unwritable_instance_dir_falls_back_to_random(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'SECRET_KEY', '')
    blocker = tmp_path / 'not-a-dir'
    blocker.write_text('x')
    key, source = config.secret_key(str(blocker / 'instance'))
    assert source == 'random' and key


@pytest.fixture()
def single_user(monkeypatch):
    monkeypatch.setattr(config, 'LOCAL_SINGLE_USER', True)


def test_single_user_mode_skips_login(single_user, client):
    resp = client.get('/login')
    assert resp.status_code == 302 and 'login' not in resp.location
    assert client.get('/register').status_code == 302
    with client.session_transaction() as s:
        user_id = s['user_id']
    assert (user_id, s['username']) == app_module.local_user()
    # A second browser is the same person.
    other = app_module.app.test_client()
    other.get('/')
    with other.session_transaction() as s:
        assert s['user_id'] == user_id
    html = client.get('/').get_data(as_text=True)
    assert 'Log out' not in html and 'Log in' not in html


def test_single_user_data_is_saved_to_the_account(single_user, client):
    _guest(client)
    client.post('/api/settings', headers=H, json={'appearance.theme': 'dark'})
    with client.session_transaction() as s:
        user_id = s['user_id']
        assert 'settings' not in s  # not the guest cookie
    assert json.loads(app_module.get_db_connection().execute(
        'SELECT settings_json FROM user_settings WHERE user_id = ?',
        (str(user_id),)).fetchone()[0])['appearance.theme'] == 'dark'


def test_single_user_is_off_when_disabled_or_hosted(monkeypatch, client):
    assert config.LOCAL_SINGLE_USER is False  # conftest sets LOCAL_SINGLE_USER=0
    assert client.get('/login').status_code == 200
    monkeypatch.setenv('LOCAL_SINGLE_USER', '1')
    monkeypatch.setenv('FITTRACK_MODE', 'hosted')
    config.load()
    try:
        assert config.LOCAL_SINGLE_USER is False
    finally:
        monkeypatch.undo()
        config.load()


def test_csv_reader_types_columns_like_pandas_did():
    rows = app_module.read_csv_records(
        app_module.os.path.join(app_module.DATA_DIR, 'complete_4week_program.csv'))
    assert isinstance(rows[0]['Week'], int) and isinstance(rows[0]['Duration'], int)
    library = app_module.read_csv_records(
        app_module.os.path.join(app_module.DATA_DIR, 'comprehensive_training_matrix.csv'))
    assert all(isinstance(r['duration'], float) for r in library)
    # "None" in the equipment column is a missing cell, as it was under pandas.
    assert any(r['equipment'] is None for r in library)


# ── Hosted accounts: sign-up, rate limit, export, delete ─────────────

def _form_client(flask_app):
    client = flask_app.test_client()
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    return client


def _user_count():
    return app_module.get_db_connection().execute('SELECT COUNT(*) FROM users').fetchone()[0]


def test_signup_closed_blocks_register(monkeypatch, flask_app):
    monkeypatch.setattr(config, 'ALLOW_SIGNUP', False)
    client = _form_client(flask_app)
    before = _user_count()
    resp = client.post('/register', data={
        '_csrf_token': CSRF, 'username': 'closed_door', 'email': 'c@example.com',
        'password': 'longenough', 'confirm_password': 'longenough'})
    assert resp.status_code == 302 and '/login' in resp.location
    assert _user_count() == before
    assert client.get('/register').status_code == 302
    assert 'Create one' not in client.get('/login').get_data(as_text=True)


def test_short_password_is_refused(flask_app):
    client = _form_client(flask_app)
    before = _user_count()
    resp = client.post('/register', data={
        '_csrf_token': CSRF, 'username': 'shorty_pw', 'email': 's@example.com',
        'password': 'seven77', 'confirm_password': 'seven77'})
    assert resp.status_code == 200 and _user_count() == before
    assert 'at least 8 characters' in resp.get_data(as_text=True)


def test_login_rate_limit_fires(monkeypatch, flask_app):
    monkeypatch.setattr(config, 'LOGIN_RATE_LIMIT', '3 per minute')
    client = _form_client(flask_app)
    form = {'_csrf_token': CSRF, 'username': 'nobody', 'password': 'wrong-password'}
    codes = [client.post('/login', data=form).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    assert 'Too many attempts' in client.post('/login', data=form).get_data(as_text=True)
    # Reading the page is never limited.
    assert client.get('/login').status_code == 200


def _fill_account(user_id):
    """One row in every table an account owns."""
    conn = app_module.get_db_connection()
    with conn:
        conn.execute("INSERT INTO user_sessions (user_id, session_date, session_type, "
                     "total_duration) VALUES (?, '2026-09-01', 'strength', 30)", (user_id,))
        conn.execute("INSERT INTO user_progress (user_id, domain, sessions_completed) "
                     "VALUES (?, 'strength', 1)", (user_id,))
        conn.execute("INSERT INTO saved_workouts (user_id, workout_name, exercises_json, "
                     "total_duration, difficulty) VALUES (?, ?, '[]', 30, 'Beginner')",
                     (str(user_id), f'plan-{user_id}'))
        conn.execute("INSERT OR REPLACE INTO user_settings (user_id, settings_json) "
                     "VALUES (?, '{}')", (str(user_id),))
        conn.execute("INSERT INTO exercise_logs (user_id, log_date, exercise_name) "
                     "VALUES (?, '2026-09-01', ?)", (str(user_id), f'lift-{user_id}'))
        conn.execute("INSERT INTO favorite_exercises (user_id, exercise_name, category) "
                     "VALUES (?, ?, 'strength')", (str(user_id), f'fav-{user_id}'))
    conn.close()


def _rows_for(user_id):
    conn = app_module.get_db_connection()
    counts = {t: conn.execute(f'SELECT COUNT(*) FROM {t} WHERE user_id = ?',
                              (str(user_id),)).fetchone()[0]
              for t in app_module.ACCOUNT_TABLES}
    counts['users'] = conn.execute('SELECT COUNT(*) FROM users WHERE id = ?',
                                   (user_id,)).fetchone()[0]
    conn.close()
    return counts


def _uid(client):
    with client.session_transaction() as s:
        return s['user_id']


def test_export_holds_only_this_users_data(flask_app):
    alice, bob = flask_app.test_client(), flask_app.test_client()
    alice_name = register_user(alice)
    register_user(bob)
    a, b = _uid(alice), _uid(bob)
    _fill_account(a)
    _fill_account(b)
    data = json.loads(alice.get('/api/data/export?format=json').get_data(as_text=True))
    assert data['account'] == alice_name
    dumped = json.dumps(data)
    assert f'plan-{a}' in dumped and f'lift-{a}' in dumped and f'fav-{a}' in dumped
    assert f'plan-{b}' not in dumped and f'lift-{b}' not in dumped and f'fav-{b}' not in dumped
    assert len(data['sessions']) == 1 and len(data['progress']) == 1


def test_hosted_export_leaves_out_the_shared_library(hosted, logged_in_client):
    data = json.loads(logged_in_client.get('/api/data/export?format=json').get_data(as_text=True))
    assert data['custom_exercises'] == []


def test_delete_account_removes_every_row(flask_app):
    doomed, bystander = flask_app.test_client(), flask_app.test_client()
    register_user(doomed)
    register_user(bystander)
    d, b = _uid(doomed), _uid(bystander)
    _fill_account(d)
    _fill_account(b)
    assert all(_rows_for(d).values())
    assert doomed.get('/account').status_code == 200

    # Wrong password, then a missing CSRF token: nothing goes.
    resp = doomed.post('/account/delete', data={'_csrf_token': CSRF, 'confirm': 'nope-nope'})
    assert resp.status_code == 302 and all(_rows_for(d).values())
    resp = doomed.post('/account/delete', data={'confirm': 'secret123'})
    assert resp.status_code in (302, 400, 403) and all(_rows_for(d).values())

    resp = doomed.post('/account/delete', data={'_csrf_token': CSRF, 'confirm': 'secret123'})
    assert resp.status_code == 302
    assert not any(_rows_for(d).values())
    assert all(_rows_for(b).values())
    with doomed.session_transaction() as s:
        assert 'user_id' not in s


def test_backup_keeps_the_newest_copies(tmp_path, monkeypatch):
    import sqlite3
    from scripts import backup_db
    src = tmp_path / 'live.db'
    with sqlite3.connect(src) as conn:
        conn.execute('CREATE TABLE t (x)')
        conn.execute('INSERT INTO t VALUES (42)')
    conn.close()
    out = tmp_path / 'backups'
    out.mkdir()
    for i in range(9):
        (out / f'fittrack-20260101-00000{i}.db').write_bytes(b'old')
    made = backup_db.backup(str(src), str(out), keep=7)
    kept = sorted(p.name for p in out.iterdir())
    assert len(kept) == 7 and made.endswith(kept[-1])
    copy = sqlite3.connect(made)
    assert copy.execute('SELECT x FROM t').fetchone()[0] == 42
    copy.close()
