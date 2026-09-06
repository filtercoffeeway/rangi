"""test_skills_boundary is the falsifiable form of PLAN.md S2's
extensibility claim. The document says adding a vertical must not touch the
authority layer or the engine; a document cannot enforce that, but the
import graph can.

If someone later reaches from otrango.objective or otrango.store into a
skill -- the natural shortcut when a vertical needs "just one special case"
-- this fails, and the claim in the plan is retired rather than quietly
becoming false.
"""
from __future__ import annotations

import ast
import os

import pytest

import otrango

PKG_ROOT = os.path.dirname(otrango.__file__)

VERTICAL_AGNOSTIC = ["objective", "vapi", "store", "turns", "sse", "notify", "adapter"]


def imports_of(subpkg: str) -> list[str]:
    out: list[str] = []
    root = os.path.join(PKG_ROOT, subpkg)
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        out.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        out.append(node.module)
    return out


@pytest.mark.parametrize("subpkg", VERTICAL_AGNOSTIC)
def test_phase4_layer_boundary(subpkg):
    for imp in imports_of(subpkg):
        assert "skills" not in imp.split("."), (
            f"{subpkg} imports {imp} -- the vertical-agnostic layer must not "
            "depend on a skill (PLAN.md S2)"
        )


# The engine layers must also not reach for a specific vertical's
# vocabulary.
@pytest.mark.parametrize("subpkg", ["objective", "store", "vapi", "turns", "adapter"])
def test_no_coffee_vocabulary_in_engine(subpkg):
    for imp in imports_of(subpkg):
        assert "skills.coffee" not in imp and "skills.hours" not in imp, (
            f"{subpkg} imports the {imp} vertical directly"
        )


# Both verticals must satisfy the interface and be reachable by trigger
# phrase -- the registry is the only wiring a new vertical needs.
def test_registry_resolves_both_verticals():
    from otrango.skills import default

    reg = default("Tester", "Test Cafe", None)

    cases = {
        "coffee": "coffee_order",
        "latte": "coffee_order",
        "what time do you close on sunday": "hours_enquiry",
        "opening hours": "hours_enquiry",
    }
    for input_, want_type in cases.items():
        sk = reg.resolve(input_)
        assert sk.type() == want_type

    assert len(reg.types()) == 2


# Every vertical must set termination guards. Against an AI counterparty
# these are the only guaranteed way a call ends (PLAN.md S3.2), so a
# vertical that forgets them is a liveness bug, not a style problem.
def test_every_vertical_sets_termination_guards():
    from otrango.skills import default

    reg = default("Tester", "Test Cafe", None)
    for input_ in ("coffee", "closing time"):
        sk = reg.resolve(input_)
        _spec, cons = sk.build(input_)
        assert cons.max_duration_sec > 0, f"{sk.type()}: max_duration_sec is unset"
        assert cons.max_turns > 0, f"{sk.type()}: max_turns is unset"
