import http.server
import json
import queue
import threading
import time
from urllib.parse import parse_qsl, urlparse

import pytest

from otrango import objective
from otrango.notify.notify import redact
from otrango.notify.telegram import Telegram
from otrango.telegram.poller import Poller

TEST_TOKEN = "8123456789:AAHsecrettokenvalue"


class FakeAPI:
    """Stands in for the Bot API: it serves a scripted queue of updates and
    records what the poller sent back.
    """

    def __init__(self, *batches):
        self.lock = threading.Lock()
        self.batches = [list(b) for b in batches]
        self.polls: list[int] = []
        self.sent: list[str] = []

        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                path = urlparse(self.path).path
                if path.endswith("/getUpdates"):
                    qs = dict(parse_qsl(urlparse(self.path).query))
                    offset = int(qs.get("offset", "0"))
                    with fake.lock:
                        fake.polls.append(offset)
                        batch = fake.batches.pop(0) if fake.batches else []
                    body = json.dumps({"ok": True, "result": batch}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                path = urlparse(self.path).path
                if path.endswith("/sendMessage"):
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length).decode()
                    form = dict(parse_qsl(raw))
                    with fake.lock:
                        fake.sent.append(form.get("text", ""))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                else:
                    self.send_response(404)
                    self.end_headers()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def sent_messages(self) -> list[str]:
        with self.lock:
            return list(self.sent)

    def offsets(self) -> list[int]:
        with self.lock:
            return list(self.polls)

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=2)


@pytest.fixture
def fake_api():
    apis: list[FakeAPI] = []

    def make(*batches):
        f = FakeAPI(*batches)
        apis.append(f)
        return f

    yield make
    for f in apis:
        f.close()


def msg(update_id: int, chat_id: int, text: str) -> dict:
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat_id}}}


class Recorder:
    """Captures what the adapter handed to the use case."""

    def __init__(self):
        self.lock = threading.Lock()
        self.got: list[str] = []
        self.srcs: list[str] = []
        self.seen: queue.Queue = queue.Queue()

    def handle_message(self, body: str, src: str) -> str:
        with self.lock:
            self.got.append(body)
            self.srcs.append(src)
        self.seen.put(())
        return "reply to " + body

    def bodies(self) -> list[str]:
        with self.lock:
            return list(self.got)

    def sources(self) -> list[str]:
        with self.lock:
            return list(self.srcs)


def run_poller(fake: FakeAPI, app: Recorder, chat_id: int, wait_for: int):
    send = Telegram(token=TEST_TOKEN, chat_id=chat_id, base_url=fake.url)
    p = Poller(TEST_TOKEN, chat_id, app, send)
    p.base_url = fake.url

    stop_event = threading.Event()
    thread = threading.Thread(target=p.run, args=(stop_event,), daemon=True)
    thread.start()

    deadline = time.monotonic() + 3
    for _ in range(wait_for):
        remaining = deadline - time.monotonic()
        try:
            app.seen.get(timeout=max(remaining, 0))
        except queue.Empty:
            stop_event.set()
            thread.join(timeout=2)
            pytest.fail(f"timed out waiting for a message")

    # Handing the body to the use case and sending the reply are separate
    # steps; stopping between them would drop a reply the poller was about
    # to make, so wait for the send too.
    waited = 0.0
    while len(fake.sent_messages()) < wait_for:
        if waited > 2:
            stop_event.set()
            thread.join(timeout=2)
            pytest.fail(f"timed out waiting for {wait_for} replies, got {len(fake.sent_messages())}")
        time.sleep(0.01)
        waited += 0.01
    if wait_for == 0:
        time.sleep(0.15)

    stop_event.set()
    thread.join(timeout=2)
    if thread.is_alive():
        pytest.fail("poller did not stop on cancellation")


def test_owner_message_reaches_the_use_case_and_is_answered(fake_api):
    fake = fake_api([], [msg(11, 42, "coffee")])
    app = Recorder()

    run_poller(fake, app, 42, 1)

    assert app.bodies() == ["coffee"]
    # The store records which channel the order arrived on, and every
    # adapter shares handle_message -- so the source has to travel with
    # the message.
    assert app.sources() == [objective.SOURCE_TELEGRAM]
    assert fake.sent_messages() == ["reply to coffee"]


# The chat whitelist is what stops someone who knows the bot's handle from
# making this service place a call.
def test_message_from_another_chat_is_ignored(fake_api):
    fake = fake_api([], [msg(11, 999, "coffee")])
    app = Recorder()

    run_poller(fake, app, 42, 0)

    assert app.bodies() == []
    assert fake.sent_messages() == []


# An ignored update must still advance the offset. Leaving it behind would
# refetch the same message on every poll forever, and -- once the sender
# was whitelisted again -- replay it.
def test_ignored_update_still_advances_the_offset(fake_api):
    fake = fake_api([], [msg(11, 999, "coffee")], [msg(12, 42, "latte")])
    app = Recorder()

    run_poller(fake, app, 42, 1)

    offsets = fake.offsets()
    assert len(offsets) >= 3
    assert offsets[0] == -1  # backlog skip
    assert offsets[2] == 12


# Telegram holds undelivered updates for 24 hours. Without the startup
# skip, a restart replays everything said to the bot since yesterday --
# including the "hi" sent during setup, which parses as an order and would
# dial.
def test_backlog_from_before_startup_is_not_acted_on(fake_api):
    fake = fake_api(
        [msg(7, 42, "coffee")],  # backlog, answered with offset=-1
        [msg(8, 42, "latte")],   # the first live message
    )
    app = Recorder()

    run_poller(fake, app, 42, 1)

    assert app.bodies() == ["latte"]


# A slash command from Telegram's command menu is the same order as typing
# it.
def test_slash_command_is_treated_as_plain_text(fake_api):
    fake = fake_api([], [msg(11, 42, "/coffee")])
    app = Recorder()

    run_poller(fake, app, 42, 1)

    assert app.bodies() == ["coffee"]


# The requests library puts the request URL into transport errors, and the
# token lives in that URL. An unredacted error writes a live credential
# into the log.
def test_transport_error_does_not_leak_the_token():
    err = redact(
        RuntimeError(f'Get "https://api.telegram.org/bot{TEST_TOKEN}/getUpdates": timeout'),
        TEST_TOKEN,
    )
    assert TEST_TOKEN not in str(err)
    assert "<redacted>" in str(err)
