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
