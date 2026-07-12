"""``TownHallApp`` — the Textual TUI that renders a TinyIC debate.

The app is a pure renderer over the frozen event stream (``docs/event-schema.md``):
it never imports engine internals, it only folds parsed
:class:`~tinyic.tui.events.Event` objects into :class:`~tinyic.tui.state.TownHallState`
and draws the result across four panes (FR-5.1):

* a **header** with company/ticker, the current phase, elapsed time, and a live
  cost/usage rollup;
* a **transcript** of chronological turn cards (persona, role/phase badge, speech)
  each with a collapsed ``▸ thinking`` row, interleaved with phase banners,
  inline steering notes (queued → delivered), and structured-artifact cards;
* a **committee sidebar** of six persona cards showing each member's model/auth
  chips and live cognitive-state badges (mood / attention / goal).

**One code path for replay and live.** Every event — whether read from a recorded
log (replay) or, in a later milestone, pulled off a ``queue.Queue`` fed by the
running engine (live) — travels through :meth:`TownHallApp._apply` →
``state.dispatch`` → :meth:`TownHallApp._sync`. Replay simply enqueues the
recorded events into ``self._pending`` and a batched timer drains them
progressively (FR-5.5: batched ~30 ms UI updates, never a single dump). A live
producer would call :meth:`TownHallApp.feed` (via ``call_from_thread``) to push
onto the same queue, and the same pump renders it.
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Static

from .events import Event, read_events
from .state import TownHallState
from .widgets import (
    ArtifactCard,
    PersonaCard,
    PhaseBanner,
    StatusHeader,
    SteeringNote,
    TurnCard,
)

__all__ = ["TownHallApp", "run_replay", "summarize_event", "format_event_line"]


# --------------------------------------------------------------------------- #
# Event -> one-line summary (kept from the skeleton: pure, tolerant, reused by
# diagnostics and covered by its own tests).
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# The app
# --------------------------------------------------------------------------- #

class TownHallApp(App):
    """Render a TinyIC debate as a live town hall (replay or, later, live feed)."""

    TITLE = "TinyIC Town Hall"
    CSS = """
    Screen { layout: vertical; }

    StatusHeader {
        height: auto;
        min-height: 2;
        padding: 0 1;
        background: $panel;
        border-bottom: heavy $accent;
    }

    #body { height: 1fr; }

    #transcript {
        width: 2fr;
        padding: 0 1;
    }
    #committee {
        width: 38;
        min-width: 28;
        padding: 0 1;
        border-left: heavy $panel-lighten-2;
    }

    #empty-note { padding: 1 2; color: $text-muted; }

    .phase-banner {
        padding: 1 0 0 0;
        color: $accent;
        text-style: bold;
    }
    .phase-banner.completed { color: $success; }

    .turn-card {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        border-left: thick $panel-lighten-2;
    }
    .turn-card.completed { border-left: thick $accent; }
    .turn-card.interrupted { border-left: thick $error; }
    .turn-head { text-style: bold; }
    .turn-speech { padding: 0 0 0 2; }
    .turn-think { padding: 0 0 0 2; }
    .think-body { color: $text-muted; text-style: italic; }

    .steering-note {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        border: round $warning;
    }
    .steering-note.delivered { border: round $success; }
    .steering-note.dropped { border: round $error; }

    .artifact-card {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        border: round $panel-lighten-2;
    }
    .artifact-card.kind-scorecard { border: round $success; }
    .artifact-card.kind-debate-error { border: round $error; }

    .persona-card {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        border: round $panel-lighten-1;
    }
    .persona-card.speaking { border: round $success; background: $boost; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
        ("T", "toggle_thinking", "Toggle thinking"),
    ]

    def __init__(
        self,
        log_path: str | Path | None = None,
        *,
        events: list[Event] | None = None,
        batch_size: int = 6,
        tick: float = 0.03,
        auto_replay: bool = True,
    ) -> None:
        super().__init__()
        self.log_path = Path(log_path) if log_path is not None else None
        if events is not None:
            self.events: list[Event] = list(events)
        elif self.log_path is not None:
            self.events = read_events(self.log_path)
        else:
            self.events = []

        # Pacing knobs (FR-5.5): drain the queue in small batches on a short
        # timer so the render stays progressive instead of dumping at once.
        self.batch_size = max(1, batch_size)
        self.tick = tick
        self.auto_replay = auto_replay

        # The single shared pipeline: events land in ``_pending`` (from replay
        # now, or a live producer via ``feed``), the pump folds them into
        # ``state`` and re-syncs the panes.
        self.state = TownHallState()
        self._pending: deque[Event] = deque(self.events)

        self._header = StatusHeader(self.state)
        self._persona_widgets: dict[str, PersonaCard] = {}
        self._item_widgets: dict[str, TurnCard | PhaseBanner | SteeringNote | ArtifactCard] = {}
        self._timer = None
        self._replay_complete: asyncio.Event | None = None

    # -- composition ------------------------------------------------------- #

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(id="body"):
            yield VerticalScroll(id="transcript")
            yield VerticalScroll(id="committee")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self._log_label()
        self._replay_complete = asyncio.Event()
        if not self.events:
            self.query_one("#transcript", VerticalScroll).mount(
                Static("(no events — empty or missing log)", id="empty-note")
            )
            self._replay_complete.set()
            return
        if self.auto_replay:
            self._timer = self.set_interval(self.tick, self._pump)

    # -- the shared render pipeline --------------------------------------- #

    def feed(self, event: Event) -> None:
        """Enqueue one event for rendering.

        Replay pre-loads the whole recorded log; a future live session would call
        this (through ``call_from_thread``) for each event the engine emits. Both
        drain through the same pump, so replay and live share one code path.
        """
        self._pending.append(event)
        if self.auto_replay and self._timer is None and self.is_running:
            self._timer = self.set_interval(self.tick, self._pump)

    def _pump(self) -> None:
        """Timer tick: fold one batch of pending events, then re-sync the panes."""
        self._apply_batch(self.batch_size)
        self._sync()
        if not self._pending:
            self._stop_replay()

    def _apply_batch(self, limit: int) -> int:
        applied = 0
        while self._pending and applied < limit:
            self.state.dispatch(self._pending.popleft())
            applied += 1
        return applied

    def replay_all_now(self) -> None:
        """Fold every pending event immediately and sync once (no pacing).

        Used by callers/tests that want the final rendered state deterministically
        without waiting on the batch timer.
        """
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._apply_batch(len(self._pending) or 0)
        self._sync()
        self._stop_replay()

    def _stop_replay(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._replay_complete is not None:
            self._replay_complete.set()

    async def wait_for_replay(self) -> None:
        """Await until every currently-pending event has been rendered."""
        if self._replay_complete is not None:
            await self._replay_complete.wait()

    def _sync(self) -> None:
        """Reflect the current ``state`` across all panes (idempotent)."""
        self._header.sync()

        committee = self.query_one("#committee", VerticalScroll)
        new_cards: list[PersonaCard] = []
        for name, pstate in self.state.personas.items():
            card = self._persona_widgets.get(name)
            if card is None:
                card = PersonaCard(pstate)
                self._persona_widgets[name] = card
                new_cards.append(card)
            else:
                card.sync()
        if new_cards:
            committee.mount(*new_cards)

        transcript = self.query_one("#transcript", VerticalScroll)
        new_items: list = []
        for item in self.state.transcript:
            widget = self._item_widgets.get(item.key)
            if widget is None:
                widget = self._make_item_widget(item)
                self._item_widgets[item.key] = widget
                new_items.append(widget)
            else:
                widget.sync()
        if new_items:
            transcript.mount(*new_items)
            transcript.scroll_end(animate=False)

    @staticmethod
    def _make_item_widget(item):
        if item.kind == "phase":
            return PhaseBanner(item)
        if item.kind == "turn":
            return TurnCard(item)
        if item.kind == "steering":
            return SteeringNote(item)
        return ArtifactCard(item)

    # -- actions ----------------------------------------------------------- #

    def action_toggle_thinking(self) -> None:
        """Expand or collapse every turn's thinking row at once (FR-5.2 preview)."""
        from textual.widgets import Collapsible

        collapsibles = list(self.query(Collapsible))
        # If any are collapsed, expand all; otherwise collapse all.
        expand = any(c.collapsed for c in collapsibles)
        for c in collapsibles:
            c.collapsed = not expand

    # -- misc -------------------------------------------------------------- #

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
