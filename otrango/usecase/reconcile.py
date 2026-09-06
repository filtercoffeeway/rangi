from __future__ import annotations

import threading
from datetime import timedelta
from typing import Optional

from otrango import objective

# STALE_AFTER is how long a call may run without a terminal outcome before
# one is forced. Comfortably longer than any max_duration_sec.
STALE_AFTER = timedelta(minutes=15)


class ReconcileMixin:
    """See otrango.usecase.service.Service."""

    def reconcile(self, grace: Optional[timedelta] = None) -> int:
        """Forces a terminal outcome onto every call stuck without one, and
        returns how many it resolved.

        Webhooks arrive late, out of order, or not at all, and a process
        can die mid-call. Without this sweep those calls sit in limbo and
        the owner never learns what happened -- the worst failure
        available to a system whose job is being honest about what it does
        and does not know.
        """
        if not grace:
            grace = STALE_AFTER
        try:
            stale = self.calls.stale(self.now() - grace, 50)
        except Exception as e:
            self.log.error("stale call query err=%s", e)
            return 0

        resolved = 0
        for call in stale:
            notes = "no terminal outcome recorded; forced by reconciler"
            reason = ""
            transcript = call.transcript or ""

            # Ask the provider what happened before giving up: if it
            # knows, we get a real reason instead of a shrug.
            if call.provider_call_id and self.caller is not None:
                try:
                    r = self.caller.lookup(call.provider_call_id)
                    if r:
                        reason = r
                        notes = "reconciled from provider: " + r
                except Exception:
                    pass

            result, klass, from_transcript = objective.Classify(reason, transcript)
            if from_transcript:
                notes = "answered by a recording, not a person"

            try:
                wrote = self.outcomes.record(objective.Outcome(
                    call_id=call.id, result=result, confidence=objective.CONF_LOW,
                    failure_class=klass, notes=notes, needs_review=True,
                ))
            except Exception as e:
                self.log.error("reconcile outcome call_id=%s err=%s", call.id, e)
                continue
            if wrote:
                resolved += 1
                self.log.warning(
                    "forced terminal outcome call_id=%s result=%s class=%s",
                    call.id, result, klass,
                )
                self.notify_outcome(call.id)
        return resolved

    def run_reconciler(self, stop_event: threading.Event,
                        interval: Optional[timedelta] = None,
                        grace: Optional[timedelta] = None) -> None:
        """Sweeps on an interval until stop_event is set."""
        if not interval:
            interval = timedelta(minutes=2)
        while not stop_event.wait(interval.total_seconds()):
            n = self.reconcile(grace)
            if n > 0:
                self.log.warning("reconciled stale calls count=%s", n)
