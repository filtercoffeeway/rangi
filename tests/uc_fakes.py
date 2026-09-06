"""In-memory ports for otrango.usecase tests -- the Python mirror of
fakes_test.go. The point of the layering: every rule is exercised with no
SQLite file, no HTTP server and no provider account.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable, Optional

from otrango import objective
from otrango.turns.metrics import Metrics
from otrango.usecase.ports import CallRequest
from otrango.usecase.service import Deps, Service
from otrango.skills import default as default_skills
from otrango.voice.voice import NAME_AGENT, NAME_MINE, Profile as VoiceProfile, Registry as VoiceRegistry


class FakeMandates:
    def __init__(self):
        self._lock = threading.Lock()
        self.m: dict[str, objective.Mandate] = {}
        self.for_call_fn: Optional[Callable[[str], objective.Mandate]] = None

    def create(self, m: objective.Mandate) -> None:
        with self._lock:
            # Mirrors the partial unique index: one live draft per user.
            if m.status == objective.STATUS_DRAFT:
                for e in self.m.values():
                    if e.user_id == m.user_id and e.status == objective.STATUS_DRAFT:
                        e.status = objective.STATUS_SUPERSEDED
            self.m[m.id] = replace(m)

    def get(self, id: str) -> objective.Mandate:
        with self._lock:
            if id not in self.m:
                raise KeyError("not found")
            return replace(self.m[id])

    def latest_draft(self, user_id: str) -> objective.Mandate:
        with self._lock:
            best = None
            for m in self.m.values():
                if m.user_id == user_id and m.status == objective.STATUS_DRAFT:
                    if best is None or m.created_at > best.created_at:
                        best = m
            if best is None:
                raise KeyError("not found")
            return replace(best)

    def authorize(self, id: str, at: datetime) -> None:
        with self._lock:
            m = self.m.get(id)
            if m is None or m.status != objective.STATUS_DRAFT:
                raise ValueError("not a draft")
            m.status, m.authorized_at = objective.STATUS_AUTHORIZED, at

    def consume(self, id: str, now: datetime) -> objective.Mandate:
        with self._lock:
            m = self.m.get(id)
            if m is None:
                raise KeyError("not found")
            if now > m.expires_at:
                m.status = objective.STATUS_EXPIRED
                raise ValueError("expired")
            if m.status != objective.STATUS_AUTHORIZED:
                raise ValueError("not authorized")
            m.status = objective.STATUS_CONSUMED
            return replace(m)

    def supersede(self, id: str) -> None:
        with self._lock:
            m = self.m.get(id)
            if m is not None and m.status == objective.STATUS_DRAFT:
                m.status = objective.STATUS_SUPERSEDED

    def for_call(self, provider_call_id: str) -> objective.Mandate:
        if self.for_call_fn is None:
            raise KeyError("not found")
        return self.for_call_fn(provider_call_id)


class FakeCalls:
    def __init__(self):
        self._lock = threading.Lock()
        self.c: dict[str, objective.Call] = {}

    def create(self, c: objective.Call) -> None:
        with self._lock:
            if c.created_at is None:
                c.created_at = datetime.now(timezone.utc)
            self.c[c.id] = replace(c)

    def get(self, id: str) -> objective.Call:
        with self._lock:
            if id not in self.c:
                raise KeyError("not found")
            return replace(self.c[id])

    def by_provider_id(self, pid: str) -> objective.Call:
        with self._lock:
            for c in self.c.values():
                if c.provider_call_id == pid:
                    return replace(c)
            raise KeyError("not found")

    def list(self, limit: int) -> list[objective.Call]:
        with self._lock:
            return [replace(c) for c in self.c.values()]

    def attach_provider_id(self, call_id: str, pid: str) -> None:
        with self._lock:
            c = self.c.get(call_id)
            if c is not None:
                c.provider_call_id = pid
                c.status = objective.CALL_DIALING

    def attach_mandate(self, call_id: str, mandate_id: str) -> None:
        with self._lock:
            c = self.c.get(call_id)
            if c is not None:
                c.mandate_id = mandate_id

    def mark_failed(self, call_id: str, reason: str) -> None:
        with self._lock:
            c = self.c.get(call_id)
            if c is not None:
                c.status, c.end_reason = objective.CALL_FAILED, reason

    def update(self, pid: str, u: objective.CallUpdate) -> None:
        return None

    def stale(self, before: datetime, limit: int) -> list[objective.Call]:
        with self._lock:
            return [replace(c) for c in self.c.values()
                    if c.status != objective.CALL_FAILED and c.created_at < before]

    def latest_needing_review(self) -> objective.Call:
        with self._lock:
            for c in self.c.values():
                return replace(c)
            raise KeyError("not found")


class FakeOutcomes:
    def __init__(self):
        self._lock = threading.Lock()
        self.o: dict[str, objective.Outcome] = {}
        self.m: dict[str, Metrics] = {}

    def record(self, o: objective.Outcome) -> bool:
        with self._lock:
            if o.call_id in self.o:
                return False
            cp = replace(o)
            cp.created_at = datetime.now(timezone.utc)
            self.o[o.call_id] = cp
            return True

    def get(self, call_id: str) -> objective.Outcome:
        with self._lock:
            if call_id not in self.o:
                raise KeyError("not found")
            return replace(self.o[call_id])

    def set_verdict(self, call_id: str, verdict: str, at: datetime) -> None:
        with self._lock:
            o = self.o.get(call_id)
            if o is not None:
                o.user_verdict, o.user_verified_at, o.needs_review = verdict, at, False

    def save_metrics(self, call_id: str, m: Metrics) -> None:
        with self._lock:
            self.m[call_id] = m

    def get_metrics(self, call_id: str) -> Metrics:
        with self._lock:
            if call_id not in self.m:
                raise KeyError("not found")
            return self.m[call_id]


class FakeEvents:
    def __init__(self):
        self.n = 0

    def record(self, id: str, kind: str, payload: str, call_id: Optional[str]) -> bool:
        self.n += 1
        return True

    def list(self, call_id: str, limit: int) -> list[objective.Event]:
        return []


class FakeCaller:
    def __init__(self):
        self.placed: list[CallRequest] = []
        self.err: Optional[Exception] = None
        self.ended = ""
        self.lookups = 0

    def place(self, req: CallRequest) -> str:
        if self.err is not None:
            raise self.err
        self.placed.append(req)
        return "prov-" + str(uuid.uuid4())

    def lookup(self, id: str) -> str:
        self.lookups += 1
        return self.ended


class FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []

    def notify(self, body: str) -> None:
        self.sent.append(body)


@dataclass
class Fixture:
    svc: Service
    mandates: FakeMandates
    calls: FakeCalls
    outcomes: FakeOutcomes
    caller: FakeCaller
    notifier: FakeNotifier
    now: datetime


def new_fixture() -> Fixture:
    mandates = FakeMandates()
    calls = FakeCalls()
    outcomes = FakeOutcomes()
    caller = FakeCaller()
    notifier = FakeNotifier()
    now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)

    # for_call spans two repos, so resolve it through the call store.
    def for_call(pid: str) -> objective.Mandate:
        c = calls.by_provider_id(pid)
        if c.mandate_id is None:
            raise KeyError("not found")
        return mandates.get(c.mandate_id)

    mandates.for_call_fn = for_call

    svc = Service(Deps(
        mandates=mandates, calls=calls, outcomes=outcomes,
        events=FakeEvents(), caller=caller, notifier=notifier,
        skills=default_skills("Tester", "Test Cafe", None),
        voices=VoiceRegistry(
            NAME_AGENT,
            VoiceProfile(name=NAME_AGENT, provider="p", voice_id="a"),
            VoiceProfile(name=NAME_MINE, provider="p", voice_id="b"),
        ),
        now=lambda: fx.now,
        owner_user_id="owner",
        default_target="+14155550100",
    ))
    fx = Fixture(svc=svc, mandates=mandates, calls=calls, outcomes=outcomes,
                 caller=caller, notifier=notifier, now=now)
    return fx
