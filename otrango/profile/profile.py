"""Package profile holds the owner's standing preferences: where they order
from, what those places sell, and what they cost.

Routing falls out of the menus rather than a separate table -- if only one
place lists "filter coffee", that is where the call goes. `prefer` exists
only to break ties when several places sell the same thing.

This is vertical-agnostic on purpose (PLAN.md S2): a place with a menu is
also a barber with a price list.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Item:
    name: str
    price_cents: int = 0
    aliases: list[str] = field(default_factory=list)
    # cap_cents overrides the default headroom for this item alone. Use it
    # when one item warrants a different ceiling -- raising the global
    # headroom to suit a single item quietly loosens the limit on
    # everything else.
    cap_cents: int = 0
    # preferences are the owner's standing answers for this item -- milk,
    # temperature, sugar, size, dine_in, whatever comes up. Answer-only-if-
    # asked by default: the agent does not volunteer one unless the order
    # itself asked for it by name (coffee.Skill.build handles that
    # distinction; this is just where the standing value lives). A key
    # missing here falls back to Defaults.preferences, then to nothing --
    # blank means genuinely no preference, not a guess.
    preferences: dict[str, str] = field(default_factory=dict)

    def known_as(self, phrase: str) -> bool:
        """Reports whether `phrase` is a name this item genuinely goes by,
        and so may be spoken to the shop in place of the menu name.

        The test runs one way only. A phrase that *contains* the menu name
        is more specific than it ("large filter coffee") and safe to echo.
        A phrase the menu name contains is an abbreviation of it --
        "coffee" for "filter coffee" -- and echoing that drops the word
        that distinguishes the item, which at a shop selling three kinds of
        coffee is the word that matters. Anything else has to earn it by
        being in aliases.
        """
        p = phrase.strip().lower()
        if self.name.lower() in p:
            return True
        return any(a.lower() == p for a in self.aliases)

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Item":
        return Item(
            name=d.get("item") or "",
            price_cents=int(d.get("price_cents") or 0),
            aliases=list(d.get("aliases") or []),
            cap_cents=int(d.get("cap_cents") or 0),
            preferences=dict(d.get("preferences") or {}),
        )


@dataclass
class Place:
    name: str
    phone: str
    note: str = ""
    menu: list[Item] = field(default_factory=list)

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Place":
        return Place(
            name=d.get("name") or "",
            phone=d.get("phone") or "",
            note=d.get("note") or "",
            menu=[Item.from_json(i) for i in (d.get("menu") or [])],
        )


@dataclass
class Defaults:
    item: str = ""
    cap_headroom_cents: int = 250
    pickup_within_minutes: int = 45
    # Fallback preferences (milk, temperature, sugar, size, dine_in, ...)
    # when an item on the menu does not set its own. "size" lives here too
    # now -- it is the same kind of thing as milk or sugar, not special;
    # _apply_defaults gives it "medium" if the profile leaves it out
    # entirely.
    preferences: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Defaults":
        return Defaults(
            item=d.get("item") or "",
            cap_headroom_cents=int(d.get("cap_headroom_cents") or 0),
            pickup_within_minutes=int(d.get("pickup_within_minutes") or 0),
            preferences=dict(d.get("preferences") or {}),
        )


@dataclass
class Match:
    """A resolved order: what was asked for, where it is sold, for how
    much.
    """

    place: Place
    item: Item
    as_requested: str = ""  # the owner's own phrasing, echoed back to them


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    customer_name: str = ""
    places: list[Place] = field(default_factory=list)
    prefer: dict[str, str] = field(default_factory=dict)
    defaults: Defaults = field(default_factory=Defaults)

    # -- loading -------------------------------------------------------

    @staticmethod
    def load(path: str) -> "Profile":
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        try:
            d = json.loads(raw)
        except ValueError as e:
            raise ProfileError(f"{path}: {e}") from e
        p = Profile(
            customer_name=d.get("customer_name") or "",
            places=[Place.from_json(pl) for pl in (d.get("places") or [])],
            prefer=dict(d.get("prefer") or {}),
            defaults=Defaults.from_json(d.get("defaults") or {}),
        )
        try:
            p._validate()
        except ProfileError as e:
            raise ProfileError(f"{path}: {e}") from e
        p._apply_defaults()
        return p

    def _validate(self) -> None:
        if not self.places:
            raise ProfileError("no places configured")
        for pl in self.places:
            if not pl.name or not pl.phone:
                raise ProfileError("every place needs a name and a phone number")
            if not pl.menu:
                raise ProfileError(f"{pl.name} has no menu, so nothing can route to it")
            for it in pl.menu:
                # A cap under list price rejects every honest quote, which
                # would look like the counterparty overcharging on every
                # single call.
                if it.cap_cents > 0 and it.cap_cents < it.price_cents:
                    raise ProfileError(
                        f"{pl.name}: {it.name} has a cap of {it.cap_cents} "
                        f"below its list price of {it.price_cents}"
                    )
        # A preference naming a place that does not exist would silently
        # never apply, which is worse than refusing to start.
        for term, want in self.prefer.items():
            if self._place(want) is None:
                raise ProfileError(f'prefer[{term!r}] names {want!r}, which is not in places')

    def _apply_defaults(self) -> None:
        if not self.defaults.preferences.get("size"):
            self.defaults.preferences["size"] = "medium"
        if self.defaults.cap_headroom_cents <= 0:
            self.defaults.cap_headroom_cents = 250
        if self.defaults.pickup_within_minutes <= 0:
            self.defaults.pickup_within_minutes = 45

    def _place(self, name: str) -> Place | None:
        for pl in self.places:
            if pl.name.lower() == name.lower():
                return pl
        return None

    # -- resolution ------------------------------------------------------

    def resolve(self, input_: str) -> Match:
        """Maps a phrase like "order filter coffee" onto a place and an
        item.

        Two rules, in order:

        1. `prefer` is an override, not a tiebreaker. "coffee": "Mylapore
           Express" means a request mentioning coffee goes there, even if
           somewhere else lists an item by that exact name. A tiebreaker
           would not deliver that -- the other place can win outright
           without any tie occurring.
        2. Otherwise the longest matching menu phrase wins, so "filter
           coffee" beats a bare "coffee". Remaining ties go to declaration
           order, never to dict iteration, which would route the same
           phrase differently per run.
        """
        in_ = input_.strip().lower()
        if in_ == "" or in_ == "usual":
            in_ = self.defaults.item.lower()

        pl = self._preferred(in_)
        if pl is not None:
            # Loose matching inside the preferred place: "coffee" should
            # reach "filter coffee" there. Strict containment alone would
            # fail, because the request is shorter than the menu name, and
            # the preference would silently never apply.
            m = _search(in_, [pl], loose=True)
            if m is not None:
                return m
            # The preferred place does not sell it; fall through rather
            # than fail, so a stale preference degrades to normal routing.
        m = _search(in_, self.places, loose=False)
        if m is not None:
            return m
        raise ProfileError(f"nothing on your menus matches {input_!r}")

    def _preferred(self, in_: str) -> Place | None:
        """Returns the place named by the longest matching prefer key, so a
        specific rule outranks a general one.
        """
        best_key, best_place = "", ""
        for key, place in self.prefer.items():
            k = key.lower()
            if k in in_ and len(k) > len(best_key):
                best_key, best_place = k, place
        if best_key == "":
            return None
        return self._place(best_place)

    def knows(self, input_: str) -> bool:
        """Reports whether anything on any menu matches -- used by the
        skill registry to decide which vertical owns a phrase.
        """
        try:
            self.resolve(input_)
            return True
        except ProfileError:
            return False

    def cap_for(self, m: Match) -> int:
        return self.cap_for_qty(m, 1)

    def cap_for_qty(self, m: Match, qty: int) -> int:
        """Scales the per-unit ceiling. A fixed cap on a multi-item order
        would reject every honest quote and read as the counterparty
        overcharging.
        """
        if qty < 1:
            qty = 1
        per = m.item.cap_cents
        if per <= 0:
            per = m.item.price_cents + self.defaults.cap_headroom_cents
        return per * qty

    def confirm(self, item: str, place: str, as_requested: str) -> Match:
        """Checks a parsed order against the profile. Nothing the model
        wrote is trusted: the item must exist on the named place's menu,
        and the place must be one we configured. A hallucinated item is
        rejected here rather than dialled.
        """
        if not item:
            raise ProfileError("no item")
        candidates = self.places
        if place:
            pl = self._place(place)
            if pl is None:
                raise ProfileError(f"{place!r} is not one of your places")
            candidates = [pl]
        for pl in candidates:
            for it in pl.menu:
                if it.name.lower() != item.lower():
                    continue
                # Echo the owner's phrasing only if it really is one of the
                # names this item goes by; otherwise fall back to the menu
                # name.
                as_ = it.name
                if as_requested and it.known_as(as_requested):
                    as_ = as_requested
                return Match(place=pl, item=it, as_requested=as_)
        raise ProfileError(f"{item!r} is not on any menu we have")


def _search(in_: str, places: list[Place], loose: bool) -> Match | None:
    """Finds the longest matching menu phrase. Loose also accepts a menu
    phrase that contains the request, which is only safe once a preference
    has already narrowed us to one place.
    """
    best: Match | None = None
    best_len = 0
    for pl in places:
        for it in pl.menu:
            for phrase in [it.name, *it.aliases]:
                lp = phrase.lower()
                if not lp:
                    continue
                hit = lp in in_ or (loose and in_ in lp)
                if not hit or len(lp) <= best_len:
                    continue
                best, best_len = Match(place=pl, item=it, as_requested=phrase), len(lp)
    return best


def builtin(customer_name: str, business_name: str, phone: str) -> Profile:
    """The fallback when no profile file is configured: one place, the
    stock menu. It keeps the service runnable -- and the tests honest --
    before anyone has written preferences.
    """
    p = Profile(
        customer_name=customer_name,
        places=[Place(
            name=business_name,
            phone=phone,
            menu=[
                Item(name="drip coffee", price_cents=325,
                     aliases=["filter coffee", "coffee", "regular coffee"]),
                Item(name="latte", price_cents=495, aliases=["cafe latte"]),
                Item(name="cappuccino", price_cents=475),
                Item(name="americano", price_cents=395),
                Item(name="cortado", price_cents=445),
                Item(name="flat white", price_cents=495),
            ],
        )],
        defaults=Defaults(item="drip coffee"),
    )
    p._apply_defaults()
    return p


Builtin = builtin
