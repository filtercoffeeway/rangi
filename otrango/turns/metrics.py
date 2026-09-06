"""Package turns derives conversational timing metrics from a call
transcript (PLAN.md S3.3).

These exist because the counterparty is another voice agent. Two
endpointing algorithms negotiating over PSTN produce failure modes that do
not occur when a human answers -- deadlock, mutual barge-in, politeness
loops -- and none of them are visible in a transcript's text. They are only
visible in its timing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# The gap above which we consider the channel stalled: long enough that a
# human would say "hello?", and the point at which our deadlock breaker
# should speak rather than keep waiting.
DEAD_AIR_THRESHOLD_MS = 2500


@dataclass
class Metrics:
    turn_count: int = 0
    gap_p50_ms: int = 0
    gap_p95_ms: int = 0
    gap_max_ms: int = 0
    overlap_ms: int = 0
    barge_in_count: int = 0
    dead_air_events: int = 0
    time_to_outcome_ms: int = 0

    def healthy(self, max_turns: int) -> bool:
        """Reports whether the call showed none of the S3.2 pathologies.
        Used by the eval harness to assert on timing, not just on the
        outcome row.
        """
        return (
            self.dead_air_events == 0
            and self.barge_in_count <= 2
            and (max_turns <= 0 or self.turn_count <= max_turns)
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "turn_count": self.turn_count,
            "gap_p50_ms": self.gap_p50_ms,
            "gap_p95_ms": self.gap_p95_ms,
            "gap_max_ms": self.gap_max_ms,
            "overlap_ms": self.overlap_ms,
            "barge_in_count": self.barge_in_count,
            "dead_air_events": self.dead_air_events,
            "time_to_outcome_ms": self.time_to_outcome_ms,
        }


@dataclass
class Turn:
    """One party speaking, in seconds from the start of the call."""

    role: str
    start: float
    end: float


def _percentile(sorted_vals: list[int], p: float) -> int:
    if not sorted_vals:
        return 0
    idx = math.ceil(p * len(sorted_vals)) - 1
    idx = min(max(idx, 0), len(sorted_vals) - 1)
    return sorted_vals[idx]


def compute(ts: list[Turn]) -> Metrics:
    """Derives the metrics from an ordered turn list.

    A gap is measured only between turns by *different* speakers:
    consecutive turns from one side are one party continuing, not the
    channel waiting, and counting those as dead air would manufacture
    deadlocks that never happened.
    """
    m = Metrics()
    m.turn_count = len(ts)
    if not ts:
        return m

    gaps: list[int] = []
    last_end = 0.0
    last_role = ""
    have_last = False

    for t in ts:
        if have_last and t.role != last_role:
            delta_ms = round((t.start - last_end) * 1000)
            if delta_ms < 0:
                # Both talking: the new speaker started before the previous
                # one stopped. That is a barge-in, not a negative gap.
                m.overlap_ms += -delta_ms
                m.barge_in_count += 1
            else:
                gaps.append(delta_ms)
                if delta_ms > DEAD_AIR_THRESHOLD_MS:
                    m.dead_air_events += 1
        if t.end > last_end:
            last_end = t.end
        last_role, have_last = t.role, True

    if gaps:
        gaps.sort()
        m.gap_p50_ms = _percentile(gaps, 0.50)
        m.gap_p95_ms = _percentile(gaps, 0.95)
        m.gap_max_ms = gaps[-1]
    m.time_to_outcome_ms = round(last_end * 1000)
    return m


def parse_vapi_messages(raw: Any) -> list[Turn] | None:
    """Extracts turns from a Vapi end-of-call artifact. It is tolerant by
    design: field names vary across payload versions, and a metrics gap is
    worth far less than a failed webhook.
    """
    import json

    if isinstance(raw, (str, bytes)):
        try:
            msgs = json.loads(raw)
        except (ValueError, TypeError):
            return None
    else:
        msgs = raw
    if not isinstance(msgs, list):
        return None

    out: list[Turn] = []
    base = 0.0
    known = False
    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if role not in ("user", "assistant", "bot"):
            continue  # system / tool-call entries are not spoken turns
        if role == "bot":
            role = "assistant"

        start = float(msg.get("secondsFromStart") or 0)
        duration = float(msg.get("duration") or 0)
        end = start + duration / 1000

        # Fall back to absolute epoch timestamps, rebased on the first turn.
        t = float(msg.get("time") or 0)
        end_time = float(msg.get("endTime") or 0)
        if start == 0 and t > 0:
            if not known:
                base, known = t, True
            start = (t - base) / 1000
            if end_time > t:
                end = (end_time - base) / 1000
            else:
                end = start + duration / 1000
        if end < start:
            end = start
        out.append(Turn(role=role, start=start, end=end))
    return out
