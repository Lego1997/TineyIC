"""Tests for the Town Hall composer, keys, and phase flow (FR-5.2/5.3/5.4).

Two layers:

* **Loop-free** checks of the pump's phase gate, the ``esc`` skip, and the small
  action flags — these fold events straight through the app object (no timer).
* **Textual pilots** that press real keys / drive the composer to prove the wiring
  (mode toggle, queued steer echo, thinking toggle on the selected turn, the
  interrupt skip, and the quit-confirm modal) end to end on a recorded log.
"""

from __future__ import annotations

from textual.widgets import Collapsible, Input

from tinyic.tui.app import QuitConfirmScreen, TownHallApp
from tinyic.tui.state import SteeringState, TurnState
from tinyic.tui.steering import SteeringMessage
from tinyic.tui.widgets import SteeringNote, TurnCard

from tests.support import synthetic_events as G


def _turns(app: TownHallApp) -> list[TurnState]:
    return [i for i in app.state.transcript if isinstance(i, TurnState)]


# --------------------------------------------------------------------------- #
# Phase gate (FR-5.4): paused mode holds at each phase banner
# --------------------------------------------------------------------------- #

def test_paused_pump_holds_at_each_phase_boundary():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False, batch_size=1000)
    app.paused = True

    # First drain parks right after opening's banner: no opening turns yet.
    app._drain_gated()
    assert app._holding is True
    assert app.state.current_phase == "opening"
    assert len(_turns(app)) == 0

    # Releasing plays out opening and re-parks at cross_exam's banner.
    app._holding = False
    app._drain_gated()
    assert app._holding is True
    assert app.state.current_phase == "cross_exam"
    assert len(_turns(app)) == 6

    # And again into rebuttal (opening 6 + cross_exam 4).
    app._holding = False
    app._drain_gated()
    assert app.state.current_phase == "rebuttal"
    assert len(_turns(app)) == 10
    assert app.state.finished is False


def test_unpaused_pump_never_holds():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False, batch_size=1000)
    assert app.paused is False
    while app._pending:
        app._drain_gated()
        assert app._holding is False
    assert app.state.finished is True
    assert len(_turns(app)) == 22


# --------------------------------------------------------------------------- #
# Interrupt (FR-5.2/5.3: esc) — skip to next turn boundary
# --------------------------------------------------------------------------- #

def test_skip_to_turn_boundary_advances_exactly_one_turn():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False, batch_size=1000)
    app._skip_to_turn_boundary()
    turns = _turns(app)
    assert len(turns) == 1 and turns[-1].completed is True
    app._skip_to_turn_boundary()
    assert len(_turns(app)) == 2


def test_interrupt_overrides_a_phase_hold():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False, batch_size=1000)
    app.paused = True
    app._drain_gated()  # holding at opening banner, 0 turns
    assert app._holding is True and len(_turns(app)) == 0
    app._skip_to_turn_boundary()  # hard interrupt breaks the hold
    assert app._holding is False
    assert len(_turns(app)) == 1


# --------------------------------------------------------------------------- #
# Action flags (loop-free)
# --------------------------------------------------------------------------- #

def test_toggle_mode_flips_steer_and_queue():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    assert app.steer_mode == "steer"
    app.action_toggle_mode()
    assert app.steer_mode == "queue"
    app.action_toggle_mode()
    assert app.steer_mode == "steer"


def test_toggle_pause_clears_hold_on_resume():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    app.action_toggle_pause()
    assert app.paused is True
    app._holding = True
    app.action_toggle_pause()  # resume
    assert app.paused is False
    assert app._holding is False


def test_next_phase_only_releases_hold_while_paused():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    app.paused = True
    app._holding = True
    app.action_next_phase()
    assert app._holding is False
    # When not paused, `n` is inert (no phase-stepping in auto-advance).
    app.paused = False
    app._holding = True
    app.action_next_phase()
    assert app._holding is True


# --------------------------------------------------------------------------- #
# Composer pilots (FR-5.3)
# --------------------------------------------------------------------------- #

async def test_composer_and_mode_chip_render():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#composer-input", Input) is not None
        assert "STEER" in str(app.query_one("#mode-chip").render())
        # Drive mode by default: nothing focused, single keys are controls.
        assert app.focused is None


async def test_submitting_composer_echoes_a_queued_steer():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()  # personas populated; the log's own 2 steers rendered
        await pilot.pause()
        assert len(app.query(SteeringNote)) == 2

        app.set_focus(app._composer_input)
        app._composer_input.value = "@buffett watch the China exposure"
        await pilot.press("enter")
        await pilot.pause()

        # A third, locally-echoed steering note appears in the queued state.
        assert len(app.query(SteeringNote)) == 3
        local = [
            s for s in app.state.transcript
            if isinstance(s, SteeringState) and s.msg_id == "local-1"
        ]
        assert len(local) == 1
        note = local[0]
        assert note.status == "queued"  # never delivered — no engine in replay
        assert note.target_persona == "Warren Buffett"
        assert note.text == "watch the China exposure"
        # Input cleared and focus handed back to drive mode.
        assert app._composer_input.value == ""
        assert app.focused is None


async def test_submitting_in_queue_mode_marks_the_note_queue():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()
        app.action_toggle_mode()  # -> queue
        app.set_focus(app._composer_input)
        app._composer_input.value = "everyone cite a multiple"
        await pilot.press("enter")
        await pilot.pause()
        note = next(
            s for s in app.state.transcript
            if isinstance(s, SteeringState) and s.msg_id == "local-1"
        )
        assert note.mode == "queue"
        assert note.target_persona is None


# --------------------------------------------------------------------------- #
# Key routing pilots (FR-5.2)
# --------------------------------------------------------------------------- #

async def test_tab_toggles_mode_without_stealing_focus():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.steer_mode == "steer"
        await pilot.press("tab")
        assert app.steer_mode == "queue"
        assert app.focused is None  # tab did NOT move focus into the composer
        await pilot.press("tab")
        assert app.steer_mode == "steer"


async def test_enter_in_drive_mode_focuses_the_composer():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not app._is_composing
        await pilot.press("enter")
        assert app._is_composing


async def test_escape_key_interrupts_and_skips_a_turn():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.query(TurnCard)) == 0
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.query(TurnCard)) == 1


async def test_t_toggles_thinking_on_the_selected_turn_only():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()
        # No explicit selection -> latest turn is the effective target.
        latest = _turns(app)[-1].key
        first = _turns(app)[0].key
        assert app._effective_selected_key() == latest

        app.action_toggle_thinking_selected()
        await pilot.pause()
        assert app._item_widgets[latest]._think.collapsed is False
        assert app._item_widgets[first]._think.collapsed is True  # untouched

        # Selecting an earlier turn redirects `t` to it.
        app.select_turn(first)
        assert app._effective_selected_key() == first
        assert app._item_widgets[first].selected is True
        app.action_toggle_thinking_selected()
        await pilot.pause()
        assert app._item_widgets[first]._think.collapsed is False


async def test_T_still_toggles_all_thinking_rows():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()
        collapsibles = list(app.query(Collapsible))
        assert collapsibles and all(c.collapsed for c in collapsibles)
        app.action_toggle_thinking()
        await pilot.pause()
        assert all(not c.collapsed for c in app.query(Collapsible))


# --------------------------------------------------------------------------- #
# Pause / phase-step under a live timer (pilot)
# --------------------------------------------------------------------------- #

async def test_paused_replay_holds_then_steps_with_n():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False, batch_size=1000)
    app.paused = True
    async with app.run_test() as pilot:
        app._pump()  # drains to the opening banner, then parks
        await pilot.pause()
        assert app._holding is True
        assert app.state.current_phase == "opening"
        assert not app.state.finished

        app.action_next_phase()  # release one phase
        app._pump()
        await pilot.pause()
        assert app.state.current_phase == "cross_exam"
        assert not app.state.finished


# --------------------------------------------------------------------------- #
# Quit confirmation (FR-5.2)
# --------------------------------------------------------------------------- #

async def test_q_while_running_opens_confirm_then_cancels():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._quit_needs_confirm() is True
        await pilot.press("q")
        await pilot.pause()
        assert isinstance(app.screen, QuitConfirmScreen)
        # Escape (or `n`) dismisses the dialog and keeps the debate.
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, QuitConfirmScreen)
        assert app.is_running


async def test_quit_needs_no_confirm_once_replay_is_complete():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._quit_needs_confirm() is True
        app.replay_all_now()
        await pilot.pause()
        assert app._quit_needs_confirm() is False
