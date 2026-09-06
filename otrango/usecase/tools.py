from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from otrango import objective
from otrango.objective.mandate import Decision, LineItem, Proposal


class ToolsMixin:
    """See otrango.usecase.service.Service.

    HandleTool runs one LLM-callable tool against a live call and returns
    the JSON the model should see.

    It takes a tool name and raw arguments rather than a provider
    envelope, so the eval harness drives exactly this code path without a
    web server or a telephony account in the way.

    This sits on the conversational critical path -- the caller hears
    silence until it returns -- so nothing here calls an LLM or a provider
    API.
    """

    def handle_tool(self, provider_call_id: str, name: str, args: Any) -> str:
        parsed = _unwrap_args(args)
        err: Optional[Exception] = None
        out = ""
        try:
            out = self._dispatch_tool(provider_call_id, name, parsed)
        except Exception as e:
            err = e

        # Surface the negotiation as it happens. publish is non-blocking by
        # contract, so this cannot add latency to a path the caller hears.
        self.pub.publish("tool", {
            "provider_call_id": provider_call_id,
            "name": name,
            "args": parsed,
            "result": out,
            "error": str(err) if err else "",
        })
        if err is not None:
            raise err
        return out

    def _dispatch_tool(self, provider_call_id: str, name: str, args: dict[str, Any]) -> str:
        if name == "get_call_context":
            return self._tool_context(provider_call_id)
        if name == "propose_order":
            return self._tool_propose(provider_call_id, args)
        if name == "escalate":
            return self._tool_escalate(provider_call_id, args)
        if name == "record_outcome":
            return self._tool_record_outcome(provider_call_id, args)
        if name == "endCall":
            # Vapi implements endCall itself and never routes it here, so
            # this branch exists for the eval substrate, where every tool
            # the model calls comes through this dispatch. Without it the
            # prompt instructs a hang-up the evals answer with "unknown
            # tool", and T0 stops testing the agent that actually ships.
            return '{"ok":true}'
        raise ValueError(f"unknown tool {name!r}")

    def _tool_context(self, provider_call_id: str) -> str:
        m = self.mandates.for_call(provider_call_id)
        # Deliberately omits the spending limit. Telling the model the cap
        # lets it pre-judge a price and decline without ever calling
        # propose_order, which bypasses the enforcement point entirely --
        # the server stops being the one that decides. The model's job is
        # to report what was quoted; the limit is ours to apply.
        return json.dumps({
            "objective": m.objective_type,
            "spec": m.spec,
            "substitutions": m.constraints.allow_substitutions,
            "dry_run": m.constraints.dry_run,
        })

    def _tool_propose(self, provider_call_id: str, args: dict[str, Any]) -> str:
        items = [LineItem.from_json(i) for i in (args.get("items") or [])]
        total_cents = int(args.get("total_cents") or 0)
        ready_at_s = args.get("ready_at") or ""
        notes = args.get("notes") or ""

        m = self.mandates.for_call(provider_call_id)

        p = Proposal(items=items, total_cents=total_cents, notes=notes)
        if ready_at_s:
            try:
                p.ready_at = parse_loose_time(ready_at_s)
            except ValueError:
                pass

        # Generic limits first -- price cap, deadline, expiry, dry run --
        # decided by code that knows nothing about coffee. The vertical's
        # own rules run second.
        decision = m.evaluate(p, self.now())
        if decision.approved:
            try:
                sk = self.skills.get(m.objective_type)
                sk.validate_proposal(m, p)
            except KeyError:
                pass
            except Exception as verr:
                decision = Decision(
                    approved=False, reason=str(verr),
                    failure_class=objective.FAIL_ITEM_UNAVAILABLE,
                )
        if not decision.approved:
            self.log.warning(
                "proposal rejected provider_call_id=%s reason=%s class=%s",
                provider_call_id, decision.reason, decision.failure_class,
            )

        return json.dumps({
            "approved": decision.approved,
            "reason": decision.reason,
            "guidance": _guidance_for(decision),
        })

    def _tool_escalate(self, provider_call_id: str, args: dict[str, Any]) -> str:
        reason = args.get("reason") or ""
        self.log.info("escalation provider_call_id=%s reason=%s", provider_call_id, reason)
        return json.dumps({
            "action": "abort_politely",
            "message": "Apologise, say you'll call back to confirm, and end the call now.",
        })

    def _tool_record_outcome(self, provider_call_id: str, args: dict[str, Any]) -> str:
        result = args.get("result") or ""
        confidence = args.get("confidence") or ""
        failure_class = args.get("failure_class") or ""
        items = args.get("items") or []
        total_cents = int(args.get("total_cents") or 0)
        ready_at_s = args.get("ready_at") or ""
        confirmation_ref = args.get("confirmation_ref") or ""
        notes = args.get("notes") or ""

        call = self.calls.by_provider_id(provider_call_id)

        o = objective.Outcome(
            call_id=call.id,
            result=_normalize_result(result),
            confidence=_normalize_confidence(confidence),
            failure_class=failure_class.upper(),
            confirmation_ref=confirmation_ref,
            notes=notes,
        )
        if items:
            o.items = items
        if total_cents > 0:
            o.total_cents = total_cents
        if ready_at_s:
            try:
                o.ready_at = parse_loose_time(ready_at_s)
            except ValueError:
                pass

        # A restaurant has no API, so a success is still a claim about the
        # world and needs something concrete behind it. An order number is
        # the strongest evidence, but small local shops rarely issue one --
        # requiring it would flag every genuine order, and a flag that
        # fires every time is one nobody reads. A stated pickup time is the
        # same kind of evidence: a commitment the counterparty would not
        # make about an order they had not taken.
        o.needs_review = (
            o.result != objective.RESULT_SUCCESS
            or o.confidence != objective.CONF_HIGH
            or (not o.confirmation_ref and o.ready_at is None)
        )

        self.outcomes.record(o)
        self.publish_call(call.id)
        self.notify_outcome(call.id)

        return '{"ok":true,"guidance":"Outcome recorded. Thank them and end the call now."}'


def _guidance_for(d: Decision) -> str:
    if d.approved and d.price_unknown:
        return (
            "Approved. No price was quoted, which is fine — we pay at the counter. "
            "Do not ask them for one. Confirm the order and get the pickup time."
        )
    if d.approved:
        return "Confirm the order with the other party, then call record_outcome."
    return (
        "You may NOT confirm this order. Tell the other party politely that you "
        "cannot proceed, call record_outcome, and end the call."
    )


# normalize_result never promotes an unrecognised value to success: a false
# success sends the owner to collect coffee that was never ordered.
def _normalize_result(v: str) -> str:
    w = v.strip().lower()
    if w in ("success", "ok", "confirmed"):
        return objective.RESULT_SUCCESS
    if w == "partial":
        return objective.RESULT_PARTIAL
    if w in ("failed", "failure"):
        return objective.RESULT_FAILED
    if w == "escalated":
        return objective.RESULT_ESCALATED
    if w in ("refused", "declined"):
        return objective.RESULT_REFUSED
    if w == "unreachable":
        return objective.RESULT_UNREACH
    return objective.RESULT_AMBIGUOUS


def _normalize_confidence(v: str) -> str:
    w = v.strip().lower()
    if w == "high":
        return objective.CONF_HIGH
    if w in ("medium", "med"):
        return objective.CONF_MEDIUM
    return objective.CONF_LOW


def _unwrap_args(raw: Any) -> dict[str, Any]:
    """Accepts a dict already, a JSON object as text, or a JSON-encoded
    string holding one.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        trimmed = raw.strip()
        if trimmed == "":
            return {}
        if trimmed.startswith('"'):
            try:
                inner = json.loads(trimmed)
                if isinstance(inner, str):
                    trimmed = inner
            except ValueError:
                pass
        try:
            parsed = json.loads(trimmed)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


# -- time parsing ---------------------------------------------------------
#
# A pickup time is always ahead of the call that agreed it. now() here uses
# the real wall clock (not the Service's injectable clock) -- this mirrors
# the Go original, where these are free functions, not Service methods --
# but is kept timezone-aware (UTC) rather than the machine's local zone, so
# it compares cleanly against the UTC-aware deadlines the skills build.

def _now() -> datetime:
    return datetime.now(timezone.utc)


_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty-five": 45, "sixty": 60, "half": 30,
}


def parse_relative(v: str) -> Optional[datetime]:
    """Handles what a counter actually says: "ten minutes", "about 5 min",
    "half an hour". Clock times are the exception on these calls, not the
    rule, and failing to parse this leaves the outcome without the one
    piece of evidence that an order was really taken.
    """
    f = v.lower()
    if "min" not in f and "hour" not in f:
        return None
    unit = timedelta(minutes=1)
    if "hour" in f:
        unit = timedelta(hours=1)
    for w in re.split(r"[ ,~]+", f):
        w = w.strip(".")
        if not w:
            continue
        if w.lstrip("-").isdigit():
            n = int(w)
            if 0 < n < 240:
                return _now() + unit * n
        if w in _NUMBER_WORDS:
            # "half an hour" is 30 minutes, not 30 hours.
            if w == "half":
                return _now() + timedelta(minutes=30)
            return _now() + unit * _NUMBER_WORDS[w]
    return None


_JUST_PASSED = timedelta(minutes=15)


def resolve_clock(now: datetime, hour: int, minute: int, ambiguous: bool) -> datetime:
    """Places a wall-clock reading on the calendar. A pickup time is always
    ahead of the call that agreed it, so the answer is the next moment the
    clock reads that way: at 16:30 "five o'clock" is 17:00 today, and at
    22:00 it is 05:00 tomorrow -- which the mandate's completion deadline
    will then reject, as it should.

    `ambiguous` says whether the hour still needs an am/pm decision. "5:15"
    does; "5:15 PM" and "17:00" do not, and forcing a choice on them would
    move a time the counterparty had already been explicit about.
    """
    hours = [hour % 12, hour % 12 + 12] if ambiguous else [hour]
    cutoff = now - _JUST_PASSED

    best: Optional[datetime] = None
    for day in (0, 1):
        for h in hours:
            t = now.replace(hour=h, minute=minute, second=0, microsecond=0) + timedelta(days=day)
            if t >= cutoff and (best is None or t < best):
                best = t
    return best


_RE_YMD_HM = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})$")
_RE_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
_RE_HM = re.compile(r"^(\d{1,2}):(\d{2})$")
_RE_AMPM_COLON = re.compile(r"^(\d{1,2}):(\d{2})\s?(AM|PM)$")
_RE_AMPM_BARE = re.compile(r"^(\d{1,2})\s?(AM|PM)$")


def _to_24h(h12: int, meridiem: str) -> int:
    h = h12 % 12
    if meridiem == "PM":
        h += 12
    return h


def parse_loose_time(v: str) -> datetime:
    """Accepts what a model actually emits: a relative wait, RFC3339, or a
    bare clock time like "4:15 PM" resolved against today.
    """
    v = v.strip()
    rel = parse_relative(v)
    if rel is not None:
        return rel

    # "five o'clock" reaches the tool as "5 o'clock" or "5 oclock" often
    # enough to be worth handling; the phrase carries no information the
    # hour does not.
    low = v.lower()
    for suffix in ("o'clock", "o clock", "oclock"):
        if low.endswith(suffix):
            v = low[: -len(suffix)].strip()
            low = v
            break

    # "5.05 pm" -- a dot for the colon. Common in speech-to-text. Normalised
    # here, ahead of the layouts, so the hour still reaches the branch that
    # decides whether it is ambiguous; substituting after that branch would
    # read "5.05" as 5am.
    if v.count(".") == 1 and ":" not in v:
        v = v.replace(".", ":", 1)

    m = _RE_RFC3339.match(v)
    if m:
        y, mo, d, h, mi, s, off = m.groups()
        if off == "Z":
            tz = timezone.utc
        else:
            sign = 1 if off[0] == "+" else -1
            oh, om = int(off[1:3]), int(off[4:6])
            tz = timezone(sign * timedelta(hours=oh, minutes=om))
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=tz)

    m = _RE_YMD_HM.match(v)
    if m:
        y, mo, d, h, mi = (int(x) for x in m.groups())
        return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)

    m = _RE_HM.match(v)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            # An hour past 12 can only be 24-hour notation. Below that the
            # clock face alone does not say which half of the day it is.
            return resolve_clock(_now(), h, mi, h < 13)

    upper = v.upper()
    m = _RE_AMPM_COLON.match(upper)
    if m:
        h, mi, mer = int(m.group(1)), int(m.group(2)), m.group(3)
        return resolve_clock(_now(), _to_24h(h, mer), mi, False)
    m = _RE_AMPM_BARE.match(upper)
    if m:
        h, mer = int(m.group(1)), m.group(2)
        return resolve_clock(_now(), _to_24h(h, mer), 0, False)

    # A bare hour -- "5". Nothing above matches it, and dropping it costs
    # the outcome the pickup time, which is the only evidence a small shop
    # gives that an order was taken at all.
    if re.fullmatch(r"-?\d+", v):
        h = int(v)
        if 0 <= h <= 23:
            return resolve_clock(_now(), h, 0, h < 13)

    raise ValueError(f"unrecognised time {v!r}")
