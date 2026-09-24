"""Pytest fixtures for the FitTrack Flask app.

DATABASE_PATH must be set before `app` is imported, because the module
initializes and populates the database at import time. Every test run gets a
fresh temporary database seeded from the CSV data.
"""
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_db_fd, _db_path = tempfile.mkstemp(prefix='fittrack_test_', suffix='.db')
os.close(_db_fd)
os.environ['DATABASE_PATH'] = _db_path

import pytest  # noqa: E402
import app as app_module  # noqa: E402  (import after DATABASE_PATH is set)

CSRF = 'test-csrf-token'


@pytest.fixture(autouse=True)
def no_typesafe_key(monkeypatch):
    """No key, no socket, no memory between tests: the judge never leaves the process."""
    import config
    import workout_judge
    monkeypatch.setattr(config, 'TYPESAFE_API_KEY', '')
    monkeypatch.setattr(workout_judge, '_post',
                        lambda payload, timeout: (_ for _ in ()).throw(
                            AssertionError('test reached the network')))
    workout_judge._cache.clear()


@pytest.fixture(scope='session')
def flask_app():
    app_module.app.config.update(TESTING=True)
    return app_module.app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


def register_user(client, password='secret123'):
    """Register a unique user through the real endpoint; returns the username."""
    name = 'user_' + uuid.uuid4().hex[:10]
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    resp = client.post('/register', data={
        '_csrf_token': CSRF,
        'username': name,
        'email': f'{name}@example.com',
        'password': password,
        'confirm_password': password,
    })
    assert resp.status_code == 302, f'registration failed: {resp.status_code}'
    return name


@pytest.fixture()
def logged_in_client(flask_app):
    """A test client already registered and logged in as a fresh user."""
    client = flask_app.test_client()
    register_user(client)
    return client
