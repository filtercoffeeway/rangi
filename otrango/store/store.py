"""Package store owns persistence. SQLite for now; the SQL is kept
dialect-neutral so a move to Postgres would mostly be a driver swap
(PLAN.md S4).
"""
from __future__ import annotations

import os
import sqlite3
import threading

from otrango.store.calls import CallsMixin
from otrango.store.events import EventsMixin
from otrango.store.mandates import MandatesMixin
from otrango.store.outcomes import OutcomesMixin

_SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schema")


class Store(CallsMixin, EventsMixin, MandatesMixin, OutcomesMixin):
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        # A single connection is used from multiple threads (the HTTP
        # server, the Telegram poller, the reconciler), so writes are
        # serialized here rather than relying on SQLite's own locking --
        # WAL mode lets reads proceed concurrently regardless.
        self.lock = threading.RLock()

    @staticmethod
    def open(path: str) -> "Store":
        # WAL lets concurrent reads proceed while a write is in flight;
        # busy_timeout absorbs the brief writer contention that remains.
        db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")

        s = Store(db)
        s._migrate()
        return s

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        with self.lock:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)"
            )
            names = sorted(f for f in os.listdir(_SCHEMA_DIR) if f.endswith(".sql"))
            for name in names:
                seen = self.db.execute(
                    "SELECT name FROM schema_migrations WHERE name = ?", (name,)
                ).fetchone()
                if seen is not None:
                    continue

                with open(os.path.join(_SCHEMA_DIR, name), "r", encoding="utf-8") as f:
                    body = f.read()

                self.db.execute("BEGIN")
                try:
                    for stmt in _split_statements(body):
                        self.db.execute(stmt)
                    self.db.execute(
                        "INSERT INTO schema_migrations (name) VALUES (?)", (name,)
                    )
                    self.db.execute("COMMIT")
                except Exception as e:
                    self.db.execute("ROLLBACK")
                    raise RuntimeError(f"{name}: {e}") from e


def _split_statements(body: str) -> list[str]:
    """Splits a schema file into individual statements on ";". Good enough
    for our schema files: plain DDL, no semicolons embedded in string
    literals.
    """
    return [s.strip() for s in body.split(";") if s.strip()]
