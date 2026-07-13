"""Unit tests for the framework-free Town Hall view state (``tinyic.tui.state``).

The state fold is the shared code path behind both replay and (later) a live
event queue, so these tests exercise it directly — no Textual, no timers — for
speed and determinism. They lock down: progressive accumulation, per-persona
cognitive/vote updates, steering queued->delivered transitions, cost/token
rollups (including subscription None-cost lanes), elapsed-time derivation, and
tolerance of unknown/partial events.
"""

from __future__ import annotations

from tinyic.tui.events import parse_event, read_events
from tinyic.tui.state import (
    ArtifactState,
    PhaseState,
    SteeringState,
    TownHallState,
    TurnState,
    humanize_count,
    humanize_duration,
)

from tests.support import synthetic_events as G


def _folded() -> TownHallState:
    state = TownHallState()
    for event in read_events(G.FIXTURE_PATH):
        state.dispatch(event)
    return state


# --------------------------------------------------------------------------- #
# Progressive accumulation (the "same code path" replay/live drives)
# --------------------------------------------------------------------------- #

def test_dispatch_accumulates_one_event_at_a_time():
    state = TownHallState()
    events = read_events(G.FIXTURE_PATH)
    assert state.applied_count == 0
    assert state.transcript == []

    # Fold the first handful; only a fraction of the transcript exists yet.
    for event in events[:12]:
        state.dispatch(event)
    assert state.applied_count == 12
    partial_turns = sum(1 for i in state.transcript if isinstance(i, TurnState))
    assert 0 < partial_turns < 22  # strictly in-progress, not the full log

    for event in events[12:]:
        state.dispatch(event)
    assert state.applied_count == len(events) == 172
    assert sum(1 for i in state.transcript if isinstance(i, TurnState)) == 22


def test_full_fold_header_rollups():
    state = _folded()
    assert state.ticker == "AAPL"
    assert state.company_name == "Apple Inc."
    assert state.preset == "default"
    assert state.finished is True and state.errored is False
    assert state.phases_completed == ["opening", "cross_exam", "rebuttal", "verdict"]
    assert state.current_phase == "verdict"
    assert state.phase_position == "verdict (4/4)"
    # duration_s from debate_completed wins over ts-derived elapsed.
    assert state.duration_s == 372.5
    assert state.elapsed_s == 372.5


def test_committee_is_ordered_and_fully_bound():
    state = _folded()
    assert list(state.personas) == [
        "Warren Buffett",
        "Charlie Munger",
        "Benjamin Graham",
        "Peter Lynch",
        "Howard Marks",
        "Li Lu",
    ]
    buffett = state.personas["Warren Buffett"]
    assert buffett.model_ref == "anthropic/claude-opus-4-8"
    assert buffett.auth_profile == "anthropic:claude-max"
    assert buffett.thinking_level == "high"
    assert buffett.temperament == "balanced"


# --------------------------------------------------------------------------- #
# Live per-persona updates
# --------------------------------------------------------------------------- #

def test_cognitive_state_updates_persona_badges():
    state = _folded()
    buffett = state.personas["Warren Buffett"]
    # Latest cognitive_state for Buffett is his verdict turn.
    assert buffett.mood  # emotions snippet present
    assert buffett.attention == "final decision"
    assert buffett.goal  # first goal captured
    # Final vote reflected on the card.
    assert buffett.vote == "BUY" and buffett.confidence == "HIGH"
    assert buffett.stance == "bullish"


def test_collapse_metric_flags_caving_and_updates_stance():
    state = _folded()
    lynch = state.personas["Peter Lynch"]
    marks = state.personas["Howard Marks"]
    assert lynch.caved is True
    assert lynch.stance == "cautious-hold"  # stance_after from collapse_metric
    assert marks.caved is False


def test_only_the_active_speaker_is_marked_speaking_mid_debate():
    state = TownHallState()
    events = read_events(G.FIXTURE_PATH)
    # Stop right after the first turn_started, before its turn_completed.
    applied = 0
    for event in events:
        state.dispatch(event)
        applied += 1
        if event.type == "turn_started":
            break
    speaking = [n for n, p in state.personas.items() if p.speaking]
    assert speaking == ["Warren Buffett"]  # opening speaker, exactly one


# --------------------------------------------------------------------------- #
# Steering transitions
# --------------------------------------------------------------------------- #

def test_steering_queued_then_delivered():
    state = _folded()
    steers = [i for i in state.transcript if isinstance(i, SteeringState)]
    by_id = {s.msg_id: s for s in steers}
    assert set(by_id) == {"m01", "m02"}
    assert by_id["m01"].mode == "steer"
    assert by_id["m01"].status == "delivered"
    assert by_id["m01"].delivered_before_turn_id == "t009"
    assert by_id["m02"].mode == "queue"
    assert by_id["m02"].status == "delivered"


def test_steering_dropped_is_marked():
    state = TownHallState()
    state.dispatch(parse_event(
        '{"type":"steering_submitted","payload":{"msg_id":"z9","mode":"steer","text":"hi","source":"tui"}}'
    ))
    state.dispatch(parse_event(
        '{"type":"steering_dropped","payload":{"msg_id":"z9","reason":"debate ended first"}}'
    ))
    (steer,) = [i for i in state.transcript if isinstance(i, SteeringState)]
    assert steer.status == "dropped"
    assert steer.reason == "debate ended first"


# --------------------------------------------------------------------------- #
# Phase banners & transcript composition
# --------------------------------------------------------------------------- #

def test_phase_banners_gain_turn_counts_on_completion():
    state = _folded()
    phases = [i for i in state.transcript if isinstance(i, PhaseState)]
    assert [p.phase for p in phases] == ["opening", "cross_exam", "rebuttal", "verdict"]
    assert all(p.completed for p in phases)
    assert [p.turn_count for p in phases] == [6, 4, 6, 6]
    cross = next(p for p in phases if p.phase == "cross_exam")
    assert cross.da_persona == "Howard Marks"


def test_artifacts_appear_as_transcript_cards():
    state = _folded()
    arts = [i for i in state.transcript if isinstance(i, ArtifactState)]
    kinds = [a.kind for a in arts]
    assert kinds.count("memo_section") == 5
    assert "scorecard" in kinds
    assert "disagreement" in kinds
    assert "data_ready" in kinds
    assert state.scorecard is not None and state.scorecard["consensus"] == "BUY"


def test_turn_cards_carry_speech_and_thinking():
    state = _folded()
    turns = [i for i in state.transcript if isinstance(i, TurnState)]
    assert all(t.speech for t in turns)
    assert all(t.thinking for t in turns)  # every turn has a thinking row payload
    assert all(t.completed for t in turns)


# --------------------------------------------------------------------------- #
# Per-turn stance (FR-5.1 stance badge) + mind-view think snippet (FR-5.2)
# --------------------------------------------------------------------------- #

def test_turn_stance_is_stamped_from_thesis_and_vote_and_backfilled():
    state = _folded()
    turns = [i for i in state.transcript if isinstance(i, TurnState)]

    def turn(persona: str, phase: str) -> TurnState:
        return next(t for t in turns if t.persona == persona and t.phase == phase)

    # The opening thesis stance stamps the just-finished opening turn (the event
    # arrives right after it), for bulls and bears alike.
    assert turn("Warren Buffett", "opening").stance == "bullish"
    assert turn("Benjamin Graham", "opening").stance == "bearish"
    # Backfill: that stance carries onto the persona's later turns until it
    # changes — Buffett stays bullish through cross-exam and rebuttal.
    assert turn("Warren Buffett", "cross_exam").stance == "bullish"
    assert turn("Warren Buffett", "rebuttal").stance == "bullish"
    # The verdict vote overrides the stance on the verdict turn.
    assert turn("Warren Buffett", "verdict").stance == "BUY"
    assert turn("Benjamin Graham", "verdict").stance == "SELL"
    assert turn("Peter Lynch", "verdict").stance == "HOLD"


def test_persona_think_snippet_tracks_latest_completed_thinking():
    state = _folded()
    # The mind view reads the persona's latest private-reasoning snippet, mirrored
    # from that persona's most recent think event (their verdict turn).
    buffett = state.personas["Warren Buffett"]
    assert buffett.think
    buffett_turns = [
        t for t in state.transcript
        if isinstance(t, TurnState) and t.persona == "Warren Buffett"
    ]
    assert buffett.think == buffett_turns[-1].thinking


# --------------------------------------------------------------------------- #
# Usage / cost rollups
# --------------------------------------------------------------------------- #

def test_usage_rollup_sums_tokens_and_costs_and_counts_subscription_lanes():
    state = _folded()
    assert state.input_tokens == 45000
    assert state.output_tokens == 10120
    assert round(state.cost_usd, 4) == 0.2115
    # Anthropic (claude-max) turns report cost_usd == null -> subscription lane.
    assert state.subscription_calls == 10
    assert state.usage_window is not None
    assert state.usage_window["window_used_msgs"] == 18


def test_elapsed_falls_back_to_timestamps_before_completion():
    state = TownHallState()
    events = read_events(G.FIXTURE_PATH)
    for event in events[:5]:  # no debate_completed yet
        state.dispatch(event)
    assert state.duration_s is None
    assert state.elapsed_s > 0  # derived from ts span


# --------------------------------------------------------------------------- #
# Tolerance
# --------------------------------------------------------------------------- #

def test_unknown_and_partial_events_do_not_crash_the_fold():
    state = TownHallState()
    state.dispatch(parse_event('{"type":"debate_started","payload":{}}'))
    state.dispatch(parse_event('{"type":"brand_new_future_event","payload":{"x":1}}'))
    state.dispatch(parse_event('{"type":"turn_started","payload":{}}'))  # no turn_id
    state.dispatch(parse_event('{"type":"cognitive_state","payload":{"persona":"Ghost"}}'))
    # Unknown type counted but not applied to any pane; malformed turn ignored.
    assert state.applied_count == 4
    assert state.transcript == []
    assert state.personas == {}


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def test_humanize_helpers():
    assert humanize_count(950) == "950"
    assert humanize_count(12400) == "12.4k"
    assert humanize_count(45000) == "45k"
    assert humanize_count(2_500_000) == "2.5M"
    assert humanize_duration(372.5) == "6:12"
    assert humanize_duration(8) == "0:08"
    assert humanize_duration(None) == "0:00"


# --------------------------------------------------------------------------- #
# Live-think fold (FR-5.1 locked decision): think_delta streams live, talk
# auto-collapses; completed-only logs keep the old behavior.
# --------------------------------------------------------------------------- #

def _feed(state: TownHallState):
    """Return a helper that dispatches ``(type, **payload)`` into ``state``."""
    def ev(type_: str, **payload) -> None:
        state.dispatch(parse_event({"type": type_, "payload": payload}))
    return ev


def _only_turn(state: TownHallState) -> TurnState:
    return next(i for i in state.transcript if isinstance(i, TurnState))


def test_thinking_live_property_semantics():
    t = TurnState(key="k", turn_id="t", persona="p", phase="opening", role="statement")
    assert t.thinking_live is False  # nothing streaming yet
    t.thinking_streaming = True
    assert t.thinking_live is True  # a delta arrived, no talk/complete/interrupt
    t.talk_started = True
    assert t.thinking_live is False  # talk collapses the live block
    t.talk_started = False
    t.completed = True
    assert t.thinking_live is False  # completion collapses it
    t.completed = False
    t.interrupted = True
    assert t.thinking_live is False  # an interrupt collapses it


def test_think_delta_marks_thinking_live_until_talk_begins():
    state = TownHallState()
    ev = _feed(state)
    ev("turn_started", turn_id="t1", persona="Warren Buffett", phase="opening", role="statement")
    turn = _only_turn(state)
    assert turn.thinking_live is False  # no think yet

    ev("think_delta", turn_id="t1", text="weighing ")
    ev("think_delta", turn_id="t1", text="the moat")
    assert turn.thinking_streaming is True
    assert turn.thinking == "weighing the moat"
    assert turn.thinking_live is True  # streaming, talk not begun -> live block

    # think_completed alone must NOT collapse the block (talk hasn't begun).
    ev("think_completed", turn_id="t1", full_text="weighing the moat carefully")
    assert turn.thinking == "weighing the moat carefully"
    assert turn.thinking_live is True

    # The first talk fragment collapses it, content preserved for the ▸ row.
    ev("talk_delta", turn_id="t1", text="I would buy.")
    assert turn.talk_started is True
    assert turn.thinking_live is False
    assert turn.thinking == "weighing the moat carefully"


def test_completed_only_turn_never_goes_live():
    state = TownHallState()
    ev = _feed(state)
    ev("turn_started", turn_id="t1", persona="X", phase="opening", role="statement")
    ev("think_completed", turn_id="t1", full_text="a full private thought")
    turn = _only_turn(state)
    # No think_delta ever arrived, so the stream was never "live": the turn keeps
    # the standard collapsed ▸ thinking row (current behavior).
    assert turn.thinking_streaming is False
    assert turn.thinking_live is False
    assert turn.thinking == "a full private thought"
    ev("talk_completed", turn_id="t1", full_text="my statement")
    ev("turn_completed", turn_id="t1", persona="X", phase="opening", interrupted=False)
    assert turn.talk_started is True
    assert turn.thinking_live is False


def test_one_shot_talk_completed_collapses_a_live_block():
    # A provider that streams THINK deltas but delivers TALK in one shot still
    # collapses the block the instant talk_completed lands.
    state = TownHallState()
    ev = _feed(state)
    ev("turn_started", turn_id="t1", persona="X", phase="opening", role="statement")
    ev("think_delta", turn_id="t1", text="streamed thought")
    turn = _only_turn(state)
    assert turn.thinking_live is True
    ev("talk_completed", turn_id="t1", full_text="one-shot statement")
    assert turn.talk_started is True
    assert turn.thinking_live is False


def test_talk_completed_marks_speech_final_but_deltas_do_not():
    # ``speech_final`` is the renderer's cue to swap the plain incremental
    # stream for the rendered (markdown) form — it must flip only on the
    # authoritative talk_completed, never on a delta (Stage-1 markdown speech).
    state = TownHallState()
    ev = _feed(state)
    ev("turn_started", turn_id="t1", persona="X", phase="opening", role="statement")
    turn = _only_turn(state)
    assert turn.speech_final is False

    ev("talk_delta", turn_id="t1", text="**streaming ")
    ev("talk_delta", turn_id="t1", text="markdown**")
    assert turn.speech == "**streaming markdown**"
    assert turn.speech_final is False  # still mid-stream

    ev("talk_completed", turn_id="t1", full_text="**streaming markdown**")
    assert turn.speech_final is True


def test_turn_interrupted_captures_provenance_and_clears_live():
    state = TownHallState()
    ev = _feed(state)
    ev("turn_started", turn_id="t1", persona="X", phase="cross_exam", role="response")
    ev("think_delta", turn_id="t1", text="mid thought")
    turn = _only_turn(state)
    assert turn.thinking_live is True

    ev("turn_interrupted", turn_id="t1", persona="X", by="user", disposition="cancelled")
    assert turn.interrupted is True
    assert turn.interrupted_by == "user"
    assert turn.interrupt_disposition == "cancelled"
    assert turn.thinking_live is False  # an interrupt clears the live block

    # A trailing turn_completed(interrupted=False) must not erase the interrupt.
    ev("turn_completed", turn_id="t1", persona="X", phase="cross_exam", interrupted=False)
    assert turn.interrupted is True
    assert turn.interrupted_by == "user"


# --------------------------------------------------------------------------- #
# Terminal events settle every in-flight cue (nothing pulses past the end)
# --------------------------------------------------------------------------- #

def _mid_think_state() -> TownHallState:
    """A fold parked mid-THINK: speaker spotlit, bench spinner on, block live."""
    state = TownHallState()
    ev = _feed(state)
    ev("debate_started", ticker="AAPL",
       personas=[{"name": "Warren Buffett"}, {"name": "Howard Marks"}])
    ev("phase_started", phase="opening", index=0)
    ev("turn_started", turn_id="t1", persona="Warren Buffett",
       phase="opening", role="statement")
    ev("think_delta", turn_id="t1", text="weighing the moat")
    buffett = state.personas["Warren Buffett"]
    assert buffett.speaking is True and buffett.thinking_active is True
    assert _only_turn(state).thinking_live is True
    return state


def test_debate_error_mid_think_settles_speaker_and_live_block():
    state = _mid_think_state()
    state.dispatch(parse_event({
        "type": "debate_error",
        "payload": {"stage": "opening", "message": "provider 500"},
    }))
    assert state.finished is True and state.errored is True
    buffett = state.personas["Warren Buffett"]
    assert buffett.speaking is False
    assert buffett.thinking_active is False
    turn = _only_turn(state)
    assert turn.thinking_live is False
    assert turn.thinking_streaming is False
    # The streamed reasoning survives for the standard collapsed ▸ row.
    assert turn.thinking == "weighing the moat"


def test_debate_completed_mid_think_settles_speaker_and_live_block():
    state = _mid_think_state()
    state.dispatch(parse_event({
        "type": "debate_completed",
        "payload": {"phases_completed": ["opening"], "duration_s": 1.0},
    }))
    assert state.finished is True and state.errored is False
    for member in state.personas.values():
        assert member.speaking is False
        assert member.thinking_active is False
    assert _only_turn(state).thinking_live is False


def test_settle_is_idempotent_and_public_for_stream_death():
    # Renderers call settle() directly when the stream dies without a terminal
    # event (truncated replay log, live sentinel close) — same result, twice.
    state = _mid_think_state()
    state.settle()
    state.settle()
    assert state.personas["Warren Buffett"].speaking is False
    assert state.personas["Warren Buffett"].thinking_active is False
    turn = _only_turn(state)
    assert turn.thinking_live is False
    assert turn.thinking == "weighing the moat"
    # A completed turn is left untouched (nothing to settle).
    assert turn.completed is False
