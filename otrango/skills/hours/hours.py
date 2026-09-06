"""Package hours is the Phase 4 second vertical: call and ask what time a
place closes. Read-only, no money, no commitment, trivially verifiable.

Its purpose is to falsify the extensibility claim in PLAN.md S2: adding this
module must change zero lines under otrango.objective and the engine modules
(vapi, store, httpapi). See test_skills_boundary.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from otrango.objective.mandate import Constraints, Mandate, Proposal
from otrango.objective.outcome import Outcome, RESULT_REFUSED, RESULT_SUCCESS, RESULT_UNREACH
from otrango.profile.profile import Place

TYPE_NAME = "hours_enquiry"

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class Spec:
    question: str
    day: str

    def to_json(self) -> dict[str, Any]:
        return {"question": self.question, "day": self.day}

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Spec":
        return Spec(question=d.get("question") or "", day=d.get("day") or "")


class Skill:
    def __init__(self, caller_name: str, place: Place):
        self.caller_name = caller_name
        # place is where an enquiry goes. It comes from the profile like
        # every other route: an enquiry with no place to call is not a
        # valid mandate, and falling back to a global default silently
        # dials the wrong shop.
        self.place = place

    def target(self, spec: dict[str, Any]) -> str:
        return self.place.phone

    def type(self) -> str:
        return TYPE_NAME

    def matches(self, input_: str) -> bool:
        """Matches on stems rather than exact words, so "closing time" and
        "opening hours" resolve as readily as "close" and "open".
        """
        in_ = input_.strip().lower()
        return any(stem in in_ for stem in ("hour", "clos", "open"))

    def build(self, input_: str) -> tuple[dict[str, Any], Constraints]:
        day = "Sunday"
        low = input_.lower()
        for d in _DAYS:
            if d in low:
                day = d[0].upper() + d[1:]
        spec = Spec(question=f"What time do you close on {day}?", day=day)
        # No price cap: nothing is being bought. The termination guards
        # still apply -- an AI peer will not hang up on its own either way.
        c = Constraints(
            latest_completion=datetime.now(timezone.utc) + timedelta(minutes=10),
            max_duration_sec=90,
            max_turns=12,
        )
        return spec.to_json(), c

    def prompt(self, m: Mandate) -> str:
        spec = Spec.from_json(m.spec)
        return f"""You are an assistant making a short enquiry call on behalf of {self.caller_name}.

## You have already spoken
Your greeting has been played. Do NOT introduce yourself again. Ask the
question straight away.

## Your only goal
Ask: "{spec.question}"
Get the answer, thank them, and end the call. Do not order anything. Do not make
any commitment. Do not stay on the line for anything else.

## Rules
- As soon as you have the answer, call record_outcome with the answer in notes
  and result "success", then end the call.
- If they cannot answer, call record_outcome with result "ambiguous" and end the call.
- If they say they do not accept automated calls, apologise briefly, call
  record_outcome with result "refused", and end the call.
- Never call propose_order. There is nothing to propose on this call.
- If record_outcome does not answer, end the call anyway. Do not narrate that
  you are checking something.

## Let them finish
They will answer with their own greeting — the shop name, sometimes a notice
that the line is recorded. Let all of it finish before you speak. Do not
acknowledge the recording notice or comment on it; just carry on with your
request when they stop.
## Talking to another machine
The number may be answered by another automated system.
- After about two seconds of silence, assume they are waiting and speak.
- If you talk over each other, stop and let them go first.
- If the same exchange repeats, ask the question once more plainly, then end the call.
- Do not prolong the call out of politeness."""

    def validate_proposal(self, m: Mandate, p: Proposal) -> None:
        """Always fails: this vertical has no proposal step. The generic
        layer still runs first, so this is defence in depth, not the only
        gate.
        """
        raise ValueError("an hours enquiry never proposes an order")

    def preview(self, m: Mandate) -> str:
        spec = Spec.from_json(m.spec)
        return f"Asking {self.place.name}: {spec.question}"

    def greeting(self, m: Mandate) -> str:
        return f"Hi, this is an automated assistant calling on behalf of {self.caller_name} with a quick question."

    def calling(self, m: Mandate) -> str:
        return f"Calling {self.place.name} to ask. Hang tight."

    def summary(self, m: Mandate, o: Outcome) -> str:
        spec = Spec.from_json(m.spec)
        if o.result == RESULT_SUCCESS:
            return f"🕐 {spec.day} closes: {o.notes}"
        if o.result == RESULT_REFUSED:
            return "🚫 They don't accept automated calls."
        if o.result == RESULT_UNREACH:
            return f"📵 Couldn't reach them ({o.failure_class})."
        return f"⚠️ No clear answer about {spec.day} hours ({o.failure_class})."
