"""Package httpapi is delivery only: it parses requests, calls a use case,
and renders the result. No orchestration and no business rules live here
-- those are in otrango.usecase, where they can be tested without a web
server.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from flask import Flask, Response, g, request

from otrango.console.console import read_index
from otrango.httpapi import mandates as mandates_routes
from otrango.httpapi import message as message_routes
from otrango.httpapi import stream as stream_routes
from otrango.httpapi import webhook as webhook_routes
from otrango.httpapi.common import write_err, write_json


def create_app(cfg, svc, broker, log: Optional[logging.Logger] = None) -> Flask:
    app = Flask("otrango")
    app.url_map.strict_slashes = False
    if log is None:
        log = logging.getLogger("otrango.httpapi.noop")
        log.addHandler(logging.NullHandler())
        log.propagate = False

    @app.get("/")
    def handle_console():
        if request.path != "/":
            return write_err("not found", 404)
        return Response(read_index(), mimetype="text/html; charset=utf-8")

    @app.get("/healthz")
    def handle_health():
        return write_json({
            "ok": True,
            "subscribers": broker.count(),
            "vapi_ready": cfg.require_vapi() is None,
        })

    @app.get("/api/calls")
    def handle_list_calls():
        limit = 50
        v = request.args.get("limit")
        if v:
            try:
                n = int(v)
                limit = n if 0 < n <= 500 else 50
            except ValueError:
                pass
        try:
            calls = svc.list_calls(limit)
        except Exception as e:
            log.error("list calls err=%s", e)
            return write_err("could not list calls", 500)
        return write_json({"calls": calls})

    @app.get("/api/calls/<id>")
    def handle_get_call(id: str):
        try:
            detail = svc.call_detail(id)
        except Exception:
            return write_err("no such call", 404)
        return write_json(detail)

    @app.post("/api/calls/<id>/verify")
    def handle_verify_outcome(id: str):
        body = request.get_json(silent=True)
        if body is None:
            return write_err("invalid JSON body", 400)
        verdict = body.get("verdict")
        if verdict not in ("correct", "wrong", "never_arrived"):
            return write_err("verdict must be correct, wrong, or never_arrived", 400)
        try:
            svc.record_verdict(id, verdict)
        except Exception:
            return write_err("could not record verdict", 500)
        return write_json({"status": "ok"})

    mandates_routes.register(app, cfg, svc, broker, log)
    webhook_routes.register(app, cfg, svc, broker, log)
    message_routes.register(app, cfg, svc, broker, log)
    stream_routes.register(app, cfg, svc, broker, log)

    @app.before_request
    def _start_timer():
        g._start = time.monotonic()

    @app.after_request
    def _log_request(resp: Response):
        if request.path == "/api/stream":
            return resp  # long-lived; logging its duration is noise
        dur_ms = int((time.monotonic() - getattr(g, "_start", time.monotonic())) * 1000)
        log.info("http method=%s path=%s status=%s dur_ms=%s",
                  request.method, request.path, resp.status_code, dur_ms)
        return resp

    return app
