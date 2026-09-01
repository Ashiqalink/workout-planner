"""API and unit tests for the FitTrack Flask app."""
import json
from datetime import datetime as real_datetime, timedelta

import app as app_module
from conftest import CSRF, register_user


def _freeze_day(monkeypatch, weekday_name):
    """Patch app_module.datetime so 'today' is the next given weekday.

    Guests with no program_start are always week 1, so only the weekday
    matters for program-day tests.
    """
    target = real_datetime.now()
    while target.strftime('%A') != weekday_name:
        target += timedelta(days=1)

    class FakeDT(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(target.year, target.month, target.day, 10, 0, 0)

    monkeypatch.setattr(app_module, 'datetime', FakeDT)


class FakeHTTPResponse:
    """Stands in for urllib.request.urlopen's context-manager response."""
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ── Pages & health ───────────────────────────────────────────────────

def test_health(client):
    resp = client.get('/api/health')
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'ok'


def test_pages_render(client):
    for path in ('/', '/library', '/planner', '/progress', '/session', '/login', '/register'):
        resp = client.get(path)
        assert resp.status_code == 200, f'{path} returned {resp.status_code}'


def test_debug_endpoint_hidden_without_flag(client, monkeypatch):
    monkeypatch.delenv('FLASK_DEBUG', raising=False)
    assert client.get('/api/debug').status_code == 404


# ── Exercise data ────────────────────────────────────────────────────

def test_api_exercises_populated(client):
    data = client.get('/api/exercises').get_json()
    assert len(data['exercises']) > 0
    ex = data['exercises'][0]
    for field in ('name', 'category', 'duration', 'difficulty'):
        assert field in ex


def test_rest_times(client):
    data = client.get('/api/rest-times').get_json()
    assert 'Strength & Power' in data['rest_times']
    assert data['rest_times']['Strength & Power']['beginner'] > 0


# ── Workout generation (rule-based fallback, LLM stubbed out) ────────

def test_generate_workout_fallback(client, monkeypatch):
    monkeypatch.setattr(app_module, 'generate_workout_via_llm', lambda *a, **k: None)
    resp = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert len(data['exercises']) > 0
    assert 0 < data['total_duration'] <= 15
    assert all(e['category'] == 'Strength & Power' for e in data['exercises'])


def test_generate_workout_validation(client):
    assert client.post('/api/generate-workout', json={}).status_code == 400
    assert client.post('/api/generate-workout',
                       json={'domains': [], 'duration': 15}).status_code == 400
    assert client.post('/api/generate-workout',
                       json={'domains': ['Strength'], 'duration': 'abc'}).status_code == 400
    assert client.post('/api/generate-workout',
                       json={'domains': ['Strength'], 'duration': 9999}).status_code == 400


# ── Auth ─────────────────────────────────────────────────────────────

def test_login_without_csrf_rejected(client):
    resp = client.post('/login', data={'username': 'x', 'password': 'y'})
    assert resp.status_code == 403


def test_register_login_logout_flow(flask_app):
    client = flask_app.test_client()
    username = register_user(client, password='secret123')

    # Registration logs the user in
    with client.session_transaction() as s:
        assert s['username'] == username

    client.get('/logout')
    with client.session_transaction() as s:
        assert 'username' not in s

    # Log back in with the same credentials
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    resp = client.post('/login', data={
        '_csrf_token': CSRF, 'username': username, 'password': 'secret123'
    })
    assert resp.status_code == 302
    with client.session_transaction() as s:
        assert s['username'] == username


def test_login_wrong_password(client):
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    resp = client.post('/login', data={
        '_csrf_token': CSRF, 'username': 'nobody', 'password': 'wrong'
    })
    assert resp.status_code == 200
    with client.session_transaction() as s:
        assert 'username' not in s


def test_register_validation(client):
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    resp = client.post('/register', data={
        '_csrf_token': CSRF, 'username': 'ab', 'email': 'a@b.c',
        'password': 'secret123', 'confirm_password': 'secret123'
    })
    assert resp.status_code == 200  # re-renders the form instead of redirecting
    assert b'at least 3 characters' in resp.data


def test_register_duplicate_username(flask_app):
    client = flask_app.test_client()
    username = register_user(client)
    client.get('/logout')
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    resp = client.post('/register', data={
        '_csrf_token': CSRF, 'username': username, 'email': 'other@example.com',
        'password': 'secret123', 'confirm_password': 'secret123'
    })
    assert resp.status_code == 200
    assert b'already exists' in resp.data


# ── Custom exercises ─────────────────────────────────────────────────

def test_add_exercise_requires_login(client):
    resp = client.post('/api/exercises/add', json={'name': 'X', 'category': 'Endurance'})
    assert resp.status_code == 401


def test_add_exercise_and_duplicate(logged_in_client):
    payload = {
        'name': 'Test Unique Exercise', 'category': 'Endurance',
        'duration': 1.5, 'difficulty': 'beginner',
        'description': 'test', 'target_muscles': 'legs', 'instructions': 'go'
    }
    resp = logged_in_client.post('/api/exercises/add', json=payload)
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True

    # Same (category, name) again → 409 from the UNIQUE constraint
    resp = logged_in_client.post('/api/exercises/add', json=payload)
    assert resp.status_code == 409

    # It shows up in the library
    names = [e['name'] for e in logged_in_client.get('/api/exercises').get_json()['exercises']]
    assert 'Test Unique Exercise' in names


def test_add_exercise_missing_fields(logged_in_client):
    resp = logged_in_client.post('/api/exercises/add', json={'name': '', 'category': ''})
    assert resp.status_code == 400


# ── Saved workouts ───────────────────────────────────────────────────

def test_save_workout_requires_login(client):
    resp = client.post('/api/workouts/save', json={'workout_name': 'W', 'exercises': [{}]})
    assert resp.status_code == 401


def test_save_and_list_workout(logged_in_client):
    resp = logged_in_client.post('/api/workouts/save', json={
        'workout_name': 'My Template',
        'workout_description': 'desc',
        'exercises': [{'name': 'Push-ups', 'category': 'Strength & Power', 'duration': 0.5}],
        'total_duration': 0.5,
        'difficulty': 'beginner'
    })
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True

    data = logged_in_client.get('/api/workouts/saved').get_json()
    assert data['success'] is True
    names = [w['workout_name'] for w in data['saved_workouts']]
    assert 'My Template' in names
    saved = next(w for w in data['saved_workouts'] if w['workout_name'] == 'My Template')
    assert saved['exercises'][0]['name'] == 'Push-ups'


# ── Session completion & progress ────────────────────────────────────

SESSION_PAYLOAD = {
    'exercises': [
        {'name': 'Push-ups', 'category': 'Strength & Power', 'duration': 0.5, 'difficulty': 'beginner'},
        {'name': 'Squats', 'category': 'Strength & Power', 'duration': 0.5, 'difficulty': 'beginner'},
    ],
    'totalDuration': 12,
    'completedExercises': 2,
    'domainProgress': {'strength_power': {'exercises': 2, 'minutes': 1}},
    'sessionType': 'custom',
}


def test_complete_session_guest(client):
    resp = client.post('/api/complete_session', json=SESSION_PAYLOAD)
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'success'

    progress = client.get('/api/user_progress').get_json()
    assert progress['total_sessions'] == 1
    assert progress['total_minutes'] == 12
    assert progress['total_exercises'] == 2
    assert progress['current_streak'] >= 1
    assert sum(progress['weekly_progress']) == 12


def test_complete_session_logged_in(logged_in_client):
    resp = logged_in_client.post('/api/complete_session', json=SESSION_PAYLOAD)
    assert resp.status_code == 200

    progress = logged_in_client.get('/api/user_progress').get_json()
    assert progress['total_sessions'] == 1
    assert progress['total_minutes'] == 12
    assert progress['total_exercises'] == 2
    assert progress['strength']['sessions'] == 1
    assert progress['strength']['minutes'] == 1
    assert progress['current_streak'] >= 1
    assert sum(progress['weekly_progress']) == 12


def test_user_progress_guest_default(client):
    # Hitting the progress API before any page visit must not crash for guests
    progress = client.get('/api/user_progress').get_json()
    assert progress['total_sessions'] == 0


# ── Program: week progression & prescribed workouts ──────────────────

def test_new_cognitive_exercises_in_library(client):
    names = [e['name'] for e in client.get('/api/exercises').get_json()['exercises']]
    for name in ('Mental Math', 'Mindfulness Practice', 'Dual N-Back',
                 'Working Memory Games', 'Brain Teasers', 'Cardio Intervals'):
        assert name in names, f'{name} missing from library'


def test_today_workout_endpoint(client):
    resp = client.post('/api/today-workout')
    assert resp.status_code == 200
    data = resp.get_json()
    if data['success']:
        # A prescribed day: real exercises with prescribed durations
        assert len(data['exercises']) > 0
        assert data['total_duration'] > 0
        assert data['week'] in (1, 2, 3, 4)
        assert all(ex['duration'] > 0 for ex in data['exercises'])
        assert data['difficulty'] in ('beginner', 'intermediate', 'advanced')
    else:
        # A freeform day: must explicitly ask the client to fall back
        assert data['fallback'] is True


def test_week_progression_guest(client):
    from datetime import datetime, timedelta
    # No program started yet → week 1
    resp = client.get('/api/user_progress').get_json()
    assert resp['strength']['week'] == 1

    # Program started 8 days ago → week 2
    with client.session_transaction() as s:
        s['user_id'] = 'guest'
        s['program_start'] = (datetime.now() - timedelta(days=8)).strftime('%Y-%m-%d')
    resp = client.get('/api/user_progress').get_json()
    assert resp['strength']['week'] == 2


def test_week_cycles_after_four_weeks(client):
    from datetime import datetime, timedelta
    # 30 days in → 4 full weeks elapsed → cycles back to week 1
    with client.session_transaction() as s:
        s['user_id'] = 'guest'
        s['program_start'] = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    resp = client.get('/api/user_progress').get_json()
    assert resp['strength']['week'] == 1


def test_first_session_starts_program(client):
    client.post('/api/complete_session', json=SESSION_PAYLOAD)
    with client.session_transaction() as s:
        assert 'program_start' in s
    resp = client.get('/api/user_progress').get_json()
    assert resp['strength']['week'] == 1


def test_parse_prescription():
    f = app_module.parse_prescription
    assert f('Push-ups (2min), Plank (3min)') == [('Push-ups', 2), ('Plank', 3)]
    assert f('Jump Rope (Imaginary) (2min)') == [('Jump Rope (Imaginary)', 2)]
    assert f('All categories mini-circuit') == []
    assert f('') == []
    assert f(None) == []


def test_resolve_prescribed_exercises(client):
    resolved = app_module.resolve_prescribed_exercises(
        [('Mindfulness', 4), ('Glute Bridges', 2), ('Push-ups', 2)])
    by_name = {ex['name']: ex for ex in resolved}
    # Aliases resolve to real library entries with metadata
    assert 'Mindfulness Practice' in by_name
    assert 'Glute Bridge' in by_name
    assert by_name['Mindfulness Practice']['category'] == 'Cognition'
    assert by_name['Mindfulness Practice']['instructions']
    # Prescribed minutes override library defaults
    assert by_name['Mindfulness Practice']['duration'] == 4.0
    assert by_name['Push-ups']['duration'] == 2.0


def test_intensity_to_difficulty():
    f = app_module.intensity_to_difficulty
    assert f('Light') == 'beginner'
    assert f('Light-Moderate') == 'intermediate'
    assert f('Moderate') == 'intermediate'
    assert f('Moderate-High') == 'advanced'
    assert f('Very High') == 'advanced'
    assert f('Maximum') == 'advanced'
    assert f(None) == 'beginner'


def test_detailed_program_prescriptions_resolve():
    """Every prescribed exercise across all 4 weeks must exist in the library."""
    unresolved = []
    for entry in app_module.training_data['student_program']:
        for name, minutes in app_module.parse_prescription(entry.get('Exercises')):
            resolved = app_module.resolve_prescribed_exercises([(name, minutes)])[0]
            if resolved['category'] == 'General':
                unresolved.append(f"week {entry['Week']} {entry['Day']}: {name}")
    assert not unresolved, f'Prescribed exercises missing from library: {unresolved}'


def test_program_covers_four_weeks():
    weeks = {int(e['Week']) for e in app_module.training_data['student_program']}
    assert weeks == {1, 2, 3, 4}


# ── Unit tests ───────────────────────────────────────────────────────

def test_count_completed_exercises():
    f = app_module.count_completed_exercises
    assert f({'completedExercises': 3}) == 3
    assert f({'completedExercises': 0, 'exercises': [1, 2]}) == 2
    assert f({'completedExercises': 'bad', 'exercises': [1]}) == 1
    assert f({'completedExercises': -5, 'exercises': []}) == 0
    assert f({}) == 0


def test_weekly_buckets():
    from datetime import datetime, timedelta
    today = datetime.now().date()
    day = lambda n: (today - timedelta(days=n)).strftime('%Y-%m-%d')

    buckets = app_module._weekly_buckets([
        (day(0), 10),      # this week
        (day(8), 20),      # last week
        (day(15), 30),     # 2 weeks ago
        (day(22), 40),     # 3 weeks ago
        (day(40), 99),     # too old — ignored
        ('not-a-date', 5), # unparseable — ignored
        (None, 5),         # unparseable — ignored
    ])
    assert buckets == [40, 30, 20, 10]


# ── AI workout generation (LLM path, mocked LM Studio) ───────────────

# A factual claim nothing in the app computes. The fake responses below embed it
# to simulate a server that ignored response_format and let the model write prose
# anyway; no test may ever find this string in an API response.
FABRICATED_CLAIM = ('This circuit puts you in the fat-burning heart rate zone '
                    'and raises your IQ.')

LLM_WORKOUT = {
    'exercises': [
        {'name': 'Push-ups', 'category': 'Strength & Power', 'duration': 5,
         'difficulty': 'beginner', 'description': 'd', 'target_muscles': 'chest'},
        {'name': 'Plank', 'category': 'Strength & Power', 'duration': 7,
         'difficulty': 'beginner', 'description': 'd', 'target_muscles': 'core'},
    ],
    'total_duration': 999,  # deliberately wrong — server must recompute
    'explanation': FABRICATED_CLAIM,  # must never reach the user
}


def _fake_chat_completion(content: str) -> bytes:
    return json.dumps({'choices': [{'message': {'content': content}}]}).encode('utf-8')


def _ai_workouts_on(monkeypatch):
    """AI generation ships off (the rule engine benchmarked better), so the
    tests that exercise the model path have to switch it on first."""
    original = app_module.get_settings
    monkeypatch.setattr(app_module, 'get_settings',
                        lambda: dict(original(), **{'ai.workout_generation': True}))


def test_generate_workout_llm_success(client, monkeypatch):
    """LLM returns markdown-fenced JSON → endpoint strips fences, recomputes totals."""
    monkeypatch.setattr(app_module, 'get_loaded_model', lambda: 'test-model')
    _ai_workouts_on(monkeypatch)
    body = _fake_chat_completion('```json\n' + json.dumps(LLM_WORKOUT) + '\n```')
    monkeypatch.setattr(app_module.urllib.request, 'urlopen',
                        lambda req, data=None, timeout=None: FakeHTTPResponse(body))

    resp = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 6, 'difficulty': 'beginner',
        'goal': 'stronger core'
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['engine'] == 'ai'
    assert sorted(e['name'] for e in data['exercises']) == ['Plank', 'Push-ups']
    # The model chose the two names; every number here is the engine's. Its
    # 5- and 7-minute "sets" are discarded in favour of the library's 30 s,
    # and the total is the wall clock including rest, inside the 6 asked for.
    assert all(e['set_seconds'] == 30 and e['sets'] >= 2 for e in data['exercises'])
    assert 0 < data['total_duration'] <= 6
    # The explanation is built from the resolved workout, never taken from the
    # model — see learn/07-truth-boundary.md.
    assert FABRICATED_CLAIM not in json.dumps(data)
    assert data['explanation'].startswith(
        f"A {data['total_duration']:g}-minute beginner session across 2 exercises: "
        '2 Strength & Power.')
    assert 'sets in total' in data['explanation']
    assert data['workout_id'].startswith('workout_')


def test_generate_workout_llm_garbage_falls_back(client, monkeypatch):
    """Unparseable LLM output must fall back to the rule-based generator."""
    monkeypatch.setattr(app_module, 'get_loaded_model', lambda: 'test-model')
    _ai_workouts_on(monkeypatch)
    body = _fake_chat_completion('sorry, I cannot produce JSON today')
    monkeypatch.setattr(app_module.urllib.request, 'urlopen',
                        lambda req, data=None, timeout=None: FakeHTTPResponse(body))

    resp = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert len(data['exercises']) > 0
    assert all(e['category'] == 'Strength & Power' for e in data['exercises'])


def test_generate_workout_unknown_domain(client, monkeypatch):
    """Unknown domains don't crash; they just produce an empty workout."""
    monkeypatch.setattr(app_module, 'generate_workout_via_llm', lambda *a, **k: None)
    resp = client.post('/api/generate-workout', json={
        'domains': ['NotARealDomain'], 'duration': 15, 'difficulty': 'beginner'
    })
    assert resp.status_code == 200
    assert resp.get_json()['exercises'] == []


# ── Small-model hardening: validation, clamping, retry, schema ───────

VALIDATION_CANDIDATES = [
    {'name': 'Push-ups', 'category': 'Strength & Power', 'duration': 0.5, 'difficulty': 'beginner'},
    {'name': 'Plank', 'category': 'Strength & Power', 'duration': 0.5, 'difficulty': 'beginner'},
]


def test_validate_llm_selection():
    f = app_module.validate_llm_selection

    # Valid pick, case-insensitive name match, library metadata joined back
    ex, err = f({'exercises': [{'name': 'push-UPS', 'duration': 5}]},
                VALIDATION_CANDIDATES, 5)
    assert err is None
    assert ex[0]['name'] == 'Push-ups'
    assert ex[0]['category'] == 'Strength & Power'
    assert ex[0]['duration'] == 5.0

    # Hallucinated exercise → rejected, error names the offender
    ex, err = f({'exercises': [{'name': 'Bench Press', 'duration': 5}]},
                VALIDATION_CANDIDATES, 5)
    assert ex is None and 'Bench Press' in err

    # Empty / malformed selections → rejected
    assert f({'exercises': []}, VALIDATION_CANDIDATES, 5)[0] is None
    assert f({}, VALIDATION_CANDIDATES, 5)[0] is None
    assert f('not even a dict', VALIDATION_CANDIDATES, 5)[0] is None
    assert f({'exercises': ['just a string']}, VALIDATION_CANDIDATES, 5)[0] is None

    # Non-numeric duration → falls back to the candidate's default
    ex, err = f({'exercises': [{'name': 'Push-ups', 'duration': 'lots'}]},
                VALIDATION_CANDIDATES, 0.5)
    assert err is None and ex[0]['duration'] == 0.5

    # Total wildly off target → rejected with a corrective message
    ex, err = f({'exercises': [{'name': 'Push-ups', 'duration': 10}]},
                VALIDATION_CANDIDATES, 30)
    assert ex is None and '30' in err


def test_generate_workout_llm_durations_are_ignored(client, monkeypatch):
    """The model does not get to decide how long a set is; the library does.

    A small model asked for "minutes of one set" answers 0.2 or 100 often
    enough. Both are discarded: set length is the library's number, and the
    planned session still fits the time the user asked for.
    """
    monkeypatch.setattr(app_module, 'get_loaded_model', lambda: 'test-model')
    _ai_workouts_on(monkeypatch)
    body = _fake_chat_completion(json.dumps({
        'exercises': [{'name': 'Push-ups', 'duration': 0.2},
                      {'name': 'Plank', 'duration': 100}],
        'explanation': 'Clamp me',
    }))
    monkeypatch.setattr(app_module.urllib.request, 'urlopen',
                        lambda req, data=None, timeout=None: FakeHTTPResponse(body))

    resp = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 6, 'difficulty': 'beginner'
    })
    data = resp.get_json()
    assert data['success'] is True
    assert {e['set_seconds'] for e in data['exercises']} == {30}
    assert 0 < data['total_duration'] <= 6


def test_generate_workout_llm_hallucinated_names_fall_back(client, monkeypatch):
    """Valid JSON with made-up exercises must be rejected → rule-based fallback."""
    monkeypatch.setattr(app_module, 'get_loaded_model', lambda: 'test-model')
    _ai_workouts_on(monkeypatch)
    calls = []
    body = _fake_chat_completion(json.dumps({
        'exercises': [{'name': 'Bench Press', 'duration': 10}],
        'explanation': FABRICATED_CLAIM,
    }))

    def fake_urlopen(req, data=None, timeout=None):
        calls.append(1)
        return FakeHTTPResponse(body)

    monkeypatch.setattr(app_module.urllib.request, 'urlopen', fake_urlopen)
    resp = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'
    })
    data = resp.get_json()
    assert data['success'] is True
    assert len(calls) == 2  # initial attempt + one corrective retry
    assert FABRICATED_CLAIM not in json.dumps(data)
    assert 'sets in total' in data['explanation']  # engine-built, rule-based path
    assert all(e['category'] == 'Strength & Power' for e in data['exercises'])


def test_generate_workout_llm_off_target_total_falls_back(client, monkeypatch):
    """A total far from the requested duration is a semantic failure → fallback."""
    monkeypatch.setattr(app_module, 'get_loaded_model', lambda: 'test-model')
    _ai_workouts_on(monkeypatch)
    body = _fake_chat_completion(json.dumps({
        'exercises': [{'name': 'Push-ups', 'duration': 14},
                      {'name': 'Plank', 'duration': 14}],
        'explanation': FABRICATED_CLAIM,
    }))
    monkeypatch.setattr(app_module.urllib.request, 'urlopen',
                        lambda req, data=None, timeout=None: FakeHTTPResponse(body))

    resp = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 10, 'difficulty': 'beginner'
    })
    data = resp.get_json()
    assert data['success'] is True
    assert FABRICATED_CLAIM not in json.dumps(data)
    assert 'sets in total' in data['explanation']  # engine-built, rule-based path


def test_generate_workout_llm_retry_recovers(client, monkeypatch):
    """First response is garbage; the corrective retry succeeds and is served."""
    monkeypatch.setattr(app_module, 'get_loaded_model', lambda: 'test-model')
    _ai_workouts_on(monkeypatch)
    responses = [
        _fake_chat_completion('not json at all'),
        _fake_chat_completion(json.dumps({
            'exercises': [{'name': 'Push-ups', 'duration': 6},
                          {'name': 'Plank', 'duration': 6}],
            'explanation': FABRICATED_CLAIM,
        })),
    ]
    sent_payloads = []

    def fake_urlopen(req, data=None, timeout=None):
        sent_payloads.append(json.loads(data.decode('utf-8')))
        return FakeHTTPResponse(responses.pop(0))

    monkeypatch.setattr(app_module.urllib.request, 'urlopen', fake_urlopen)
    resp = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 6, 'difficulty': 'beginner'
    })
    data = resp.get_json()
    assert data['success'] is True
    assert data['engine'] == 'ai'
    assert FABRICATED_CLAIM not in json.dumps(data)
    assert sorted(e['name'] for e in data['exercises']) == ['Plank', 'Push-ups']
    assert 0 < data['total_duration'] <= 6
    # The retry conversation must include the rejected reply and the error
    retry_messages = sent_payloads[1]['messages']
    assert retry_messages[-2]['role'] == 'assistant'
    assert 'rejected' in retry_messages[-1]['content']


def test_llm_request_enforces_schema(client, monkeypatch):
    """Every LM Studio call must ship structured-output enforcement."""
    monkeypatch.setattr(app_module, 'get_loaded_model', lambda: 'test-model')
    _ai_workouts_on(monkeypatch)
    sent_payloads = []
    body = _fake_chat_completion(json.dumps({
        'exercises': [{'name': 'Push-ups', 'duration': 12}],
        'explanation': 'ok',
    }))

    def fake_urlopen(req, data=None, timeout=None):
        sent_payloads.append(json.loads(data.decode('utf-8')))
        return FakeHTTPResponse(body)

    monkeypatch.setattr(app_module.urllib.request, 'urlopen', fake_urlopen)
    client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 12, 'difficulty': 'beginner'
    })
    payload = sent_payloads[0]
    assert payload['response_format']['type'] == 'json_schema'
    assert payload['response_format']['json_schema']['name'] == 'workout_selection'
    assert payload['temperature'] == 0
    # Candidates are sent as a short grouped list, not a JSON dump of the pool
    prompt = payload['messages'][1]['content']
    assert 'Strength & Power:' in prompt
    assert '. Push-ups ' in prompt
    assert prompt.count('. ') <= app_module.MODEL_SHORTLIST + 8
    assert 'Choose exactly' in prompt


# ── Truth boundary ───────────────────────────────────────────────────
# The model selects; the engine speaks. See learn/07-truth-boundary.md.

def test_schema_gives_the_model_no_prose_field():
    """The grammar itself must make a free-text claim impossible to emit."""
    schema = app_module.WORKOUT_SELECTION_SCHEMA['schema']
    assert set(schema['properties']) == {'exercises'}
    assert schema['required'] == ['exercises']
    assert schema['additionalProperties'] is False
    item = schema['properties']['exercises']['items']
    assert set(item['properties']) == {'name', 'duration'}
    assert item['additionalProperties'] is False
    # And nothing in the prompt asks for prose it could smuggle back
    assert 'explanation' not in json.dumps(app_module.WORKOUT_SELECTION_SCHEMA)


def test_prompt_does_not_request_prose(client, monkeypatch):
    monkeypatch.setattr(app_module, 'get_loaded_model', lambda: 'test-model')
    _ai_workouts_on(monkeypatch)
    sent = []
    body = _fake_chat_completion(json.dumps({
        'exercises': [{'name': 'Push-ups', 'duration': 12}]}))

    def fake_urlopen(req, data=None, timeout=None):
        sent.append(json.loads(data.decode('utf-8')))
        return FakeHTTPResponse(body)

    monkeypatch.setattr(app_module.urllib.request, 'urlopen', fake_urlopen)
    client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 12, 'difficulty': 'beginner'})
    system = sent[0]['messages'][0]['content']
    assert 'No prose, no explanation, no extra fields' in system
    assert '"explanation"' not in json.dumps(sent[0]['messages'])


def test_build_workout_explanation_uses_only_engine_values():
    out = app_module.build_workout_explanation([
        {'name': 'Push-ups', 'category': 'Strength & Power', 'duration': 3},
        {'name': 'Plank', 'category': 'Strength & Power', 'duration': 4.5},
        {'name': 'High Knees', 'category': 'Endurance', 'duration': 2},
    ], 'intermediate')
    assert out == ('A 9.5-minute intermediate session across 3 exercises: '
                   '2 Strength & Power, 1 Endurance. '
                   'Longest block: Plank, 4.5 min.')


def test_build_workout_explanation_edge_cases():
    assert app_module.build_workout_explanation([], 'beginner') == \
        'No exercises matched your selection.'

    one = app_module.build_workout_explanation(
        [{'name': 'Plank', 'category': 'Strength & Power', 'duration': 5}], 'beginner')
    assert '1 exercise:' in one  # singular, not "1 exercises"

    # A missing/unparseable duration must not raise
    messy = app_module.build_workout_explanation(
        [{'name': 'Mystery', 'category': None, 'duration': None},
         {'name': 'Plank', 'category': 'Strength & Power', 'duration': 'x'}], 'beginner')
    assert '0-minute' in messy and 'General' in messy

    with_focus = app_module.build_workout_explanation(
        [{'name': 'Plank', 'category': 'Strength & Power', 'duration': 5}],
        'beginner', focus='core stability')
    assert with_focus.endswith('Focus: core stability.')


def test_explanation_is_stable_for_the_same_workout():
    """Same workout in a different order → same sentence (no dict-order leak)."""
    a = [{'name': 'Push-ups', 'category': 'Strength & Power', 'duration': 3},
         {'name': 'High Knees', 'category': 'Endurance', 'duration': 2},
         {'name': 'Plank', 'category': 'Strength & Power', 'duration': 4}]
    b = [a[1], a[2], a[0]]
    assert app_module.build_workout_explanation(a, 'beginner') == \
        app_module.build_workout_explanation(b, 'beginner')


def test_get_loaded_model_caches_success(monkeypatch):
    calls = []

    def fake_urlopen(req, data=None, timeout=None):
        calls.append(1)
        # LM Studio native API shape: type and state let the app tell a loaded
        # chat model from a downloaded embedding model.
        return FakeHTTPResponse(json.dumps({
            'data': [{'id': 'my-model', 'type': 'llm', 'state': 'loaded'}]
        }).encode())

    monkeypatch.setattr(app_module, '_cached_model', None)
    monkeypatch.setattr(app_module.urllib.request, 'urlopen', fake_urlopen)
    assert app_module.get_loaded_model() == 'my-model'
    assert app_module.get_loaded_model() == 'my-model'
    assert len(calls) == 1  # second call served from cache


def test_get_loaded_model_skips_embeddings_and_unloaded(monkeypatch):
    """An embedding model must never be chosen for chat, and a model that is
    merely downloaded (not loaded) must not be cached — the user may load a
    different one next."""
    def fake_urlopen(req, data=None, timeout=None):
        return FakeHTTPResponse(json.dumps({'data': [
            {'id': 'nomic-embed-text', 'type': 'embeddings', 'state': 'loaded'},
            {'id': 'small-chat-model', 'type': 'llm', 'state': 'not-loaded'},
        ]}).encode())

    monkeypatch.setattr(app_module, '_cached_model', None)
    monkeypatch.setattr(app_module.urllib.request, 'urlopen', fake_urlopen)
    assert app_module.get_loaded_model() == 'small-chat-model'
    assert app_module._cached_model is None  # nothing loaded -> nothing cached


def test_get_loaded_model_prefers_smallest_for_jit(monkeypatch):
    """With nothing loaded, the smallest chat model is named so LM Studio's
    just-in-time load finishes in seconds rather than minutes."""
    def fake_urlopen(req, data=None, timeout=None):
        return FakeHTTPResponse(json.dumps({'data': [
            {'id': 'big-model-26b', 'type': 'vlm', 'state': 'not-loaded'},
            {'id': 'tiny-model-0.5b', 'type': 'llm', 'state': 'not-loaded'},
            {'id': 'mid-model-7b', 'type': 'llm', 'state': 'not-loaded'},
        ]}).encode())

    monkeypatch.setattr(app_module, '_cached_model', None)
    monkeypatch.setattr(app_module.urllib.request, 'urlopen', fake_urlopen)
    assert app_module.get_loaded_model() == 'tiny-model-0.5b'


def test_get_loaded_model_failure_not_cached(monkeypatch):
    def fake_urlopen(req, data=None, timeout=None):
        raise OSError('LM Studio not running')

    monkeypatch.setattr(app_module, '_cached_model', None)
    monkeypatch.setattr(app_module.urllib.request, 'urlopen', fake_urlopen)
    assert app_module.get_loaded_model() == 'lm-studio'
    assert app_module._cached_model is None  # failure must not be cached


# ── Today's program workout (deterministic day) ──────────────────────

def test_today_workout_prescribed_day(client, monkeypatch):
    """Week 1 Monday is a fully prescribed day: 6 exercises, 15 minutes."""
    _freeze_day(monkeypatch, 'Monday')
    resp = client.post('/api/today-workout')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['week'] == 1
    assert data['sessionType'] == 'Strength Foundation'
    assert len(data['exercises']) == 6
    assert data['total_duration'] == 15.0
    assert data['difficulty'] == 'intermediate'  # Light-Moderate intensity
    by_name = {ex['name']: ex for ex in data['exercises']}
    # Library metadata attached, prescribed minutes override defaults
    assert by_name['Push-ups']['category'] == 'Strength & Power'
    assert by_name['Push-ups']['duration'] == 2.0
    assert 'Mindfulness Practice' in by_name  # alias resolved


def test_today_workout_freeform_day_falls_back(client, monkeypatch):
    """Week 1 Saturday ('All categories mini-circuit') must ask for fallback."""
    _freeze_day(monkeypatch, 'Saturday')
    resp = client.post('/api/today-workout')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is False
    assert data['fallback'] is True


# ── Saved workouts: validation & isolation ───────────────────────────

def test_save_workout_validation(logged_in_client):
    assert logged_in_client.post('/api/workouts/save', json={}).status_code == 400
    resp = logged_in_client.post('/api/workouts/save',
                                 json={'workout_name': '', 'exercises': []})
    assert resp.status_code == 400


def test_saved_workouts_guest_empty(client):
    data = client.get('/api/workouts/saved').get_json()
    assert data['success'] is True
    assert data['saved_workouts'] == []


def test_saved_workouts_user_isolation(flask_app):
    alice = flask_app.test_client()
    register_user(alice)
    resp = alice.post('/api/workouts/save', json={
        'workout_name': 'Alice Private Workout',
        'exercises': [{'name': 'Squats', 'duration': 1}],
        'total_duration': 1, 'difficulty': 'beginner'
    })
    assert resp.status_code == 200

    bob = flask_app.test_client()
    register_user(bob)
    names = [w['workout_name']
             for w in bob.get('/api/workouts/saved').get_json()['saved_workouts']]
    assert 'Alice Private Workout' not in names


# ── Streaks & accumulation ───────────────────────────────────────────

def test_guest_streak_consecutive_days(client):
    yesterday = (real_datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    with client.session_transaction() as s:
        s['user_id'] = 'guest'
        s['streak_dates'] = [yesterday]
    client.post('/api/complete_session', json=SESSION_PAYLOAD)
    assert client.get('/api/user_progress').get_json()['current_streak'] == 2


def test_guest_streak_broken_by_gap(client):
    old = (real_datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    with client.session_transaction() as s:
        s['user_id'] = 'guest'
        s['streak_dates'] = [old]
    client.post('/api/complete_session', json=SESSION_PAYLOAD)
    assert client.get('/api/user_progress').get_json()['current_streak'] == 1


def test_multiple_sessions_accumulate(logged_in_client):
    for _ in range(2):
        assert logged_in_client.post('/api/complete_session',
                                     json=SESSION_PAYLOAD).status_code == 200
    progress = logged_in_client.get('/api/user_progress').get_json()
    assert progress['total_sessions'] == 2
    assert progress['total_minutes'] == 24
    assert progress['total_exercises'] == 4
    assert progress['strength']['sessions'] == 2
    assert progress['strength']['minutes'] == 2


def test_complete_session_count_falls_back_to_exercise_list(client):
    payload = dict(SESSION_PAYLOAD)
    payload.pop('completedExercises')
    assert client.post('/api/complete_session', json=payload).status_code == 200
    assert client.get('/api/user_progress').get_json()['total_exercises'] == 2


# ── Debug endpoint & remaining auth paths ────────────────────────────

def test_debug_endpoint_visible_with_flag(client, monkeypatch):
    monkeypatch.setenv('FLASK_DEBUG', '1')
    resp = client.get('/api/debug')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['training_data_loaded'] is True
    assert data['exercises_count'] > 0


def test_register_without_csrf_rejected(client):
    resp = client.post('/register', data={
        'username': 'someone', 'email': 'a@b.c',
        'password': 'secret123', 'confirm_password': 'secret123'
    })
    assert resp.status_code == 403


def test_login_with_email(flask_app):
    client = flask_app.test_client()
    username = register_user(client, password='secret123')
    client.get('/logout')
    with client.session_transaction() as s:
        s['_csrf_token'] = CSRF
    resp = client.post('/login', data={
        '_csrf_token': CSRF,
        'username': f'{username}@example.com',  # log in by email, not username
        'password': 'secret123',
    })
    assert resp.status_code == 302
    with client.session_transaction() as s:
        assert s['username'] == username


def test_logout_redirects_and_clears_session(logged_in_client):
    resp = logged_in_client.get('/logout')
    assert resp.status_code == 302
    with logged_in_client.session_transaction() as s:
        assert 'user_id' not in s


# ── Sets, rest and the planner ───────────────────────────────────────
# The library stores one set of an exercise; the planner turns that into a
# prescription. These tests pin the arithmetic the user is promised.

PLAN_POOL = [
    {'name': 'Push-ups', 'category': 'Strength & Power', 'duration': 0.5,
     'difficulty': 'beginner', 'target_muscles': 'Chest, triceps'},
    {'name': 'Squats', 'category': 'Strength & Power', 'duration': 0.5,
     'difficulty': 'beginner', 'target_muscles': 'Quadriceps, glutes'},
    {'name': 'Plank', 'category': 'Strength & Power', 'duration': 0.5,
     'difficulty': 'beginner', 'target_muscles': 'Core'},
]


def test_plan_sets_prescribes_sets_and_fits_the_budget():
    blocks = app_module.plan_sets(PLAN_POOL, 10, 'beginner', {})
    assert blocks and all(b['sets'] >= 2 for b in blocks)
    for b in blocks:
        assert b['set_seconds'] == 30
        assert b['rest_seconds'] == 45  # Strength & Power, beginner
        assert b['duration'] == round(
            (b['set_seconds'] * b['sets'] + b['rest_seconds'] * (b['sets'] - 1)) / 60.0, 2)
    # The promised length includes the rest the session timer will run
    assert app_module.plan_total_minutes(blocks) <= 10


def test_plan_sets_never_promises_more_than_the_time_asked_for():
    for minutes in (1, 3, 7, 12, 30):
        blocks = app_module.plan_sets(PLAN_POOL, minutes, 'advanced', {})
        assert app_module.plan_total_minutes(blocks) <= minutes


def test_plan_sets_runs_long_library_entries_once():
    """A four-minute cardio block is a block, not something to do three times."""
    long_one = [{'name': 'Cardio Intervals', 'category': 'Endurance',
                 'duration': 4.0, 'difficulty': 'intermediate', 'target_muscles': 'legs'}]
    blocks = app_module.plan_sets(long_one, 20, 'intermediate', {})
    assert blocks[0]['sets'] == 1
    assert blocks[0]['set_seconds'] == 240


def test_plan_sets_trades_sets_for_exercises_when_time_is_short():
    """Ten advanced minutes is not four sets of one movement."""
    pool = [dict(ex, difficulty='advanced', duration=1.0) for ex in PLAN_POOL]
    blocks = app_module.plan_sets(pool, 10, 'advanced', {})
    assert len(blocks) >= 2


def test_rest_seconds_follow_the_user_settings():
    f = app_module.rest_seconds_for
    assert f('Strength & Power', 'beginner', {}) == 45
    assert f('Strength & Power', 'advanced', {}) == 75
    assert f('Strength & Power', 'beginner', {'timers.rest_source': 'none'}) == 0
    assert f('Strength & Power', 'beginner',
             {'timers.rest_source': 'fixed', 'timers.fixed_rest_seconds': 90}) == 90
    assert f('Strength & Power', 'beginner', {'timers.rest_multiplier': 50}) == 23
    assert f('No Such Category', 'beginner', {}) == app_module.DEFAULT_REST_SECONDS


def test_selection_spreads_across_categories_and_muscles():
    """Two domains asked for, two domains delivered, not eight chest exercises."""
    pool = [
        {'name': 'A', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Chest'},
        {'name': 'B', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Chest'},
        {'name': 'C', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Chest'},
        {'name': 'D', 'category': 'Endurance', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Legs'},
    ]
    chosen = app_module.select_balanced(pool, 'beginner', (), 2)
    assert {ex['category'] for ex in chosen} == {'Strength & Power', 'Endurance'}


def test_selection_prefers_the_requested_difficulty_then_neighbours():
    pool = [
        {'name': 'Hard', 'category': 'Agility', 'duration': 0.5, 'difficulty': 'advanced'},
        {'name': 'Easy', 'category': 'Agility', 'duration': 0.5, 'difficulty': 'beginner'},
        {'name': 'Middle', 'category': 'Agility', 'duration': 0.5, 'difficulty': 'intermediate'},
    ]
    assert app_module.select_balanced(pool, 'advanced', (), 1)[0]['name'] == 'Hard'
    assert app_module.select_balanced(pool, 'beginner', (), 1)[0]['name'] == 'Easy'


def test_selection_is_deterministic_per_seed_but_rotates_between_seeds():
    """Same request today: same workout. Tomorrow: a different tie broken."""
    pool = [{'name': f'Ex {i}', 'category': 'Agility', 'duration': 0.5,
             'difficulty': 'beginner', 'target_muscles': 'Legs'} for i in range(12)]
    first = [ex['name'] for ex in app_module.select_balanced(pool, 'beginner', (), 3, 'day-1')]
    assert first == [ex['name'] for ex in
                     app_module.select_balanced(pool, 'beginner', (), 3, 'day-1')]
    assert first != [ex['name'] for ex in
                     app_module.select_balanced(pool, 'beginner', (), 3, 'day-2')]


def test_recent_exercises_are_pushed_down_not_dropped():
    chosen = app_module.select_balanced(PLAN_POOL, 'beginner', ('push-ups',), 3)
    assert chosen[-1]['name'] == 'Push-ups'     # last, but still available
    assert len(chosen) == 3


def test_validate_llm_selection_rejects_repeats_and_wrong_counts():
    f = app_module.validate_llm_selection
    repeated = {'exercises': [{'name': 'Push-ups', 'duration': 1},
                              {'name': 'push-ups', 'duration': 1}]}
    ex, err = f(repeated, VALIDATION_CANDIDATES, 5)
    assert ex is None and 'twice' in err

    picks = {'exercises': [{'name': 'Push-ups', 'duration': 1}]}
    # One either side of the engine's count is accepted; further off is not
    assert f(picks, VALIDATION_CANDIDATES, 5, expected_count=2)[1] is None
    ex, err = f(picks, VALIDATION_CANDIDATES, 5, expected_count=6)
    assert ex is None and 'exactly 6' in err


def test_generated_workout_reports_the_time_the_session_will_take(client, monkeypatch):
    """total_duration is wall clock: every set, and every rest between them."""
    monkeypatch.setattr(app_module, 'generate_workout_via_llm', lambda *a, **k: None)
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'
    }).get_json()

    exercises = data['exercises']
    assert all(e['sets'] >= 1 and e['set_seconds'] > 0 for e in exercises)
    by_hand = sum(e['set_seconds'] * e['sets'] + e['rest_seconds'] * (e['sets'] - 1)
                  for e in exercises)
    by_hand += sum(e['rest_seconds'] for e in exercises[1:])
    assert round(by_hand / 60.0, 1) == data['total_duration'] <= 15


def test_ai_workout_generation_is_off_by_default(client, monkeypatch):
    """The model is opt-in: an installed model is not reason enough to wait for it."""
    called = []
    monkeypatch.setattr(app_module, 'get_loaded_model', lambda: 'test-model')
    monkeypatch.setattr(app_module, 'generate_workout_via_llm',
                        lambda *a, **k: called.append(1))

    data = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 15, 'difficulty': 'beginner'
    }).get_json()

    assert called == []
    assert data['success'] is True and data['engine'] == 'rules'


# ── Reading an exercise: regions, patterns, focus ────────────────────


def test_muscle_regions_group_labels_that_mean_the_same_thing():
    f = app_module.muscle_regions
    assert f({'target_muscles': 'Chest, shoulders, triceps'}) == {'push'}
    assert f({'target_muscles': 'Chest, triceps'}) == {'push'}
    assert f({'target_muscles': 'Quadriceps, glutes'}) == {'legs'}
    # 'Full body' survives beside a non-anatomical label and yields to a real one
    assert f({'target_muscles': 'Full body, cardio'}) == {'full body', 'cardio'}
    assert f({'target_muscles': 'Full body, chest'}) == {'push'}
    assert f({'target_muscles': ''}) == set()


def test_movement_pattern_reads_the_name_before_the_muscles():
    f = app_module.movement_pattern
    assert f({'name': 'Diamond Push-ups', 'target_muscles': 'Chest, triceps'}) == 'push'
    assert f({'name': 'Doorway Rows', 'target_muscles': 'Back, biceps'}) == 'pull'
    assert f({'name': 'Chair Squats', 'target_muscles': 'Quadriceps'}) == 'squat'
    assert f({'name': 'Side Plank', 'target_muscles': 'Core, shoulders'}) == 'core'
    assert f({'name': 'Mental Math', 'target_muscles': 'Brain'}) == 'cognitive'
    # No name match and no muscles: the category is the last resort
    assert f({'name': 'Mystery Move', 'category': 'Agility'}) == 'agility'


def test_focus_profile_accepts_the_client_vocabulary_and_free_text():
    f = app_module.focus_profile
    assert f('upper_body') is app_module.FOCUS_PROFILES['upper_body']
    assert f('Upper Body') is app_module.FOCUS_PROFILES['upper_body']
    assert f('legs') is app_module.FOCUS_PROFILES['lower_body']
    assert f('') is None and f(None) is None
    # Anything unrecognised still steers, as bare keywords with no region
    regions, keywords = f('shoulder rehab')
    assert regions == set() and 'shoulder' in keywords


def test_selection_charges_more_for_the_second_repeat_than_the_first():
    """The old set-based score made every extra chest exercise equally cheap."""
    press = {'name': 'Push-ups', 'category': 'Strength & Power',
             'difficulty': 'beginner', 'target_muscles': 'Chest, triceps'}
    balance = app_module.new_balance()
    scores = []
    for _ in range(3):
        scores.append(app_module.selection_penalty(press, 'beginner', (), balance))
        app_module.add_to_balance(balance, press)
    assert scores[0] < scores[1] < scores[2]


def test_selection_avoids_stacking_one_movement_pattern():
    """Three presses available, three slots — one of them should not be a press."""
    pool = [
        {'name': 'Push-ups', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Chest, triceps'},
        {'name': 'Diamond Push-ups', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Chest, shoulders'},
        {'name': 'Incline Push-ups', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Chest, triceps'},
        {'name': 'Doorway Rows', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Back, biceps'},
    ]
    chosen = app_module.select_balanced(pool, 'beginner', (), 3)
    assert 'Doorway Rows' in [ex['name'] for ex in chosen]


def test_focus_pulls_the_selection_towards_what_was_asked_for():
    pool = [
        {'name': 'Push-ups', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Chest, triceps'},
        {'name': 'Doorway Rows', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Back, biceps'},
        {'name': 'Chair Squats', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Quadriceps, glutes'},
        {'name': 'Glute Bridge', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Glutes, hamstrings'},
    ]
    upper = [ex['name'] for ex in
             app_module.select_balanced(pool, 'beginner', (), 2, focus='upper_body')]
    lower = [ex['name'] for ex in
             app_module.select_balanced(pool, 'beginner', (), 2, focus='lower_body')]
    assert set(upper) == {'Push-ups', 'Doorway Rows'}
    assert set(lower) == {'Chair Squats', 'Glute Bridge'}
    # No focus given: the balanced spread is unchanged
    neutral = app_module.select_balanced(pool, 'beginner', (), 2)
    assert len(neutral) == 2


def test_focus_never_outranks_the_requested_difficulty():
    pool = [
        {'name': 'Easy Row', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Back'},
        {'name': 'Hard Squat', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'advanced', 'target_muscles': 'Quadriceps'},
    ]
    picked = app_module.select_balanced(pool, 'advanced', (), 1, focus='upper_body')
    assert picked[0]['name'] == 'Hard Squat'


def test_recent_region_load_reads_what_was_trained_not_just_what_was_named():
    library = [
        {'name': 'Chair Squats', 'target_muscles': 'Quadriceps, glutes'},
        {'name': 'Push-ups', 'target_muscles': 'Chest, triceps'},
    ]
    load = app_module.recent_region_load(['Chair Squats', 'Chair Squats'], library)
    assert load == {'legs': 1.0}
    assert app_module.recent_region_load([], library) == {}
    # Names the library has never heard of are ignored, not guessed at
    assert app_module.recent_region_load(['Kettlebell Snatch'], library) == {}


def test_history_pushes_the_next_session_off_a_tired_region():
    pool = [
        {'name': 'Chair Squats', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Quadriceps, glutes'},
        {'name': 'Doorway Rows', 'category': 'Strength & Power', 'duration': 0.5,
         'difficulty': 'beginner', 'target_muscles': 'Back, biceps'},
    ]
    fresh = app_module.select_balanced(pool, 'beginner', (), 1)
    tired = app_module.select_balanced(pool, 'beginner', (), 1,
                                       history={'legs': 1.0})
    assert fresh[0]['name'] == 'Chair Squats'
    assert tired[0]['name'] == 'Doorway Rows'


# ── Sequencing ───────────────────────────────────────────────────────

SEQ_ITEMS = [
    {'name': 'Push-ups', 'category': 'Strength & Power', 'target_muscles': 'Chest'},
    {'name': 'Diamond Push-ups', 'category': 'Strength & Power',
     'target_muscles': 'Chest'},
    {'name': 'Incline Push-ups', 'category': 'Strength & Power',
     'target_muscles': 'Chest'},
    {'name': 'Chair Squats', 'category': 'Strength & Power',
     'target_muscles': 'Quadriceps'},
    {'name': 'Glute Bridge', 'category': 'Strength & Power',
     'target_muscles': 'Glutes'},
    {'name': 'Doorway Rows', 'category': 'Strength & Power', 'target_muscles': 'Back'},
]


def test_sequencing_splits_up_the_repeated_movement():
    """Three presses in six blocks should not end up next to each other."""
    ordered = app_module.sequence_for_recovery(SEQ_ITEMS)
    patterns = [app_module.movement_pattern(ex) for ex in ordered]
    assert not any(a == b for a, b in zip(patterns, patterns[1:]))
    assert sorted(ex['name'] for ex in ordered) == \
        sorted(ex['name'] for ex in SEQ_ITEMS)


def test_sequencing_is_deterministic_and_never_worse_than_greedy():
    first = [ex['name'] for ex in app_module.sequence_for_recovery(SEQ_ITEMS)]
    assert first == [ex['name'] for ex in app_module.sequence_for_recovery(SEQ_ITEMS)]
    assert app_module.sequence_cost(app_module.sequence_for_recovery(SEQ_ITEMS)) <= \
        app_module.sequence_cost(SEQ_ITEMS)
    # Too short to reorder: returned untouched
    assert app_module.sequence_for_recovery(SEQ_ITEMS[:2]) == SEQ_ITEMS[:2]
    assert app_module.sequence_for_recovery([]) == []


def test_alternate_ordering_now_works_inside_a_single_category():
    """The old round-robin returned a one-category workout completely unchanged."""
    ordered = app_module.order_exercises(SEQ_ITEMS, 'alternate')
    assert [ex['name'] for ex in ordered] != [ex['name'] for ex in SEQ_ITEMS]
    assert sorted(ex['name'] for ex in ordered) == \
        sorted(ex['name'] for ex in SEQ_ITEMS)


# ── Filling the time asked for ───────────────────────────────────────


def test_rule_based_spills_over_rather_than_returning_a_thin_workout():
    """One advanced exercise in the library is not a sixty-minute session."""
    pool = [{'name': 'Single-leg Squat', 'category': 'Strength & Power',
             'duration': 1.0, 'difficulty': 'advanced',
             'target_muscles': 'Quadriceps, glutes'}]
    pool += [{'name': f'Move {i}', 'category': 'Strength & Power', 'duration': 0.7,
              'difficulty': 'intermediate', 'target_muscles': m}
             for i, m in enumerate(('Chest', 'Back', 'Core', 'Glutes', 'Calves'))]

    blocks = app_module.select_exercises_rule_based(pool, 60, 'advanced', {})
    assert len(blocks) > 1
    assert app_module.plan_total_minutes(blocks) > 30
    # The requested difficulty still leads
    assert blocks[0]['name'] == 'Single-leg Squat'


def test_rule_based_keeps_the_thin_plan_when_spillover_is_off():
    pool = [{'name': 'Single-leg Squat', 'category': 'Strength & Power',
             'duration': 1.0, 'difficulty': 'advanced',
             'target_muscles': 'Quadriceps'}]
    pool += [{'name': f'Move {i}', 'category': 'Strength & Power', 'duration': 0.7,
              'difficulty': 'intermediate', 'target_muscles': 'Chest'}
             for i in range(5)]
    blocks = app_module.select_exercises_rule_based(
        pool, 60, 'advanced', {'workout.difficulty_spillover': False})
    assert [b['name'] for b in blocks] == ['Single-leg Squat']


def test_generated_workout_says_when_it_could_not_fill_the_time(client, monkeypatch):
    """A short session is reported, not quietly handed over."""
    monkeypatch.setattr(app_module, 'generate_workout_via_llm', lambda *a, **k: None)
    data = client.post('/api/generate-workout', json={
        'domains': ['Strength'], 'duration': 200, 'difficulty': 'beginner'
    }).get_json()
    assert data['total_duration'] < 200
    assert any('short of the 200 requested' in note for note in data['applied_settings'])


def test_generated_workout_honours_focus_without_the_model(client, monkeypatch):
    """Focus used to reach the model only — with AI off it was silently dropped."""
    monkeypatch.setattr(app_module, 'generate_workout_via_llm', lambda *a, **k: None)

    def names(focus):
        data = client.post('/api/generate-workout', json={
            'domains': ['Strength'], 'duration': 20, 'difficulty': 'beginner',
            'focus': focus}).get_json()
        assert data['engine'] == 'rules'
        return [app_module.muscle_regions(e) for e in data['exercises']]

    upper = sum(1 for r in names('upper_body') if 'push' in r or 'pull' in r)
    lower = sum(1 for r in names('lower_body') if 'legs' in r)
    assert upper >= 3 and lower >= 3


def test_refit_plan_gives_back_time_ordering_borrowed():
    blocks = app_module.plan_sets(PLAN_POOL, 10, 'beginner', {})
    # What reordering does: a block with a longer rest lands after the first
    # one, so the join between exercises now costs more than it was budgeted at
    blocks[1] = dict(blocks[1], rest_seconds=120)
    stretched = app_module.plan_total_minutes(blocks)
    trimmed = app_module.refit_plan(blocks, 10)
    assert stretched > 10 >= app_module.plan_total_minutes(trimmed)
    # Depth comes off before an exercise does
    assert len(trimmed) == len(blocks)
    assert sum(b['sets'] for b in trimmed) < sum(b['sets'] for b in blocks)


def test_refit_plan_leaves_a_fitting_plan_and_an_untimed_one_alone():
    blocks = app_module.plan_sets(PLAN_POOL, 10, 'beginner', {})
    assert app_module.refit_plan(blocks, 10) == blocks
    assert app_module.refit_plan(blocks, 0) == blocks
    assert app_module.refit_plan([], 10) == []
    # An older saved workout carries no set figures — nothing to trim by
    legacy = [{'name': 'Push-ups', 'duration': 30.0}]
    assert app_module.refit_plan(legacy, 5) == legacy


def test_generated_workout_fits_the_time_with_bookends_on(client, monkeypatch):
    """Warm-up, main work and cool-down together, joins included."""
    monkeypatch.setattr(app_module, 'generate_workout_via_llm', lambda *a, **k: None)
    client.patch('/api/settings', json={'changes': {
        'workout.include_warmup': True, 'workout.include_cooldown': True}},
        headers={'X-CSRF-Token': CSRF})
    for minutes in (12, 20, 35, 45):
        data = client.post('/api/generate-workout', json={
            'domains': ['Strength', 'Endurance'], 'duration': minutes,
            'difficulty': 'intermediate'}).get_json()
        assert data['total_duration'] <= minutes, (minutes, data['total_duration'])
