"""SQLite state for chromes (LRU + cap).

  chromes(profile PK, port, pid, last_used)   — one Chrome per profile

Eviction policy: LRU when over BSESSION_MAX_PROFILES (default 5).
"""

import os
import sqlite3
import time

STATE_DIR = os.environ.get("BSESSION_STATE_DIR", "/workspace/.bsession-state")
DB_PATH = os.path.join(STATE_DIR, "state.db")
MAX_PROFILES = int(os.environ.get("BSESSION_MAX_PROFILES", "5"))
BASE_PORT = 9300


def _db():
    os.makedirs(STATE_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("""CREATE TABLE IF NOT EXISTS chromes (
        profile TEXT PRIMARY KEY,
        port INTEGER UNIQUE NOT NULL,
        pid INTEGER,
        last_used REAL
    )""")
    return db


def get_chrome(profile):
    """Return (port, pid) or None."""
    with _db() as db:
        return db.execute(
            "SELECT port, pid FROM chromes WHERE profile = ?", (profile,)
        ).fetchone()


def list_chromes():
    """Return [(profile, port, pid, last_used)] sorted by last_used desc."""
    with _db() as db:
        return list(db.execute(
            "SELECT profile, port, pid, last_used FROM chromes ORDER BY last_used DESC"
        ))


def insert_chrome(profile, port, pid):
    with _db() as db:
        db.execute(
            "INSERT INTO chromes (profile, port, pid, last_used) VALUES (?, ?, ?, ?)",
            (profile, port, pid, time.time()),
        )
        db.commit()


def touch_chrome(profile):
    with _db() as db:
        db.execute("UPDATE chromes SET last_used = ? WHERE profile = ?",
                   (time.time(), profile))
        db.commit()


def remove_chrome(profile):
    with _db() as db:
        db.execute("DELETE FROM chromes WHERE profile = ?", (profile,))
        db.commit()


def lru_chrome():
    """Profile with oldest last_used, or None."""
    with _db() as db:
        row = db.execute(
            "SELECT profile FROM chromes ORDER BY last_used ASC LIMIT 1"
        ).fetchone()
    return row[0] if row else None


def chrome_count():
    with _db() as db:
        return db.execute("SELECT COUNT(*) FROM chromes").fetchone()[0]


def allocate_port():
    """Pick next free port starting from BASE_PORT."""
    with _db() as db:
        rows = db.execute("SELECT port FROM chromes ORDER BY port").fetchall()
    used = {r[0] for r in rows}
    p = BASE_PORT
    while p in used:
        p += 1
    return p
