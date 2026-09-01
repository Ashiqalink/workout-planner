from flask import (Flask, render_template, request, jsonify, session, redirect,
                   url_for, flash, abort, Response, g)
import pandas as pd
import sqlite3
import csv
import io
import json
import hashlib
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
import secrets
from werkzeug.security import generate_password_hash, check_password_hash

import settings_registry as reg

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

# ── Logging ──────────────────────────────────────────────────────────
# Console + rotating file log. LOG_LEVEL env var overrides (DEBUG/INFO/...).
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

_log_level = os.environ.get(
    'LOG_LEVEL',
    'DEBUG' if os.environ.get('FLASK_DEBUG', '0') == '1' else 'INFO'
).upper()

_formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
_file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, 'app.log'), maxBytes=1_000_000, backupCount=3, encoding='utf-8'
)
_file_handler.setFormatter(_formatter)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)

app.logger.setLevel(_log_level)
app.logger.addHandler(_file_handler)
if not any(isinstance(h, logging.StreamHandler) for h in app.logger.handlers):
    app.logger.addHandler(_console_handler)
logger = app.logger

if os.environ.get('SECRET_KEY'):
    app.secret_key = os.environ['SECRET_KEY']
else:
    app.secret_key = secrets.token_hex(16)
    logger.warning("Using random secret key — sessions will not survive restarts. Set SECRET_KEY env var.")

# CSRF helpers
def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

def validate_csrf_token():
    token = session.get('_csrf_token')
    form_token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
    if not token or not form_token or not secrets.compare_digest(token, form_token):
        logger.warning("CSRF validation failed for %s %s", request.method, request.path)
        abort(403)

app.jinja_env.globals['csrf_token'] = generate_csrf_token

@app.context_processor
def inject_globals():
    """Values every template can rely on.

    `settings` is injected globally rather than passed per-route because the
    base layout itself reads it — the theme, navigation style, font size and
    custom CSS are all applied before any page-specific block renders.
    """
    values = get_settings()
    return {
        'current_year': datetime.now().year,
        'settings': values,
        'settings_json': json.dumps(values),
        'ui_tier': reg.TIER_RANK.get(values.get('ui.mode', 'simple'), 0),
    }

# Configuration — absolute paths so the app works no matter what the CWD is.
# DATABASE_PATH env var lets tests (and deploys) point at another database.
DATABASE = os.environ.get('DATABASE_PATH', os.path.join(BASE_DIR, 'training_app.db'))
DATA_DIR = os.path.join(BASE_DIR, 'data')

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

def get_db_connection():
    """Create database connection with row factory for easy access."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize the database with required tables."""
    conn = get_db_connection()

    # Create users table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create user sessions table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            session_date DATE DEFAULT CURRENT_DATE,
            exercises_completed TEXT,
            exercises_count INTEGER DEFAULT 0,
            total_duration INTEGER,
            session_type TEXT,
            completion_status TEXT DEFAULT 'completed',
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # Create user progress table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            domain TEXT,
            sessions_completed INTEGER DEFAULT 0,
            total_minutes INTEGER DEFAULT 0,
            current_week INTEGER DEFAULT 1,
            streak_days INTEGER DEFAULT 0,
            last_session_date DATE,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # Create exercises table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            duration_minutes INTEGER,
            primary_benefit TEXT,
            secondary_benefit TEXT,
            difficulty_level TEXT,
            instructions TEXT,
            UNIQUE(category, exercise_name)
        )
    ''')
    # Create saved workouts table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS saved_workouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            workout_name TEXT NOT NULL,
            workout_description TEXT,
            exercises_json TEXT NOT NULL,
            total_duration REAL NOT NULL,
            difficulty TEXT NOT NULL,
            created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Per-user settings. One JSON blob rather than a row per key: settings are
    # always read as a whole and the registry — not the schema — decides which
    # keys are meaningful, so retiring a setting needs no migration.
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id TEXT PRIMARY KEY,
            settings_json TEXT NOT NULL,
            updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Set-by-set logging, written only when session.tracking_mode asks for more
    # than a timer. Weight is stored in the unit it was entered in so a later
    # unit switch never silently rewrites history.
    conn.execute('''
        CREATE TABLE IF NOT EXISTS exercise_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            log_date DATE NOT NULL,
            workout_id TEXT,
            exercise_name TEXT NOT NULL,
            category TEXT,
            set_number INTEGER DEFAULT 1,
            reps INTEGER,
            weight REAL,
            weight_unit TEXT DEFAULT 'kg',
            created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS favorite_exercises (
            user_id TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            category TEXT NOT NULL,
            PRIMARY KEY (user_id, exercise_name, category)
        )
    ''')

    # Migrations: columns added after the first release. Each is guarded by a
    # PRAGMA check so an existing database upgrades in place on next startup.
    _add_missing_columns(conn, 'user_sessions', {
        'exercises_count': 'INTEGER DEFAULT 0',
        'rpe': 'INTEGER',
        'notes': 'TEXT',
    })
    _add_missing_columns(conn, 'exercises', {
        'equipment': 'TEXT',
    })

    # Indexes for the per-user lookups every page performs
    conn.execute('CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id, session_date)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_user_progress_user ON user_progress(user_id, domain)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_saved_workouts_user ON saved_workouts(user_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_exercise_logs_user ON exercise_logs(user_id, log_date)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_exercise_logs_name ON exercise_logs(user_id, exercise_name)')

    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DATABASE)

def _add_missing_columns(conn, table, columns):
    """ALTER TABLE ADD COLUMN for any of `columns` the table doesn't have yet."""
    existing = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
            logger.info("Migrated %s: added column %s", table, name)

def load_exercise_data():
    """Load exercise data from CSV files."""
    try:
        exercises_df = pd.read_csv(os.path.join(DATA_DIR, 'comprehensive_training_matrix.csv'))
        logger.debug("Loaded %d exercises from comprehensive_training_matrix.csv", len(exercises_df))

        program_df = pd.read_csv(os.path.join(DATA_DIR, 'complete_4week_program.csv'))
        logger.debug("Loaded %d program entries from complete_4week_program.csv", len(program_df))

        student_program_df = pd.read_csv(os.path.join(DATA_DIR, 'student_training_program.csv'))
        logger.debug("Loaded %d student program entries from student_training_program.csv", len(student_program_df))

        return {
            'exercises': exercises_df.to_dict('records'),
            'program': program_df.to_dict('records'),
            'student_program': student_program_df.to_dict('records')
        }
    except FileNotFoundError as e:
        logger.error("CSV file not found: %s", e)
        return {
            'exercises': [],
            'program': [],
            'student_program': []
        }
    except Exception:
        logger.exception("Error loading CSV files")
        return {
            'exercises': [],
            'program': [],
            'student_program': []
        }

def populate_exercises_db():
    """Sync the exercises table with the CSV data.

    INSERT OR IGNORE (against the UNIQUE(category, exercise_name) constraint)
    means new CSV exercises appear in existing databases on the next startup
    while user-added custom exercises are never touched.
    """
    conn = get_db_connection()
    data = load_exercise_data()

    for exercise in data['exercises']:
        conn.execute('''
            INSERT OR IGNORE INTO exercises (category, exercise_name, duration_minutes,
                                 primary_benefit, secondary_benefit, difficulty_level, instructions, equipment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            exercise['category'],
            exercise['name'],
            exercise['duration'],
            exercise['description'],
            exercise['target_muscles'],
            exercise['difficulty'],
            exercise.get('instructions', ''),
            _clean_cell(exercise.get('equipment')) or 'None'
        ))
        # Backfill for databases seeded before the equipment column existed.
        # Restricted to NULL so a user's own edits are never overwritten.
        conn.execute('''
            UPDATE exercises SET equipment = ?
            WHERE category = ? AND exercise_name = ? AND equipment IS NULL
        ''', (_clean_cell(exercise.get('equipment')) or 'None',
              exercise['category'], exercise['name']))

    conn.commit()
    conn.close()
    invalidate_exercise_cache()
    logger.info("Synced exercises table with %d exercises from CSV", len(data['exercises']))

def _clean_cell(value):
    """A CSV cell as a plain string. Pandas reads blanks as NaN, which is truthy —
    so `value or default` silently keeps the NaN unless it goes through here."""
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value).strip()

# The exercise library changes only when someone adds a custom exercise, but it
# is read on nearly every request — the planner, the session page, prescription
# resolution and every generation attempt. Caching it turns those into a dict
# lookup instead of a query plus row conversion.
_exercise_cache = None

def invalidate_exercise_cache():
    global _exercise_cache
    _exercise_cache = None

def get_all_exercises_from_db():
    """Retrieve exercises from the SQLite database (cached until invalidated)."""
    global _exercise_cache
    if _exercise_cache is not None:
        return _exercise_cache

    conn = get_db_connection()
    try:
        rows = conn.execute('''
            SELECT category, exercise_name, duration_minutes, primary_benefit,
                   secondary_benefit, difficulty_level, instructions, equipment
            FROM exercises
        ''').fetchall()
        exercises = []
        for r in rows:
            exercises.append({
                'category': r['category'],
                'name': r['exercise_name'],
                'duration': float(r['duration_minutes'] or 0.5),
                'description': r['primary_benefit'] or '',
                'target_muscles': r['secondary_benefit'] or '',
                'difficulty': r['difficulty_level'] or 'beginner',
                'instructions': r['instructions'] or '',
                'equipment': r['equipment'] or 'None',
            })
        _exercise_cache = exercises
        return exercises
    finally:
        conn.close()

# Load data at startup
training_data = load_exercise_data()

def load_rest_times():
    """Load rest times from CSV file."""
    try:
        rest_df = pd.read_csv(os.path.join(DATA_DIR, 'Category-Beginner-Intermediate-Advanced.csv'))
        logger.debug("Loaded %d rest time categories", len(rest_df))

        # Convert to dictionary for easy lookup
        rest_times = {}
        for _, row in rest_df.iterrows():
            category = row['Category']
            rest_times[category] = {
                'beginner': int(row['Beginner']),
                'intermediate': int(row['Intermediate']),
                'advanced': int(row['Advanced'])
            }
        
        return rest_times
    except FileNotFoundError as e:
        logger.error("Rest times CSV file not found: %s", e)
        return {}
    except Exception:
        logger.exception("Error loading rest times CSV")
        return {}

# Load rest times at startup
rest_times = load_rest_times()

# ── User settings ────────────────────────────────────────────────────
#
# Two implementations, as with every other stateful feature here: guests keep
# settings in the signed session cookie, logged-in users in SQLite. Both go
# through load/store so callers never need to know which one they got.
#
# Only values that differ from the registry default are persisted. That keeps
# the guest cookie small, and means a later improvement to a default reaches
# everyone who never overrode it.

GUEST_SETTINGS_BUDGET = 3000  # bytes of JSON; the session cookie limit is 4KB

def settings_owner():
    """Identity settings are stored against — a user id, or the string 'guest'."""
    return session.get('user_id', 'guest')

def load_raw_settings(user_id):
    """The user's stored overrides, exactly as saved. Unknown keys included."""
    if user_id in (None, 'guest'):
        stored = session.get('settings')
        return dict(stored) if isinstance(stored, dict) else {}

    conn = get_db_connection()
    try:
        row = conn.execute(
            'SELECT settings_json FROM user_settings WHERE user_id = ?',
            (str(user_id),)).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    try:
        stored = json.loads(row['settings_json'])
        return stored if isinstance(stored, dict) else {}
    except ValueError:
        logger.warning("Corrupt settings JSON for user %s; falling back to defaults", user_id)
        return {}

def store_raw_settings(user_id, overrides):
    """Persist overrides. Returns (ok, error) — guests can overflow the cookie."""
    if user_id in (None, 'guest'):
        encoded = json.dumps(overrides)
        if len(encoded) > GUEST_SETTINGS_BUDGET:
            return False, ('That is more customisation than a guest session can hold. '
                           'Create an account and your settings move to the database '
                           'with no size limit.')
        session['settings'] = overrides
        session.modified = True
        return True, None

    conn = get_db_connection()
    try:
        conn.execute('''
            INSERT INTO user_settings (user_id, settings_json, updated_date)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                settings_json = excluded.settings_json,
                updated_date  = CURRENT_TIMESTAMP
        ''', (str(user_id), json.dumps(overrides)))
        conn.commit()
    finally:
        conn.close()
    return True, None

def get_settings():
    """Effective settings for this request: stored overrides over defaults.

    Cached on Flask's request-scoped `g`, because a single page render asks for
    it from the context processor, the route and often an API helper, and all
    three must agree.
    """
    cached = getattr(g, '_fittrack_settings', None)
    if cached is not None:
        return cached
    values = reg.merge(load_raw_settings(settings_owner()))
    g._fittrack_settings = values
    return values

def write_settings(values):
    """Save a complete settings dict, keeping only the non-default values."""
    overrides = {}
    for key, value in values.items():
        setting = reg.SETTINGS_BY_KEY.get(key)
        if setting is None:
            continue
        if value != setting.default:
            overrides[key] = value
    ok, error = store_raw_settings(settings_owner(), overrides)
    if ok:
        g._fittrack_settings = reg.merge(overrides)
    return ok, error

def apply_setting_changes(changes):
    """Validate and persist a batch of {key, value} changes.

    Returns (accepted, rejected, error). `accepted` describes only the changes
    that actually moved a value, which is what every user-facing summary is
    built from — see reg.describe_changes.
    """
    current = get_settings()
    accepted, rejected = reg.apply_changes(current, changes)
    if not accepted:
        return [], rejected, None
    updated = dict(current)
    for change in accepted:
        updated[change['key']] = change['value']
    ok, error = write_settings(updated)
    if not ok:
        return [], rejected, error
    return accepted, rejected, None

PROGRAM_WEEKS = 4

# The detailed program names a few exercises differently from the library;
# resolve those here (all lookups are case-insensitive as well).
PRESCRIPTION_ALIASES = {
    'glute bridges': 'glute bridge',
    'superman holds': 'superman hold',
    'cone weaving': 'cone weaves',
    'ladder drills': 'ladder drills (imaginary)',
    'mindfulness': 'mindfulness practice',
    'working memory': 'working memory games',
    'joint mobility': 'joint mobility flow',
}

def get_program_start(user_id):
    """Date the user's program started: the day of their first recorded session."""
    if user_id == 'guest':
        start_str = session.get('program_start')
    else:
        conn = get_db_connection()
        try:
            row = conn.execute(
                'SELECT MIN(session_date) FROM user_sessions WHERE user_id = ?',
                (user_id,)).fetchone()
        finally:
            conn.close()
        start_str = row[0] if row else None
    if not start_str:
        return None
    try:
        return datetime.strptime(str(start_str)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None

def get_current_week(user_id):
    """Program week (1-4), advancing every 7 days from the user's first session.

    After week 4 the program cycles back to week 1, so the training habit
    keeps going past the first month instead of freezing on the last week.
    """
    start = get_program_start(user_id)
    if start is None:
        return 1
    weeks_elapsed = max(0, (datetime.now().date() - start).days) // 7
    return (weeks_elapsed % PROGRAM_WEEKS) + 1

def parse_prescription(text):
    """Parse an Exercises cell like "Push-ups (2min), Plank (3min)".

    Returns [(name, minutes), ...]. Freeform entries such as
    "All categories mini-circuit" yield [], which signals the caller to fall
    back to the AI workout generator for that day.
    """
    items = []
    for part in str(text or '').split(','):
        m = re.match(r'^\s*(.+?)\s*\((\d+)\s*min\)\s*$', part.strip())
        if m:
            items.append((m.group(1).strip(), int(m.group(2))))
    return items

def resolve_prescribed_exercises(prescription, settings=None):
    """Match (name, minutes) prescriptions against the exercise library.

    Prescribed minutes override the library's default duration. Unknown names
    are kept as bare entries so an edited program can't silently drop work.
    """
    library = {ex['name'].lower(): ex for ex in get_all_exercises_from_db()}
    resolved = []
    for name, minutes in prescription:
        key = name.lower()
        key = PRESCRIPTION_ALIASES.get(key, key)
        ex = library.get(key)
        if ex:
            ex = dict(ex)
            ex['duration'] = float(minutes)
        else:
            logger.warning("Prescribed exercise %r not found in library", name)
            ex = {
                'name': name, 'category': 'General', 'duration': float(minutes),
                'description': '', 'target_muscles': '', 'difficulty': 'beginner',
                'instructions': ''
            }
        # The program prescribes one continuous block, so it is one set of the
        # prescribed length. Stated explicitly because the session timer now
        # runs sets, and a missing count would silently mean one anyway.
        ex['sets'] = 1
        ex['set_seconds'] = int(round(float(minutes) * 60))
        ex['rest_seconds'] = rest_seconds_for(ex.get('category'), ex.get('difficulty'),
                                              settings or {})
        ex['work_seconds'] = ex['set_seconds']
        resolved.append(ex)
    return resolved

def intensity_to_difficulty(intensity):
    """Map program intensities (Light-Moderate, High, Maximum...) to app levels."""
    s = str(intensity or '').lower()
    if 'high' in s or 'max' in s:
        return 'advanced'
    if 'moderate' in s:
        return 'intermediate'
    return 'beginner'

def find_program_entry(entries, week, day):
    """Find the row for (week, day) in a program CSV's records."""
    for entry in entries or []:
        try:
            entry_week = int(entry.get('Week', 0))
        except (TypeError, ValueError):
            continue
        if entry_week == week and str(entry.get('Day', '')).strip().lower() == day.lower():
            return entry
    return None

def get_today_session(user_id):
    """Get the recommended training program session for today."""
    try:
        current_week = get_current_week(user_id)
        current_day = datetime.now().strftime('%A')

        entry = find_program_entry(training_data.get('program'), current_week, current_day)
        if entry:
            return {
                'name': entry.get('Type'),
                'description': entry.get('Description') or f"Focus: {entry.get('Focus')}",
                'duration': entry.get('Duration'),
                'difficulty': entry.get('Intensity'),
                'focus': entry.get('Focus') or '',
                'week': current_week
            }
    except Exception:
        logger.exception("Error getting today's session")
    return None

@app.route('/')
def root():
    """Send the user wherever ui.landing_page says they want to start.

    The dashboard keeps its own URL (/dashboard) so that changing this setting
    never makes the dashboard unreachable — the navigation links there
    directly, and only the bare "/" entry point is redirected.
    """
    target = get_settings().get('ui.landing_page', 'dashboard')
    endpoints = {
        'dashboard': 'index', 'planner': 'planner', 'library': 'library',
        'progress': 'progress', 'session': 'training_session',
    }
    if target != 'dashboard' and target in endpoints:
        return redirect(url_for(endpoints[target]))
    return index()

@app.route('/dashboard')
def index():
    """Main dashboard route."""
    # Seed guest session defaults. setdefault rather than assignment: a session
    # can already hold progress from /api/complete_session if the user reached
    # the planner or a saved workout before ever loading the dashboard.
    session.setdefault('user_id', 'guest')
    session.setdefault('streak', 0)
    session.setdefault('weekly_minutes', 0)
    session.setdefault('domain_progress', {
        'strength_power': 0,
        'speed_mobility': 0,
        'endurance': 0,
        'agility': 0,
        'cognition': 0
    })

    if isinstance(session.get('domain_progress'), dict) and 'strength' in session['domain_progress']:
        # Migrate old session keys if they exist
        session['domain_progress'] = {
            'strength_power': session['domain_progress'].get('strength_power', session['domain_progress'].get('strength', 0)),
            'speed_mobility': session['domain_progress'].get('speed_mobility', session['domain_progress'].get('speed', 0)),
            'endurance': session['domain_progress'].get('endurance', 0),
            'agility': session['domain_progress'].get('agility', 0),
            'cognition': session['domain_progress'].get('cognition', session['domain_progress'].get('cognitive', 0))
        }

    user_id = session.get('user_id')
    progress_data = get_user_progress_data(user_id)
    recent_sessions = progress_data.get('recent_sessions', [])
    today_session = get_today_session(user_id)

    # On a planned rest day the dashboard suggests recovery instead of a
    # session; goals.rest_days also keeps the streak alive through it.
    today_name = datetime.now().strftime('%A')
    rest_day_today = (today_name if today_name.lower()
                      in {str(d).lower() for d in get_settings().get('goals.rest_days', [])}
                      else None)

    return render_template('index.html',
                         progress_data=progress_data,
                         recent_sessions=recent_sessions,
                         today_session=today_session,
                         rest_day_today=rest_day_today)

@app.route('/library')
def library():
    """Exercise library page."""
    return render_template('library.html')

@app.route('/planner')
def planner():
    """Training planner page."""
    return render_template('planner.html')

@app.route('/progress')
def progress():
    """Progress tracking page."""
    user_id = session.get('user_id', 'guest')
    progress_data = get_user_progress_data(user_id)
    return render_template('progress.html', progress_data=progress_data, recent_sessions=progress_data.get('recent_sessions', []))

@app.route('/session')
def training_session():
    """Training session page."""
    return render_template('session.html')

# API Routes
@app.route('/api/exercises')
def api_exercises():
    """Get all exercises from the SQLite database."""
    try:
        exercises = get_all_exercises_from_db()
        return jsonify({'exercises': exercises})
    except Exception as e:
        logger.exception("Failed to load exercises from database")
        return jsonify({'exercises': [], 'error': str(e)}), 500

LM_STUDIO_API_URL = os.environ.get('LM_STUDIO_API_URL', 'http://localhost:1234/v1')

_cached_model = None

def get_loaded_model():
    """Query LM Studio to find a usable chat model name.

    LM Studio's native /api/v0/models is tried first because it reports each
    model's type and load state — the OpenAI-compatible /v1/models lists every
    *downloaded* model, and its first entry is frequently an embedding model
    that can never answer a chat request.

    Only a model that is actually loaded is cached. When nothing is loaded,
    the best chat-capable candidate is returned uncached so that a model the
    user loads later is picked up on the next request (and so LM Studio's
    just-in-time loading can pull the named one into memory meanwhile).
    Failures are never cached either, so LM Studio started after this app is
    found as soon as it is up.
    """
    global _cached_model
    if _cached_model is not None:
        return _cached_model

    base = LM_STUDIO_API_URL.rsplit('/v1', 1)[0]
    try:
        req = urllib.request.Request(f"{base}/api/v0/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8')).get('data', [])
        chat_models = [m for m in data if m.get('type') in ('llm', 'vlm')]
        loaded = [m for m in chat_models if m.get('state') == 'loaded']
        if loaded:
            _cached_model = loaded[0].get('id', 'lm-studio')
            logger.info("LM Studio loaded model detected: %s", _cached_model)
            return _cached_model
        if chat_models:
            # Nothing in memory yet, so naming a model makes LM Studio load it
            # just-in-time. Prefer the smallest (by the parameter count most
            # ids carry, e.g. "0.5b", "27b"): this app's prompts are designed
            # for small models, and a small one loads in seconds, not minutes.
            def _size(m):
                match = re.search(r'(\d+(?:\.\d+)?)\s*b\b', str(m.get('id', '')).lower())
                return float(match.group(1)) if match else 1e9
            candidate = min(chat_models, key=_size).get('id', 'lm-studio')
            logger.info("No model loaded in LM Studio; will request %s (JIT load)", candidate)
            return candidate
    except Exception as e:
        logger.debug("LM Studio native API lookup failed (%s); trying /v1/models", e)

    try:
        req = urllib.request.Request(f"{LM_STUDIO_API_URL}/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8')).get('data', [])
        # Best effort without type metadata: skip obvious embedding models.
        candidates = [m for m in data if 'embed' not in str(m.get('id', '')).lower()] or data
        if candidates:
            _cached_model = candidates[0].get('id', 'lm-studio')
            logger.info("Model server model detected: %s", _cached_model)
            return _cached_model
    except Exception as e:
        logger.debug("Model lookup failed (%s); using fallback name", e)
    return 'lm-studio'

def ai_config(settings=None):
    """Resolve this user's model endpoint from settings, with app defaults.

    Kept separate from `get_loaded_model` because that function is also called
    outside a request (and is cached against the app-wide endpoint), while
    these values are per-user and must not poison that cache.
    """
    settings = settings if settings is not None else get_settings()
    base_url = (settings.get('ai.server_url') or '').strip().rstrip('/') or LM_STUDIO_API_URL
    model = (settings.get('ai.model') or '').strip()
    if not model:
        model = get_loaded_model() if base_url == LM_STUDIO_API_URL else _probe_model(base_url)
    try:
        timeout = int(settings.get('ai.timeout_seconds', 10))
    except (TypeError, ValueError):
        timeout = 10
    return {
        'enabled': bool(settings.get('ai.enabled', True)),
        'base_url': base_url,
        'model': model,
        'timeout': max(3, min(timeout, 120)),
    }

def _probe_model(base_url):
    """Ask a non-default endpoint which model it has loaded. Never cached."""
    try:
        req = urllib.request.Request(f"{base_url}/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8')).get('data', [])
            if data:
                return data[0].get('id', 'local-model')
    except Exception as e:
        logger.debug("Model lookup at %s failed (%s)", base_url, e)
    return 'local-model'

# Selection-only schema the model must follow, sent as OpenAI-style structured
# output. LM Studio enforces it with grammar-constrained sampling, so even very
# small models cannot emit malformed JSON or extra fields. The model only picks
# names and minutes; all other exercise metadata is joined back server-side.
#
# There is deliberately no free-text field here. The model does not author any
# sentence the user reads — user-facing prose is built by
# build_workout_explanation() from the resolved workout. additionalProperties is
# false at both levels so the grammar itself cannot produce one.
# See learn/07-truth-boundary.md.
WORKOUT_SELECTION_SCHEMA = {
    "name": "workout_selection",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "exercises": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "duration": {"type": "number"}
                    },
                    "required": ["name", "duration"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["exercises"],
        "additionalProperties": False
    }
}

MIN_EXERCISE_MINUTES = 0.5
MAX_EXERCISE_MINUTES = 15.0
TOTAL_DURATION_TOLERANCE = 0.4  # accept totals within ±40% of the target
LLM_RETRIES = 1                 # one corrective retry after a rejected response

def build_workout_explanation(exercises, difficulty, focus=''):
    """Describe a workout using only values computed from the workout itself.

    Every number and name here comes from resolved database records, so the
    sentence cannot contain a claim nobody computed. The language model never
    writes user-facing prose in this app — it selects names and minutes, and
    this function does the talking. Both the AI and rule-based generation paths
    call it, so the two produce the same shape of explanation.

    Pure function: no request, session, or database access, so it is unit
    testable on its own. See learn/07-truth-boundary.md.
    """
    if not exercises:
        return 'No exercises matched your selection.'

    def minutes(ex):
        try:
            return float(ex.get('duration') or 0)
        except (TypeError, ValueError):
            return 0.0

    total = plan_total_minutes(exercises)

    counts = {}
    for ex in exercises:
        category = ex.get('category') or 'General'
        counts[category] = counts.get(category, 0) + 1
    # Most exercises first, ties broken alphabetically, so the sentence is
    # stable for the same workout rather than following dict insertion order.
    breakdown = ', '.join(
        f"{n} {category}"
        for category, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )

    longest = max(exercises, key=minutes)
    count = len(exercises)
    sentence = (
        f"A {total:g}-minute {difficulty} session across "
        f"{count} exercise{'' if count == 1 else 's'}: {breakdown}. "
        f"Longest block: {longest.get('name', 'unnamed')}, {minutes(longest):g} min."
    )
    # Sets and rest are engine numbers too, so the sentence may quote them.
    sets = sum(int(ex.get('sets') or 0) for ex in exercises)
    if sets:
        seconds = int(longest.get('set_seconds') or 0)
        sentence = sentence[:-1] + (
            f" — {int(longest.get('sets') or 1)} × {seconds}s." if seconds else '.')
        sentence += f" {sets} sets in total."
    if focus:
        sentence += f" Focus: {focus}."
    return sentence

def validate_llm_selection(selection, candidates, duration,
                           min_minutes=None, max_minutes=None, tolerance=None,
                           expected_count=None):
    """Join the LLM's (name, duration) picks back against the candidate list.

    Returns (exercises, error). On success, exercises are full candidate dicts
    with durations clamped to sane bounds; on failure, error describes the
    problem so it can be fed back to the model for a corrective retry.

    The three bounds default to the module constants and are overridden per
    user from the workout.* settings, so someone who wants 30-minute blocks or
    a tighter length tolerance changes the validator, not just the prompt.

    `expected_count` is the engine's own answer for how many exercises fit the
    time. When it is given the model is being asked to *choose* rather than to
    do arithmetic, so the count is checked instead of the duration total —
    the planner assigns sets and rest afterwards either way. Repeats are
    rejected in both modes: naming one exercise six times is the commonest
    failure of a small model handed a long list.
    """
    min_minutes = MIN_EXERCISE_MINUTES if min_minutes is None else min_minutes
    max_minutes = MAX_EXERCISE_MINUTES if max_minutes is None else max_minutes
    tolerance = TOTAL_DURATION_TOLERANCE if tolerance is None else tolerance
    by_name = {c['name'].lower(): c for c in candidates}
    picks = selection.get('exercises') if isinstance(selection, dict) else None
    if not isinstance(picks, list) or not picks:
        return None, 'No exercises were selected.'

    resolved, seen = [], set()
    for item in picks:
        if not isinstance(item, dict):
            return None, 'Each exercise must be an object with "name" and "duration".'
        name = str(item.get('name', '')).strip()
        key = PRESCRIPTION_ALIASES.get(name.lower(), name.lower())
        ex = by_name.get(key)
        if not ex:
            return None, f'"{name}" is not in the candidate list.'
        if key in seen:
            return None, f'"{ex["name"]}" appears twice; every exercise must be different.'
        seen.add(key)
        try:
            minutes = float(item.get('duration', 0))
        except (TypeError, ValueError):
            minutes = 0.0
        if minutes <= 0:
            minutes = float(ex.get('duration') or min_minutes)
        minutes = min(max(minutes, min_minutes), max_minutes)
        ex = dict(ex)
        ex['duration'] = minutes
        resolved.append(ex)

    if expected_count is not None:
        # One either side is accepted: the planner re-times whatever arrives,
        # and a retry costs a small model more than the difference is worth.
        if abs(len(resolved) - expected_count) > 1:
            return None, (f'You chose {len(resolved)} exercises; choose exactly '
                          f'{expected_count}.')
        return resolved, None

    total = sum(e['duration'] for e in resolved)
    if abs(total - duration) > duration * tolerance:
        return None, (f'Selected exercises total {total:.1f} minutes, but the '
                      f'target is {duration} minutes.')
    return resolved, None

def _post_chat_completion(messages, schema=None, config=None):
    """POST to the local model server; returns the message content, or None.

    `schema` is the structured-output grammar the response must satisfy. Every
    call site passes one — an unconstrained completion has no place in this app,
    because validation downstream assumes the shape is already enforced.
    """
    schema = schema or WORKOUT_SELECTION_SCHEMA
    base_url = (config or {}).get('base_url') or LM_STUDIO_API_URL
    model = (config or {}).get('model') or get_loaded_model()
    timeout = (config or {}).get('timeout') or 10

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "enable_thinking": False,
        "response_format": {"type": "json_schema", "json_schema": schema}
    }
    try:
        req = urllib.request.Request(f"{base_url}/chat/completions", method="POST")
        req.add_header("Content-Type", "application/json")
        data_bytes = json.dumps(payload).encode('utf-8')
        with urllib.request.urlopen(req, data_bytes, timeout=timeout) as response:
            res_json = json.loads(response.read().decode('utf-8'))
        choices = res_json.get("choices", [])
        if not choices:
            return None
        return (choices[0].get("message", {}).get("content") or "").strip()
    except Exception as e:
        logger.warning("Local model call failed, falling back to the rule-based engine: %s", e)
        return None

def _strip_code_fence(content):
    """Remove markdown fences a server may add when it ignores response_format."""
    if content.startswith("```"):
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'\s*```$', '', content).strip()
    return content

MODEL_SHORTLIST = 24        # candidates shown to the model, at most


def shortlist_candidates(pool, difficulty, avoid=(), limit=MODEL_SHORTLIST, seed='',
                         focus=None, history=None):
    """The slice of the pool the model is allowed to choose from.

    A 163-exercise list is not a prompt a 0.5–4B model reads carefully; it
    copies from the top of it. So the engine pre-selects a balanced, on-
    difficulty, non-repeating shortlist with the same scoring the rule-based
    path uses, and the model reorders and picks within it. The floor on quality
    is therefore the engine's own shortlist, and the prompt stays short — which
    at this model size is a correctness feature, not a cost optimisation.
    """
    return select_balanced(pool, difficulty, avoid, limit, seed,
                          focus=focus, history=history)


def generate_workout_via_llm(domains, duration, difficulty, focus, candidates,
                             goal=None, settings=None, target_count=None,
                             avoid=()):
    """Ask the local LLM to choose exercises from the candidate shortlist.

    The model's job is deliberately small, and smaller than it used to be: it
    chooses `target_count` names, and the engine assigns sets, rest and total
    time afterwards. Asking a small model to make per-exercise minutes add up
    to a target was arithmetic it routinely failed, and the retry it triggered
    cost more than the selection was worth.

    Responses are validated against the candidate list; a rejected response
    gets one corrective retry quoting the exact error, then we give up and let
    the rule-based generator run.
    """
    if not candidates:
        return None

    settings = settings if settings is not None else {}
    config = ai_config(settings) if settings else None
    bounds = {
        'min_minutes': settings.get('workout.min_exercise_minutes', MIN_EXERCISE_MINUTES),
        'max_minutes': settings.get('workout.max_exercise_minutes', MAX_EXERCISE_MINUTES),
        'tolerance': float(settings.get('workout.duration_tolerance', 40)) / 100.0
                     if settings else TOTAL_DURATION_TOLERANCE,
        'expected_count': target_count,
    }

    # Grouped, numbered, one line each. Grouping is what lets the model satisfy
    # "cover every domain" by reading rather than reasoning; the numbers give
    # the retry message something to point at.
    grouped, order = {}, []
    for c in candidates:
        category = c.get('category') or 'General'
        if category not in grouped:
            grouped[category] = []
            order.append(category)
        grouped[category].append(c)
    lines, index = [], 1
    for category in order:
        lines.append(f"{category}:")
        for c in grouped[category]:
            muscles = str(c.get('target_muscles') or '').strip()
            lines.append(f"  {index}. {c['name']} — {c.get('difficulty', 'beginner')}"
                         + (f", works {muscles}" if muscles else ""))
            index += 1
    candidate_lines = '\n'.join(lines)

    count_rule = (f'Choose exactly {target_count} different exercises.'
                  if target_count else 'Choose 4 to 8 different exercises.')

    system_content = (
        'You are a strength coach picking exercises for one training session. '
        'Follow these rules exactly:\n'
        '1. Answer with JSON only: {"exercises": [{"name": "...", "duration": <minutes>}]}\n'
        '2. Every name must be copied character for character from the candidate list.\n'
        '3. Never repeat an exercise.\n'
        '4. "duration" is the length of ONE set of that exercise in minutes '
        '(0.5 is normal). The app decides how many sets to run, so do not try '
        'to make the durations add up to the session length.\n'
        '5. No prose, no explanation, no extra fields, no exercises of your own.'
    )

    priorities = [f'Difficulty: {difficulty}']
    if focus:
        priorities.append(f'Emphasise: {focus}')
    if goal:
        priorities.append(f"The athlete's goal: {goal}")
    if domains:
        priorities.append('Cover these areas: ' + ', '.join(domains))

    user_content = f"""Session brief
{chr(10).join(priorities)}
{count_rule}
Prefer exercises that train different muscles from each other.

Candidates:
{candidate_lines}

Answer with JSON in exactly this shape (this example is only the format, not the answer):
{{"exercises": [{{"name": "Push-ups", "duration": 0.5}}, {{"name": "Plank", "duration": 0.5}}]}}"""

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content}
    ]

    for attempt in range(LLM_RETRIES + 1):
        content = _post_chat_completion(messages, config=config)
        if content is None:
            return None  # transport failure — retrying won't help

        content = _strip_code_fence(content)

        try:
            selection = json.loads(content)
        except ValueError:
            selection, error = None, 'Response was not valid JSON.'

        if selection is not None:
            exercises, error = validate_llm_selection(selection, candidates, duration, **bounds)
            if exercises:
                # The model chose; the engine times. Set length comes from the
                # library record, not from the model's number — a small model
                # asked for "minutes of one set" will happily answer 12, and
                # there is no reason to let it redefine what a push-up is.
                by_name = {c['name'].lower(): c for c in candidates}
                picks = [dict(ex, duration=by_name.get(str(ex.get('name', '')).lower(), ex)
                              .get('duration', ex.get('duration')))
                         for ex in exercises]
                blocks = plan_sets(picks, duration, difficulty, settings)
                if not blocks:
                    return None
                total = plan_total_minutes(blocks)
                logger.info("LLM generated workout: %d exercises, %.1f min",
                            len(blocks), total)
                # Only the model's selection is used. Any other key it managed to
                # emit — including prose, if a server ignored response_format —
                # is dropped here rather than reaching the user.
                return {
                    'success': True,
                    'exercises': blocks,
                    'total_duration': total,
                    'explanation': build_workout_explanation(blocks, difficulty, focus)
                }

        logger.warning("LLM workout response rejected (attempt %d): %s", attempt + 1, error)
        if attempt < LLM_RETRIES:
            messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content":
                    f'That answer was rejected: {error} Answer again with JSON only. '
                    'Copy names exactly from the candidate list, no repeats'
                    + (f', exactly {target_count} exercises.' if target_count else '.')}
            ]

    return None

# ── Workout engine ───────────────────────────────────────────────────
#
# The helpers down to build_candidate_pool are pure: given exercise dicts and a
# settings dict they return a new list. Every knob in the "Workout building"
# settings group is therefore unit-testable without a request, a database or a
# model — the same split that keeps build_workout_explanation honest.

DOMAIN_TO_CATEGORY = {
    'strength_power': 'Strength & Power',
    'speed_mobility': 'Speed & Mobility',
    'endurance': 'Endurance',
    'agility': 'Agility',
    'cognition': 'Cognition',
    # The planner posts display names; accept both spellings.
    'Strength': 'Strength & Power',
    'Strength & Power': 'Strength & Power',
    'Speed & Mobility': 'Speed & Mobility',
    'Endurance': 'Endurance',
    'Agility': 'Agility',
    'Cognitive': 'Cognition',
    'Cognition': 'Cognition',
}

MOBILITY_CATEGORY = 'Speed & Mobility'

# Maps words appearing in an exercise's `equipment` cell onto the keys used by
# the workout.equipment setting. Anything unrecognised is treated as needing
# nothing: offering an exercise the user cannot do is a smaller failure than
# silently hiding one they can.
EQUIPMENT_SYNONYMS = {
    'none': 'none', 'no equipment': 'none', 'bodyweight': 'none',
    'chair': 'chair', 'desk': 'chair', 'seat': 'chair', 'sofa': 'chair',
    'wall': 'wall',
    'doorway': 'doorway', 'door frame': 'doorway', 'door': 'doorway',
    'staircase': 'stairs', 'stairs': 'stairs',
    'step': 'step', 'low box': 'step', 'box': 'step', 'platform': 'step',
    'exercise mat': 'mat', 'yoga mat': 'mat', 'mat': 'mat', 'carpet': 'mat',
    'towel': 'towel',
    'open space': 'open_space', 'space': 'open_space', 'room': 'open_space',
    'resistance band': 'resistance_band', 'resistance bands': 'resistance_band',
    'band': 'resistance_band', 'bands': 'resistance_band',
    'dumbbells': 'dumbbells', 'dumbbell': 'dumbbells', 'weights': 'dumbbells',
    'kettlebell': 'kettlebell', 'kettlebells': 'kettlebell',
    'pull-up bar': 'pullup_bar', 'pull up bar': 'pullup_bar',
    'pullup bar': 'pullup_bar', 'chin-up bar': 'pullup_bar',
    'jump rope': 'jump_rope', 'skipping rope': 'jump_rope', 'rope': 'jump_rope',
    'bench': 'bench',
    'cones': 'cones', 'cone': 'cones', 'markers': 'cones',
}

# Longest phrases first, so "pull-up bar" wins over the bare word "bar".
_EQUIPMENT_PHRASES = sorted(EQUIPMENT_SYNONYMS, key=len, reverse=True)


def equipment_requirements(exercise):
    """What an exercise needs, as a list of alternative requirement sets.

    An `equipment` cell reads either as a choice ("Wall or chair" — either will
    do) or a combination ("Mat, towel" — both needed). The result is a list of
    alternatives, each a set of required keys; the exercise is doable when ANY
    one alternative is fully covered.
    """
    raw = _clean_cell(exercise.get('equipment')).lower()
    if not raw or raw in ('none', 'nan', 'no equipment', 'bodyweight'):
        return [set()]

    alternatives = []
    for option in re.split(r'\bor\b|/', raw):
        needed = set()
        for part in re.split(r',|\band\b|\+|&', option):
            token = part.strip(' .')
            if not token:
                continue
            key = EQUIPMENT_SYNONYMS.get(token)
            if key is None:
                for phrase in _EQUIPMENT_PHRASES:
                    if phrase in token:
                        key = EQUIPMENT_SYNONYMS[phrase]
                        break
            if key and key != 'none':
                needed.add(key)
        alternatives.append(needed)
    return alternatives or [set()]


def filter_by_equipment(exercises, owned):
    """Keep exercises the user can actually perform with what they listed."""
    if not owned:
        return list(exercises)
    have = set(owned)
    return [ex for ex in exercises
            if any(alt <= have for alt in equipment_requirements(ex))]


def filter_by_exclusions(exercises, keyword_text):
    """Drop exercises matching any of the comma-separated keywords.

    Matched across name, muscles, description and instructions, so one word is
    enough when someone is training around an injury — "knee" removes both the
    exercises named for it and the ones that describe loading it.
    """
    keywords = [k.strip().lower() for k in str(keyword_text or '').split(',') if k.strip()]
    if not keywords:
        return list(exercises)
    kept = []
    for ex in exercises:
        haystack = ' '.join(
            str(ex.get(field, '') or '')
            for field in ('name', 'target_muscles', 'description', 'instructions')
        ).lower()
        if not any(word in haystack for word in keywords):
            kept.append(ex)
    return kept


def _difficulty_rank(exercise):
    return {'beginner': 0, 'intermediate': 1, 'advanced': 2}.get(
        str(exercise.get('difficulty', '')).lower(), 1)


def _minutes(exercise):
    try:
        return float(exercise.get('duration') or 0)
    except (TypeError, ValueError):
        return 0.0


# ── Sets, rest and honest wall-clock timing ──────────────────────────
#
# The library stores one *set* of each exercise, not a whole block: the median
# `duration` in the matrix is 0.7 min (~42 s), which is how long one set of that
# movement takes, not how long you should train it for. The old generator
# treated it as the block and simply added blocks until the minutes ran out, so
# "15 minutes" produced ~30 half-minute one-offs and, once the session timer
# paid the rest table between them, took closer to 37 real minutes.
#
# The planner below prescribes sets instead: work time comes from the library,
# set count from the difficulty, rest from the same table the session timer
# uses. Everything is seconds internally, because reps are not knowable from
# time-based data — a "set" here is a timed set of that exercise's natural
# length, not a generic 30 s block.

SETS_BY_DIFFICULTY = {'beginner': 2, 'intermediate': 3, 'advanced': 4}
MAX_SETS = 5                # ceiling for the budget top-up pass
MIN_SETS_TO_ADMIT = 2       # an exercise worth starting is worth two sets
LONG_SET_SECONDS = 120      # above this the library entry is a block, not a set
MAX_EXERCISES = 10          # beyond this a session stops being one workout
MIN_EXERCISES = 3           # breadth the planner tries to reach before depth
DEFAULT_REST_SECONDS = 30   # used when the rest table has no row
SET_SECONDS_STEP = 5        # per-set work times are rounded to whole 5 s


def rest_seconds_for(category, difficulty, settings):
    """Rest after one set, in seconds.

    Deliberately the same rule `restSecondsFor` runs in templates/session.html:
    the planner may only spend time the session timer will actually charge, or
    the promised length is fiction again.
    """
    settings = settings or {}
    source = settings.get('timers.rest_source', 'auto')
    if source == 'none':
        return 0
    try:
        multiplier = float(settings.get('timers.rest_multiplier', 100) or 100) / 100.0
    except (TypeError, ValueError):
        multiplier = 1.0
    if source == 'fixed':
        try:
            seconds = float(settings.get('timers.fixed_rest_seconds', DEFAULT_REST_SECONDS))
        except (TypeError, ValueError):
            seconds = DEFAULT_REST_SECONDS
    else:
        table = rest_times.get(category) or {}
        key = str(difficulty or 'beginner').lower()
        seconds = float(table.get(key) or table.get('beginner') or DEFAULT_REST_SECONDS)
    # +0.5 rather than round(): JavaScript's Math.round is half-up and the
    # session timer must charge exactly what the planner budgeted.
    return max(0, int(seconds * multiplier + 0.5))


def set_seconds_for(exercise, settings):
    """Work time for ONE set: the library's per-set time, clamped and rounded."""
    settings = settings or {}
    lo = float(settings.get('workout.min_exercise_minutes', MIN_EXERCISE_MINUTES))
    hi = float(settings.get('workout.max_exercise_minutes', MAX_EXERCISE_MINUTES))
    minutes = _minutes(exercise) or lo
    seconds = min(max(minutes, lo), hi) * 60
    return max(SET_SECONDS_STEP,
               int(round(seconds / SET_SECONDS_STEP) * SET_SECONDS_STEP))


def block_seconds(set_secs, sets, rest_secs):
    """Seconds an exercise occupies: its sets plus the rests between them."""
    return set_secs * sets + rest_secs * max(0, sets - 1)


def fit_base_sets(chosen, budget_seconds, base, difficulty, settings):
    """Trade sets for exercises when the prescribed set count won't fit.

    Four sets of one movement is not an advanced ten-minute session, it is a
    single exercise repeated — so the set count comes down until roughly
    MIN_EXERCISES blocks fit, and never below two: one set of anything is a
    demonstration, not training.
    """
    sample = [ex for ex in chosen[:5]]
    if not sample or base <= 2:
        return base
    set_secs = sorted(set_seconds_for(ex, settings) for ex in sample)[len(sample) // 2]
    rest_secs = sorted(rest_seconds_for(ex.get('category'), difficulty, settings)
                       for ex in sample)[len(sample) // 2]
    while base > 2:
        needed = (MIN_EXERCISES * block_seconds(set_secs, base, rest_secs)
                  + (MIN_EXERCISES - 1) * rest_secs)
        if needed <= budget_seconds:
            break
        base -= 1
    return base


def plan_sets(chosen, minutes, difficulty, settings, base_sets=None, max_sets=None):
    """Turn an ordered list of exercises into timed set blocks fitting `minutes`.

    The budget is wall clock, not work: rest between sets *and* the rest the
    session takes between exercises are both paid out of it. Exercises that no
    longer fit are dropped rather than shrunk, and leftover time is spent
    round-robin on extra sets so every movement grows evenly instead of the
    first one swallowing the budget.

    Pure: exercise dicts and a settings dict in, new dicts out. Each block
    carries `sets`, `set_seconds`, `rest_seconds` and a `duration` in minutes
    that is the whole block, so existing duration arithmetic keeps working.
    """
    budget = max(0.0, float(minutes or 0)) * 60
    settings = settings or {}
    base = base_sets if base_sets is not None else \
        SETS_BY_DIFFICULTY.get(str(difficulty or '').lower(), 2)
    ceiling = MAX_SETS if max_sets is None else max_sets
    base = max(1, min(base, ceiling))
    if max_sets is None:
        base = fit_base_sets(chosen, budget, base, difficulty, settings)

    blocks, spent = [], 0.0
    for exercise in chosen:
        if len(blocks) >= MAX_EXERCISES:
            break
        set_secs = set_seconds_for(exercise, settings)
        rest_secs = rest_seconds_for(exercise.get('category'), difficulty, settings)
        transition = rest_secs if blocks else 0
        # A four-minute cardio interval is already a block; splitting it into
        # sets would misread the library. Anything that long runs once.
        long_block = set_secs > LONG_SET_SECONDS
        sets = 1 if long_block else base
        floor = 1 if not blocks else min(sets, MIN_SETS_TO_ADMIT)
        # Shrink rather than return nothing when the budget is small, but stop
        # at the floor: a one-set stub on the end is worse than giving that
        # time to the exercises already chosen.
        while sets > floor and spent + transition + block_seconds(set_secs, sets, rest_secs) > budget:
            sets -= 1
        cost = transition + block_seconds(set_secs, sets, rest_secs)
        if blocks and spent + cost > budget:
            continue
        block = dict(exercise)
        block['sets'] = sets
        block['set_seconds'] = set_secs
        block['rest_seconds'] = rest_secs
        block['_max_sets'] = 1 if long_block else ceiling
        blocks.append(block)
        spent += cost

    # Spend what is left on extra sets, one per exercise per pass.
    progressed = True
    while progressed:
        progressed = False
        for block in blocks:
            if block['sets'] >= block['_max_sets']:
                continue
            cost = block['rest_seconds'] + block['set_seconds']
            if spent + cost <= budget:
                block['sets'] += 1
                spent += cost
                progressed = True

    for block in blocks:
        block.pop('_max_sets', None)
        block['work_seconds'] = block['set_seconds'] * block['sets']
        block['duration'] = round(
            block_seconds(block['set_seconds'], block['sets'], block['rest_seconds']) / 60.0, 2)
    return blocks


def plan_total_minutes(blocks):
    """Wall clock for a planned workout: every block plus the rest between them.

    Each planned block carries the rest that follows it, so the total is read
    off the plan rather than recomputed. The explanation sentence and the API
    total both come through here, which is what keeps them from disagreeing —
    and a workout with no rest figures (an older saved one) simply totals its
    blocks, exactly as it always did.
    """
    total = sum(_minutes(b) for b in blocks)
    for block in blocks[1:]:
        try:
            total += float(block.get('rest_seconds') or 0) / 60.0
        except (TypeError, ValueError):
            pass
    return round(total, 1)


def refit_plan(blocks, minutes):
    """Trim an already-planned, already-ordered session back inside `minutes`.

    Two things happen after `plan_sets` has balanced its budget, and both can
    push the total past the promise by a few seconds:

    - **Reordering.** The rest between two exercises is the *second* one's, so
      moving a short-rest block to the front (or a long-rest one out of it)
      changes the wall clock even though nothing was added. Ordering has to run
      after planning — the user's 'Exercise order' choice must not be what
      decides which exercises get dropped — so the correction happens here.
    - **Bookends.** A warm-up and a cool-down are planned against their own
      minutes; the two transitions joining them to the main work are not in
      anybody's budget.

    Sets come off the deepest block first, so the trim is spread rather than
    gutting one exercise; only when everything is down to a single set does a
    block go. Returns a new list, and never touches `minutes <= 0`.
    """
    if not blocks or minutes <= 0:
        return list(blocks)
    if not all(b.get('sets') and b.get('set_seconds') for b in blocks):
        return list(blocks)      # an older saved plan carries no set figures
    plan = [dict(b) for b in blocks]

    def retime(block):
        block['work_seconds'] = block['set_seconds'] * block['sets']
        block['duration'] = round(block_seconds(
            block['set_seconds'], block['sets'], block['rest_seconds']) / 60.0, 2)

    while plan and plan_total_minutes(plan) > minutes:
        deepest = max(range(len(plan)), key=lambda i: (plan[i]['sets'], -i))
        if plan[deepest]['sets'] > 1:
            plan[deepest]['sets'] -= 1
            retime(plan[deepest])
        else:
            plan.pop()
    return plan


# ── Reading an exercise: muscles, regions, movement patterns ─────────
#
# The library labels muscles in free text ("Chest, shoulders, triceps"), which
# is precise enough to spot a repeat but too fine to balance a session with: a
# workout of Diamond Push-ups, Decline Push-ups and Push-up Progression shares
# no *token* problem, it shares a movement. So the selector reads three levels
# of the same exercise — its muscle words, the body region they belong to, and
# the movement pattern its name describes — and charges for a repeat at every
# level. That is what stops three presses in a row without needing a model.

_MUSCLE_SPLIT = re.compile(r'[,/&]| and ')

MUSCLE_REGIONS = {
    'chest': 'push', 'shoulders': 'push', 'triceps': 'push', 'deltoids': 'push',
    'back': 'pull', 'lats': 'pull', 'biceps': 'pull', 'forearms': 'pull',
    'rear delts': 'pull', 'traps': 'pull', 'upper back': 'pull',
    'quadriceps': 'legs', 'quads': 'legs', 'hamstrings': 'legs', 'glutes': 'legs',
    'calves': 'legs', 'adductors': 'legs', 'hip abductors': 'legs',
    'hip flexors': 'legs', 'lower body': 'legs', 'legs': 'legs', 'ankles': 'legs',
    'core': 'core', 'obliques': 'core', 'lower abs': 'core', 'abs': 'core',
    'lower back': 'core', 'abdominals': 'core',
    'cardio': 'cardio', 'cardiovascular fitness': 'cardio', 'endurance': 'cardio',
    'brain': 'cognitive', 'memory': 'cognitive', 'working memory': 'cognitive',
    'fluid intelligence': 'cognitive', 'pattern recognition': 'cognitive',
    'spatial awareness': 'cognitive', 'creativity': 'cognitive',
    'concentration': 'cognitive', 'focus': 'cognitive', 'attention': 'cognitive',
    'coordination': 'neural', 'balance': 'neural', 'agility': 'neural',
    'speed': 'neural', 'reaction time': 'neural', 'rhythm': 'neural',
    'control': 'neural', 'power': 'neural',
    'full body': 'full body', 'upper body': 'push', 'neck': 'mobility',
    'wrists': 'mobility', 'flexibility': 'mobility', 'strength': 'full body',
}

# Ordered: the first phrase found in the name wins, so "Plank to Push-up" is a
# push and "Mountain Climber Twists" is core. Name before muscles, because the
# name is what the exercise *is* and the muscle list is what it happens to hit.
MOVEMENT_PATTERN_RULES = (
    ('cognitive', ('memory', 'math', 'mindful', 'brain', 'n-back', 'teaser',
                   'recognition', 'reaction time', 'spatial', 'rhythm',
                   'dual task', 'pattern')),
    ('push', ('push-up', 'push up', 'pushup', 'dip', 'press', 'overhead reach')),
    ('pull', ('row', 'pull', 'superman', 'chin')),
    ('hinge', ('bridge', 'hip thrust', 'deadlift', 'good morning', 'inchworm',
               'glute')),
    ('squat', ('squat', 'wall sit', 'step-up', 'step up', 'knee extension')),
    ('lunge', ('lunge', 'skater', 'split')),
    ('jump', ('jump', 'hop', 'bound', 'burpee', 'skip', 'jack')),
    ('core', ('plank', 'crunch', 'dead bug', 'leg raise', 'hollow', 'tabletop',
              'shoulder tap', 'twist', 'bear crawl', 'crab walk', 'sit-up')),
    ('carry', ('hold', 'isometric', 'wall sit', 'carry')),
    ('mobility', ('stretch', 'circle', 'swing', 'roll', 'mobility', 'hug',
                  'toe touch', 'heel to butt')),
    ('locomotion', ('run', 'sprint', 'shuttle', 'shuffle', 'carioca', 'grapevine',
                    'weave', 'walk', 'jog', 'march', 'climb', 'knee', 'kick',
                    'boxing', 'danc', 'drill', 'step', 'ladder', 'cone',
                    'backpedal', 'quick feet', 'figure 8', 't-drill', 'balance')),
    ('calves', ('calf raise',)),
)

_ANATOMICAL_REGIONS = {'push', 'pull', 'legs', 'core'}

_REGION_TO_PATTERN = {'push': 'push', 'pull': 'pull', 'legs': 'squat',
                      'core': 'core', 'cardio': 'locomotion',
                      'cognitive': 'cognitive', 'neural': 'locomotion',
                      'mobility': 'mobility'}


def muscle_tokens(exercise):
    """Muscle words for overlap scoring — 'Chest, shoulders' → {chest, shoulders}."""
    raw = str(exercise.get('target_muscles') or '')
    return {t.strip().lower() for t in _MUSCLE_SPLIT.split(raw) if t.strip()}


def muscle_regions(exercise):
    """Coarse regions a muscle list belongs to — the unit session balance uses.

    'Chest, shoulders, triceps' and 'Chest, triceps' are different token sets
    but the same region ({push}), which is the whole point: two pressing
    exercises should read as a repeat even when their labels differ.
    """
    regions = {MUSCLE_REGIONS[t] for t in muscle_tokens(exercise) if t in MUSCLE_REGIONS}
    # 'Full body, power' is still a full-body exercise; 'Full body, chest' is a
    # chest one that was labelled loosely. Only a named body part displaces it.
    if regions & _ANATOMICAL_REGIONS:
        regions.discard('full body')
    return regions


def movement_pattern(exercise):
    """The movement an exercise is, as one word — 'push', 'squat', 'locomotion'.

    Read from the name first, then from the regions, then from the category, so
    a library row with no muscle labels still classifies to something usable.
    """
    name = str(exercise.get('name') or '').lower()
    for pattern, phrases in MOVEMENT_PATTERN_RULES:
        if any(phrase in name for phrase in phrases):
            return pattern
    for region in sorted(muscle_regions(exercise)):
        if region in _REGION_TO_PATTERN:
            return _REGION_TO_PATTERN[region]
    return str(exercise.get('category') or 'general').lower()


# ── Focus: what the user asked to emphasise ──────────────────────────
#
# The client sends a small vocabulary ('upper_body', 'lower_body', 'core',
# 'cardio', 'flexibility'); free text from anywhere else is matched word by
# word. Focus only *biases the selector* — it never widens the pool and never
# reaches the explanation, so nothing the user reads can claim more than the
# engine actually did.

FOCUS_PROFILES = {
    'upper_body': ({'push', 'pull'}, ('upper', 'chest', 'shoulder', 'arm',
                                      'tricep', 'bicep', 'back', 'push', 'pull')),
    'lower_body': ({'legs'}, ('lower', 'leg', 'squat', 'lunge', 'glute',
                              'quad', 'hamstring', 'calf', 'hip')),
    'core': ({'core'}, ('core', 'abs', 'plank', 'oblique', 'trunk')),
    'cardio': ({'cardio'}, ('cardio', 'run', 'jump', 'heart', 'aerobic',
                            'conditioning', 'sprint')),
    'flexibility': ({'mobility'}, ('stretch', 'mobility', 'flexib', 'circle',
                                   'swing', 'range of motion')),
    'full_body': ({'full body'}, ('full body', 'total body', 'whole body')),
}

_FOCUS_ALIASES = {
    'upper': 'upper_body', 'upper body': 'upper_body', 'push': 'upper_body',
    'lower': 'lower_body', 'lower body': 'lower_body', 'legs': 'lower_body',
    'abs': 'core', 'trunk': 'core',
    'conditioning': 'cardio', 'endurance': 'cardio',
    'mobility': 'flexibility', 'stretching': 'flexibility',
    'full body': 'full_body', 'total body': 'full_body',
}


def focus_profile(focus):
    """Turn a focus string into (regions, keywords), or None when there is none.

    Accepts the client's vocabulary ('upper_body'), the everyday phrasings a
    user or a program CSV writes ('Upper Body'), and anything else as bare
    keywords — an unknown focus still steers selection instead of being lost.
    """
    text = str(focus or '').strip().lower().replace('-', ' ')
    if not text:
        return None
    key = text.replace(' ', '_')
    if key in FOCUS_PROFILES:
        return FOCUS_PROFILES[key]
    if text in _FOCUS_ALIASES:
        return FOCUS_PROFILES[_FOCUS_ALIASES[text]]
    for alias, target in _FOCUS_ALIASES.items():
        if alias in text:
            return FOCUS_PROFILES[target]
    words = tuple(w for w in re.split(r'[^a-z]+', text) if len(w) > 2)
    return (set(), words) if words else None


def focus_match(exercise, profile):
    """How well one exercise serves the focus, 0.0 to 1.0.

    A region hit is the strong signal (the exercise genuinely trains that part);
    a word hit in the name or muscle list is the weaker one, so a keyword-only
    focus still ranks sensibly.
    """
    if not profile:
        return 0.0
    regions, keywords = profile
    score = 0.0
    if regions and (regions & muscle_regions(exercise)):
        score += 0.7
    if keywords:
        haystack = f"{exercise.get('name', '')} {exercise.get('target_muscles', '')}".lower()
        if any(word in haystack for word in keywords):
            score += 0.5 if regions else 1.0
    return min(1.0, score)


# ── Choosing what goes in ────────────────────────────────────────────

# Penalty weights. Kept together because they only mean anything relative to
# each other: difficulty is the hardest constraint, a repeated movement costs
# more than a repeated category, and a focus hit is worth about one category
# clash — enough to steer the session, never enough to override difficulty.
W_DIFFICULTY = 3.0
W_CATEGORY = 2.0
W_MUSCLE = 2.0
W_REGION = 1.5
W_PATTERN = 2.5
W_RECENT = 4.0
W_HISTORY = 1.5
W_FOCUS = 4.0
W_UNLABELLED = 0.5
FOCUS_REPEAT_DISCOUNT = 0.75   # how much a focus hit relaxes the spread terms


def new_balance():
    """The running tally of what a workout already contains."""
    return {'categories': {}, 'muscles': {}, 'regions': {}, 'patterns': {}}


def add_to_balance(balance, exercise):
    """Record one pick, so the next score sees it."""
    balance['categories'][exercise.get('category') or 'General'] = \
        balance['categories'].get(exercise.get('category') or 'General', 0) + 1
    for token in muscle_tokens(exercise):
        balance['muscles'][token] = balance['muscles'].get(token, 0) + 1
    for region in muscle_regions(exercise):
        balance['regions'][region] = balance['regions'].get(region, 0) + 1
    pattern = movement_pattern(exercise)
    balance['patterns'][pattern] = balance['patterns'].get(pattern, 0) + 1
    return balance


def _mean_load(counts, keys):
    if not keys:
        return 0.0
    return sum(counts.get(k, 0) for k in keys) / float(len(keys))


def selection_penalty(exercise, difficulty, avoid, balance,
                      focus=None, history=None):
    """Lower is better. Deterministic, so the same request plans the same way.

    Six pressures, in the order they matter for a training session: difficulty
    match, then what the user asked to emphasise, then a movement pattern the
    session has already used, then spread across the domains asked for and the
    muscles not yet worked, then anything trained recently.

    Every term counts *how many times* something has appeared rather than
    whether it has. An earlier version used a set of muscles already worked, so
    the second chest exercise and the fourth cost exactly the same and a
    strength session drifted into three presses in a row.
    """
    target_rank = {'beginner': 0, 'intermediate': 1, 'advanced': 2}.get(
        str(difficulty or '').lower())
    if target_rank is None:
        penalty = 0.0
    else:
        penalty = W_DIFFICULTY * abs(_difficulty_rank(exercise) - target_rank)

    match = focus_match(exercise, focus)
    penalty -= W_FOCUS * match
    # Asking for an upper-body session is asking for the upper body *again and
    # again*, so a focus hit buys permission to repeat its region, its muscles
    # and its category — but never its movement: push, push, push is still a
    # worse upper-body workout than push, pull, push, pull.
    spread = 1.0 - FOCUS_REPEAT_DISCOUNT * match

    penalty += W_PATTERN * balance['patterns'].get(movement_pattern(exercise), 0)
    penalty += spread * W_CATEGORY * balance['categories'].get(
        exercise.get('category') or 'General', 0)

    tokens = muscle_tokens(exercise)
    if tokens:
        penalty += spread * W_MUSCLE * _mean_load(balance['muscles'], tokens)
        penalty += spread * W_REGION * _mean_load(
            balance['regions'], muscle_regions(exercise))
    else:
        penalty += W_UNLABELLED  # unlabelled exercises can't be balanced

    if history:
        penalty += W_HISTORY * _mean_load(history, muscle_regions(exercise))

    if str(exercise.get('name', '')).lower() in avoid:
        penalty += W_RECENT
    return penalty


def _tiebreak(name, seed):
    """Stable pseudo-order for equally-scored exercises.

    Plain alphabetical tie-breaking made every beginner strength session open
    with Arm Circles. Hashing name+seed keeps a single request deterministic
    (same seed, same workout) while a seed that changes daily rotates which of
    several equally good exercises comes first.
    """
    return hashlib.md5(f"{seed}|{name}".encode('utf-8')).hexdigest()


def select_balanced(pool, difficulty, avoid=(), limit=MAX_EXERCISES, seed='',
                    focus=None, history=None):
    """Pick up to `limit` exercises that cover the pool instead of scanning it.

    Greedy, but the score is recomputed after every pick, so each choice sees
    which categories, muscles, regions and movement patterns the workout
    already has. That is what stops a 'Strength + Endurance' request coming
    back as eight chest exercises in database order, which is what a plain
    greedy fill always did.
    """
    avoid = {str(n).lower() for n in (avoid or ())}
    profile = focus if isinstance(focus, tuple) else focus_profile(focus)
    remaining = list(pool)
    chosen, balance = [], new_balance()

    while remaining and len(chosen) < limit:
        best = min(remaining, key=lambda ex: (
            selection_penalty(ex, difficulty, avoid, balance, profile, history),
            _tiebreak(str(ex.get('name', '')), seed)))
        remaining.remove(best)
        chosen.append(best)
        add_to_balance(balance, best)
    return chosen


# ── Sequencing: which order the chosen work is done in ───────────────


def _adjacency_cost(exercise, previous):
    """How much `exercise` clashes with the blocks just before it.

    Back-to-back work on the same pattern or the same region is the one
    ordering mistake that actually costs a trainee reps — the immediately
    preceding block counts double the one before it.
    """
    cost = 0.0
    for distance, earlier in enumerate(reversed(previous[-2:])):
        weight = 1.0 / (distance + 1)
        if movement_pattern(exercise) == movement_pattern(earlier):
            cost += 3.0 * weight
        shared = muscle_regions(exercise) & muscle_regions(earlier)
        if shared:
            cost += 2.0 * weight * len(shared)
        if (exercise.get('category') or '') == (earlier.get('category') or ''):
            cost += 1.0 * weight
    return cost


def sequence_cost(exercises):
    """Total clash across a whole ordering — the number the sequencer minimises."""
    items = list(exercises)
    return sum(_adjacency_cost(items[i], items[:i]) for i in range(1, len(items)))


def sequence_for_recovery(exercises):
    """Order a chosen set so consecutive blocks rest each other. Adds nothing.

    Greedy first — keep the selector's best pick as the opener, then repeatedly
    take whichever remaining exercise clashes least with what was just done —
    and then a swap pass, because greedy alone defers the awkward exercises and
    ends up stacking them: six strength blocks with three presses among them
    came back with all three presses last. Swapping any two positions that
    lowers the total cost fixes that, and with at most MAX_EXERCISES blocks the
    whole pass is a few hundred comparisons.

    Deterministic throughout: ties break on the order the exercises were
    chosen, and a swap must strictly improve the total to be taken.
    """
    items = list(exercises)
    if len(items) < 3:
        return items
    ordered, remaining = [items[0]], items[1:]
    while remaining:
        best = min(range(len(remaining)),
                   key=lambda i: (_adjacency_cost(remaining[i], ordered), i))
        ordered.append(remaining.pop(best))

    cost = sequence_cost(ordered)
    for _ in range(len(ordered)):
        improved = False
        for i in range(len(ordered) - 1):
            for j in range(i + 1, len(ordered)):
                swapped = list(ordered)
                swapped[i], swapped[j] = swapped[j], swapped[i]
                candidate = sequence_cost(swapped)
                if candidate < cost - 1e-9:
                    ordered, cost, improved = swapped, candidate, True
        if not improved:
            break
    return ordered


def order_exercises(exercises, mode):
    """Sequence a chosen set. Never adds or removes anything."""
    items = list(exercises)
    if mode == 'hardest_first':
        return sorted(items, key=lambda e: -_difficulty_rank(e))
    if mode == 'easiest_first':
        return sorted(items, key=_difficulty_rank)
    if mode == 'longest_first':
        return sorted(items, key=lambda e: -_minutes(e))
    if mode == 'shortest_first':
        return sorted(items, key=_minutes)
    if mode == 'alternate':
        # Spread the work: consecutive blocks avoid repeating a movement
        # pattern, a body region and a category, in that order of importance.
        # The old version round-robined categories alone, which did nothing at
        # all to a single-category session — exactly the case (a pure strength
        # workout) where ordering matters most.
        return sequence_for_recovery(items)
    return items  # 'as_generated'


# ── Filling the time asked for ───────────────────────────────────────


def _plan_shortfall(blocks, minutes):
    """Minutes of the request a plan leaves unspent (0 when it fills it)."""
    return max(0.0, float(minutes or 0) - plan_total_minutes(blocks))


def select_exercises_rule_based(pool, duration, difficulty, settings, avoid=(),
                                seed='', focus=None, history=None):
    """Fill `duration` minutes from `pool` without a model.

    This is the engine's own answer, used whenever the model is off, absent or
    rejected — and, since it also decides how many exercises a workout should
    contain, the reference plan the model is asked to improve on.

    It widens its search in defined steps rather than returning a thin workout:
    the requested difficulty first, then neighbouring difficulties when
    workout.difficulty_spillover allows it. A tier is only *accepted* when the
    plan it produces actually fills the time — the old version returned the
    first tier that produced anything at all, so 60 advanced minutes of
    Strength & Power came back as the library's one advanced strength exercise
    and ten real minutes. Now a tier that underfills is kept as a candidate and
    the next one is tried; the fullest plan wins.
    """
    settings = settings or {}
    avoid = {str(name).lower() for name in (avoid or ())}
    variety = settings.get('workout.variety', 'balanced')
    profile = focus_profile(focus)

    try:
        tolerance = float(settings.get('workout.duration_tolerance', 40)) / 100.0
    except (TypeError, ValueError):
        tolerance = 0.4
    floor_minutes = float(duration or 0) * (1.0 - max(0.0, min(tolerance, 0.9)))

    wanted = str(difficulty or '').lower()
    exact = [ex for ex in pool if wanted and wanted in str(ex.get('difficulty', '')).lower()]
    tiers = [exact, pool] if settings.get('workout.difficulty_spillover', True) \
        else [exact or pool]

    best = None
    for tier in tiers:
        if not tier:
            continue
        candidates = list(tier)
        if variety == 'strict':
            fresh = [ex for ex in candidates if str(ex.get('name', '')).lower() not in avoid]
            if fresh:
                candidates = fresh
        # 'balanced' needs no filtering: recency is one term in the penalty, so
        # a recent exercise still appears when a small library has nothing else.
        soften = () if variety == 'repeat_ok' else avoid

        chosen = select_balanced(candidates, difficulty, soften, MAX_EXERCISES, seed,
                                 focus=profile, history=history)
        blocks = plan_sets(chosen, duration, difficulty, settings)
        if not blocks:
            continue
        total = plan_total_minutes(blocks)
        if total >= floor_minutes:
            return blocks
        if best is None or total > plan_total_minutes(best):
            best = blocks
    if best:
        return best

    if pool:
        return plan_sets([min(pool, key=_minutes)], duration, difficulty, settings,
                         base_sets=1, max_sets=1)
    return []


def build_bookend(pool, minutes, settings=None, seed='', avoid=()):
    """Easy mobility work totalling roughly `minutes`, for a warm-up or cool-down.

    Drawn from the same library as everything else, so a warm-up respects the
    user's equipment and exclusion settings for free — `pool` arrives filtered.
    One set each: a warm-up is a sequence, not a prescription. The seed is the
    day's, so the warm-up rotates with the workout instead of being the same
    four movements every morning.
    """
    if minutes <= 0 or not pool:
        return []
    mobility = [ex for ex in pool if ex.get('category') == MOBILITY_CATEGORY] or pool
    easy = [ex for ex in mobility
            if str(ex.get('difficulty', '')).lower() == 'beginner'] or mobility
    chosen = select_balanced(easy, 'beginner', avoid, MAX_EXERCISES, seed)
    return plan_sets(chosen, minutes, 'beginner', settings or {}, base_sets=1, max_sets=1)


def recent_region_load(names, library):
    """How much each body region the user trained lately, normalised to 0..1.

    Names alone say what to avoid repeating; the regions behind them say what
    is *tired*. Two heavy leg sessions this week should push the next one
    towards the upper body even when none of the individual exercises repeat.
    Weighted by recency: the most recent session's exercises count most.
    """
    if not names:
        return {}
    by_name = {str(ex.get('name', '')).lower(): ex for ex in (library or [])}
    load = {}
    for position, name in enumerate(names):
        exercise = by_name.get(str(name).lower())
        if not exercise:
            continue
        weight = 1.0 / (1.0 + position / 10.0)
        for region in muscle_regions(exercise):
            load[region] = load.get(region, 0.0) + weight
    peak = max(load.values()) if load else 0.0
    return {region: value / peak for region, value in load.items()} if peak else {}


def recent_exercise_names(user_id, sessions=5):
    """Names from the user's last few sessions — the input to workout.variety."""
    try:
        if user_id in (None, 'guest'):
            history = session.get('recent_exercise_names', [])
            return [str(name) for name in history][:80]

        conn = get_db_connection()
        try:
            rows = conn.execute('''
                SELECT exercises_completed FROM user_sessions
                WHERE user_id = ? ORDER BY id DESC LIMIT ?
            ''', (user_id, int(sessions))).fetchall()
        finally:
            conn.close()

        names = []
        for row in rows:
            try:
                for entry in json.loads(row['exercises_completed'] or '[]'):
                    if isinstance(entry, dict) and entry.get('name'):
                        names.append(str(entry['name']))
            except ValueError:
                continue
        return names[:80]
    except Exception:
        logger.debug("Could not read recent exercise names", exc_info=True)
        return []


def build_candidate_pool(domains, settings, exercises=None):
    """Every exercise the user's settings allow for the requested domains.

    One place decides what may appear in a workout, so the model path and the
    rule-based path can never disagree about what is on the table — the model
    is only ever offered exercises the rules would also have allowed.
    """
    library = exercises if exercises is not None else get_all_exercises_from_db()
    categories = {DOMAIN_TO_CATEGORY[d] for d in domains if d in DOMAIN_TO_CATEGORY}
    pool = [ex for ex in library if ex.get('category') in categories]
    pool = filter_by_equipment(pool, (settings or {}).get('workout.equipment'))
    pool = filter_by_exclusions(pool, (settings or {}).get('workout.exclude_keywords'))
    return pool


def _json_safe_exercise(exercise):
    """Replace pandas NaN with None so the response serialises."""
    clean = {}
    for key, value in exercise.items():
        try:
            clean[key] = None if pd.isna(value) else value
        except (TypeError, ValueError):
            clean[key] = value
    return clean


@app.route('/api/generate-workout', methods=['POST'])
def api_generate_workout():
    """Generate a custom workout from the request and the user's settings.

    Each stage falls through to the next:
      1. the local model, when ai.enabled and ai.workout_generation allow it
         (workout generation is opt-in: the rule engine measured better),
      2. the rule-based selector,
      3. the single shortest exercise, so a non-empty pool never yields an
         empty workout.

    The response names the engine that answered in `engine` and lists any
    settings that narrowed the pool in `applied_settings`, so a user can always
    see why they got what they got.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'No JSON data received'}), 400

        settings = get_settings()

        domains = data.get('domains', [])
        if not isinstance(domains, list) or not domains:
            return jsonify({'error': 'At least one training domain is required'}), 400
        try:
            duration = int(data.get('duration', settings.get('workout.default_duration', 15)))
        except (TypeError, ValueError):
            return jsonify({'error': 'Duration must be a number of minutes'}), 400
        if not 1 <= duration <= 240:
            return jsonify({'error': 'Duration must be between 1 and 240 minutes'}), 400

        difficulty = data.get('difficulty') or settings.get('workout.default_difficulty', 'beginner')
        goal = data.get('goal', '')
        focus = data.get('focus', '')
        logger.debug("generate-workout: domains=%s duration=%s difficulty=%s",
                     domains, duration, difficulty)

        try:
            library = get_all_exercises_from_db()
        except Exception as e:
            return jsonify({'error': f'Database error: {str(e)}'}), 500
        if not library:
            return jsonify({'error': 'No exercise data available'}), 400

        pool = build_candidate_pool(domains, settings, library)

        # Warm-up and cool-down are carved out of the requested time rather than
        # added to it — the user asked for a workout of this length.
        warmup_minutes = (int(settings.get('workout.warmup_minutes', 3))
                          if settings.get('workout.include_warmup') else 0)
        cooldown_minutes = (int(settings.get('workout.cooldown_minutes', 3))
                            if settings.get('workout.include_cooldown') else 0)
        if warmup_minutes + cooldown_minutes >= duration:
            warmup_minutes = cooldown_minutes = 0
        main_duration = duration - warmup_minutes - cooldown_minutes

        avoid = (recent_exercise_names(session.get('user_id', 'guest'))
                 if settings.get('workout.variety', 'balanced') != 'repeat_ok' else ())
        # What those recent names actually *worked*, so two leg days in a row
        # push the selector towards the upper body even when no single exercise
        # repeats. Names say what not to redo; regions say what is still tired.
        history = recent_region_load(avoid, library)
        # Rotates the tie-break order once a day per user, so asking for the
        # same session tomorrow is not the same six exercises in the same
        # order — without making a single request non-deterministic.
        seed = f"{session.get('user_id', 'guest')}:{datetime.now().strftime('%Y-%m-%d')}"

        bookend_pool = (build_candidate_pool(['Speed & Mobility'], settings, library)
                        if (warmup_minutes or cooldown_minutes) else [])
        warmup = build_bookend(bookend_pool, warmup_minutes, settings, seed=seed,
                               avoid=avoid)
        cooldown = build_bookend(bookend_pool, cooldown_minutes, settings,
                                 seed=f'{seed}:cooldown', avoid=avoid)

        # The rule-based plan is built first even when the model is on: it is
        # the fallback, and it is also how many exercises the time actually
        # holds once sets and rest are paid for. The model is then asked to
        # choose that many from a shortlist — a judgement call — instead of
        # doing the arithmetic the engine has already done.
        main_exercises = select_exercises_rule_based(
            pool, main_duration, difficulty, settings, avoid=avoid, seed=seed,
            focus=focus, history=history)
        engine = 'rules'

        if (settings.get('ai.enabled', True) and settings.get('ai.workout_generation', False)
                and main_exercises):
            # Full library records, so a model-chosen exercise keeps its
            # instructions and equipment just like a rule-chosen one.
            candidates = shortlist_candidates(pool, difficulty, avoid, seed=seed,
                                              focus=focus, history=history)
            llm_workout = generate_workout_via_llm(
                domains, main_duration, difficulty, focus, candidates, goal, settings,
                target_count=len(main_exercises), avoid=avoid)
            if llm_workout:
                main_exercises = llm_workout['exercises']
                engine = 'ai'

        main_exercises = order_exercises(main_exercises,
                                         settings.get('workout.ordering', 'alternate'))

        # Ordering and the bookend joins both move the wall clock a little, so
        # the whole session is trimmed back to the length that was asked for.
        planned = refit_plan(warmup + main_exercises + cooldown, duration)
        exercises = [_json_safe_exercise(ex) for ex in planned]
        total_time = plan_total_minutes(exercises)

        wanted_categories = {DOMAIN_TO_CATEGORY.get(d) for d in domains}
        unfiltered = sum(1 for e in library if e.get('category') in wanted_categories)
        applied = []
        if len(pool) < unfiltered:
            applied.append(f'{unfiltered - len(pool)} exercises filtered out by your equipment '
                           'and exclusion settings')
        if warmup:
            applied.append(f'{warmup_minutes} min warm-up')
        if cooldown:
            applied.append(f'{cooldown_minutes} min cool-down')
        # Say so rather than quietly handing back a short session: with a small
        # library, breadth (MAX_EXERCISES) and depth (MAX_SETS) can both run out
        # before the clock does.
        shortfall = round(duration - total_time)
        if shortfall >= 2:
            applied.append(f'{shortfall} min short of the {duration} requested — the '
                           'exercises your settings allow ran out')

        return jsonify({
            'success': True,
            'exercises': exercises,
            'total_duration': total_time,
            'difficulty': difficulty,
            'explanation': build_workout_explanation(exercises, difficulty, focus),
            'engine': engine,
            'applied_settings': applied,
            'pool_size': len(pool),
            'workout_id': f"workout_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        })
    except Exception as e:
        logger.exception("generate-workout failed")
        return jsonify({'error': str(e)}), 500

@app.route('/api/today-workout', methods=['POST'])
def api_today_workout():
    """Build today's workout from the detailed training program prescriptions.

    Program days with exact prescriptions ("Push-ups (2min), Plank (3min)...")
    are served verbatim with library metadata attached. Freeform days
    ("All categories mini-circuit") return fallback=True so the client uses
    the AI workout generator with the day's focus instead.
    """
    try:
        user_id = session.get('user_id', 'guest')
        current_week = get_current_week(user_id)
        current_day = datetime.now().strftime('%A')

        entry = find_program_entry(training_data.get('student_program'), current_week, current_day)
        if not entry:
            return jsonify({'success': False, 'fallback': True,
                            'reason': 'No detailed program entry for today'})

        prescription = parse_prescription(entry.get('Exercises'))
        if not prescription:
            return jsonify({'success': False, 'fallback': True,
                            'reason': 'Freeform program day'})

        exercises = resolve_prescribed_exercises(prescription, get_settings())
        total = round(sum(ex['duration'] for ex in exercises), 1)

        # Intensity lives in the high-level program CSV; use it for rest times
        high_level = find_program_entry(training_data.get('program'), current_week, current_day)
        difficulty = intensity_to_difficulty(high_level.get('Intensity') if high_level else '')

        focus = entry.get('Focus_Areas') or ''
        notes = entry.get('Progression_Notes') or ''
        explanation = f"Week {current_week} of your program — {focus}."
        if notes:
            explanation += f" {notes}."

        logger.info("Serving prescribed workout: week %s %s, %d exercises, %.1f min",
                    current_week, current_day, len(exercises), total)
        return jsonify({
            'success': True,
            'exercises': exercises,
            'total_duration': total,
            'difficulty': difficulty,
            'sessionType': entry.get('Session_Type') or 'Program Session',
            'explanation': explanation,
            'week': current_week,
            'workout_id': f"program_w{current_week}_{current_day.lower()}"
        })
    except Exception as e:
        logger.exception("today-workout failed")
        return jsonify({'success': False, 'fallback': True, 'error': str(e)}), 500

@app.route('/api/exercises/add', methods=['POST'])
def api_add_exercise():
    """Add a custom exercise to the library."""
    if session.get('user_id') in (None, 'guest'):
        return jsonify({'success': False, 'error': 'Login required'}), 401
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data received'}), 400
            
        name = data.get('name')
        category = data.get('category')
        duration = float(data.get('duration', 0.5))
        difficulty = data.get('difficulty', 'beginner')
        description = data.get('description', '')
        target_muscles = data.get('target_muscles', '')
        instructions = data.get('instructions', '')
        # Free text rather than a fixed list: the equipment filter maps whatever
        # is written here through EQUIPMENT_SYNONYMS, and anything it cannot
        # place is treated as needing nothing.
        equipment = str(data.get('equipment', '') or 'None').strip() or 'None'

        if not name or not category:
            return jsonify({'success': False, 'error': 'Name and Category are required'}), 400

        # Save to database
        conn = get_db_connection()
        try:
            conn.execute('''
                INSERT INTO exercises (category, exercise_name, duration_minutes,
                                     primary_benefit, secondary_benefit, difficulty_level,
                                     instructions, equipment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (category, name, duration, description, target_muscles, difficulty,
                  instructions, equipment))
            conn.commit()
        finally:
            conn.close()
        invalidate_exercise_cache()

        new_ex = {
            'category': category,
            'name': name,
            'duration': duration,
            'description': description,
            'target_muscles': target_muscles,
            'difficulty': difficulty,
            'instructions': instructions,
            'equipment': equipment
        }
        return jsonify({'success': True, 'exercise': new_ex})
    except sqlite3.IntegrityError:
        return jsonify({
            'success': False,
            'error': 'An exercise with that name already exists in this category.'
        }), 409
    except Exception as e:
        logger.exception("Failed to add custom exercise")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/workouts/save', methods=['POST'])
def api_save_workout():
    """Save an approved workout template."""
    if session.get('user_id') in (None, 'guest'):
        return jsonify({'success': False, 'error': 'Login required'}), 401
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data received'}), 400
            
        user_id = str(session.get('user_id'))
        name = data.get('workout_name')
        description = data.get('workout_description', '')
        exercises = data.get('exercises', [])
        duration = float(data.get('total_duration', 0))
        difficulty = data.get('difficulty', 'intermediate')
        
        if not name or not exercises:
            return jsonify({'success': False, 'error': 'Workout name and exercises are required'}), 400

        conn = get_db_connection()
        conn.execute('''
            INSERT INTO saved_workouts (user_id, workout_name, workout_description, exercises_json, total_duration, difficulty)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, name, description, json.dumps(exercises), duration, difficulty))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        logger.exception("Failed to save workout template")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/workouts/saved', methods=['GET'])
def api_list_saved_workouts():
    """Get all saved workouts for the current user."""
    try:
        user_id = str(session.get('user_id', 'guest'))
        conn = get_db_connection()
        rows = conn.execute('''
            SELECT id, workout_name, workout_description, exercises_json, total_duration, difficulty, created_date
            FROM saved_workouts
            WHERE user_id = ?
            ORDER BY created_date DESC
        ''', (user_id,)).fetchall()
        conn.close()
        
        saved = []
        for r in rows:
            saved.append({
                'id': r['id'],
                'workout_name': r['workout_name'],
                'workout_description': r['workout_description'],
                'exercises': json.loads(r['exercises_json']),
                'total_duration': r['total_duration'],
                'difficulty': r['difficulty'],
                'created_date': r['created_date']
            })
            
        return jsonify({'success': True, 'saved_workouts': saved})
    except Exception as e:
        logger.exception("Failed to list saved workouts")
        return jsonify({'success': False, 'error': str(e)}), 500

def count_completed_exercises(data):
    """Exercises actually completed in a session payload.

    Both the guest and logged-in paths use this so the two agree; falls back to
    the length of the exercise list for older clients that omit the count.
    """
    try:
        n = int(data.get('completedExercises', 0))
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        n = len(data.get('exercises', []) or [])
    return max(0, n)

@app.route('/api/complete_session', methods=['POST'])
def api_complete_session():
    """Record a completed training session."""
    data = request.get_json()
    user_id = session.get('user_id', 'guest')
    exercises_count = count_completed_exercises(data)

    try:
        if user_id == 'guest':
            # Update session data for guest users
            total_duration = data.get('totalDuration', 0)
            session['weekly_minutes'] = session.get('weekly_minutes', 0) + total_duration
            today_str = datetime.now().strftime('%Y-%m-%d')
            # First recorded session starts the 4-week program clock
            session.setdefault('program_start', today_str)
            # Compute streak from consecutive days, same helper as the
            # logged-in path so the two can never drift apart.
            streak_dates = session.get('streak_dates', [])
            if today_str not in streak_dates:
                streak_dates.insert(0, today_str)
            settings = get_settings()
            session['streak'] = streak_from_dates(
                streak_dates,
                grace_days=int(settings.get('goals.streak_grace_days', 0)),
                rest_days=settings.get('goals.rest_days', []))
            session['streak_dates'] = streak_dates[:30]

            # Names feed the workout.variety setting on the next generation.
            names = [e.get('name') for e in (data.get('exercises') or [])
                     if isinstance(e, dict) and e.get('name')]
            session['recent_exercise_names'] = (names + session.get('recent_exercise_names', []))[:80]

            # Update domain progress using domainProgress data structure
            domain_progress = session.get('domain_progress', {})
            domain_progress_data = data.get('domainProgress', {})
            
            for domain, progress_data in domain_progress_data.items():
                if progress_data.get('exercises', 0) > 0:
                    current = domain_progress.get(domain, {'exercises': 0, 'minutes': 0})
                    if isinstance(current, (int, float)):
                        current = {'exercises': int(current), 'minutes': 0}
                    domain_progress[domain] = {
                        'exercises': current.get('exercises', 0) + progress_data.get('exercises', 0),
                        'minutes': current.get('minutes', 0) + progress_data.get('minutes', 0)
                    }
            
            session['domain_progress'] = domain_progress

            # Store recent sessions for guest users
            recent_sessions = session.get('recent_sessions', [])
            new_session = {
                'session_date': datetime.now().strftime('%Y-%m-%d'),
                'total_duration': total_duration,
                'session_type': data.get('sessionType', 'custom')
            }
            recent_sessions.insert(0, new_session)  # Add to beginning
            # Keep only last 10 sessions
            recent_sessions = recent_sessions[:10]
            session['recent_sessions'] = recent_sessions

            # Running totals — recent_sessions is capped at 10, so it can't be counted
            session['session_count'] = session.get('session_count', 0) + 1
            session['total_exercises'] = session.get('total_exercises', 0) + exercises_count

            return jsonify({'status': 'success', 'message': 'Session recorded for guest user'})

        # For logged-in users, save to database. session_date is set explicitly
        # to the server's LOCAL date — SQLite's CURRENT_DATE default is UTC,
        # which shifted evening sessions onto the wrong day for streaks and
        # week progression.
        # rpe is optional; only whole numbers 1-10 are stored.
        try:
            rpe = int(data.get('rpe'))
            rpe = rpe if 1 <= rpe <= 10 else None
        except (TypeError, ValueError):
            rpe = None

        conn = get_db_connection()
        conn.execute('''
            INSERT INTO user_sessions (user_id, session_date, exercises_completed, exercises_count, total_duration, session_type, rpe)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, datetime.now().strftime('%Y-%m-%d'),
              json.dumps(data.get('exercises', [])), exercises_count,
              data.get('totalDuration', 0), data.get('sessionType', 'custom'), rpe))

        # Update user progress
        update_user_progress(user_id, data, conn)

        conn.commit()
        conn.close()

        logger.info("Session recorded for user %s: %d exercises, %s min",
                    user_id, exercises_count, data.get('totalDuration', 0))
        return jsonify({'status': 'success', 'message': 'Session recorded successfully'})
    
    except Exception as e:
        logger.exception("Exception in api_complete_session")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/user_progress')
def api_user_progress():
    """Get user progress data for the progress page."""
    user_id = session.get('user_id', 'guest')
    progress_data = get_user_progress_data(user_id)
    return jsonify(progress_data)

@app.route('/api/activity-heatmap')
def api_activity_heatmap():
    """Minutes trained per day over the last ~12 weeks, keyed by date.

    Feeds the dashboard and progress heatmaps. Guests get whatever their
    cookie still holds (at most the last 10 sessions), which the heatmap
    renders honestly rather than pretending to a longer memory.
    """
    user_id = session.get('user_id', 'guest')
    days = {}
    if user_id == 'guest':
        for s_ in session.get('recent_sessions', []):
            key = str(s_.get('session_date', ''))[:10]
            if key:
                days[key] = days.get(key, 0) + (s_.get('total_duration') or 0)
    else:
        conn = get_db_connection()
        try:
            rows = conn.execute('''
                SELECT session_date, COALESCE(SUM(total_duration), 0)
                FROM user_sessions
                WHERE user_id = ? AND session_date >= date('now', '-84 days')
                GROUP BY session_date
            ''', (user_id,)).fetchall()
        finally:
            conn.close()
        days = {str(r[0])[:10]: r[1] for r in rows}
    return jsonify({'success': True, 'days': days})

@app.route('/api/debug')
def api_debug():
    """Debug endpoint to check data loading (only when FLASK_DEBUG=1)."""
    if os.environ.get('FLASK_DEBUG', '0') != '1':
        return jsonify({'error': 'Not available'}), 404  # noqa: also see __main__ default below
    return jsonify({
        'training_data_loaded': bool(training_data),
        'exercises_count': len(training_data.get('exercises', [])) if training_data else 0,
        'sample_exercise': training_data.get('exercises', [{}])[0] if training_data and training_data.get('exercises') else None
    })

@app.route('/api/rest-times')
def api_rest_times():
    """Get rest times for different categories and difficulty levels."""
    return jsonify({'rest_times': rest_times})

@app.route('/api/health')
def api_health():
    """Health check endpoint."""
    return jsonify({'status': 'ok', 'app': 'FitTrack', 'version': '3.0'})

@app.route('/sw.js')
def service_worker():
    """Serve the offline worker from the root.

    A service worker can only control paths at or below its own URL, so this
    cannot live under /static/ — from there it could only cache /static.
    """
    response = app.send_static_file('sw.js')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Service-Worker-Allowed'] = '/'
    return response

def goal_snapshot(rows, settings):
    """Progress against the weekly targets, from (date, minutes) pairs.

    Returns zeroed-out, disabled data when both targets are zero, which is how
    a user turns the goal display off entirely — the template checks `enabled`
    rather than needing a separate visibility switch.
    """
    today = datetime.now().date()
    sessions = minutes = 0
    for value, mins in rows:
        try:
            day = datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
        except (TypeError, ValueError):
            continue
        if 0 <= (today - day).days < 7:
            sessions += 1
            minutes += mins or 0

    try:
        target_sessions = int(settings.get('goals.weekly_sessions', 0))
        target_minutes = int(settings.get('goals.weekly_minutes', 0))
    except (TypeError, ValueError):
        target_sessions = target_minutes = 0

    return {
        'sessions': sessions,
        'minutes': round(minutes),
        'target_sessions': target_sessions,
        'target_minutes': target_minutes,
        'sessions_pct': min(100, round(sessions * 100 / target_sessions)) if target_sessions else 0,
        'minutes_pct': min(100, round(minutes * 100 / target_minutes)) if target_minutes else 0,
        'enabled': bool(target_sessions or target_minutes),
    }


def streak_from_dates(date_strings, grace_days=0, rest_days=()):
    """Consecutive-day training streak, counting back from today.

    Two settings soften the plain definition, because a streak that resets on
    the first missed day punishes exactly the people a habit tracker exists to
    help:

    ``rest_days``   weekdays the user planned not to train on. Those days never
                    count as a break, so a Mon/Wed/Fri routine with weekends
                    off keeps its streak through the weekend.
    ``grace_days``  additional unplanned days the streak survives.

    Pure function over date strings, so both the guest and logged-in paths can
    share it and it is testable without a database.
    """
    rest_weekdays = {str(d).lower() for d in (rest_days or ())}
    weekday_names = ('monday', 'tuesday', 'wednesday', 'thursday',
                     'friday', 'saturday', 'sunday')

    parsed = []
    for value in date_strings:
        try:
            parsed.append(datetime.strptime(str(value)[:10], '%Y-%m-%d').date())
        except (TypeError, ValueError):
            continue
    if not parsed:
        return 0

    parsed = sorted(set(parsed), reverse=True)
    streak = 0
    previous = datetime.now().date()

    for day in parsed:
        gap = (previous - day).days
        if gap < 0:
            continue  # a session dated in the future; ignore it
        # Days strictly between the two that were planned rest days are free.
        excused = sum(
            1 for step in range(1, gap)
            if weekday_names[(day + timedelta(days=step)).weekday()] in rest_weekdays
        )
        if gap - 1 <= grace_days + excused:
            streak += 1
            previous = day
        else:
            break
    return streak


def _weekly_buckets(rows):
    """Bucket (date_str, minutes) pairs into the last four 7-day windows, oldest first.

    Index 3 is the current week (today back 6 days), index 0 the window 21-27
    days ago. Sessions older than 28 days or with unparseable dates are ignored.
    """
    buckets = [0, 0, 0, 0]
    today = datetime.now().date()
    for ds, mins in rows:
        try:
            d = datetime.strptime(str(ds)[:10], '%Y-%m-%d').date()
        except (TypeError, ValueError):
            continue
        age = (today - d).days
        if 0 <= age < 28:
            buckets[3 - age // 7] += mins or 0
    return buckets

def get_user_progress_data(user_id):
    """Get detailed progress data for charts."""
    if user_id == 'guest':
        # For guest users, use session data
        domain_progress = session.get('domain_progress', {})
        weekly_minutes = session.get('weekly_minutes', 0)
        streak = session.get('streak', 0)
        recent_sessions = session.get('recent_sessions', [])
        current_week = get_current_week(user_id)

        def _guest_domain(key):
            dp = domain_progress.get(key, {})
            if isinstance(dp, (int, float)):
                return {'sessions': int(dp), 'minutes': 0, 'week': current_week, 'progress': min(int(dp) * 10, 100)}
            ex_count = dp.get('exercises', 0)
            mins = dp.get('minutes', 0)
            return {
                'sessions': ex_count,
                'minutes': mins,
                'week': current_week,
                'progress': min(mins, 100)
            }

        # Create domain-specific data structure with proper domain key mapping
        domains = {
            'strength_power': _guest_domain('strength_power'),
            'speed_mobility': _guest_domain('speed_mobility'),
            'endurance': _guest_domain('endurance'),
            'agility': _guest_domain('agility'),
            'cognition': _guest_domain('cognition')
        }
        
        total_sessions = session.get('session_count', len(recent_sessions))
        total_exercises = session.get('total_exercises', 0)
        if not total_exercises:
            # Fall back to the per-domain tallies for sessions recorded before
            # total_exercises was tracked.
            total_exercises = sum(d.get('exercises', 0) for d in domain_progress.values() if isinstance(d, dict))

        return {
            'total_exercises': total_exercises,
            'total_sessions': total_sessions,
            'total_minutes': weekly_minutes,
            'current_streak': streak,
            'avg_session_length': round(weekly_minutes / total_sessions) if total_sessions else 0,
            'weekly_progress': _weekly_buckets(
                (s.get('session_date'), s.get('total_duration', 0)) for s in recent_sessions
            ),
            'goal': goal_snapshot(
                [(s.get('session_date'), s.get('total_duration', 0)) for s in recent_sessions],
                get_settings()),
            'recent_sessions': recent_sessions,
            'strength': domains['strength_power'],
            'speed_mobility': domains['speed_mobility'],
            'endurance': domains['endurance'],
            'agility': domains['agility'],
            'cognitive': domains['cognition']
        }

    # For logged-in users, get from database
    current_week = get_current_week(user_id)
    conn = get_db_connection()

    # Get recent sessions. The list length is a setting: a short history keeps
    # the dashboard light, a long one is a training log.
    try:
        history_limit = int(get_settings().get('progress.history_limit', 20))
    except (TypeError, ValueError):
        history_limit = 20
    sessions = conn.execute('''
        SELECT session_date, total_duration, session_type, exercises_count, rpe
        FROM user_sessions
        WHERE user_id = ?
        ORDER BY session_date DESC, id DESC
        LIMIT ?
    ''', (user_id, max(1, min(history_limit, 500)))).fetchall()

    # Get domain progress
    domain_progress = {}
    domains_data = conn.execute('''
        SELECT domain, sessions_completed, total_minutes 
        FROM user_progress 
        WHERE user_id = ?
    ''', (user_id,)).fetchall()

    for domain in domains_data:
        domain_progress[domain['domain']] = {
            'sessions': domain['sessions_completed'],
            'minutes': domain['total_minutes'],
            'week': current_week,
            'progress': min(domain['total_minutes'], 100)  # Progress based on minutes
        }

    # Get total stats from actual session records (not domain exercise counts)
    total_row = conn.execute('''
        SELECT COUNT(*) as cnt,
               COALESCE(SUM(total_duration), 0) as mins,
               COALESCE(SUM(exercises_count), 0) as exercises
        FROM user_sessions WHERE user_id = ?
    ''', (user_id,)).fetchone()
    total_sessions = total_row['cnt']
    total_minutes = total_row['mins']
    # Real exercise count. user_progress.sessions_completed counts sessions that
    # touched a domain, not exercises, so it can't be summed for this.
    total_exercises = total_row['exercises']
    
    # Create domain structure with defaults
    _empty = {'sessions': 0, 'minutes': 0, 'week': current_week, 'progress': 0}
    domains = {
        'strength_power': domain_progress.get('strength_power', dict(_empty)),
        'speed_mobility': domain_progress.get('speed_mobility', dict(_empty)),
        'endurance': domain_progress.get('endurance', dict(_empty)),
        'agility': domain_progress.get('agility', dict(_empty)),
        'cognition': domain_progress.get('cognition', dict(_empty))
    }

    # Compute streak from consecutive session DATES. Query distinct dates rather
    # than reusing `sessions`: that list is capped at 10 rows, and several
    # sessions on one day are still a single day of the streak.
    streak_rows = conn.execute('''
        SELECT DISTINCT session_date
        FROM user_sessions
        WHERE user_id = ?
        ORDER BY session_date DESC
        LIMIT 366
    ''', (user_id,)).fetchall()

    settings = get_settings()
    streak = streak_from_dates(
        [row['session_date'] for row in streak_rows],
        grace_days=int(settings.get('goals.streak_grace_days', 0)),
        rest_days=settings.get('goals.rest_days', []))

    avg_length = round(total_minutes / total_sessions) if total_sessions > 0 else 0

    # Minutes per day over the last 4 weeks for the weekly activity chart
    week_rows = conn.execute('''
        SELECT session_date, COALESCE(SUM(total_duration), 0)
        FROM user_sessions
        WHERE user_id = ?
        GROUP BY session_date
    ''', (user_id,)).fetchall()
    weekly_progress = _weekly_buckets((r[0], r[1]) for r in week_rows)

    conn.close()

    return {
        'total_sessions': total_sessions,
        'total_exercises': total_exercises,
        'total_minutes': total_minutes,
        'current_streak': streak,
        'avg_session_length': avg_length,
        'weekly_progress': weekly_progress,
        'goal': goal_snapshot([(r[0], r[1]) for r in week_rows], settings),
        'recent_sessions': [dict(s) for s in sessions],
        'strength': domains['strength_power'],
        'speed_mobility': domains['speed_mobility'],
        'endurance': domains['endurance'],
        'agility': domains['agility'],
        'cognitive': domains['cognition']
    }

def update_user_progress(user_id, session_data, conn):
    """Update user progress after completing a session. Caller owns the connection."""
    # Update or insert domain progress based on domainProgress data
    domain_progress = session_data.get('domainProgress', {})

    for domain, progress_data in domain_progress.items():
        if progress_data.get('exercises', 0) > 0:
            existing = conn.execute('''
                SELECT id FROM user_progress WHERE user_id = ? AND domain = ?
            ''', (user_id, domain)).fetchone()

            if existing:
                conn.execute('''
                    UPDATE user_progress
                    SET sessions_completed = sessions_completed + 1,
                        total_minutes = total_minutes + ?,
                        last_session_date = CURRENT_DATE
                    WHERE user_id = ? AND domain = ?
                ''', (progress_data.get('minutes', 0), user_id, domain))
            else:
                conn.execute('''
                    INSERT INTO user_progress (user_id, domain, sessions_completed, total_minutes, last_session_date)
                    VALUES (?, ?, 1, ?, CURRENT_DATE)
                ''', (user_id, domain, progress_data.get('minutes', 0)))

# ── Settings ─────────────────────────────────────────────────────────

def csrf_protect_json():
    """CSRF check for JSON endpoints that change account state.

    The browser sends the token as X-CSRF-Token; app.js attaches it to every
    same-origin fetch. Read-only endpoints are exempt, as are the older
    workout endpoints, which predate this and carry no account-level risk.
    """
    validate_csrf_token()


@app.route('/settings')
def settings_page():
    """The settings screen. All controls are rendered from the registry."""
    return render_template(
        'settings.html',
        presets=reg.PRESETS,
        group_icons=reg.GROUP_ICONS,
        groups=reg.GROUP_ORDER,
        registry=reg.SETTINGS,
        settings_units={s.key: s.unit for s in reg.SETTINGS},
    )


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    """Current values plus the schema needed to render them."""
    return jsonify({'success': True, **reg.client_payload(get_settings())})


@app.route('/api/settings', methods=['POST'])
def api_patch_settings():
    """Apply a batch of {key, value} changes.

    Accepts either {"changes": [{"key": ..., "value": ...}]} or a flat
    {"key": value} object, because the settings page posts single controls and
    the assistant posts batches.
    """
    csrf_protect_json()
    data = request.get_json(silent=True) or {}
    changes = data.get('changes')
    if changes is None:
        changes = [{'key': k, 'value': v} for k, v in data.items() if k != 'changes']

    accepted, rejected, error = apply_setting_changes(changes)
    if error:
        return jsonify({'success': False, 'error': error, 'rejected': rejected}), 400
    return jsonify({
        'success': True,
        'changed': accepted,
        'rejected': rejected,
        'summary': reg.describe_changes(accepted),
        'values': get_settings(),
    })


@app.route('/api/settings/reset', methods=['POST'])
def api_reset_settings():
    """Restore defaults — for one group, or everything."""
    csrf_protect_json()
    data = request.get_json(silent=True) or {}
    group = data.get('group')

    current = dict(get_settings())
    restored = []
    for setting in reg.SETTINGS:
        if group and setting.group != group:
            continue
        default = list(setting.default) if isinstance(setting.default, list) else setting.default
        if current.get(setting.key) != default:
            restored.append(setting.key)
            current[setting.key] = default

    ok, error = write_settings(current)
    if not ok:
        return jsonify({'success': False, 'error': error}), 400
    logger.info("Settings reset (%s): %d values", group or 'all', len(restored))
    return jsonify({'success': True, 'reset': restored, 'values': get_settings()})


@app.route('/api/settings/preset', methods=['POST'])
def api_apply_preset():
    """Apply a named preset — a whole configuration in one tap."""
    csrf_protect_json()
    data = request.get_json(silent=True) or {}
    name = str(data.get('preset', '')).strip()
    preset = reg.PRESETS.get(name)
    if not preset:
        return jsonify({'success': False, 'error': f'No preset named "{name}".'}), 400

    accepted, rejected, error = apply_setting_changes(
        [{'key': k, 'value': v} for k, v in preset['values'].items()])
    if error:
        return jsonify({'success': False, 'error': error}), 400
    logger.info("Applied preset %s: %d changes", name, len(accepted))
    return jsonify({
        'success': True,
        'preset': name,
        'changed': accepted,
        'rejected': rejected,
        'summary': reg.describe_changes(accepted),
        'values': get_settings(),
    })


# ── The settings assistant ───────────────────────────────────────────
#
# Same truth boundary as workout generation: the model routes, it does not
# author. Its entire output is a list of (key, value) pairs drawn from a
# candidate list; every pair is validated against the registry, and the
# sentence the user reads is built by reg.describe_changes from values that
# passed. There is deliberately no free-text field in the schema.
#
# The deterministic matcher runs first. When it is confident, the model is
# never called — that makes the common requests instant and means the whole
# feature still works on a machine with no model installed.

SETTINGS_INTENT_SCHEMA = {
    "name": "settings_intent",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"}
                    },
                    "required": ["key", "value"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["changes"],
        "additionalProperties": False
    }
}

# Above this score the deterministic matcher is trusted on its own.
DIRECT_MATCH_CONFIDENCE = 12.0
SETTINGS_SHORTLIST = 16


def shortlist_settings(query, limit=SETTINGS_SHORTLIST):
    """The settings most likely to be meant, best first.

    The model is shown this shortlist rather than all ~80 settings. Small
    models degrade badly on long candidate lists, and the deterministic scorer
    is good enough at narrowing that the model only has to make the final call.
    """
    scored = [(reg.score_setting(s, query), s) for s in reg.SETTINGS]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = [s for score, s in scored if score > 0][:limit]
    if len(top) < 6:
        # Nothing matched strongly — offer the always-visible settings so the
        # model has something coherent to choose from instead of guessing keys.
        for setting in reg.SETTINGS:
            if setting.tier == 'simple' and setting not in top:
                top.append(setting)
            if len(top) >= 8:
                break
    return top


def describe_setting_for_model(setting, current_value):
    """One compact line describing a setting and what it will accept."""
    if setting.type == 'bool':
        allowed = 'on | off'
    elif setting.type in ('enum', 'multi'):
        allowed = ' | '.join(setting.choice_values)
        if setting.type == 'multi':
            allowed = f'comma-separated from: {allowed}'
    elif setting.type in ('int', 'float'):
        allowed = f'number {setting.minimum} to {setting.maximum}{setting.unit}'
    else:
        allowed = 'free text'
    return (f'- {setting.key} — {setting.label} ({setting.group}). '
            f'Accepts: {allowed}. Currently: {setting.label_for(current_value)}')


def settings_intent_via_llm(query, settings):
    """Ask the local model which settings a request refers to.

    Returns a list of {key, value} dicts, or None if the model was unavailable
    or never produced a usable answer. Nothing here reaches the user directly —
    the caller validates every pair against the registry.
    """
    config = ai_config(settings)
    if not config['enabled']:
        return None

    candidates = shortlist_settings(query)
    if not candidates:
        return None

    candidate_lines = '\n'.join(
        describe_setting_for_model(s, settings.get(s.key, s.default)) for s in candidates)

    system_content = (
        'You map a person\'s request onto application settings. Respond ONLY with '
        'a JSON object of the form {"changes": [{"key": "<setting key>", '
        '"value": "<new value>"}]}. Use only keys that appear in the settings '
        'list, copied exactly. Values must be one of the accepted values shown '
        'for that key. If the request matches no setting, return an empty '
        'changes list. Do not write any prose, explanation, or extra fields.'
    )
    user_content = f"""Request: {query}

Settings you may change:
{candidate_lines}

Example response:
{{"changes": [{{"key": "appearance.theme", "value": "dark"}}]}}"""

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    for attempt in range(LLM_RETRIES + 1):
        content = _post_chat_completion(messages, schema=SETTINGS_INTENT_SCHEMA, config=config)
        if content is None:
            return None  # transport failure — retrying will not help

        content = _strip_code_fence(content)
        try:
            parsed = json.loads(content)
        except ValueError:
            error = 'Response was not valid JSON.'
            parsed = None

        if isinstance(parsed, dict):
            changes = parsed.get('changes')
            if isinstance(changes, list):
                accepted, rejected = reg.apply_changes(settings, changes)
                if accepted or not rejected:
                    # An empty-but-clean answer is a real answer: the model is
                    # allowed to say "nothing here matches".
                    return changes
                error = rejected[0]['error']
            else:
                error = 'The "changes" field must be a list.'
        elif parsed is not None:
            error = 'Response was not a JSON object.'

        logger.warning("Settings intent rejected (attempt %d): %s", attempt + 1, error)
        if attempt < LLM_RETRIES:
            messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content":
                    f'Your previous response was rejected: {error} '
                    'Respond again with corrected JSON only, using only keys from '
                    'the settings list.'},
            ]
    return None


def resolve_settings_request(query, settings):
    """Turn a plain-language request into proposed changes.

    Returns (changes, engine). `engine` is 'rules' or 'ai' and is reported to
    the user, so it is always clear which one answered.
    """
    scored = max((reg.score_setting(s, query) for s in reg.SETTINGS), default=0.0)
    direct = reg.match_intent(query, current=settings)

    if direct and scored >= DIRECT_MATCH_CONFIDENCE:
        return direct, 'rules'

    if settings.get('ai.enabled', True):
        via_model = settings_intent_via_llm(query, settings)
        if via_model is not None:
            return via_model, 'ai'

    return direct, 'rules'


@app.route('/api/settings/ask', methods=['POST'])
def api_settings_ask():
    """Describe a change in plain language; get back the settings that do it.

    Nothing is applied unless the caller asks for it or ai.auto_apply is on, so
    the default experience is: say what you want, see exactly which switches
    move, then confirm.
    """
    csrf_protect_json()
    settings = get_settings()
    if not settings.get('ai.allow_settings_changes', True):
        return jsonify({
            'success': False,
            'error': 'Changing settings by request is turned off. You can re-enable it '
                     'under Local AI → "Let it change settings".'
        }), 403

    data = request.get_json(silent=True) or {}
    query = str(data.get('query', '')).strip()
    if not query:
        return jsonify({'success': False, 'error': 'Say what you would like to change.'}), 400
    if len(query) > 500:
        query = query[:500]

    changes, engine = resolve_settings_request(query, settings)
    proposals, rejected = reg.apply_changes(settings, changes)

    if not proposals:
        already = bool(changes) and not rejected
        return jsonify({
            'success': True,
            'applied': False,
            'engine': engine,
            'proposals': [],
            'rejected': rejected,
            'summary': ('Those settings are already set that way.' if already else
                        'Nothing in the settings matches that. Try naming what you want '
                        'to change — "dark mode", "longer rests", "no sound".'),
        })

    should_apply = bool(data.get('apply')) or bool(settings.get('ai.auto_apply'))
    if should_apply:
        accepted, rejected_now, error = apply_setting_changes(changes)
        if error:
            return jsonify({'success': False, 'error': error}), 400
        logger.info("Assistant applied %d settings via %s: %s",
                    len(accepted), engine, [c['key'] for c in accepted])
        return jsonify({
            'success': True,
            'applied': True,
            'engine': engine,
            'proposals': accepted,
            'rejected': rejected_now,
            'summary': reg.describe_changes(accepted),
            'values': get_settings(),
        })

    return jsonify({
        'success': True,
        'applied': False,
        'engine': engine,
        'proposals': proposals,
        'rejected': rejected,
        'summary': reg.describe_changes(proposals, applied=False),
    })


@app.route('/api/settings/search')
def api_settings_search():
    """Rank settings against a query — powers the settings search and Ctrl+K.

    Uses the same scorer as the assistant, so typing into the search box and
    asking the assistant surface the same settings for the same words.
    """
    query = str(request.args.get('q', '')).strip()
    settings = get_settings()
    if not query:
        return jsonify({'success': True, 'results': []})

    scored = [(reg.score_setting(s, query), s) for s in reg.SETTINGS]
    results = [{
        'key': s.key, 'label': s.label, 'group': s.group, 'tier': s.tier,
        'help': s.help, 'value_label': s.label_for(settings.get(s.key, s.default)),
        'score': round(score, 1),
    } for score, s in sorted(scored, key=lambda p: -p[0]) if score > 0][:12]
    return jsonify({'success': True, 'results': results})


# ── Data portability ─────────────────────────────────────────────────

@app.route('/api/data/export')
def api_export_data():
    """Everything this account holds, in one file.

    JSON is complete and round-trips through the import endpoint; CSV is the
    session history only, for spreadsheets. Nothing is filtered by the
    retention setting — an export is the whole record.
    """
    user_id = session.get('user_id', 'guest')
    settings = get_settings()
    fmt = str(request.args.get('format') or settings.get('data.export_format', 'json')).lower()
    stamp = datetime.now().strftime('%Y%m%d')

    if user_id == 'guest':
        sessions = session.get('recent_sessions', [])
        saved, logs, custom = [], [], []
    else:
        conn = get_db_connection()
        try:
            sessions = [dict(r) for r in conn.execute(
                'SELECT session_date, session_type, total_duration, exercises_count, '
                'exercises_completed, rpe, notes FROM user_sessions '
                'WHERE user_id = ? ORDER BY session_date DESC', (user_id,)).fetchall()]
            saved = [dict(r) for r in conn.execute(
                'SELECT workout_name, workout_description, exercises_json, total_duration, '
                'difficulty, created_date FROM saved_workouts WHERE user_id = ?',
                (str(user_id),)).fetchall()]
            logs = [dict(r) for r in conn.execute(
                'SELECT log_date, exercise_name, category, set_number, reps, weight, '
                'weight_unit FROM exercise_logs WHERE user_id = ? ORDER BY log_date DESC',
                (str(user_id),)).fetchall()]
            custom = [dict(r) for r in conn.execute(
                'SELECT category, exercise_name, duration_minutes, difficulty_level, '
                'equipment FROM exercises').fetchall()]
        finally:
            conn.close()

    if fmt == 'csv':
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['session_date', 'session_type', 'total_duration',
                         'exercises_count', 'rpe'])
        for row in sessions:
            writer.writerow([row.get('session_date', ''), row.get('session_type', ''),
                             row.get('total_duration', ''), row.get('exercises_count', ''),
                             row.get('rpe', '')])
        return Response(
            buffer.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=fittrack-{stamp}.csv'})

    payload = {
        'exported': datetime.now().isoformat(timespec='seconds'),
        'app': 'FitTrack',
        'format_version': 1,
        'account': 'guest' if user_id == 'guest' else session.get('username', str(user_id)),
        'settings': load_raw_settings(settings_owner()),
        'sessions': sessions,
        'saved_workouts': saved,
        'exercise_logs': logs,
        'custom_exercises': custom,
    }
    return Response(
        json.dumps(payload, indent=2, default=str), mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename=fittrack-{stamp}.json'})


@app.route('/api/settings/import', methods=['POST'])
def api_import_settings():
    """Restore settings from an exported file.

    Only the settings block is read, and every value still goes through the
    registry — an export from a newer version with settings this build does not
    have imports cleanly, skipping what it cannot place.
    """
    csrf_protect_json()
    data = request.get_json(silent=True) or {}
    incoming = data.get('settings')
    if not isinstance(incoming, dict):
        return jsonify({'success': False,
                        'error': 'That file has no settings block in it.'}), 400

    accepted, rejected, error = apply_setting_changes(
        [{'key': k, 'value': v} for k, v in incoming.items()])
    if error:
        return jsonify({'success': False, 'error': error}), 400
    logger.info("Imported %d settings (%d skipped)", len(accepted), len(rejected))
    return jsonify({
        'success': True,
        'changed': accepted,
        'skipped': [r['key'] for r in rejected],
        'summary': reg.describe_changes(accepted),
        'values': get_settings(),
    })


# ── Favourites and set logging ───────────────────────────────────────

@app.route('/api/exercises/favorites', methods=['GET'])
def api_list_favorites():
    """Names of the current user's favourited exercises."""
    user_id = session.get('user_id', 'guest')
    if user_id == 'guest':
        return jsonify({'success': True, 'favorites': session.get('favorites', [])})

    conn = get_db_connection()
    try:
        rows = conn.execute(
            'SELECT exercise_name, category FROM favorite_exercises WHERE user_id = ?',
            (str(user_id),)).fetchall()
    finally:
        conn.close()
    return jsonify({'success': True,
                    'favorites': [f"{r['category']}|{r['exercise_name']}" for r in rows]})


@app.route('/api/exercises/favorite', methods=['POST'])
def api_toggle_favorite():
    """Add or remove a favourite. Works for guests too, in the session cookie."""
    csrf_protect_json()
    data = request.get_json(silent=True) or {}
    name = str(data.get('name', '')).strip()
    category = str(data.get('category', '')).strip()
    if not name or not category:
        return jsonify({'success': False, 'error': 'Exercise name and category are required'}), 400

    token = f'{category}|{name}'
    user_id = session.get('user_id', 'guest')

    if user_id == 'guest':
        favorites = list(session.get('favorites', []))
        if token in favorites:
            favorites.remove(token)
            state = False
        else:
            favorites.append(token)
            state = True
        session['favorites'] = favorites[:200]
        session.modified = True
        return jsonify({'success': True, 'favorited': state})

    conn = get_db_connection()
    try:
        existing = conn.execute(
            'SELECT 1 FROM favorite_exercises WHERE user_id = ? AND exercise_name = ? '
            'AND category = ?', (str(user_id), name, category)).fetchone()
        if existing:
            conn.execute('DELETE FROM favorite_exercises WHERE user_id = ? AND '
                         'exercise_name = ? AND category = ?', (str(user_id), name, category))
            state = False
        else:
            conn.execute('INSERT INTO favorite_exercises (user_id, exercise_name, category) '
                         'VALUES (?, ?, ?)', (str(user_id), name, category))
            state = True
        conn.commit()
    finally:
        conn.close()
    return jsonify({'success': True, 'favorited': state})


@app.route('/api/logs', methods=['POST'])
def api_save_exercise_logs():
    """Record set-by-set numbers from a session.

    Only reached when session.tracking_mode asks for more than a timer. Weight
    is stored with the unit it was entered in, so changing units.weight later
    never rewrites what was actually lifted.
    """
    csrf_protect_json()
    user_id = session.get('user_id', 'guest')
    data = request.get_json(silent=True) or {}
    entries = data.get('logs') or []
    if not isinstance(entries, list):
        return jsonify({'success': False, 'error': 'logs must be a list'}), 400

    if user_id == 'guest':
        # Guests keep the last few sessions' logs in the cookie; anything more
        # needs an account, and the response says so rather than failing quietly.
        stored = session.get('exercise_logs', [])
        session['exercise_logs'] = (entries + stored)[:60]
        session.modified = True
        return jsonify({'success': True, 'saved': len(entries), 'persisted': False,
                        'notice': 'Saved to this browser session. Log in to keep set '
                                  'history permanently.'})

    unit = get_settings().get('units.weight', 'kg')
    today = datetime.now().strftime('%Y-%m-%d')
    workout_id = str(data.get('workout_id', '') or '')[:64]

    rows = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get('name'):
            continue
        try:
            reps = int(entry.get('reps') or 0) or None
        except (TypeError, ValueError):
            reps = None
        try:
            weight = float(entry.get('weight') or 0) or None
        except (TypeError, ValueError):
            weight = None
        try:
            set_number = max(1, int(entry.get('set') or 1))
        except (TypeError, ValueError):
            set_number = 1
        rows.append((str(user_id), today, workout_id, str(entry['name'])[:120],
                     str(entry.get('category', ''))[:60], set_number, reps, weight, unit))

    if rows:
        conn = get_db_connection()
        try:
            conn.executemany('''
                INSERT INTO exercise_logs (user_id, log_date, workout_id, exercise_name,
                                           category, set_number, reps, weight, weight_unit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', rows)
            conn.commit()
        finally:
            conn.close()
    logger.info("Recorded %d set logs for user %s", len(rows), user_id)
    return jsonify({'success': True, 'saved': len(rows), 'persisted': True})


@app.route('/api/logs/records')
def api_personal_records():
    """Best set per exercise — the closest thing this app has to a PR board."""
    user_id = session.get('user_id', 'guest')
    if user_id == 'guest':
        return jsonify({'success': True, 'records': [], 'persisted': False})

    conn = get_db_connection()
    try:
        rows = conn.execute('''
            SELECT exercise_name,
                   MAX(COALESCE(weight, 0))  AS best_weight,
                   MAX(COALESCE(reps, 0))    AS best_reps,
                   COUNT(*)                  AS sets_logged,
                   MAX(log_date)             AS last_date,
                   weight_unit
            FROM exercise_logs
            WHERE user_id = ?
            GROUP BY exercise_name
            ORDER BY last_date DESC
        ''', (str(user_id),)).fetchall()
    finally:
        conn.close()
    return jsonify({'success': True, 'persisted': True,
                    'records': [dict(r) for r in rows]})


# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login route."""
    if request.method == 'POST':
        validate_csrf_token()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        conn = get_db_connection()
        user = conn.execute('''
            SELECT id, username, password_hash 
            FROM users 
            WHERE username = ? OR email = ?
        ''', (username, username)).fetchone()
        conn.close()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            logger.info("User %s logged in", user['username'])
            flash('Login successful!', 'success')
            return redirect(url_for('index'))

        logger.warning("Failed login attempt for username/email %r", username)
        flash('Invalid username or password', 'error')

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """User registration route."""
    if request.method == 'POST':
        validate_csrf_token()
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        # Server-side validation
        if not username or len(username) < 3:
            flash('Username must be at least 3 characters', 'error')
            return render_template('register.html')
        if not email or '@' not in email:
            flash('Please enter a valid email address', 'error')
            return render_template('register.html')
        if len(password) < 6:
            flash('Password must be at least 6 characters', 'error')
            return render_template('register.html')
        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('register.html')

        conn = get_db_connection()

        # Create new user — UNIQUE constraint handles concurrency
        password_hash = generate_password_hash(password)
        try:
            cursor = conn.execute('''
                INSERT INTO users (username, email, password_hash)
                VALUES (?, ?, ?)
            ''', (username, email, password_hash))
            conn.commit()
            user_id = cursor.lastrowid
            conn.close()

            # Set session
            session['user_id'] = user_id
            session['username'] = username

            logger.info("New user registered: %s (id=%s)", username, user_id)
            flash('Registration successful!', 'success')
            return redirect(url_for('index'))
        except sqlite3.IntegrityError:
            conn.close()
            flash('Username or email already exists', 'error')
            return render_template('register.html')
        except Exception:
            conn.close()
            logger.exception("Registration failed for username %r", username)
            flash('Registration failed. Please try again.', 'error')
            return render_template('register.html')

    return render_template('register.html')

@app.route('/logout')
def logout():
    """User logout route."""
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('index'))

# Initialize database and populate exercises on import
# (works with both `python app.py` and `flask run` / gunicorn)
with app.app_context():
    init_db()
    populate_exercises_db()

if __name__ == '__main__':
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', 5000))
    logger.info("Starting FitTrack on http://%s:%s (log level %s)", host, port, _log_level)
    app.run(
        debug=os.environ.get('FLASK_DEBUG', '0') == '1',
        host=host,
        port=port
    )
