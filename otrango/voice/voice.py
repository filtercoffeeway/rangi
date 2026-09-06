"""Package voice selects which synthesised voice a call speaks with.

Voice is an experimental variable, not a cosmetic setting. Time-to-first-byte
differs between models, and TTS latency feeds directly into the inter-turn
gaps that produce the agent-to-agent pathologies in PLAN.md S3.2 -- a slower
voice can manufacture the deadlocks we are trying to measure. So a profile is
selectable per call and recorded against the call, letting turn metrics be
grouped by voice instead of silently confounded by it (RESEARCH.md S2.4).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# The two profiles the eval sweep alternates between.
NAME_AGENT = "agent"  # stock provider voice
NAME_MINE = "mine"  # the operator's cloned voice


@dataclass
class Profile:
    name: str
    label: str = ""
    provider: str = ""
    voice_id: str = ""
    model: str = ""

    def configured(self) -> bool:
        """Reports whether this profile can actually be sent to the
        provider. An unconfigured profile is not an error: the call simply
        falls back to whatever the assistant already has, which is how the
        service stays runnable before any voice has been cloned.
        """
        return bool(self.provider) and bool(self.voice_id)


class Registry:
    def __init__(self, default_name: str, *profiles: Profile):
        self._by_name: dict[str, Profile] = {}
        self._order: list[str] = []
        for p in profiles:
            self._by_name[p.name] = p
            self._order.append(p.name)
        self._fallback = NAME_AGENT
        if default_name and default_name in self._by_name:
            self._fallback = default_name

    def get(self, name: str) -> Profile:
        return get(self, name)

    def default(self) -> Profile:
        return get(self, self._fallback)

    def default_name(self) -> str:
        return default_name(self)

    def names(self) -> list[str]:
        return names(self)

    def comparable(self) -> bool:
        return comparable(self)


# The functions below take an Optional[Registry] and degrade gracefully when
# it is None -- mirroring the Go original's nil-receiver behaviour: a
# hand-built Config (a test, or a caller that predates voice support) should
# degrade to "no voice override", not crash mid-dial.


def get(registry: Optional[Registry], name: str) -> Profile:
    name = (name or "").strip().lower()
    if registry is None:
        if name in ("", NAME_AGENT):
            return Profile(name=NAME_AGENT, label="Stock agent voice")
        raise ValueError(f"no voice registry configured, cannot resolve {name!r}")
    if name == "":
        name = registry._fallback
    p = registry._by_name.get(name)
    if p is None:
        raise ValueError(f"unknown voice {name!r} (have: {', '.join(names(registry))})")
    return p


def default_name(registry: Optional[Registry]) -> str:
    if registry is None:
        return NAME_AGENT
    return registry._fallback


def names(registry: Optional[Registry]) -> list[str]:
    if registry is None:
        return [NAME_AGENT]
    return sorted(registry._order)


def comparable(registry: Optional[Registry]) -> bool:
    """Reports whether an A/B sweep is currently possible: both profiles
    present and configured. The eval harness uses this to skip a voice
    sweep rather than silently running the same voice twice and reporting a
    null result.
    """
    if registry is None:
        return False
    try:
        a = get(registry, NAME_AGENT)
        b = get(registry, NAME_MINE)
    except ValueError:
        return False
    return a.configured() and b.configured()
