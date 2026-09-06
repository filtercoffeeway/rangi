"""Small helpers so our dataclasses serialize the way the Go structs did:
``omitempty`` semantics (drop ``None`` fields) and RFC3339 timestamps with a
``Z`` suffix rather than Python's default ``+00:00``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def iso(dt: datetime | None) -> str | None:
    """Render a datetime the way Go's encoding/json renders time.Time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = dt.astimezone(timezone.utc).isoformat()
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def omit_none(d: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None — the omitempty behaviour for pointer
    fields. A field that is legitimately false/0/"" in Go's *non*-pointer
    fields stays; only pointer (Optional) fields in our dataclasses use this.
    """
    return {k: v for k, v in d.items() if v is not None}
