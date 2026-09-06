"""Package usecase holds the application's orchestration: what happens when
an order is drafted, authorized, dialed, negotiated and reported.

It depends on the domain (objective, turns, skills) and on the interfaces
below -- never on SQLite, Vapi, Telegram or Flask. Those are supplied by the
caller. This is the layer that used to live inside otrango.httpapi, where it
could not be exercised without standing up a web server.

The interfaces are declared here as typing.Protocol -- Python's structural
equivalent of Go's consumer-declared interfaces -- so an adapter can be
swapped without the use cases knowing anything changed. Nothing here is
enforced at runtime (Python has no compile-time interface check); the fakes
in tests simply implement the same methods.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

from otrango import objective
from otrango.turns.metrics import Metrics
from otrango.voice.voice import Profile as VoiceProfile


class Mandates(Protocol):
    """Persists grants of authority. authorize and consume are
    compare-and-set by contract: an implementation that lets two callers
    consume the same mandate would allow one order to be placed twice.
    """

    def create(self, m: objective.Mandate) -> None: ...
    def get(self, id: str) -> objective.Mandate: ...
    def latest_draft(self, user_id: str) -> objective.Mandate: ...
    def authorize(self, id: str, at: datetime) -> None: ...
    def consume(self, id: str, now: datetime) -> objective.Mandate: ...
    def supersede(self, id: str) -> None: ...
    def for_call(self, provider_call_id: str) -> objective.Mandate: ...


class Calls(Protocol):
    def create(self, c: objective.Call) -> None: ...
    def get(self, id: str) -> objective.Call: ...
    def by_provider_id(self, provider_call_id: str) -> objective.Call: ...
    def list(self, limit: int) -> list[objective.Call]: ...
    def attach_provider_id(self, call_id: str, provider_call_id: str) -> None: ...
    def attach_mandate(self, call_id: str, mandate_id: str) -> None: ...
    def mark_failed(self, call_id: str, reason: str) -> None: ...
    def update(self, provider_call_id: str, u: objective.CallUpdate) -> None: ...
    def stale(self, before: datetime, limit: int) -> list[objective.Call]: ...
    def latest_needing_review(self) -> objective.Call: ...


class Outcomes(Protocol):
    """Write-once on call_id: record reports whether it actually wrote, so
    a late webhook or the reconciler cannot overwrite what the agent said
    during the call.
    """

    def record(self, o: objective.Outcome) -> bool: ...
    def get(self, call_id: str) -> objective.Outcome: ...
    def set_verdict(self, call_id: str, verdict: str, at: datetime) -> None: ...
    def save_metrics(self, call_id: str, m: Metrics) -> None: ...
    def get_metrics(self, call_id: str) -> Metrics: ...


class Events(Protocol):
    """The idempotent webhook sink. record returns False for a delivery
    already seen.
    """

    def record(self, provider_event_id: str, kind: str, payload: str,
               call_id: Optional[str]) -> bool: ...
    def list(self, call_id: str, limit: int) -> list[objective.Event]: ...


@dataclass
class CallRequest:
    to: str
    greeting: str
    system_prompt: str
    max_duration_sec: int
    voice: VoiceProfile


class Caller(Protocol):
    """Places outbound calls. The only thing the use cases know about a
    telephony provider.
    """

    def place(self, req: CallRequest) -> str: ...
    def lookup(self, provider_call_id: str) -> str: ...


class Notifier(Protocol):
    """Delivers an outcome to the owner."""

    def notify(self, body: str) -> None: ...


class Publisher(Protocol):
    """Fans state changes out to connected consoles. Best-effort by
    contract: it must never block the caller, because tool endpoints sit on
    the conversational critical path.
    """

    def publish(self, kind: str, payload: Any) -> None: ...


# Clock exists so time-dependent rules -- expiry, staleness -- are testable
# without sleeping.
Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(timezone.utc)
