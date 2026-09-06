from __future__ import annotations

import sqlite3
from typing import Optional

from otrango import objective
from otrango.jsonutil import parse_iso


def _row_to_event(row: tuple) -> objective.Event:
    id_, call_id, kind, payload, received_at = row
    return objective.Event(id=id_, call_id=call_id, kind=kind, payload=payload,
                            received_at=parse_iso(received_at))


class EventsMixin:
    """See otrango.store.store.Store. Mirrors internal/store/events.go."""

    def record_event(self, provider_event_id: str, kind: str, payload: str,
                      call_id: Optional[str]) -> bool:
        """Inserts a webhook event. Returns False (without error) when
        provider_event_id has been seen before -- that is the idempotency
        guarantee, and it is enforced by the UNIQUE constraint rather than
        a read-then-write, so concurrent duplicate deliveries cannot both
        win.
        """
        with self.lock:
            try:
                self.db.execute(
                    "INSERT INTO events (provider_event_id, call_id, kind, payload) "
                    "VALUES (?, ?, ?, ?)",
                    (provider_event_id, call_id, kind, payload),
                )
            except sqlite3.IntegrityError:
                return False
            return True

    def list_events(self, call_id: str, limit: int) -> list[objective.Event]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id, call_id, kind, payload, received_at "
                "FROM events WHERE call_id = ? ORDER BY id ASC LIMIT ?",
                (call_id, limit),
            ).fetchall()
        return [_row_to_event(r) for r in rows]

    def count_events(self) -> int:
        """Exists so the Phase 0 replay test can assert on it directly."""
        with self.lock:
            row = self.db.execute("SELECT COUNT(*) FROM events").fetchone()
        return row[0]
