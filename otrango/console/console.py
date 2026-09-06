"""Package console serves the embedded operator UI (PLAN.md S7.3a).

It ships inside the package directory so there is no second process, no
separate deploy and no CORS. The console is the debugging surface for
every later phase.
"""
from __future__ import annotations

import functools
import os

_INDEX_PATH = os.path.join(os.path.dirname(__file__), "index.html")


@functools.lru_cache(maxsize=1)
def read_index() -> str:
    with open(_INDEX_PATH, "r", encoding="utf-8") as f:
        return f.read()
