import os, json, sqlite3, secrets
from datetime import datetime, date
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY') or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
DB_PATH = BASE_DIR / 'student_success.db'

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db

@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute('PRAGMA foreign_keys = ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      email TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS student_profiles (
      user_id INTEGER PRIMARY KEY,
      course TEXT NOT NULL DEFAULT '',
      year_level TEXT NOT NULL DEFAULT '',
      goal TEXT NOT NULL DEFAULT '',
      subjects TEXT NOT NULL DEFAULT '',
      strengths TEXT NOT NULL DEFAULT '',
      challenges TEXT NOT NULL DEFAULT '',
      hours_per_day INTEGER NOT NULL DEFAULT 2,
      preferred_style TEXT NOT NULL DEFAULT 'Mixed',
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS plans (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      plan_json TEXT NOT NULL,
      recommendations_json TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS completed_tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      task_key TEXT NOT NULL,
      completed INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(user_id, task_key),
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    ''')
    db.commit(); db.close()

init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'):
            flash('Please log in to view your student workspace.', 'info')
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped


def current_user():
    if not session.get('user_id'):
        return None
    return get_db().execute('SELECT id, name, email FROM users WHERE id=?', (session['user_id'],)).fetchone()

@app.context_processor
def inject_user():
    return {'current_user': current_user()}


def get_profile(user_id):
    return get_db().execute('SELECT * FROM student_profiles WHERE user_id=?', (user_id,)).fetchone()


def fallback_study_plan(profile):
    goal = profile['goal'] or 'Improve academic performance'
    subjects = [s.strip() for s in (profile['subjects'] or '').split(',') if s.strip()]
    if not subjects:
        subjects = ['Priority subject', 'Practice and revision']
    hours = max(1, min(8, int(profile['hours_per_day'] or 2)))
    tasks = []
    # A flexible schedule with short focused blocks and a break.
    blocks = max(2, min(5, hours + 1))
    for i in range(blocks):
        subject = subjects[i % len(subjects)]
        if i == 0:
            title, detail = f'Plan today’s target for {subject}', 'Write one clear outcome and review the key concepts.'
        elif i == blocks - 1:
            title, detail = 'Recall and reflect', 'Close your notes, write what you remember, then list doubts for tomorrow.'
        else:
            title, detail = f'Focused practice: {subject}', 'Solve questions or complete a small practical exercise; check mistakes.'
        tasks.append({'key': f'day1-task{i+1}', 'time': f'{min(20 + i*25, hours*60)} min block', 'title': title, 'detail': detail, 'subject': subject})
    recommendations = [
        f'Work backwards from your goal: {goal}. Choose one measurable result for this week.',
        f'Prioritize {subjects[0]} in your first focused block, when your attention is freshest.',
        'Use active recall and practice questions instead of only rereading notes.',
        'Review mistakes at the end of each session and revisit difficult topics within 48 hours.',
    ]
    if profile['challenges']:
        recommendations.insert(1, f'For your stated challenge ({profile["challenges"]}), break work into 20–30 minute steps and start with the smallest one.')
    return tasks, recommendations, 'Personalized starter plan (built-in mode)'


def make_ai_plan(profile):
    """Uses OpenAI when configured; always has a local fallback."""
    fallback_tasks, fallback_recs, mode = fallback_study_plan(profile)
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key or OpenAI is None:
        return fallback_tasks, fallback_recs, mode
    try:
        client = OpenAI(api_key=api_key)
        prompt = {
            'student_goal': profile['goal'], 'course': profile['course'], 'year': profile['year_level'],
            'subjects': profile['subjects'], 'strengths': profile['strengths'],
            'challenges': profile['challenges'], 'hours_per_day': profile['hours_per_day'],
            'preferred_style': profile['preferred_style'],
            'requirements': 'Create realistic student support, not guaranteed outcomes. Return JSON only with keys tasks and recommendations. tasks: 3-6 objects with key, time, title, detail, subject. recommendations: 4-6 short specific recommendations. Make the plan fit the daily available hours, include breaks or review, and do not assume exams/dates not provided.'
        }
        response = client.chat.completions.create(
            model=os.getenv('OPENAI_MODEL', 'gpt-4o-mini'),
            messages=[
                {'role':'system','content':'You are a careful, encouraging study coach. Personalize learning advice; avoid fabricated facts. Output valid JSON only.'},
                {'role':'user','content':json.dumps(prompt)}
            ], temperature=0.4, response_format={'type':'json_object'}, max_tokens=1200
        )
        data = json.loads(response.choices[0].message.content)
        tasks = data.get('tasks', [])
        recs = data.get('recommendations', [])
        if not isinstance(tasks, list) or not tasks or not isinstance(recs, list) or not recs:
            raise ValueError('AI response missing required fields')
        clean_tasks = []
        for i, task in enumerate(tasks[:8]):
            if not isinstance(task, dict): continue
            clean_tasks.append({'key': f'day1-task{i+1}', 'time': str(task.get('time','25 min')), 'title': str(task.get('title','Study session'))[:120], 'detail': str(task.get('detail','Focus on one learning outcome.'))[:400], 'subject': str(task.get('subject','General'))[:80]})
        clean_recs = [str(r)[:400] for r in recs[:8] if str(r).strip()]
        if clean_tasks and clean_recs:
            return clean_tasks, clean_recs, 'AI-personalized plan'
    except Exception as exc:
        app.logger.warning('AI plan generation unavailable; using fallback: %s', exc)
    return fallback_tasks, fallback_recs, mode


def latest_plan(user_id):
    row = get_db().execute('SELECT * FROM plans WHERE user_id=? ORDER BY id DESC LIMIT 1', (user_id,)).fetchone()
    if row:
        return json.loads(row['plan_json']), json.loads(row['recommendations_json']), row['created_at']
    profile = get_profile(user_id)
    if profile:
        tasks, recs, _ = fallback_study_plan(profile)
        return tasks, recs, None
    return [], [], None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if session.get('user_id'): return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        if len(name) < 2 or len(name) > 80:
            flash('Enter your name (2–80 characters).', 'error')
        elif '@' not in email or len(email) > 180:
            flash('Enter a valid email address.', 'error')
        elif len(password) < 8:
            flash('Use a password with at least 8 characters.', 'error')
        else:
            db = get_db()
            try:
                cur = db.execute('INSERT INTO users(name,email,password_hash) VALUES(?,?,?)', (name,email,generate_password_hash(password)))
                db.execute('INSERT INTO student_profiles(user_id) VALUES(?)', (cur.lastrowid,))
                db.commit()
                session.clear(); session['user_id'] = cur.lastrowid
                flash('Account created. Tell us about your learning goals to personalize your plan.', 'success')
                return redirect(url_for('profile'))
            except sqlite3.IntegrityError:
                flash('An account with that email already exists. Please log in.', 'error')
    return render_template('auth.html', mode='register')

@app.route('/login', methods=['GET','POST'])
def login():
    if session.get('user_id'): return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        user = get_db().execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session.clear(); session['user_id'] = user['id']
            return redirect(url_for('dashboard'))
        flash('Email or password is incorrect.', 'error')
    return render_template('auth.html', mode='login')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear(); flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

@app.route('/profile', methods=['GET','POST'])
@login_required
def profile():
    user = current_user(); existing = get_profile(user['id'])
    if request.method == 'POST':
        course = request.form.get('course','').strip()[:100]
        year_level = request.form.get('year_level','').strip()[:60]
        goal = request.form.get('goal','').strip()[:300]
        subjects = request.form.get('subjects','').strip()[:500]
        strengths = request.form.get('strengths','').strip()[:500]
        challenges = request.form.get('challenges','').strip()[:500]
        preferred_style = request.form.get('preferred_style','Mixed').strip()[:40]
        try: hours = int(request.form.get('hours_per_day','2'))
        except ValueError: hours = 2
        hours = max(1, min(8, hours))
        if not goal or not subjects:
            flash('Please fill in your learning goal and at least one subject.', 'error')
        else:
            db = get_db()
            db.execute('''UPDATE student_profiles SET course=?,year_level=?,goal=?,subjects=?,strengths=?,challenges=?,hours_per_day=?,preferred_style=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=?''',
                       (course,year_level,goal,subjects,strengths,challenges,hours,preferred_style,user['id']))
            db.commit()
            tasks, recs, mode = make_ai_plan(get_profile(user['id']))
            db.execute('DELETE FROM completed_tasks WHERE user_id=?', (user['id'],))
            db.execute('INSERT INTO plans(user_id,plan_json,recommendations_json) VALUES(?,?,?)', (user['id'],json.dumps(tasks),json.dumps(recs)))
            db.commit()
            flash(f'Your study plan has been saved. {mode}.', 'success')
            return redirect(url_for('dashboard'))
    return render_template('profile.html', profile=existing)

@app.route('/dashboard')
@login_required
def dashboard():
    user=current_user(); profile=get_profile(user['id'])
    if not profile or not profile['goal'] or not profile['subjects']:
        flash('Complete your learning profile to generate your personal plan.', 'info')
        return redirect(url_for('profile'))
    tasks, recs, created = latest_plan(user['id'])
    completed_rows = get_db().execute('SELECT task_key FROM completed_tasks WHERE user_id=? AND completed=1', (user['id'],)).fetchall()
    completed_keys = {row['task_key'] for row in completed_rows}
    done = sum(1 for task in tasks if task.get('key') in completed_keys)
    total = len(tasks); percent = round(done / total * 100) if total else 0
    mode_row=get_db().execute('SELECT created_at FROM plans WHERE user_id=? ORDER BY id DESC LIMIT 1',(user['id'],)).fetchone()
    return render_template('dashboard.html', profile=profile, tasks=tasks, recommendations=recs, done=done, total=total, percent=percent, plan_date=mode_row['created_at'] if mode_row else None)

@app.route('/study-plan', methods=['GET','POST'])
@login_required
def study_plan():
    user=current_user(); profile=get_profile(user['id'])
    if not profile or not profile['goal'] or not profile['subjects']: return redirect(url_for('profile'))
    if request.method == 'POST':
        task_key=request.form.get('task_key','')[:100]
        completed=1 if request.form.get('completed') == '1' else 0
        tasks, _, _=latest_plan(user['id'])
        if any(t.get('key') == task_key for t in tasks):
            get_db().execute('''INSERT INTO completed_tasks(user_id,task_key,completed,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(user_id,task_key) DO UPDATE SET completed=excluded.completed,updated_at=CURRENT_TIMESTAMP''',(user['id'],task_key,completed))
            get_db().commit()
        return redirect(url_for('study_plan'))
    tasks,recs,created=latest_plan(user['id'])
    rows=get_db().execute('SELECT task_key,completed FROM completed_tasks WHERE user_id=?',(user['id'],)).fetchall()
    states={r['task_key']:bool(r['completed']) for r in rows}
    done=sum(1 for t in tasks if states.get(t.get('key',''),False))
    return render_template('studyplan.html',profile=profile,tasks=tasks,recommendations=recs,states=states,done=done,total=len(tasks),percent=round(done/len(tasks)*100) if tasks else 0)

@app.route('/progress')
@login_required
def progress():
    user=current_user(); profile=get_profile(user['id'])
    tasks,recs,created=latest_plan(user['id'])
    rows=get_db().execute('SELECT task_key,completed FROM completed_tasks WHERE user_id=?',(user['id'],)).fetchall()
    states={r['task_key']:bool(r['completed']) for r in rows}
    done=sum(1 for t in tasks if states.get(t.get('key',''),False)); total=len(tasks)
    return render_template('progress.html',profile=profile,tasks=tasks,done=done,total=total,percent=round(done/total*100) if total else 0,recommendations=recs)

@app.route('/motivation')
@login_required
def motivation():
    profile=get_profile(session['user_id'])
    return render_template('motivational.html',profile=profile)

@app.route('/regenerate-plan', methods=['POST'])
@login_required
def regenerate_plan():
    profile=get_profile(session['user_id'])
    if not profile or not profile['goal'] or not profile['subjects']:
        return redirect(url_for('profile'))
    tasks,recs,mode=make_ai_plan(profile)
    db=get_db(); db.execute('DELETE FROM completed_tasks WHERE user_id=?',(session['user_id'],))
    db.execute('INSERT INTO plans(user_id,plan_json,recommendations_json) VALUES(?,?,?)',(session['user_id'],json.dumps(tasks),json.dumps(recs)))
    db.commit()
    flash(f'New recommendations saved. {mode}.', 'success')
    return redirect(url_for('dashboard'))

@app.errorhandler(404)
def not_found(_error):
    return render_template('error.html', code=404, message='That page could not be found.'),404

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG','false').lower() == 'true', host='127.0.0.1', port=int(os.getenv('PORT','5000')))
