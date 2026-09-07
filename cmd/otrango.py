#!/usr/bin/env python3
"""Command otrango runs the voice-agent control service: dial endpoint,
webhook sink, reconciler, and the embedded operator console.

This is the composition root. It is the only place that knows a use case
is backed by SQLite and Vapi rather than by something else.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from otrango import adapter, config as config_mod
from otrango.httpapi.server import create_app
from otrango.notify.notify import Discard
from otrango.notify.telegram import Telegram
from otrango.parse.parse import LLM as ParseLLM
from otrango.skills.register import default as default_skills, with_parser
from otrango.sse.broker import Broker
from otrango.store.store import Store
from otrango.telegram.poller import Poller
from otrango.usecase.service import Deps, Service
from otrango.vapi.client import Client as VapiClient


def main() -> None:
    log = logging.getLogger("otrango")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                         stream=sys.stdout)
    try:
        run(log)
    except Exception as e:
        log.error("fatal: %s", e)
        sys.exit(1)


def run(log: logging.Logger) -> None:
    cfg = config_mod.load()

    st = Store.open(cfg.database_path)
    try:
        _run_with_store(cfg, st, log)
    finally:
        st.close()


def _run_with_store(cfg, st: Store, log: logging.Logger) -> None:
    missing = cfg.require_vapi()
    if missing:
        log.warning("vapi not configured — console and webhooks work, dialing will fail: %s", missing)
    if not cfg.vapi_webhook_secret:
        log.warning("VAPI_WEBHOOK_SECRET unset — webhook endpoint is unauthenticated")
    if not cfg.public_base_url:
        log.warning("PUBLIC_BASE_URL unset — set it to your ngrok URL so Vapi can reach the webhook")

    # The owner's channel, in both directions. Unconfigured is a degraded
    # state rather than a fatal one: the console still drafts and dials,
    # and outcomes go to the log, which is what the tests and the eval
    # tiers use.
    notifier = Discard(log)
    tg = None
    if cfg.telegram_ready():
        tg = Telegram(token=cfg.telegram_bot_token, chat_id=cfg.telegram_chat_id)
        notifier = tg
        log.info("outcomes: telegram chat_id=%s", cfg.telegram_chat_id)
    else:
        log.warning("telegram not configured — the console still works; "
                     "outcomes will be logged, not sent")

    broker = Broker()
    app = Service(Deps(
        mandates=adapter.Mandates(st), calls=adapter.Calls(st),
        outcomes=adapter.Outcomes(st), events=adapter.Events(st),
        caller=adapter.Caller(
            client=VapiClient(cfg.vapi_api_key),
            phone_number_id=cfg.vapi_phone_number_id,
            assistant_id=cfg.vapi_assistant_id,
            provider=cfg.voice_llm_provider,
            model=cfg.voice_llm_model,
        ),
        notifier=notifier,
        pub=broker,
        skills=_build_skills(cfg, log),
        voices=cfg.voices,
        log=log,
        owner_user_id=cfg.owner_user_id,
        default_target=cfg.target_phone_number,
    ))

    stop_event = threading.Event()

    def handle_signal(signum, frame):
        log.info("shutting down")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Stops a crashed or dropped call sitting in limbo with the owner
    # never told.
    reconciler_thread = threading.Thread(
        target=app.run_reconciler, args=(stop_event, timedelta(minutes=2), None), daemon=True,
    )
    reconciler_thread.start()

    # The trigger reaches out to the Bot API rather than being reached, so
    # it needs no public URL and works before the tunnel is up. Only the
    # Vapi callback depends on PUBLIC_BASE_URL.
    poller_thread = None
    if tg is not None:
        poller = Poller(cfg.telegram_bot_token, cfg.telegram_chat_id, app, tg, log)
        poller_thread = threading.Thread(target=poller.run, args=(stop_event,), daemon=True)
        poller_thread.start()

    flask_app = create_app(cfg, app, broker, log)
    log.info("listening addr=:%s console=http://localhost:%s db=%s",
              cfg.port, cfg.port, cfg.database_path)

    # Werkzeug's dev server, threaded so the SSE stream and webhook/tool
    # calls can proceed concurrently -- matching the Go original's
    # goroutine-per-request net/http server. Not for production traffic;
    # swap in a WSGI server (gunicorn/waitress) behind a real deploy.
    from werkzeug.serving import make_server

    httpd = make_server("0.0.0.0", int(cfg.port), flask_app, threaded=True)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    try:
        stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        server_thread.join(timeout=10)


def _build_skills(cfg, log: logging.Logger):
    """Enables model-assisted parsing when there is a key to do it with.
    Without one the deterministic matcher still handles the common
    phrasings, so the service is never blocked on an LLM being reachable.
    """
    if not cfg.anthropic_api_key or cfg.profile is None:
        log.info("order parsing: keyword matching (no ANTHROPIC_API_KEY)")
        return default_skills(cfg.customer_name, cfg.business_name, cfg.profile)
    log.info("order parsing: model-assisted model=%s", cfg.llm_model)
    return with_parser(cfg.customer_name, cfg.business_name, cfg.profile,
                        ParseLLM(cfg.anthropic_api_key, cfg.llm_model, cfg.profile))


if __name__ == "__main__":
    main()
