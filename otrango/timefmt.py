"""Small time-formatting helpers matching Go's stdlib layouts exactly, since
the wording in prompts and confirmations is meant to read like something a
person typed, not a locale-dependent strftime output.
"""
from __future__ import annotations

from datetime import datetime


def kitchen(dt: datetime) -> str:
    """Mirrors Go's time.Kitchen layout: "3:04PM" -- no leading zero on the
    hour, minute zero-padded, uppercase AM/PM with no space.
    """
    h = dt.hour % 12
    if h == 0:
        h = 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{h}:{dt.minute:02d}{ampm}"
