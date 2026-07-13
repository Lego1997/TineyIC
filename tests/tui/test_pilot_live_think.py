"""Textual pilots for live-think streaming + interrupts (M5 stage 2).

These prove FR-5.1's locked interview decision end to end: the *current* speaker's
THINK streams into an inline, auto-expanded highlight block on their turn card,
which auto-collapses to the standard ``▸ thinking`` row the moment their TALK
begins — driven purely by folded ``think_delta`` / ``talk_delta`` events, so
**replay and live behave identically** and historical turns are unaffected. They
also prove the ``esc`` hard-interrupt seam (FR-5.3): in live mode ``esc`` emits an
interrupt request through the narrow :class:`~tinyic.tui.steering.ControlSink`
(recorded for the engine to honor in M6) rather than fabricating a skip, and that
the thinking stream never bypasses the FR-5.5 batch pump.

Determinism: the queue is fed manually and the pump is driven manually
(``replay_all_now`` / ``_pump`` / ``_apply_batch``) — no wall-clock assertions.
"""

from __future__ import annotations

import queue

from tinyic.tui.app import TownHallApp
from tinyic.tui.events import parse_event
from tinyic.tui.state import TurnState
from tinyic.tui.steering import RecordingControlSink
from tinyic.tui.widgets import TurnCard


def _ev(type_: str, **payload):
    """One parsed :class:`Event` from a type + payload (no envelope needed)."""
    return parse_event({"type": type_, "payload": payload})


def _card(app: TownHallApp, turn_id: str) -> TurnCard:
    """The mounted :class:`TurnCard` for ``turn_id``."""
    widget = app._item_widgets[f"turn-{turn_id}"]
    assert isinstance(widget, TurnCard)
    return widget


def _turn_count(app: TownHallApp) -> int:
    return sum(1 for i in app.state.transcript if isinstance(i, TurnState))


def _bootstrap():
    """A debate + one already-finished delta turn (t001) — the 'historical' turn."""
    return [
        _ev(
            "debate_started",
            ticker="AAPL", company_name="Apple Inc.", preset="default",
            personas=[{"name": "Warren Buffett", "model_ref": "m"}],
            moderator={}, aggregator={}, caps={}, config_hash="x",
            tinyic_version="2.0",
        ),
        _ev("phase_started", phase="opening", index=0),
        # t001 streamed THINK *and* TALK, then completed — it was briefly "live"
        # but must be back to the standard row now that it has spoken.
        _ev("turn_started", turn_id="t001", persona="Warren Buffett",
            phase="opening", role="statement"),
        _ev("think_delta", turn_id="t001", text="first private thought"),
        _ev("talk_delta", turn_id="t001", text="my opening"),
        _ev("talk_completed", turn_id="t001", full_text="my opening statement"),
        _ev("turn_completed", turn_id="t001", persona="Warren Buffett",
            phase="opening", interrupted=False),
    ]


# --------------------------------------------------------------------------- #
# (a) Live: THINK streams in a highlighted block, then auto-collapses on TALK.
# --------------------------------------------------------------------------- #

async def test_live_think_streams_then_autocollapses_when_talk_begins():
    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        for ev in _bootstrap():
            q.put(ev)
        app.replay_all_now()
        await pilot.pause()

        # The current speaker begins; THINK streams token by token.
        tid = "t002"
        q.put(_ev("turn_started", turn_id=tid, persona="Warren Buffett",
                  phase="opening", role="statement"))
        q.put(_ev("think_delta", turn_id=tid, text="weighing the "))
        q.put(_ev("think_delta", turn_id=tid, text="durable moat"))
        app.replay_all_now()
        await pilot.pause()

        card = _card(app, tid)
        # The live highlight block is shown (auto-expanded) and the standard
        # collapsible row is hidden while THINK streams.
        assert card.turn.thinking_live is True
        assert card._think_live.display is True
        assert card._think.display is False
        assert "durable moat" in str(card._think_live.render())

        # think_completed (still no talk) keeps the live block up.
        q.put(_ev("think_completed", turn_id=tid,
                  full_text="weighing the durable moat carefully"))
        app.replay_all_now()
        await pilot.pause()
        assert card.turn.thinking_live is True
        assert card._think_live.display is True

        # The first TALK fragment auto-collapses to the standard ▸ thinking row.
        q.put(_ev("talk_delta", turn_id=tid, text="I would buy."))
        app.replay_all_now()
        await pilot.pause()
        assert card.turn.thinking_live is False
        assert card._think_live.display is False
        assert card._think.display is True
        # Content preserved and still collapsed (expandable via t/T as today).
        assert card._think.collapsed is True
        assert "durable moat carefully" in str(card._think_body.render())
        assert "I would buy." in str(card._speech.render())

        # The historical turn t001 is untouched — never live, standard row shown.
        hist = _card(app, "t001")
        assert hist.turn.thinking_live is False
        assert hist._think_live.display is False
        assert hist._think.display is True


# --------------------------------------------------------------------------- #
# (b) Replay drives the identical behavior off a recorded delta log.
# --------------------------------------------------------------------------- #

async def test_replay_with_deltas_streams_live_then_collapses():
    events = [
        _ev("debate_started", ticker="AAPL",
            personas=[{"name": "Warren Buffett"}]),                       # 0
        _ev("phase_started", phase="opening", index=0),                  # 1
        _ev("turn_started", turn_id="t1", persona="Warren Buffett",
            phase="opening", role="statement"),                          # 2
        _ev("think_delta", turn_id="t1", text="weighing "),              # 3
        _ev("think_delta", turn_id="t1", text="the moat"),               # 4
        _ev("talk_delta", turn_id="t1", text="I buy."),                  # 5
        _ev("talk_completed", turn_id="t1", full_text="I buy."),         # 6
        _ev("turn_completed", turn_id="t1", persona="Warren Buffett",
            phase="opening", interrupted=False),                         # 7
        _ev("debate_completed", phases_completed=["opening"],
            duration_s=1.0, result_ref="x"),                             # 8
    ]
    app = TownHallApp(events=events, auto_replay=False, batch_size=1000)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Fold through the second think_delta (indices 0..4): the turn is live.
        app._apply_batch(5)
        app._sync()
        await pilot.pause()
        card = _card(app, "t1")
        assert card.turn.thinking_live is True
        assert card._think_live.display is True
        assert card._think.display is False

        # Fold the talk_delta (index 5): it auto-collapses.
        app._apply_batch(1)
        app._sync()
        await pilot.pause()
        assert card.turn.thinking_live is False
        assert card._think_live.display is False
        assert card._think.display is True


# --------------------------------------------------------------------------- #
# (c) A completed-only log keeps the current, non-live behavior.
# --------------------------------------------------------------------------- #

async def test_live_completed_only_log_keeps_standard_thinking_row():
    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        q.put(_ev("debate_started", ticker="AAPL",
                  personas=[{"name": "Warren Buffett"}]))
        q.put(_ev("phase_started", phase="opening", index=0))
        tid = "t001"
        q.put(_ev("turn_started", turn_id=tid, persona="Warren Buffett",
                  phase="opening", role="statement"))
        # Only a one-shot think_completed — never a delta.
        q.put(_ev("think_completed", turn_id=tid, full_text="a full private thought"))
        app.replay_all_now()
        await pilot.pause()

        card = _card(app, tid)
        assert card.turn.thinking_streaming is False
        assert card.turn.thinking_live is False
        assert card._think_live.display is False   # no live block ever
        assert card._think.display is True          # standard collapsed row
        assert card._think.collapsed is True


# --------------------------------------------------------------------------- #
# (d) A folded turn_interrupted event stamps the card's interrupt badge.
# --------------------------------------------------------------------------- #

async def test_folded_turn_interrupted_shows_the_badge():
    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        for ev in _bootstrap():
            q.put(ev)
        tid = "t002"
        q.put(_ev("turn_started", turn_id=tid, persona="Warren Buffett",
                  phase="opening", role="statement"))
        q.put(_ev("think_delta", turn_id=tid, text="mid thought"))
        q.put(_ev("turn_interrupted", turn_id=tid, persona="Warren Buffett",
                  by="user", disposition="cancelled"))
        app.replay_all_now()
        await pilot.pause()

        card = _card(app, tid)
        assert card.turn.interrupted is True
        assert card.turn.thinking_live is False  # interrupt clears the live block
        assert card._think_live.display is False
        head = str(card._head.render())
        assert "interrupted" in head and "(esc)" in head


# --------------------------------------------------------------------------- #
# (e) esc in live mode emits an interrupt request via the ControlSink (no skip).
# --------------------------------------------------------------------------- #

async def test_esc_in_live_records_interrupt_request_without_skipping():
    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Live default is the recording control sink (M6 swaps in the engine one).
        assert isinstance(app._control, RecordingControlSink)

        for ev in _bootstrap():
            q.put(ev)
        q.put(_ev("turn_started", turn_id="t002", persona="Warren Buffett",
                  phase="opening", role="statement"))
        q.put(_ev("think_delta", turn_id="t002", text="mid thought"))
        app.replay_all_now()
        await pilot.pause()
        turns_before = _turn_count(app)

        await pilot.press("escape")
        await pilot.pause()

        # The request is recorded for the producer to honor (engine, M6); the
        # renderer neither skips events nor fabricates an interrupt badge.
        assert len(app._control.requests) == 1
        req = app._control.requests[0]
        assert req.turn_id == "t002"   # the current speaker at esc time
        assert req.source == "tui"
        assert _turn_count(app) == turns_before
        assert app.state.current_turn_id == "t002"
        assert _card(app, "t002").turn.interrupted is False


# --------------------------------------------------------------------------- #
# (f) The thinking stream rides the FR-5.5 batch pump (never bypasses it).
# --------------------------------------------------------------------------- #

async def test_live_think_deltas_go_through_the_batch_pump():
    q: queue.Queue = queue.Queue()
    app = TownHallApp.live(q, auto_replay=False, batch_size=3)
    async with app.run_test() as pilot:
        await pilot.pause()
        for ev in (
            _ev("debate_started", ticker="T", personas=[{"name": "Solo"}]),
            _ev("phase_started", phase="opening", index=0),
            _ev("turn_started", turn_id="t1", persona="Solo",
                phase="opening", role="statement"),
            _ev("think_delta", turn_id="t1", text="a"),
            _ev("think_delta", turn_id="t1", text="b"),
            _ev("think_delta", turn_id="t1", text="c"),
        ):
            q.put(ev)

        # One tick drains the whole queue onto _pending but folds only one batch:
        # the deltas are paced through the pump, not dumped at once.
        app._pump()
        await pilot.pause()
        assert app.state.applied_count == 3
        assert len(app._pending) == 3

        app._pump()
        await pilot.pause()
        assert app.state.applied_count == 6
        assert len(app._pending) == 0
        card = _card(app, "t1")
        assert card.turn.thinking == "abc"
        assert card.turn.thinking_live is True
