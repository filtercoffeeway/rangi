"""Package skills holds the vertical-specific layer and its registry.

A Skill knows about one domain -- coffee, opening hours -- and nothing about
telephony, mandates, or persistence. Verticals satisfy this interface
structurally (duck typing), so a skill module never imports this one and the
registry can import every skill without a cycle.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from otrango.objective.mandate import Constraints, Mandate, Proposal
from otrango.objective.outcome import Outcome


@runtime_checkable
class Skill(Protocol):
    def type(self) -> str:
        """The objective_type stored on the mandate, e.g. "coffee_order"."""
        ...

    def build(self, input_: str) -> tuple[dict[str, Any], Constraints]:
        """Turns a trigger phrase ("coffee", "usual") into a spec and the
        constraints that bound it.
        """
        ...

    def prompt(self, m: Mandate) -> str:
        """Renders the system prompt for a call under this mandate."""
        ...

    def preview(self, m: Mandate) -> str:
        """Describes a mandate that has not been acted on yet -- the text
        the owner authorizes against.
        """
        ...

    def validate_proposal(self, m: Mandate, p: Proposal) -> Optional[Exception]:
        """Applies vertical rules -- is this on the menu, is the
        substitution sensible. Generic limits (price cap, deadline, dry
        run) are already enforced by Mandate.evaluate before this is
        called. Return None on success, or raise/return an exception.
        """
        ...

    def greeting(self, m: Mandate) -> str:
        """The first thing the counterparty hears."""
        ...

    def calling(self, m: Mandate) -> str:
        """What the owner sees the instant they authorize."""
        ...

    def summary(self, m: Mandate, o: Outcome) -> str:
        """The one-line human summary sent to the owner."""
        ...


class Registry:
    """Keeps skills in registration order as well as by type. The order
    matters: if two skills both match an input, the first registered wins,
    and that must be deterministic. Iterating a dict here would make the
    winner vary between runs -- a trigger that resolves differently on
    Tuesday.
    """

    def __init__(self, *skills: Skill):
        self._ordered: list[Skill] = list(skills)
        self._by_type: dict[str, Skill] = {s.type(): s for s in skills}

    def get(self, objective_type: str) -> Skill:
        s = self._by_type.get(objective_type)
        if s is None:
            raise KeyError(f"no skill registered for objective type {objective_type!r}")
        return s

    def types(self) -> list[str]:
        return [s.type() for s in self._ordered]

    def resolve(self, input_: str) -> Skill:
        """Picks the first registered skill whose trigger phrases match.
        Deliberately a keyword match, not an LLM -- a model in the trigger
        path buys nothing and adds latency plus a failure mode (PLAN.md
        S7.3b).
        """
        for s in self._ordered:
            matches = getattr(s, "matches", None)
            if matches is not None and matches(input_):
                return s
        raise KeyError(f"no skill understands {input_!r}")
