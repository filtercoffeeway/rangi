"""Mandate: a frozen grant of authority, and the constraint evaluation that
enforces it server-side (PLAN.md S5.3).

Phase 4's pass condition is that adding a second vertical changes nothing in
this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from otrango.jsonutil import iso, omit_none

STATUS_DRAFT = "draft"
STATUS_AUTHORIZED = "authorized"
STATUS_CONSUMED = "consumed"
STATUS_EXPIRED = "expired"
STATUS_SUPERSEDED = "superseded"

# Source is the channel an order arrived on, stored as an opaque string.
# Mandates predating the Telegram adapter carry "retired": the channel they
# arrived on no longer exists, and relabelling them as this one would be a
# false record of how a real call was triggered.
SOURCE_CONSOLE = "console"
SOURCE_TELEGRAM = "telegram"


@dataclass
class LineItem:
    """Deliberately generic: a name, a count, a unit price. A coffee order
    and a haircut booking are both lists of these.
    """

    name: str
    qty: int = 0
    unit_cents: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "qty": self.qty, "unit_cents": self.unit_cents}

    @staticmethod
    def from_json(d: dict[str, Any]) -> "LineItem":
        return LineItem(
            name=d.get("name") or "",
            qty=int(d.get("qty") or 0),
            unit_cents=int(d.get("unit_cents") or 0),
        )


@dataclass
class Proposal:
    """What the model says the counterparty agreed to, before we let it
    commit. Vertical-agnostic on purpose.
    """

    items: list[LineItem] = field(default_factory=list)
    total_cents: int = 0
    ready_at: datetime | None = None
    notes: str = ""


@dataclass
class Decision:
    approved: bool
    reason: str
    failure_class: str = ""
    # price_unknown means the counterparty never stated a total, so the cap
    # had nothing to check. The order is still approved -- small shops
    # routinely take an order without quoting -- but the owner is told the
    # limit did not apply.
    price_unknown: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "failure_class": self.failure_class,
            "price_unknown": self.price_unknown,
        }


def _approve(reason: str) -> Decision:
    return Decision(approved=True, reason=reason)


def _reject(fc: str, reason: str) -> Decision:
    return Decision(approved=False, reason=reason, failure_class=fc)


@dataclass
class Constraints:
    max_total_cents: int = 0
    latest_completion: datetime | None = None
    allow_substitutions: bool = False
    allowed_subs: list[str] = field(default_factory=list)

    # Termination guards. Against an AI counterparty these are the only
    # guaranteed way a call ends -- a bot will not hang up out of impatience
    # (PLAN.md S3.2).
    max_duration_sec: int = 0
    max_turns: int = 0

    # Dry run: conduct the whole conversation, then abort at the commit
    # point instead of confirming.
    dry_run: bool = False

    # Voice names the TTS profile this call should speak with.
    voice: str = ""

    def defaults(self) -> None:
        """Fills in guards the caller omitted. A zero termination guard is
        never allowed through -- that is the one field where "unset" is
        dangerous.
        """
        if self.max_duration_sec <= 0:
            self.max_duration_sec = 180
        if self.max_turns <= 0:
            self.max_turns = 40

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "max_total_cents": self.max_total_cents,
            "latest_completion": iso(self.latest_completion),
            "allow_substitutions": self.allow_substitutions,
            "max_duration_sec": self.max_duration_sec,
            "max_turns": self.max_turns,
            "dry_run": self.dry_run,
        }
        if self.allowed_subs:
            d["allowed_substitutions"] = self.allowed_subs
        if self.voice:
            d["voice"] = self.voice
        return d

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Constraints":
        from otrango.jsonutil import parse_iso

        return Constraints(
            max_total_cents=int(d.get("max_total_cents") or 0),
            latest_completion=parse_iso(d.get("latest_completion")),
            allow_substitutions=bool(d.get("allow_substitutions", False)),
            allowed_subs=list(d.get("allowed_substitutions") or []),
            max_duration_sec=int(d.get("max_duration_sec") or 0),
            max_turns=int(d.get("max_turns") or 0),
            dry_run=bool(d.get("dry_run", False)),
            voice=d.get("voice") or "",
        )


@dataclass
class Mandate:
    """A frozen grant of authority: this objective, this number, these
    limits, until this time. Created as a draft, authorized once, consumed
    once.
    """

    id: str
    user_id: str
    objective_type: str
    target_phone: str
    spec: dict[str, Any]
    constraints: Constraints
    status: str
    source: str
    created_at: datetime
    expires_at: datetime
    authorized_at: datetime | None = None

    def evaluate(self, p: Proposal, now: datetime) -> Decision:
        """The enforcement point. The model proposes; this function
        disposes. It is a plain comparison precisely so no amount of
        prompting, peer pressure, or hallucination can talk past it.
        """
        from otrango.objective.outcome import (
            FAIL_DRY_RUN,
            FAIL_MANDATE_EXPIRED,
            FAIL_PRICE_EXCEEDED,
        )

        if self.status not in (STATUS_AUTHORIZED, STATUS_CONSUMED):
            return _reject(FAIL_MANDATE_EXPIRED, f"mandate is {self.status}, not authorized")
        if now > self.expires_at:
            return _reject(FAIL_MANDATE_EXPIRED, f"mandate expired at {iso(self.expires_at)}")
        # No quoted price is normal, not an error. A local shop takes the
        # order and you pay at the counter; requiring a total would force
        # the agent either to ask (which nobody does) or to invent one
        # (which is worse). The cap simply has nothing to act on.
        if p.total_cents <= 0:
            return Decision(
                approved=True,
                reason="no price was quoted, so nothing to check against the limit",
                price_unknown=True,
            )
        c = self.constraints
        if c.max_total_cents > 0 and p.total_cents > c.max_total_cents:
            return _reject(
                FAIL_PRICE_EXCEEDED,
                f"quoted {money(p.total_cents)} exceeds the {money(c.max_total_cents)} cap",
            )
        if c.latest_completion is not None and p.ready_at is not None and p.ready_at > c.latest_completion:
            from otrango.timefmt import kitchen

            return _reject(
                FAIL_PRICE_EXCEEDED,
                f"ready at {kitchen(p.ready_at)}, later than the "
                f"{kitchen(c.latest_completion)} deadline",
            )
        if c.dry_run:
            return _reject(FAIL_DRY_RUN, "dry run: everything checks out, but do not commit")
        return _approve("within mandate")

    def substitution_allowed(self, item: str) -> bool:
        """Reports whether swapping in `item` is permitted. Kept here rather
        than in the skill because the *authority* to substitute is a
        property of the mandate; what counts as a sensible substitute is
        not.
        """
        if not self.constraints.allow_substitutions:
            return False
        if not self.constraints.allowed_subs:
            return True
        item = item.strip().lower()
        return any(s.strip().lower() == item for s in self.constraints.allowed_subs)

    def to_json(self) -> dict[str, Any]:
        return omit_none({
            "id": self.id,
            "user_id": self.user_id,
            "objective_type": self.objective_type,
            "target_phone": self.target_phone,
            "spec": self.spec,
            "constraints": self.constraints.to_json(),
            "status": self.status,
            "source": self.source,
            "created_at": iso(self.created_at),
            "authorized_at": iso(self.authorized_at),
            "expires_at": iso(self.expires_at),
        })


def money(cents: int) -> str:
    return f"${cents // 100}.{abs(cents) % 100:02d}"
