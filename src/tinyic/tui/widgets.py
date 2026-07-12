"""Textual widgets for the Town Hall panes.

Each widget is a thin, stateless-ish *view* over a data object from
:mod:`tinyic.tui.state`: it holds a reference to its state object and rebuilds
its display in :meth:`sync`. The app owns the state and calls ``sync`` after each
batch of events is folded in. No widget touches the event stream, Textual timers,
or any engine internals — they only read plain dataclasses.
"""

from __future__ import annotations

from rich.text import Text
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Collapsible, Static

from .state import (
    ArtifactState,
    PersonaState,
    PhaseState,
    SteeringState,
    TownHallState,
    TurnState,
    humanize_count,
    humanize_duration,
)

__all__ = [
    "StatusHeader",
    "PhaseBanner",
    "TurnCard",
    "SteeringNote",
    "ArtifactCard",
    "PersonaCard",
    "persona_color",
]

# A small, stable palette. The six canonical committee members get fixed hues;
# anything else falls back to a rotation so unknown personas still get a color.
_PERSONA_COLORS: dict[str, str] = {
    "Warren Buffett": "#4fc3f7",
    "Charlie Munger": "#ba68c8",
    "Benjamin Graham": "#4db6ac",
    "Peter Lynch": "#ffb74d",
    "Howard Marks": "#e57373",
    "Li Lu": "#aed581",
}
_FALLBACK_COLORS = ("#4fc3f7", "#ba68c8", "#4db6ac", "#ffb74d", "#e57373", "#aed581")

_VOTE_COLORS = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}
_STANCE_COLORS = {"bullish": "green", "bearish": "red", "neutral": "yellow"}
_PHASE_LABELS = {
    "opening": "OPENING",
    "cross_exam": "CROSS-EXAMINATION",
    "rebuttal": "REBUTTAL",
    "verdict": "VERDICT",
}


def persona_color(name: str) -> str:
    """A stable display color for a persona name."""
    if name in _PERSONA_COLORS:
        return _PERSONA_COLORS[name]
    return _FALLBACK_COLORS[hash(name) % len(_FALLBACK_COLORS)]


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #

class StatusHeader(Static):
    """Top strip: company/ticker, current phase, elapsed, cost/usage rollup."""

    def __init__(self, state: TownHallState) -> None:
        super().__init__(id="header")
        self.state = state

    def sync(self) -> None:
        self.refresh()

    def render(self) -> Text:
        st = self.state
        title = st.ticker or "TinyIC"
        if st.company_name:
            title = f"{title} · {st.company_name}"
        line1 = Text()
        line1.append(title, style="bold")
        if st.preset:
            line1.append(f"   preset {st.preset}", style="dim")
        if st.finished:
            line1.append("   ✓ complete" if not st.errored else "   ✗ error",
                         style="green" if not st.errored else "red")
        elif st.truncated:
            line1.append("   ⚠ incomplete (truncated log)", style="bold yellow")

        line2 = Text()
        line2.append("phase ", style="dim")
        line2.append(st.phase_position, style="bold cyan")
        line2.append("   elapsed ", style="dim")
        line2.append(humanize_duration(st.elapsed_s))
        line2.append("   cost ", style="dim")
        cost = f"${st.cost_usd:.4f}" if st.cost_usd else "$0.00"
        if st.subscription_calls:
            cost += f" (+{st.subscription_calls} sub)"
        line2.append(cost, style="bold green")
        line2.append("   tokens ", style="dim")
        line2.append(
            f"{humanize_count(st.input_tokens)} in / "
            f"{humanize_count(st.output_tokens)} out"
        )
        if st.usage_window:
            used = st.usage_window.get("window_used_msgs", "?")
            est = st.usage_window.get("window_estimate_msgs", "?")
            line2.append(f"   window {used}/{est}", style="dim")

        out = Text()
        out.append_text(line1)
        out.append("\n")
        out.append_text(line2)
        return out


# --------------------------------------------------------------------------- #
# Transcript items
# --------------------------------------------------------------------------- #

class PhaseBanner(Static):
    """A full-width rule announcing a phase (and its DA / turn count)."""

    def __init__(self, phase: PhaseState) -> None:
        super().__init__(classes="phase-banner")
        self.phase_state = phase

    def on_mount(self) -> None:
        self.sync()

    def sync(self) -> None:
        self.set_class(self.phase_state.completed, "completed")
        self.refresh()

    def render(self) -> Text:
        ph = self.phase_state
        label = _PHASE_LABELS.get(ph.phase, ph.phase.upper())
        text = Text(no_wrap=False)
        text.append(f"── {label} ", style="bold")
        if ph.da_persona:
            text.append(f"· devil's advocate: {ph.da_persona} ", style="italic red")
        if ph.completed and ph.turn_count is not None:
            text.append(f"· {ph.turn_count} turns ✓ ", style="dim green")
        text.append("─" * 8, style="dim")
        return text


class TurnCard(Vertical):
    """A speaker turn card: header + speech, with a collapsed thinking row.

    Clicking the card posts :class:`TurnCard.Selected` so the app can mark this
    turn as the ``t``-key target (FR-5.2: "toggle thinking on selected turn").
    """

    class Selected(Message):
        """Posted when the card is clicked; carries the transcript item key."""

        def __init__(self, key: str) -> None:
            self.key = key
            super().__init__()

    def __init__(self, turn: TurnState) -> None:
        super().__init__(classes="turn-card")
        self.turn = turn
        self.selected = False
        self._head = Static(classes="turn-head")
        self._speech = Static(classes="turn-speech")
        self._think_body = Static(classes="think-body")
        self._think = Collapsible(
            self._think_body,
            title="thinking",
            collapsed=True,
            collapsed_symbol="▸",
            expanded_symbol="▾",
            classes="turn-think",
        )

    def compose(self):
        yield self._head
        yield self._speech
        yield self._think

    def on_mount(self) -> None:
        self.sync()

    def on_click(self) -> None:
        self.post_message(self.Selected(self.turn.key))

    def toggle_thinking(self) -> None:
        """Expand/collapse just this turn's thinking row (the ``t`` key)."""
        self._think.collapsed = not self._think.collapsed

    def sync(self) -> None:
        self.set_class(self.turn.interrupted, "interrupted")
        self.set_class(self.turn.completed, "completed")
        self.set_class(self.selected, "selected")
        self._head.update(self._head_text())
        self._speech.update(
            Text(self.turn.speech) if self.turn.speech
            else Text("…", style="dim italic")
        )
        self._think_body.update(
            Text(self.turn.thinking) if self.turn.thinking
            else Text("(no private reasoning captured)", style="dim italic")
        )

    def _head_text(self) -> Text:
        turn = self.turn
        head = Text()
        head.append("● ", style=persona_color(turn.persona))
        head.append(turn.persona or "?", style=f"bold {persona_color(turn.persona)}")
        badge = f"  ⟨{turn.phase or '?'} · {turn.role or '?'}⟩"
        head.append(badge, style="dim")
        if turn.target_persona:
            head.append(f" → {turn.target_persona}", style="italic")
        if turn.interrupted:
            head.append("  ⚡interrupted", style="bold red")
        return head


class SteeringNote(Static):
    """An inline moderator/user steering message: queued -> delivered|dropped."""

    def __init__(self, steer: SteeringState) -> None:
        super().__init__(classes="steering-note")
        self.steer = steer

    def on_mount(self) -> None:
        self.sync()

    def sync(self) -> None:
        self.set_class(self.steer.status == "delivered", "delivered")
        self.set_class(self.steer.status == "dropped", "dropped")
        self.refresh()

    def render(self) -> Text:
        s = self.steer
        text = Text()
        mode = (s.mode or "steer").upper()
        text.append(f" ✎ {mode} ", style="bold black on yellow")
        if s.target_persona:
            text.append(f" @{s.target_persona}", style="bold")
        if s.source:
            text.append(f" ({s.source})", style="dim")
        text.append(f"  {_clip(s.text, 200)}\n", style="italic")
        if s.status == "delivered":
            tail = "→ delivered"
            if s.delivered_before_turn_id:
                tail += f" before {s.delivered_before_turn_id}"
            text.append(f"   {tail}", style="green")
        elif s.status == "dropped":
            text.append(f"   ✗ dropped — {s.reason}", style="red")
        else:
            text.append("   queued…", style="yellow")
        return text


class ArtifactCard(Static):
    """A standalone card for a structured artifact (data, scorecard, memo…)."""

    def __init__(self, artifact: ArtifactState) -> None:
        super().__init__(classes="artifact-card")
        self.artifact = artifact
        self.set_class(True, f"kind-{artifact.kind.replace('_', '-')}")

    def on_mount(self) -> None:
        self.sync()

    def sync(self) -> None:
        self.refresh()

    def render(self) -> Text:
        art = self.artifact
        text = Text()
        text.append(f"{art.title}\n", style="bold")
        if art.body:
            text.append(art.body, style="none")
        return text


# --------------------------------------------------------------------------- #
# Committee sidebar
# --------------------------------------------------------------------------- #

class PersonaCard(Static):
    """One committee member: static binding chips + live cognitive badges."""

    def __init__(self, persona: PersonaState) -> None:
        super().__init__(classes="persona-card")
        self.persona = persona

    def on_mount(self) -> None:
        self.sync()

    def sync(self) -> None:
        self.set_class(self.persona.speaking, "speaking")
        self.refresh()

    def render(self) -> Text:
        p = self.persona
        color = persona_color(p.name)
        text = Text()

        header = Text()
        header.append("● ", style=color)
        header.append(p.name, style=f"bold {color}")
        if p.speaking:
            header.append("  ◗ speaking", style="bold green")
        text.append_text(header)
        text.append("\n")

        # Model / auth / thinking chips.
        chips = Text()
        chips.append(p.model_ref or "—", style="cyan")
        text.append_text(chips)
        text.append("\n")
        meta = Text(style="dim")
        bits = []
        if p.auth_profile:
            bits.append(f"auth {p.auth_profile}")
        if p.thinking_level:
            bits.append(f"think {p.thinking_level}")
        if p.temperament:
            bits.append(p.temperament)
        meta.append(" · ".join(bits) if bits else "unbound")
        text.append_text(meta)

        # Live cognitive-state badges.
        if p.mood:
            text.append("\n")
            text.append("mood ", style="dim")
            text.append(_clip(p.mood, 60))
        if p.attention:
            text.append("\n")
            text.append("eye  ", style="dim")
            text.append(_clip(p.attention, 60))
        if p.goal:
            text.append("\n")
            text.append("goal ", style="dim")
            text.append(_clip(p.goal, 60))

        # Stance / vote line.
        if p.stance or p.vote or p.caved:
            text.append("\n")
            if p.stance:
                text.append(
                    p.stance,
                    style=f"bold {_STANCE_COLORS.get(p.stance, 'white')}",
                )
            if p.vote:
                if p.stance:
                    text.append(" · ")
                text.append(
                    p.vote, style=f"bold {_VOTE_COLORS.get(p.vote, 'white')}"
                )
                if p.confidence:
                    text.append(f" ({p.confidence})", style="dim")
            if p.caved:
                text.append("  ⚑ caved", style="bold red")
        return text
