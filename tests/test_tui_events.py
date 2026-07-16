"""Unit tests for the tolerant JSONL event reader (``tinyic.tui.events``).

These lock down the reader's half of the frozen engine->renderer contract:
seq-ordering, forward-compatible tolerance of unknown types/fields, and graceful
handling of truncated/garbage logs (a crashed debate leaves a partial last line).
"""

from __future__ import annotations

import json

from tinyic.tui import events as E
from tinyic.tui.events import Event, EventType

from tests.support import synthetic_events as G


def _line(seq, etype, payload=None, **envelope):
    obj = {
        "v": 1,
        "seq": seq,
        "ts": "2026-07-12T20:30:00.000Z",
        "debate_id": "x",
        "type": etype,
        "payload": payload or {},
    }
    obj.update(envelope)
    return json.dumps(obj)


# --------------------------------------------------------------------------- #
# Schema tables stay internally consistent
# --------------------------------------------------------------------------- #

def test_known_types_match_enum_and_required_field_table():
    assert E.KNOWN_EVENT_TYPES == frozenset(m.value for m in EventType)
    # Every known type declares its required payload fields (no silent gaps).
    assert set(E.REQUIRED_PAYLOAD_FIELDS) == E.KNOWN_EVENT_TYPES
    # Enum tables only reference known types.
    assert set(E.ENUM_FIELDS) <= E.KNOWN_EVENT_TYPES


# --------------------------------------------------------------------------- #
# seq ordering
# --------------------------------------------------------------------------- #

def test_read_events_sorts_by_seq():
    lines = [
        _line(3, "talk_completed", {"turn_id": "t1", "full_text": "c"}),
        _line(1, "debate_started", {}),
        _line(2, "turn_started", {"turn_id": "t1", "persona": "A", "phase": "opening", "role": "statement"}),
    ]
    events = E.read_events(lines)
    assert [e.seq for e in events] == [1, 2, 3]


def test_seqless_events_sort_after_and_keep_file_order():
    lines = [
        _line(2, "phase_completed", {"phase": "opening", "index": 0, "turn_count": 1}),
        json.dumps({"v": 1, "ts": "t", "debate_id": "x", "type": "usage", "payload": {}}),  # no seq
        _line(1, "debate_started", {}),
        json.dumps({"v": 1, "ts": "t", "debate_id": "x", "type": "memo_section", "payload": {}}),  # no seq
    ]
    events = E.read_events(lines)
    assert [e.seq for e in events[:2]] == [1, 2]
    # The two seq-less events keep their original file order at the tail.
    assert [e.type for e in events[2:]] == ["usage", "memo_section"]
    assert [e.seq for e in events[2:]] == [None, None]


# --------------------------------------------------------------------------- #
# Forward-compatible tolerance
# --------------------------------------------------------------------------- #

def test_unknown_event_type_is_preserved_not_dropped():
    lines = [
        _line(1, "debate_started", {}),
        _line(2, "brand_new_future_event", {"anything": [1, 2, 3]}),
    ]
    events = E.read_events(lines)
    assert len(events) == 2
    unknown = events[1]
    assert unknown.type == "brand_new_future_event"
    assert unknown.known is False
    assert unknown.payload == {"anything": [1, 2, 3]}
    # Unknown types declare no requirements, so they never look "invalid".
    assert E.missing_required_fields(unknown) == ()
    assert E.invalid_enum_fields(unknown) == ()


def test_unknown_payload_field_on_known_type_is_preserved():
    line = _line(
        5,
        "talk_completed",
        {"turn_id": "t1", "full_text": "hello", "future_field": {"nested": True}},
    )
    (event,) = E.read_events([line])
    assert event.known is True
    assert event.payload["future_field"] == {"nested": True}
    assert E.missing_required_fields(event) == ()


# --------------------------------------------------------------------------- #
# Truncated / garbage tolerance
# --------------------------------------------------------------------------- #

def test_truncated_final_line_is_skipped_gracefully():
    good = _line(1, "debate_started", {})
    good2 = _line(2, "phase_started", {"phase": "opening", "index": 0})
    truncated = '{"v":1,"seq":3,"type":"debate_comple'  # cut mid-write
    events = E.read_events([good, good2, truncated])
    assert [e.seq for e in events] == [1, 2]


def test_blank_and_non_object_lines_are_skipped():
    lines = [
        "",
        "   ",
        "123",
        "[1, 2, 3]",
        '"just a string"',
        "not json at all",
        json.dumps({"v": 1, "seq": 9, "payload": {}}),  # no type
        _line(1, "debate_started", {}),
    ]
    events = E.read_events(lines)
    assert [e.type for e in events] == ["debate_started"]


def test_empty_iterable_and_missing_file_yield_empty(tmp_path):
    assert E.read_events([]) == []
    assert E.read_events("/no/such/path/does-not-exist.jsonl") == []
    # A directory path (e.g. the runs/ dir fat-fingered) reads as empty, not a crash.
    assert E.read_events(tmp_path) == []


def test_parse_event_accepts_a_decoded_mapping():
    obj = {"v": 1, "seq": 7, "ts": "t", "debate_id": "x", "type": "usage",
           "payload": {"purpose": "turn", "model_ref": "m", "input_tokens": 1,
                       "output_tokens": 2, "cached_tokens": 0}}
    event = E.parse_event(obj)
    assert isinstance(event, Event)
    assert event.seq == 7 and event.type == "usage"
    assert E.parse_event({"no": "type"}) is None
    # Booleans must not masquerade as an int seq.
    weird = E.parse_event({"type": "usage", "seq": True, "payload": {}})
    assert weird is not None and weird.seq is None


# --------------------------------------------------------------------------- #
# Streaming/interrupt/error types (not in the happy-path fixture) still parse
# --------------------------------------------------------------------------- #

def test_delta_interrupt_dropped_error_types_are_known_and_valid():
    lines = [
        _line(1, "think_delta", {"turn_id": "t1", "text": "hm"}),
        _line(2, "talk_delta", {"turn_id": "t1", "text": "well,"}),
        _line(3, "turn_interrupted", {"turn_id": "t1", "persona": "A", "by": "user", "disposition": "cancelled"}),
        _line(4, "steering_dropped", {"msg_id": "m9", "reason": "debate ended first"}),
        _line(5, "debate_error", {"stage": "data", "message": "boom", "recoverable": False}),
    ]
    events = E.read_events(lines)
    assert all(e.known for e in events)
    for e in events:
        assert E.missing_required_fields(e) == ()
        assert E.invalid_enum_fields(e) == ()


def test_validators_flag_missing_field_and_bad_enum():
    missing = E.parse_event(_line(1, "vote_recorded", {"persona": "A", "vote": "BUY"}))
    assert set(E.missing_required_fields(missing)) == {
        "confidence", "reasoning", "key_risks", "changed_mind", "source",
    }
    bad_enum = E.parse_event(
        _line(2, "vote_recorded", {
            "persona": "A", "vote": "STRONG_BUY", "confidence": "HIGH",
            "reasoning": [], "key_risks": [], "changed_mind": False, "source": "structured",
        })
    )
    assert E.invalid_enum_fields(bad_enum) == (("vote", "STRONG_BUY"),)


# --------------------------------------------------------------------------- #
# is_replayable
# --------------------------------------------------------------------------- #

def test_is_replayable_requires_started_and_terminal():
    started = E.parse_event(_line(1, "debate_started", {}))
    completed = E.parse_event(_line(2, "debate_completed", {}))
    errored = E.parse_event(_line(2, "debate_error", {}))
    mid = E.parse_event(_line(2, "turn_started", {}))
    assert E.is_replayable([started, completed]) is True
    assert E.is_replayable([started, errored]) is True  # error is a valid terminal
    assert E.is_replayable([started, mid]) is False      # truncated / crashed
    assert E.is_replayable([]) is False


# --------------------------------------------------------------------------- #
# Against the committed golden-ish fixture
# --------------------------------------------------------------------------- #

def test_reads_committed_fixture_in_seq_order():
    events = E.read_events(G.FIXTURE_PATH)
    assert len(events) == 172
    assert [e.seq for e in events] == list(range(1, 173))
    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_completed"
    assert E.is_replayable(events) is True
    assert all(e.debate_id == G.DEBATE_ID for e in events)
