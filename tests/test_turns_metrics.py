import json

from otrango.turns.metrics import Turn, compute, parse_vapi_messages


def test_healthy_conversation():
    m = compute([
        Turn("assistant", 0.0, 3.0),
        Turn("user", 3.4, 6.0),
        Turn("assistant", 6.5, 9.0),
        Turn("user", 9.3, 11.0),
    ])
    assert m.turn_count == 4
    assert m.dead_air_events == 0
    assert m.barge_in_count == 0
    assert m.healthy(40)


def test_deadlock_shows_as_dead_air():
    m = compute([
        Turn("assistant", 0.0, 2.0),
        Turn("user", 8.0, 9.0),         # 6s of silence
        Turn("assistant", 16.0, 17.0),  # 7s more
    ])
    assert m.dead_air_events == 2
    assert m.gap_max_ms >= 6000
    assert not m.healthy(40)


def test_overlap_counts_as_barge_in_not_negative_gap():
    m = compute([
        Turn("assistant", 0.0, 4.0),
        Turn("user", 3.0, 5.0),  # starts 1s before the other stops
        Turn("assistant", 4.5, 6.0),
    ])
    assert m.barge_in_count == 2
    assert m.overlap_ms > 0
    assert m.gap_p50_ms >= 0


def test_same_speaker_pause_is_not_dead_air():
    m = compute([
        Turn("assistant", 0.0, 2.0),
        Turn("assistant", 9.0, 11.0),  # 7s, same speaker
        Turn("user", 11.3, 12.0),
    ])
    assert m.dead_air_events == 0


def test_empty_transcript():
    m = compute([])
    assert m.turn_count == 0
    assert m.gap_p50_ms == 0
    assert m.time_to_outcome_ms == 0


def test_parse_vapi_messages_skips_non_spoken_turns():
    raw = json.dumps([
        {"role": "system", "message": "prompt", "secondsFromStart": 0},
        {"role": "bot", "secondsFromStart": 1.0, "duration": 2000},
        {"role": "user", "secondsFromStart": 3.5, "duration": 1500},
        {"role": "tool_calls", "secondsFromStart": 5.0},
    ])
    got = parse_vapi_messages(raw)
    assert len(got) == 2
    assert got[0].role == "assistant"
    assert got[0].end == 3.0


def test_parse_vapi_messages_tolerates_garbage():
    assert parse_vapi_messages(json.dumps({"not": "an array"})) is None
