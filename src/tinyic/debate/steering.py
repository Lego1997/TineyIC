"""Engine-backed steering + interrupt — the moderator-side command channel.

The web API and headless ``--steer-stdin`` reader push commands into a
thread-safe :class:`SteeringInbox` that the debate loop drains at turn/phase
boundaries, and the *engine* emits the
authoritative ``steering_submitted`` / ``steering_delivered`` / ``steering_dropped``
events (and ``turn_interrupted`` for an interrupt).  Nothing here draws or
imports Textual; nothing on the renderer side imports this.

Delivery semantics mirror the frozen contract (``docs/event-schema.md`` /
FR-5.3), delivered **one at a time** at a boundary:

* ``steer`` — delivered at the targeted persona's next *speaker-turn* boundary
  (the next boundary of any speaker when untargeted, or when the target names no
  committee member).
* ``queue`` — delivered at the next *phase* boundary (before that phase's first
  turn).
* ``interrupt`` — the in-flight turn is discarded on arrival, the engine emits
  ``turn_interrupted`` and the speaker retakes the turn with the accompanying
  message (if any) in context.

An undelivered command still pending when the debate ends is dropped explicitly
with a ``steering_dropped`` event — never silently.
"""

from __future__ import annotations

import threading
from collections.abc import Collection
from dataclasses import dataclass

__all__ = [
    "SteeringCommand",
    "InterruptCommand",
    "SteeringInbox",
]

#: The two delivery modes carried by ``steering_submitted`` (schema enum).
_STEERING_MODES = frozenset({"steer", "queue"})


def _normalize_name(name: str | None) -> str | None:
    """Fold a persona name for target matching (casefold + space/underscore).

    Mirrors ``Moderator._match_override`` so a steer targeting ``"Warren
    Buffett"`` matches the same member the moderator addresses, whether the
    caller used the display name or the registry slug.
    """
    if not name:
        return None
    return name.casefold().replace(" ", "_")


@dataclass(frozen=True)
class SteeringCommand:
    """A queued steer/queue command awaiting delivery at a boundary."""

    msg_id: str
    mode: str  # "steer" | "queue"
    text: str
    target: str | None = None
    source: str = "stdin"  # "stdin" | "api"


@dataclass(frozen=True)
class InterruptCommand:
    """A pending hard-interrupt request; ``text`` (if any) lands on the speaker."""

    text: str | None = None
    target: str | None = None
    source: str = "stdin"


class SteeringInbox:
    """Thread-safe engine inbox: producers push, the debate loop drains.

    One inbox serves one debate. Producers (the stdin reader or web API) call
    :meth:`submit` / :meth:`request_interrupt`
    from any thread; the orchestrator (the single consumer) calls :meth:`drain`
    / :meth:`take_interrupt` at turn boundaries on the worker thread.  The
    ``steering_submitted`` acknowledgement is emitted **inside** the inbox lock so
    a command can never be delivered before it is acknowledged.
    """

    def __init__(self, event_log=None, *, id_prefix: str = "s") -> None:
        self._event_log = event_log
        self._id_prefix = id_prefix
        self._lock = threading.RLock()
        self._pending: list[SteeringCommand] = []
        self._interrupt: InterruptCommand | None = None
        self._counter = 0
        self._closed = False
        #: msg_ids already acknowledged with a ``steering_submitted`` event. The
        #: ack is emitted eagerly at submit when the log is open, else deferred to
        #: delivery/drop — so a command submitted before ``debate_started`` (a
        #: pre-loaded programmatic steer) is still acknowledged before it lands.
        self._acked: set[str] = set()

    def bind_event_log(self, event_log) -> None:
        """Attach the debate's event log if one was not supplied at construction."""
        with self._lock:
            if self._event_log is None:
                self._event_log = event_log

    # -- producer side ------------------------------------------------------ #

    def submit(
        self,
        mode: str,
        text: str,
        *,
        target: str | None = None,
        source: str = "stdin",
        msg_id: str | None = None,
    ) -> str | None:
        """Acknowledge and enqueue a steer/queue command; return its ``msg_id``.

        Returns ``None`` (and records nothing) when the inbox is closed, the mode
        is not ``steer``/``queue``, or the text is blank — a malformed producer
        line never corrupts the stream.
        """
        if mode not in _STEERING_MODES:
            return None
        body = (text or "").strip()
        if not body:
            return None
        with self._lock:
            if self._closed:
                return None
            self._counter += 1
            resolved_id = msg_id or f"{self._id_prefix}-{self._counter:04d}"
            command = SteeringCommand(
                msg_id=resolved_id,
                mode=mode,
                text=body,
                target=(target or None),
                source=source,
            )
            self._pending.append(command)
            # Ack eagerly when the log is already open (the real-time stdin/API
            # path); otherwise defer to drain/close so a pre-start submit is still
            # acknowledged before it is delivered or dropped.
            if getattr(self._event_log, "started", False):
                self._ack(command)
        return resolved_id

    def _ack(self, command: SteeringCommand) -> None:
        """Emit ``steering_submitted`` for a command once (idempotent, under lock)."""
        if command.msg_id in self._acked:
            return
        self._acked.add(command.msg_id)
        payload: dict[str, object] = {
            "msg_id": command.msg_id,
            "mode": command.mode,
            "text": command.text,
            "source": command.source,
        }
        if command.target:
            payload["target_persona"] = command.target
        self._safe_emit("steering_submitted", payload)

    def request_interrupt(
        self,
        *,
        text: str | None = None,
        target: str | None = None,
        source: str = "stdin",
    ) -> bool:
        """Record a hard-interrupt request; the engine acts on it at the turn end.

        The latest request wins (a second interrupt before the first is consumed
        replaces it).  Returns ``False`` when the inbox is already closed.
        """
        clean = (text or "").strip() or None
        with self._lock:
            if self._closed:
                return False
            self._interrupt = InterruptCommand(
                text=clean, target=(target or None), source=source
            )
        return True

    # -- consumer side (orchestrator, single thread) ------------------------ #

    def drain(
        self,
        *,
        phase_boundary: bool,
        upcoming_speaker: str | None = None,
        known_targets: Collection[str] | None = None,
    ) -> list[SteeringCommand]:
        """Return the commands deliverable at this boundary, in submit order.

        ``queue`` commands are returned only at a ``phase_boundary``. ``steer``
        commands are returned at every speaker-turn boundary, with one
        refinement: a steer addressed to a specific persona (``target``) waits
        for *that persona's* turn -- it is ready only when ``upcoming_speaker``
        is the target. A target naming no member of ``known_targets`` falls back
        to today's untargeted (broadcast) delivery, so it can never pend forever.
        Returned commands are removed from the inbox.
        """
        with self._lock:
            if self._closed or not self._pending:
                return []
            known = (
                {_normalize_name(name) for name in known_targets}
                if known_targets is not None
                else None
            )
            upcoming = _normalize_name(upcoming_speaker)
            ready: list[SteeringCommand] = []
            remaining: list[SteeringCommand] = []
            for command in self._pending:
                if self._is_ready(command, phase_boundary, upcoming, known):
                    ready.append(command)
                else:
                    remaining.append(command)
            self._pending = remaining
            # Acknowledge (once) each command as it leaves the inbox, so a
            # deferred ack strictly precedes its steering_delivered.
            for command in ready:
                self._ack(command)
            return ready

    @staticmethod
    def _is_ready(
        command: SteeringCommand,
        phase_boundary: bool,
        upcoming: str | None,
        known: set[str | None] | None,
    ) -> bool:
        """Whether ``command`` is deliverable at this boundary (see :meth:`drain`)."""
        if command.mode == "queue":
            return phase_boundary
        # steer: an addressed steer waits for its target's own turn; a target
        # that names no known member is treated as untargeted (broadcast).
        targeted = bool(command.target) and (
            known is None or _normalize_name(command.target) in known
        )
        if not targeted:
            return True
        return upcoming is not None and _normalize_name(command.target) == upcoming

    def requeue(self, commands: Collection[SteeringCommand]) -> None:
        """Return drained-but-undelivered commands to the front of the inbox.

        Used when a boundary delivery aborts mid-flight (e.g. the moderator relay
        raised): commands already drained but not yet acknowledged delivered are
        put back in submit order, so the terminal :meth:`close` still drops each
        one explicitly rather than losing it silently. If the inbox has already
        closed, each is dropped now (defensive -- requeue normally runs on the
        debate thread before ``close``).
        """
        with self._lock:
            if not commands:
                return
            if self._closed:
                for command in commands:
                    self._ack(command)
                    self._safe_emit(
                        "steering_dropped",
                        {"msg_id": command.msg_id, "reason": "debate_ended"},
                    )
                return
            self._pending[:0] = list(commands)

    def take_interrupt(self) -> InterruptCommand | None:
        """Atomically consume the pending interrupt request, if any."""
        with self._lock:
            interrupt = self._interrupt
            self._interrupt = None
            return interrupt

    def has_interrupt(self) -> bool:
        with self._lock:
            return self._interrupt is not None

    def emit_delivered(self, msg_id: str, *, before_turn_id: str) -> None:
        """Emit the authoritative ``steering_delivered`` for a delivered command."""
        with self._lock:
            self._safe_emit(
                "steering_delivered",
                {"msg_id": msg_id, "delivered_before_turn_id": before_turn_id},
            )

    def close(self, *, reason: str = "debate_ended") -> None:
        """Mark the inbox closed and drop every still-pending command explicitly.

        Idempotent.  ``steering_dropped`` is emitted only while the log is still
        open (before the terminal event), so callers must close the inbox *before*
        emitting ``debate_completed`` / ``debate_error``.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = self._pending
            self._pending = []
            for command in pending:
                # Keep the contract: an explicit drop is still preceded by its ack.
                self._ack(command)
                self._safe_emit(
                    "steering_dropped", {"msg_id": command.msg_id, "reason": reason}
                )

    # -- internals ---------------------------------------------------------- #

    def _safe_emit(self, event_type: str, payload: dict) -> None:
        """Emit an event, tolerating a not-yet-started or already-terminal log.

        A steering event that cannot legally be written (the debate has not
        started, or has already ended) is dropped rather than raised — the inbox
        must never crash the producer thread or abort a completing debate.
        """
        log = self._event_log
        if log is None:
            return
        try:
            log.emit(event_type, payload)
        except Exception:  # pragma: no cover - defensive, non-load-bearing
            pass
