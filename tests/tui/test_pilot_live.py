"""Textual ``App.run_test`` pilots for the Town Hall TUI in **live** mode.

Replay pre-loads a recorded log; live is fed the *same* parsed events over a
thread-safe ``queue.Queue`` as they are produced. These pilots prove that live
and replay drive the identical ``feed`` → ``dispatch`` → ``_sync`` pump: events
put on the queue render into the transcript exactly as recorded ones do, the
header shows a live "debating…" status until a terminal event flips it to
complete/error, batching is preserved, and a full debate can be watched end to
end through a file follower — with the renderer only ever touching parsed
events, never the engine (PRD §3 / FR-5.1, FR-5.5).

Determinism: the queue is fed manually and the pump is driven manually
(``replay_all_now`` / ``_pump``) so there are no wall-clock assertions; the two
auto-mode pilots await :meth:`TownHallApp.wait_for_replay`, which resolves off an
app event (set when the stream is exhausted), not a fixed sleep.
"""

from __future__ import annotations

import queue

from tinyic.tui.app import TownHallApp
from tinyic.tui.events import read_events
from tinyic.live import QUEUE_SENTINEL, attach_event_log_follower
from tinyic.tui.widgets import PersonaCard, StatusHeader, TurnCard

from tests.support import synthetic_events as G

TOTAL_TURNS = 22  # opening 6 + cross_exam 4 + rebuttal 6 + verdict 6 (see replay pilots)


def _events():
    """The recorded log, parsed — the exact events a live producer would queue."""
    return read_events(G.FIXTURE_PATH)


def _header(app: TownHallApp) -> str:
    return str(app.query_one(StatusHeader).render()).lower()


# --------------------------------------------------------------------------- #
# (a) A queued prefix shows "debating…"; the terminal event flips to complete.
# --------------------------------------------------------------------------- #

async def test_live_prefix_debating_then_terminal_completes():
    events = _events()
    assert events[-1].type == "debate_completed"
    prefix, terminal = events[:-1], events[-1]

    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Mode is set up front: live view, not finished, header says "debating".
        assert app.live_mode is True
        assert app.state.live is True
        assert "debating" in _header(app)

        # Feed everything but the terminal event and fold it deterministically.
        for ev in prefix:
            q.put(ev)
        app.replay_all_now()
        await pilot.pause()

        # Same rendering as replay: real turn cards from queued events.
        assert len(list(app.query(TurnCard))) == TOTAL_TURNS
        assert len(list(app.query(PersonaCard))) == 6
        # Still live — no terminal yet — so the header holds "debating", not done.
        assert app.state.finished is False
        header = _header(app)
        assert "debating" in header
        assert "complete" not in header
        # The stream is open (no sentinel, no terminal), so the debate is running.
        assert app._replay_running is True

        # Now the terminal event lands and flips the header to final.
        q.put(terminal)
        app.replay_all_now()
        await pilot.pause()

        assert app.state.finished is True
        header = _header(app)
        assert "complete" in header
        assert "debating" not in header
        assert app._replay_running is False  # exhausted → quit needs no confirm


# --------------------------------------------------------------------------- #
# (b) A debate_error terminal flips the live header to an error state.
# --------------------------------------------------------------------------- #

async def test_live_error_terminal_shows_error():
    events = _events()
    started = events[0]
    assert started.type == "debate_started"
    error = type(started)(
        v=1, seq=99999, ts="", debate_id=started.debate_id,
        type="debate_error",
        payload={"stage": "turn", "message": "provider exploded", "recoverable": False},
    )

    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        q.put(started)
        q.put(error)
        app.replay_all_now()
        await pilot.pause()

        assert app.state.finished is True
        assert app.state.errored is True
        header = _header(app)
        assert "error" in header
        assert "debating" not in header


# --------------------------------------------------------------------------- #
# (c) Batching (FR-5.5): one pump tick folds at most ``batch_size`` events.
# --------------------------------------------------------------------------- #

async def test_live_pump_batches_events():
    prefix = _events()[:10]
    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q, auto_replay=False, batch_size=6)
    async with app.run_test() as pilot:
        await pilot.pause()
        for ev in prefix:
            q.put(ev)

        # One tick: the whole queue is drained onto _pending, but only one batch
        # is folded — the stream is not dumped at once.
        app._pump()
        await pilot.pause()
        assert app.state.applied_count == 6
        assert len(app._pending) == 4  # 10 queued − 6 folded

        # A second tick folds the rest.
        app._pump()
        await pilot.pause()
        assert app.state.applied_count == 10
        assert len(app._pending) == 0


# --------------------------------------------------------------------------- #
# (d) A sentinel closes the stream (no terminal event) and stops the pump.
# --------------------------------------------------------------------------- #

async def test_live_sentinel_closes_stream_without_terminal():
    prefix = _events()[:8]
    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        for ev in prefix:
            q.put(ev)
        q.put(QUEUE_SENTINEL)  # producer gives up without a terminal event
        app.replay_all_now()
        await pilot.pause()

        # The source is closed and exhausted; the pump has stopped.
        assert app._source is not None
        assert app._source.closed is True
        assert app._source_exhausted() is True
        assert app._replay_running is False
        # No terminal event ever arrived, so the debate is not "finished" — and
        # a closed stream is a *dead* one: the header drops the live "debating…"
        # pulse for the honest incomplete indicator (never a false "complete"),
        # and every in-flight cue settles (see test_tui_motion).
        assert app.state.finished is False
        assert app.state.truncated is True
        header = _header(app)
        assert "complete" not in header.replace("incomplete", "")
        assert "debating" not in header
        assert "incomplete" in header


# --------------------------------------------------------------------------- #
# (e) Auto mode: the timer-driven pump renders a queued debate to completion.
# --------------------------------------------------------------------------- #

async def test_live_auto_renders_to_completion():
    events = _events()
    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q)  # auto_replay=True: the batch timer drives the pump
    async with app.run_test() as pilot:
        for ev in events:  # includes the terminal debate_completed
            q.put(ev)
        # Resolves when the pump has folded the terminal event and stopped —
        # an app-event await, not a fixed sleep.
        await app.wait_for_replay()
        await pilot.pause()

        assert app.state.finished is True
        assert len(list(app.query(TurnCard))) == TOTAL_TURNS
        assert "complete" in _header(app)


async def test_live_auto_stops_on_sentinel():
    prefix = _events()[:8]
    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q)
    async with app.run_test() as pilot:
        for ev in prefix:
            q.put(ev)
        q.put(QUEUE_SENTINEL)
        await app.wait_for_replay()  # the sentinel closes the source → pump stops
        await pilot.pause()

        assert app._source is not None and app._source.closed is True
        assert app.state.finished is False
        assert app._replay_running is False


# --------------------------------------------------------------------------- #
# (f) End to end: a file follower feeds a live debate off an on-disk log.
#
# This is the M6 shape — a debate writes JSONL in another process; the follower
# tails it into the queue; the TUI renders it live, with zero engine imports.
# --------------------------------------------------------------------------- #

async def test_live_follows_a_written_log_file(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text(G.to_jsonl(G.build_events()), encoding="utf-8")

    q: queue.Queue = queue.Queue()
    follower = attach_event_log_follower(path, q, poll_interval=0.01)
    app = TownHallApp.live(q)
    try:
        async with app.run_test() as pilot:
            await app.wait_for_replay()  # follower emits the terminal → pump stops
            await pilot.pause()

            assert app.state.finished is True
            assert app.state.ticker == G.TICKER
            assert len(list(app.query(TurnCard))) == TOTAL_TURNS
            assert len(list(app.query(PersonaCard))) == 6
            assert "complete" in _header(app)
    finally:
        follower.stop()
        follower.join(timeout=5.0)
