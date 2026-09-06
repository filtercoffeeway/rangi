from __future__ import annotations

import queue

from flask import Flask, Response, stream_with_context

HEARTBEAT_SECONDS = 25.0


def register(app: Flask, cfg, svc, broker, log) -> None:
    @app.get("/api/stream")
    def handle_stream():
        """The console's live feed of call-state changes."""
        ch = broker.subscribe()

        def generate():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        msg = ch.get(timeout=HEARTBEAT_SECONDS)
                    except queue.Empty:
                        # Keeps proxies and ngrok from reaping an idle
                        # connection.
                        yield ": ping\n\n"
                        continue
                    yield f"data: {msg.decode('utf-8')}\n\n"
            except GeneratorExit:
                raise
            finally:
                broker.unsubscribe(ch)

        resp = Response(stream_with_context(generate()), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["Connection"] = "keep-alive"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp
