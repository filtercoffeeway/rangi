from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from otrango.jsonutil import iso, omit_none

RESULT_SUCCESS = "success"
RESULT_PARTIAL = "partial"
RESULT_FAILED = "failed"
RESULT_ESCALATED = "escalated"
RESULT_REFUSED = "refused"
RESULT_UNREACH = "unreachable"
RESULT_AMBIGUOUS = "ambiguous"

CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"

# FailureClass is the taxonomy from PLAN.md S5.2. The agent-to-agent group
# exists only because the counterparty is a bot; it would be empty if a
# human answered the phone.

# Reachability.
FAIL_NO_ANSWER = "NO_ANSWER"
FAIL_BUSY = "BUSY"
FAIL_VOICEMAIL = "VOICEMAIL"
FAIL_IVR_TRAPPED = "IVR_TRAPPED"
FAIL_CLOSED = "CLOSED"
FAIL_WRONG_NUMBER = "WRONG_NUMBER"

# Negotiation.
FAIL_ITEM_UNAVAILABLE = "ITEM_UNAVAILABLE"
FAIL_PRICE_EXCEEDED = "PRICE_EXCEEDED"
FAIL_PEER_REFUSED = "PEER_REFUSED_AUTOMATION"
FAIL_LANGUAGE = "LANGUAGE_BARRIER"

# Agent-to-agent (PLAN.md S3.2).
FAIL_DEADLOCK = "TURN_TAKING_DEADLOCK"
FAIL_MUTUAL_BARGE_IN = "MUTUAL_BARGE_IN"
FAIL_POLITENESS_LOOP = "POLITENESS_LOOP"
FAIL_TURN_CAP = "TURN_CAP_EXCEEDED"

# System.
FAIL_MANDATE_EXPIRED = "MANDATE_EXPIRED"
FAIL_CALL_TIMEOUT = "CALL_TIMEOUT"
FAIL_PROVIDER_ERROR = "PROVIDER_ERROR"
FAIL_AMBIGUOUS = "AMBIGUOUS_OUTCOME"

# Not a failure: the mandate was a dry run and the agent stopped short.
FAIL_DRY_RUN = "DRY_RUN_ABORTED"


@dataclass
class Outcome:
    """The terminal record for a call. `needs_review` defaults true because
    a restaurant has no API: "the counterparty agreed" is a claim about the
    world, not a confirmed write (PLAN.md S1).
    """

    call_id: str
    result: str
    confidence: str
    failure_class: str = ""
    items: list[dict[str, Any]] | None = None
    total_cents: int | None = None
    ready_at: datetime | None = None
    confirmation_ref: str = ""
    notes: str = ""
    needs_review: bool = True
    user_verified_at: datetime | None = None
    user_verdict: str = ""
    created_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        d = omit_none({
            "call_id": self.call_id,
            "result": self.result,
            "confidence": self.confidence,
            "failure_class": self.failure_class or None,
            "items": self.items or None,
            "total_cents": self.total_cents,
            "ready_at": iso(self.ready_at),
            "confirmation_ref": self.confirmation_ref or None,
            "notes": self.notes or None,
            "user_verified_at": iso(self.user_verified_at),
            "user_verdict": self.user_verdict or None,
            "created_at": iso(self.created_at),
        })
        d["needs_review"] = self.needs_review
        return d


def _contains(s: str, *subs: str) -> bool:
    s = s.lower()
    return any(sub and sub.lower() in s for sub in subs)


def classify_end_reason(reason: str) -> tuple[str, str]:
    """Maps a provider hangup reason onto our taxonomy. Anything
    unrecognised becomes AMBIGUOUS rather than success -- a false success
    means the user waits for coffee that is not coming.
    """
    if reason == "":
        return RESULT_AMBIGUOUS, FAIL_AMBIGUOUS
    # Providers spell this several ways: "no-answer", "noanswer",
    # "customer-did-not-answer". Missing one files a genuine no-answer as
    # ambiguous, which loses the reason we already knew.
    if _contains(reason, "no-answer", "noanswer", "did-not-answer", "didnotanswer"):
        return RESULT_UNREACH, FAIL_NO_ANSWER
    if _contains(reason, "busy"):
        return RESULT_UNREACH, FAIL_BUSY
    if _contains(reason, "voicemail"):
        return RESULT_UNREACH, FAIL_VOICEMAIL
    if _contains(reason, "max-duration", "exceeded-max-duration", "timeout"):
        return RESULT_AMBIGUOUS, FAIL_CALL_TIMEOUT
    if _contains(reason, "customer-ended", "assistant-ended", "hangup"):
        return RESULT_AMBIGUOUS, FAIL_AMBIGUOUS
    if _contains(reason, "error", "failed", "pipeline"):
        return RESULT_FAILED, FAIL_PROVIDER_ERROR
    return RESULT_AMBIGUOUS, FAIL_AMBIGUOUS


# Phrases only a machine says. A voicemail box and a carrier announcement
# both answer the line, so the provider reports a normal connected call; the
# only evidence that no person was ever reached is what was said.
_VOICEMAIL_PHRASES = [
    "voicemail", "voice mail", "leave a message", "leave your message",
    "after the tone", "after the beep", "at the tone", "record your message",
]

# Carrier announcements. Deliberately whole phrases: "not available" or
# "busy" on their own are things a person behind a counter says.
_UNREACHABLE_PHRASES = [
    "trying to reach is not available", "is not answering",
    "unable to take your call", "cannot be completed",
    "switched off", "out of coverage", "not reachable",
]


def machine_greeting(transcript: str) -> tuple[str, bool]:
    """Reports whether a transcript reads as a recording rather than a
    person, and which kind.
    """
    if _contains(transcript, *_VOICEMAIL_PHRASES):
        return FAIL_VOICEMAIL, True
    if _contains(transcript, *_UNREACHABLE_PHRASES):
        return FAIL_NO_ANSWER, True
    return "", False


def classify(ended_reason: str, transcript: str) -> tuple[str, str, bool]:
    """Combines how the provider says the call ended with what was said on
    it, and reports whether the transcript was what decided it.

    The end reason wins whenever it is specific. It is consulted first and
    the transcript only breaks a tie, because a call that reaches voicemail
    is answered as far as telephony is concerned: Vapi calls it
    "customer-ended-call" and we filed it as AMBIGUOUS_OUTCOME -- "unclear
    whether the order went through" -- when nobody had heard the order at
    all.
    """
    result, klass = classify_end_reason(ended_reason)
    if klass != FAIL_AMBIGUOUS:
        return result, klass, False
    c, ok = machine_greeting(transcript)
    if ok:
        return RESULT_UNREACH, c, True
    return result, klass, False


# Aliases matching the Go names used elsewhere in this port.
ClassifyEndReason = classify_end_reason
MachineGreeting = machine_greeting
Classify = classify
