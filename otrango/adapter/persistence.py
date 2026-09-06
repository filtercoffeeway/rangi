"""Package adapter implements the usecase ports against concrete
infrastructure.

These are deliberately thin. Their whole job is to let the store keep names
that read well in SQL ("create_mandate") while the use cases talk in the
vocabulary of their own ports ("create"), without either side bending to the
other.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from otrango import objective
from otrango.store.store import Store
from otrango.turns.metrics import Metrics


class Mandates:
    def __init__(self, s: Store):
        self.s = s

    def create(self, m: objective.Mandate) -> None:
        self.s.create_mandate(m)

    def get(self, id: str) -> objective.Mandate:
        return self.s.get_mandate(id)

    def latest_draft(self, user_id: str) -> objective.Mandate:
        return self.s.latest_draft(user_id)

    def authorize(self, id: str, at: datetime) -> None:
        self.s.authorize_mandate(id, at)

    def consume(self, id: str, now: datetime) -> objective.Mandate:
        return self.s.consume_mandate(id, now)

    def supersede(self, id: str) -> None:
        self.s.supersede_draft(id)

    def for_call(self, provider_call_id: str) -> objective.Mandate:
        return self.s.mandate_for_call(provider_call_id)


class Calls:
    def __init__(self, s: Store):
        self.s = s

    def create(self, c: objective.Call) -> None:
        self.s.create_call(c)

    def get(self, id: str) -> objective.Call:
        return self.s.get_call(id)

    def by_provider_id(self, id: str) -> objective.Call:
        return self.s.get_call_by_provider_id(id)

    def list(self, limit: int) -> list[objective.Call]:
        return self.s.list_calls(limit)

    def attach_provider_id(self, call_id: str, provider_call_id: str) -> None:
        self.s.attach_provider_call_id(call_id, provider_call_id)

    def attach_mandate(self, call_id: str, mandate_id: str) -> None:
        self.s.attach_mandate(call_id, mandate_id)

    def mark_failed(self, call_id: str, reason: str) -> None:
        self.s.mark_failed(call_id, reason)

    def update(self, provider_call_id: str, u: objective.CallUpdate) -> None:
        self.s.update_call_by_provider_id(provider_call_id, u)

    def stale(self, before: datetime, limit: int) -> list[objective.Call]:
        return self.s.stale_calls(before, limit)

    def latest_needing_review(self) -> objective.Call:
        return self.s.latest_call_needing_review()


class Outcomes:
    def __init__(self, s: Store):
        self.s = s

    def record(self, o: objective.Outcome) -> bool:
        return self.s.record_outcome(o)

    def get(self, call_id: str) -> objective.Outcome:
        return self.s.get_outcome(call_id)

    def set_verdict(self, call_id: str, verdict: str, at: datetime) -> None:
        self.s.set_user_verdict(call_id, verdict, at)

    def save_metrics(self, call_id: str, m: Metrics) -> None:
        self.s.save_turn_metrics(call_id, m)

    def get_metrics(self, call_id: str) -> Metrics:
        return self.s.get_turn_metrics(call_id)


class Events:
    def __init__(self, s: Store):
        self.s = s

    def record(self, provider_event_id: str, kind: str, payload: str,
               call_id: Optional[str]) -> bool:
        return self.s.record_event(provider_event_id, kind, payload, call_id)

    def list(self, call_id: str, limit: int) -> list[objective.Event]:
        return self.s.list_events(call_id, limit)
