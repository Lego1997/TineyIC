"""Tests for the Town Hall TUI (``tinyic.tui.app``).

Covers the pure, loop-free load path, the event-line formatter's tolerance, and
Textual ``run_test`` pilots proving the app mounts and renders a recorded log
end to end across its panes (the M5 replay entry point) without any LLM calls.
"""

from __future__ import annotations

import asyncio

from tinyic.tui.app import TownHallApp, format_event_line, run_replay, summarize_event
from tinyic.tui.events import Event, read_events
from tinyic.tui.widgets import PersonaCard, PhaseBanner, StatusHeader, SteeringNote, TurnCard

from tests.support import synthetic_events as G


# --------------------------------------------------------------------------- #
# Loop-free load path
# --------------------------------------------------------------------------- #

def test_app_loads_events_from_log_path_without_running():
    app = TownHallApp(G.FIXTURE_PATH)
    assert len(app.events) == 172
    assert app.events[0].type == "debate_started"


def test_app_accepts_preparsed_events():
    events = read_events(G.FIXTURE_PATH)
    app = TownHallApp(events=events)
    assert app.events is not events  # defensively copied
    assert len(app.events) == 172


def test_app_with_no_source_is_empty():
    app = TownHallApp()
    assert app.events == []


def test_app_with_missing_file_is_empty_not_crashing():
    app = TownHallApp("/no/such/log.jsonl")
    assert app.events == []


# --------------------------------------------------------------------------- #
# Formatter tolerance
# --------------------------------------------------------------------------- #

def test_format_event_line_is_stable_and_tolerant():
    events = read_events(G.FIXTURE_PATH)
    for event in events:
        line = format_event_line(event)
        assert isinstance(line, str) and line
        assert event.type in line

    # An unknown, seq-less, empty-payload event must still format, not crash.
    unknown = Event(v=1, seq=None, ts="", debate_id="x", type="mystery_event",
                    payload={}, known=False, line_index=0)
    line = format_event_line(unknown)
    assert "mystery_event" in line
    assert "unknown type" in summarize_event(unknown)


# --------------------------------------------------------------------------- #
# run_replay wiring (no real TUI launch)
# --------------------------------------------------------------------------- #

def test_run_replay_constructs_app_and_runs(monkeypatch):
    launched = {}

    def fake_run(self):  # noqa: ANN001 - test stub
        launched["events"] = len(self.events)
        launched["path"] = self.log_path

    monkeypatch.setattr(TownHallApp, "run", fake_run)
    run_replay(G.FIXTURE_PATH)
    assert launched["events"] == 172
    assert str(launched["path"]) == str(G.FIXTURE_PATH)


# --------------------------------------------------------------------------- #
# Textual pilot: the app mounts and renders the whole log across its panes
# --------------------------------------------------------------------------- #

async def test_pilot_renders_recorded_log():
    # Fast pacing so the batched timer drains quickly under the test loop.
    app = TownHallApp(G.FIXTURE_PATH, tick=0.005, batch_size=8)
    async with app.run_test() as pilot:
        await asyncio.wait_for(app.wait_for_replay(), 5)
        await pilot.pause()
        assert len(app.events) == 172
        assert G.DEBATE_ID in app.sub_title
        # Transcript pane: one card per committed turn (opening 6 + xexam 4 +
        # rebuttal 6 + verdict 6 = 22), four phase banners, two steering notes.
        assert len(app.query(TurnCard)) == 22
        assert len(app.query(PhaseBanner)) == 4
        assert len(app.query(SteeringNote)) == 2
        # Committee sidebar: six persona cards.
        assert len(app.query(PersonaCard)) == 6
        # Header reflects the debate.
        header_text = str(app.query_one(StatusHeader).render())
        assert "AAPL" in header_text and "verdict" in header_text


async def test_pilot_renders_progressively_not_all_at_once():
    # With auto_replay off, mounting must not fold any events; draining then
    # renders them — proving replay is progressive, not a single mount-time dump.
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.state.applied_count == 0
        assert len(app.query(TurnCard)) == 0
        app.replay_all_now()
        await pilot.pause()
        assert app.state.applied_count == 172
        assert len(app.query(TurnCard)) == 22


async def test_pilot_toggle_thinking_expands_all_rows():
    from textual.widgets import Collapsible

    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.replay_all_now()
        await pilot.pause()
        collapsibles = list(app.query(Collapsible))
        assert collapsibles and all(c.collapsed for c in collapsibles)
        app.action_toggle_thinking()
        await pilot.pause()
        assert all(not c.collapsed for c in app.query(Collapsible))


async def test_pilot_handles_empty_log():
    app = TownHallApp("/no/such/log.jsonl")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.events == []
        # A placeholder is shown in the transcript instead of turn cards.
        note = app.query_one("#empty-note")
        assert "no events" in str(note.render())
        assert len(app.query(TurnCard)) == 0
