"""Tests for the Stage-1 motion layer (``tinyic.tui.motion``).

The single :class:`GlyphPulse` helper animates the header's "debating…"
indicator and a turn's live-think block. The contract under test: it starts
only on a widget with a live message pump, stops (and rests on the historical
static glyph) the moment the state ends, costs nothing while idle (no timer),
and is presentation-only — driven by widget lifecycle, never by events, so the
folded state and replay/export output are untouched.
"""

from __future__ import annotations

import queue

from tinyic.tui.app import TownHallApp
from tinyic.tui.events import parse_event
from tinyic.tui.motion import PULSE_FRAMES, GlyphPulse
from tinyic.tui.widgets import StatusHeader, TurnCard


# --------------------------------------------------------------------------- #
# Unit level: a fake widget proves the lifecycle without Textual
# --------------------------------------------------------------------------- #

class _FakeTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeWidget:
    def __init__(self, *, is_running: bool = True) -> None:
        self.is_running = is_running
        self.intervals: list[tuple[float, object, _FakeTimer]] = []
        self.refreshes = 0

    def set_interval(self, interval, callback):
        timer = _FakeTimer()
        self.intervals.append((interval, callback, timer))
        return timer

    def refresh(self) -> None:
        self.refreshes += 1


def test_pulse_rests_on_the_historical_static_glyph():
    pulse = GlyphPulse(_FakeWidget())
    assert pulse.running is False
    assert pulse.glyph == PULSE_FRAMES[0] == "◉"  # exactly the pre-motion char


def test_pulse_starts_once_and_advances_frames():
    widget = _FakeWidget()
    pulse = GlyphPulse(widget)
    pulse.set_running(True)
    pulse.set_running(True)  # idempotent: still one timer
    assert pulse.running is True
    assert len(widget.intervals) == 1

    _, tick, _ = widget.intervals[0]
    seen = [pulse.glyph]
    for _ in range(len(PULSE_FRAMES)):
        tick()
        seen.append(pulse.glyph)
    # The cycle wraps back to the resting frame and repaints on every tick.
    assert seen[0] == seen[-1] == PULSE_FRAMES[0]
    assert set(seen) == set(PULSE_FRAMES)
    assert widget.refreshes == len(PULSE_FRAMES)


def test_pulse_stop_kills_the_timer_and_snaps_to_rest():
    widget = _FakeWidget()
    pulse = GlyphPulse(widget)
    pulse.set_running(True)
    _, tick, timer = widget.intervals[0]
    tick()
    assert pulse.glyph != PULSE_FRAMES[0]

    pulse.set_running(False)
    assert pulse.running is False
    assert timer.stopped is True
    assert pulse.glyph == PULSE_FRAMES[0]
    pulse.set_running(False)  # idempotent, no second timer interaction


def test_pulse_never_starts_on_a_widget_without_a_live_pump():
    # Bare unit-test construction / pre-mount rendering: no timer machinery.
    widget = _FakeWidget(is_running=False)
    pulse = GlyphPulse(widget)
    pulse.set_running(True)
    assert pulse.running is False
    assert widget.intervals == []


def test_pulse_custom_on_frame_receives_every_tick():
    widget = _FakeWidget()
    painted = []
    pulse = GlyphPulse(widget, on_frame=lambda: painted.append(pulse.glyph))
    pulse.set_running(True)
    _, tick, _ = widget.intervals[0]
    tick()
    tick()
    assert painted == [PULSE_FRAMES[1], PULSE_FRAMES[2]]
    assert widget.refreshes == 0  # the custom painter replaced refresh entirely


# --------------------------------------------------------------------------- #
# Pilot level: real widgets start/stop the pulse from folded state only
# --------------------------------------------------------------------------- #

def _ev(seq: int, type_: str, **payload):
    return parse_event(
        {
            "v": 1,
            "seq": seq,
            "ts": "2026-07-12T20:30:00.000Z",
            "debate_id": "aapl-20260712-puls",
            "type": type_,
            "payload": payload,
        }
    )


async def test_header_pulse_runs_while_live_and_stops_on_terminal_event():
    live_queue: queue.Queue = queue.Queue()
    app = TownHallApp.live(live_queue, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        header = app.query_one(StatusHeader)
        # Live and unfinished: the debating indicator breathes.
        assert header._pulse.running is True
        assert "debating" in str(header.render())

        live_queue.put(
            _ev(1, "debate_started", ticker="AAPL", company_name="Apple Inc.",
                preset="default", personas=[])
        )
        live_queue.put(_ev(2, "debate_completed", phases_completed=[], duration_s=1.0))
        app.replay_all_now()
        await pilot.pause()
        assert app.state.finished is True
        # The state ended -> the pulse stopped with it.
        assert header._pulse.running is False
        assert "complete" in str(header.render())


async def test_turn_live_think_pulse_stops_the_moment_talk_begins():
    events = [
        _ev(1, "debate_started", ticker="AAPL", company_name="Apple Inc.",
            preset="default", personas=[{"name": "Warren Buffett"}]),
        _ev(2, "phase_started", phase="opening", index=0),
        _ev(3, "turn_started", turn_id="t1", persona="Warren Buffett",
            phase="opening", role="statement"),
        _ev(4, "think_delta", turn_id="t1", text="weighing the moat"),
    ]
    app = TownHallApp(events=events, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.replay_all_now()
        await pilot.pause()
        card = app.query_one(TurnCard)
        assert card.turn.thinking_live is True
        assert card._pulse.running is True

        app.feed(_ev(5, "talk_delta", turn_id="t1", text="Here is my view."))
        app.replay_all_now()
        await pilot.pause()
        assert card.turn.thinking_live is False
        assert card._pulse.running is False
        # Resting glyph restored for any later static rendering.
        assert card._pulse.glyph == PULSE_FRAMES[0]
