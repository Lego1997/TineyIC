"""Focused checks for the web-to-engine run control seam."""

from __future__ import annotations

import threading

import pytest

from tinyic.debate.control import DebateStopRequested, RunControl
from tinyic.debate.moderator import Moderator


def test_unpaused_control_never_blocks_phase_boundary():
    control = RunControl()
    control.wait_for_phase()
    assert control.snapshot().paused is False


def test_pause_then_next_releases_exactly_one_phase():
    control = RunControl(paused=True)
    reached: list[str] = []

    def worker() -> None:
        control.wait_for_phase()
        reached.append("first")
        control.wait_for_phase()
        reached.append("second")

    thread = threading.Thread(target=worker)
    thread.start()
    assert reached == []
    assert control.next_phase() is True
    thread.join(timeout=0.05)
    assert reached == ["first"]
    assert thread.is_alive()
    assert control.resume() is True
    thread.join(timeout=1)
    assert reached == ["first", "second"]
    assert not thread.is_alive()


def test_stop_wakes_parked_worker_and_raises():
    control = RunControl(paused=True)
    stopped = threading.Event()

    def worker() -> None:
        with pytest.raises(DebateStopRequested):
            control.wait_for_phase()
        stopped.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert control.stop() is True
    assert stopped.wait(1)
    thread.join(timeout=1)
    with pytest.raises(DebateStopRequested):
        control.check_stop()


def test_moderator_accepts_run_control_and_legacy_event():
    moderator = Moderator()
    moderator.gate_phase(RunControl())
    legacy = threading.Event()
    legacy.set()
    moderator.gate_phase(legacy)
    assert not legacy.is_set()
