"""Package telegram is the owner's channel, in both directions. It is
delivery only: whitelist the sender, hand the message body to the use
case, and render the reply back into the chat. No orchestration lives
here.

It long-polls rather than taking a webhook. A webhook would need a public
URL registered with setWebhook, which means re-registering every time the
dev tunnel hands out a new address -- a manual step before every demo, and
a silent dead trigger whenever it is forgotten. Polling reaches out instead
of being reached, so the trigger path does not depend on the tunnel at
all.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Protocol

import requests

from otrango import objective
from otrango.notify.notify import NotifyError, redact
from otrango.notify.telegram import api_url

# poll_timeout is how long the Bot API holds an empty request open. Long
# enough that an idle bot costs a couple of requests a minute.
POLL_TIMEOUT = 30.0
# The HTTP deadline must clear poll_timeout, or every quiet poll would be
# cancelled client-side and logged as a failure.
HTTP_TIMEOUT = POLL_TIMEOUT + 10.0

MIN_BACKOFF = 1.0
MAX_BACKOFF = 30.0


class App(Protocol):
    """The use case this adapter drives -- the same entry point the
    console and the shell call.
    """

    def handle_message(self, body: str, src: str) -> str: ...


class Sender(Protocol):
    """Delivers the reply. notify.Telegram satisfies it, so a reply to a
    message and an outcome notification travel the identical path.
    """

    def notify(self, body: str) -> None: ...


class Poller:
    def __init__(self, token: str, chat_id: int, app: App, send: Sender,
                 log: Optional[logging.Logger] = None):
        self.token = token
        self.chat_id = chat_id
        self.app = app
        self.send = send
        self.log = log or logging.getLogger("otrango.telegram.noop")
        if log is None:
            self.log.addHandler(logging.NullHandler())
            self.log.propagate = False
        self.session = requests.Session()
        # base_url overrides the Bot API root; empty means the real one.
        self.base_url = ""
        self.offset = 0

    def run(self, stop_event: threading.Event) -> None:
        """Polls until stop_event is set. Owns its own errors: a bot that
        cannot reach Telegram must not take the service down, because the
        console, the webhook sink and the reconciler are all still
        working.
        """
        try:
            self._skip_backlog(stop_event)
        except Exception as e:
            if not stop_event.is_set():
                self.log.warning("telegram: could not clear backlog, starting from the next message: %s", e)
        self.log.info("telegram: listening chat_id=%s", self.chat_id)

        backoff = MIN_BACKOFF
        while not stop_event.is_set():
            try:
                updates = self._get_updates(self.offset, POLL_TIMEOUT, stop_event)
            except Exception as e:
                if stop_event.is_set():
                    return
                self.log.warning("telegram: poll failed err=%s retry_in=%s", e, backoff)
                if stop_event.wait(backoff):
                    return
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue
            backoff = MIN_BACKOFF

            for u in updates:
                # Advance past every update, including ones this bot
                # ignores. Leaving the offset behind an unhandled update
                # would refetch it on the next poll, forever.
                update_id = u.get("update_id", 0)
                if update_id >= self.offset:
                    self.offset = update_id + 1
                self._dispatch(u)

    def _skip_backlog(self, stop_event: threading.Event) -> None:
        """Discards whatever is already queued before the first real
        poll.

        Telegram holds undelivered updates for 24 hours, so without this a
        restart would replay everything said to the bot since yesterday --
        including the "hi" sent to it during setup, which parses as an
        order. Nothing queued before the service started is a live
        instruction.

        offset=-1 asks for the most recent update only; acknowledging past
        it confirms the whole backlog without acting on any of it.
        """
        updates = self._get_updates(-1, 0, stop_event)
        for u in updates:
            update_id = u.get("update_id", 0)
            if update_id >= self.offset:
                self.offset = update_id + 1
        if updates:
            self.log.info("telegram: skipped queued messages from before startup through_update=%s",
                           self.offset - 1)

    def _dispatch(self, u: dict) -> None:
        msg = u.get("message")
        if not msg:
            return
        chat_id = (msg.get("chat") or {}).get("id")
        # The chat whitelist, and the reason knowing the bot's handle is
        # not enough to make this service place a call.
        if chat_id != self.chat_id:
            self.log.warning("telegram: message from unknown chat chat_id=%s text_len=%s",
                              chat_id, len(msg.get("text") or ""))
            return
        body = msg.get("text") or ""
        if not body:
            return
        # Telegram's command menu sends "/coffee"; the use case parses
        # words. The leading slash is presentation, so it is stripped here
        # rather than taught to a parser shared with channels that have no
        # such convention.
        if len(body) > 1 and body[0] == "/":
            body = body[1:]

        reply = self.app.handle_message(body, objective.SOURCE_TELEGRAM)
        if not reply:
            return
        try:
            self.send.notify(reply)
        except Exception as e:
            self.log.error("telegram: could not send reply err=%s", e)

    def _get_updates(self, offset: int, wait: float, stop_event: threading.Event) -> list[dict]:
        url = api_url(self.base_url, self.token, "getUpdates")
        try:
            resp = self.session.get(
                url,
                params={"offset": offset, "timeout": int(wait), "allowed_updates": '["message"]'},
                timeout=wait + 10,
            )
        except requests.RequestException as e:
            raise redact(e, self.token) from None

        if resp.status_code == 409:
            raise NotifyError(
                "telegram getUpdates: 409 Conflict — another process is polling this "
                "bot, or a webhook is still registered (clear it with deleteWebhook)"
            )
        if not (200 <= resp.status_code < 300):
            raise NotifyError(f"telegram getUpdates: {resp.status_code} {resp.reason}")

        payload = resp.json()
        if not payload.get("ok"):
            raise NotifyError("telegram getUpdates: not ok")
        return payload.get("result") or []
