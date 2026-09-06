from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from otrango import objective
from otrango.jsonutil import iso, parse_iso
from otrango.store.errors import ErrNotFound

_CALL_COLS = (
    "id, mandate_id, provider, provider_call_id, to_number, status, "
    "end_reason, recording_url, transcript, cost_cents, voice_profile, "
    "created_at, started_at, ended_at"
)


def _row_to_call(row: tuple) -> objective.Call:
    (id_, mandate_id, provider, provider_call_id, to_number, status,
     end_reason, recording_url, transcript, cost_cents, voice_profile,
     created_at, started_at, ended_at) = row
    return objective.Call(
        id=id_, mandate_id=mandate_id, provider=provider,
        provider_call_id=provider_call_id, to_number=to_number, status=status,
        end_reason=end_reason, recording_url=recording_url, transcript=transcript,
        cost_cents=cost_cents, voice_profile=voice_profile,
        created_at=parse_iso(created_at), started_at=parse_iso(started_at),
        ended_at=parse_iso(ended_at),
    )


class CallsMixin:
    """See otrango.store.store.Store. Mirrors internal/store/calls.go."""

    def create_call(self, c: objective.Call) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO calls (id, to_number, status, provider, voice_profile) "
                "VALUES (?, ?, ?, ?, ?)",
                (c.id, c.to_number, c.status, "vapi", c.voice_profile),
            )
            row = self.db.execute(
                "SELECT created_at FROM calls WHERE id = ?", (c.id,)
            ).fetchone()
            if row is not None:
                c.created_at = parse_iso(row[0])

    def attach_provider_call_id(self, id_: str, provider_call_id: str) -> None:
        """Links our row to the provider's call once dialing starts."""
        with self.lock:
            self.db.execute(
                "UPDATE calls SET provider_call_id = ?, status = 'dialing' WHERE id = ?",
                (provider_call_id, id_),
            )

    def mark_failed(self, id_: str, reason: str) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE calls SET status = 'failed', end_reason = ?, ended_at = ? WHERE id = ?",
                (reason, iso(datetime.now().astimezone()), id_),
            )

    def update_call_by_provider_id(self, provider_call_id: str, u: objective.CallUpdate) -> None:
        with self.lock:
            self.db.execute(
                """
                UPDATE calls SET
                    status        = COALESCE(?, status),
                    end_reason    = COALESCE(?, end_reason),
                    recording_url = COALESCE(?, recording_url),
                    transcript    = COALESCE(?, transcript),
                    cost_cents    = COALESCE(?, cost_cents),
                    started_at    = COALESCE(?, started_at),
                    ended_at      = COALESCE(?, ended_at)
                WHERE provider_call_id = ?
                """,
                (u.status, u.end_reason, u.recording_url, u.transcript,
                 u.cost_cents, iso(u.started_at), iso(u.ended_at), provider_call_id),
            )

    def get_call(self, id_: str) -> objective.Call:
        with self.lock:
            row = self.db.execute(
                f"SELECT {_CALL_COLS} FROM calls WHERE id = ?", (id_,)
            ).fetchone()
        if row is None:
            raise ErrNotFound()
        return _row_to_call(row)

    def get_call_by_provider_id(self, provider_call_id: str) -> objective.Call:
        with self.lock:
            row = self.db.execute(
                f"SELECT {_CALL_COLS} FROM calls WHERE provider_call_id = ?", (provider_call_id,)
            ).fetchone()
        if row is None:
            raise ErrNotFound()
        return _row_to_call(row)

    def list_calls(self, limit: int) -> list[objective.Call]:
        with self.lock:
            rows = self.db.execute(
                f"SELECT {_CALL_COLS} FROM calls ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_call(r) for r in rows]
