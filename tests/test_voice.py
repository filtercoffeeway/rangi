import pytest

from otrango.voice.voice import NAME_AGENT, NAME_MINE, Profile, Registry


def reg() -> Registry:
    return Registry(
        NAME_AGENT,
        Profile(name=NAME_AGENT, label="Stock", provider="11labs", voice_id="stock-1"),
        Profile(name=NAME_MINE, label="Cloned", provider="11labs", voice_id="mine-1", model="flash"),
    )


def test_empty_name_resolves_to_default():
    got = reg().get("")
    assert got.name == NAME_AGENT


def test_default_is_overridable():
    r = Registry(
        NAME_MINE,
        Profile(name=NAME_AGENT, provider="p", voice_id="a"),
        Profile(name=NAME_MINE, provider="p", voice_id="b"),
    )
    assert r.get("").name == NAME_MINE


# An unknown voice must fail at draft time, not silently fall back -- a
# sweep that quietly ran the wrong voice would produce data attributed to
# the wrong arm, which is worse than no data.
def test_unknown_voice_is_an_error():
    with pytest.raises(ValueError):
        reg().get("morgan-freeman")


@pytest.mark.parametrize("in_", ["MINE", "  mine ", "Mine"])
def test_names_are_case_and_space_insensitive(in_):
    got = reg().get(in_)
    assert got.name == NAME_MINE


@pytest.mark.parametrize("p,want", [
    (Profile(name="", provider="11labs", voice_id="x"), True),
    (Profile(name="", provider="11labs"), False),
    (Profile(name="", voice_id="x"), False),
    (Profile(name=""), False),
])
def test_configured_requires_provider_and_id(p, want):
    assert p.configured() == want


# A nil registry is what a hand-built Config has. It must degrade to "no
# voice override" rather than panic mid-dial.
def test_none_registry_degrades_instead_of_panicking():
    from otrango.voice import voice as v

    r = None
    got = v.get(r, "")
    assert got.name == NAME_AGENT
    assert not got.configured()
    assert v.default_name(r) == NAME_AGENT
    assert len(v.names(r)) == 1
    assert not v.comparable(r)


# The sweep is only meaningful when both arms exist and are configured.
def test_comparable_requires_both_arms():
    assert reg().comparable()
    half = Registry(
        NAME_AGENT,
        Profile(name=NAME_AGENT, provider="11labs", voice_id="stock-1"),
        Profile(name=NAME_MINE),  # cloned voice not set up yet
    )
    assert not half.comparable()
