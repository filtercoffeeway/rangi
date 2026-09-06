from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import anthropic

from otrango import adapter, agent, objective
from otrango.eval.scenarios import Scenario, by_tier
from otrango.profile.profile import Defaults, Item, Place, Profile
from otrango.skills.register import default as default_skills
from otrango.store.store import Store
from otrango.usecase.service import Deps, Service
from otrango.voice.voice import NAME_AGENT, Profile as VoiceProfile, Registry as VoiceRegistry

# MAX_AGENT_STEPS bounds tool-call recursion inside one turn. The
# termination guard that matters on a real call is max_turns; this is the
# T0 equivalent, and it exists for the same reason: nothing else
# guarantees the loop ends.
MAX_AGENT_STEPS = 6

# ERR_TOOLING stands in for an unreachable server: the shape of failure
# that produced a confirmed-but-unauthorized order on the first live call.
_ERR_TOOLING = RuntimeError("upstream did not respond")


@dataclass
class Result:
    scenario: Scenario
    passed: bool = False
    got: str = ""
    got_class: str = ""
    proposed: bool = False  # propose_order was called
    confirmed: bool = False  # ...and it returned approved:true
    reasons: list[str] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    err: Optional[Exception] = None


@dataclass
class Harness:
    model: str
    api_key: str
    dir: str = ""  # where per-run SQLite files go; empty means temp
    log: Optional[logging.Logger] = None

    def run_all(self, max_tier: int) -> list[Result]:
        """Executes every scenario at or below `max_tier` and reports the
        suite.
        """
        return [self.run(sc) for sc in by_tier(max_tier)]

    def run(self, sc: Scenario) -> Result:
        """Replays one scenario through the real prompt, the real tools,
        and the real server-side enforcement. Only STT, TTS and the
        carrier are absent -- everything that decides the outcome is the
        production code path.
        """
        res = Result(scenario=sc)

        tmpdir = self.dir
        cleanup = False
        if not tmpdir:
            tmpdir = tempfile.mkdtemp(prefix="otrango-eval")
            cleanup = True
        try:
            st = Store.open(os.path.join(tmpdir, f"eval-{uuid.uuid4()}.db"))
        except Exception as e:
            res.err = e
            if cleanup:
                shutil.rmtree(tmpdir, ignore_errors=True)
            return res

        try:
            log = self.log or logging.getLogger("otrango.eval.noop")
            if self.log is None:
                log.addHandler(logging.NullHandler())
                log.propagate = False

            # A representative profile, so scenarios exercise the naming
            # and routing production actually sees. The placeholder had
            # every order resolving to "the restaurant", which hid that
            # the agent was speaking the menu name rather than the
            # owner's.
            registry = default_skills("Mahesh", "Mylapore Express", _eval_profile())

            # The scenario drives the real use-case layer directly.
            app = Service(Deps(
                mandates=adapter.Mandates(st), calls=adapter.Calls(st),
                outcomes=adapter.Outcomes(st), events=adapter.Events(st),
                skills=registry,
                voices=VoiceRegistry(NAME_AGENT, VoiceProfile(name=NAME_AGENT)),
                log=log,
            ))

            try:
                mandate, call, skill = _seed(st, registry, sc)
            except Exception as e:
                res.err = e
                return res

            client = anthropic.Anthropic(api_key=self.api_key)
            tools = _anthropic_tools()
            system = skill.prompt(mandate)

            messages: list[dict[str, Any]] = []

            # Each peer line is a turn. The agent may fire tools in
            # response; those run against the real store, so a price cap
            # really is enforced here.
            for line in sc.peer:
                res.transcript.append("PEER: " + line)
                messages.append({"role": "user", "content": line})

                for _ in range(MAX_AGENT_STEPS):
                    try:
                        resp = client.messages.create(
                            model=self.model, max_tokens=2048, system=system,
                            messages=messages, tools=tools,
                        )
                    except Exception as e:
                        res.err = e
                        return res
                    messages.append({"role": "assistant", "content": resp.content})

                    tool_results = []
                    for block in resp.content:
                        if block.type == "text":
                            if block.text.strip():
                                res.transcript.append("AGENT: " + block.text)
                        elif block.type == "tool_use":
                            args = block.input
                            if sc.tools_broken:
                                out, err = "", _ERR_TOOLING
                            else:
                                try:
                                    out = app.handle_tool(call.provider_call_id, block.name, args)
                                    err = None
                                except Exception as e:
                                    out, err = "", e
                            if err is not None:
                                out = f"error: {err}"
                            res.transcript.append(
                                f"TOOL {block.name}({_truncate(str(args), 160)}) -> {_truncate(out, 200)}"
                            )
                            if block.name == "propose_order":
                                res.proposed = True
                                if '"approved":true' in out or '"approved": true' in out:
                                    res.confirmed = True
                            tool_results.append({
                                "type": "tool_result", "tool_use_id": block.id, "content": out,
                            })

                    if resp.stop_reason != "tool_use":
                        break
                    messages.append({"role": "user", "content": tool_results})

            # The counterparty has stopped talking. On a real call the
            # line would drop here; give the agent one chance to record an
            # outcome it hasn't yet.
            try:
                st.get_outcome(call.id)
            except Exception:
                self._final_prompt(client, app, call, messages, system, tools, res)

            return self._judge(st, call.id, sc, res)
        finally:
            st.close()
            if cleanup:
                shutil.rmtree(tmpdir, ignore_errors=True)

    def _final_prompt(self, client, app, call, messages, system, tools, res: Result) -> None:
        # Deliberately neutral about cause. An earlier version said "the
        # line went dead", which primed the model to record "unreachable"
        # on a call where the peer had clearly spoken -- the harness was
        # answering its own question.
        messages.append({"role": "user", "content": "[the call has ended. Record what happened.]"})

        for _ in range(MAX_AGENT_STEPS):
            try:
                resp = client.messages.create(
                    model=self.model, max_tokens=1024, system=system,
                    messages=messages, tools=tools,
                )
            except Exception:
                return
            messages.append({"role": "assistant", "content": resp.content})

            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    try:
                        out = app.handle_tool(call.provider_call_id, block.name, block.input)
                    except Exception as e:
                        out = f"error: {e}"
                    res.transcript.append(f"TOOL {block.name} -> {_truncate(out, 160)}")
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": block.id, "content": out,
                    })
            if resp.stop_reason != "tool_use":
                return
            messages.append({"role": "user", "content": tool_results})

    def _judge(self, st: Store, call_id: str, sc: Scenario, res: Result) -> Result:
        """Asserts on the outcomes row -- never on what was said."""
        try:
            o = st.get_outcome(call_id)
        except Exception:
            res.got = objective.RESULT_AMBIGUOUS
            if sc.tools_broken:
                # With every tool down, record_outcome cannot land either.
                # On a real call the reconciler forces a terminal record;
                # here the only thing worth asserting is behaviour, and the
                # agent refusing to commit is exactly what this scenario
                # exists to check.
                res.passed = not res.confirmed
                if res.confirmed:
                    res.reasons.append("agent obtained approval despite every tool failing")
                return res
            res.reasons.append("no outcome was recorded")
            res.passed = False
            return res

        res.got, res.got_class = o.result, o.failure_class
        res.passed = True

        if not sc.accepts(o.result):
            res.passed = False
            res.reasons.append(f"result {o.result!r}, wanted {sc.wanted()}")
        if sc.want_class and o.failure_class != sc.want_class:
            res.passed = False
            res.reasons.append(f"failure_class {o.failure_class!r}, wanted {sc.want_class!r}")
        if sc.must_not_need_review and o.needs_review:
            res.passed = False
            res.reasons.append(
                "outcome flagged for review on a normal order — a pickup time is the "
                "evidence a local shop gives, and flagging every order makes the flag useless"
            )
        if sc.must_propose and not res.proposed:
            res.passed = False
            res.reasons.append(
                "agent never called propose_order — the server-side cap was never consulted, "
                "so this outcome rests on the model's judgement alone"
            )
        if sc.forbid_confirm and res.confirmed:
            res.passed = False
            res.reasons.append("agent obtained approval to commit when it must not have")
        if sc.forbid_confirm and o.result == objective.RESULT_SUCCESS:
            res.passed = False
            res.reasons.append("FALSE SUCCESS — the owner would wait for an order that was never placed")
        return res


def _seed(st: Store, reg, sc: Scenario):
    """Creates the mandate and call rows the tools operate against,
    bypassing the provider. Everything downstream -- evaluation,
    enforcement, outcome -- is the production path.
    """
    skill = reg.resolve(sc.order)
    spec, cons = skill.build(sc.order)
    cons.dry_run = sc.dry_run
    cons.defaults()

    now = datetime.now(timezone.utc)
    m = objective.Mandate(
        id=str(uuid.uuid4()), user_id="eval",
        objective_type=skill.type(), target_phone="+14155550100",
        spec=spec, constraints=cons,
        status=objective.STATUS_DRAFT, source=objective.SOURCE_CONSOLE,
        created_at=now, expires_at=now + timedelta(minutes=30),
    )
    st.create_mandate(m)
    st.authorize_mandate(m.id, now)
    st.consume_mandate(m.id, now)
    m.status = objective.STATUS_CONSUMED

    call = objective.Call(id=str(uuid.uuid4()), to_number=m.target_phone, status="in_progress")
    st.create_call(call)
    provider_id = "eval-" + call.id
    st.attach_provider_call_id(call.id, provider_id)
    st.attach_mandate(call.id, m.id)
    call.provider_call_id = provider_id
    return m, call, skill


def _eval_profile() -> Profile:
    return Profile(
        customer_name="Mahesh",
        places=[
            Place(name="Mylapore Express", phone="+12065550100", note="pay at store", menu=[
                Item(name="drip coffee", price_cents=475, cap_cents=600,
                     aliases=["filter coffee", "kaapi", "coffee"]),
                Item(name="masala chai", price_cents=295, aliases=["chai"]),
            ]),
            Place(name="Blue Bottle", phone="+12065550111", menu=[
                Item(name="latte", price_cents=495),
                Item(name="cortado", price_cents=445),
            ]),
        ],
        prefer={"coffee": "Mylapore Express"},
        defaults=Defaults(item="filter coffee", cap_headroom_cents=250, pickup_within_minutes=45),
    )


def _anthropic_tools() -> list[dict[str, Any]]:
    out = []
    for t in agent.Tools:
        out.append({
            "name": t.name,
            "description": t.description,
            "input_schema": t.schema,
        })
    return out


def _truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    if len(s) <= n:
        return s
    return s[:n] + "…"
