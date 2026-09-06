import re

from otrango import objective
from otrango.usecase.messaging import ReservedByCarrier
from tests.uc_fakes import new_fixture


def chat(fx, body: str) -> str:
    return fx.svc.handle_message(body, objective.SOURCE_TELEGRAM)


def test_draft_then_authorize():
    fx = new_fixture()

    reply = chat(fx, "coffee")
    assert "reply y" in reply.lower()
    # The confirmation names the item and the place. It deliberately does
    # not quote a spending limit: this shop never states a price, so
    # showing a cap the mechanism cannot act on is clutter in a message
    # meant to be glanced at. The limit is still enforced server-side if a
    # price ever is quoted.
    for want in ("coffee", "Test Cafe"):
        assert want in reply
    # A draft has not been attempted, so it must not read like a result. An
    # earlier version reused the outcome formatter and told the owner their
    # un-dialed order had failed.
    for bad in ("failed", "❌", "Nothing was ordered"):
        assert bad not in reply

    # Naming the place is the point: it confirms the routing while there is
    # still time to notice it was wrong.
    got = chat(fx, "Y")
    assert "Calling" in got and "Test Cafe" in got
    assert len(fx.caller.placed) == 1


# Texting the order twice then Y must authorize exactly one mandate: the
# newest.
def test_second_draft_supersedes_the_first():
    fx = new_fixture()

    chat(fx, "coffee")
    first = fx.mandates.latest_draft("owner")
    chat(fx, "latte")
    second = fx.mandates.latest_draft("owner")
    assert second.id != first.id

    chat(fx, "Y")

    a = fx.mandates.get(first.id)
    b = fx.mandates.get(second.id)
    assert a.status != "consumed"
    assert b.status == "consumed"
    assert len(fx.caller.placed) == 1


def test_authorize_with_nothing_pending():
    fx = new_fixture()
    reply = chat(fx, "Y")
    assert "nothing pending" in reply.lower()
    assert len(fx.caller.placed) == 0


def test_drop_discards_the_draft():
    fx = new_fixture()
    chat(fx, "coffee")
    chat(fx, "drop")
    assert "Calling" not in chat(fx, "Y")


# The carrier intercepts its opt-out keywords before this service sees them
# and blocks the number permanently. A command sharing one would
# unsubscribe the owner from their own campaign, with no error anywhere
# here.
def test_no_command_uses_a_carrier_opt_out_keyword():
    for keyword in ReservedByCarrier:
        fx = new_fixture()
        chat(fx, "coffee")
        draft = fx.mandates.latest_draft("owner")

        reply = chat(fx, keyword)

        after = fx.mandates.get(draft.id)
        assert after.status == "draft", (
            f"{keyword!r} is wired to a command — it moved a mandate to {after.status!r}. "
            "Carrier keywords must fall through unhandled."
        )
        assert "Discarded" not in reply and "Calling" not in reply


def test_help_names_the_discard_command():
    fx = new_fixture()
    help_ = chat(fx, "help")
    assert "DROP" in help_
    # Whole words only -- "end" is a substring of "Send". STOP is exempt: it
    # is worth telling the owner about, just not a command we handle.
    for k in ReservedByCarrier:
        if k == "stop":
            continue
        assert not re.search(rf"\b{k}\b", help_, re.IGNORECASE)


def test_unrecognised_input():
    fx = new_fixture()
    reply = chat(fx, "the airspeed velocity of an unladen swallow")
    assert "HELP" in reply
