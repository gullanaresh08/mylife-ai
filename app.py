"""MyLife AI - Flask + SQLite backend.

Run:  pip install -r requirements.txt
      export SECRET_KEY="long-random-string"
      export ANTHROPIC_API_KEY="..."   # optional; without it chat shows your data summary
      python app.py
"""
import json, os, sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1")
DB_PATH = os.environ.get("DB_PATH", "mylife.db")

# name, label, type, required, options
MODULES = {
    "mood": ("Mood", [("mood", "Mood (1 low - 5 great)", "select", True, ["1", "2", "3", "4", "5"]),
                      ("journal", "Journal", "text", False, None)]),
    "health": ("Health", [("weight", "Weight (kg)", "number", False, None), ("sleep", "Sleep (h)", "number", False, None),
                          ("exercise", "Exercise (min)", "number", False, None), ("water", "Water (glasses)", "number", False, None),
                          ("symptoms", "Symptoms", "text", False, None)]),
    "edu": ("Study", [("subject", "Subject", "text", True, None), ("hours", "Hours", "number", True, None),
                      ("test", "Test or goal", "text", False, None), ("score", "Score %", "number", False, None)]),
    "career": ("Career", [("kind", "Kind", "select", True, ["skill", "resume", "project", "application", "interview prep"]),
                          ("title", "Details", "text", True, None), ("hours", "Hours", "number", False, None)]),
    "biz": ("Business", [("kind", "Kind", "select", True, ["idea", "task", "expense", "goal"]),
                         ("title", "Details", "text", True, None), ("amount", "Amount", "number", False, None)]),
    "fin": ("Money", [("kind", "Type", "select", True, ["income", "expense", "saving"]),
                      ("category", "Category", "text", True, None), ("amount", "Amount", "number", True, None)]),
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, budget REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, module TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, text TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS goals(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL, kind TEXT NOT NULL, progress INTEGER DEFAULT 0, created TEXT NOT NULL);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def err(msg, code=400):
    return jsonify(error=msg), code


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "uid" not in session:
            return err("Please sign in.", 401)
        return f(*a, **k)
    return w


# ---------- auth ----------
@app.post("/api/register")
def register():
    b = request.get_json(silent=True) or {}
    name, pw = str(b.get("username", "")).strip().lower(), str(b.get("password", ""))
    if not (3 <= len(name) <= 30) or len(pw) < 8:
        return err("Username needs 3-30 characters and the password at least 8.")
    try:
        cur = db().execute("INSERT INTO users(username,pw_hash) VALUES(?,?)", (name, generate_password_hash(pw)))
        db().commit()
    except sqlite3.IntegrityError:
        return err("That username is taken.", 409)
    session["uid"] = cur.lastrowid
    return jsonify(username=name)


@app.post("/api/login")
def login():
    b = request.get_json(silent=True) or {}
    name = str(b.get("username", "")).strip().lower()
    u = db().execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
    if not u or not check_password_hash(u["pw_hash"], str(b.get("password", ""))):
        return err("Wrong username or password.", 401)
    session["uid"] = u["id"]
    return jsonify(username=name)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    if "uid" not in session:
        return err("Not signed in.", 401)
    u = db().execute("SELECT username,budget FROM users WHERE id=?", (session["uid"],)).fetchone()
    if not u:
        session.clear()
        return err("Not signed in.", 401)
    return jsonify(username=u["username"], budget=u["budget"],
                   schema={k: {"name": n, "fields": [dict(name=f[0], label=f[1], type=f[2], required=f[3], options=f[4]) for f in fs]}
                           for k, (n, fs) in MODULES.items()})


# ---------- trackers ----------
def clean(module, body):
    out = {}
    for name, label, typ, req, opts in MODULES[module][1]:
        v = str(body.get(name, "")).strip()[:300]
        if not v:
            if req:
                raise ValueError(f"{label} is required.")
            continue
        if typ == "number":
            try:
                if float(v) < 0:
                    raise ValueError
            except ValueError:
                raise ValueError(f"{label} must be a positive number.")
        if typ == "select" and v not in opts:
            raise ValueError(f"Invalid {label}.")
        out[name] = v
    if not out:
        raise ValueError("Enter at least one value.")
    return out


@app.get("/api/entries/<module>")
@login_required
def list_entries(module):
    if module not in MODULES:
        return err("Unknown module.", 404)
    rows = db().execute("SELECT id,data,created FROM entries WHERE user_id=? AND module=? ORDER BY id DESC LIMIT 50",
                        (session["uid"], module)).fetchall()
    return jsonify([dict(id=r["id"], created=r["created"][:10], data=json.loads(r["data"])) for r in rows])


@app.post("/api/entries/<module>")
@login_required
def add_entry(module):
    if module not in MODULES:
        return err("Unknown module.", 404)
    try:
        data = clean(module, request.get_json(silent=True) or {})
    except ValueError as e:
        return err(str(e))
    db().execute("INSERT INTO entries(user_id,module,data,created) VALUES(?,?,?,?)",
                 (session["uid"], module, json.dumps(data), now()))
    db().commit()
    return jsonify(ok=True), 201


@app.delete("/api/entries/item/<int:eid>")
@login_required
def del_entry(eid):
    db().execute("DELETE FROM entries WHERE id=? AND user_id=?", (eid, session["uid"]))
    db().commit()
    return jsonify(ok=True)


# ---------- memories & goals ----------
@app.route("/api/memories", methods=["GET", "POST"])
@login_required
def memories():
    uid = session["uid"]
    if request.method == "POST":
        t = str((request.get_json(silent=True) or {}).get("text", "")).strip()[:300]
        if not t:
            return err("Memory text is required.")
        db().execute("INSERT INTO memories(user_id,text,created) VALUES(?,?,?)", (uid, t, now()))
        db().commit()
    rows = db().execute("SELECT id,text,created FROM memories WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.delete("/api/memories/<int:mid>")
@login_required
def del_memory(mid):
    db().execute("DELETE FROM memories WHERE id=? AND user_id=?", (mid, session["uid"]))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/goals", methods=["GET", "POST"])
@login_required
def goals():
    uid = session["uid"]
    if request.method == "POST":
        b = request.get_json(silent=True) or {}
        t, k = str(b.get("title", "")).strip()[:200], b.get("kind")
        if not t or k not in ("short-term", "long-term"):
            return err("A title and a goal type are required.")
        db().execute("INSERT INTO goals(user_id,title,kind,created) VALUES(?,?,?,?)", (uid, t, k, now()))
        db().commit()
    rows = db().execute("SELECT id,title,kind,progress FROM goals WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/goals/<int:gid>", methods=["PATCH", "DELETE"])
@login_required
def goal(gid):
    if request.method == "DELETE":
        db().execute("DELETE FROM goals WHERE id=? AND user_id=?", (gid, session["uid"]))
    else:
        p = max(0, min(100, int((request.get_json(silent=True) or {}).get("progress", 0))))
        db().execute("UPDATE goals SET progress=? WHERE id=? AND user_id=?", (p, gid, session["uid"]))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/budget")
@login_required
def budget():
    try:
        v = max(0.0, float((request.get_json(silent=True) or {}).get("budget", 0)))
    except (TypeError, ValueError):
        return err("Budget must be a number.")
    db().execute("UPDATE users SET budget=? WHERE id=?", (v, session["uid"]))
    db().commit()
    return jsonify(ok=True)


# ---------- analysis ----------
def rows_of(uid, module):
    return [json.loads(r["data"]) for r in db().execute("SELECT data FROM entries WHERE user_id=? AND module=? ORDER BY id", (uid, module))]


def avg(rows, key):
    v = [float(r[key]) for r in rows if key in r]
    return sum(v) / len(v) if v else None


def total(rows, key, kind=None):
    return sum(float(r.get(key, 0)) for r in rows if kind is None or r.get("kind") == kind)


def analyze(uid):
    mood, health, edu = rows_of(uid, "mood"), rows_of(uid, "health"), rows_of(uid, "edu")
    career, fin, biz = rows_of(uid, "career"), rows_of(uid, "fin"), rows_of(uid, "biz")
    budget_v = db().execute("SELECT budget FROM users WHERE id=?", (uid,)).fetchone()["budget"]
    goals_ = [r["progress"] for r in db().execute("SELECT progress FROM goals WHERE user_id=?", (uid,))]
    recent = mood[-7:]
    stats = {
        "mood_avg": avg(recent, "mood"), "mood_trend": [int(r["mood"]) for r in recent],
        "sleep_avg": avg(health, "sleep"), "exercise_avg": avg(health, "exercise"), "water_avg": avg(health, "water"),
        "study_hours": total(edu, "hours"), "score_avg": avg(edu, "score"),
        "applications": sum(1 for r in career if r.get("kind") == "application"),
        "income": total(fin, "amount", "income"), "expenses": total(fin, "amount", "expense"), "saved": total(fin, "amount", "saving"),
        "biz_expenses": total(biz, "amount", "expense"), "ideas": sum(1 for r in biz if r.get("kind") == "idea"),
        "goal_avg": round(sum(goals_) / len(goals_)) if goals_ else None, "budget": budget_v,
    }
    tips = []
    s, m = stats["sleep_avg"], stats["mood_avg"]
    if s is not None and s < 7:
        tips.append("Average sleep is under 7 hours." + (" Mood is also low, so rest may help." if m is not None and m < 3.5 else ""))
    if stats["exercise_avg"] is not None and stats["exercise_avg"] < 30:
        tips.append("Exercise averages under 30 minutes. Short daily walks add up.")
    if budget_v and stats["expenses"] > budget_v:
        tips.append(f"Spending ({stats['expenses']:.0f}) is over your budget ({budget_v:.0f}).")
    if fin and not stats["saved"]:
        tips.append("No savings logged yet.")
    if edu and stats["study_hours"] < 5:
        tips.append("Study time is under 5 hours. A short daily session helps.")
    if career and not stats["applications"]:
        tips.append("No job applications logged yet.")
    tips.append(f"Goals are {stats['goal_avg']}% complete on average." if goals_ else "Add a goal to track progress.")
    return stats, tips


@app.get("/api/dashboard")
@login_required
def dashboard():
    stats, tips = analyze(session["uid"])
    return jsonify(stats=stats, insights=tips)


# ---------- chat ----------
SYSTEM = ("You are MyLife AI, a warm, practical personal assistant for study, career, business, money, health habits "
          "and daily life. Be concise and supportive. Use the user's data only when relevant. You are not a doctor, lawyer "
          "or financial advisor; say so for high-stakes questions. If the user mentions self-harm, respond with care and "
          "encourage contacting local emergency services or a trusted person.")


def context(uid):
    stats, tips = analyze(uid)
    mem = [r["text"] for r in db().execute("SELECT text FROM memories WHERE user_id=? ORDER BY id DESC LIMIT 30", (uid,))]
    gl = [f"{r['title']} ({r['kind']}, {r['progress']}%)" for r in db().execute("SELECT * FROM goals WHERE user_id=?", (uid,))]
    return "Memories the user allowed: " + "; ".join(mem) + "\nStats: " + json.dumps(stats) + "\nGoals: " + "; ".join(gl) + "\nInsights: " + " ".join(tips)


@app.post("/api/chat")
@login_required
def chat():
    uid = session["uid"]
    msgs = [m for m in (request.get_json(silent=True) or {}).get("messages", [])[-8:]
            if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()]
    if not msgs or msgs[-1]["role"] != "user":
        return err("Send a message.")
    last = str(msgs[-1]["content"]).strip()
    if last.lower().startswith("remember"):
        t = last[8:].lstrip(": ").strip()[:300]
        if t:
            db().execute("INSERT INTO memories(user_id,text,created) VALUES(?,?,?)", (uid, t, now()))
            db().commit()
            return jsonify(reply="Saved to your memory. You can review or delete it under Privacy.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify(reply="AI chat is off because ANTHROPIC_API_KEY is not set on the server. Your summary:\n" + context(uid))
    try:
        import anthropic
        r = anthropic.Anthropic().messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=700,
            system=SYSTEM + "\nUSER DATA:\n" + context(uid),
            messages=[{"role": m["role"], "content": str(m["content"])[:2000]} for m in msgs])
        return jsonify(reply=r.content[0].text)
    except Exception as e:  # keep the app usable if the API fails
        app.logger.error("chat error: %s", e)
        return err("The AI could not answer right now. Try again soon.", 502)


# ---------- privacy ----------
@app.get("/api/export")
@login_required
def export():
    uid = session["uid"]
    return jsonify(
        entries={k: rows_of(uid, k) for k in MODULES},
        memories=[dict(r) for r in db().execute("SELECT text,created FROM memories WHERE user_id=?", (uid,))],
        goals=[dict(r) for r in db().execute("SELECT title,kind,progress FROM goals WHERE user_id=?", (uid,))])


@app.delete("/api/data")
@login_required
def wipe():
    uid = session["uid"]
    for t in ("entries", "memories", "goals"):
        db().execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
    db().execute("UPDATE users SET budget=0 WHERE id=?", (uid,))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/account")
@login_required
def delete_account():
    db().execute("DELETE FROM users WHERE id=?", (session["uid"],))
    db().commit()
    session.clear()
    return jsonify(ok=True)


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


with sqlite3.connect(DB_PATH) as _c:
    _c.executescript(SCHEMA_SQL)

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
