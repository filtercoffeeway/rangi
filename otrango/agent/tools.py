"""Package agent holds the tool contract the voice agent is given.

One definition, used by both the Vapi assistant config and the T0 eval
tier. If these diverged, the evals would be testing a different agent than
the one that ships -- which is the failure mode the whole harness exists to
prevent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    schema: dict[str, Any]


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


def _str(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


def _num(desc: str) -> dict[str, Any]:
    return {"type": "integer", "description": desc}


# Tools exist only for what the model cannot know: the mandate it is
# operating under, and the writes it needs to make. The menu is fixed and
# small, so it lives in the prompt -- a get_menu tool against a static file
# would be pure blocking latency (PLAN.md S8 row 4).
Tools: list[ToolDef] = [
    ToolDef(
        name="get_call_context",
        description=(
            "Retrieve the order you are placing and the limits you are operating under. "
            "Call this once at the start if you are unsure what you were asked to do."
        ),
        schema=_obj({}),
    ),
    ToolDef(
        name="propose_order",
        description=(
            "Check the order against your authorization BEFORE agreeing to it. "
            "You must call this and receive approved=true before confirming any order. "
            "If it returns approved=false you may not proceed. "
            "Call it even if no price has been mentioned — most small shops never quote "
            "one, and that is fine. Leave total_cents out in that case. Never ask for a "
            "price just to fill this in."
        ),
        schema=_obj({
            "items": {
                "type": "array",
                "description": "The items as the other party has quoted them.",
                "items": _obj({
                    "name": _str("Item name as it appears on the menu."),
                    "qty": _num("How many."),
                    "unit_cents": _num("Price per unit in cents."),
                }, ["name", "qty"]),
            },
            "total_cents": _num(
                "The total the other party quoted, in cents. OMIT THIS "
                "if they did not say a price. Never guess it and never use the price you "
                "were told to expect — only a number they actually said out loud."
            ),
            "ready_at": _str('When it will be ready, e.g. "4:15 PM". Optional.'),
            "notes": _str("Anything notable about the quote. Optional."),
        }, ["items"]),
    ),
    ToolDef(
        name="escalate",
        description=(
            "Report that something is off that you cannot resolve within your "
            "authorization. Returns the action to take. Do not use this for a simple "
            "price rejection — propose_order already handles that."
        ),
        schema=_obj({"reason": _str("What went wrong, in one sentence.")}, ["reason"]),
    ),
    ToolDef(
        name="record_outcome",
        description=(
            "Record how the call ended. REQUIRED before you hang up, on every call, "
            'whatever the result. Be honest: if you are not sure the order was '
            'understood, use result "ambiguous", not "success".'
        ),
        schema=_obj({
            "result": {
                "type": "string",
                "enum": ["success", "partial", "failed", "escalated",
                         "refused", "unreachable", "ambiguous"],
                # Each value is spelled out because the model otherwise
                # reaches for whichever word matches its own action rather
                # than the state of the world: it recorded "refused" and
                # "unreachable" to mean "I declined on price", which the
                # taxonomy reserves for the counterparty rejecting
                # automated callers and for never reaching anyone at all.
                "description": (
                    "How the call ended, from the world's point of view, not yours. "
                    "success = the other party clearly confirmed the order. "
                    "partial = they confirmed something, but not what was asked for. "
                    "failed = the order could not be placed, e.g. the price was not authorized "
                    "or the item was unavailable. Use this when YOU declined. "
                    "escalated = you stopped because something needs the owner's decision. "
                    "refused = the other party refused to deal with an automated caller. "
                    "Do not use this for a price or stock disagreement. "
                    "unreachable = nobody answered, the line was busy, or you got voicemail. "
                    "Do not use this if somebody spoke to you. "
                    "ambiguous = somebody spoke, but you cannot tell whether the order was placed."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "How sure you are that this is what actually happened.",
            },
            "failure_class": _str("If it did not succeed, the closest reason code."),
            "total_cents": _num("Final total in cents, if there was one."),
            "ready_at": _str(
                'When it will be ready, e.g. "5:05 PM" or "ten minutes". '
                "Get this — it is the main thing that proves the order was taken."
            ),
            "confirmation_ref": _str(
                "Order number, ONLY if they volunteer one. Most small "
                "shops do not give one. Never ask for it. Leave empty."
            ),
            "notes": _str("A one-line summary of what happened."),
            "items": {
                "type": "array",
                "items": _obj({"name": _str(""), "qty": _num(""), "unit_cents": _num("")}),
            },
        }, ["result", "confidence"]),
    ),
]


def vapi_tool_specs(server_url: str) -> list[dict[str, Any]]:
    """Renders the tool list in the shape a Vapi assistant expects."""
    out = []
    for t in Tools:
        out.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.schema,
            },
            "server": {"url": server_url},
            # Silence the filler. By default the provider narrates the
            # wait ("Just a sec", "One moment") which, on the first live
            # call, fired four times and made a broken tunnel sound like a
            # distracted human. A checked order should not announce that it
            # is being checked; the pause is short enough to read as
            # thinking.
            "messages": [
                {"type": "request-start", "content": ""},
                {"type": "request-response-delayed", "content": "", "timingMilliseconds": 30000},
            ],
        })
    return out
