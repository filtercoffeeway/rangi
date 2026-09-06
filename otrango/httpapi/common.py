"""Shared response helpers for otrango.httpapi. Delivery only: parse
requests, call a use case, render the result. No orchestration and no
business rules live here -- those are in otrango.usecase, where they can
be tested without a web server.
"""
from __future__ import annotations

import json
from typing import Any

from flask import Response

from otrango.sse.broker import _json_default


def write_json(data: Any, status: int = 200) -> Response:
    body = json.dumps(data, default=_json_default)
    return Response(body, status=status, mimetype="application/json")


def write_err(msg: str, status: int = 400) -> Response:
    return write_json({"error": msg}, status)


def parse_limit(v: str) -> int:
    try:
        n = int(v)
    except ValueError:
        raise ValueError("not a number")
    if n <= 0 or n > 500:
        return 50
    return n


def to_jsonable(v: Any) -> Any:
    """Runs a value through the same to_json() convention write_json uses,
    for building nested response bodies by hand.
    """
    to_json = getattr(v, "to_json", None)
    if callable(to_json):
        return to_json()
    return v
