from datetime import timedelta

import pytest

from otrango.usecase.tools import _now, parse_loose_time


# What a counter actually says. Failing to parse these leaves the outcome
# with no evidence the order was taken, which flags every genuine order for
# review.
@pytest.mark.parametrize("in_,mins", [
    ("ten minutes", 10),
    ("10 minutes", 10),
    ("about 5 minutes", 5),
    ("~15 min", 15),
    ("twenty minutes", 20),
    ("a couple minutes", 1),  # "a" -> 1; imprecise but present
    ("half an hour", 30),
    ("1 hour", 60),
])
def test_parse_loose_time_accepts_relative_waits(in_, mins):
    now = _now()
    got = parse_loose_time(in_)
    got_mins = round((got - now).total_seconds() / 60)
    assert got_mins == mins


@pytest.mark.parametrize("in_", ["4:15 PM", "16:15", "2026-08-30T16:15:00Z"])
def test_parse_loose_time_still_accepts_clock_times(in_):
    parse_loose_time(in_)  # must not raise


@pytest.mark.parametrize("in_", ["", "soon", "when it's done"])
def test_parse_loose_time_rejects_nonsense(in_):
    with pytest.raises(ValueError):
        parse_loose_time(in_)
