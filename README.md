# Rangi

A voice agent that places real phone calls on your behalf. The first use case is ordering
coffee: you send "coffee" to a Telegram bot, confirm the order and price cap, and the agent
calls the shop, places the order, and reports back what was actually agreed.

It is built on [Vapi](https://vapi.ai) for telephony and voice, and Anthropic's Claude for
order parsing and the conversation itself.

---

## How it works

**The model proposes, the server disposes.** A price cap living in a system prompt is not a
price cap. When the agent tries to commit to an order, `propose_order` checks it against the
stored mandate (item, quantity, cap) in Python. No amount of prompting, upselling or injected
text moves it.

**Nothing dials without your say-so.** Every order is two steps: draft a mandate, then
authorize it. Drafting creates no authority.

**A phone call has no API.** There's no order ID and no ACK — "the shop agreed" is a claim, not a
confirmed write. So every call ends with an explicit `result` + `confidence` + `needs_review`;
anything unrecognised is `ambiguous`, never `success`; and a reconciler forces a terminal record
on any call that goes quiet.

**The other side may be a bot too.** Many order lines are now AI agents. Two voice agents on one
call fail in ways a human never causes: turn-taking deadlock, mutual barge-in, politeness loops,
calls that never end. `otrango/turns` measures these, and hard duration and turn caps guarantee
every call terminates.

---

## Requirements

- Python 3.11+
- A [Vapi](https://vapi.ai) account and an imported phone number (outbound calls need one)
- An [Anthropic API key](https://console.anthropic.com)
- A Telegram bot (optional, but it's the easiest way to trigger orders)
- A public HTTPS URL for Vapi's webhooks — [ngrok](https://ngrok.com) is fine for local use

## Install

```bash
git clone https://github.com/filtercoffeeway/rangi.git
cd rangi
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The tests need no accounts or network.

## Configure

```bash
cp .env.example .env
cp profile.example.json profile.json
```

`.env.example` documents every variable. In order:

1. **Anthropic** — set `ANTHROPIC_API_KEY`.
2. **Vapi** — private API key into `VAPI_API_KEY`. Buy or import a number and put its ID in
   `VAPI_PHONE_NUMBER_ID`.
3. **Public URL** — run `ngrok http 8080` (or use any HTTPS host) and set `PUBLIC_BASE_URL`.
4. **Webhook secret** — `openssl rand -hex 32` → `VAPI_WEBHOOK_SECRET`.
5. **Create the assistant:**
   ```bash
   .venv/bin/python cmd/otrango_assistant.py | curl -sS -X POST https://api.vapi.ai/assistant \
     -H "Authorization: Bearer $VAPI_API_KEY" -H 'Content-Type: application/json' -d @-
   ```
   Copy the returned `id` into `VAPI_ASSISTANT_ID`.
6. **Telegram bot** — message [@BotFather](https://t.me/BotFather) → `/newbot` → token into
   `TELEGRAM_BOT_TOKEN`. Open the bot's `t.me` link and send it any message (a bot can't start
   a conversation), then read your chat ID:
   ```bash
   curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" \
     | jq '.result[-1].message.chat.id'      # → TELEGRAM_CHAT_ID
   ```
   Only that chat can trigger calls.

### Your profile

`profile.json` holds your standing preferences: the places you order from, their menus and
phone numbers, and defaults like size or milk. Menus do the routing — if only one place lists
an item, that's where the call goes. See `profile.example.json` for the format.

> **Start safe:** put your *own* phone number on every place until the agent behaves the way
> you want, and play the shop yourself.

## Run

```bash
./scripts/dev.sh
```

This starts the service and an ngrok tunnel, checks the webhook path end to end, and (on macOS)
holds `caffeinate` so the machine can't sleep mid-call. Ctrl-C stops everything.

To run just the service: `.venv/bin/python cmd/otrango.py`. It starts without credentials; the
console, mandates and order parser all work, and only dialing needs Vapi.

The operator console is at `http://localhost:8080`.

## Place an order

**Telegram:**

```
you → coffee
←     1 filter coffee (small) for pickup · cap $7.25 — reply Y to dial
you → Y
←     ☕ filter coffee confirmed · $4.75 · ready 4:12 · #47 — reply N if wrong
```

**Console:** type an order, review the cap, click **Authorize & dial**.

**Terminal:** `.venv/bin/python cmd/otrango_shell.py`

**HTTP:**

```bash
MID=$(curl -s -X POST localhost:8080/api/mandates \
  -H 'Content-Type: application/json' -d '{"input":"latte","dry_run":true}' | jq -r .id)
curl -X POST localhost:8080/api/mandates/$MID/authorize
```

**Use `dry_run` first.** It runs the whole conversation and hangs up at the commit point, so
you get a real call with no order placed.

## Evals

Twelve scripted scenarios (aggressive upsells, quotes above the cap, unavailable items, a shop
that refuses automated callers, etc.) replay against the real prompt, tools and server-side checks. Each asserts on the
recorded outcome, never on transcript text.

```bash
.venv/bin/python cmd/otrango_eval.py                     # text replay, seconds, a few cents
.venv/bin/python cmd/otrango_eval.py --tier 1            # + voice-shaped scenarios
.venv/bin/python cmd/otrango_eval.py --scenario upsell -v
```

The pass bar is at least 10/12, with **zero false successes** and zero calls that never end.

## Adding a use case

Verticals live in `otrango/skills/` (`coffee/` and `hours/` ship today). The core —
`objective/`, `usecase/`, `store/`, `vapi/`, `turns/` — knows nothing about coffee, and
`tests/test_skills_boundary.py` fails the build if it ever starts to. A new skill is a new
package plus a line in `skills/register.py`.

## Layout

```
cmd/otrango.py              the service
cmd/otrango_assistant.py    emits the Vapi assistant config
cmd/otrango_eval.py         scenario suite
cmd/otrango_shell.py        talk to the agent in a terminal

otrango/objective/   domain: call, mandate, constraints, outcome
otrango/turns/       agent-to-agent timing metrics
otrango/usecase/     ordering, dialing, tools, outcomes, messaging, reconciler
otrango/store/       SQLite, migrations, idempotent event sink
otrango/vapi/        Vapi client
otrango/telegram/    Telegram long-poll in, replies out
otrango/httpapi/     HTTP API and webhooks (Flask)
otrango/console/     operator UI
otrango/skills/      verticals: coffee, hours
otrango/agent/       tool contract shared by Vapi and the evals
```

Dependencies point inward: `objective`, `usecase` and `turns` import no infrastructure, so the
rules are tested with in-memory fakes.

## Notes

- **Outcomes are write-once.** The first terminal record wins; a late webhook can't overwrite it.
- **Webhooks are idempotent** and the raw body is always stored, so anything mis-parsed is
  recoverable from the database.
- **No LLM in the trigger path.** `coffee` / `usual` / `Y` / `N` are exact keyword matches.
- The built-in server is fine for local use. Put gunicorn or waitress in front of it for
  anything more.

## License

[MIT](LICENSE)
