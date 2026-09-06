"""Package sse fans out call-state changes to connected consoles."""
from __future__ import annotations

import json
import queue
import threading
from typing import Any


def _json_default(o: Any) -> Any:
    """Lets a payload be a domain dataclass (Call, Mandate, ...) with its
    own to_json(), the same way Go's json.Marshal reads a struct's tags.
    """
    to_json = getattr(o, "to_json", None)
    if callable(to_json):
        return to_json()
    raise TypeError(f"not JSON serializable: {o!r}")


class Broker:
    def __init__(self):
        self._lock = threading.RLock()
        self._subs: set["queue.Queue[bytes]"] = set()

    def subscribe(self) -> "queue.Queue[bytes]":
        ch: "queue.Queue[bytes]" = queue.Queue(maxsize=16)
        with self._lock:
            self._subs.add(ch)
        return ch

    def unsubscribe(self, ch: "queue.Queue[bytes]") -> None:
        with self._lock:
            self._subs.discard(ch)

    def publish(self, kind: str, payload: Any) -> None:
        """Never blocks. A console that cannot keep up drops frames rather
        than stalling the webhook handler -- the webhook path is on a
        latency budget (PLAN.md S3.1) and must not be held hostage by a
        slow browser.
        """
        try:
            msg = json.dumps({"kind": kind, "data": payload}, default=_json_default).encode("utf-8")
        except (TypeError, ValueError):
            return
        with self._lock:
            subs = list(self._subs)
        for ch in subs:
            try:
                ch.put_nowait(msg)
            except queue.Full:
                pass  # subscriber is behind; drop

    def count(self) -> int:
        with self._lock:
            return len(self._subs)
