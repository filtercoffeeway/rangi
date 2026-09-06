import uuid
from datetime import timedelta

from otrango import objective
from tests.uc_fakes import new_fixture


def stale_call(fx, status: str) -> str:
    c = objective.Call(
        id=str(uuid.uuid4()), to_number="+14155550100", status=status,
        created_at=fx.now - timedelta(hours=1),
    )
    fx.calls.create(c)
    return c.id


# A call that never reaches a terminal outcome must not sit in limbo:
# without this the owner simply never learns what happened.
def test_reconcile_forces_terminal_outcome():
    fx = new_fixture()
    id_ = stale_call(fx, objective.CALL_IN_PROGRESS)

    assert fx.svc.reconcile(timedelta(0)) == 1
    o = fx.outcomes.get(id_)
    assert o.result == objective.RESULT_AMBIGUOUS
    assert o.needs_review


# The reconciler is a backstop, not an authority.
def test_reconcile_never_overwrites_a_real_outcome():
    fx = new_fixture()
    id_ = stale_call(fx, objective.CALL_ENDED)
    ref, total = "47", 495
    fx.outcomes.record(objective.Outcome(
        call_id=id_, result=objective.RESULT_SUCCESS, confidence=objective.CONF_HIGH,
        confirmation_ref=ref, total_cents=total,
    ))

    assert fx.svc.reconcile(timedelta(0)) == 0
    o = fx.outcomes.get(id_)
    assert o.result == objective.RESULT_SUCCESS and o.confirmation_ref == ref


def test_reconcile_is_idempotent():
    fx = new_fixture()
    stale_call(fx, objective.CALL_IN_PROGRESS)
    first = fx.svc.reconcile(timedelta(0))
    second = fx.svc.reconcile(timedelta(0))
    assert (first, second) == (1, 0)


# A call that failed at dial time already has its terminal record.
def test_reconcile_skips_failed_calls():
    fx = new_fixture()
    id_ = stale_call(fx, objective.CALL_QUEUED)
    fx.calls.mark_failed(id_, "PROVIDER_ERROR")
    assert fx.svc.reconcile(timedelta(0)) == 0


# Calls inside the grace period are still running, not stuck.
def test_reconcile_respects_grace_period():
    fx = new_fixture()
    stale_call(fx, objective.CALL_IN_PROGRESS)
    assert fx.svc.reconcile(timedelta(hours=24)) == 0


# When the provider knows why the call ended, that beats a shrug.
def test_reconcile_asks_the_provider_first():
    fx = new_fixture()
    id_ = stale_call(fx, objective.CALL_IN_PROGRESS)
    pid = "prov-x"
    fx.calls.attach_provider_id(id_, pid)
    fx.caller.ended = "customer-did-not-answer"

    fx.svc.reconcile(timedelta(0))

    assert fx.caller.lookups > 0
    o = fx.outcomes.get(id_)
    assert o.failure_class == objective.FAIL_NO_ANSWER
