#!/usr/bin/env python3
"""Command otrango-assistant prints the Vapi assistant configuration.

    python cmd/otrango_assistant.py | curl -sS -X POST https://api.vapi.ai/assistant \\
      -H "Authorization: Bearer $VAPI_API_KEY" -H 'Content-Type: application/json' -d @-

The tool schemas come from otrango.agent, the same definitions the eval
harness uses -- so the assistant Vapi runs and the agent the evals test are
the same agent.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from otrango import agent, config as config_mod


def main() -> None:
    try:
        cfg = config_mod.load()
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    if not cfg.public_base_url:
        print("PUBLIC_BASE_URL is not set (your ngrok or Fly https URL)", file=sys.stderr)
        sys.exit(1)
    webhook = cfg.public_base_url + "/webhooks/vapi"

    assistant: dict[str, Any] = {
        "name": "otrango",

        # The per-call system prompt is supplied as an assistantOverride
        # at dial time, because it encodes the specific mandate. This
        # placeholder only applies if a call is ever placed without one.
        "model": {
            "provider": "anthropic",
            "model": cfg.llm_model,
            "messages": [{
                "role": "system",
                "content": "You are an ordering assistant. If you have not been given "
                            "a specific order, call get_call_context. If that fails, "
                            "apologise and end the call.",
            }],
            # endCall is Vapi's built-in hang-up. endCallFunctionEnabled
            # below is the legacy switch for it; listing the tool
            # explicitly is what actually puts it in front of the model,
            # and the prompt tells the model to call it. Without it the
            # agent has no way to hang up: on the second live call it
            # thanked the counterparty six times and waited out the
            # silence timeout instead.
            "tools": agent.vapi_tool_specs(webhook) + [{"type": "endCall"}],
        },

        # The assistant's default voice. Individual calls override this
        # per mandate, so one assistant serves both sides of a voice A/B
        # without being reconfigured between runs.
        "voice": _voice_block(cfg),

        # Wait for them to finish answering before speaking. A business
        # answers with its own greeting -- "this is Mylapore Express on a
        # recorded line, what can we get for you" -- and speaking over
        # that is both rude and the fastest way to lose the first ten
        # seconds to crosstalk.
        #
        # The risk this creates is the one in PLAN.md S3.2: if the far end
        # is also an agent configured to wait, neither speaks and the call
        # deadlocks. silenceTimeoutSeconds is the only breaker, so it is
        # short enough to fail fast rather than burn the duration cap in
        # silence.
        "firstMessageMode": "assistant-waits-for-user",
        "firstMessage": "Hi, this is an automated assistant calling to place a pickup order.",

        # When to start talking. The default is a flat 0.4s of silence,
        # which on a real call cut the counterparty off mid-sentence: a
        # person pausing to think reads as a finished turn. Smart
        # endpointing scores whether the utterance is actually complete
        # instead of timing the gap, and the transcription plan gives an
        # unpunctuated clause -- which is what a shop saying "that's four
        # seventy five and it'll be" looks like to the STT -- a longer
        # grace period than a sentence that has landed on a full stop.
        # Numbers get their own, longer wait because a price or a time is
        # read out in digit groups with gaps between them.
        "startSpeakingPlan": {
            "waitSeconds": 0.8,
            "smartEndpointingPlan": {"provider": "livekit"},
            "transcriptionEndpointingPlan": {
                "onPunctuationSeconds": 0.6,
                "onNoPunctuationSeconds": 1.8,
                "onNumberSeconds": 1.0,
            },
        },

        # When to stop talking. Backing off on a single stray word makes
        # the agent flinch at the counterparty's "mhm"; requiring two
        # words and a little voiced audio keeps a real interruption
        # working without turning every acknowledgement into a dropped
        # turn.
        "stopSpeakingPlan": {
            "numWords": 2,
            "voiceSeconds": 0.2,
            "backoffSeconds": 1.0,
        },

        # Termination guards. Against an AI counterparty these are the
        # only guaranteed way the call ends -- a bot will not hang up out
        # of impatience (PLAN.md S3.2). Overridden per call from the
        # mandate.
        "maxDurationSeconds": 180,
        "endCallFunctionEnabled": True,
        "silenceTimeoutSeconds": 12,

        "server": {"url": webhook, "secret": cfg.vapi_webhook_secret},
        "serverMessages": ["status-update", "end-of-call-report", "tool-calls"],
    }

    default_voice = cfg.voices.get("")
    if not default_voice.configured():
        print(f"note: no voice configured for profile {default_voice.name!r}; the assistant will "
              "use the provider default. Set VOICE_PROVIDER and VOICE_ID to pin it.", file=sys.stderr)

    print(json.dumps(assistant, indent=2))


def _voice_block(cfg) -> Optional[dict[str, Any]]:
    """Renders the default voice, or None when none is configured so the
    key is omitted rather than sent empty.
    """
    try:
        p = cfg.voices.get("")
    except Exception:
        return None
    if not p.configured():
        return None
    out: dict[str, Any] = {"provider": p.provider, "voiceId": p.voice_id}
    if p.model:
        out["model"] = p.model
    return out


if __name__ == "__main__":
    main()
