# MyLife AI (Flask + SQLite)

## Run locally
    python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    export SECRET_KEY="long-random-string"
    export ANTHROPIC_API_KEY="your-key"     # optional: enables AI chat
    python app.py                           # open http://127.0.0.1:5000

## Structure
- app.py: API, auth (hashed passwords, sessions), analysis, chat, privacy
- static/index.html: single-page frontend
- mylife.db: created on first run

## Privacy design
Every query filters by the signed-in user's id, so accounts are isolated. Users can delete entries, memories, all data, or their account. Chat is only sent your data summary and memories you saved.

## Before deploying
Use HTTPS, set SECRET_KEY and SESSION_COOKIE_SECURE=True, run with gunicorn app:app, and add rate limiting on /api/login.
