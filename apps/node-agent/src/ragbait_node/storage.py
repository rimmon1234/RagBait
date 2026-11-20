"""Metadata-only SQLite store for job deduplication and local lifecycle recovery.

CRITICAL PRIVACY GUARANTEE:
In accordance with M2.8 specifications, prompts, retrieved study excerpts, and
generated response texts are NEVER written to this local database.
Only non-sensitive metadata (job_id, model_name, status, timestamp) is recorded.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


class StateStorage:
    """Local SQLite state store for job deduplication."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS executed_jobs (
                    job_id TEXT PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    executed_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def has_job(self, job_id: str) -> bool:
        """Check whether a job ID has already been executed or accepted."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM executed_jobs WHERE job_id = ?", (job_id,))
            return cursor.fetchone() is not None

    def record_job(self, job_id: str, model_name: str, status: str = "completed") -> None:
        """Record job completion metadata."""
        now_iso = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO executed_jobs (job_id, model_name, status, executed_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, model_name, status, now_iso),
            )
            conn.commit()

    def prune_old_jobs(self, retention_days: int = 7) -> int:
        """Remove metadata entries older than the retention window."""
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM executed_jobs WHERE executed_at < ?", (cutoff,))
            pruned = cursor.rowcount
            conn.commit()
            return pruned
