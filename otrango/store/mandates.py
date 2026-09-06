from __future__ import annotations

import json
from datetime import datetime

from otrango import objective
from otrango.jsonutil import iso, parse_iso
from otrango.objective.mandate import Constraints
from otrango.store.errors import ErrExpired, ErrNotAuthorized, ErrNotFound

_MANDATE_COLS = (
    "id, user_id, objective_type, target_phone, spec, constraints, "
    "status, source, created_at, authorized_at, expires_at"
)


def _row_to_mandate(row: tuple) -> objective.Mandate:
    (id_, user_id, objective_type, target_phone, spec, constraints,
     status, source, created_at, authorized_at, expires_at) = row
    return objective.Mandate(
        id=id_, user_id=user_id, objective_type=objective_type, target_phone=target_phone,
        spec=json.loads(spec), constraints=Constraints.from_json(json.loads(constraints)),
        status=status, source=source, created_at=parse_iso(created_at),
        authorized_at=parse_iso(authorized_at), expires_at=parse_iso(expires_at),
    )


class MandatesMixin:
    """See otrango.store.store.Store. Mirrors internal/store/mandates.go."""

    def create_mandate(self, m: objective.Mandate) -> None:
        cons = json.dumps(m.constraints.to_json())
        with self.lock:
            self.db.execute("BEGIN")
            try:
                # A new draft supersedes any prior one for this user, so
                # "Y" in the chat always binds to the newest (PLAN.md
                # S7.3b).
                if m.status == objective.STATUS_DRAFT:
                    self.db.execute(
                        "UPDATE mandates SET status = 'superseded' "
                        "WHERE user_id = ? AND status = 'draft'",
                        (m.user_id,),
                    )
                self.db.execute(
                    f"INSERT INTO mandates ({_MANDATE_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (m.id, m.user_id, m.objective_type, m.target_phone,
                     json.dumps(m.spec), cons, m.status, m.source,
                     iso(m.created_at), iso(m.authorized_at), iso(m.expires_at)),
                )
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def get_mandate(self, id_: str) -> objective.Mandate:
        with self.lock:
            row = self.db.execute(
                f"SELECT {_MANDATE_COLS} FROM mandates WHERE id = ?", (id_,)
            ).fetchone()
        if row is None:
            raise ErrNotFound()
        return _row_to_mandate(row)

    def latest_draft(self, user_id: str) -> objective.Mandate:
        """Finds the mandate a bare "Y" should authorize."""
        with self.lock:
            row = self.db.execute(
                f"SELECT {_MANDATE_COLS} FROM mandates "
                "WHERE user_id = ? AND status = 'draft' ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        if row is None:
            raise ErrNotFound()
        return _row_to_mandate(row)

    def authorize_mandate(self, id_: str, at: datetime) -> None:
        """Moves draft -> authorized. The WHERE clause carries the
        expected status so two concurrent authorizations cannot both win.
        """
        with self.lock:
            cur = self.db.execute(
                "UPDATE mandates SET status = 'authorized', authorized_at = ? "
                "WHERE id = ? AND status = 'draft'",
                (iso(at), id_),
            )
            if cur.rowcount == 0:
                raise ErrNotAuthorized()

    def consume_mandate(self, id_: str, now: datetime) -> objective.Mandate:
        """Atomically claims an authorized, unexpired mandate for a call.
        Compare-and-set in effect: a mandate can back exactly one call,
        even if two dial requests arrive together (guarded further by the
        lock, which serializes the whole read-check-write).
        """
        with self.lock:
            m = self._get_mandate_locked(id_)
            if now > m.expires_at:
                self.db.execute(
                    "UPDATE mandates SET status = 'expired' WHERE id = ? AND status = 'authorized'",
                    (id_,),
                )
                raise ErrExpired()
            cur = self.db.execute(
                "UPDATE mandates SET status = 'consumed' WHERE id = ? AND status = 'authorized'",
                (id_,),
            )
            if cur.rowcount == 0:
                raise ErrNotAuthorized()
            m.status = objective.STATUS_CONSUMED
            return m

    def _get_mandate_locked(self, id_: str) -> objective.Mandate:
        row = self.db.execute(
            f"SELECT {_MANDATE_COLS} FROM mandates WHERE id = ?", (id_,)
        ).fetchone()
        if row is None:
            raise ErrNotFound()
        return _row_to_mandate(row)

    def mandate_for_call(self, provider_call_id: str) -> objective.Mandate:
        """Resolves the authority a live call is operating under, so tool
        endpoints can evaluate proposals against it.
        """
        with self.lock:
            row = self.db.execute(
                """
                SELECT m.id, m.user_id, m.objective_type, m.target_phone, m.spec, m.constraints,
                       m.status, m.source, m.created_at, m.authorized_at, m.expires_at
                FROM mandates m JOIN calls c ON c.mandate_id = m.id
                WHERE c.provider_call_id = ?
                """,
                (provider_call_id,),
            ).fetchone()
        if row is None:
            raise ErrNotFound()
        return _row_to_mandate(row)

    def attach_mandate(self, call_id: str, mandate_id: str) -> None:
        with self.lock:
            self.db.execute("UPDATE calls SET mandate_id = ? WHERE id = ?", (mandate_id, call_id))

    def supersede_draft(self, id_: str) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE mandates SET status = 'superseded' WHERE id = ? AND status = 'draft'",
                (id_,),
            )

    def latest_call_needing_review(self):
        """Backs a bare "N" in the chat: the most recent call whose outcome
        the owner has not yet confirmed.

        Columns are qualified with `c.` -- unlike the unqualified column
        list used elsewhere, this query joins against outcomes, which also
        has a created_at column, and an unqualified reference to it is
        ambiguous.
        """
        from otrango.store.calls import _row_to_call

        cols = ("c.id, c.mandate_id, c.provider, c.provider_call_id, c.to_number, c.status, "
                "c.end_reason, c.recording_url, c.transcript, c.cost_cents, c.voice_profile, "
                "c.created_at, c.started_at, c.ended_at")
        with self.lock:
            row = self.db.execute(
                f"SELECT {cols} FROM calls c "
                "JOIN outcomes o ON o.call_id = c.id "
                "WHERE o.user_verdict IS NULL "
                "ORDER BY c.created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise ErrNotFound()
        return _row_to_call(row)
