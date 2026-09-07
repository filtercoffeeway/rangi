"""Package coffee is the Phase 1 vertical: order one coffee for pickup.

It imports objective (the authority layer) but nothing from usecase, store,
or httpapi -- the dependency direction the Phase 4 zero-diff proof relies on.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from otrango.objective.mandate import Constraints, Mandate, Proposal
from otrango.objective.outcome import Outcome, RESULT_AMBIGUOUS, RESULT_ESCALATED, RESULT_REFUSED, RESULT_SUCCESS, RESULT_UNREACH
from otrango.objective import money
from otrango.profile.profile import Match, Profile, ProfileError
from otrango.timefmt import kitchen

TYPE_NAME = "coffee_order"

# PREFERENCE_KEYS are the recognised axes of Item.preferences /
# Defaults.preferences -- what build() resolves and what prompt() gives the
# agent guidance for. (label) is how each reads in that guidance; the JSON
# key is always the plain field name. Add a new axis here and it is wired
# everywhere with no other code change.
PREFERENCE_KEYS = ("milk", "temperature", "size", "sugar", "dine_in")
PREFERENCE_LABELS = {"dine_in": "for here or to go"}


@dataclass
class Spec:
    """The coffee-specific payload stored on the mandate."""

    item: str
    # as_requested is the owner's own phrasing ("filter coffee"), echoed
    # back so a confirmation reads in their words rather than the menu's.
    as_requested: str = ""
    # business, phone and note come from the profile: where this order goes
    # is a property of what was ordered, not of global configuration.
    business: str = ""
    phone: str = ""
    note: str = ""
    price_cents: int = 0
    # preferences are the answer to give if the shop asks -- milk,
    # temperature, sugar, size, dine_in, whatever the profile has. Resolved
    # at build() time from, in order, what the order itself said, the
    # item's own standing preference, then the profile's global one. A key
    # missing here means genuinely no preference anywhere.
    #
    # preferences_requested marks which of those came from the order text
    # itself, as opposed to being the standing default. That is what
    # decides whether it gets said UP FRONT: a real request is spoken as
    # part of placing the order (spoken_item()); a standing default is held
    # back and only given if the shop asks (prompt()'s pref_line) --
    # "regular filter coffee" is not how anyone orders a plain coffee, but
    # it is a fine answer to "regular or vegan?".
    preferences: dict[str, str] = field(default_factory=dict)
    preferences_requested: dict[str, bool] = field(default_factory=dict)
    qty: int = 1
    customer_name: str = ""
    pickup_note: str = ""

    def display(self) -> str:
        """Prefers the owner's own words for the item ("filter coffee")
        over the menu's name, because that is what the shop will
        recognise.
        """
        return self.as_requested or self.item

    def business_or(self, fallback: str) -> str:
        return self.business or fallback

    def spoken_item(self) -> str:
        """The order as it should be said UP FRONT, when first placing it --
        modifiers first, e.g. "oat milk filter coffee" -- but only for a
        preference the order actually asked for. A standing default that
        merely fills in the answer for later is not volunteered here; see
        prompt()'s pref_line for that.

        Only milk and temperature ever fold into the item's own name this
        way -- they are the two axes an order-time request can ask for by
        name (see parse.Order). The rest (sugar, size, dine_in) are always
        answer-only; see prompt().
        """
        words = self.display()
        milk = self.preferences.get("milk", "")
        if milk and self.preferences_requested.get("milk"):
            words = f"{milk} milk {words}"
        temperature = self.preferences.get("temperature", "")
        if temperature and self.preferences_requested.get("temperature"):
            words = f"{temperature} {words}"
        return words

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        # omitempty parity with the Go struct tags.
        for k in ("as_requested", "business", "phone", "note", "price_cents", "pickup_note",
                   "preferences", "preferences_requested"):
            if not d[k]:
                d.pop(k)
        return d

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Spec":
        return Spec(
            item=d.get("item") or "",
            as_requested=d.get("as_requested") or "",
            business=d.get("business") or "",
            phone=d.get("phone") or "",
            note=d.get("note") or "",
            price_cents=int(d.get("price_cents") or 0),
            preferences=dict(d.get("preferences") or {}),
            preferences_requested=dict(d.get("preferences_requested") or {}),
            qty=int(d.get("qty") or 1),
            customer_name=d.get("customer_name") or "",
            pickup_note=d.get("pickup_note") or "",
        )


class ErrAsk(Exception):
    """Carries a question back to the owner when the request cannot be
    turned into one order. Asking beats guessing: this spends their money.
    """

    def __init__(self, question: str):
        super().__init__(question)
        self.question = question


class Skill:
    def __init__(self, customer_name: str, profile: Profile):
        self.customer_name = customer_name
        self.profile = profile
        # Optional: a model-assisted parser. Its output is still validated
        # against the profile, and a failure falls back to matching rather
        # than refusing the order.
        self.parser = None

    def with_parser(self, parser) -> "Skill":
        self.parser = parser
        return self

    def type(self) -> str:
        return TYPE_NAME

    def matches(self, input_: str) -> bool:
        """Asks the profile, so adding an item to a menu makes it orderable
        without touching this module.
        """
        in_ = input_.strip().lower()
        if "usual" in in_ or self.profile.knows(in_):
            return True
        # With a parser, anything unrecognised is still worth a look --
        # "something warm" and "filtr coffe" are orders that substring
        # matching cannot see. The registry tries the specific verticals
        # first, so this claims only what nothing else wanted.
        return self.parser is not None

    def build(self, input_: str) -> tuple[dict[str, Any], Constraints]:
        m, qty, note, milk_asked, temperature_asked = self._resolve(input_)

        # Precedence per key: what this order actually said (milk and
        # temperature only -- the two an order-time request can name; see
        # parse.Order), then the item's own standing preference, then the
        # profile's general one. Never invented -- a key absent from all
        # three just does not appear. Whether it came from the order text
        # is kept separately in preferences_requested: that is what tells
        # the prompt whether to say it up front or only on ask.
        asked = {"milk": milk_asked, "temperature": temperature_asked}
        preferences: dict[str, str] = {}
        preferences_requested: dict[str, bool] = {}
        for key in PREFERENCE_KEYS:
            value = asked.get(key, "") or m.item.preferences.get(key, "") \
                or self.profile.defaults.preferences.get(key, "")
            if value:
                preferences[key] = value
            if asked.get(key):
                preferences_requested[key] = True

        spec = Spec(
            item=m.item.name,
            as_requested=m.as_requested,
            business=m.place.name,
            phone=m.place.phone,
            note=m.place.note,
            price_cents=m.item.price_cents,
            preferences=preferences,
            preferences_requested=preferences_requested,
            qty=qty,
            pickup_note=note,
            customer_name=self.customer_name,
        )

        # Headroom over list price absorbs tax or a price rise without
        # letting an upsell through. Enforced server-side, never in the
        # prompt.
        c = Constraints(
            max_total_cents=self.profile.cap_for_qty(m, qty),
            latest_completion=datetime.now(timezone.utc)
            + timedelta(minutes=self.profile.defaults.pickup_within_minutes),
            allow_substitutions=False,
            max_duration_sec=180,
            max_turns=40,
        )
        return spec.to_json(), c

    def _resolve(self, input_: str) -> tuple[Match, int, str, str, str]:
        """Reads the request. The model gets first refusal because it
        handles quantity and free phrasing; substring matching is the
        fallback, so an API outage degrades the feature rather than the
        service.

        milk and temperature come back as "" from the keyword-matching
        fallback -- it cannot read free phrasing -- which is fine: build()
        still has the item's and the profile's standing preference to fall
        back to.
        """
        if self.parser is not None:
            try:
                o = self.parser.parse(input_)
            except Exception:
                o = None  # Fall through to matching.
            if o is not None:
                if o.unclear:
                    raise ErrAsk(o.unclear)
                try:
                    m = self.profile.confirm(o.item, o.place, o.as_requested)
                    return m, max(o.qty, 1), o.note, o.milk, o.temperature
                except ProfileError:
                    pass  # The model named something not on a menu; try matching.
        m = self.profile.resolve(input_)
        return m, 1, "", "", ""

    def target(self, spec: dict[str, Any]) -> str:
        """Tells the use case which number this order should dial. The
        place is a property of what was ordered, not of global config.
        """
        return spec.get("phone") or ""

    def prompt(self, m: Mandate) -> str:
        spec = Spec.from_json(m.spec)

        dry_run = ""
        if m.constraints.dry_run:
            dry_run = (
                "\n## DRY RUN\nThis is a rehearsal. Conduct the whole conversation normally, "
                'but stop before the order is agreed: say "Sorry — I\'ll have to call back," '
                "then call endCall. Nothing more. Never let a real order stand.\n"
            )

        subs = "Do not accept a different item instead."
        if m.constraints.allow_substitutions:
            subs = "A sensible substitute is acceptable if they are out of it."

        qty = max(spec.qty, 1)
        display = spec.display()
        order_words = spec.spoken_item()
        business = spec.business_or("the shop")
        customer = spec.customer_name

        pref_line = "\n".join(filter(None, [
            _pref_guidance(
                PREFERENCE_LABELS.get(key, key),
                spec.preferences.get(key, ""),
                spec.preferences_requested.get(key, False),
            )
            for key in PREFERENCE_KEYS
        ]))
        if pref_line:
            pref_line = "\n" + pref_line

        return f"""You are placing a phone order with {business} on behalf of {customer}.

## You have already spoken
Your greeting has played. Do NOT introduce yourself again and do not repeat that
you are an automated assistant. Your first turn continues from there.

## The order
{qty} x {order_words}, for pickup, under the name {customer}.
Say it in those words — that is what this shop calls it, plus anything above you
actually asked for. Do not translate it into a different name.
The name on the order is {customer}. You already have it. Never ask them what the name
is — you are the customer; the name is yours to give, not theirs to know.
{subs}{pref_line}
Payment is at the counter. Do not discuss payment or offer card details.

## Which side of this call you are on
You are the customer. They run the shop. Everything about taking an order,
confirming one, quoting a price, or naming a pickup time belongs to them, and
none of it is yours to say. You place the order and you answer their questions.
That is the whole of your role.

## Your lines
These are the only things you say. Nothing here is a question except the last
one, and that one is conditional.

  to place the order   "I'd like to order {qty} {order_words}."
  asked if that is all "No, that's it."
  offered an extra     "No, not today."
  asked for a name     "{customer}"
  no time given yet    "What time will that be ready?"
  to finish            "Thanks, see you then."   then call endCall

Say one of these, then stop and listen. Do not join two together. Do not add a
preamble, an apology, or an explanation to any of them.

## Things that are theirs, not yours
Never say any of these, in any wording:
- Reading the order back — "so that's one {display}, pickup at five, under {customer}". They
  confirm the order to you. You do not confirm it to them.
- Naming a pickup time, an order number, or a total. You may only ask for the
  time, and only if they have not said it.
- Asking what name the order is under. The name is {customer}; it is yours to give.
- Asking what it costs, or anything about payment.
- Announcing that you are checking, confirming, or need a moment. Your checks
  are silent. Say nothing while one is running.

If you catch yourself about to say a sentence that a person behind the counter
would say, stop — it is not your line.

## Before you agree to the order
- Call propose_order. If they stated a total, pass exactly that number; if they
  never mentioned one — the usual case — call it without a total. Its reply
  tells you exactly what to do next -- follow it, including if it tells you to
  call propose_order again once you have a pickup time.
- If it returns approved=false you may NOT confirm. Say politely that you cannot
  proceed, and end the call.
- If it does not answer, errors, or times out, treat that EXACTLY as
  approved=false. Silence is not permission. Say you have a technical problem
  and will call back, then end the call.
- Call record_outcome before hanging up, whatever happened.

## The pickup time
It is the one thing that proves the order was taken. Do not trust your own
memory of whether they have said it -- it is easy to miss when they fold it
into another sentence ("thanks, that'll be ready in ten minutes" answers a
different question but still says the time). The moment you hear anything
that sounds like a time, call propose_order again with it, even though you
already called it once -- its reply will confirm whether you are done. Only
ask directly -- "What time will that be ready?" -- once, and only once
propose_order has told you it is still missing after a couple more of their
turns.

## Ending the call
You have an endCall tool. It is the only way this call ends on your terms.
As soon as record_outcome comes back, close in the same turn and call endCall.
Say exactly one of these, never anything else:
- Their last line was "anything else?" or offering an extra: your close
  answers that too — "No, that's it — thanks, see you then."
- Anything else, including silence: "Thanks, see you then."
Do not wait for them to hang up first, and do not thank them a second time. If
they are still talking, let them finish that sentence, then say your line and
call endCall anyway. A call that does not end costs money and blocks the line.

## Talking to another machine
The number may be answered by another automated system.
- After about two seconds of silence, assume they are waiting and speak.
- If you talk over each other, stop and let them go first.
- If the same exchange repeats a third time, state the order once more and end
  the call. Do not try to get them to confirm it — that is their move to make.
- Do not prolong the call out of politeness.{dry_run}"""

    def validate_proposal(self, m: Mandate, p: Proposal) -> None:
        """Applies the vertical's own rule: the item has to be the one that
        was ordered. Generic limits ran before this.
        """
        spec = Spec.from_json(m.spec)
        if not p.items:
            raise ValueError("no items in the proposal")
        for li in p.items:
            if not _same_item(li.name, spec) and not m.substitution_allowed(li.name):
                raise ValueError(f"{li.name!r} was not ordered and substitutions are not allowed")
            if li.qty > spec.qty and spec.qty > 0:
                raise ValueError(f"proposal has {li.qty} x {li.name}, but only {spec.qty} was ordered")

    def preview(self, m: Mandate) -> str:
        """What the owner authorizes: one sentence, in their words."""
        spec = Spec.from_json(m.spec)
        line = f"Ordering {max(spec.qty, 1)} {spec.spoken_item()} from {spec.business_or('the restaurant')}. Pickup asap."
        if spec.note:
            line += " " + spec.note[0].upper() + spec.note[1:] + "."
        return line

    def greeting(self, m: Mandate) -> str:
        spec = Spec.from_json(m.spec)
        return f"Hi, this is an automated assistant calling on behalf of {spec.customer_name} to place a pickup order."

    def calling(self, m: Mandate) -> str:
        """What the owner sees the moment they authorize, before the call
        has produced anything. Naming the place confirms the routing was
        right while there is still time to notice it was not.
        """
        spec = Spec.from_json(m.spec)
        return f"Calling {spec.business_or('the restaurant')} to place the order. Hang tight."

    def summary(self, m: Mandate, o: Outcome) -> str:
        spec = Spec.from_json(m.spec)

        if o.result == RESULT_SUCCESS:
            msg = f"Order placed. {max(spec.qty, 1)} {spec.spoken_item()}."
            if o.ready_at is not None:
                msg += " Pickup time " + kitchen(o.ready_at).lower() + "."
            if o.total_cents is not None:
                msg += " " + money(o.total_cents) + "."
            if o.confirmation_ref:
                msg += " Order #" + o.confirmation_ref + "."
            return msg + " Pay at store. Reply N to make changes to order."
        if o.result == RESULT_AMBIGUOUS:
            return f"⚠️ {spec.item}: unclear whether the order went through ({o.failure_class}). Worth a call."
        if o.result == RESULT_REFUSED:
            return "🚫 They don't accept automated calls. Nothing was ordered."
        if o.result == RESULT_ESCALATED:
            return f"✋ Stopped: {o.notes}. Nothing was ordered."
        if o.result == RESULT_UNREACH:
            return f"📵 Couldn't reach them ({o.failure_class}). Nothing was ordered."
        return f"❌ {spec.item} failed ({o.failure_class}). Nothing was ordered."


def _pref_guidance(label: str, value: str, requested: bool) -> str:
    """One line telling the agent what to do if the shop asks about this
    axis (milk, temperature). Three cases:

    - Asked for by name this time: it is already in the opening line: just
      repeat it, and do not re-offer it as if it were still open.
    - A standing default exists but was not volunteered: give the answer,
      but only when asked -- unprompted it would read as a customer who
      over-specifies a plain order.
    - Nothing on file either way: refuse to invent one.
    """
    if requested:
        return (
            f"- {label}: already stated above. If they ask you to confirm it, repeat that. "
            "Do not bring it up again unasked, and do not offer a different one."
        )
    if value:
        return (
            f'- {label}: not mentioned yet. If they ask, say "{value}". Do not bring it up '
            "yourself."
        )
    return (
        f'- {label}: nothing on file. If they ask, say "whatever you have is fine" -- do not '
        "invent a preference and do not press them for a choice."
    )


def _same_item(quoted: str, spec: Spec) -> bool:
    """Tolerates the counterparty's phrasing: they may say "filter coffee"
    where the menu says "drip coffee", or vice versa.
    """
    q = quoted.strip().lower()
    for known in (spec.item, spec.as_requested):
        k = known.lower()
        if k and (q in k or k in q):
            return True
    return False
