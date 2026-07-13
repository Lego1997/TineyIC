"""Tests for the live event source: ``EventQueueSource`` and the JSONL follower.

These cover the M5 live-feed *bridge* — the piece that lets a debate running in
another process be watched live by the TUI without a single engine import. The
:class:`~tinyic.tui.live.EventQueueSource` is exercised as a pure queue adapter
(no threads); the :class:`~tinyic.tui.live.EventLogFollower` is exercised against
a file the test writes **incrementally**, which is exactly the concurrent-writer
situation it exists for. All thread coordination is through blocking
``queue.get(timeout=...)`` — the test only ever advances when the follower has
actually produced something, so there is no sleep-and-hope and no wall-clock
*assertion*.
"""

from __future__ import annotations

import json
import queue
import threading

import pytest

from tinyic.tui.events import Event
from tinyic.tui.live import (
    QUEUE_SENTINEL,
    EventLogFollower,
    EventQueueSource,
    attach_event_log_follower,
)

# Generous ceilings: the follower's poll interval is ~10 ms, so a get normally
# returns in well under a millisecond. These only guard against a hang.
GET_TIMEOUT = 5.0
JOIN_TIMEOUT = 5.0


def _line(event: dict) -> str:
    """One newline-terminated JSONL record, matching the engine's writer."""
    return json.dumps(event, separators=(",", ":")) + "\n"


def _append(path, text: str) -> None:
    """Append raw text to the file and close it (flushing to the OS)."""
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


def _ev(seq: int, etype: str, **payload) -> dict:
    return {"v": 1, "seq": seq, "ts": "", "debate_id": "d", "type": etype, "payload": payload}


# --------------------------------------------------------------------------- #
# EventQueueSource — the app-side queue adapter (pure, no threads)
# --------------------------------------------------------------------------- #

def test_queue_source_drains_fifo_and_ignores_junk():
    q: queue.Queue = queue.Queue()
    src = EventQueueSource(q)
    a = Event(v=1, seq=1, ts="", debate_id="d", type="turn_started", payload={})
    b = Event(v=1, seq=2, ts="", debate_id="d", type="talk_delta", payload={})

    assert src.drain() == []  # nothing queued yet
    assert src.closed is False

    q.put(a)
    q.put("not-an-event")  # defensively ignored, never crashes
    q.put(b)
    drained = src.drain()
    assert [e.type for e in drained] == ["turn_started", "talk_delta"]
    assert src.closed is False


def test_queue_source_sentinel_closes_and_keeps_prior_events():
    q: queue.Queue = queue.Queue()
    src = EventQueueSource(q)
    e = Event(v=1, seq=1, ts="", debate_id="d", type="phase_started", payload={})
    after = Event(v=1, seq=2, ts="", debate_id="d", type="turn_started", payload={})

    q.put(e)
    q.put(QUEUE_SENTINEL)
    q.put(after)  # queued after the sentinel: never surfaced

    drained = src.drain()
    assert [x.type for x in drained] == ["phase_started"]  # prior event kept
    assert src.closed is True
    # A second drain after close stays closed and yields nothing new to render.
    assert src.drain() == []
    assert src.closed is True


def test_queue_source_accepts_none_as_sentinel():
    q: queue.Queue = queue.Queue()
    src = EventQueueSource(q)
    q.put(None)
    assert src.drain() == []
    assert src.closed is True


def test_queue_source_close_is_local_only():
    q: queue.Queue = queue.Queue()
    src = EventQueueSource(q)
    src.close()
    assert src.closed is True
    assert src.queue is q


# --------------------------------------------------------------------------- #
# EventLogFollower — tails an actively-written JSONL file into the queue
# --------------------------------------------------------------------------- #

def test_follower_tails_file_written_incrementally(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text("", encoding="utf-8")  # exists but empty at attach

    q: queue.Queue = queue.Queue()
    follower = attach_event_log_follower(path, q, poll_interval=0.01)
    try:
        # First two whole lines arrive in order as they are appended.
        _append(path, _line(_ev(1, "debate_started", ticker="AAPL")))
        _append(path, _line(_ev(2, "phase_started", phase="opening", index=0)))
        e1 = q.get(timeout=GET_TIMEOUT)
        e2 = q.get(timeout=GET_TIMEOUT)
        assert (e1.type, e2.type) == ("debate_started", "phase_started")
        assert e1.seq == 1 and e2.seq == 2

        # A half-written record: bytes land without the closing newline. The
        # follower must NOT parse it yet — it holds the partial in its buffer.
        full = _line(_ev(3, "turn_started", turn_id="t1", persona="Warren Buffett"))
        head, tail = full[:34], full[34:]
        assert "\n" not in head and tail.endswith("\n")
        _append(path, head)
        # Now complete the line; only now should the whole, valid event appear.
        _append(path, tail)
        e3 = q.get(timeout=GET_TIMEOUT)
        assert e3.type == "turn_started"
        assert e3.payload["turn_id"] == "t1"  # the record was reassembled intact

        # A terminal event ends the follow and is delivered before the sentinel.
        _append(path, _line(_ev(4, "debate_completed", phases_completed=["opening"])))
        e4 = q.get(timeout=GET_TIMEOUT)
        assert e4.type == "debate_completed"
        assert q.get(timeout=GET_TIMEOUT) is QUEUE_SENTINEL  # end-of-stream marker

        follower.join(timeout=JOIN_TIMEOUT)
        assert not follower.is_alive()  # thread exited after the terminal event
    finally:
        follower.stop()
        follower.join(timeout=JOIN_TIMEOUT)


def test_follower_reads_preexisting_content_from_start(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text(
        _line(_ev(1, "debate_started", ticker="MSFT"))
        + _line(_ev(2, "phase_started", phase="opening", index=0))
        + _line(_ev(3, "debate_completed", phases_completed=["opening"])),
        encoding="utf-8",
    )
    q: queue.Queue = queue.Queue()
    follower = attach_event_log_follower(path, q, poll_interval=0.01)
    try:
        got = [q.get(timeout=GET_TIMEOUT) for _ in range(3)]
        assert [e.type for e in got] == [
            "debate_started",
            "phase_started",
            "debate_completed",
        ]
        assert q.get(timeout=GET_TIMEOUT) is QUEUE_SENTINEL
        follower.join(timeout=JOIN_TIMEOUT)
        assert not follower.is_alive()
    finally:
        follower.stop()
        follower.join(timeout=JOIN_TIMEOUT)


def test_follower_from_start_false_tails_only_new_lines(tmp_path):
    path = tmp_path / "run.jsonl"
    # Pre-existing history the tail-only follower must skip.
    path.write_text(
        _line(_ev(1, "debate_started", ticker="NVDA"))
        + _line(_ev(2, "phase_started", phase="opening", index=0)),
        encoding="utf-8",
    )
    q: queue.Queue = queue.Queue()
    follower = EventLogFollower(path, q, poll_interval=0.01, from_start=False)
    follower.start()
    try:
        # Wait until the follower has seeked past the history, so the append that
        # follows is unambiguously "new" (deterministic, no sleep-and-hope).
        assert follower.wait_ready(timeout=GET_TIMEOUT)
        # Only records appended *after* attach are surfaced.
        _append(path, _line(_ev(3, "turn_started", turn_id="t9", persona="Li Lu")))
        e = q.get(timeout=GET_TIMEOUT)
        assert e.type == "turn_started" and e.seq == 3
    finally:
        follower.stop()
        follower.join(timeout=JOIN_TIMEOUT)
        # Stopping always releases the consumer with a sentinel.
        assert follower.queue.get(timeout=GET_TIMEOUT) is QUEUE_SENTINEL


def test_follower_stop_puts_sentinel_on_a_nonterminating_log(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text(_line(_ev(1, "debate_started", ticker="AAPL")), encoding="utf-8")
    q: queue.Queue = queue.Queue()
    follower = attach_event_log_follower(path, q, poll_interval=0.01)
    try:
        assert q.get(timeout=GET_TIMEOUT).type == "debate_started"
        # No terminal event ever arrives; an explicit stop must still release us.
        follower.stop()
        assert q.get(timeout=GET_TIMEOUT) is QUEUE_SENTINEL
        follower.join(timeout=JOIN_TIMEOUT)
        assert not follower.is_alive()
    finally:
        follower.stop()
        follower.join(timeout=JOIN_TIMEOUT)


def test_follower_waits_for_a_file_created_after_attach(tmp_path):
    path = tmp_path / "later.jsonl"  # does not exist yet
    q: queue.Queue = queue.Queue()
    follower = attach_event_log_follower(path, q, poll_interval=0.01)
    try:
        # Create the file only after the follower is already watching.
        _append(path, _line(_ev(1, "debate_started", ticker="TSLA")))
        _append(path, _line(_ev(2, "debate_error", stage="data", message="boom")))
        assert q.get(timeout=GET_TIMEOUT).type == "debate_started"
        assert q.get(timeout=GET_TIMEOUT).type == "debate_error"  # terminal
        assert q.get(timeout=GET_TIMEOUT) is QUEUE_SENTINEL
        follower.join(timeout=JOIN_TIMEOUT)
        assert not follower.is_alive()
    finally:
        follower.stop()
        follower.join(timeout=JOIN_TIMEOUT)


def test_attach_creates_a_queue_when_none_is_given(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text(
        _line(_ev(1, "debate_started", ticker="AMD"))
        + _line(_ev(2, "debate_completed", phases_completed=[])),
        encoding="utf-8",
    )
    follower = attach_event_log_follower(path, poll_interval=0.01)
    try:
        q = follower.queue  # the queue it created for us
        assert isinstance(q, queue.Queue)
        assert q.get(timeout=GET_TIMEOUT).type == "debate_started"
        assert q.get(timeout=GET_TIMEOUT).type == "debate_completed"
        assert q.get(timeout=GET_TIMEOUT) is QUEUE_SENTINEL
    finally:
        follower.stop()
        follower.join(timeout=JOIN_TIMEOUT)


def test_follower_is_a_daemon_thread(tmp_path):
    # A watcher must never keep the process alive on exit.
    path = tmp_path / "run.jsonl"
    path.write_text("", encoding="utf-8")
    follower = EventLogFollower(path, queue.Queue(), stop_event=threading.Event())
    assert follower.daemon is True
