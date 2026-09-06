#!/usr/bin/env python3
"""Command otrango-eval runs the scenario suite (PLAN.md S6, Phase 1).

    python cmd/otrango_eval.py                 # T0 only — text replay, ~$0.05, seconds
    python cmd/otrango_eval.py --tier 1         # + voice-shaped scenarios
    python cmd/otrango_eval.py --scenario upsell -v

The pass bar is >=10/12 correct, zero false successes, zero
non-terminating calls. A false success is disqualifying on its own: it
means the owner is waiting for coffee that was never ordered.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from otrango import config as config_mod
from otrango.eval.harness import Harness
from otrango.eval.scenarios import by_tier, tier_name


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, default=0, help="max tier to run: 0 text, 1 voice, 2 pstn")
    ap.add_argument("--scenario", default="", help="substring match: run only matching scenarios")
    ap.add_argument("-v", "--verbose", action="store_true", help="print the transcript for every scenario")
    ap.add_argument("--voice", default="", help="voice profile to run with: agent | mine (T1/T2 only)")
    args = ap.parse_args()

    try:
        cfg = config_mod.load()
    except Exception as e:
        die(f"config: {e}")
        return
    if not cfg.anthropic_api_key:
        die("ANTHROPIC_API_KEY is not set (put it in .env)")
        return

    h = Harness(model=cfg.llm_model, api_key=cfg.anthropic_api_key)
    scenarios = by_tier(args.tier)
    if args.scenario:
        needle = args.scenario.lower()
        scenarios = [s for s in scenarios if needle in s.name.lower()]
    if not scenarios:
        die("no scenarios matched")
        return

    # T0 replays text: there is no audio, so voice cannot affect the
    # outcome. Saying so beats letting someone believe they ran a voice
    # A/B that the tier is structurally incapable of measuring.
    if args.voice:
        try:
            v = cfg.voices.get(args.voice)
        except Exception as e:
            die(str(e))
            return
        if args.tier == 0:
            print(f"note: -voice {v.name} ignored at T0 — the text tier synthesises no audio.")
        elif not v.configured():
            die(f"voice {v.name!r} is selected but not configured (set its provider and id)")
            return
        else:
            print(f"voice: {v.name} ({v.provider})")

    print(f"running {len(scenarios)} scenarios on {cfg.llm_model}\n")

    passed = 0
    false_success = 0
    for sc in scenarios:
        r = h.run(sc)

        if r.err is not None:
            print(f"  ERROR  [{tier_name(sc.tier)}] {sc.name:<42} {r.err}")
        elif r.passed:
            passed += 1
            print(f"  pass   [{tier_name(sc.tier)}] {sc.name:<42} → {r.got}")
        else:
            print(f"  FAIL   [{tier_name(sc.tier)}] {sc.name:<42} → {r.got}")
            for reason in r.reasons:
                print(f"           {reason}")
                if reason.startswith("FALSE SUCCESS"):
                    false_success += 1

        if args.verbose or (not r.passed and r.err is None):
            for line in r.transcript:
                print(f"           | {line}")

    print(f"\n{passed}/{len(scenarios)} passed", end="")
    if false_success > 0:
        print(f" · {false_success} FALSE SUCCESS", end="")
    print()

    if false_success > 0 or passed < len(scenarios):
        sys.exit(1)


if __name__ == "__main__":
    main()
