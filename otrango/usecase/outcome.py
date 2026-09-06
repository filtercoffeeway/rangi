from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from otrango import objective
from otrango.turns import metrics as turns


@dataclass
class CallDetail:
    """Everything the console shows for one call."""

    call: objective.Call
    events: list[objective.Event]
    outcome: Optional[objective.Outcome] = None
    metrics: Optional[turns.Metrics] = None
    mandate: Optional[objective.Mandate] = None

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {"call": self.call.to_json()}
        if self.events:
            d["events"] = [e.to_json() for e in self.events]
        if self.outcome is not None:
            d["outcome"] = self.outcome.to_json()
        if self.metrics is not None:
            d["metrics"] = self.metrics.to_json()
        if self.mandate is not None:
            d["mandate"] = self.mandate.to_json()
        return d


class OutcomeMixin:
    """See otrango.usecase.service.Service."""

    def finalize_call(self, call_id: str, ended_reason: str, transcript: str,
                       transcript_turns: Any) -> None:
        """Derives turn metrics and guarantees a terminal record.

        The agent is supposed to call record_outcome before hanging up, but
        a call that deadlocks, is cut off, or ends mid-sentence never
        reaches that tool. Recording is write-once, so a real outcome
        always wins and this only fills genuine gaps. The transcript is
        passed in because the end reason alone cannot tell a voicemail box
        from a shopkeeper hanging up.
        """
        if transcript_turns:
            parsed = turns.parse_vapi_messages(transcript_turns)
            if parsed:
                m = turns.compute(parsed)
                try:
                    self.outcomes.save_metrics(call_id, m)
                except Exception as e:
                    self.log.error("save turn metrics call_id=%s err=%s", call_id, e)
                else:
                    if not m.healthy(0):
                        # The agent-to-agent pathologies are invisible in
                        # transcript text and only show up in timing, so
                        # they are surfaced explicitly.
                        self.log.warning(
                            "agent-to-agent pathology call_id=%s dead_air=%s "
                            "barge_ins=%s gap_p95_ms=%s turns=%s",
                            call_id, m.dead_air_events, m.barge_in_count,
                            m.gap_p95_ms, m.turn_count,
                        )

        result, klass, from_transcript = objective.Classify(ended_reason, transcript)
        notes = "call ended without the agent recording an outcome"
        if from_transcript:
            notes = "answered by a recording, not a person"
        try:
            wrote = self.outcomes.record(objective.Outcome(
                call_id=call_id, result=result, confidence=objective.CONF_LOW,
                failure_class=klass, notes=notes, needs_review=True,
            ))
        except Exception as e:
            self.log.error("backstop outcome call_id=%s err=%s", call_id, e)
            return
        if wrote:
            self.log.warning(
                "no outcome from agent; recorded backstop call_id=%s ended_reason=%s class=%s",
                call_id, ended_reason, klass,
            )
            self.notify_outcome(call_id)

    def notify_outcome(self, call_id: str) -> None:
        """Closes the loop with the owner. Safe to call more than once per
        call: the store guards against a duplicate terminal record, and
        this only reports what is already stored.
        """
        try:
            o = self.outcomes.get(call_id)
            call = self.calls.get(call_id)
            if call.mandate_id is None:
                return
            m = self.mandates.get(call.mandate_id)
            skill = self.skills.get(m.objective_type)
        except Exception:
            return
        try:
            self.notifier.notify(skill.summary(m, o))
        except Exception as e:
            self.log.error("notify call_id=%s err=%s", call_id, e)

    def record_verdict(self, call_id: str, verdict: str) -> None:
        self.outcomes.set_verdict(call_id, verdict, self.now())
        self.publish_call(call_id)

    def call_detail(self, id: str) -> CallDetail:
        call = self.calls.get(id)
        d = CallDetail(call=call, events=[])
        try:
            d.events = self.events.list(id, 200)
        except Exception:
            pass
        try:
            d.outcome = self.outcomes.get(id)
        except Exception:
            pass
        try:
            d.metrics = self.outcomes.get_metrics(id)
        except Exception:
            pass
        if call.mandate_id is not None:
            try:
                d.mandate = self.mandates.get(call.mandate_id)
            except Exception:
                pass
        return d

    def apply_call_update(self, provider_call_id: str, u: objective.CallUpdate) -> objective.Call:
        """Records provider state changes against a call."""
        self.calls.update(provider_call_id, u)
        c = self.calls.by_provider_id(provider_call_id)
        self.pub.publish("call.updated", c)
        return c

    def record_event(self, provider_event_id: str, kind: str, payload: str,
                      call_id: Optional[str]) -> bool:
        """The idempotency gate for provider webhooks."""
        return self.events.record(provider_event_id, kind, payload, call_id)
