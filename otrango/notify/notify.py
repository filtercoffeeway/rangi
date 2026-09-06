"""Package notify delivers the outcome half of the mandate lifecycle."""
from __future__ import annotations

import logging
from typing import Optional


class Discard:
    """Used before a transport is configured, so the whole outcome path is
    exercisable without credentials.
    """

    def __init__(self, log: Optional[logging.Logger] = None):
        self.log = log

    def notify(self, body: str) -> None:
        if self.log is not None:
            self.log.info("notify (no transport configured): %s", body)


class NotifyError(Exception):
    """Carries a transport's own failure text. Names no provider: the same
    type is returned by every notifier here.
    """

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


def redact(err: Exception, token: str) -> Exception:
    """Strips a bot token out of an error before it reaches a log.

    The requests/http libraries put the full request URL into transport
    errors, so an unwrapped timeout from the Bot API would print the token
    -- a live credential -- into the service log at whatever level the
    caller chose. Like the Go original, this discards the original error
    type and returns a plain new one -- the caller only needs the message.
    """
    if err is None or not token:
        return err
    msg = str(err)
    if token not in msg:
        return err
    return RuntimeError(msg.replace(token, "<redacted>"))
