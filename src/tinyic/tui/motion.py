"""Presentation-only motion for the Town Hall: one shared glyph-pulse helper.

Stage-1 rule: motion is a property of *widgets*, never of the event stream or
the folded state. :class:`GlyphPulse` is a tiny timer-driven glyph cycle a
widget owns; the widget starts it when its state says something is in flight
(the header's "debating…" indicator, a turn's live-think block) and stops it
the moment that state ends. Because the fold, the event log, replay, and export
never see it, a 100-turn replay renders identically with or without motion —
the pulse only ever changes which glyph the *currently mounted* widget paints.

Cost model: while stopped there is **no timer** and :attr:`GlyphPulse.glyph` is
the resting frame (the same static ``◉`` the UI used before motion existed), so
an idle or finished debate costs zero CPU. The frames stay inside the app's
existing glyph vocabulary (``· ● ◉``) — a breathing dot, not a new alphabet.
"""

from __future__ import annotations

from typing import Callable, Sequence

__all__ = ["PULSE_FRAMES", "PULSE_INTERVAL", "GlyphPulse"]

# A breathe-out/in cycle whose *resting* frame (index 0) is the historical
# static indicator glyph, so a stopped pulse renders exactly as before.
PULSE_FRAMES: tuple[str, ...] = ("◉", "●", "·", "●")
PULSE_INTERVAL = 0.35  # seconds per frame — calm, not a busy spinner


class GlyphPulse:
    """A timer-driven glyph cycle owned by exactly one widget.

    * ``set_running(True)`` starts the widget-owned interval timer — but only
      if the widget's message pump is actually running, so bare widgets in
      unit tests (or content built pre-mount) never touch the timer machinery.
    * ``set_running(False)`` stops the timer and snaps back to the resting
      frame; idempotent in both directions.
    * Each tick advances the frame and invokes ``on_frame`` (default: the
      widget's ``refresh``) so the owner repaints with the new glyph.

    Textual stops widget timers when the widget unmounts, so a pulse can never
    outlive its owner; the owning widget should still ``set_running(False)``
    when the *state* it animates ends (talk started, debate finished).
    """

    def __init__(
        self,
        widget,
        *,
        frames: Sequence[str] = PULSE_FRAMES,
        interval: float = PULSE_INTERVAL,
        on_frame: Callable[[], object] | None = None,
    ) -> None:
        if not frames:
            raise ValueError("GlyphPulse needs at least one frame")
        self._widget = widget
        self._frames = tuple(frames)
        self._interval = interval
        self._on_frame = on_frame if on_frame is not None else widget.refresh
        self._timer = None
        self._index = 0

    @property
    def running(self) -> bool:
        """True while the interval timer is live (state in flight)."""
        return self._timer is not None

    @property
    def glyph(self) -> str:
        """The frame to paint right now (the resting frame while stopped)."""
        return self._frames[self._index]

    def set_running(self, running: bool) -> None:
        """Start/stop the cycle to match the owning widget's state. Idempotent."""
        if running == self.running:
            return
        if running:
            # Never start a timer on a widget without a live message pump
            # (bare unit-test construction, pre-mount rendering).
            if not getattr(self._widget, "is_running", False):
                return
            self._timer = self._widget.set_interval(self._interval, self._advance)
        else:
            timer, self._timer = self._timer, None
            timer.stop()
            if self._index != 0:
                self._index = 0
                self._on_frame()  # repaint the resting frame

    def stop(self) -> None:
        """Alias for ``set_running(False)`` — reads better at call sites."""
        self.set_running(False)

    def _advance(self) -> None:
        self._index = (self._index + 1) % len(self._frames)
        self._on_frame()
