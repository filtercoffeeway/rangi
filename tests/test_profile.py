import json
import os

import pytest

from otrango.profile.profile import Profile, ProfileError

SAMPLE = {
    "customer_name": "Mahesh",
    "places": [
        {"name": "Mylapore Express", "phone": "+14155550100", "note": "pay at store",
         "menu": [{"item": "filter coffee", "price_cents": 325, "aliases": ["kaapi", "degree coffee"]},
                  {"item": "masala chai", "price_cents": 295, "aliases": ["chai"]}]},
        {"name": "Blue Bottle", "phone": "+14155550111",
         "menu": [{"item": "latte", "price_cents": 595}, {"item": "coffee", "price_cents": 450}]},
    ],
    "prefer": {"coffee": "Mylapore Express"},
    "defaults": {"item": "filter coffee", "cap_headroom_cents": 250},
}


def load(tmp_path, body):
    path = tmp_path / "p.json"
    if isinstance(body, dict):
        body = json.dumps(body)
    path.write_text(body)
    return Profile.load(str(path))


def p(tmp_path):
    return load(tmp_path, SAMPLE)


# The point of the design: the menus do the routing.
def test_menu_determines_the_place(tmp_path):
    pr = p(tmp_path)
    cases = [
        ("order filter coffee", "Mylapore Express", "filter coffee"),
        ("kaapi", "Mylapore Express", "filter coffee"),
        ("degree coffee please", "Mylapore Express", "filter coffee"),
        ("chai", "Mylapore Express", "masala chai"),
        ("latte", "Blue Bottle", "latte"),
    ]
    for in_, place, item in cases:
        m = pr.resolve(in_)
        assert (m.place.name, m.item.name) == (place, item)


# "filter coffee" must not lose to a bare "coffee" on another menu.
def test_longer_match_wins(tmp_path):
    m = p(tmp_path).resolve("filter coffee")
    assert m.place.name == "Mylapore Express"


# prefer is an override: Blue Bottle lists an item literally called
# "coffee" and would otherwise win outright, with no tie for a tiebreaker
# to resolve. It must also resolve identically on every run.
def test_preference_overrides_an_exact_match_elsewhere(tmp_path):
    pr = p(tmp_path)
    for _ in range(25):
        m = pr.resolve("coffee")
        assert m.place.name == "Mylapore Express"
        assert m.item.name == "filter coffee"


def test_usual_falls_back_to_the_default_item(tmp_path):
    m = p(tmp_path).resolve("usual")
    assert m.item.name == "filter coffee"


def test_unknown_item_is_an_error(tmp_path):
    with pytest.raises(ProfileError):
        p(tmp_path).resolve("lobster thermidor")


def test_cap_is_list_price_plus_headroom(tmp_path):
    pr = p(tmp_path)
    m = pr.resolve("filter coffee")
    assert pr.cap_for(m) == 575


# A preference pointing at a place that does not exist would silently never
# apply. Refusing to start is better than routing somewhere unintended.
def test_preference_naming_an_unknown_place_is_rejected(tmp_path):
    body = {"places": [{"name": "A", "phone": "+1415555", "menu": [{"item": "x", "price_cents": 1}]}],
            "prefer": {"x": "Nowhere"}}
    with pytest.raises(ProfileError):
        load(tmp_path, body)


def test_place_with_no_menu_is_rejected(tmp_path):
    body = {"places": [{"name": "A", "phone": "+1415555", "menu": []}]}
    with pytest.raises(ProfileError):
        load(tmp_path, body)


# A per-item cap must override the global headroom without disturbing other
# items on the same menu.
def test_per_item_cap_overrides_headroom(tmp_path):
    pr = load(tmp_path, {
        "places": [{"name": "Mylapore Express", "phone": "+12065550100", "menu": [
            {"item": "filter coffee", "price_cents": 475, "cap_cents": 600},
            {"item": "masala chai", "price_cents": 295},
        ]}],
        "defaults": {"cap_headroom_cents": 250},
    })
    fc = pr.resolve("filter coffee")
    assert pr.cap_for(fc) == 600
    chai = pr.resolve("masala chai")
    assert pr.cap_for(chai) == 545


# A cap below list price would reject every honest quote, so it fails at
# load rather than looking like the counterparty overcharging on every
# call.
def test_cap_below_list_price_is_rejected(tmp_path):
    body = {"places": [{"name": "A", "phone": "+1415555", "menu": [
        {"item": "x", "price_cents": 500, "cap_cents": 300}]}]}
    with pytest.raises(ProfileError):
        load(tmp_path, body)


# What the agent says out loud is Confirm's as_requested. The owner's own
# word is echoed only when it is a name the item really goes by --
# otherwise a trigger phrase like "coffee" would be read back to the shop
# in place of "filter coffee", which is the one word that distinguishes it
# from the chai and the two coffees on the other menu.
def test_confirm_echoes_owner_phrasing_only_when_it_is_more_specific(tmp_path):
    pr = p(tmp_path)
    cases = [
        ("coffee", "filter coffee"),
        ("kaapi", "kaapi"),
        ("degree coffee", "degree coffee"),
        ("filter coffee", "filter coffee"),
        ("large filter coffee", "large filter coffee"),
        ("something else", "filter coffee"),
    ]
    for as_requested, want in cases:
        m = pr.confirm("filter coffee", "Mylapore Express", as_requested)
        assert m.as_requested == want
