from datetime import datetime, timedelta, timezone

import pytest

from otrango.usecase.tools import _JUST_PASSED, _now, parse_loose_time, resolve_clock


def pdt(h, m):
    tz = timezone(timedelta(hours=-7))
    return datetime(2026, 8, 30, h, m, 0, tzinfo=tz)


# A pickup time is always ahead of the call that agreed it. These cases are
# the readings a shop actually gives, resolved against the moment they were
# said -- the failure this replaces stamped every one of them onto today and
# produced pickup times in the past.
@pytest.mark.parametrize("name,now,hour,minute,ambiguous,want_day,want_hour", [
    ("afternoon, bare five means this evening", pdt(16, 30), 5, 0, True, 30, 17),
    ("morning, bare five still means this evening", pdt(9, 0), 5, 0, True, 30, 17),
    ("just before five in the morning, bare five means this morning", pdt(4, 45), 5, 0, True, 30, 5),
    ("five has only just gone, so it is still today", pdt(17, 2), 5, 0, True, 30, 17),
    ("late evening, bare five is tomorrow morning", pdt(22, 0), 5, 0, True, 31, 5),
    ("an explicit pm is not second-guessed", pdt(16, 30), 17, 0, False, 30, 17),
    ("an explicit pm already past rolls to tomorrow, not backwards", pdt(22, 0), 17, 0, False, 31, 17),
    ("24-hour notation is taken as given", pdt(9, 0), 17, 0, False, 30, 17),
])
def test_resolve_clock_takes_the_next_future_reading(name, now, hour, minute, ambiguous, want_day, want_hour):
    got = resolve_clock(now, hour, minute, ambiguous)
    assert (got.day, got.hour) == (want_day, want_hour)
    assert got >= now - _JUST_PASSED


# The whole point of resolving forward: a pickup time in the past is never
# after the mandate's deadline, so it slipped the completion check silently.
@pytest.mark.parametrize("in_", [
    "5:00", "5", "5:15", "12:30", "5:00 PM", "5pm", "17:00",
    "5 o'clock", "5 oclock", "5.05 pm", "5.05", "17.30",
    "in 30 minutes", "in about an hour",
])
def test_parsed_pickup_time_is_never_in_the_past(in_):
    got = parse_loose_time(in_)
    assert got >= _now() - _JUST_PASSED


# "5.05" is a dot standing in for a colon, not a decimal, and it has to
# reach the same am/pm reasoning "5:05" does. Normalising it after that
# decision would silently pin every dotted time to the morning.
def test_dotted_time_keeps_its_am_pm_reasoning():
    dotted = parse_loose_time("5.05")
    colon = parse_loose_time("5:05")
    assert dotted == colon

    pm = parse_loose_time("5.05 pm")
    assert (pm.hour, pm.minute) == (17, 5)
