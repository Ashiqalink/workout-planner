from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, abort
import pandas as pd
import sqlite3
import json
import os
from datetime import datetime, timedelta
import secrets
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
if os.environ.get('SECRET_KEY'):
    app.secret_key = os.environ['SECRET_KEY']
else:
    app.secret_key = secrets.token_hex(16)
    print("WARNING: Using random secret key — sessions will not survive restarts. Set SECRET_KEY env var.")

# CSRF helpers
def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

def validate_csrf_token():
    token = session.get('_csrf_token')
    form_token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
    if not token or token != form_token:
        abort(403)

app.jinja_env.globals['csrf_token'] = generate_csrf_token

# Configuration
DATABASE = 'training_app.db'
UPLOAD_FOLDER = 'data'

# Ensure data directory exists
if not os.path.exists('data'):
    os.makedirs('data')

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

    # Migration: add exercises_count to databases created before it existed
    cols = [r['name'] for r in conn.execute('PRAGMA table_info(user_sessions)').fetchall()]
    if 'exercises_count' not in cols:
        conn.execute('ALTER TABLE user_sessions ADD COLUMN exercises_count INTEGER DEFAULT 0')

    conn.commit()
    conn.close()

def load_exercise_data():
    """Load exercise data from CSV files."""
    try:
        # Load comprehensive training matrix
        print("Loading comprehensive_training_matrix.csv...")
        exercises_df = pd.read_csv('data/comprehensive_training_matrix.csv')
        print(f"Loaded {len(exercises_df)} exercises")

        # Load 4-week program
        print("Loading complete_4week_program.csv...")
        program_df = pd.read_csv('data/complete_4week_program.csv')
        print(f"Loaded {len(program_df)} program entries")

        # Load student training program
        print("Loading student_training_program.csv...")
        student_program_df = pd.read_csv('data/student_training_program.csv')
        print(f"Loaded {len(student_program_df)} student program entries")

        return {
            'exercises': exercises_df.to_dict('records'),
            'program': program_df.to_dict('records'),
            'student_program': student_program_df.to_dict('records')
        }
    except FileNotFoundError as e:
        print(f"CSV file not found: {e}")
        return {
            'exercises': [],
            'program': [],
            'student_program': []
        }
    except Exception as e:
        print(f"Error loading CSV files: {e}")
        return {
            'exercises': [],
            'program': [],
            'student_program': []
        }

def populate_exercises_db():
    """Populate exercises table from CSV data if empty."""
    conn = get_db_connection()

    # Check if table exists and has rows using PRAGMA (safe, no exception needed)
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='exercises'"
    ).fetchone()
    if table_exists:
        count = conn.execute('SELECT COUNT(*) FROM exercises').fetchone()[0]
        if count > 0:
            conn.close()
            return

    # Only clear if table exists (don't wipe custom exercises if table is missing)
    if table_exists:
        conn.execute('DELETE FROM exercises')

    # Load exercise data
    data = load_exercise_data()

    for exercise in data['exercises']:
        conn.execute('''
            INSERT OR IGNORE INTO exercises (category, exercise_name, duration_minutes, 
                                 primary_benefit, secondary_benefit, difficulty_level, instructions)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            exercise['category'],
            exercise['name'],
            exercise['duration'],
            exercise['description'],
            exercise['target_muscles'],
            exercise['difficulty'],
            exercise.get('instructions', '')
        ))

    conn.commit()
    conn.close()

def get_all_exercises_from_db():
    """Retrieve exercises from the SQLite database."""
    conn = get_db_connection()
    try:
        rows = conn.execute('''
            SELECT category, exercise_name, duration_minutes, primary_benefit, secondary_benefit, difficulty_level, instructions
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
                'instructions': r['instructions'] or ''
            })
        return exercises
    finally:
        conn.close()

# Load data at startup
training_data = load_exercise_data()

def load_rest_times():
    """Load rest times from CSV file."""
    try:
        print("Loading Category-Beginner-Intermediate-Advanced.csv...")
        rest_df = pd.read_csv('data/Category-Beginner-Intermediate-Advanced.csv')
        print(f"Loaded {len(rest_df)} rest time categories")
        
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
        print(f"Rest times CSV file not found: {e}")
        return {}
    except Exception as e:
        print(f"Error loading rest times CSV: {e}")
        return {}

# Load rest times at startup
rest_times = load_rest_times()

def get_today_session(user_id):
    """Get the recommended training program session for today."""
    try:
        current_week = 1
        if user_id != 'guest':
            conn = get_db_connection()
            progress = conn.execute('''
                SELECT current_week 
                FROM user_progress 
                WHERE user_id = ? 
                LIMIT 1
            ''', (user_id,)).fetchone()
            if progress:
                current_week = progress['current_week']
            conn.close()
        else:
            current_week = session.get('current_week', 1)
            
        current_day = datetime.now().strftime('%A')
        
        if training_data and 'program' in training_data:
            for entry in training_data['program']:
                if int(entry.get('Week', 1)) == int(current_week) and str(entry.get('Day', '')).strip().lower() == current_day.lower():
                    return {
                        'name': entry.get('Type'),
                        'description': entry.get('Description') or f"Focus: {entry.get('Focus')}",
                        'duration': entry.get('Duration'),
                        'difficulty': entry.get('Intensity'),
                        'focus': entry.get('Focus') or ''
                    }
    except Exception as e:
        print(f"Error getting today's session: {e}")
    return None

@app.route('/')
def index():
    """Main dashboard route."""
    # Seed guest session defaults. setdefault rather than assignment: a session
    # can already hold progress from /api/complete_session if the user reached
    # the planner or a saved workout before ever loading the dashboard.
    session.setdefault('user_id', 'guest')
    session.setdefault('current_week', 1)
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

    return render_template('index.html',
                         progress_data=progress_data,
                         recent_sessions=recent_sessions,
                         today_session=today_session)

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
    user_id = session.get('user_id')
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
        return jsonify({'exercises': [], 'error': str(e)}), 500

import urllib.request
import urllib.error
import re

LM_STUDIO_API_URL = os.environ.get('LM_STUDIO_API_URL', 'http://localhost:1234/v1')

_cached_model = None

def get_loaded_model():
    """Query LM Studio to find the currently loaded model name (cached)."""
    global _cached_model
    if _cached_model is not None:
        return _cached_model
    try:
        req = urllib.request.Request(f"{LM_STUDIO_API_URL}/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            models = json.loads(resp.read().decode('utf-8'))
            data = models.get('data', [])
            if data:
                _cached_model = data[0].get('id', 'lm-studio')
                return _cached_model
    except Exception:
        pass
    _cached_model = 'lm-studio'
    return _cached_model

def generate_workout_via_llm(domains, duration, difficulty, focus, candidates, goal=None):
    url = f"{LM_STUDIO_API_URL}/chat/completions"
    
    system_content = "You are a professional fitness coach assistant. You must design a cohesive workout session using the provided candidate exercises. Respond ONLY with a raw JSON object containing the schema requested. No markdown format."
    
    user_content = f"""Design a workout session using the candidate exercises below.
Target Duration: {duration} minutes
Difficulty: {difficulty}
Focus Area: {focus if focus else 'None'}
Selected Domains: {', '.join(domains)}
Custom Goal/Instructions: {goal if goal else 'None'}

Candidate Exercises:
{json.dumps(candidates, indent=2)}

Rules:
1. Select exercises from the Candidate Exercises that match the target domains.
2. The total sum of the selected exercise durations MUST be close to {duration} minutes.
3. You can adjust the duration (in minutes) of selected exercises to fit the target duration.
4. If there are not enough candidates, you may generate highly related new exercises that fit the target domains, using the same JSON format.
5. Provide a short 2-3 sentence paragraph under the "explanation" key explaining why this workout is designed this way, what it targets, and how it helps the user achieve their specific Custom Goal or focus area.
6. Respond strictly with JSON. Do not wrap in ```json or ``` blocks.

Expected JSON output format:
{{
  "exercises": [
    {{
      "name": "Exercise Name",
      "category": "Category Name",
      "duration": 1.0,
      "difficulty": "beginner",
      "description": "Short description of the exercise.",
      "target_muscles": "Target muscles."
    }}
  ],
  "total_duration": 15,
  "explanation": "This workout focuses on lower back stability and core strength..."
}}"""

    payload = {
        "model": get_loaded_model(),
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content}
        ],
        "temperature": 0.3,
        "enable_thinking": False
    }
    
    try:
        req = urllib.request.Request(url, method="POST")
        req.add_header("Content-Type", "application/json")
        
        data_bytes = json.dumps(payload).encode('utf-8')
        
        # 10 second timeout
        with urllib.request.urlopen(req, data_bytes, timeout=10) as response:
            res_body = response.read().decode('utf-8')
            res_json = json.loads(res_body)
            
            choices = res_json.get("choices", [])
            if not choices:
                return None
                
            content = choices[0].get("message", {}).get("content", "").strip()
            
            # Clean up potential markdown blocks
            if content.startswith("```"):
                content = re.sub(r'^```(?:json)?\s*', '', content)
                content = re.sub(r'\s*```$', '', content)
                content = content.strip()
                
            workout_data = json.loads(content)
            
            if "exercises" in workout_data and isinstance(workout_data["exercises"], list):
                total_time = 0
                for ex in workout_data["exercises"]:
                    ex["duration"] = float(ex.get("duration", 0.5))
                    total_time += ex["duration"]
                
                workout_data["total_duration"] = round(total_time, 1)
                workout_data["explanation"] = workout_data.get("explanation", "Custom workout designed to fit your goals.")
                workout_data["success"] = True
                return workout_data
                
    except Exception as e:
        print(f"LM Studio API Call Failed: {e}")
        
    return None

@app.route('/api/generate-workout', methods=['POST'])
def api_generate_workout():
    """Generate a custom workout based on user preferences."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data received'}), 400
        
        domains = data.get('domains', [])
        duration = int(data.get('duration', 15))
        difficulty = data.get('difficulty', 'beginner')
        goal = data.get('goal', '')

        # Fetch exercises from database
        try:
            db_exercises = get_all_exercises_from_db()
        except Exception as e:
            return jsonify({'error': f'Database error: {str(e)}'}), 500

        if not db_exercises:
            return jsonify({'error': 'No exercise data available'}), 400

        # Create domain to category mapping
        domain_to_category = {
            'strength_power': 'Strength & Power',
            'speed_mobility': 'Speed & Mobility', 
            'endurance': 'Endurance',
            'agility': 'Agility',
            'cognition': 'Cognition',
            # Handle the values that the planner actually sends
            'Strength': 'Strength & Power',
            'Speed & Mobility': 'Speed & Mobility',
            'Endurance': 'Endurance',
            'Agility': 'Agility',
            'Cognitive': 'Cognition'
        }

        # First, prepare candidates for LLM
        llm_categories = [domain_to_category[d] for d in domains if d in domain_to_category]
        candidates = []
        for ex in db_exercises:
            if ex.get('category') in llm_categories:
                candidates.append({
                    'name': ex.get('name'),
                    'category': ex.get('category'),
                    'duration': ex.get('duration', 0.5),
                    'difficulty': ex.get('difficulty', 'beginner'),
                    'description': ex.get('description', ''),
                    'target_muscles': ex.get('target_muscles', '')
                })

        # Try local LLM generation
        focus = data.get('focus', '')
        llm_workout = generate_workout_via_llm(domains, duration, difficulty, focus, candidates, goal)
        if llm_workout:
            llm_workout['workout_id'] = f"workout_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            return jsonify(llm_workout)

        # FALLBACK: Create explanation
        fallback_explanation = f"A {difficulty} level workout session focusing on {', '.join(domains)} domains"
        if focus:
            fallback_explanation += f" with a focus on {focus}."
        else:
            fallback_explanation += "."

        # FALLBACK: Filter exercises based on criteria (original rule-based logic)
        filtered_exercises = []
        for exercise in db_exercises:
            # Check if exercise category matches any selected domain
            category_match = False
            for domain in domains:
                if domain in domain_to_category and domain_to_category[domain] == exercise['category']:
                    category_match = True
                    break
            
            # Check difficulty match
            difficulty_match = difficulty.lower() in exercise['difficulty'].lower()

            if category_match and difficulty_match:
                filtered_exercises.append(exercise)

        # If no exercises match criteria, try with just category match
        if not filtered_exercises:
            for exercise in db_exercises:
                category_match = False
                for domain in domains:
                    if domain in domain_to_category and domain_to_category[domain] == exercise['category']:
                        category_match = True
                        break
                
                if category_match:
                    filtered_exercises.append(exercise)

        # If still no exercises, return exercises from the first selected domain
        if not filtered_exercises and domains:
            first_domain = domains[0]
            if first_domain in domain_to_category:
                category = domain_to_category[first_domain]
                filtered_exercises = [ex for ex in db_exercises 
                                    if ex['category'] == category]

        # Select exercises to fit duration
        selected_exercises = []
        total_time = 0
        
        # Sort exercises by duration to get a good mix
        filtered_exercises.sort(key=lambda x: x['duration'])
        
        for exercise in filtered_exercises:
            if total_time + exercise['duration'] <= duration:
                selected_exercises.append(exercise)
                total_time += exercise['duration']
            if total_time >= duration:
                break

        # If still no exercises, return at least one from the selected domain
        if not selected_exercises and filtered_exercises:
            selected_exercises = [filtered_exercises[0]]
            total_time = filtered_exercises[0]['duration']

        # Clean NaN values from exercises before JSON serialization
        cleaned_exercises = []
        for exercise in selected_exercises:
            cleaned_exercise = {}
            for key, value in exercise.items():
                if pd.isna(value):
                    cleaned_exercise[key] = None
                else:
                    cleaned_exercise[key] = value
            cleaned_exercises.append(cleaned_exercise)

        return jsonify({
            'success': True,
            'exercises': cleaned_exercises,
            'total_duration': round(total_time, 1),
            'difficulty': difficulty,
            'explanation': fallback_explanation,
            'workout_id': f"workout_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

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
        
        if not name or not category:
            return jsonify({'success': False, 'error': 'Name and Category are required'}), 400

        # Save to database
        conn = get_db_connection()
        try:
            conn.execute('''
                INSERT INTO exercises (category, exercise_name, duration_minutes, 
                                     primary_benefit, secondary_benefit, difficulty_level, instructions)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (category, name, duration, description, target_muscles, difficulty, instructions))
            conn.commit()
        finally:
            conn.close()
        
        new_ex = {
            'category': category,
            'name': name,
            'duration': duration,
            'description': description,
            'target_muscles': target_muscles,
            'difficulty': difficulty,
            'instructions': instructions
        }
        return jsonify({'success': True, 'exercise': new_ex})
    except sqlite3.IntegrityError:
        return jsonify({
            'success': False,
            'error': 'An exercise with that name already exists in this category.'
        }), 409
    except Exception as e:
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
            # Compute streak from consecutive days, same logic as logged-in path
            today_str = datetime.now().strftime('%Y-%m-%d')
            streak_dates = session.get('streak_dates', [])
            if today_str not in streak_dates:
                streak_dates.insert(0, today_str)
            streak = 0
            prev = datetime.now().date()
            for ds in streak_dates:
                try:
                    d = datetime.strptime(ds, '%Y-%m-%d').date()
                    if (prev - d).days <= 1:
                        streak += 1
                        prev = d
                    else:
                        break
                except Exception:
                    break
            session['streak'] = streak
            session['streak_dates'] = streak_dates[:30]

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

        # For logged-in users, save to database
        conn = get_db_connection()
        # Save session record
        # Save session
        conn.execute('''
            INSERT INTO user_sessions (user_id, exercises_completed, exercises_count, total_duration, session_type)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, json.dumps(data.get('exercises', [])), exercises_count,
              data.get('totalDuration', 0), data.get('sessionType', 'custom')))

        # Update user progress
        update_user_progress(user_id, data, conn)

        conn.commit()
        conn.close()

        return jsonify({'status': 'success', 'message': 'Session recorded successfully'})
    
    except Exception as e:
        import traceback
        print('[ERROR] Exception in api_complete_session:', e)
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/user_progress')
def api_user_progress():
    """Get user progress data for the progress page."""
    user_id = session.get('user_id', 'guest')
    progress_data = get_user_progress_data(user_id)
    return jsonify(progress_data)

@app.route('/api/user_stats')
def api_user_stats():
    """Get user statistics."""
    user_id = session.get('user_id')
    stats = get_user_stats(user_id)
    return jsonify(stats)

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
    return jsonify({'status': 'ok', 'app': 'FitTrack', 'version': '2.0'})

def get_domain_key(category):
    """Convert category to domain key."""
    mapping = {
        'strength': 'strength_power',
        'strength & power': 'strength_power',
        'speed': 'speed_mobility',
        'speed & mobility': 'speed_mobility',
        'mobility': 'speed_mobility',
        'endurance': 'endurance',
        'agility': 'agility',
        'cognitive': 'cognition',
        'cognition': 'cognition'
    }
    return mapping.get(category.lower())

def get_user_stats(user_id):
    """Get user statistics from database or session."""
    if user_id == 'guest':
        return {
            'current_week': session.get('current_week', 1),
            'streak': session.get('streak', 0),
            'weekly_minutes': session.get('weekly_minutes', 0),
            'total_sessions': 0,
            'domain_progress': session.get('domain_progress', {})
        }

    # For logged-in users, get from database
    conn = get_db_connection()

    # Get basic progress
    progress = conn.execute('''
        SELECT current_week, streak_days, total_minutes 
        FROM user_progress 
        WHERE user_id = ? 
        LIMIT 1
    ''', (user_id,)).fetchone()

    # Get total sessions
    sessions = conn.execute('''
        SELECT COUNT(*) as total 
        FROM user_sessions 
        WHERE user_id = ?
    ''', (user_id,)).fetchone()

    # Get domain progress
    domain_progress = {}
    domains = conn.execute('''
        SELECT domain, sessions_completed 
        FROM user_progress 
        WHERE user_id = ?
    ''', (user_id,)).fetchall()

    for domain in domains:
        domain_progress[domain['domain']] = domain['sessions_completed']

    conn.close()

    if progress:
        return {
            'current_week': progress['current_week'],
            'streak': progress['streak_days'],
            'weekly_minutes': progress['total_minutes'],
            'total_sessions': sessions['total'] if sessions else 0,
            'domain_progress': domain_progress
        }
    else:
        return {
            'current_week': 1,
            'streak': 0,
            'weekly_minutes': 0,
            'total_sessions': 0,
            'domain_progress': {}
        }

def get_user_progress_data(user_id):
    """Get detailed progress data for charts."""
    if user_id == 'guest':
        # For guest users, use session data
        domain_progress = session.get('domain_progress', {})
        weekly_minutes = session.get('weekly_minutes', 0)
        streak = session.get('streak', 0)
        recent_sessions = session.get('recent_sessions', [])
        
        def _guest_domain(key):
            dp = domain_progress.get(key, {})
            if isinstance(dp, (int, float)):
                return {'sessions': int(dp), 'minutes': 0, 'week': 1, 'progress': min(int(dp) * 10, 100)}
            ex_count = dp.get('exercises', 0)
            mins = dp.get('minutes', 0)
            return {
                'sessions': ex_count,
                'minutes': mins,
                'week': 1,
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
            'weekly_progress': [weekly_minutes, 0, 0, 0],  # 4 weeks of data
            'recent_sessions': recent_sessions,
            'strength': domains['strength_power'],
            'speed_mobility': domains['speed_mobility'],
            'endurance': domains['endurance'],
            'agility': domains['agility'],
            'cognitive': domains['cognition']
        }

    # For logged-in users, get from database
    conn = get_db_connection()

    # Get recent sessions
    sessions = conn.execute('''
        SELECT session_date, total_duration, session_type 
        FROM user_sessions 
        WHERE user_id = ? 
        ORDER BY session_date DESC 
        LIMIT 10
    ''', (user_id,)).fetchall()

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
            'week': 1,  # Default week
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
    domains = {
        'strength_power': domain_progress.get('strength_power', {'sessions': 0, 'minutes': 0, 'week': 1, 'progress': 0}),
        'speed_mobility': domain_progress.get('speed_mobility', {'sessions': 0, 'minutes': 0, 'week': 1, 'progress': 0}),
        'endurance': domain_progress.get('endurance', {'sessions': 0, 'minutes': 0, 'week': 1, 'progress': 0}),
        'agility': domain_progress.get('agility', {'sessions': 0, 'minutes': 0, 'week': 1, 'progress': 0}),
        'cognition': domain_progress.get('cognition', {'sessions': 0, 'minutes': 0, 'week': 1, 'progress': 0})
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

    streak = 0
    prev = datetime.now().date()
    for row in streak_rows:
        try:
            d = datetime.strptime(str(row['session_date']), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            break
        if (prev - d).days <= 1:
            streak += 1
            prev = d
        else:
            break

    avg_length = round(total_minutes / total_sessions) if total_sessions > 0 else 0

    conn.close()

    return {
        'total_sessions': total_sessions,
        'total_exercises': total_exercises,
        'total_minutes': total_minutes,
        'current_streak': streak,
        'avg_session_length': avg_length,
        'weekly_progress': [total_minutes, 0, 0, 0],
        'recent_sessions': [dict(s) for s in sessions],
        'strength': domains['strength_power'],
        'speed_mobility': domains['speed_mobility'],
        'endurance': domains['endurance'],
        'agility': domains['agility'],
        'cognitive': domains['cognition']
    }

def update_user_progress(user_id, session_data, conn=None):
    """Update user progress after completing a session."""
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True

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

    if close_conn:
        conn.commit()
        conn.close()

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
            flash('Login successful!', 'success')
            return redirect(url_for('index'))

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
            conn.execute('''
                INSERT INTO users (username, email, password_hash)
                VALUES (?, ?, ?)
            ''', (username, email, password_hash))
            conn.commit()

            # Get the new user ID
            user_id = conn.execute('''
                SELECT id FROM users WHERE username = ?
            ''', (username,)).fetchone()['id']

            conn.close()

            # Set session
            session['user_id'] = user_id
            session['username'] = username

            flash('Registration successful!', 'success')
            return redirect(url_for('index'))
        except sqlite3.IntegrityError:
            conn.close()
            flash('Username or email already exists', 'error')
            return render_template('register.html')
        except Exception as e:
            conn.close()
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
    # Run the app
    app.run(
        debug=os.environ.get('FLASK_DEBUG', '0') == '1',
        host=os.environ.get('FLASK_HOST', '127.0.0.1'),
        port=int(os.environ.get('FLASK_PORT', 5000))
    )
