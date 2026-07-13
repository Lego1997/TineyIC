"""Typed event models and a tolerant JSONL reader for the TinyIC event stream.

This module is the *read* side of the engine -> renderer contract frozen in
``docs/event-schema.md``. The debate engine (later milestones) writes the
append-only JSONL log; the TUI, headless renderer, and HTML exporter all consume
it through this reader. Nothing here imports engine internals.

Design rules taken straight from the schema's compatibility promises:

* Every line is one envelope object ``{v, seq, ts, debate_id, type, payload}``.
* ``seq`` is monotonically increasing per debate; renderers may resync by seq, so
  :func:`read_events` yields events sorted by ``seq``.
* Renderers must ignore unknown event *types* and unknown *payload fields* — so
  the reader never drops or rejects them: unknown types are yielded with
  ``known=False`` and the full payload is preserved verbatim.
* A crashed debate produces a truncated log (a partial final line): the reader
  skips structurally-invalid / partial lines gracefully instead of raising.

The ``REQUIRED_PAYLOAD_FIELDS`` / ``ENUM_FIELDS`` tables are a machine-readable
transcription of the schema doc's event tables. They power the schema-conformance
test and an optional :func:`missing_required_fields` validator; they are *not*
enforced by the reader itself (tolerance first).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = [
    "SCHEMA_VERSION",
    "EventType",
    "KNOWN_EVENT_TYPES",
    "REQUIRED_PAYLOAD_FIELDS",
    "ENUM_FIELDS",
    "Event",
    "parse_event",
    "iter_raw_events",
    "read_events",
    "missing_required_fields",
    "invalid_enum_fields",
    "is_replayable",
]

SCHEMA_VERSION = 1


class EventType(str, Enum):
    """The event types defined by event-schema.md v1.

    Membership is authoritative for "is this a known type"; the reader still
    tolerates and preserves types outside this set for forward compatibility.
    """

    # Lifecycle
    DEBATE_STARTED = "debate_started"
    DATA_READY = "data_ready"
    PHASE_STARTED = "phase_started"
    PHASE_COMPLETED = "phase_completed"
    DEBATE_COMPLETED = "debate_completed"
    DEBATE_ERROR = "debate_error"
    # Turns & streaming
    TURN_STARTED = "turn_started"
    THINK_DELTA = "think_delta"
    THINK_COMPLETED = "think_completed"
    TALK_DELTA = "talk_delta"
    TALK_COMPLETED = "talk_completed"
    COGNITIVE_STATE = "cognitive_state"
    TURN_COMPLETED = "turn_completed"
    TURN_INTERRUPTED = "turn_interrupted"
    # Steering
    STEERING_SUBMITTED = "steering_submitted"
    STEERING_DELIVERED = "steering_delivered"
    STEERING_DROPPED = "steering_dropped"
    # Structured artifacts & analysis
    THESIS_RECORDED = "thesis_recorded"
    VOTE_RECORDED = "vote_recorded"
    SCORECARD = "scorecard"
    MEMO_SECTION = "memo_section"
    DISAGREEMENT = "disagreement"
    COLLAPSE_METRIC = "collapse_metric"
    # Usage & cost
    USAGE = "usage"
    USAGE_WINDOW = "usage_window"


KNOWN_EVENT_TYPES: frozenset[str] = frozenset(member.value for member in EventType)

# Required payload fields per type, transcribed from event-schema.md. A field
# marked "?" in the schema (optional) is intentionally omitted here.
REQUIRED_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    EventType.DEBATE_STARTED.value: (
        "ticker",
        "company_name",
        "preset",
        "personas",
        "moderator",
        "aggregator",
        "caps",
        "config_hash",
        "tinyic_version",
    ),
    EventType.DATA_READY.value: (
        "sources",
        "financials_summary",
        "description",
        "fetched_at",
    ),
    EventType.PHASE_STARTED.value: ("phase", "index"),
    EventType.PHASE_COMPLETED.value: ("phase", "index", "turn_count"),
    EventType.DEBATE_COMPLETED.value: ("phases_completed", "duration_s", "result_ref"),
    EventType.DEBATE_ERROR.value: ("stage", "message", "recoverable"),
    EventType.TURN_STARTED.value: ("turn_id", "persona", "phase", "role"),
    EventType.THINK_DELTA.value: ("turn_id", "text"),
    EventType.THINK_COMPLETED.value: ("turn_id", "full_text"),
    EventType.TALK_DELTA.value: ("turn_id", "text"),
    EventType.TALK_COMPLETED.value: ("turn_id", "full_text"),
    EventType.COGNITIVE_STATE.value: (
        "turn_id",
        "persona",
        "goals",
        "attention",
        "emotions",
    ),
    EventType.TURN_COMPLETED.value: (
        "turn_id",
        "persona",
        "phase",
        "interrupted",
        "usage_ref",
    ),
    EventType.TURN_INTERRUPTED.value: ("turn_id", "persona", "by", "disposition"),
    EventType.STEERING_SUBMITTED.value: ("msg_id", "mode", "text", "source"),
    EventType.STEERING_DELIVERED.value: ("msg_id", "delivered_before_turn_id"),
    EventType.STEERING_DROPPED.value: ("msg_id", "reason"),
    EventType.THESIS_RECORDED.value: (
        "persona",
        "phase",
        "stance",
        "claims",
        "confidence",
    ),
    EventType.VOTE_RECORDED.value: (
        "persona",
        "vote",
        "confidence",
        "reasoning",
        "key_risks",
        "changed_mind",
        "source",
    ),
    EventType.SCORECARD.value: ("votes", "bull_count", "bear_count", "hold_count"),
    EventType.MEMO_SECTION.value: (
        "section",
        "content",
        "contributing_personas",
        "supporting_data",
    ),
    EventType.DISAGREEMENT.value: ("dimension", "description", "sides", "resolution"),
    EventType.COLLAPSE_METRIC.value: (
        "persona",
        "phase",
        "stance_before",
        "stance_after",
        "caved",
        "note",
    ),
    EventType.USAGE.value: (
        "purpose",
        "model_ref",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
    ),
    EventType.USAGE_WINDOW.value: (
        "auth_profile",
        "lane",
        "window_used_msgs",
        "window_estimate_msgs",
    ),
}

# Closed enum value sets for top-level payload fields, transcribed from the
# schema. Nested enums (e.g. data_ready.sources[].status) are validated
# separately by consumers that care.
ENUM_FIELDS: dict[str, dict[str, frozenset[str]]] = {
    EventType.PHASE_STARTED.value: {
        "phase": frozenset({"opening", "cross_exam", "rebuttal", "verdict"}),
    },
    EventType.PHASE_COMPLETED.value: {
        "phase": frozenset({"opening", "cross_exam", "rebuttal", "verdict"}),
    },
    EventType.TURN_STARTED.value: {
        "phase": frozenset({"opening", "cross_exam", "rebuttal", "verdict"}),
        "role": frozenset(
            {"statement", "challenge", "response", "rebuttal", "verdict"}
        ),
    },
    EventType.TURN_COMPLETED.value: {
        "phase": frozenset({"opening", "cross_exam", "rebuttal", "verdict"}),
    },
    EventType.TURN_INTERRUPTED.value: {
        "by": frozenset({"user", "system"}),
        "disposition": frozenset({"cancelled", "discarded_on_arrival"}),
    },
    EventType.STEERING_SUBMITTED.value: {
        "mode": frozenset({"steer", "queue"}),
        "source": frozenset({"tui", "stdin", "api"}),
    },
    EventType.THESIS_RECORDED.value: {
        "phase": frozenset({"opening"}),
        "stance": frozenset({"bullish", "bearish", "neutral"}),
    },
    EventType.VOTE_RECORDED.value: {
        "vote": frozenset({"BUY", "HOLD", "SELL"}),
        "confidence": frozenset({"HIGH", "MEDIUM", "LOW"}),
        "source": frozenset({"structured", "extracted"}),
    },
    EventType.MEMO_SECTION.value: {
        "section": frozenset(
            {
                "executive_summary",
                "investment_thesis",
                "key_risks",
                "valuation_discussion",
                "final_verdict",
            }
        ),
    },
    EventType.COLLAPSE_METRIC.value: {
        "phase": frozenset({"opening", "cross_exam", "rebuttal", "verdict"}),
    },
    EventType.USAGE.value: {
        "purpose": frozenset({"turn", "extraction", "memo", "research"}),
    },
    EventType.USAGE_WINDOW.value: {
        "lane": frozenset({"subscription"}),
    },
}

_TERMINAL_TYPES = frozenset(
    {EventType.DEBATE_COMPLETED.value, EventType.DEBATE_ERROR.value}
)


@dataclass(frozen=True)
class Event:
    """One parsed envelope from the JSONL stream.

    The envelope fields are typed; ``payload`` is left as a plain mapping so
    unknown/forward-compatible fields survive untouched. ``known`` records
    whether ``type`` is part of the v1 schema, and ``line_index`` preserves the
    original file order for stable sorting and diagnostics.
    """

    v: int | None
    seq: int | None
    ts: str
    debate_id: str
    type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    known: bool = True
    line_index: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        """Read a payload field with a default."""
        return self.payload.get(key, default)

    @property
    def turn_id(self) -> str | None:
        value = self.payload.get("turn_id")
        return value if isinstance(value, str) else None

    @property
    def persona(self) -> str | None:
        value = self.payload.get("persona")
        return value if isinstance(value, str) else None

    @property
    def phase(self) -> str | None:
        value = self.payload.get("phase")
        return value if isinstance(value, str) else None

    @property
    def is_terminal(self) -> bool:
        """True for the events that legitimately end a debate log."""
        return self.type in _TERMINAL_TYPES


def parse_event(source: str | Mapping[str, Any], *, line_index: int = 0) -> Event | None:
    """Parse a single JSONL line (or already-decoded object) into an :class:`Event`.

    Returns ``None`` for blank lines, non-JSON / truncated lines, non-object
    JSON, or objects with no usable ``type`` — the caller keeps going. This is
    what makes truncated logs (a partial final line after a crash) safe to read.
    """
    if isinstance(source, str):
        text = source.strip()
        if not text:
            return None
        try:
            obj: Any = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
    else:
        obj = source

    if not isinstance(obj, Mapping):
        return None

    event_type = obj.get("type")
    if not isinstance(event_type, str) or not event_type:
        return None

    payload = obj.get("payload")
    if not isinstance(payload, Mapping):
        payload = {}

    raw_seq = obj.get("seq")
    seq = raw_seq if isinstance(raw_seq, int) and not isinstance(raw_seq, bool) else None
    raw_v = obj.get("v")
    v = raw_v if isinstance(raw_v, int) and not isinstance(raw_v, bool) else None

    ts = obj.get("ts")
    debate_id = obj.get("debate_id")

    return Event(
        v=v,
        seq=seq,
        ts=ts if isinstance(ts, str) else "",
        debate_id=debate_id if isinstance(debate_id, str) else "",
        type=event_type,
        payload=dict(payload),
        known=event_type in KNOWN_EVENT_TYPES,
        line_index=line_index,
    )


def iter_raw_events(lines: Iterable[str]) -> Iterator[Event]:
    """Yield events in *file order*, skipping unparseable lines.

    Use this when you want to preserve exactly how the log was written (e.g. for
    diagnostics). Most renderers should use :func:`read_events`, which sorts by
    ``seq``.
    """
    for index, line in enumerate(lines):
        event = parse_event(line, line_index=index)
        if event is not None:
            yield event


def read_events(source: str | Path | Iterable[str]) -> list[Event]:
    """Read a whole log and return its events sorted by ``seq``.

    ``source`` may be a filesystem path (str/Path) or any iterable of raw lines
    (handy for tests). Events with a missing/invalid ``seq`` are kept and sorted
    after the well-formed ones, preserving their original file order. A missing
    file or empty log yields ``[]``.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.is_file():  # missing path or a directory -> empty, never raise
            return []
        with path.open("r", encoding="utf-8") as handle:
            events = list(iter_raw_events(handle))
    else:
        events = list(iter_raw_events(source))

    # Stable sort: well-formed seqs ascending; seq-less events keep file order
    # at the end. This lets renderers "resync by seq" per the schema.
    events.sort(
        key=lambda event: (
            event.seq is None,
            event.seq if event.seq is not None else event.line_index,
        )
    )
    return events


def missing_required_fields(event: Event) -> tuple[str, ...]:
    """Return required payload fields absent for a *known* event type.

    Unknown types have no declared requirements, so they always return ``()``.
    """
    required = REQUIRED_PAYLOAD_FIELDS.get(event.type)
    if required is None:
        return ()
    return tuple(name for name in required if name not in event.payload)


def invalid_enum_fields(event: Event) -> tuple[tuple[str, Any], ...]:
    """Return ``(field, value)`` pairs whose value is outside the schema enum.

    Only closed top-level enums in :data:`ENUM_FIELDS` are checked, and only when
    the field is present (presence is a separate concern handled by
    :func:`missing_required_fields`).
    """
    spec = ENUM_FIELDS.get(event.type)
    if not spec:
        return ()
    invalid: list[tuple[str, Any]] = []
    for name, allowed in spec.items():
        if name in event.payload and event.payload[name] not in allowed:
            invalid.append((name, event.payload[name]))
    return tuple(invalid)


def is_replayable(events: Iterable[Event]) -> bool:
    """A log is replayable iff it starts with ``debate_started`` and ends with a
    terminal event (``debate_completed`` or ``debate_error``). Mirrors the
    schema's replayability promise; truncated logs correctly report ``False``.
    """
    materialized = list(events)
    if not materialized:
        return False
    return (
        materialized[0].type == EventType.DEBATE_STARTED.value
        and materialized[-1].is_terminal
    )
