from __future__ import annotations

from flask import Flask, request

from otrango import objective
from otrango.httpapi.common import write_err, write_json


def register(app: Flask, cfg, svc, broker, log) -> None:
    @app.post("/api/message")
    def handle_message():
        """The trigger surface for anything that is not the chat bot --
        today, the terminal shell.

        It delegates to the same usecase.handle_message the Telegram
        poller uses, so the shell is a genuine second adapter rather than
        a parallel implementation that can drift from the one people
        actually message.
        """
        body = request.get_json(silent=True)
        if body is None:
            return write_err("invalid JSON body", 400)
        reply = svc.handle_message(body.get("body") or "", objective.SOURCE_CONSOLE)
        return write_json({"reply": reply})
