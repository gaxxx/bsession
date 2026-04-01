"""Eval recorder — tracks skill run metrics in SQLite.

Uses the existing ports.db to avoid a second database file.
Records: start/end time, status, duration, retries, error, skill-specific data.
Provides summary stats (success rate, avg duration) per session/skill.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass

from lib.skill import SkillResult

DB_PATH = os.environ.get("SESSION_DB", "/workspace/data/ports.db")


@dataclass(frozen=True)
class RunSummary:
    total_runs: int
    successes: int
    failures: int
    success_rate: float
    avg_duration_ms: float
    p95_duration_ms: float
    last_error: str | None
    last_run: str | None


class EvalRecorder:
    """Records and queries skill execution metrics."""

    def __init__(self, db_path: str = DB_PATH) -> None:
        self._db_path = db_path
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._db_path)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _ensure_table(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                duration_ms INTEGER DEFAULT 0,
                retries INTEGER DEFAULT 0,
                error TEXT,
                metadata TEXT
            )""")

    def record(self, skill_name: str, session_id: str, result: SkillResult) -> int:
        """Record a completed skill run. Returns the run ID."""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        status = "success" if result.success else "failure"
        meta = json.dumps(result.data) if result.data else None

        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO runs
                   (session_id, skill_name, started_at, finished_at,
                    status, duration_ms, retries, error, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, skill_name, now, now,
                    status, result.duration_ms, result.retries,
                    result.error, meta,
                ),
            )
            return cursor.lastrowid

    def get_runs(
        self, session_id: str, limit: int = 20,
    ) -> list[dict]:
        """Get recent runs for a session."""
        with self._connect() as db:
            rows = db.execute(
                """SELECT id, skill_name, started_at, status,
                          duration_ms, retries, error, metadata
                   FROM runs WHERE session_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()

        return [
            {
                "id": r[0], "skill": r[1], "started_at": r[2],
                "status": r[3], "duration_ms": r[4], "retries": r[5],
                "error": r[6],
                "data": json.loads(r[7]) if r[7] else {},
            }
            for r in rows
        ]

    def get_summary(self, session_id: str) -> RunSummary:
        """Aggregate stats for a session."""
        with self._connect() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM runs WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]

            if total == 0:
                return RunSummary(0, 0, 0, 0.0, 0.0, 0.0, None, None)

            successes = db.execute(
                "SELECT COUNT(*) FROM runs WHERE session_id = ? AND status = 'success'",
                (session_id,),
            ).fetchone()[0]

            avg_dur = db.execute(
                "SELECT AVG(duration_ms) FROM runs WHERE session_id = ? AND status = 'success'",
                (session_id,),
            ).fetchone()[0] or 0.0

            durations = [
                r[0] for r in db.execute(
                    "SELECT duration_ms FROM runs WHERE session_id = ? AND status = 'success' ORDER BY duration_ms",
                    (session_id,),
                ).fetchall()
            ]
            p95_idx = int(len(durations) * 0.95) if durations else 0
            p95 = durations[min(p95_idx, len(durations) - 1)] if durations else 0.0

            last_error_row = db.execute(
                "SELECT error FROM runs WHERE session_id = ? AND status = 'failure' ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()

            last_run_row = db.execute(
                "SELECT started_at FROM runs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()

        return RunSummary(
            total_runs=total,
            successes=successes,
            failures=total - successes,
            success_rate=round(successes / total * 100, 1) if total else 0.0,
            avg_duration_ms=round(avg_dur, 1),
            p95_duration_ms=float(p95),
            last_error=last_error_row[0] if last_error_row else None,
            last_run=last_run_row[0] if last_run_row else None,
        )
