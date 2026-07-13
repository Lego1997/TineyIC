"""TinyIC Textual TUI package.

Renderers in this package consume the JSONL event stream defined in
``docs/event-schema.md`` and never import debate-engine internals. The engine
emits events; the TUI reads them. Replay is re-feeding a recorded log to the
same renderer.
"""

from .events import (
    Event,
    EventType,
    KNOWN_EVENT_TYPES,
    REQUIRED_PAYLOAD_FIELDS,
    is_replayable,
    missing_required_fields,
    read_events,
)
from .live import (
    QUEUE_SENTINEL,
    EventLogFollower,
    EventQueueSource,
    attach_event_log_follower,
)

__all__ = [
    "Event",
    "EventType",
    "KNOWN_EVENT_TYPES",
    "REQUIRED_PAYLOAD_FIELDS",
    "is_replayable",
    "missing_required_fields",
    "read_events",
    "QUEUE_SENTINEL",
    "EventLogFollower",
    "EventQueueSource",
    "attach_event_log_follower",
]
