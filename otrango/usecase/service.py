from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from otrango.skills.skill import Registry as SkillRegistry
from otrango.usecase.messaging import MessagingMixin
from otrango.usecase.ordering import OrderingMixin
from otrango.usecase.outcome import OutcomeMixin
from otrango.usecase.ports import (
    Calls,
    Caller,
    Clock,
    Events,
    Mandates,
    Notifier,
    Outcomes,
    Publisher,
    system_clock,
)
from otrango.usecase.reconcile import ReconcileMixin
from otrango.usecase.tools import ToolsMixin
from otrango.voice.voice import Registry as VoiceRegistry


class _NoopNotifier:
    def notify(self, body: str) -> None:
        return None


class _NoopPublisher:
    def publish(self, kind: str, payload: Any) -> None:
        return None


@dataclass
class Deps:
    """The constructor's argument so adding a dependency does not silently
    reorder an existing call site.
    """

    mandates: Mandates
    calls: Calls
    outcomes: Outcomes
    events: Events
    skills: SkillRegistry
    caller: Optional[Caller] = None
    notifier: Optional[Notifier] = None
    pub: Optional[Publisher] = None
    voices: Optional[VoiceRegistry] = None
    now: Optional[Clock] = None
    log: Optional[logging.Logger] = None
    owner_user_id: str = ""
    default_target: str = ""


class Service(OrderingMixin, OutcomeMixin, MessagingMixin, ReconcileMixin, ToolsMixin):
    """The application. Every field is a protocol or a domain type, so the
    whole layer can be exercised with fakes -- no SQLite file, no HTTP
    server, no provider account.

    Split across mixins the way the Go original split one struct's methods
    across ordering.go / outcome.go / messaging.go / reconcile.go /
    tools.go -- each file below owns one concern.
    """

    def __init__(self, d: Deps):
        self.mandates = d.mandates
        self.calls = d.calls
        self.outcomes = d.outcomes
        self.events = d.events
        self.caller = d.caller
        self.notifier: Notifier = d.notifier or _NoopNotifier()
        self.pub: Publisher = d.pub or _NoopPublisher()
        self.skills = d.skills
        self.voices = d.voices
        self.now: Clock = d.now or system_clock
        self.log = d.log or logging.getLogger("otrango.usecase.noop")
        if d.log is None:
            self.log.addHandler(logging.NullHandler())
            self.log.propagate = False

        self.owner_user_id = d.owner_user_id or "owner"
        self.default_target = d.default_target

    def publish_call(self, id: str) -> None:
        """Re-reads before broadcasting so every console converges on
        persisted state rather than on whatever the caller happened to
        hold.
        """
        try:
            c = self.calls.get(id)
        except Exception:
            return
        self.pub.publish("call.updated", c)
