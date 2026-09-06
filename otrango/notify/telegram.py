"""Telegram delivers outcomes to the owner's chat via the Bot API.

Nothing stands between this call and the handset: no carrier, no
registration, and no downstream filtering of an already-accepted message. A
2xx here means the message is in the chat, so success is delivery rather
than acceptance for delivery.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import requests

from otrango.notify.notify import NotifyError, redact

DEFAULT_API_BASE = "https://api.telegram.org"


def api_url(base: str, token: str, method: str) -> str:
    """Builds a Bot API endpoint. The token sits in the path, which is why
    every error crossing a module boundary goes through redact first.
    """
    base = base or DEFAULT_API_BASE
    return f"{base}/bot{token}/{method}"


@dataclass
class Telegram:
    token: str
    chat_id: int
    # base_url overrides the Bot API root. Empty means the real one; tests
    # set it to a stub so the delivery path is exercised without the
    # network.
    base_url: str = ""
    timeout: float = 10.0
    session: requests.Session = field(default_factory=requests.Session)

    def notify(self, body: str) -> None:
        url = api_url(self.base_url, self.token, "sendMessage")
        try:
            resp = self.session.post(
                url, data={"chat_id": str(self.chat_id), "text": body}, timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise redact(e, self.token) from None

        if not (200 <= resp.status_code < 300):
            raise NotifyError(
                f"telegram sendMessage: {resp.status_code} {resp.reason}: {_telegram_reason(resp)}"
            )

    def get_updates(self, offset: int, wait_seconds: float) -> list[dict]:
        url = api_url(self.base_url, self.token, "getUpdates")
        try:
            resp = self.session.get(
                url,
                params={"offset": offset, "timeout": int(wait_seconds), "allowed_updates": '["message"]'},
                timeout=wait_seconds + 10,
            )
        except requests.RequestException as e:
            raise redact(e, self.token) from None

        if resp.status_code == 409:
            # Telegram allows one reader per bot. Both causes are operator
            # error with a specific fix, and neither is guessable from
            # "409".
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


def _telegram_reason(resp: requests.Response) -> str:
    """Pulls the Bot API's own explanation out of an error response. Worth
    the few lines: the common failures here -- 403 "bot was blocked by the
    user", 400 "chat not found" -- are each one config mistake with one
    fix, and a bare status code names neither.
    """
    try:
        payload = resp.json()
    except ValueError:
        return "no description"
    return payload.get("description") or "no description"
