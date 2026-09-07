"""Package parse turns what the owner typed into a structured order.

The model reads the request; it does not decide anything. It returns names
and a quantity, and the profile then has to recognise every one of them --
same division as propose_order, where the model proposes and deterministic
code disposes. A hallucinated item or a place that is not in the profile is
rejected rather than dialled.

It also never writes the confirmation. That sentence is what the owner
authorizes, so it stays rendered from the validated structure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import anthropic

from otrango.profile.profile import Profile

# TIMEOUT bounds the draft path. This never runs during a call, so it is
# not on the conversational latency budget -- but an owner waiting on a
# text should not wait long either.
TIMEOUT = 6.0


@dataclass
class Order:
    """What the model extracted. Every field is a claim to be checked."""

    item: str = ""
    place: str = ""
    qty: int = 1
    as_requested: str = ""
    note: str = ""
    # milk and temperature are set only when the owner actually said one --
    # e.g. "oat milk iced coffee". Empty means unspecified, not "none": the
    # skill falls back to the item's or the profile's standing preference.
    milk: str = ""
    temperature: str = ""
    # unclear is set when the request cannot be turned into one order. It
    # carries the question to put back to the owner rather than a guess.
    unclear: str = ""


class Parser(Protocol):
    def parse(self, input_: str) -> Order: ...


_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "item": {"type": "string",
                  "description": "The menu item, spelled exactly as it appears in the menus "
                                 "above. Empty if unclear."},
        "place": {"type": "string",
                  "description": "The place, spelled exactly as given above. Empty if they did "
                                 "not say and only one place sells the item."},
        "qty": {"type": "integer", "description": "How many. Default 1."},
        "as_requested": {"type": "string",
                          "description": 'The words THEY used for the item, e.g. "kaapi". '
                                         "Keep their phrasing."},
        "note": {"type": "string",
                 "description": 'Any instruction that is not item, place, quantity, milk or '
                                'temperature, e.g. "extra hot". Empty if none.'},
        "milk": {"type": "string",
                 "description": 'Milk preference ONLY if they said one, e.g. "oat", "almond", '
                                '"whole", "regular". Empty if not mentioned -- leave it to the '
                                "standing preference on file. Never invent one."},
        "temperature": {"type": "string",
                         "description": 'Hot or cold/iced, ONLY if they said one. Empty if not '
                                        "mentioned -- leave it to the standing preference on file. "
                                        "Never invent one."},
        "unclear": {"type": "string",
                    "description": "If you cannot work out a single order, the short question "
                                   "to ask them. Empty otherwise. Prefer asking over guessing."},
    },
    "required": ["item", "qty", "unclear"],
}


class LLM:
    def __init__(self, api_key: str, model: str, profile: Profile):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.profile = profile

    def parse(self, input_: str) -> Order:
        resp = self.client.with_options(timeout=TIMEOUT).messages.create(
            model=self.model,
            max_tokens=512,
            system=self._system(),
            messages=[{"role": "user", "content": input_}],
            tools=[{
                "name": "order",
                "description": "Record the order the person is asking for.",
                "input_schema": _TOOL_SCHEMA,
            }],
            tool_choice={"type": "tool", "name": "order"},
        )

        for block in resp.content:
            if block.type == "tool_use":
                d = block.input
                o = Order(
                    item=d.get("item") or "", place=d.get("place") or "",
                    qty=int(d.get("qty") or 0), as_requested=d.get("as_requested") or "",
                    note=d.get("note") or "",
                    milk=d.get("milk") or "", temperature=d.get("temperature") or "",
                    unclear=d.get("unclear") or "",
                )
                if o.qty <= 0:
                    o.qty = 1
                return o
        raise RuntimeError("model returned no order")

    def _system(self) -> str:
        lines = [
            "You read short requests and turn them into one order. "
            "You do not talk to the person and you do not place anything -- another "
            "part of the system does that.",
            "",
            "These are the only places and items that exist:",
            "",
        ]
        for pl in self.profile.places:
            lines.append(pl.name)
            for it in pl.menu:
                line = f"  - {it.name} (${it.price_cents // 100}.{it.price_cents % 100:02d})"
                if it.aliases:
                    line += " also called: " + ", ".join(it.aliases)
                lines.append(line)
        if self.profile.prefer:
            lines.append("")
            lines.append("When the request is ambiguous, prefer:")
            for term, place in self.profile.prefer.items():
                lines.append(f'  - "{term}" -> {place}')
        lines.append("")
        lines.append(f'If they say "the usual", that means {self.profile.defaults.item}.')
        lines.append("")
        lines.append(
            "Rules:\n"
            "- Only ever name an item or place from the lists above. Never invent one.\n"
            "- If they ask for something that is not listed, set unclear and say what is available.\n"
            "- If several places sell what they asked for and they did not say which, set unclear and ask.\n"
            '- Keep their own wording in as_requested. If they said "kaapi", that is what goes there.\n'
            "- Asking is always better than guessing. This spends the owner's money."
        )
        return "\n".join(lines)
