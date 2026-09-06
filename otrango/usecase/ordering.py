from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from otrango import objective
from otrango.usecase.ports import CallRequest
from otrango.voice import voice as voicepkg

# MANDATE_TTL bounds how long an authorization stays good. A mandate is
# permission to spend money on someone's behalf; it should go stale.
MANDATE_TTL = timedelta(minutes=30)

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class ErrNoTarget(Exception):
    def __init__(self):
        super().__init__("target phone must be E.164, e.g. +14155551234")


class ErrNotDraft(Exception):
    def __init__(self):
        super().__init__("mandate is not a draft (already used, expired, or superseded)")


class ErrNoSuchDraft(Exception):
    def __init__(self):
        super().__init__("nothing pending")


@dataclass
class DraftRequest:
    """Field names are load-bearing: the console and the README both send
    snake_case ("dry_run"), and this is what the JSON request body is
    decoded into.
    """

    input: str = ""
    to: str = ""
    dry_run: bool = False
    voice: str = ""

    @staticmethod
    def from_json(d: dict[str, Any]) -> "DraftRequest":
        return DraftRequest(
            input=d.get("input") or "",
            to=d.get("to") or "",
            dry_run=bool(d.get("dry_run", False)),
            voice=d.get("voice") or "",
        )


class OrderingMixin:
    """See otrango.usecase.service.Service."""

    def draft(self, user_id: str, req: DraftRequest, src: str) -> objective.Mandate:
        """Turns a trigger phrase into a mandate in draft. No authority
        exists yet and nothing is dialed -- that requires an explicit
        authorize.
        """
        skill = self.skills.resolve(req.input)
        spec, cons = skill.build(req.input)
        cons.dry_run = req.dry_run
        cons.defaults()

        # Resolved here rather than at dial time: an unknown voice should
        # fail while drafting, not after the mandate has been authorized
        # and spent.
        profile = voicepkg.get(self.voices, req.voice)
        cons.voice = profile.name

        # Precedence: an explicit override, then the place the skill
        # resolved from the owner's preferences, then global config. The
        # skill wins over config because where an order goes is a property
        # of what was ordered.
        to = req.to
        if not to:
            target = getattr(skill, "target", None)
            if target is not None:
                to = target(spec)
        if not to:
            to = self.default_target
        if not to or not _E164.match(to):
            raise ErrNoTarget()

        now = self.now()
        m = objective.Mandate(
            id=str(uuid.uuid4()),
            user_id=user_id,
            objective_type=skill.type(),
            target_phone=to,
            spec=spec,
            constraints=cons,
            status=objective.STATUS_DRAFT,
            source=src,
            created_at=now,
            expires_at=now + MANDATE_TTL,
        )
        self.mandates.create(m)
        self.pub.publish("mandate.created", m)
        return m

    def authorize_and_dial(self, mandate_id: str) -> objective.Call:
        """One step on purpose. Splitting them would leave a window in
        which authority exists but nothing is using it, and an unused
        authorized mandate is a standing permission to spend money.
        """
        try:
            self.mandates.authorize(mandate_id, self.now())
        except Exception:
            raise ErrNotDraft()
        return self.dial(mandate_id)

    def dial(self, mandate_id: str) -> objective.Call:
        """Consumes the mandate, then places the call. Consumption happens
        first: if the process dies mid-dial the authority is spent, which
        is the safe direction -- a lost call is recoverable, a double
        order is not.
        """
        m = self.mandates.consume(mandate_id, self.now())
        skill = self.skills.get(m.objective_type)
        profile = voicepkg.get(self.voices, m.constraints.voice)

        call = objective.Call(
            id=str(uuid.uuid4()), to_number=m.target_phone,
            status=objective.CALL_QUEUED, voice_profile=profile.name,
        )
        self.calls.create(call)
        self.calls.attach_mandate(call.id, m.id)
        self.pub.publish("call.created", call)

        try:
            provider_id = self.caller.place(CallRequest(
                to=m.target_phone,
                greeting=skill.greeting(m),
                system_prompt=skill.prompt(m),
                max_duration_sec=m.constraints.max_duration_sec,
                voice=profile,
            ))
        except Exception as e:
            self.calls.mark_failed(call.id, objective.FAIL_PROVIDER_ERROR)
            self.outcomes.record(objective.Outcome(
                call_id=call.id,
                result=objective.RESULT_FAILED,
                confidence=objective.CONF_HIGH,
                failure_class=objective.FAIL_PROVIDER_ERROR,
                notes=str(e),
                needs_review=True,
            ))
            self.publish_call(call.id)
            raise RuntimeError(f"could not place the call: {e}") from e

        try:
            self.calls.attach_provider_id(call.id, provider_id)
        except Exception as e:
            self.log.error("attach provider id call_id=%s err=%s", call.id, e)
        self.log.info(
            "dialing call_id=%s mandate_id=%s objective=%s dry_run=%s voice=%s",
            call.id, m.id, m.objective_type, m.constraints.dry_run, profile.name,
        )
        self.publish_call(call.id)

        try:
            return self.calls.get(call.id)
        except Exception:
            return call

    def list_calls(self, limit: int) -> list[objective.Call]:
        return self.calls.list(limit)

    def get_call(self, id: str) -> objective.Call:
        return self.calls.get(id)

    def get_mandate(self, id: str) -> objective.Mandate:
        return self.mandates.get(id)
