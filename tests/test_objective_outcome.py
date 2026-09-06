import pytest

from otrango.objective import outcome as ob


# Provider vocabulary varies. An unmapped reason silently degrades a known
# outcome to "ambiguous", discarding information we already had.
@pytest.mark.parametrize("reason,want_result,want_class", [
    ("no-answer", ob.RESULT_UNREACH, ob.FAIL_NO_ANSWER),
    ("noanswer", ob.RESULT_UNREACH, ob.FAIL_NO_ANSWER),
    ("customer-did-not-answer", ob.RESULT_UNREACH, ob.FAIL_NO_ANSWER),
    ("customer-busy", ob.RESULT_UNREACH, ob.FAIL_BUSY),
    ("voicemail", ob.RESULT_UNREACH, ob.FAIL_VOICEMAIL),
    ("exceeded-max-duration", ob.RESULT_AMBIGUOUS, ob.FAIL_CALL_TIMEOUT),
    ("customer-ended-call", ob.RESULT_AMBIGUOUS, ob.FAIL_AMBIGUOUS),
    ("pipeline-error", ob.RESULT_FAILED, ob.FAIL_PROVIDER_ERROR),
    ("", ob.RESULT_AMBIGUOUS, ob.FAIL_AMBIGUOUS),
    ("something-nobody-has-seen", ob.RESULT_AMBIGUOUS, ob.FAIL_AMBIGUOUS),
])
def test_classify_end_reason(reason, want_result, want_class):
    got_r, got_c = ob.classify_end_reason(reason)
    assert (got_r, got_c) == (want_result, want_class)


# Nothing may classify as success: the provider only reports how a call
# ended, never whether the errand actually worked.
@pytest.mark.parametrize("reason", ["no-answer", "customer-ended-call", "", "assistant-ended-call", "busy"])
def test_classify_never_returns_success(reason):
    got, _ = ob.classify_end_reason(reason)
    assert got != ob.RESULT_SUCCESS


# The real transcript from a call the restaurant never picked up. Vapi
# reported "customer-ended-call" -- the voicemail box answered -- and the
# owner was told it was unclear whether the order went through.
VOICEMAIL = (
    "User: Your call has been forwarded to voice mail. "
    "The person you're trying to reach is not available.\n"
)


@pytest.mark.parametrize("name,reason,transcript,result,klass,decided", [
    ("voicemail box", "customer-ended-call", VOICEMAIL, ob.RESULT_UNREACH, ob.FAIL_VOICEMAIL, True),
    ("carrier announcement", "customer-ended-call",
     "User: The number you are trying to reach is not reachable.",
     ob.RESULT_UNREACH, ob.FAIL_NO_ANSWER, True),
    ("no evidence either way", "customer-ended-call", "User: Hello?",
     ob.RESULT_AMBIGUOUS, ob.FAIL_AMBIGUOUS, False),
    ("specific reason wins over transcript", "customer-busy", VOICEMAIL,
     ob.RESULT_UNREACH, ob.FAIL_BUSY, False),
    ("a real conversation stays ambiguous", "customer-ended-call",
     "AI: One filter coffee please.\nUser: Sorry, we are closing.",
     ob.RESULT_AMBIGUOUS, ob.FAIL_AMBIGUOUS, False),
])
def test_classify_reads_voicemail_from_transcript(name, reason, transcript, result, klass, decided):
    r, cl, dec = ob.classify(reason, transcript)
    assert (r, cl, dec) == (result, klass, decided)
