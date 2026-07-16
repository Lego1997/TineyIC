"""Live event source: watch a debate as it happens, renderer-free.

Replay reads a *recorded* JSONL log; **live** feeds the same events as they are
produced over a thread-safe :class:`queue.Queue`. A renderer does not learn
about the engine — it drains parsed
:class:`~tinyic.tui.events.Event` objects off the queue exactly as it drains a
recorded list. The producer may be anything that appends parsed events to the
queue, on any thread.

Two pieces live here:

* :class:`EventQueueSource` — an **app-side adapter**. Each pump tick a consumer
  calls :meth:`EventQueueSource.drain` on its own thread to pull
  whatever the producer has queued so far; the producer only ever calls
  ``queue.put`` on its thread, so the queue is the sole synchronization point. A
  sentinel (:data:`QUEUE_SENTINEL`, or ``None``) marks end-of-stream.
* :class:`EventLogFollower` / :func:`attach_event_log_follower` — one concrete
  **producer**: a daemon thread that *tails an actively-written JSONL file* (a
  debate running in another process appends to it; we follow it line by line)
  and puts each parsed event on the queue. This is exactly how the headless CLI
  watches a running debate live — a separate process, not a single import of
  engine code.

Nothing here imports Textual or engine internals — only the shared,
framework-free event reader (:func:`tinyic.tui.events.parse_event`). The module
lives at package scope because web, replay, and any future renderer share it.
"""

from __future__ import annotations

import codecs
import queue as _queue
import threading
from pathlib import Path

from .tui.events import Event, parse_event

__all__ = [
    "QUEUE_SENTINEL",
    "EventQueueSource",
    "EventLogFollower",
    "attach_event_log_follower",
]

# Put this (or ``None``) on the queue to mark end-of-stream. It lets a producer
# close the source even when no terminal debate event was emitted — e.g. the
# writer crashed, or a file follower was told to stop — so the app's pump can end
# cleanly instead of polling an abandoned queue forever.
QUEUE_SENTINEL = object()


def _is_sentinel(item: object) -> bool:
    return item is QUEUE_SENTINEL or item is None


class EventQueueSource:
    """Drain :class:`Event` objects a producer puts on a thread-safe queue.

    ``drain`` is **non-blocking**: it returns whatever is available right now and
    never waits, so it composes with the app's batched ~30 ms pump without ever
    stalling the UI thread. The producer runs on any other thread and only calls
    ``queue.put``; no other synchronization is needed.
    """

    def __init__(self, queue: "_queue.Queue[object]") -> None:
        self._queue = queue
        self._closed = False

    @property
    def queue(self) -> "_queue.Queue[object]":
        """The underlying queue a producer appends events to."""
        return self._queue

    @property
    def closed(self) -> bool:
        """True once a sentinel has been seen — no more events will ever come."""
        return self._closed

    def drain(self) -> list[Event]:
        """Return every event queued so far, in FIFO order, without blocking.

        A sentinel (:data:`QUEUE_SENTINEL` or ``None``) marks the stream closed
        and stops the drain; any well-formed events queued *before* it are still
        returned. Non-:class:`Event` junk is ignored defensively, so a stray put
        can never crash the renderer.
        """
        if self._closed:
            return []  # stream already ended; ignore anything queued afterward
        events: list[Event] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except _queue.Empty:
                break
            if _is_sentinel(item):
                self._closed = True
                break
            if isinstance(item, Event):
                events.append(item)
        return events

    def close(self) -> None:
        """Locally mark the source closed (does not touch the queue)."""
        self._closed = True


class EventLogFollower(threading.Thread):
    """A daemon thread that tails an actively-written JSONL event log.

    It reads the file from the start (so a watcher attaching mid-debate still
    sees the whole story), parses each *complete* newline-terminated line into an
    :class:`Event`, and puts it on the queue. Whatever a concurrent writer's
    mid-flush snapshot leaves dangling is held until it is complete: a
    half-written trailing *line* (no newline yet) waits for its newline, and a
    truncated trailing multi-byte UTF-8 *character* (the file is read as bytes
    through an incremental decoder) waits for its remaining bytes. The follower
    finishes after a terminal
    event (``debate_completed`` / ``debate_error``) or when :meth:`stop` is
    called, and **always** puts :data:`QUEUE_SENTINEL` on the way out so the
    paired :class:`EventQueueSource` flips ``closed`` and the app can end cleanly.
    """

    def __init__(
        self,
        path: str | Path,
        queue: "_queue.Queue[object]",
        *,
        poll_interval: float = 0.05,
        from_start: bool = True,
        stop_on_terminal: bool = True,
        stop_event: threading.Event | None = None,
    ) -> None:
        super().__init__(name="tinyic-event-follower", daemon=True)
        self._path = Path(path)
        self._queue = queue
        self._poll_interval = max(0.0, float(poll_interval))
        self._from_start = from_start
        self._stop_on_terminal = stop_on_terminal
        # NB: NOT ``self._stop`` — that name is a real ``threading.Thread`` method
        # the interpreter calls during join/teardown; shadowing it with an Event
        # breaks thread cleanup.
        self._stop_event = stop_event or threading.Event()
        # Set once the follower has opened the file and taken its start position,
        # i.e. it is actually watching. Callers (and tests of ``from_start=False``)
        # can wait on it to avoid racing the initial seek.
        self._ready = threading.Event()

    @property
    def queue(self) -> "_queue.Queue[object]":
        """The queue this follower feeds (handy when it created its own)."""
        return self._queue

    def stop(self) -> None:
        """Ask the follower to finish at its next poll boundary."""
        self._stop_event.set()

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def wait_ready(self, timeout: float | None = None) -> bool:
        """Block until the follower is watching the file (past its initial seek).

        Returns True once it is positioned, or False on timeout. Useful before
        appending in a ``from_start=False`` scenario so no line is missed.
        """
        return self._ready.wait(timeout)

    def run(self) -> None:
        try:
            self._follow()
        finally:
            # Guarantee the consumer is released even on error / early stop.
            self._queue.put(QUEUE_SENTINEL)

    # -- internals --------------------------------------------------------- #

    def _wait(self) -> bool:
        """Sleep one poll interval, returning True if asked to stop meanwhile."""
        return self._stop_event.wait(self._poll_interval or 0.01)

    def _follow(self) -> None:
        # The writer may create the file a beat after we attach; wait for it
        # (interruptibly) rather than racing to a FileNotFoundError.
        while not self._path.exists():
            if self._wait():
                return

        # Two nested layers of buffering keep the follower robust against a
        # concurrent writer's mid-flush snapshots. The *decoder* holds an
        # incomplete trailing multi-byte UTF-8 sequence (a poll that lands
        # mid-character, e.g. the first two bytes of a 3-byte '…') until its
        # remaining bytes arrive; ``buffer`` holds an incomplete trailing *line*
        # (no newline yet) until its newline arrives. That is why the file is
        # opened in **binary** mode and fed through an incremental decoder: a
        # text-mode read() of the whole position..EOF span decodes it as one
        # unit and raises UnicodeDecodeError on a partial trailing char — killing
        # the follower and losing even the fully-written lines ahead of it. LLM
        # debate speech is full of '—', '…', curly quotes and non-ASCII names,
        # so this mid-character split is a routine transient, not an edge case.
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        line_index = 0
        position = 0
        with self._path.open("rb") as handle:
            if not self._from_start:
                handle.seek(0, 2)  # tail from the current end
                position = handle.tell()
            self._ready.set()  # positioned and watching
            while True:
                handle.seek(position)
                chunk = handle.read()
                position = handle.tell()
                if chunk:
                    # final=False: an incomplete trailing byte sequence is held
                    # inside the decoder (no raise), to be completed by the next
                    # read — mirroring how ``buffer`` holds an incomplete line.
                    buffer += decoder.decode(chunk)
                    # Only whole, newline-terminated lines are parsed; a partial
                    # tail stays buffered until its newline arrives.
                    while "\n" in buffer:
                        raw, buffer = buffer.split("\n", 1)
                        event = parse_event(raw, line_index=line_index)
                        line_index += 1
                        if event is None:
                            continue  # blank / not-yet-valid line, keep going
                        self._queue.put(event)
                        if self._stop_on_terminal and event.is_terminal:
                            return
                    continue  # drained a chunk; immediately look for more
                if self._stop_event.is_set():
                    return
                if self._wait():
                    return


def attach_event_log_follower(
    path: str | Path,
    queue: "_queue.Queue[object] | None" = None,
    **kwargs: object,
) -> EventLogFollower:
    """Start (and return) an :class:`EventLogFollower` tailing ``path``.

    ``queue`` is the thread-safe queue the follower feeds and
    a renderer drains; if omitted, a fresh one is created and exposed as
    ``follower.queue``. The returned thread is already ``start()``-ed.

    This is the M6 bridge: a headless debate in another process writes the JSONL
    log; ``attach_event_log_follower`` turns that file into the live event queue
    a renderer consumes — with zero engine imports on the renderer side.
    """
    q = queue if queue is not None else _queue.Queue()
    follower = EventLogFollower(path, q, **kwargs)  # type: ignore[arg-type]
    follower.start()
    return follower
