"""``TownHallApp`` — the Textual TUI that renders a recorded TinyIC debate.

This is the M5 skeleton. It loads a JSONL event log (via
:mod:`tinyic.tui.events`) and renders a raw chronological list of events as a
first cut. Later M5 work layers the real layout on top (transcript pane,
committee sidebar, thinking lane, composer — PRD FR-5.1..5.5), but the load and
render path stays event-only: the app never imports engine internals, it only
consumes parsed :class:`~tinyic.tui.events.Event` objects.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, RichLog

from .events import Event, read_events

__all__ = ["TownHallApp", "run_replay", "summarize_event", "format_event_line"]


def summarize_event(event: Event) -> str:
    """Produce a short, human-readable summary of an event's payload.

    Deliberately generic: it reads a few well-known payload keys and otherwise
    falls back to a compact key list, so unknown/forward-compatible event types
    still render something useful instead of crashing.
    """
    payload = event.payload
    etype = event.type

    if etype == "debate_started":
        personas = payload.get("personas") or []
        return (
            f"{payload.get('ticker', '?')} "
            f"({payload.get('company_name', '?')}) · "
            f"preset={payload.get('preset', '?')} · {len(personas)} personas"
        )
    if etype == "data_ready":
        sources = payload.get("sources") or []
        return f"{len(sources)} sources · {payload.get('financials_summary', '')}"[:80]
    if etype in {"phase_started", "phase_completed"}:
        extra = ""
        if "turn_count" in payload:
            extra = f" · {payload['turn_count']} turns"
        if payload.get("da_persona"):
            extra += f" · DA={payload['da_persona']}"
        return f"phase={payload.get('phase', '?')} idx={payload.get('index', '?')}{extra}"
    if etype == "turn_started":
        target = payload.get("target_persona")
        arrow = f" -> {target}" if target else ""
        return f"{payload.get('persona', '?')} [{payload.get('role', '?')}]{arrow}"
    if etype in {"think_delta", "talk_delta"}:
        return _truncate(payload.get("text", ""))
    if etype in {"think_completed", "talk_completed"}:
        return _truncate(payload.get("full_text", ""))
    if etype == "cognitive_state":
        return f"{payload.get('persona', '?')} · {payload.get('emotions', '')}"[:80]
    if etype == "turn_completed":
        flag = " · INTERRUPTED" if payload.get("interrupted") else ""
        return f"{payload.get('persona', '?')}{flag}"
    if etype == "turn_interrupted":
        return f"{payload.get('persona', '?')} by {payload.get('by', '?')} ({payload.get('disposition', '?')})"
    if etype == "steering_submitted":
        target = payload.get("target_persona")
        at = f" @{target}" if target else ""
        return f"[{payload.get('mode', '?')}]{at} {_truncate(payload.get('text', ''))}"
    if etype == "steering_delivered":
        return f"{payload.get('msg_id', '?')} before {payload.get('delivered_before_turn_id', '?')}"
    if etype == "steering_dropped":
        return f"{payload.get('msg_id', '?')} · {payload.get('reason', '')}"
    if etype == "thesis_recorded":
        return f"{payload.get('persona', '?')} · {payload.get('stance', '?')} ({payload.get('confidence', '?')})"
    if etype == "vote_recorded":
        return (
            f"{payload.get('persona', '?')} · {payload.get('vote', '?')} "
            f"({payload.get('confidence', '?')})"
        )
    if etype == "scorecard":
        return (
            f"consensus={payload.get('consensus', 'none')} · "
            f"bull={payload.get('bull_count', 0)} "
            f"bear={payload.get('bear_count', 0)} "
            f"hold={payload.get('hold_count', 0)}"
        )
    if etype == "memo_section":
        return f"{payload.get('section', '?')} · {_truncate(payload.get('content', ''))}"
    if etype == "disagreement":
        return f"{payload.get('dimension', '?')} · {_truncate(payload.get('description', ''))}"
    if etype == "collapse_metric":
        caved = "CAVED" if payload.get("caved") else "held"
        return f"{payload.get('persona', '?')} · {caved} · {payload.get('stance_before', '?')}->{payload.get('stance_after', '?')}"
    if etype == "usage":
        return (
            f"{payload.get('purpose', '?')} · {payload.get('model_ref', '?')} · "
            f"in={payload.get('input_tokens', 0)} out={payload.get('output_tokens', 0)}"
        )
    if etype == "usage_window":
        return (
            f"{payload.get('auth_profile', '?')} · "
            f"{payload.get('window_used_msgs', '?')}/{payload.get('window_estimate_msgs', '?')} msgs"
        )
    if etype in {"debate_completed", "debate_error"}:
        if etype == "debate_error":
            return f"{payload.get('stage', '?')} · {payload.get('message', '')}"
        return (
            f"phases={payload.get('phases_completed', [])} · "
            f"{payload.get('duration_s', '?')}s"
        )

    # Unknown / forward-compatible type: show its payload keys, never crash.
    keys = ", ".join(sorted(payload)) if payload else ""
    return f"(unknown type) {keys}"


def _truncate(text: object, limit: int = 72) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def format_event_line(event: Event) -> str:
    """Format one event as a single, aligned raw-list line."""
    seq = "----" if event.seq is None else f"{event.seq:>4}"
    marker = " " if event.known else "?"
    return f"{seq} {marker} {event.type:<18} {summarize_event(event)}"


class TownHallApp(App):
    """Render a recorded debate log as a chronological event list."""

    TITLE = "TinyIC Town Hall"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(
        self,
        log_path: str | Path | None = None,
        *,
        events: list[Event] | None = None,
    ) -> None:
        super().__init__()
        self.log_path = Path(log_path) if log_path is not None else None
        if events is not None:
            self.events: list[Event] = list(events)
        elif self.log_path is not None:
            self.events = read_events(self.log_path)
        else:
            self.events = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="event-log", highlight=False, markup=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self._log_label()
        log = self.query_one("#event-log", RichLog)
        if not self.events:
            log.write("(no events — empty or missing log)")
            return
        for event in self.events:
            log.write(format_event_line(event))

    def _log_label(self) -> str:
        if self.events:
            debate_id = self.events[0].debate_id
            if debate_id:
                return f"{debate_id} · {len(self.events)} events"
        if self.log_path is not None:
            return str(self.log_path)
        return f"{len(self.events)} events"


def run_replay(log_path: str | Path) -> None:
    """Launch the TUI to replay a recorded event log (zero LLM calls)."""
    TownHallApp(log_path).run()
