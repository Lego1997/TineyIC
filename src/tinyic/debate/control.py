"""Thread-safe live run controls for the web face.

The event log remains the only engine-to-renderer channel. This module is the
opposite direction only: a small process-local control seam that can pause at a
phase boundary, release one phase, resume automatic progress, or request a
graceful stop. It emits no events and carries no secrets.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


class DebateStopRequested(RuntimeError):
    """Raised at a safe engine checkpoint after the user requests stop."""


@dataclass(frozen=True)
class RunControlState:
    """Immutable status snapshot suitable for diagnostics and API metadata."""

    paused: bool
    stopped: bool
    phase_steps_queued: int


class RunControl:
    """Coordinate web controls with the debate worker without polling.

    ``pause`` takes effect at the next phase boundary. While paused,
    ``next_phase`` releases exactly one phase and leaves the following boundary
    parked. ``stop`` wakes every waiter; the worker raises
    :class:`DebateStopRequested` at its next phase/turn checkpoint.
    """

    def __init__(self, *, paused: bool = False) -> None:
        self._condition = threading.Condition()
        self._paused = bool(paused)
        self._stopped = False
        self._phase_steps = 0

    def pause(self) -> bool:
        """Park at the next phase boundary; return whether state changed."""
        with self._condition:
            if self._stopped or self._paused:
                return False
            self._paused = True
            self._phase_steps = 0
            return True

    def resume(self) -> bool:
        """Resume automatic phase progress and wake a parked worker."""
        with self._condition:
            if self._stopped or not self._paused:
                return False
            self._paused = False
            self._phase_steps = 0
            self._condition.notify_all()
            return True

    def next_phase(self) -> bool:
        """Release one future phase while remaining paused."""
        with self._condition:
            if self._stopped or not self._paused:
                return False
            self._phase_steps += 1
            self._condition.notify_all()
            return True

    def stop(self) -> bool:
        """Request graceful termination and wake a parked worker."""
        with self._condition:
            if self._stopped:
                return False
            self._stopped = True
            self._condition.notify_all()
            return True

    def wait_for_phase(self) -> None:
        """Block at a phase boundary until auto-run or one step is available."""
        with self._condition:
            while self._paused and self._phase_steps == 0 and not self._stopped:
                self._condition.wait()
            self._raise_if_stopped()
            if self._paused and self._phase_steps:
                self._phase_steps -= 1

    def check_stop(self) -> None:
        """Raise when stop was requested; intended for cheap turn checkpoints."""
        with self._condition:
            self._raise_if_stopped()

    def snapshot(self) -> RunControlState:
        """Return a lock-consistent immutable state snapshot."""
        with self._condition:
            return RunControlState(
                paused=self._paused,
                stopped=self._stopped,
                phase_steps_queued=self._phase_steps,
            )

    def _raise_if_stopped(self) -> None:
        if self._stopped:
            raise DebateStopRequested("debate stopped by user")


__all__ = ["DebateStopRequested", "RunControl", "RunControlState"]
