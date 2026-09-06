"""Default is the wiring point for verticals. Adding one touches this file
and the new module -- and nothing under otrango.objective or the engine
modules. That is the whole extensibility claim, and it is asserted by
test_skills_boundary.py rather than left as an assertion in a document.

The profile supplies menus and routing; a None one falls back to a built-in
single place, so the service stays runnable before anyone writes
preferences.
"""
from __future__ import annotations

from typing import Optional

from otrango.profile.profile import Profile, builtin
from otrango.skills.coffee.coffee import Skill as CoffeeSkill
from otrango.skills.hours.hours import Skill as HoursSkill
from otrango.skills.skill import Registry


def default(customer_name: str, business_name: str, prof: Optional[Profile]) -> Registry:
    return with_parser(customer_name, business_name, prof, None)


def with_parser(customer_name: str, business_name: str, prof: Optional[Profile], parser) -> Registry:
    """Default plus model-assisted request parsing. A None parser leaves the
    deterministic matcher in charge, which is what the tests use and what
    the service falls back to without an API key.
    """
    if prof is None:
        prof = builtin(customer_name, business_name, "")
    c = CoffeeSkill(customer_name, prof)
    if parser is not None:
        c = c.with_parser(parser)
    # An enquiry has to go somewhere; the first configured place is the
    # only sensible default, and the profile guarantees there is one.
    enquiry = prof.places[0]
    # Order matters: hours is specific, coffee is the catch-all once a
    # parser is attached. Registering coffee first would swallow "what
    # time do you close" before the hours vertical ever saw it.
    return Registry(HoursSkill(customer_name, enquiry), c)
