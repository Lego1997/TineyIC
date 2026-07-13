"""A minimal, spec-shaped Server-Sent Events line parser (FR-1.2).

All three streaming wire families TinyIC speaks are SSE:

* OpenAI Chat Completions — ``data:``-only frames terminated by ``data: [DONE]``;
* OpenAI Responses — ``event:`` + ``data:`` framed, typed by the JSON ``type``;
* Anthropic Messages — ``event:`` + ``data:`` framed, typed by the JSON ``type``.

This parser turns a raw line iterator into ``(event, data)`` records following
the WHATWG SSE dispatch rules TinyIC relies on: ``data`` fields accumulate
(newline-joined) until a blank line dispatches the event, ``event`` names the
event type (``None`` for the OpenAI-chat data-only shape), a single leading
space after the field colon is stripped, and ``:``-comment/heartbeat lines are
ignored.  It never parses JSON — malformed-frame resilience is the adapter's
job so one bad frame cannot abort a stream.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import NamedTuple


class SseEvent(NamedTuple):
    """One dispatched SSE event: an optional ``event`` name and its ``data``."""

    event: str | None
    data: str


def iter_sse_events(lines: Iterable[str]) -> Iterator[SseEvent]:
    """Yield :class:`SseEvent`s from a raw SSE line iterator.

    ``lines`` may include or omit trailing newlines (both real HTTP clients and
    recorded fixtures are accepted); CR/LF are normalized away.  A trailing
    event with no terminating blank line is still dispatched at end-of-stream.
    """
    event_type: str | None = None
    data_parts: list[str] = []
    saw_field = False

    for raw in lines:
        line = raw.rstrip("\n").rstrip("\r")
        if line == "":
            # Blank line dispatches the buffered event (if any field was seen).
            if saw_field:
                yield SseEvent(event_type, "\n".join(data_parts))
            event_type = None
            data_parts = []
            saw_field = False
            continue
        if line.startswith(":"):
            # Comment / keep-alive heartbeat — ignore.
            continue
        field, sep, value = line.partition(":")
        if not sep:
            # A field name with no colon; treat the whole line as the field,
            # empty value (SSE spec). We only care about event/data, so skip.
            field, value = line, ""
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_type = value
            saw_field = True
        elif field == "data":
            data_parts.append(value)
            saw_field = True
        # id/retry and unknown fields are ignored on purpose.

    if saw_field:
        yield SseEvent(event_type, "\n".join(data_parts))


__all__ = ["SseEvent", "iter_sse_events"]
