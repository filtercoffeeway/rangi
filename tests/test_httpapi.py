import json
import os
import uuid

import pytest

from otrango import adapter, objective
from otrango.config import Config
from otrango.httpapi.server import create_app
from otrango.skills import default as default_skills
from otrango.sse.broker import Broker
from otrango.store.store import Store
from otrango.usecase.service import Deps, Service
from otrango.voice.voice import NAME_AGENT, Profile as VoiceProfile, Registry as VoiceRegistry

PROVIDER_CALL_ID = "vapi-call-abc123"


def new_test_server(tmp_path):
    """The HTTP layer is tested against the real service and store: this
    exercises the webhook envelope, auth and idempotency, which are
    delivery concerns. The rules behind them are covered in
    tests/test_usecase_*.py, without any of this scaffolding.
    """
    st = Store.open(str(tmp_path / "test.db"))
    svc = Service(Deps(
        mandates=adapter.Mandates(st), calls=adapter.Calls(st),
        outcomes=adapter.Outcomes(st), events=adapter.Events(st),
        skills=default_skills("Test", "Test Cafe", None),
        voices=VoiceRegistry(NAME_AGENT, VoiceProfile(name=NAME_AGENT)),
    ))
    cfg = Config(owner_user_id="test", customer_name="Test")
    broker = Broker()
    app = create_app(cfg, svc, broker)
    return app.test_client(), st, cfg


def seed_call(st: Store) -> objective.Call:
    c = objective.Call(id=str(uuid.uuid4()), to_number="+14155551234", status="queued")
    st.create_call(c)
    st.attach_provider_call_id(c.id, PROVIDER_CALL_ID)
    return c


# The console and the README both send snake_case. Flask/json decoding is
# exact-key, so this exercises the wire format the clients actually send.
def test_draft_decodes_snake_case_wire_format(tmp_path):
    client, st, cfg = new_test_server(tmp_path)

    body = {"input": "coffee", "to": "+14155559999", "dry_run": True}
    resp = client.post("/api/mandates", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 201, resp.get_data(as_text=True)

    got = resp.get_json()
    assert got["constraints"]["dry_run"] is True, "dry_run:true decoded as false — the console's rehearsal checkbox would dial for real"
    assert got["target_phone"] == "+14155559999"


def post_webhook(client, body: str):
    return client.post("/webhooks/vapi", data=body, content_type="application/json")


# The Phase 0 acceptance criterion (PLAN.md S6): replaying an identical
# webhook payload must not produce duplicate state.
def test_webhook_replay_is_idempotent(tmp_path):
    client, st, cfg = new_test_server(tmp_path)
    call = seed_call(st)

    payload = json.dumps({"message": {
        "type": "end-of-call-report", "endedReason": "customer-ended-call",
        "recordingUrl": "https://example.com/rec.wav", "transcript": "AI: Hello.\nUser: Hi.",
        "cost": 0.42, "call": {"id": PROVIDER_CALL_ID},
    }})

    for i in range(5):
        resp = post_webhook(client, payload)
        assert resp.status_code == 200, f"delivery {i + 1}"

    assert st.count_events() == 1

    got = st.get_call(call.id)
    assert got.status == "ended"
    assert got.end_reason == "customer-ended-call"
    assert got.recording_url == "https://example.com/rec.wav"
    assert got.transcript
    assert got.cost_cents == 42
    assert got.ended_at is not None


# Distinct states must still each be recorded -- idempotency must not
# collapse genuine progress into one event.
def test_distinct_status_updates_are_distinct_events(tmp_path):
    client, st, cfg = new_test_server(tmp_path)
    call = seed_call(st)

    for status in ("ringing", "in-progress", "ended"):
        body = json.dumps({"message": {"type": "status-update", "status": status,
                                        "call": {"id": PROVIDER_CALL_ID}}})
        post_webhook(client, body)
        post_webhook(client, body)  # immediate replay of each

    assert st.count_events() == 3
    assert st.get_call(call.id).status == "ended"


# Webhooks arrive partial and out of order. A later payload that omits a
# field must not blank a value an earlier one established.
def test_partial_update_does_not_clobber(tmp_path):
    client, st, cfg = new_test_server(tmp_path)
    call = seed_call(st)

    post_webhook(client, json.dumps({"message": {
        "type": "end-of-call-report", "endedReason": "done",
        "recordingUrl": "https://example.com/rec.wav", "transcript": "hello",
        "call": {"id": PROVIDER_CALL_ID},
    }}))
    # Same call, different reason, no recording or transcript present.
    post_webhook(client, json.dumps({"message": {
        "type": "end-of-call-report", "endedReason": "late-report",
        "call": {"id": PROVIDER_CALL_ID},
    }}))

    got = st.get_call(call.id)
    assert got.recording_url == "https://example.com/rec.wav"
    assert got.transcript == "hello"


def test_webhook_secret_rejects_bad_header(tmp_path):
    client, st, cfg = new_test_server(tmp_path)
    seed_call(st)
    cfg.vapi_webhook_secret = "correct-horse"

    body = json.dumps({"message": {"type": "status-update", "status": "ended",
                                    "call": {"id": PROVIDER_CALL_ID}}})

    resp = client.post("/webhooks/vapi", data=body, content_type="application/json",
                        headers={"x-vapi-secret": "wrong"})
    assert resp.status_code == 401
    assert st.count_events() == 0

    resp = client.post("/webhooks/vapi", data=body, content_type="application/json",
                        headers={"x-vapi-secret": "correct-horse"})
    assert resp.status_code == 200


# An unparseable body is our bug, not a transient fault: acknowledge it so
# the provider stops redelivering, but never treat it as processed.
def test_malformed_body_is_acknowledged_not_stored(tmp_path):
    client, st, cfg = new_test_server(tmp_path)
    resp = client.post("/webhooks/vapi", data="{\"message\": not json",
                        content_type="application/json")
    assert resp.status_code == 200
    assert st.count_events() == 0


# A call answered by voicemail is a call nobody heard. It must be reported
# as unreachable, not as "unclear whether the order went through".
def test_voicemail_end_of_call_is_unreachable(tmp_path):
    client, st, cfg = new_test_server(tmp_path)
    call = seed_call(st)

    post_webhook(client, json.dumps({"message": {
        "type": "end-of-call-report", "endedReason": "customer-ended-call",
        "transcript": "User: Your call has been forwarded to voice mail. "
                       "The person you're trying to reach is not available.\n",
        "call": {"id": PROVIDER_CALL_ID},
    }}))

    o = st.get_outcome(call.id)
    assert o.result == objective.RESULT_UNREACH
    assert o.failure_class == objective.FAIL_VOICEMAIL
