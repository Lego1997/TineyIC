"""Pure, framework-free view state for the Town Hall renderer.

``TownHallState`` folds the :class:`~tinyic.tui.events.Event` stream into the
data every renderer needs: header rollups, an ordered transcript of
turns / phase banners / steering notes / artifacts, and a live per-persona
committee snapshot (cognitive state, votes, stance).

The single entry point is :meth:`TownHallState.dispatch`. **This is the code
path the TUI, the (future) live event queue, and any test all share** — replay
is nothing more than calling ``dispatch`` for each recorded event in order, and
a live debate is calling ``dispatch`` for each event pulled off a
``queue.Queue``. Nothing in this module imports Textual, ``rich``, or any engine
internals, so it stays trivially unit-testable and cheap to fold thousands of
events through.

Everything is tolerant by design (matching the reader in :mod:`tinyic.tui.events`
and the schema's compatibility promises): payload fields are read with defaults,
unknown event types are counted but otherwise ignored, and partial/mutating
turns (deltas arriving before completion) update in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .events import Event

__all__ = [
    "PHASE_ORDER",
    "PersonaState",
    "TurnState",
    "PhaseState",
    "SteeringState",
    "ArtifactState",
    "TranscriptItem",
    "TownHallState",
    "humanize_count",
    "humanize_duration",
]

PHASE_ORDER: tuple[str, ...] = ("opening", "cross_exam", "rebuttal", "verdict")

# Artifact event types the transcript surfaces as standalone cards (in arrival
# order), keyed by type -> human title. Everything else is folded into the
# header, the committee, or an existing turn.
_ARTIFACT_TITLES: dict[str, str] = {
    "data_ready": "Data package",
    "scorecard": "Scorecard",
    "disagreement": "Disagreement",
    "debate_error": "Error",
}


# --------------------------------------------------------------------------- #
# Small formatting helpers (pure; shared by renderers)
# --------------------------------------------------------------------------- #

def humanize_count(n: int) -> str:
    """Compact token/count formatting: ``950`` -> ``950``, ``12400`` -> ``12.4k``."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M")


def humanize_duration(seconds: float | None) -> str:
    """``93.4`` -> ``1:33``; ``8`` -> ``0:08``; ``None`` -> ``0:00``."""
    if not seconds or seconds < 0:
        seconds = 0
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _parse_ts(ts: str) -> float | None:
    """Parse an ISO-8601 ``...Z`` timestamp to epoch seconds, or ``None``."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Data classes for the view model
# --------------------------------------------------------------------------- #

@dataclass
class PersonaState:
    """A committee member's static binding plus its live cognitive snapshot."""

    name: str
    model_ref: str = ""
    auth_profile: str = ""
    thinking_level: str = ""
    temperament: str = ""
    # Live, updated from cognitive_state / thesis / vote / collapse events:
    mood: str = ""
    attention: str = ""
    goal: str = ""
    # Latest completed private-reasoning snippet, surfaced by the `m` mind view.
    think: str = ""
    stance: str = ""
    vote: str = ""
    confidence: str = ""
    caved: bool | None = None
    speaking: bool = False


@dataclass
class TurnState:
    """One speaker turn; mutates in place as deltas / completion arrive."""

    key: str
    turn_id: str
    persona: str
    phase: str
    role: str
    target_persona: str | None = None
    speech: str = ""
    thinking: str = ""
    # The speaker's stance for this turn: bullish/bearish/neutral (from the
    # opening thesis) or BUY/HOLD/SELL (from the verdict vote). Backfilled and
    # carried forward per persona by ``TownHallState`` (FR-5.1 stance badge).
    stance: str = ""
    interrupted: bool = False
    completed: bool = False
    # Live-think tracking (FR-5.1 locked decision). ``thinking_streaming`` flips
    # True the moment a *think_delta* arrives (never on think_completed), so a
    # completed-only log keeps the old collapsed-row behavior. ``talk_started``
    # flips True on the first talk_delta *or* talk_completed — the instant the
    # live thinking block must auto-collapse. See :attr:`thinking_live`.
    thinking_streaming: bool = False
    talk_started: bool = False
    # True once ``talk_completed`` delivered the authoritative full speech — the
    # renderer's cue to swap the incremental plain-text stream for the rendered
    # (markdown) form. Never set by deltas, so live streaming stays cheap.
    speech_final: bool = False
    # Interrupt provenance (FR-5.3), captured from ``turn_interrupted`` so the
    # card can render the badge as the esc affordance's result (by == "user")
    # versus a system/provider interruption.
    interrupted_by: str = ""
    interrupt_disposition: str = ""
    kind: str = "turn"

    @property
    def thinking_live(self) -> bool:
        """True while this turn's THINK is streaming and should render as an
        auto-expanded, highlighted live block (FR-5.1's locked decision).

        The block shows exactly when a ``think_delta`` has arrived
        (``thinking_streaming``) and the speaker has **not** begun talking, and
        the turn is neither completed nor interrupted. The moment the first
        ``talk_delta`` / ``talk_completed`` lands (``talk_started``) it flips
        False and the card auto-collapses to the standard ``▸ thinking`` row.

        A completed-only log (``think_completed`` with no deltas) never sets
        ``thinking_streaming``, so ``thinking_live`` stays False and that log
        keeps its current, non-live rendering.
        """
        return (
            self.thinking_streaming
            and not self.talk_started
            and not self.completed
            and not self.interrupted
        )


@dataclass
class PhaseState:
    """A phase banner; gains its turn_count / completed flag at phase end."""

    key: str
    phase: str
    index: int
    da_persona: str | None = None
    turn_count: int | None = None
    completed: bool = False
    kind: str = "phase"


@dataclass
class SteeringState:
    """A moderator/user steering message; flips queued -> delivered|dropped."""

    key: str
    msg_id: str
    mode: str
    text: str
    target_persona: str | None = None
    source: str = ""
    status: str = "queued"  # queued | delivered | dropped
    delivered_before_turn_id: str | None = None
    reason: str = ""
    kind: str = "steering"


@dataclass
class ArtifactState:
    """A standalone transcript card for a structured artifact (memo, scorecard…)."""

    key: str
    kind: str
    title: str
    body: str
    payload: Mapping[str, Any] = field(default_factory=dict)


TranscriptItem = TurnState | PhaseState | SteeringState | ArtifactState


# --------------------------------------------------------------------------- #
# The fold
# --------------------------------------------------------------------------- #

class TownHallState:
    """Accumulated view of a debate, built by folding events one at a time."""

    def __init__(self) -> None:
        # Header / lifecycle
        self.ticker: str = ""
        self.company_name: str = ""
        self.preset: str = ""
        self.tinyic_version: str = ""
        self.config_hash: str = ""
        self.caps: Mapping[str, Any] = {}
        self.moderator: Mapping[str, Any] = {}
        self.aggregator: Mapping[str, Any] = {}
        self.current_phase: str = ""
        self.current_phase_index: int | None = None
        self.phases_completed: list[str] = []
        self.finished: bool = False
        self.errored: bool = False
        # Log-level flag: the recorded stream ended without a terminal event (a
        # mid-debate crash). Not folded per-event — the app sets it once from the
        # whole parsed log (see ``TownHallApp.on_mount``) so the header can show
        # an explicit "incomplete" indicator for a truncated replay.
        self.truncated: bool = False
        # Mode flag: this view is fed by a live event queue rather than a recorded
        # log. Like ``truncated`` it is a renderer-level fact the app sets once
        # (not folded per-event), so the header can show a live "debating…" status
        # until ``finished`` flips it to complete/error.
        self.live: bool = False
        self.duration_s: float | None = None

        # Data package
        self.financials_summary: str = ""
        self.description: str = ""
        self.data_sources: list[Mapping[str, Any]] = []

        # Committee (ordered by first appearance / debate_started order)
        self.personas: dict[str, PersonaState] = {}

        # Transcript (ordered by arrival) + fast lookups for in-place mutation
        self.transcript: list[TranscriptItem] = []
        self._turns_by_id: dict[str, TurnState] = {}
        self._steering_by_id: dict[str, SteeringState] = {}
        self._phases_by_index: dict[int, PhaseState] = {}
        self.current_turn_id: str | None = None
        # Per-persona stance carry-forward + the most recent turn to backfill,
        # so a stance recorded *after* a turn still stamps that turn and every
        # subsequent one until it changes (FR-5.1 stance badge).
        self._latest_stance: dict[str, str] = {}
        self._last_turn_by_persona: dict[str, TurnState] = {}

        # Usage / cost rollups
        self.cost_usd: float = 0.0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cached_tokens: int = 0
        self.usage_calls: int = 0
        self.subscription_calls: int = 0
        self.usage_window: Mapping[str, Any] | None = None

        # Structured results
        self.scorecard: Mapping[str, Any] | None = None

        # Bookkeeping
        self.applied_count: int = 0
        self._first_ts: float | None = None
        self._last_ts: float | None = None

    # -- public API -------------------------------------------------------- #

    def dispatch(self, event: Event) -> None:
        """Fold one event into the view. Unknown types are counted, not applied."""
        self.applied_count += 1
        self._track_time(event.ts)
        handler = getattr(self, f"_on_{event.type}", None)
        if handler is not None:
            handler(event.payload)

    @property
    def elapsed_s(self) -> float:
        """Seconds represented so far — the recorded duration once finished,
        otherwise the span between the first and latest applied timestamps."""
        if self.duration_s is not None:
            return self.duration_s
        if self._first_ts is None or self._last_ts is None:
            return 0.0
        return max(0.0, self._last_ts - self._first_ts)

    @property
    def phase_position(self) -> str:
        """``cross_exam (2/4)`` style indicator for the header."""
        if not self.current_phase:
            return "—"
        try:
            pos = PHASE_ORDER.index(self.current_phase) + 1
        except ValueError:
            return self.current_phase
        return f"{self.current_phase} ({pos}/{len(PHASE_ORDER)})"

    # -- lifecycle handlers ------------------------------------------------ #

    def _on_debate_started(self, p: Mapping[str, Any]) -> None:
        self.ticker = str(p.get("ticker", "") or "")
        self.company_name = str(p.get("company_name", "") or "")
        self.preset = str(p.get("preset", "") or "")
        self.tinyic_version = str(p.get("tinyic_version", "") or "")
        self.config_hash = str(p.get("config_hash", "") or "")
        self.caps = p.get("caps") or {}
        self.moderator = p.get("moderator") or {}
        self.aggregator = p.get("aggregator") or {}
        for entry in p.get("personas") or []:
            if not isinstance(entry, Mapping):
                continue
            name = str(entry.get("name", "") or "")
            if not name:
                continue
            self.personas[name] = PersonaState(
                name=name,
                model_ref=str(entry.get("model_ref", "") or ""),
                auth_profile=str(entry.get("auth_profile", "") or ""),
                thinking_level=str(entry.get("thinking_level", "") or ""),
                temperament=str(entry.get("temperament", "") or ""),
            )

    def _on_data_ready(self, p: Mapping[str, Any]) -> None:
        self.financials_summary = str(p.get("financials_summary", "") or "")
        self.description = str(p.get("description", "") or "")
        sources = p.get("sources") or []
        self.data_sources = [s for s in sources if isinstance(s, Mapping)]
        ok = sum(1 for s in self.data_sources if s.get("status") == "ok")
        body_lines = [self.financials_summary] if self.financials_summary else []
        for s in self.data_sources:
            status = str(s.get("status", "?"))
            line = f"{s.get('name', '?')}: {status}"
            if s.get("warning"):
                line += f" — {s['warning']}"
            body_lines.append(line)
        self._append_artifact(
            key="data_ready",
            kind="data_ready",
            title=f"Data package · {ok}/{len(self.data_sources)} sources ok",
            body="\n".join(body_lines),
            payload=p,
        )

    def _on_phase_started(self, p: Mapping[str, Any]) -> None:
        phase = str(p.get("phase", "") or "")
        index = _as_int(p.get("index"), default=len(self._phases_by_index))
        self.current_phase = phase
        self.current_phase_index = index
        state = PhaseState(
            key=f"phase-{index}-{phase}",
            phase=phase,
            index=index,
            da_persona=p.get("da_persona"),
        )
        self._phases_by_index[index] = state
        self.transcript.append(state)

    def _on_phase_completed(self, p: Mapping[str, Any]) -> None:
        index = _as_int(p.get("index"), default=-1)
        state = self._phases_by_index.get(index)
        if state is not None:
            state.turn_count = _as_int(p.get("turn_count"), default=None)
            state.completed = True
        phase = str(p.get("phase", "") or "")
        if phase and phase not in self.phases_completed:
            self.phases_completed.append(phase)

    def _on_debate_completed(self, p: Mapping[str, Any]) -> None:
        self.finished = True
        completed = p.get("phases_completed")
        if isinstance(completed, list):
            self.phases_completed = [str(x) for x in completed]
        dur = p.get("duration_s")
        if isinstance(dur, (int, float)):
            self.duration_s = float(dur)

    def _on_debate_error(self, p: Mapping[str, Any]) -> None:
        self.finished = True
        self.errored = True
        self._append_artifact(
            key=f"error-{self.applied_count}",
            kind="debate_error",
            title=f"Error · {p.get('stage', '?')}",
            body=str(p.get("message", "") or ""),
            payload=p,
        )

    # -- turn handlers ----------------------------------------------------- #

    def _on_turn_started(self, p: Mapping[str, Any]) -> None:
        turn_id = str(p.get("turn_id", "") or "")
        if not turn_id:
            return
        persona = str(p.get("persona", "") or "")
        turn = TurnState(
            key=f"turn-{turn_id}",
            turn_id=turn_id,
            persona=persona,
            phase=str(p.get("phase", "") or ""),
            role=str(p.get("role", "") or ""),
            target_persona=p.get("target_persona"),
            stance=self._latest_stance.get(persona, ""),
        )
        self._turns_by_id[turn_id] = turn
        self._last_turn_by_persona[persona] = turn
        self.transcript.append(turn)
        self.current_turn_id = turn_id
        # Highlight the active speaker in the committee panel.
        for name, member in self.personas.items():
            member.speaking = name == persona

    def _on_think_delta(self, p: Mapping[str, Any]) -> None:
        turn = self._turns_by_id.get(str(p.get("turn_id", "")))
        if turn is not None:
            # A delta (not a one-shot completion) is what makes the think stream
            # "live" and drives the auto-expanded highlight block (FR-5.1).
            turn.thinking_streaming = True
            turn.thinking += str(p.get("text", "") or "")
            self._sync_think_snippet(turn)

    def _on_think_completed(self, p: Mapping[str, Any]) -> None:
        turn = self._turns_by_id.get(str(p.get("turn_id", "")))
        if turn is not None:
            turn.thinking = str(p.get("full_text", "") or "")
            self._sync_think_snippet(turn)

    def _on_talk_delta(self, p: Mapping[str, Any]) -> None:
        turn = self._turns_by_id.get(str(p.get("turn_id", "")))
        if turn is not None:
            # The first talk fragment collapses the live thinking block.
            turn.talk_started = True
            turn.speech += str(p.get("text", "") or "")

    def _on_talk_completed(self, p: Mapping[str, Any]) -> None:
        turn = self._turns_by_id.get(str(p.get("turn_id", "")))
        if turn is not None:
            # A one-shot talk_completed (no talk_delta) still collapses the block.
            turn.talk_started = True
            turn.speech = str(p.get("full_text", "") or "")
            turn.speech_final = True

    def _on_cognitive_state(self, p: Mapping[str, Any]) -> None:
        member = self.personas.get(str(p.get("persona", "")))
        if member is None:
            return
        member.mood = str(p.get("emotions", "") or "")
        member.attention = str(p.get("attention", "") or "")
        goals = p.get("goals")
        if isinstance(goals, list) and goals:
            member.goal = str(goals[0])
        elif isinstance(goals, str):
            member.goal = goals

    def _on_turn_completed(self, p: Mapping[str, Any]) -> None:
        turn = self._turns_by_id.get(str(p.get("turn_id", "")))
        if turn is not None:
            turn.completed = True
            # Never *un*-set an interrupt a prior ``turn_interrupted`` recorded.
            turn.interrupted = turn.interrupted or bool(p.get("interrupted"))
        member = self.personas.get(str(p.get("persona", "")))
        if member is not None:
            member.speaking = False

    def _on_turn_interrupted(self, p: Mapping[str, Any]) -> None:
        turn = self._turns_by_id.get(str(p.get("turn_id", "")))
        if turn is not None:
            turn.interrupted = True
            turn.interrupted_by = str(p.get("by", "") or "")
            turn.interrupt_disposition = str(p.get("disposition", "") or "")

    # -- steering handlers ------------------------------------------------- #

    def _on_steering_submitted(self, p: Mapping[str, Any]) -> None:
        msg_id = str(p.get("msg_id", "") or "")
        state = SteeringState(
            key=f"steer-{msg_id or self.applied_count}",
            msg_id=msg_id,
            mode=str(p.get("mode", "") or ""),
            text=str(p.get("text", "") or ""),
            target_persona=p.get("target_persona"),
            source=str(p.get("source", "") or ""),
        )
        if msg_id:
            self._steering_by_id[msg_id] = state
        self.transcript.append(state)

    def _on_steering_delivered(self, p: Mapping[str, Any]) -> None:
        state = self._steering_by_id.get(str(p.get("msg_id", "")))
        if state is not None:
            state.status = "delivered"
            state.delivered_before_turn_id = p.get("delivered_before_turn_id")

    def _on_steering_dropped(self, p: Mapping[str, Any]) -> None:
        state = self._steering_by_id.get(str(p.get("msg_id", "")))
        if state is not None:
            state.status = "dropped"
            state.reason = str(p.get("reason", "") or "")

    # -- structured artifacts / committee updates -------------------------- #

    def _on_thesis_recorded(self, p: Mapping[str, Any]) -> None:
        persona = str(p.get("persona", "") or "")
        stance = str(p.get("stance", "") or "")
        member = self.personas.get(persona)
        if member is not None:
            member.stance = stance
        self._stamp_stance(persona, stance)

    def _on_vote_recorded(self, p: Mapping[str, Any]) -> None:
        persona = str(p.get("persona", "") or "")
        vote = str(p.get("vote", "") or "")
        member = self.personas.get(persona)
        if member is not None:
            member.vote = vote
            member.confidence = str(p.get("confidence", "") or "")
        self._stamp_stance(persona, vote)

    def _on_collapse_metric(self, p: Mapping[str, Any]) -> None:
        member = self.personas.get(str(p.get("persona", "")))
        if member is not None:
            member.caved = bool(p.get("caved"))
            after = str(p.get("stance_after", "") or "")
            if after:
                member.stance = after

    def _on_scorecard(self, p: Mapping[str, Any]) -> None:
        self.scorecard = p
        consensus = p.get("consensus") or "none"
        self._append_artifact(
            key="scorecard",
            kind="scorecard",
            title=f"Scorecard · consensus {consensus}",
            body=(
                f"BUY {p.get('bull_count', 0)} · "
                f"HOLD {p.get('hold_count', 0)} · "
                f"SELL {p.get('bear_count', 0)}"
            ),
            payload=p,
        )

    def _on_memo_section(self, p: Mapping[str, Any]) -> None:
        section = str(p.get("section", "") or "section")
        self._append_artifact(
            key=f"memo-{section}",
            kind="memo_section",
            title=f"Memo · {section.replace('_', ' ')}",
            body=str(p.get("content", "") or ""),
            payload=p,
        )

    def _on_disagreement(self, p: Mapping[str, Any]) -> None:
        self._append_artifact(
            key=f"disagreement-{self.applied_count}",
            kind="disagreement",
            title=f"Disagreement · {p.get('dimension', '?')}",
            body=str(p.get("description", "") or ""),
            payload=p,
        )

    # -- usage handlers ---------------------------------------------------- #

    def _on_usage(self, p: Mapping[str, Any]) -> None:
        self.usage_calls += 1
        self.input_tokens += _as_int(p.get("input_tokens"), default=0)
        self.output_tokens += _as_int(p.get("output_tokens"), default=0)
        self.cached_tokens += _as_int(p.get("cached_tokens"), default=0)
        cost = p.get("cost_usd")
        if isinstance(cost, (int, float)):
            self.cost_usd += float(cost)
        else:
            # None cost == a subscription lane call (no dollar figure).
            self.subscription_calls += 1

    def _on_usage_window(self, p: Mapping[str, Any]) -> None:
        self.usage_window = p

    # -- internals --------------------------------------------------------- #

    def _stamp_stance(self, persona: str, stance: str) -> None:
        """Record a persona's latest stance and backfill their most recent turn.

        A stance (bullish/bearish/neutral from ``thesis_recorded`` in the opening,
        or BUY/HOLD/SELL from ``vote_recorded`` in the verdict) arrives *after*
        the turn that produced it, so we stamp that just-finished turn here and
        stash the value so :meth:`_on_turn_started` carries it onto every
        subsequent turn of the same persona until it changes.
        """
        if not persona or not stance:
            return
        self._latest_stance[persona] = stance
        turn = self._last_turn_by_persona.get(persona)
        if turn is not None:
            turn.stance = stance

    def _sync_think_snippet(self, turn: TurnState) -> None:
        """Mirror a turn's private reasoning onto its persona for the mind view."""
        member = self.personas.get(turn.persona)
        if member is not None:
            member.think = turn.thinking

    def _append_artifact(
        self, *, key: str, kind: str, title: str, body: str, payload: Mapping[str, Any]
    ) -> None:
        self.transcript.append(
            ArtifactState(key=key, kind=kind, title=title, body=body, payload=payload)
        )

    def _track_time(self, ts: str) -> None:
        moment = _parse_ts(ts)
        if moment is None:
            return
        if self._first_ts is None:
            self._first_ts = moment
        self._last_ts = moment


def _as_int(value: Any, *, default: int | None) -> Any:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default
