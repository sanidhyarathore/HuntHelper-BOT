"""SQLite layer. Single file, no ORM — easy to inspect with `sqlite3 data/jobs.db`."""
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel       TEXT NOT NULL,
    channel_title TEXT,
    msg_id        INTEGER NOT NULL,
    posted_at     TEXT,
    text          TEXT,
    buttons       TEXT,           -- JSON [{text, url|callback}]
    permalink     TEXT,
    processed     INTEGER DEFAULT 0,
    UNIQUE(channel, msg_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id    INTEGER REFERENCES messages(id),
    company       TEXT,
    title         TEXT,
    location      TEXT,
    country       TEXT,
    seniority     TEXT,
    function      TEXT,
    comp          TEXT,
    min_years     REAL,
    apply_type    TEXT,           -- url | email | telegram_bot | dm | unknown
    apply_url     TEXT,
    apply_email   TEXT,
    canonical_url TEXT,
    dedupe_key    TEXT,
    scam          INTEGER DEFAULT 0,
    scam_reason   TEXT,
    fit_score     INTEGER,
    fit_reason    TEXT,
    raw           TEXT,
    status        TEXT DEFAULT 'new',   -- new|notified|applied|skipped|later|autorejected
    created_at    TEXT,
    UNIQUE(dedupe_key)
);

CREATE TABLE IF NOT EXISTS applications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        INTEGER REFERENCES jobs(id) UNIQUE,
    applied_at    TEXT,
    method        TEXT,
    draft_id      TEXT,
    followup_1    INTEGER DEFAULT 0,
    followup_2    INTEGER DEFAULT 0,
    outcome       TEXT,
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_msg_processed ON messages(processed);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def save_message(channel, channel_title, msg_id, posted_at, text, buttons, permalink) -> bool:
    """Returns True if newly inserted."""
    with conn() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO messages
               (channel, channel_title, msg_id, posted_at, text, buttons, permalink)
               VALUES (?,?,?,?,?,?,?)""",
            (channel, channel_title, msg_id, posted_at, text, json.dumps(buttons), permalink),
        )
        return cur.rowcount > 0


def last_msg_id(channel) -> int:
    """Highest message id already stored for a channel. 0 if never seen."""
    with conn() as c:
        r = c.execute(
            "SELECT MAX(msg_id) FROM messages WHERE channel = ?", (str(channel),)
        ).fetchone()
        return r[0] or 0


def unprocessed_messages(limit=200):
    with conn() as c:
        return c.execute(
            "SELECT * FROM messages WHERE processed = 0 ORDER BY id LIMIT ?", (limit,)
        ).fetchall()


def mark_processed(message_id):
    with conn() as c:
        c.execute("UPDATE messages SET processed = 1 WHERE id = ?", (message_id,))


def save_job(d) -> int | None:
    """Insert a job. Returns row id, or None if it was a duplicate."""
    d.setdefault("created_at", now())
    cols = ",".join(d)
    marks = ",".join("?" * len(d))
    with conn() as c:
        cur = c.execute(f"INSERT OR IGNORE INTO jobs ({cols}) VALUES ({marks})", tuple(d.values()))
        return cur.lastrowid if cur.rowcount else None


def job(job_id):
    with conn() as c:
        return c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def pending_notification(threshold, limit):
    with conn() as c:
        return c.execute(
            """SELECT * FROM jobs
               WHERE status = 'new' AND scam = 0 AND fit_score >= ?
               ORDER BY fit_score DESC, id DESC LIMIT ?""",
            (threshold, limit),
        ).fetchall()


def set_status(job_id, status):
    with conn() as c:
        c.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


def log_application(job_id, method, draft_id=None):
    with conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO applications (job_id, applied_at, method, draft_id)
               VALUES (?,?,?,?)""",
            (job_id, now(), method, draft_id),
        )


def due_followups(days_1, days_2):
    with conn() as c:
        return c.execute(
            """SELECT a.*, j.company, j.title
               FROM applications a JOIN jobs j ON j.id = a.job_id
               WHERE (a.followup_1 = 0 AND julianday('now') - julianday(a.applied_at) >= ?)
                  OR (a.followup_2 = 0 AND julianday('now') - julianday(a.applied_at) >= ?)""",
            (days_1, days_2),
        ).fetchall()


def mark_followup(app_id, which):
    with conn() as c:
        c.execute(f"UPDATE applications SET followup_{which} = 1 WHERE id = ?", (app_id,))


def stats():
    with conn() as c:
        return {
            "messages": c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "jobs": c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "scam": c.execute("SELECT COUNT(*) FROM jobs WHERE scam = 1").fetchone()[0],
            "autorejected": c.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'autorejected'").fetchone()[0],
            "notified": c.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'notified'").fetchone()[0],
            "applied": c.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
        }
