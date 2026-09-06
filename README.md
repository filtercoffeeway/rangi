# Otrango (Python)

A voice agent that places real phone calls on your behalf. First use case: ordering a coffee
by calling the restaurant directly — where the restaurant's order line is **itself an AI agent**.

This is a line-for-line Python port of the original Go implementation (`../otrango`). Same
architecture, same behavior, same tests, same eval suite — see [PLAN.md](PLAN.md) for the
architecture and open questions, and [context.md](context.md) for the original idea.

**Status: Phases 0, 1, 2 and 4 are built and tested. Phase 3 — the real call — needs your
credentials and a real coffee.**

---

## What makes this more than a demo

**A restaurant has no API.** There's no ACK and no order ID. "The counterparty agreed" is a
claim about the world, not a confirmed write. So every call terminates with an explicit
`result` + `confidence` + `needs_review`, an unrecognised result is `ambiguous` rather than
`success`, and a reconciler forces a terminal record on any call that goes quiet.

**The counterparty is a bot.** Two voice agents negotiating over PSTN — each with its own VAD,
endpointing and barge-in policy — fail in ways that don't exist when a human answers: turn-taking
deadlock, mutual barge-in livelock, politeness loops, and calls that simply never end because
neither side gets impatient. `otrango/turns` measures all of it; hard duration and turn caps are
the only guaranteed termination condition.

**The model proposes, the server disposes.** A price cap living in a system prompt is not a
price cap. `propose_order` evaluates against the stored mandate in Python, so no amount of
prompting, peer pressure, or injected text moves it (`test_price_cap_survives_adversarial_framing`).

---

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env      # fill in as accounts come online
.venv/bin/pytest
./scripts/dev.sh          # service + ngrok tunnel + caffeinate
```

`dev.sh` starts everything needed to take a real call from this machine, then proves the path
end to end: the tunnel reaches the service, and the webhook rejects an unsigned request while
accepting a signed one. It refuses to start on a stale config rather than serving one silently.

A laptop is a legitimate host for testing — the process is already warm, so unlike scale-to-zero
hosting there is no cold start, and ngrok's inspector at `:4040` replays every webhook Vapi
sends. What a laptop cannot survive is sleeping mid-call, which is why the script holds
`caffeinate` for exactly as long as the service runs.

For just the service, without a tunnel: `.venv/bin/python cmd/otrango.py`. It starts without any
credentials — console, webhooks, mandates and the order parser all work; only dialing needs Vapi.

## Setup, in order

1. **Create the Telegram bot.** Message [@BotFather](https://t.me/BotFather) → `/newbot` →
   token into `TELEGRAM_BOT_TOKEN`. Then open the `t.me` link it gives you and **send the bot
   anything**: a bot cannot start a conversation, so until you do this it has no chat to reply
   to and the next step returns nothing. Read your chat ID off it:
   ```bash
   curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" \
     | jq '.result[-1].message.chat.id'      # → TELEGRAM_CHAT_ID
   ```
   Optional, and worth it: `/setcommands` in BotFather (`coffee`, `help`) gives the chat a
   native command menu.
2. **Vapi account** → private API key → `.env`.
3. **Buy a number and import it into Vapi.** Required, not optional: new Vapi accounts
   get inbound-only numbers, so outbound calls need an imported one. The number is for
   placing calls only — nothing here sends or receives text messages.
4. **`ngrok http 8080`** → put the https URL in `PUBLIC_BASE_URL`. Needed by the Vapi
   callback only; the Telegram trigger reaches out and works without it.
5. **`openssl rand -hex 32`** → `VAPI_WEBHOOK_SECRET`.
6. **Create the assistant:**
   ```bash
   .venv/bin/python cmd/otrango_assistant.py | curl -sS -X POST https://api.vapi.ai/assistant \
     -H "Authorization: Bearer $VAPI_API_KEY" -H 'Content-Type: application/json' -d @-
   ```
   Copy the returned `id` into `VAPI_ASSISTANT_ID`.

## Ordering

Two steps, always. Drafting creates no authority; nothing dials until you authorize.

```bash
# Console: type an order, review the cap, click Authorize & dial.

# Terminal shell:
.venv/bin/python cmd/otrango_shell.py

# Telegram:
you → coffee
←     1 drip coffee (medium) for pickup, under Mahesh · cap $5.75 — reply Y to dial
you → Y
←     ☕ drip coffee confirmed · $3.25 · ready 4:12 · #47 — reply N if wrong

# curl:
MID=$(curl -s -X POST localhost:8080/api/mandates \
  -H 'Content-Type: application/json' -d '{"input":"latte","dry_run":true}' | jq -r .id)
curl -X POST localhost:8080/api/mandates/$MID/authorize
```

**Use `dry_run` first against a real target.** It runs the whole conversation and aborts at the
commit point — real peer, real timing, no coffee.

## Evals

The twelve scenarios in `otrango/eval` are the regression suite for a system whose interface is
a conversation. Every one asserts on the `outcomes` row, never on transcript text — which is why
a scenario can change substrate without its assertion changing.

```bash
.venv/bin/python cmd/otrango_eval.py                    # T0: text replay, ~$0.05, seconds
.venv/bin/python cmd/otrango_eval.py --tier 1            # + voice-shaped scenarios
.venv/bin/python cmd/otrango_eval.py --scenario upsell -v
```

T0 replays a scripted counterparty through the real prompt, the real tools and the real
server-side enforcement — only STT, TTS and the carrier are absent. Pass bar: ≥10/12, **zero
false successes** (a false success means you'd walk to a counter for coffee that was never
ordered), zero non-terminating calls.

## Layout

```
cmd/otrango.py             composition root: wires SQLite + Vapi into the use cases
cmd/otrango_eval.py         scenario suite
cmd/otrango_assistant.py    emits the Vapi assistant config
cmd/otrango_shell.py        a conversation with the agent, in the terminal

DOMAIN — no internal dependencies
  otrango/objective/   entities and rules: call, mandate, constraints, outcome
  otrango/turns/       agent-to-agent timing metrics

USE CASES — depend on the domain and on ports they declare themselves
  otrango/usecase/     ports.py plus ordering, dial, tools, outcome, messaging, reconcile

ADAPTERS — implement the ports
  otrango/adapter/     store and Vapi behind the usecase interfaces
  otrango/store/       SQLite, migrations, idempotent event sink
  otrango/vapi/        provider client
  otrango/notify/      outcome delivery
  otrango/telegram/    the owner's channel: long-poll in, reply out
  otrango/httpapi/     delivery only: parse, call a use case, render (Flask)
  otrango/console/     embedded operator UI (static HTML, served by httpapi)
  otrango/sse/         fan-out

VERTICALS
  otrango/skills/      coffee/ and hours/ + registry
  otrango/agent/       tool contract, shared by Vapi and the evals
```

Dependencies point inward. `otrango/usecase`, `otrango/objective` and `otrango/turns` import no
infrastructure at all, which is what lets the rules be tested with in-memory fakes — no SQLite
file, no web server, no provider account.

`tests/test_skills_boundary.py` parses the import graph and fails if the vertical-agnostic layer
ever reaches into a skill. The extensibility claim in PLAN.md §2 is therefore enforced, not
asserted — `otrango/skills/hours` was added without touching `objective/`, `store/`, `vapi/` or
`turns/`.

## Notes

- **Idempotency** uses a UNIQUE constraint on `sha256(call_id|type|status|endedReason)`; Vapi
  doesn't guarantee a unique event id. Tool calls take a separate path — they're synchronous and
  blocking, and a repeat is the model genuinely asking twice.
- **Outcomes are write-once.** The first terminal record wins, so a late webhook or the
  reconciler can't overwrite what the agent reported.
- **Webhook parsing is partial and tolerant.** The raw body always lands in `events.payload`,
  so anything mis-parsed today is recoverable from the DB.
- **No response timeout** on the HTTP server's SSE stream — cutting it off would sever the live
  console feed.
- **No LLM in the trigger path.** `coffee`/`usual`/`Y`/`N` are exact-keyword matches.
- Vapi's API shapes are from early-2025 docs and may have moved. If `otrango_assistant.py`'s
  output is rejected, that's the first place to look.
- The dev server (`werkzeug.serving.make_server`, threaded) is fine for local development and for
  a laptop taking a real call; put a production WSGI server (gunicorn/waitress) in front for
  anything beyond that.

## What's left

Phase 3 is the only phase that can't be faked: dry runs against the live AI order-taker to
gather real turn metrics, then one wet call, then check that the `outcomes` row matches the
coffee in your hand.
