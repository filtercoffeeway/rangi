#!/usr/bin/env bash
#
# Start everything needed to take a real call from this machine:
#   - the otrango service, including the Telegram poller that triggers orders
#   - the ngrok tunnel on the static domain from .env
#   - caffeinate, so a closing lid cannot kill a call in progress
#
# Ctrl-C stops all three.
#
# A laptop is a legitimate host for testing: the process is already warm, so
# there is no cold start, and ngrok's inspector at :4040 shows every webhook
# body Vapi sends. What a laptop cannot survive is sleeping or losing wifi
# mid-call, which is why caffeinate is not optional here.

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8080}"
LOG_DIR="${LOG_DIR:-/tmp}"
SVC_LOG="$LOG_DIR/otrango-svc.log"
NGROK_LOG="$LOG_DIR/otrango-ngrok.log"
PYTHON="${PYTHON:-.venv/bin/python}"

die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
ok()  { printf '\033[32m✓\033[0m %s\n' "$*"; }
note(){ printf '  %s\n' "$*"; }

# ---- preflight ------------------------------------------------------------

[ -f .env ] || die ".env not found. Copy .env.example and fill it in."
set -a; . ./.env; set +a

[ -x "$PYTHON" ] || die "$PYTHON not found. Run: python3 -m venv .venv && .venv/bin/pip install -e ."
command -v ngrok >/dev/null || die "ngrok not installed. brew install ngrok"
grep -q authtoken "$HOME/Library/Application Support/ngrok/ngrok.yml" 2>/dev/null \
  || die "ngrok has no authtoken. Run: ngrok config add-authtoken <token>"

[ -n "${PUBLIC_BASE_URL:-}" ] || die "PUBLIC_BASE_URL is empty in .env"
DOMAIN="${PUBLIC_BASE_URL#https://}"
DOMAIN="${DOMAIN#http://}"

# The service reads .env once at startup, so a stale process would silently
# serve the previous configuration.
pkill -f 'cmd/otrango\.py' 2>/dev/null || true
pkill -f "ngrok http" 2>/dev/null || true

# ---- start ----------------------------------------------------------------

cleanup() {
  printf '\n'
  kill $(jobs -p) 2>/dev/null || true
  pkill -f "ngrok http --url=$DOMAIN" 2>/dev/null || true
  ok "stopped"
}
trap cleanup EXIT INT TERM

"$PYTHON" cmd/otrango.py > "$SVC_LOG" 2>&1 &
SVC_PID=$!

# Supervised: the tunnel dying mid-call is silent otherwise, and every tool
# call then goes into a void while the agent is mid-conversation with someone.
( while true; do
    ngrok http --url="$DOMAIN" "$PORT" >> "$NGROK_LOG" 2>&1
    echo "$(date '+%H:%M:%S') ngrok exited; restarting" >> "$NGROK_LOG"
    sleep 2
  done ) &

# Keep the machine awake only for as long as the service actually runs.
caffeinate -i -w "$SVC_PID" &

# ---- wait for both, then prove the whole path ----------------------------

for i in $(seq 1 30); do
  curl -fsS -o /dev/null "http://localhost:$PORT/healthz" 2>/dev/null && break
  kill -0 "$SVC_PID" 2>/dev/null || { tail -20 "$SVC_LOG"; die "service exited"; }
  sleep 1
done
curl -fsS -o /dev/null "http://localhost:$PORT/healthz" 2>/dev/null \
  || { tail -20 "$SVC_LOG"; die "service never became healthy"; }
ok "service    http://localhost:$PORT"

for i in $(seq 1 30); do
  curl -fsS -o /dev/null "$PUBLIC_BASE_URL/healthz" 2>/dev/null && break
  sleep 1
done
curl -fsS -o /dev/null "$PUBLIC_BASE_URL/healthz" 2>/dev/null \
  || { tail -20 "$NGROK_LOG"; die "tunnel never reached the service"; }
ok "tunnel     $PUBLIC_BASE_URL"

# The check that actually predicts whether Vapi can talk to us: reachable from
# outside, and rejecting anything without the shared secret.
if [ -n "${VAPI_WEBHOOK_SECRET:-}" ]; then
  body='{"message":{"type":"status-update","status":"queued","call":{"id":"preflight"}}}'
  unauth=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$PUBLIC_BASE_URL/webhooks/vapi" \
    -H 'Content-Type: application/json' -d "$body")
  auth=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$PUBLIC_BASE_URL/webhooks/vapi" \
    -H 'Content-Type: application/json' -H "x-vapi-secret: $VAPI_WEBHOOK_SECRET" -d "$body")
  [ "$unauth" = "401" ] || die "webhook accepted an unauthenticated request ($unauth)"
  [ "$auth"   = "200" ] || die "webhook rejected a correctly signed request ($auth)"
  ok "webhook    401 without secret, 200 with it"
else
  printf '\033[33m!\033[0m webhook is UNAUTHENTICATED (VAPI_WEBHOOK_SECRET is empty)\n'
fi

# ---- readiness ------------------------------------------------------------

printf '\n'
if curl -fsS "http://localhost:$PORT/healthz" | grep -q '"vapi_ready":true'; then
  ok "vapi       configured — dialing available"
else
  printf '\033[33m!\033[0m vapi not configured — console works, dialing will fail\n'
  note "missing one of VAPI_API_KEY / VAPI_PHONE_NUMBER_ID / VAPI_ASSISTANT_ID"
fi

# The trigger. Everything above can be green while this is dead, and the way
# you find out is texting the bot during a demo and getting nothing back.
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  handle=$(curl -fsS --max-time 8 \
    "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe" 2>/dev/null \
    | sed -n 's/.*"username":"\([^"]*\)".*/\1/p') || true
  [ -n "$handle" ] || die "telegram rejected the bot token (getMe failed)"

  # A valid token is not the same as this process holding the poll. Telegram
  # allows one reader per bot, so a leftover otrango or a registered webhook
  # takes the trigger while the token still checks out.
  for _ in $(seq 1 15); do
    if grep -q "telegram: listening" "$SVC_LOG" 2>/dev/null; then break; fi
    sleep 1
  done
  if grep -q "409 Conflict" "$SVC_LOG" 2>/dev/null; then
    die "telegram: something else is already polling @$handle — stop it and retry"
  fi
  grep -q "telegram: listening" "$SVC_LOG" 2>/dev/null \
    || { tail -20 "$SVC_LOG"; die "telegram poller never started"; }
  ok "telegram   listening as @$handle"
else
  printf '\033[33m!\033[0m telegram not configured — console works, no chat trigger\n'
  note "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"
fi

printf '\n'
note "console    http://localhost:$PORT"
note "webhooks   http://localhost:4040   (every Vapi payload, replayable)"
note "logs       $SVC_LOG"
note "           $NGROK_LOG"
printf '\n\033[2mCtrl-C to stop everything.\033[0m\n'

# Keep proving the public path stays up. A tunnel that dies mid-call produces
# an agent that hears nothing back from its own server and improvises.
( while kill -0 "$SVC_PID" 2>/dev/null; do
    sleep 20
    if ! curl -fsS -o /dev/null --max-time 8 "$PUBLIC_BASE_URL/healthz" 2>/dev/null; then
      printf '\r\033[31m✗ tunnel unreachable\033[0m — %s  (restarting)\n' "$(date '+%H:%M:%S')"
      pkill -f "ngrok http --url=$DOMAIN" 2>/dev/null || true
    fi
  done ) &

wait "$SVC_PID"
