from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from otrango import objective
from otrango.jsonutil import iso, parse_iso
from otrango.store.errors import ErrNotFound
from otrango.turns.metrics import Metrics

_OUTCOME_COLS = (
    "call_id, result, confidence, failure_class, items, total_cents, "
    "ready_at, confirmation_ref, notes, needs_review, user_verified_at, "
    "user_verdict, created_at"
)


def _row_to_outcome(row: tuple) -> objective.Outcome:
    (call_id, result, confidence, failure_class, items, total_cents,
     ready_at, confirmation_ref, notes, needs_review, user_verified_at,
     user_verdict, created_at) = row
    return objective.Outcome(
        call_id=call_id, result=result, confidence=confidence,
        failure_class=failure_class or "", items=json.loads(items) if items else None,
        total_cents=total_cents, ready_at=parse_iso(ready_at),
        confirmation_ref=confirmation_ref or "", notes=notes or "",
        needs_review=bool(needs_review), user_verified_at=parse_iso(user_verified_at),
        user_verdict=user_verdict or "", created_at=parse_iso(created_at),
    )


class OutcomesMixin:
    """See otrango.store.store.Store. Mirrors internal/store/outcomes.go."""

    def record_outcome(self, o: objective.Outcome) -> bool:
        """Writes the terminal record. It is idempotent on call_id: the
        first terminal record wins, so a late webhook cannot overwrite
        what the agent reported during the call, and the reconciler cannot
        clobber a real outcome.
        """
        items = json.dumps(o.items) if o.items else None
        with self.lock:
            cur = self.db.execute(
                f"INSERT OR IGNORE INTO outcomes ({_OUTCOME_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (o.call_id, o.result, o.confidence, o.failure_class or None, items,
                 o.total_cents, iso(o.ready_at), o.confirmation_ref or None,
                 o.notes or None, 1 if o.needs_review else 0, iso(o.user_verified_at),
                 o.user_verdict or None, iso(datetime.now().astimezone())),
            )
            return cur.rowcount > 0

    def get_outcome(self, call_id: str) -> objective.Outcome:
        with self.lock:
            row = self.db.execute(
                f"SELECT {_OUTCOME_COLS} FROM outcomes WHERE call_id = ?", (call_id,)
            ).fetchone()
        if row is None:
            raise ErrNotFound()
        return _row_to_outcome(row)

    def set_user_verdict(self, call_id: str, verdict: str, at: datetime) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE outcomes SET user_verdict = ?, user_verified_at = ?, needs_review = 0 "
                "WHERE call_id = ?",
                (verdict, iso(at), call_id),
            )

    def stale_calls(self, before: datetime, limit: int) -> list[objective.Call]:
        """Finds calls that started before `before` and never reached a
        terminal outcome -- the reconciler's input. Without this, a crash
        or a dropped webhook leaves a call in limbo forever and the user
        never learns what happened.
        """
        from otrango.store.calls import _CALL_COLS, _row_to_call

        with self.lock:
            rows = self.db.execute(
                f"""
                SELECT {_CALL_COLS} FROM calls c
                WHERE c.created_at < ?
                  AND c.status NOT IN ('failed')
                  AND NOT EXISTS (SELECT 1 FROM outcomes o WHERE o.call_id = c.id)
                ORDER BY c.created_at ASC LIMIT ?
                """,
                (iso(before), limit),
            ).fetchall()
        return [_row_to_call(r) for r in rows]

    def save_turn_metrics(self, call_id: str, m: Metrics) -> None:
        with self.lock:
            self.db.execute(
                """
                INSERT OR REPLACE INTO turn_metrics
                (call_id, turn_count, gap_p50_ms, gap_p95_ms, gap_max_ms, overlap_ms,
                 barge_in_count, dead_air_events, time_to_outcome_ms, computed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (call_id, m.turn_count, m.gap_p50_ms, m.gap_p95_ms, m.gap_max_ms,
                 m.overlap_ms, m.barge_in_count, m.dead_air_events, m.time_to_outcome_ms,
                 iso(datetime.now().astimezone())),
            )

    def get_turn_metrics(self, call_id: str) -> Metrics:
        with self.lock:
            row = self.db.execute(
                """
                SELECT turn_count, gap_p50_ms, gap_p95_ms, gap_max_ms, overlap_ms,
                       barge_in_count, dead_air_events, time_to_outcome_ms
                FROM turn_metrics WHERE call_id = ?
                """,
                (call_id,),
            ).fetchone()
        if row is None:
            raise ErrNotFound()
        (turn_count, gap_p50, gap_p95, gap_max, overlap, barge_ins, dead_air, tto) = row
        return Metrics(
            turn_count=turn_count, gap_p50_ms=gap_p50, gap_p95_ms=gap_p95,
            gap_max_ms=gap_max, overlap_ms=overlap, barge_in_count=barge_ins,
            dead_air_events=dead_air, time_to_outcome_ms=tto,
        )
