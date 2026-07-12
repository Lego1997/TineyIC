"""Composer steering: the seam between the TUI and the (future) debate engine.

FR-5.3 lets a human interject mid-debate from the composer bar. The message is
parsed here (mode + optional ``@name`` target + text) and handed to a
:class:`SteeringSink` — a tiny protocol that decouples the renderer from whoever
actually delivers steering. This keeps the load-bearing rule intact: the TUI
never imports engine internals; it depends only on this protocol and on parsed
:class:`~tinyic.tui.events.Event` objects.

Two sinks exist across milestones:

* :class:`ReplaySink` (this milestone) — a no-op that just **echoes** the
  submission back as a locally-generated ``steering_submitted`` event so the user
  sees their message land in the transcript in the ``queued`` state. There is no
  engine during replay, so it is never delivered (never flips to ``delivered``).
* an engine-backed sink (arrives with M1/M6) — submits into the real command
  queue drained at turn/phase boundaries, and the engine emits the authoritative
  ``steering_submitted`` / ``steering_delivered`` events.

Everything here is pure and framework-free (no Textual import), so the parser and
the echo sink are unit-testable without a running app.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from .events import SCHEMA_VERSION, Event, EventType

__all__ = [
    "SteeringMessage",
    "SteeringSink",
    "ReplaySink",
    "resolve_persona",
    "parse_steering_input",
]


@dataclass(frozen=True)
class SteeringMessage:
    """A parsed composer submission ready to hand to a :class:`SteeringSink`."""

    mode: str  # "steer" | "queue"
    text: str
    target: str | None = None


class SteeringSink(Protocol):
    """Where composer submissions go. The app depends on this, not the engine."""

    def submit(self, message: SteeringMessage) -> None:  # pragma: no cover - protocol
        ...


def resolve_persona(token: str, known_personas: Iterable[str]) -> str:
    """Resolve an ``@token`` to a committee member name, best-effort.

    Matching is tolerant so a short handle finds the right person:
    ``@buffett`` / ``@warren`` / ``@Warren`` all resolve to ``"Warren Buffett"``,
    and ``@li`` resolves to ``"Li Lu"`` (word match). An unrecognized token is
    returned verbatim so an unknown target is still carried, never silently
    dropped.
    """
    low = token.casefold()
    names = list(known_personas)
    for name in names:  # exact full-name match
        if name.casefold() == low:
            return name
    for name in names:  # match a whole word of the name ("buffett", "li")
        if low in name.casefold().split():
            return name
    for name in names:  # loose substring ("buff")
        if low and low in name.casefold():
            return name
    return token


def parse_steering_input(
    raw: str, *, mode: str, known_personas: Iterable[str] = ()
) -> SteeringMessage | None:
    """Parse a raw composer line into a :class:`SteeringMessage`.

    An ``@name`` prefix (the first whitespace-delimited token after ``@``) targets
    a persona; the rest is the message body. Returns ``None`` when there is no
    message body to send (blank input, or ``@name`` with no following text).
    """
    text = raw.strip()
    if not text:
        return None
    target: str | None = None
    if text.startswith("@"):
        token, _, rest = text[1:].partition(" ")
        if token:
            target = resolve_persona(token, known_personas)
            text = rest.strip()
    if not text:
        return None
    return SteeringMessage(mode=mode, text=text, target=target)


class ReplaySink:
    """No-op steering sink for replay: echoes submissions, never delivers them.

    Each submission becomes a locally-generated ``steering_submitted`` event
    (``source: "tui"``, a ``local-N`` msg_id that cannot collide with the
    engine's ids) pushed through the supplied ``emit`` callback. The app folds it
    into the transcript so the message appears immediately in the ``queued``
    state. Because there is no engine here, no ``steering_delivered`` ever
    follows — replay steering stays queued, honestly reflecting that it went
    nowhere. Real delivery arrives with the engine-backed sink in M1/M6.
    """

    def __init__(self, emit: Callable[[Event], None]) -> None:
        self._emit = emit
        self._counter = 0

    def submit(self, message: SteeringMessage) -> None:
        self._counter += 1
        payload: dict[str, object] = {
            "msg_id": f"local-{self._counter}",
            "mode": message.mode,
            "text": message.text,
            "source": "tui",
        }
        if message.target:
            payload["target_persona"] = message.target
        self._emit(
            Event(
                v=SCHEMA_VERSION,
                seq=None,
                ts="",
                debate_id="",
                type=EventType.STEERING_SUBMITTED.value,
                payload=payload,
                known=True,
            )
        )
