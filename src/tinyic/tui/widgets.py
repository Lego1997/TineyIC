"""Textual widgets for the Town Hall panes.

Each widget is a thin, stateless-ish *view* over a data object from
:mod:`tinyic.tui.state`: it holds a reference to its state object and rebuilds
its display in :meth:`sync`. The app owns the state and calls ``sync`` after each
batch of events is folded in. No widget touches the event stream or any engine
internals — they only read plain dataclasses. The only timers here are the
presentation-only :class:`~tinyic.tui.motion.GlyphPulse` cycles (the header's
"debating…" indicator and a turn's live-think block), which are driven purely
by widget lifecycle and never touch state or events.

Persona identity (colors + monograms) comes from :mod:`tinyic.persona_style` —
the single app-level source shared with the HTML exporter. Theme-dependent Rich
content styles come from the :mod:`tinyic.tui.theme` seam so pills and emphasis
stay legible on both ``tinyic-dark`` and ``tinyic-light``.
"""

from __future__ import annotations

from rich.markdown import Markdown
from rich.text import Text
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Collapsible, Static

from ..persona_style import FALLBACK_COLORS_DARK as _FALLBACK_COLORS  # noqa: F401 - re-export
from ..persona_style import persona_color
from .motion import GlyphPulse
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
from .theme import accent, is_dark, muted, pill

__all__ = [
    "StatusHeader",
    "PhaseBanner",
    "TurnCard",
    "SteeringNote",
    "ArtifactCard",
    "PersonaCard",
    "persona_color",
    "speech_markdown",
]

_VOTE_COLORS = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}
_STANCE_COLORS = {"bullish": "green", "bearish": "red", "neutral": "yellow"}
_PHASE_LABELS = {
    "opening": "OPENING",
    "cross_exam": "CROSS-EXAMINATION",
    "rebuttal": "REBUTTAL",
    "verdict": "VERDICT",
}


def speech_markdown(text: str) -> Markdown | Text:
    """Render a completed speech as Rich markdown, falling back to plain text.

    Model output routinely contains ``**bold**``, lists, and the occasional
    fenced block; rendering the *completed* turn as markdown makes those read
    as intended. CommonMark parsing is total (malformed input parses as text),
    but the guard is absolute anyway: any exception from the markdown layer
    degrades to the exact plain :class:`~rich.text.Text` the TUI rendered
    before — a hostile or bizarre payload can never crash a renderer.
    """
    try:
        return Markdown(text)
    except Exception:
        return Text(text)


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #

class StatusHeader(Static):
    """Top strip: company/ticker, current phase, elapsed, cost/usage rollup.

    While a live feed is running (``state.live`` and not finished) the
    "debating…" indicator breathes via a :class:`GlyphPulse`; the pulse stops —
    and the glyph rests at the static ``◉`` — the moment a terminal event flips
    ``finished``. Presentation-only: ``sync`` derives run/stop purely from the
    already-folded state.
    """

    def __init__(self, state: TownHallState) -> None:
        super().__init__(id="header")
        self.state = state
        self._pulse = GlyphPulse(self)

    def on_mount(self) -> None:
        self.sync()

    def sync(self) -> None:
        self._pulse.set_running(self.state.live and not self.state.finished)
        self.refresh()

    def render(self) -> Text:
        st = self.state
        dark = is_dark(self)
        dim = muted(dark=dark)
        emph = accent(dark=dark)
        title = st.ticker or "TinyIC"
        if st.company_name:
            title = f"{title} · {st.company_name}"
        line1 = Text()
        line1.append(title, style="bold")
        if st.preset:
            line1.append(f"   preset {st.preset}", style=dim)
        if st.finished:
            line1.append("   ✓ complete" if not st.errored else "   ✗ error",
                         style="green" if not st.errored else "red")
        elif st.live:
            # A live feed shows a running status until a terminal event lands;
            # this is the live counterpart of the truncated-log indicator.
            line1.append(f"   {self._pulse.glyph} debating…", style=emph)
        elif st.truncated:
            line1.append("   ⚠ incomplete (truncated log)", style="bold yellow")

        line2 = Text()
        line2.append("phase ", style=dim)
        line2.append(st.phase_position, style=emph)
        line2.append("   elapsed ", style=dim)
        line2.append(humanize_duration(st.elapsed_s))
        line2.append("   cost ", style=dim)
        cost = f"${st.cost_usd:.4f}" if st.cost_usd else "$0.00"
        if st.subscription_calls:
            cost += f" (+{st.subscription_calls} sub)"
        line2.append(cost, style="bold green")
        line2.append("   tokens ", style=dim)
        line2.append(
            f"{humanize_count(st.input_tokens)} in / "
            f"{humanize_count(st.output_tokens)} out"
        )
        if st.usage_window:
            used = st.usage_window.get("window_used_msgs", "?")
            est = st.usage_window.get("window_estimate_msgs", "?")
            line2.append(f"   window {used}/{est}", style=dim)

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
        # Completed speech renders as Rich markdown; parsing happens once per
        # final text and is cached here (content-keyed), so the ~30 ms pump can
        # re-sync hundreds of mounted cards without ever re-parsing. (A full
        # Textual Markdown widget per turn was rejected: at 100+ turns its
        # per-widget layout cost dwarfs a cached Rich renderable in a Static.)
        self._md_cache: tuple[str, Markdown | Text] | None = None
        # Live-think motion: pulses only while ``turn.thinking_live``; repaints
        # just the live block, and stops (resting glyph) the moment talk begins.
        self._pulse = GlyphPulse(self, on_frame=self._paint_live_think)
        self._head = Static(classes="turn-head")
        # The live thinking block (FR-5.1): shown, auto-expanded and visually
        # distinct, only while ``turn.thinking_live`` — i.e. the current speaker
        # is streaming THINK and has not begun to TALK. It hides the moment talk
        # starts, and the standard collapsible ``▸ thinking`` row below takes
        # over (content preserved, toggled by t/T).
        self._think_live = Static(classes="turn-think-live")
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
        yield self._think_live
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
        live = self.turn.thinking_live
        self.set_class(self.turn.interrupted, "interrupted")
        self.set_class(self.turn.completed, "completed")
        self.set_class(self.selected, "selected")
        self.set_class(live, "thinking-live")
        self._head.update(self._head_text())
        # Live thinking block auto-expanded while streaming; the standard
        # collapsible row is hidden until talk begins, then they swap. Neither
        # touches the collapsible's ``collapsed`` state, so t/T stay user-owned.
        self._think_live.display = live
        self._pulse.set_running(live)
        if live:
            self._think_live.update(self._live_think_text())
        self._speech.update(self._speech_renderable())
        self._think.display = not live
        self._think_body.update(
            Text(self.turn.thinking) if self.turn.thinking
            else Text("(no private reasoning captured)", style="dim italic")
        )

    def _speech_renderable(self) -> Markdown | Text:
        """Plain incremental text while streaming; cached markdown once final.

        Live ``talk_delta`` accumulation stays a cheap :class:`Text` — markdown
        is *never* re-parsed per delta. Once the speech is final
        (``talk_completed`` set ``speech_final``, or the turn completed) the
        content is parsed once and the renderable cached keyed by the text, so
        every subsequent ``sync`` reuses it.
        """
        turn = self.turn
        if not turn.speech:
            return Text("…", style="dim italic")
        if not (turn.speech_final or turn.completed):
            return Text(turn.speech)
        cached = self._md_cache
        if cached is not None and cached[0] == turn.speech:
            return cached[1]
        rendered = speech_markdown(turn.speech)
        self._md_cache = (turn.speech, rendered)
        return rendered

    def _paint_live_think(self) -> None:
        """Pulse tick: repaint only the live-think block (never whole-card work)."""
        if self.turn.thinking_live:
            self._think_live.update(self._live_think_text())

    def _live_think_text(self) -> Text:
        """The streaming-THINK highlight body (auto-expanded live block)."""
        text = Text()
        text.append(f"{self._pulse.glyph} thinking… ", style="bold")
        text.append(self.turn.thinking or "…", style="italic")
        return text

    def _head_text(self) -> Text:
        turn = self.turn
        color = persona_color(turn.persona, dark=is_dark(self))
        head = Text()
        head.append("● ", style=color)
        head.append(turn.persona or "?", style=f"bold {color}")
        badge = f"  ⟨{turn.phase or '?'} · {turn.role or '?'}⟩"
        head.append(badge, style="dim")
        if turn.stance:
            stance_color = (
                _VOTE_COLORS.get(turn.stance)
                or _STANCE_COLORS.get(turn.stance)
                or "white"
            )
            head.append("  ")
            head.append(f"[{turn.stance}]", style=f"bold {stance_color}")
        if turn.target_persona:
            head.append(f" → {turn.target_persona}", style="italic")
        if turn.interrupted:
            # by == "user" is the esc affordance's result; show it as such.
            if turn.interrupted_by == "user":
                suffix = " (esc)"
            elif turn.interrupted_by:
                suffix = f" ({turn.interrupted_by})"
            else:
                suffix = ""
            head.append(f"  ⚡ interrupted{suffix}", style="bold red")
            if turn.interrupt_disposition:
                head.append(f" · {turn.interrupt_disposition}", style="dim red")
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
        text.append(f" ✎ {mode} ", style=pill(s.mode or "steer", dark=is_dark(self)))
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
        # Mind view (the `m` key): when set, the card reveals the persona's
        # latest private-reasoning snippet inline (FR-5.2).
        self.expanded = False

    def on_mount(self) -> None:
        self.sync()

    def sync(self) -> None:
        self.set_class(self.persona.speaking, "speaking")
        self.set_class(self.expanded, "mind-expanded")
        self.refresh()

    def render(self) -> Text:
        p = self.persona
        color = persona_color(p.name, dark=is_dark(self))
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

        # Mind view: the `m` key expands this card to reveal the latest private
        # reasoning snippet (the goal/attention/mood badges above already surface
        # the cognitive state).
        if self.expanded:
            text.append("\n")
            text.append("mind ", style="dim")
            if p.think:
                text.append(_clip(p.think, 240), style="italic")
            else:
                text.append("(no private reasoning yet)", style="dim italic")
        return text
