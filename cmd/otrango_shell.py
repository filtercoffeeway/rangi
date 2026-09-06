#!/usr/bin/env python3
"""Command otrango-shell is a conversation with the agent, in the
terminal.

    python cmd/otrango_shell.py
    > order filter coffee
    Ordering filter coffee from Mylapore Express. Pickup asap. Qty 1. Reply Y to confirm
    > Y
    Calling now...
    Order placed. 1 filter coffee. Pickup time 5.05pm. Pay at store. Reply N to make changes to order.

Every line goes to the same use case the Telegram adapter calls, so this is
the exact conversation the owner would have by text -- not a demo path that
could drift from the real one.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time

import requests

ESC = "\033["


class UI:
    def __init__(self, colored: bool):
        if not colored:
            self.dim = self.bold = self.agent = self.you = self.reset = ""
        else:
            self.dim = ESC + "2m"
            self.bold = ESC + "1m"
            self.agent = ESC + "38;5;68m"
            self.you = ESC + "38;5;179m"
            self.reset = ESC + "0m"


u = UI(colored=not os.environ.get("NO_COLOR"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080", help="service base URL")
    args = ap.parse_args()
    base = args.base

    try:
        health(base)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"\n  {u.bold}{u.agent}otrango{u.reset}  {u.dim}type an order, or `help`. ctrl-d to exit.{u.reset}\n")

    # Watching the stream lets the shell say "calling now" and then fall
    # quiet until the call actually ends, instead of polling.
    done = watch_calls(base)

    while True:
        try:
            line = input(f"{u.you}>{u.reset} ")
        except EOFError:
            print()
            return
        line = line.strip()
        if not line:
            continue
        if line in ("exit", "quit"):
            return

        try:
            reply = send(base, line)
        except Exception as e:
            print(f"  {u.dim}{e}{u.reset}\n")
            continue
        say(reply)

        # A dial is the one command with a delayed second half.
        if "Calling now" in reply:
            await_outcome(base, done)
        print()


def say(msg: str) -> None:
    for line in msg.strip().split("\n"):
        print(f"  {u.agent}{line}{u.reset}")


def send(base: str, body: str) -> str:
    resp = requests.post(f"{base}/api/message", json={"body": body}, timeout=30)
    out = resp.json()
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out.get("reply", "")


def await_outcome(base: str, done: "queue.Queue[str]") -> None:
    """Waits for the call to reach a terminal state, then prints the
    summary the owner would have received by text.
    """
    spin = ["·", "‥", "…"]
    i = 0
    deadline = time.monotonic() + 6 * 60
    while True:
        try:
            call_id = done.get(timeout=0.4)
        except queue.Empty:
            if time.monotonic() > deadline:
                print(f"\r  {u.dim}still running — check the console{u.reset}")
                return
            print(f"\r  {u.dim}{spin[i % len(spin)]}{u.reset}", end="", flush=True)
            i += 1
            continue
        print("\r   \r", end="")
        s = summary(base, call_id)
        if s:
            say(s)
        return


def summary(base: str, call_id: str) -> str:
    """Re-renders the outcome through the skill, so the shell says exactly
    what the notifier would have sent.
    """
    try:
        resp = requests.get(f"{base}/api/calls/{call_id}/summary", timeout=10)
        return resp.json().get("summary", "")
    except Exception:
        return ""


def watch_calls(base: str) -> "queue.Queue[str]":
    """Emits a call id once that call reaches a terminal state."""
    out: "queue.Queue[str]" = queue.Queue(maxsize=8)

    def run():
        while True:
            try:
                resp = requests.get(f"{base}/api/stream", stream=True, timeout=None)
            except requests.RequestException:
                time.sleep(2)
                continue
            try:
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not raw_line or not raw_line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(raw_line[len("data: "):])
                    except ValueError:
                        continue
                    data = ev.get("data") or {}
                    if data.get("status") in ("ended", "failed"):
                        try:
                            out.put_nowait(data.get("id", ""))
                        except queue.Full:
                            pass
            except requests.RequestException:
                pass
            finally:
                resp.close()

    threading.Thread(target=run, daemon=True).start()
    return out


def health(base: str) -> None:
    try:
        resp = requests.get(f"{base}/healthz", timeout=5)
    except requests.RequestException:
        raise RuntimeError(f"cannot reach otrango at {base} — is ./scripts/dev.sh running?")
    h = resp.json()
    if not h.get("vapi_ready"):
        print(f"  {u.dim}vapi is not configured — drafting works, dialing will fail{u.reset}")


if __name__ == "__main__":
    main()
