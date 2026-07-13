"""``TownHallApp`` — the Textual TUI that renders a TinyIC debate.

The app is a pure renderer over the frozen event stream (``docs/event-schema.md``):
it never imports engine internals, it only folds parsed
:class:`~tinyic.tui.events.Event` objects into :class:`~tinyic.tui.state.TownHallState`
and draws the result across four panes (FR-5.1):

* a **header** with company/ticker, the current phase, elapsed time, and a live
  cost/usage rollup;
* a **transcript** of chronological turn cards (persona, role/phase badge, speech)
  each with a collapsed ``▸ thinking`` row, interleaved with phase banners,
  inline steering notes (queued → delivered), and structured-artifact cards. The
  *current* speaker's THINK streams live in an inline, auto-expanded highlight
  block that auto-collapses to the standard ``▸ thinking`` row the moment their
  TALK begins (FR-5.1's locked decision), driven purely by folded
  ``think_delta`` / ``talk_delta`` events so replay and live behave identically;
* a **committee sidebar** of six persona cards showing each member's model/auth
  chips and live cognitive-state badges (mood / attention / goal).

**One code path for replay and live.** Every event — whether read from a recorded
log (:meth:`TownHallApp.replay`) or pulled off a ``queue.Queue`` a running debate
feeds (:meth:`TownHallApp.live`) — travels through :meth:`TownHallApp.feed` →
``state.dispatch`` → :meth:`TownHallApp._sync`. Replay pre-loads the recorded
events into ``self._pending``; live drains an :class:`~tinyic.tui.live.EventQueueSource`
onto the *same* ``_pending`` each pump tick. A batched timer then folds
``_pending`` progressively (FR-5.5: batched ~30 ms UI updates, never a single
dump). Live applies **no timing compression** — events render as they arrive —
and the header shows a live "debating…" status until a terminal event
(``debate_completed`` / ``debate_error``) flips it to complete/error. The
producer is any thread that ``put``s parsed events on the queue; the renderer
never imports or touches the engine.

**Interaction (FR-5.2/5.3/5.4).** A bottom composer bar is always visible with a
mode chip (``Steer`` / ``Queue``, toggled by ``tab``); submitting a line pushes a
steering message through a :class:`~tinyic.tui.steering.SteeringSink` (in replay,
a :class:`~tinyic.tui.steering.ReplaySink` echoes it into the transcript as a
``queued`` note — the real engine hookup lands in M1/M6). Keys: ``enter`` compose
/ send · ``tab`` mode toggle · ``esc`` hard interrupt — routed through a
:class:`~tinyic.tui.steering.ControlSink` mirror of the steering seam (replay
skips to the next turn boundary; live records an interrupt request M6 wires to
the engine) · ``t`` toggle thinking on the selected turn · ``T`` toggle
all · ``m`` cycle the persona mind view · ``space`` pause/resume auto-advance ·
``n`` next phase when paused · ``PageUp`` load older transcript cards · ``d``
cycle the registered tinyic-dark/tinyic-light theme · ``q`` quit (confirmed
while a debate is still running). Auto-advance is the default; paused mode
holds at each phase banner until ``n``.

**Virtualization (FR-5.5).** ``TownHallState`` keeps every turn, but the widget
layer mounts only the newest ``TRANSCRIPT_CAP`` transcript cards; older ones are
unmounted behind a single "… N earlier turns" placeholder that ``PageUp`` expands
``TRANSCRIPT_RELOAD`` at a time. This keeps the render bounded on long debates
without touching the batched ~30 ms pump.
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Static

from . import theme
from .events import Event, is_replayable, read_events
from .live import EventQueueSource
from .state import TownHallState, TurnState
from .steering import (
    ControlSink,
    InterruptRequest,
    RecordingControlSink,
    ReplayControlSink,
    ReplaySink,
    SteeringSink,
    parse_steering_input,
)
from .widgets import (
    ArtifactCard,
    PersonaCard,
    PhaseBanner,
    ScorecardTable,
    StatusHeader,
    SteeringNote,
    TurnCard,
    UsageTable,
)

if TYPE_CHECKING:
    import queue

__all__ = [
    "TownHallApp",
    "QuitConfirmScreen",
    "run_replay",
    "run_live",
    "summarize_event",
    "format_event_line",
    "TRANSCRIPT_CAP",
    "TRANSCRIPT_RELOAD",
]

# Transcript virtualization (FR-5.5): the widget layer mounts at most
# ``TRANSCRIPT_CAP`` of the newest transcript cards; ``PageUp`` widens that
# window by ``TRANSCRIPT_RELOAD`` older cards per press. State keeps them all.
TRANSCRIPT_CAP = 200
TRANSCRIPT_RELOAD = 100


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
# Quit confirmation (FR-5.2: `q` confirms while a debate is still running)
# --------------------------------------------------------------------------- #

class QuitConfirmScreen(ModalScreen[bool]):
    """A tiny modal asking to confirm quitting a still-running debate."""

    BINDINGS = [
        ("y", "confirm", "Quit"),
        ("n", "cancel", "Stay"),
        ("escape", "cancel", "Stay"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="quit-dialog"):
            yield Static("Quit the debate?", id="quit-title")
            yield Static(
                "A debate is still running.\n"
                "Press  y  to quit  ·  n / esc  to keep watching.",
                id="quit-body",
            )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# --------------------------------------------------------------------------- #
# The app
# --------------------------------------------------------------------------- #

class TownHallApp(App):
    """Render a TinyIC debate as a live town hall (replay or, later, live feed)."""

    TITLE = "TinyIC Town Hall"
    # Drive-mode is the default: nothing is auto-focused, so single-key controls
    # (t/T/space/n/q) reach the app instead of typing into the composer. The
    # composer Input is focused only on demand (click, or `enter` in drive mode).
    AUTO_FOCUS = None
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
    .turn-card.selected { border-left: thick $warning; background: $boost; }
    .turn-card.thinking-live { border-left: thick $warning; }
    .turn-head { text-style: bold; }
    .turn-speech { padding: 0 0 0 2; }
    .turn-think { padding: 0 0 0 2; }
    .think-body { color: $text-muted; text-style: italic; }
    /* The current speaker's live THINK: an auto-expanded, highlighted block
       (FR-5.1). Distinct from the muted, collapsed ``▸ thinking`` row it
       becomes once talk begins. */
    .turn-think-live {
        margin: 0 0 0 2;
        padding: 0 1;
        color: $warning;
        text-style: italic;
        background: $boost;
        border-left: thick $warning;
    }

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
    .artifact-card.kind-usage-rollup { border: round $accent; }
    /* Disagreement: literally stance-colored sides — bull left, bear right. */
    .artifact-card.kind-disagreement {
        border: round $warning;
        border-left: thick $success;
        border-right: thick $error;
    }
    /* Collapse check: the border takes the verdict's color. */
    .artifact-card.kind-collapse-metric.caved {
        border: round $error;
        border-left: thick $error;
    }
    .artifact-card.kind-collapse-metric.held {
        border: round $success;
        border-left: thick $success;
    }
    .table-card-summary { height: auto; }
    .table-card-table { height: auto; }

    .older-placeholder {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        text-style: italic;
        border-bottom: dashed $panel-lighten-2;
    }

    .persona-card {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        border: round $panel-lighten-1;
    }
    .persona-card.speaking { border: round $success; background: $boost; }
    .persona-card.mind-expanded { border: round $accent; background: $boost; }

    #composer {
        height: auto;
        padding: 0 1;
        border-top: heavy $panel-lighten-2;
    }
    #status-line { height: 1; color: $text-muted; }
    #composer-row { height: auto; }
    #mode-chip { width: auto; padding: 1 1 0 0; }
    #composer-input { width: 1fr; }

    QuitConfirmScreen { align: center middle; }
    #quit-dialog {
        width: 52;
        height: auto;
        padding: 1 2;
        background: $panel;
        border: thick $warning;
    }
    #quit-title { text-style: bold; }
    #quit-body { color: $text-muted; padding: 1 0 0 0; }
    """

    # Only a hard-exit escape hatch lives in BINDINGS; every Stage-3 key is
    # routed through ``on_key`` (below) so drive-mode single keys and composer
    # typing never fight over the same keystroke.
    BINDINGS = [
        ("ctrl+c", "quit", "Force quit"),
    ]

    def __init__(
        self,
        log_path: str | Path | None = None,
        *,
        events: list[Event] | None = None,
        live_queue: "queue.Queue | None" = None,
        batch_size: int = 6,
        tick: float = 0.03,
        auto_replay: bool = True,
        sink: SteeringSink | None = None,
        control: ControlSink | None = None,
    ) -> None:
        super().__init__()
        # Theme (Stage-1): register tinyic-dark/tinyic-light before first paint
        # and default to dark; `d` cycles (see ``action_toggle_theme``).
        theme.register(self)
        self.log_path = Path(log_path) if log_path is not None else None

        # Mode: **live** is fed by a thread-safe ``queue.Queue`` as events are
        # produced; **replay** pre-loads a recorded log. Both fold through the
        # identical ``feed`` → ``dispatch`` → ``_sync`` pump — the only difference
        # is where events come from and when the stream is considered exhausted.
        self.live_mode = live_queue is not None
        self._source: EventQueueSource | None = (
            EventQueueSource(live_queue) if live_queue is not None else None  # type: ignore[arg-type]
        )
        if self.live_mode:
            self.events: list[Event] = []  # nothing pre-loaded; events stream in
        elif events is not None:
            self.events = list(events)
        elif self.log_path is not None:
            self.events = read_events(self.log_path)
        else:
            self.events = []

        # Pacing knobs (FR-5.5): drain the queue in small batches on a short
        # timer so the render stays progressive instead of dumping at once.
        self.batch_size = max(1, batch_size)
        self.tick = tick
        self.auto_replay = auto_replay

        # The single shared pipeline: events land in ``_pending`` (pre-loaded
        # from replay, or polled off the live source via ``feed``), the pump folds
        # them into ``state`` and re-syncs the panes.
        self.state = TownHallState()
        self._pending: deque[Event] = deque(self.events)

        # Interaction state (FR-5.2/5.3/5.4).
        self.steer_mode = "steer"  # "steer" | "queue" (tab toggles)
        self.paused = False  # auto-advance on by default
        self._holding = False  # paused + parked at a phase banner (n to continue)
        self._selected_turn_key: str | None = None  # explicit `t`-key target
        self._mind_index: int = -1  # `m` mind-view cursor; -1 == collapsed/none
        # Steering seam: replay just echoes; M1/M6 swaps in an engine-backed sink.
        self._sink: SteeringSink = sink if sink is not None else ReplaySink(
            self._apply_local_event
        )
        # Control seam (``esc`` hard-interrupt), a mirror of the steering seam.
        # Replay skips to the next turn boundary; a live feed records the request
        # for a producer to honor later (M6 wires an engine-backed sink). An
        # injected ``control`` overrides both.
        if control is not None:
            self._control: ControlSink = control
        elif self.live_mode:
            self._control = RecordingControlSink()
        else:
            self._control = ReplayControlSink(self._skip_to_turn_boundary)

        self._header = StatusHeader(self.state)
        self._status_line = Static("", id="status-line")
        self._mode_chip = Static("", id="mode-chip")
        self._composer_input = Input(
            placeholder="Steer the committee…  (@name targets a persona)",
            id="composer-input",
        )
        self._persona_widgets: dict[str, PersonaCard] = {}
        self._item_widgets: dict[
            str,
            TurnCard | PhaseBanner | SteeringNote | ArtifactCard
            | ScorecardTable | UsageTable,
        ] = {}
        # Transcript virtualization (FR-5.5): only the newest ``_window_size``
        # transcript items stay mounted; ``PageUp`` widens the window and the
        # ``_older_placeholder`` stands in for everything scrolled out above it.
        self._window_size = TRANSCRIPT_CAP
        self._older_placeholder: Static | None = None
        self._timer = None
        self._replay_complete: asyncio.Event | None = None

    # -- constructors ------------------------------------------------------ #

    @classmethod
    def replay(cls, log_path: str | Path, **kwargs) -> "TownHallApp":
        """An app that replays a recorded event log (zero LLM calls)."""
        return cls(log_path, **kwargs)

    @classmethod
    def live(cls, event_queue: "queue.Queue", **kwargs) -> "TownHallApp":
        """An app fed live by a thread-safe queue of parsed :class:`Event`\\ s.

        ``event_queue`` is a ``queue.Queue`` any producer thread ``put``s events
        onto (e.g. :func:`~tinyic.tui.live.attach_event_log_follower` tailing a
        running debate's JSONL log). A :data:`~tinyic.tui.live.QUEUE_SENTINEL`
        (or ``None``) on the queue closes the stream.
        """
        return cls(live_queue=event_queue, **kwargs)

    # -- composition ------------------------------------------------------- #

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(id="body"):
            yield VerticalScroll(id="transcript")
            yield VerticalScroll(id="committee")
        with Vertical(id="composer"):
            yield self._status_line
            with Horizontal(id="composer-row"):
                yield self._mode_chip
                yield self._composer_input
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self._log_label()
        self._replay_complete = asyncio.Event()

        if self.live_mode:
            # Live: no pre-loaded log. The header shows a running "debating…"
            # status (set once here, like ``truncated``) until a terminal event
            # flips ``finished``. Start the pump so it polls the queue as the
            # producer fills it — even before the first event arrives.
            self.state.live = True
            # The header mounted (and synced) before this flag existed; re-sync
            # so its debating indicator — and its pulse — starts immediately.
            self._header.sync()
            self._sync_controls()
            if self.auto_replay:
                self._timer = self.set_interval(self.tick, self._pump)
            return

        # A recorded log is "truncated" (a mid-debate crash) when it never reaches
        # a terminal event. Derived purely from the parsed event stream so the
        # header can surface an explicit incomplete indicator without importing any
        # engine internals (FR-5.1; schema promise #2 — handle truncated logs).
        self.state.truncated = bool(self.events) and not is_replayable(self.events)
        self._sync_controls()
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

        The single entry point onto the shared pump queue. Replay pre-loads the
        whole recorded log; live polls its :class:`~tinyic.tui.live.EventQueueSource`
        each tick and calls this for every event the producer queued. Both drain
        through the same pump, so replay and live share one code path.
        """
        self._pending.append(event)
        if self.auto_replay and self._timer is None and self.is_running:
            self._timer = self.set_interval(self.tick, self._pump)

    def _poll_source(self) -> int:
        """Move every currently-available live event onto ``_pending`` (non-blocking).

        Live mode's only extra step: the producer thread fills the queue; each
        pump tick we drain whatever is there onto ``_pending`` through the same
        ``feed`` seam replay uses, then fold it. A no-op for replay (no source).
        Runs even while parked at a phase banner so events buffer in ``_pending``
        rather than backing up in the external queue.
        """
        if self._source is None:
            return 0
        count = 0
        for event in self._source.drain():
            self.feed(event)
            count += 1
        return count

    def _source_exhausted(self) -> bool:
        """True when no more events will ever arrive.

        Replay is finite (pre-loaded). A live source is exhausted once its stream
        closes (a :data:`~tinyic.tui.live.QUEUE_SENTINEL`) or a terminal debate
        event has been folded — so the pump keeps polling an open live queue even
        when ``_pending`` momentarily empties, instead of stopping between events.
        """
        if self._source is None:
            return True
        return self._source.closed or self.state.finished

    def _pump(self) -> None:
        """Timer tick: pull any live events, fold one batch, then re-sync the panes.

        Respects the phase gate: while parked at a phase banner (paused mode) the
        fold is a no-op until ``n`` or a resume clears the hold — but the live
        source is still drained onto ``_pending`` so nothing is lost.
        """
        self._poll_source()
        if self._holding:
            return
        self._drain_gated()
        self._sync()
        if not self._pending and not self._holding and self._source_exhausted():
            self._stop_replay()

    def _drain_gated(self) -> None:
        """Fold up to one batch, parking at a phase banner when paused (FR-5.4).

        In paused mode the pump plays out the current phase but stops right after
        opening the next one — the between-phases reflection moment. Sets
        ``_holding`` so the timer freezes there until ``n``/resume.
        """
        applied = 0
        while self._pending and applied < self.batch_size:
            event = self._pending.popleft()
            self.state.dispatch(event)
            applied += 1
            if self.paused and event.type == "phase_started":
                self._holding = True
                return

    def _apply_batch(self, limit: int) -> int:
        """Fold up to ``limit`` events ungated (used by :meth:`replay_all_now`)."""
        applied = 0
        while self._pending and applied < limit:
            self.state.dispatch(self._pending.popleft())
            applied += 1
        return applied

    def _skip_to_turn_boundary(self) -> int:
        """Fast-forward to the next turn boundary (the ``esc`` interrupt).

        Replay has no in-flight model call to cancel, so a "hard interrupt" here
        means: drain through the rest of the current turn (up to and including its
        ``turn_completed``) and stop, leaving the next speaker parked. Clears any
        phase hold so the interrupt always makes visible progress.
        """
        self._holding = False
        applied = 0
        while self._pending:
            event = self._pending.popleft()
            self.state.dispatch(event)
            applied += 1
            if event.type == "turn_completed":
                break
        if not self._pending:
            self._stop_replay()
        return applied

    def replay_all_now(self) -> None:
        """Fold every currently-available event immediately and sync once (no pacing).

        Used by callers/tests that want the rendered state deterministically
        without waiting on the batch timer. It first drains any live source, so it
        serves both replay and a manually-fed live queue: it stops the debate only
        when the source is actually exhausted (all recorded events, a live
        sentinel, or a terminal event), so a mid-debate live fold stays open.
        """
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._holding = False
        self._poll_source()
        self._apply_batch(len(self._pending) or 0)
        self._sync()
        if self._source_exhausted():
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
        self._sync_committee()
        self._sync_transcript()
        self._refresh_selection()
        self._sync_controls()

    def _sync_committee(self) -> None:
        """Mount/refresh the persona cards and reflect the mind-view expansion."""
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
        self._apply_mind_state()

    def _sync_transcript(self) -> None:
        """Mount the newest window of transcript cards, virtualizing the rest.

        Only items in ``[window_start, total)`` stay mounted, where
        ``window_start`` keeps the live widget count at ``_window_size`` (default
        ``TRANSCRIPT_CAP``); anything above is unmounted behind a placeholder.
        The mounted set is always a contiguous range, so the visible items that
        still need a widget form either a *head* block (older cards revealed by
        ``PageUp``, inserted above the current top) or a *tail* block (fresh
        stream items, appended) — never a gap in the middle.
        """
        transcript = self.query_one("#transcript", VerticalScroll)
        items = self.state.transcript
        total = len(items)
        window_start = max(0, total - self._window_size)

        # Unmount whatever scrolled out of the window; refresh the survivors.
        visible_keys = {items[i].key for i in range(window_start, total)}
        for key in list(self._item_widgets):
            widget = self._item_widgets[key]
            if key not in visible_keys:
                del self._item_widgets[key]
                widget.remove()
            else:
                widget.sync()

        # The first still-mounted survivor anchors head inserts. It settled in a
        # prior sync, so ``mount(before=...)`` is safe; None means a fresh pane
        # where everything simply appends as a tail block.
        top_widget = None
        for i in range(window_start, total):
            top_widget = self._item_widgets.get(items[i].key)
            if top_widget is not None:
                break

        self._sync_older_placeholder(transcript, items, window_start, top_widget)

        head_new: list = []
        tail_new: list = []
        seen_mounted = False
        for i in range(window_start, total):
            item = items[i]
            if item.key in self._item_widgets:
                seen_mounted = True
                continue
            widget = self._make_item_widget(item)
            self._item_widgets[item.key] = widget
            if seen_mounted or top_widget is None:
                tail_new.append(widget)
            else:
                head_new.append(widget)

        if head_new:
            transcript.mount(*head_new, before=top_widget)
        if tail_new:
            transcript.mount(*tail_new)
            transcript.scroll_end(animate=False)

    def _sync_older_placeholder(self, transcript, items, window_start, top_widget) -> None:
        """Keep a single '… N earlier turns' card at the top when cards are hidden."""
        if window_start <= 0:
            if self._older_placeholder is not None:
                self._older_placeholder.remove()
                self._older_placeholder = None
            return
        hidden_turns = sum(
            1 for i in range(window_start) if isinstance(items[i], TurnState)
        )
        label = f"… {hidden_turns} earlier turns (press PageUp to load)"
        if self._older_placeholder is None:
            self._older_placeholder = Static(
                label, id="older-placeholder", classes="older-placeholder"
            )
            if top_widget is not None:
                transcript.mount(self._older_placeholder, before=top_widget)
            else:
                transcript.mount(self._older_placeholder)
        else:
            self._older_placeholder.update(label)

    @staticmethod
    def _make_item_widget(item):
        if item.kind == "phase":
            return PhaseBanner(item)
        if item.kind == "turn":
            return TurnCard(item)
        if item.kind == "steering":
            return SteeringNote(item)
        # Tabular artifacts render as real DataTable cards (Stage-2); a
        # scorecard with no derivable rows degrades to the generic card.
        if item.kind == "scorecard" and item.rows:
            return ScorecardTable(item)
        if item.kind == "usage_rollup" and item.rows:
            return UsageTable(item)
        return ArtifactCard(item)

    # -- key routing (FR-5.2) --------------------------------------------- #

    @property
    def _is_composing(self) -> bool:
        """True while the composer input holds focus (typing a steer)."""
        return self.focused is self._composer_input

    def on_key(self, event: events.Key) -> None:
        """Route keys by mode. Composer typing wins; drive keys act otherwise.

        ``tab``/``esc`` act in both modes (mode toggle / hard interrupt). While
        the composer is focused, every other key is left to the ``Input`` so text
        (including ``t``, ``n``, ``q``, space) types normally; otherwise the bare
        drive keys trigger their actions. A modal (quit confirm) suppresses all of
        it — its own bindings handle input.
        """
        if len(self.screen_stack) > 1:
            return  # a modal is up; let it own the keyboard

        if event.key == "tab":
            self.action_toggle_mode()
            event.stop()
            event.prevent_default()
            return
        if event.key == "escape":
            self.action_interrupt()
            event.stop()
            event.prevent_default()
            return

        if self._is_composing:
            return  # typing — Input handles it (enter -> on_input_submitted)

        char = event.character
        if char == "t":
            self.action_toggle_thinking_selected()
        elif char == "T":
            self.action_toggle_thinking()
        elif char == "m":
            self.action_cycle_mind()
        elif char == "d":
            self.action_toggle_theme()
        elif event.key == "space":
            self.action_toggle_pause()
        elif event.key == "n":
            self.action_next_phase()
        elif event.key == "pageup":
            self.action_load_older()
        elif event.key == "q":
            self.action_request_quit()
        elif event.key == "enter":
            self._focus_composer()
        else:
            return
        event.stop()
        event.prevent_default()

    # -- thinking toggles (FR-5.2: t / T) --------------------------------- #

    def action_toggle_thinking(self) -> None:
        """Expand or collapse every turn's thinking row at once (``T``)."""
        from textual.widgets import Collapsible

        collapsibles = list(self.query(Collapsible))
        # If any are collapsed, expand all; otherwise collapse all.
        expand = any(c.collapsed for c in collapsibles)
        for c in collapsibles:
            c.collapsed = not expand

    def action_toggle_thinking_selected(self) -> None:
        """Toggle the thinking row of the selected (or latest) turn (``t``)."""
        key = self._effective_selected_key()
        if key is None:
            return
        widget = self._item_widgets.get(key)
        if isinstance(widget, TurnCard):
            widget.toggle_thinking()

    # -- persona mind view (FR-5.2: m) ------------------------------------ #

    def action_cycle_mind(self) -> None:
        """Cycle the persona 'mind' view (``m``).

        Each press focuses the next committee card and expands its inline mind
        detail (the latest cognitive state is already on the card; the expansion
        adds the persona's latest private-reasoning snippet). Cycling past the
        last persona collapses the view again.
        """
        names = list(self.state.personas)
        if not names:
            return
        self._mind_index += 1
        if self._mind_index >= len(names):
            self._mind_index = -1  # cycled past the last -> collapse
        focused = self._apply_mind_state()
        if focused is not None:
            card = self._persona_widgets.get(focused)
            if card is not None and card.is_mounted:
                card.scroll_visible()

    def _apply_mind_state(self) -> str | None:
        """Expand exactly the mind-focused persona card; collapse every other."""
        names = list(self.state.personas)
        focused = (
            names[self._mind_index] if 0 <= self._mind_index < len(names) else None
        )
        for name, card in self._persona_widgets.items():
            should = name == focused
            if card.expanded != should:
                card.expanded = should
                card.sync()
        return focused

    # -- transcript virtualization (FR-5.5: PageUp) ----------------------- #

    def action_load_older(self) -> None:
        """Reveal ~``TRANSCRIPT_RELOAD`` older transcript cards (``PageUp``)."""
        if self._window_size >= len(self.state.transcript):
            return  # everything is already mounted
        self._window_size += TRANSCRIPT_RELOAD
        self._sync()

    # -- turn selection (the `t`-key target) ------------------------------ #

    def select_turn(self, key: str) -> None:
        """Mark a transcript turn as selected (from a click or programmatically)."""
        self._selected_turn_key = key
        self._refresh_selection()

    def on_turn_card_selected(self, message: TurnCard.Selected) -> None:
        self.select_turn(message.key)

    def _effective_selected_key(self) -> str | None:
        """The explicit selection if it still exists, else the latest turn."""
        if self._selected_turn_key and self._selected_turn_key in self._item_widgets:
            return self._selected_turn_key
        for item in reversed(self.state.transcript):
            if isinstance(item, TurnState):
                return item.key
        return None

    def _refresh_selection(self) -> None:
        effective = self._effective_selected_key()
        for key, widget in self._item_widgets.items():
            if isinstance(widget, TurnCard):
                should = key == effective
                if widget.selected != should:
                    widget.selected = should
                    widget.sync()

    # -- steering / composer (FR-5.3) ------------------------------------- #

    def action_toggle_mode(self) -> None:
        """Flip the composer between Steer and Queue delivery (``tab``)."""
        self.steer_mode = "queue" if self.steer_mode == "steer" else "steer"
        self._sync_controls()

    def _focus_composer(self) -> None:
        self.set_focus(self._composer_input)

    def _blur_composer(self) -> None:
        self.set_focus(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the composer: parse, hand to the sink, clear, back to drive."""
        if event.input is not self._composer_input:
            return
        raw = event.value
        self._composer_input.value = ""
        message = parse_steering_input(
            raw, mode=self.steer_mode, known_personas=list(self.state.personas)
        )
        if message is not None:
            self._sink.submit(message)
        self._blur_composer()
        event.stop()

    def _apply_local_event(self, event: Event) -> None:
        """Fold a locally-echoed event (from :class:`ReplaySink`) and redraw now.

        Applied immediately rather than enqueued so the steer appears even while
        the pump is parked at a phase banner.
        """
        self.state.dispatch(event)
        if self.is_running:
            self._sync()

    # -- interrupt (FR-5.2/5.3: esc) -------------------------------------- #

    def action_interrupt(self) -> None:
        """Hard interrupt (``esc``): routed through the :class:`ControlSink`.

        In replay the sink skips to the next turn boundary (no in-flight call to
        cancel); in live mode it records an interrupt request a producer may
        honor later (M6 cancels the current turn engine-side and emits the
        authoritative ``turn_interrupted``). Either way we re-sync so any change
        (a skipped turn, a later interrupt badge) is reflected.
        """
        self._control.interrupt(
            InterruptRequest(turn_id=self.state.current_turn_id, source="tui")
        )
        if self.is_running:
            self._sync()

    # -- theme (Stage-1: d cycles tinyic-dark / tinyic-light) -------------- #

    def action_toggle_theme(self) -> None:
        """Cycle between the registered tinyic-dark/tinyic-light themes (``d``).

        CSS ``$tokens`` re-resolve automatically; the explicit ``_sync`` re-runs
        every mounted widget's ``sync`` so Rich content built through the
        :mod:`~tinyic.tui.theme` style seam (pills, persona hues, muted labels)
        is rebuilt for the new variant too.
        """
        theme.toggle(self)
        if self.is_running:
            self._sync()

    # -- phase flow (FR-5.4: space / n) ----------------------------------- #

    def action_toggle_pause(self) -> None:
        """Toggle auto-advance (``space``). Resuming clears any phase hold."""
        self.paused = not self.paused
        if not self.paused:
            self._holding = False
        self._sync_controls()

    def action_next_phase(self) -> None:
        """Release a phase-boundary hold to play the next phase (``n``)."""
        if self.paused and self._holding:
            self._holding = False
        self._sync_controls()

    # -- quit (FR-5.2: q, confirmed while running) ------------------------ #

    def action_request_quit(self) -> None:
        if self._quit_needs_confirm():
            self.push_screen(QuitConfirmScreen(), self._on_quit_confirm)
        else:
            self.exit()

    def _on_quit_confirm(self, confirmed: bool | None) -> None:
        if confirmed:
            self.exit()

    def _quit_needs_confirm(self) -> bool:
        return self._replay_running

    @property
    def _replay_running(self) -> bool:
        """True while there is still a debate to play out (not yet complete)."""
        done = self._replay_complete
        return done is not None and not done.is_set()

    # -- controls rendering ----------------------------------------------- #

    def _sync_controls(self) -> None:
        """Redraw the composer mode chip and the status/hint line.

        A no-op until the composer widgets are mounted, so actions invoked before
        the first render (or in unit tests) never touch an unmounted widget.
        """
        if not self._status_line.is_mounted:
            return
        dark = theme.is_dark(self)
        mode = self.steer_mode.upper()
        chip = Text()
        chip.append(f" {mode} ", style=theme.pill(self.steer_mode, dark=dark))
        self._mode_chip.update(chip)

        dim = theme.muted(dark=dark)
        status = Text()
        if self._holding:
            status.append("⏸ holding at phase boundary", style="bold yellow")
            status.append("  ·  n next phase", style=dim)
        elif self.paused:
            status.append("⏸ paused", style="bold yellow")
            status.append("  ·  n next phase", style=dim)
        else:
            status.append("▶ auto-advance", style="bold green")
        status.append(
            "     enter compose · tab mode · t/T think · m mind · space pause"
            " · n next · PgUp older · d theme · esc interrupt · q quit",
            style=dim,
        )
        self._status_line.update(status)

    # -- misc -------------------------------------------------------------- #

    def _log_label(self) -> str:
        if self.live_mode:
            return "live"
        if self.events:
            debate_id = self.events[0].debate_id
            if debate_id:
                return f"{debate_id} · {len(self.events)} events"
        if self.log_path is not None:
            return str(self.log_path)
        return f"{len(self.events)} events"


def run_replay(log_path: str | Path) -> None:
    """Launch the TUI to replay a recorded event log (zero LLM calls)."""
    TownHallApp.replay(log_path).run()


def run_live(event_queue: "queue.Queue") -> None:
    """Launch the TUI to render a live debate fed off ``event_queue``.

    The producer (e.g. :func:`~tinyic.tui.live.attach_event_log_follower`) fills
    the queue on another thread; the app drains and renders it. Blocks until the
    stream closes and the user quits.
    """
    TownHallApp.live(event_queue).run()
