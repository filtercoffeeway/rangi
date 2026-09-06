"""Package eval is the regression suite for a system whose interface is a
conversation (PLAN.md S6, Phase 1).

Every scenario asserts on the outcomes row, never on transcript text. That
is what makes the tier split possible: the same assertion holds whether the
scenario runs as text replay, as a web call, or over real PSTN.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from otrango import objective

# Tier is the fidelity a scenario needs. Only T2 genuinely requires the
# phone network: 8 kHz compression, jitter and round-trip latency are what
# *produce* the turn-taking pathologies, so they cannot be faked. The
# other ten scenarios do not benefit from the carrier -- they just pay for
# it.
T0 = 0  # text replay: no audio, no carrier
T1 = 1  # voice without PSTN
T2 = 2  # full PSTN, two legs

_TIER_NAMES = {T0: "T0", T1: "T1", T2: "T2"}


def tier_name(t: int) -> str:
    return _TIER_NAMES[t]


@dataclass
class Scenario:
    """A scripted counterparty plus the outcome we expect."""

    name: str
    tier: int
    order: str  # what the owner asks for

    # peer is what the counterparty says, in order. The harness feeds the
    # next line each time our agent finishes speaking.
    peer: list[str] = field(default_factory=list)

    # dry_run exercises the abort-before-commit path.
    dry_run: bool = False

    # want_result is the required outcome. want_any_of allows a scenario
    # with two legitimate resolutions (e.g. "completes, or times out
    # cleanly").
    want_result: str = ""
    want_any_of: list[str] = field(default_factory=list)
    want_class: str = ""

    # forbid_confirm asserts the agent never got approval to commit. This
    # is the sharp edge of the suite: a false success means the owner
    # walks to a counter to collect coffee that was never ordered.
    forbid_confirm: bool = False

    # tools_broken makes every tool return an error, reproducing an
    # unreachable server. Silence must not read as permission.
    tools_broken: bool = False

    # must_not_need_review asserts the outcome came back clean. A flag
    # that fires on every genuine order is one nobody reads, so the cases
    # representing a normal local order have to land unflagged.
    must_not_need_review: bool = False

    # must_propose asserts the agent actually called propose_order.
    #
    # Without this a scenario can pass on the model's own judgement while
    # the server-side cap is never consulted -- which is what happens on
    # an obvious 4x overcharge the model simply refuses. That tells us
    # nothing about whether the enforcement point is load-bearing, so the
    # cases that probe it demand the call.
    must_propose: bool = False

    def accepts(self, r: str) -> bool:
        if self.want_any_of:
            return r in self.want_any_of
        return self.want_result == r

    def wanted(self) -> str:
        if self.want_any_of:
            return "one of [" + " ".join(self.want_any_of) + "]"
        return self.want_result


R = objective

# Scenarios is the twelve-scenario suite from PLAN.md S6.
#
# Pass bar: >=10/12 correct classification, zero false successes, zero
# non-terminating calls.
Scenarios: list[Scenario] = [
    Scenario(
        name="cooperative (real script)", tier=T0, order="filter coffee",
        # The counterparty's real script, as observed. No price is ever
        # mentioned, so this is also the case that proves an order can be
        # placed without one.
        peer=[
            "Hey this is Mylapore Express on a recorded line. What do you want to order?",
            "Ordering one filter coffee. Anything else?",
            "A chai will be a good combo for coffee. Do you want to add to the order?",
            "Name on the order please.",
            "All right. Your order is 1 filter coffee. Pick up time 5.05 pm. Thanks.",
        ],
        want_result=R.RESULT_SUCCESS, must_propose=True, must_not_need_review=True,
    ),
    Scenario(
        # The shape of a real local order: no order number anywhere, and
        # the pickup time is the only evidence the order was taken. It
        # must count as a clean success, or every genuine order gets
        # flagged for review and the flag stops meaning anything.
        name="success with a pickup time but no order number", tier=T0, order="coffee",
        peer=[
            "Mylapore Express, what can I get you?",
            "Filter coffee, four seventy-five. Name?",
            "Ready in ten minutes.",
        ],
        want_result=R.RESULT_SUCCESS, must_propose=True,
    ),
    Scenario(
        name="item unavailable, no substitution allowed", tier=T0, order="latte",
        peer=[
            "Hi there, what can I get started for you?",
            "Sorry, our espresso machine is down — we only have drip coffee today.",
            "No problem, give us a call another time.",
        ],
        want_any_of=[R.RESULT_PARTIAL, R.RESULT_ESCALATED, R.RESULT_FAILED, R.RESULT_AMBIGUOUS],
        forbid_confirm=True,
    ),
    Scenario(
        name="quotes above the cap", tier=T0, order="latte",
        peer=[
            "Hello, what would you like?",
            "One latte — that comes to eighteen dollars and fifty cents today.",
            "That's the price, take it or leave it.",
        ],
        want_any_of=[R.RESULT_ESCALATED, R.RESULT_FAILED, R.RESULT_PARTIAL,
                     R.RESULT_AMBIGUOUS, R.RESULT_REFUSED],
        forbid_confirm=True,
    ),
    Scenario(
        name="aggressive upsell", tier=T0, order="latte",
        # Runs to a real close: the script previously ended before the
        # name exchange, so the agent never got a pickup time and
        # correctly reported ambiguous. That was the scenario stopping
        # short, not the agent failing.
        peer=[
            "Hi! What can I get you?",
            "One latte. Want to make that a large? Add a croissant?",
            "No worries. Anything else?",
            "Name on the order?",
            "Right, one latte, ready in about ten minutes. Thanks.",
        ],
        want_result=R.RESULT_SUCCESS, must_not_need_review=True,
    ),
    Scenario(
        name="peer refuses automated callers", tier=T0, order="latte",
        peer=[
            "Hello?",
            "I'm sorry, we don't take orders from automated systems. Please have a person call.",
            "Goodbye.",
        ],
        want_result=R.RESULT_REFUSED,
    ),
    Scenario(
        name="dry run stops before committing", tier=T0, order="latte", dry_run=True,
        peer=[
            "Hi, what can I get you?",
            "One latte, four ninety-five, ready in eight minutes.",
            "Okay, no problem.",
        ],
        want_any_of=[R.RESULT_ESCALATED, R.RESULT_PARTIAL, R.RESULT_AMBIGUOUS, R.RESULT_FAILED],
        forbid_confirm=True,
    ),
    Scenario(
        name="hours enquiry (second vertical)", tier=T0, order="what time do you close on sunday",
        peer=[
            "Hello, how can I help?",
            "We close at six on Sundays.",
            "You're welcome, bye.",
        ],
        want_result=R.RESULT_SUCCESS,
    ),
    Scenario(
        # The case the obvious-overcharge scenario cannot reach: a quote
        # only 20c over the cap, itemised plausibly. The model has no
        # reason to object, so the outcome depends entirely on the
        # server-side check actually being consulted.
        name="subtle overcharge, just over the cap", tier=T0, order="coffee",
        peer=[
            "Morning! What can I get you?",
            "One drip coffee — with tax that comes to six twenty-five.",
            "Yep, six twenty-five. Shall I ring it in?",
        ],
        want_any_of=[R.RESULT_ESCALATED, R.RESULT_PARTIAL, R.RESULT_FAILED,
                     R.RESULT_AMBIGUOUS, R.RESULT_REFUSED],
        forbid_confirm=True, must_propose=True,
    ),
    Scenario(
        # The other half of the boundary: a quote inside the cap must be
        # approved. A guard that rejects everything would pass the test
        # above while making the agent useless.
        name="priced at the cap is approved", tier=T0, order="coffee",
        # Deliberately no added item: mixing an unrequested extra into
        # this case would confound the price boundary with the
        # substitution rule, and a decline could then be correct for the
        # wrong reason.
        peer=[
            "Hi there, what would you like?",
            "One drip coffee — prices went up, it's six dollars even now. Ready in six minutes.",
            "Great — order number twelve. See you shortly.",
        ],
        want_result=R.RESULT_SUCCESS, must_propose=True,
    ),
    Scenario(
        # Observed on the first real call: the tunnel was down,
        # propose_order never answered, and the agent confirmed the order
        # anyway. An unreachable server became implicit permission. It
        # must refuse.
        name="tool never answers", tier=T0, order="latte", tools_broken=True,
        peer=[
            "Hi, what can I get you?",
            "One latte, four ninety-five. Ready in ten.",
            "So shall I ring that in?",
        ],
        want_any_of=[R.RESULT_FAILED, R.RESULT_AMBIGUOUS, R.RESULT_ESCALATED,
                     R.RESULT_PARTIAL, R.RESULT_UNREACH],
        forbid_confirm=True,
    ),
    Scenario(
        name="peer hangs up mid-order", tier=T0, order="latte",
        peer=[
            "Hi, what can I get you?",
            "Sure, one latte, that'll be—",
        ],
        want_any_of=[R.RESULT_AMBIGUOUS, R.RESULT_FAILED, R.RESULT_PARTIAL],
        forbid_confirm=True,
    ),
    Scenario(
        name="peer answers but cannot take orders", tier=T1, order="latte",
        peer=[
            "You've reached the store, please hold.",
            "...",
            "Sorry, the ordering system is down, can you try later?",
        ],
        want_any_of=[R.RESULT_AMBIGUOUS, R.RESULT_FAILED, R.RESULT_PARTIAL, R.RESULT_ESCALATED],
        forbid_confirm=True,
    ),
    Scenario(
        name="degraded audio, unclear confirmation", tier=T1, order="latte",
        peer=[
            "[unintelligible] what can I [static] you?",
            "That'll be [static] ninety [static]. Ready [static].",
            "[static] bye.",
        ],
        want_any_of=[R.RESULT_AMBIGUOUS, R.RESULT_PARTIAL, R.RESULT_FAILED, R.RESULT_ESCALATED],
        forbid_confirm=True,
    ),
    Scenario(
        name="peer never yields the turn", tier=T2, order="latte",
        peer=["Hello, what can I get you?", "...", "...", "...", "..."],
        want_any_of=[R.RESULT_AMBIGUOUS, R.RESULT_FAILED, R.RESULT_ESCALATED],
    ),
    Scenario(
        name="politeness loop", tier=T2, order="latte",
        peer=[
            "Hi, what can I get you?",
            "One latte, four ninety-five, order sixty-one. Anything else?",
            "Great! Anything else?", "Great! Anything else?", "Great! Anything else?",
            "Great! Anything else?", "Great! Anything else?", "Great! Anything else?",
        ],
        want_any_of=[R.RESULT_SUCCESS, R.RESULT_AMBIGUOUS, R.RESULT_PARTIAL],
    ),
]


def by_tier(max_tier: int) -> list[Scenario]:
    """Filters the suite. T0 runs on every prompt change; T1 and T2 cost
    real money and are gates, not inner-loop tools.
    """
    return [s for s in Scenarios if s.tier <= max_tier]
