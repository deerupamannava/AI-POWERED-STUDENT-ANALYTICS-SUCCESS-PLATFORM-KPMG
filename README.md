# Student Success AI — Full-stack student planner

Responsive Flask + SQLite project with individual student accounts, saved profiles, saved recommendations, study plans, and persistent completion tracking. Optional OpenAI integration is supported; if no API key is configured, a built-in personalized plan generator keeps the application usable.

## Features
- Register and log in with email/password (passwords are hashed).
- Each student's profile, generated plan, recommendations, and completed tasks are stored in SQLite.
- Returning users go directly to their saved dashboard.
- Edit learning goals and regenerate recommendations.
- Responsive landing, profile, dashboard, study plan, progress, and motivation pages.
- Optional OpenAI-powered plan generation through environment variables.

## Run locally (Windows)
1. Install Python 3.10+.
2. Extract the ZIP and open this folder in VS Code.
3. In the terminal, create a virtual environment:
   ```powershell
   py -m venv .venv
   .venv\Scripts\activate
   ```
4. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
5. Copy `.env.example` to `.env` and set a long random `SECRET_KEY`.
6. Optional AI mode: add your own API key to `OPENAI_API_KEY` in `.env`. API use may incur charges. Do not share or commit this key.
7. Start the app:
   ```powershell
   python app.py
   ```
8. Open http://127.0.0.1:5000 in your browser.

## macOS/Linux environment commands
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

## Pages
- `/` landing page
- `/register` account creation
- `/login` login
- `/profile` learning profile and plan generation
- `/dashboard` saved recommendations overview
- `/study-plan` task completion tracker
- `/progress` progress summary
- `/motivation` encouragement page

## Notes
- SQLite database is created automatically as `student_success.db` on first run.
- The included built-in generator is personalized but is not a large language model. Real AI responses require a valid API key and internet access on the server.
- For production deployment, configure HTTPS, a persistent database/volume, a strong secret key, rate limiting, CSRF protection, backups, and provider billing limits. Do not use Flask's development server as a production server.
