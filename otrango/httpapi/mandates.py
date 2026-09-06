from __future__ import annotations

from flask import Flask, request

from otrango import objective
from otrango.httpapi.common import write_err, write_json
from otrango.store.errors import ErrExpired, ErrNotAuthorized, ErrNotFound
from otrango.usecase.ordering import DraftRequest, ErrNotDraft


def register(app: Flask, cfg, svc, broker, log) -> None:
    @app.post("/api/mandates")
    def handle_create_mandate():
        body = request.get_json(silent=True)
        if body is None:
            return write_err("invalid JSON body", 400)
        req = DraftRequest.from_json(body)
        try:
            m = svc.draft(cfg.owner_user_id, req, objective.SOURCE_CONSOLE)
        except Exception as e:
            return write_err(str(e), 400)
        return write_json(m, 201)

    @app.post("/api/mandates/<id>/authorize")
    def handle_authorize_mandate(id: str):
        try:
            call = svc.authorize_and_dial(id)
        except Exception as e:
            return write_err(str(e), _status_for_dial_err(e))
        return write_json(call, 202)


def _status_for_dial_err(err: Exception) -> int:
    """Maps a use-case error onto HTTP. This mapping is the only thing the
    delivery layer is entitled to decide about a failed dial.
    """
    if isinstance(err, (ErrNotDraft, ErrExpired, ErrNotAuthorized)):
        return 409
    if isinstance(err, ErrNotFound):
        return 404
    return 502
