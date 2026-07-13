"""Pure tests for composer steering (``tinyic.tui.steering``).

The parser and the replay echo sink are framework-free, so they are exercised
here without Textual: ``@name`` targeting, mode passthrough, empty-input
rejection, tolerant persona resolution, and the ``local-N`` queued echo the
:class:`ReplaySink` emits (which never gets delivered — there is no engine during
replay).
"""

from __future__ import annotations

from tinyic.tui.events import Event
from tinyic.tui.steering import (
    InterruptRequest,
    RecordingControlSink,
    ReplayControlSink,
    ReplaySink,
    SteeringMessage,
    parse_steering_input,
    resolve_persona,
)

_COMMITTEE = (
    "Warren Buffett",
    "Charlie Munger",
    "Benjamin Graham",
    "Peter Lynch",
    "Howard Marks",
    "Li Lu",
)


# --------------------------------------------------------------------------- #
# parse_steering_input
# --------------------------------------------------------------------------- #

def test_plain_message_has_no_target_and_carries_mode():
    msg = parse_steering_input("watch the China exposure", mode="steer")
    assert msg == SteeringMessage(mode="steer", text="watch the China exposure", target=None)


def test_queue_mode_passes_through():
    msg = parse_steering_input("tie it to a multiple", mode="queue")
    assert msg is not None and msg.mode == "queue"


def test_at_prefix_targets_a_persona_and_strips_the_handle():
    msg = parse_steering_input(
        "@buffett press him on China", mode="steer", known_personas=_COMMITTEE
    )
    assert msg is not None
    assert msg.target == "Warren Buffett"
    assert msg.text == "press him on China"


def test_at_prefix_resolves_by_first_name_and_by_word():
    warren = parse_steering_input("@Warren look again", mode="steer", known_personas=_COMMITTEE)
    li = parse_steering_input("@li what about owner economics", mode="steer", known_personas=_COMMITTEE)
    assert warren is not None and warren.target == "Warren Buffett"
    assert li is not None and li.target == "Li Lu"


def test_unknown_target_is_kept_verbatim_not_dropped():
    msg = parse_steering_input("@nobody hello", mode="steer", known_personas=_COMMITTEE)
    assert msg is not None
    assert msg.target == "nobody"
    assert msg.text == "hello"


def test_blank_and_target_only_inputs_are_rejected():
    assert parse_steering_input("   ", mode="steer") is None
    assert parse_steering_input("", mode="queue") is None
    # `@name` with no message body -> nothing to send.
    assert parse_steering_input("@buffett   ", mode="steer", known_personas=_COMMITTEE) is None


def test_resolve_persona_is_case_insensitive_and_loose():
    assert resolve_persona("BUFFETT", _COMMITTEE) == "Warren Buffett"
    assert resolve_persona("munger", _COMMITTEE) == "Charlie Munger"
    assert resolve_persona("graham", _COMMITTEE) == "Benjamin Graham"
    assert resolve_persona("ghost", _COMMITTEE) == "ghost"  # verbatim fallback


# --------------------------------------------------------------------------- #
# ReplaySink: echoes as a queued steering_submitted, never delivers
# --------------------------------------------------------------------------- #

def test_replay_sink_echoes_a_well_formed_steering_submitted_event():
    emitted: list[Event] = []
    sink = ReplaySink(emitted.append)

    sink.submit(SteeringMessage(mode="steer", text="focus on China", target="Warren Buffett"))
    assert len(emitted) == 1
    event = emitted[0]
    assert event.type == "steering_submitted"
    assert event.known is True
    assert event.v == 1 and event.seq is None  # a local echo, not an engine seq
    payload = event.payload
    assert payload["msg_id"] == "local-1"
    assert payload["mode"] == "steer"
    assert payload["text"] == "focus on China"
    assert payload["source"] == "tui"
    assert payload["target_persona"] == "Warren Buffett"


def test_replay_sink_omits_target_when_untargeted_and_increments_ids():
    emitted: list[Event] = []
    sink = ReplaySink(emitted.append)

    sink.submit(SteeringMessage(mode="queue", text="one"))
    sink.submit(SteeringMessage(mode="steer", text="two"))
    assert [e.payload["msg_id"] for e in emitted] == ["local-1", "local-2"]
    assert "target_persona" not in emitted[0].payload
    # No steering_delivered is ever produced — replay steer stays queued.
    assert all(e.type == "steering_submitted" for e in emitted)


# --------------------------------------------------------------------------- #
# ControlSink: the esc hard-interrupt seam (mirror of the steering sink)
# --------------------------------------------------------------------------- #

def test_interrupt_request_defaults_and_fields():
    assert InterruptRequest() == InterruptRequest(turn_id=None, source="tui")
    req = InterruptRequest(turn_id="t7", source="stdin")
    assert req.turn_id == "t7" and req.source == "stdin"


def test_replay_control_sink_skips_to_next_turn():
    calls: list[InterruptRequest] = []
    # ReplayControlSink is a thin adapter over an app-provided skip callback; it
    # must invoke it once per interrupt (the "skip to next turn boundary" today).
    sink = ReplayControlSink(lambda: calls.append(True))
    sink.interrupt(InterruptRequest(turn_id="t3"))
    sink.interrupt(InterruptRequest(turn_id="t4"))
    assert calls == [True, True]


def test_recording_control_sink_records_requests_for_later_honoring():
    sink = RecordingControlSink()
    assert sink.requests == []
    sink.interrupt(InterruptRequest(turn_id="t1", source="tui"))
    sink.interrupt(InterruptRequest(turn_id="t2", source="stdin"))
    assert [r.turn_id for r in sink.requests] == ["t1", "t2"]
    assert [r.source for r in sink.requests] == ["tui", "stdin"]
