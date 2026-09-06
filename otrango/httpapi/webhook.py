"""The Vapi webhook sink: partial and tolerant parsing (Vapi's payloads
are large and evolve; we decode only what we persist), constant-time
secret verification, and idempotent event recording.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from flask import Flask, request

from otrango import objective
from otrango.httpapi.common import write_err, write_json


def register(app: Flask, cfg, svc, broker, log) -> None:
    @app.post("/webhooks/vapi")
    def handle_vapi_webhook():
        if not _verify_webhook_secret(cfg, request):
            return write_err("bad or missing x-vapi-secret", 401)

        raw = request.get_data(cache=False, as_text=False)[: 4 << 20]
        try:
            env = json.loads(raw) if raw else {}
        except ValueError as e:
            # Still 200: a payload we cannot parse is our bug, and
            # retries will not fix it. Log loudly instead of making Vapi
            # redeliver forever.
            log.error("webhook decode err=%s body=%s", e, _truncate(raw, 500))
            return write_json({"status": "unparsed"})

        msg = env.get("message") or {}
        call_id = ((msg.get("call") or {}).get("id")) or None

        # Tool calls are synchronous and blocking: the caller hears dead
        # air until we answer, so they take the fast path and are never
        # deduplicated. A repeated tool call is the model genuinely asking
        # twice, not a redelivery.
        if msg.get("type") == "tool-calls":
            results = []
            for tc in msg.get("toolCallList") or []:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                args = fn.get("arguments")
                try:
                    out = svc.handle_tool(call_id, name, args)
                except Exception as e:
                    log.error("tool name=%s err=%s", name, e)
                    out = f"error: {e}"
                results.append({"toolCallId": tc.get("id"), "result": out})
            svc.record_event(_event_key(msg) + ":" + os.urandom(8).hex(), msg.get("type"),
                              raw.decode("utf-8", "replace"), call_id)
            return write_json({"results": results})

        inserted = svc.record_event(_event_key(msg), msg.get("type") or "",
                                     raw.decode("utf-8", "replace"), call_id)
        if not inserted:
            # Duplicate delivery. Acknowledge without reprocessing.
            log.debug("webhook duplicate type=%s provider_call_id=%s", msg.get("type"), call_id)
            return write_json({"status": "duplicate"})

        if call_id:
            _apply_message(svc, log, msg)
        return write_json({"status": "ok"})


def _event_key(m: dict[str, Any]) -> str:
    """Derives a stable idempotency key. Vapi does not guarantee a unique
    event id, so we hash the tuple that identifies a delivery: which call,
    which message type, and which state it reports. A genuine state
    change produces a new key; a replay of the same payload produces the
    same one.
    """
    call_id = ((m.get("call") or {}).get("id")) or ""
    parts = "|".join([call_id, m.get("type") or "", m.get("status") or "", m.get("endedReason") or ""])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def _apply_message(svc, log, m: dict[str, Any]) -> None:
    u = objective.CallUpdate()
    now = datetime.now(timezone.utc)
    call_id = ((m.get("call") or {}).get("id")) or ""
    kind = m.get("type")

    if kind == "status-update":
        st = _map_status(m.get("status") or "")
        if st:
            u.status = st
            if st == objective.CALL_IN_PROGRESS:
                u.started_at = now
    elif kind == "end-of-call-report":
        u.status = objective.CALL_ENDED
        u.ended_at = now
        if m.get("endedReason"):
            u.end_reason = m.get("endedReason")
        artifact = m.get("artifact") or {}
        url = _first_non_empty(m.get("recordingUrl"), artifact.get("recordingUrl"))
        if url:
            u.recording_url = url
        tr = _first_non_empty(m.get("transcript"), artifact.get("transcript"))
        if tr:
            u.transcript = tr
        cost = m.get("cost")
        if cost:
            u.cost_cents = int(round(cost * 100))  # Vapi reports dollars
    else:
        # transcript / speech-update are stored as raw events only.
        return

    try:
        call = svc.apply_call_update(call_id, u)
    except Exception as e:
        log.error("apply webhook provider_call_id=%s err=%s", call_id, e)
        return
    if kind == "end-of-call-report":
        artifact = m.get("artifact") or {}
        msgs = artifact.get("messages")
        if not msgs:
            msgs = m.get("messages")
        svc.finalize_call(
            call.id, m.get("endedReason") or "",
            _first_non_empty(m.get("transcript"), artifact.get("transcript")),
            msgs,
        )


def _map_status(v: str) -> str:
    """Translates Vapi's vocabulary into ours. Unknown values return "" so
    an unrecognised status leaves the stored one untouched.
    """
    v = v.lower()
    if v in ("queued", "scheduled"):
        return objective.CALL_QUEUED
    if v == "ringing":
        return objective.CALL_DIALING
    if v in ("in-progress", "in_progress", "forwarding"):
        return objective.CALL_IN_PROGRESS
    if v == "ended":
        return objective.CALL_ENDED
    return ""


def _verify_webhook_secret(cfg, req) -> bool:
    want = cfg.vapi_webhook_secret
    if not want:
        return True  # unset: allowed, but main.py warns at startup
    got = req.headers.get("x-vapi-secret", "")
    return hmac.compare_digest(got, want)


def _first_non_empty(*vals: Optional[str]) -> str:
    for v in vals:
        if v:
            return v
    return ""


def _truncate(b: bytes, n: int) -> str:
    s = b.decode("utf-8", "replace")
    if len(s) <= n:
        return s
    return s[:n] + "…"
