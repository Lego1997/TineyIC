"""Tests for the Town Hall TUI skeleton (``tinyic.tui.app``).

Covers the pure, loop-free load path, the event-line formatter's tolerance, and
a Textual ``run_test`` pilot proving the app mounts and renders a recorded log
end to end (the M5 replay entry point) without any LLM calls.
"""

from __future__ import annotations

from textual.widgets import RichLog

from tinyic.tui.app import TownHallApp, format_event_line, run_replay, summarize_event
from tinyic.tui.events import Event, read_events

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
# Textual pilot: the app mounts and renders the whole log
# --------------------------------------------------------------------------- #

async def test_pilot_renders_recorded_log():
    app = TownHallApp(G.FIXTURE_PATH)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.events) == 172
        log = app.query_one("#event-log", RichLog)
        assert len(log.lines) > 0  # something was written and laid out
        assert G.DEBATE_ID in app.sub_title


async def test_pilot_handles_empty_log():
    app = TownHallApp("/no/such/log.jsonl")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.events == []
        log = app.query_one("#event-log", RichLog)
        assert len(log.lines) > 0  # the "(no events ...)" placeholder line
