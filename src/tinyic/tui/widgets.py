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

from typing import Mapping

from rich.markdown import Markdown
from rich.text import Text
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Collapsible, DataTable, Static

from ..persona_style import FALLBACK_COLORS_DARK as _FALLBACK_COLORS  # noqa: F401 - re-export
from ..persona_style import persona_color, persona_monogram
from .motion import GlyphPulse
from .state import (
    PHASE_ORDER,
    ArtifactState,
    PersonaState,
    PhaseState,
    SteeringState,
    TownHallState,
    TurnState,
    humanize_count,
    humanize_duration,
)
from .theme import accent, is_dark, muted, pill, semantic, vote_pill

__all__ = [
    "StatusHeader",
    "PhaseBanner",
    "TurnCard",
    "SteeringNote",
    "ArtifactCard",
    "ScorecardTable",
    "UsageTable",
    "PersonaCard",
    "persona_color",
    "speech_markdown",
    "build_header_text",
    "build_phase_stepper",
    "build_header_meters",
    "HEADER_METERS_MIN_WIDTH",
]

# Stance / vote coloring routes through the theme's semantic hues (Stage-2), so
# BUY/bullish is the theme's success green, SELL/bearish its error red, and
# HOLD/neutral its warning amber — legible in both variants, unlike the old
# raw ANSI "green"/"red"/"yellow".
_VOTE_SEMANTIC = {"BUY": "success", "SELL": "error", "HOLD": "warning"}
_STANCE_SEMANTIC = {"bullish": "success", "bearish": "error", "neutral": "warning"}
# Directional vote glyphs from the app's existing vocabulary (never emoji).
_VOTE_GLYPHS = {"BUY": "▲", "SELL": "▾", "HOLD": "●"}
_PHASE_LABELS = {
    "opening": "OPENING",
    "cross_exam": "CROSS-EXAMINATION",
    "rebuttal": "REBUTTAL",
    "verdict": "VERDICT",
}
# Compact phase names for the header's stepper strip.
_PHASE_STEP_LABELS = {
    "opening": "opening",
    "cross_exam": "cross-exam",
    "rebuttal": "rebuttal",
    "verdict": "verdict",
}


def _stance_style(value: str, *, dark: bool) -> str:
    """Bold, theme-tuned style for a stance/vote badge (fallback: plain bold)."""
    kind = _VOTE_SEMANTIC.get(value) or _STANCE_SEMANTIC.get(value)
    return f"bold {semantic(kind, dark=dark)}" if kind else "bold"


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
# Header — the ticker strip (Stage-2)
# --------------------------------------------------------------------------- #

# Below this many content columns the header drops its meters block first
# (FR: degrade gracefully under 80 cols), keeping identity + stepper intact.
HEADER_METERS_MIN_WIDTH = 80


def build_phase_stepper(
    st: TownHallState, *, dark: bool = True, glyph: str = "◉"
) -> Text:
    """The four-phase stepper: done = ``✓`` accent · current = ``glyph`` bold ·
    future = ``·`` muted (an errored current phase shows ``✗``).

    Pure: derived only from folded phase events (``phases_completed`` /
    ``current_phase``), so replay and live render identically. ``glyph`` is the
    caller's pulse frame — a live header breathes it; replay passes the resting
    ``◉``.
    """
    dim = muted(dark=dark)
    emph = accent(dark=dark)
    text = Text()
    for i, phase in enumerate(PHASE_ORDER):
        if i:
            text.append("   ")
        label = _PHASE_STEP_LABELS.get(phase, phase)
        if phase in st.phases_completed:
            text.append(f"✓ {label}", style=emph)
        elif phase == st.current_phase and st.errored:
            text.append(f"✗ {label}", style=f"bold {semantic('error', dark=dark)}")
        elif phase == st.current_phase and not st.finished:
            text.append(f"{glyph} {label}", style="bold")
        else:
            text.append(f"· {label}", style=dim)
    # A forward-compatible phase outside the canonical four still shows up.
    if st.current_phase and st.current_phase not in PHASE_ORDER:
        text.append("   ")
        text.append(f"{glyph} {st.current_phase}", style="bold")
    return text


def build_header_meters(st: TownHallState, *, dark: bool = True) -> Text:
    """The live meters block: elapsed · tokens · cost (+sub) · window.

    Elapsed comes from :attr:`TownHallState.elapsed_s` — event ``ts`` deltas
    (or the recorded duration), never the wall clock — so a replayed log shows
    the same figures as the live run that produced it. Tokens/cost accumulate
    from ``usage`` events; the window meter is the latest ``usage_window``
    subscription snapshot when one exists.
    """
    dim = muted(dark=dark)
    text = Text()
    text.append("elapsed ", style=dim)
    text.append(humanize_duration(st.elapsed_s))
    text.append("  tok ", style=dim)
    text.append(
        f"{humanize_count(st.input_tokens)}/{humanize_count(st.output_tokens)}"
    )
    text.append("  cost ", style=dim)
    cost = f"${st.cost_usd:.4f}" if st.cost_usd else "$0.00"
    text.append(cost, style=f"bold {semantic('success', dark=dark)}")
    if st.subscription_calls:
        text.append(f" +{st.subscription_calls} sub", style=dim)
    if st.usage_window:
        used = st.usage_window.get("window_used_msgs", "?")
        est = st.usage_window.get("window_estimate_msgs", "?")
        text.append("  win ", style=dim)
        text.append(f"{used}/{est}")
    return text


def build_header_text(
    st: TownHallState, *, width: int = 120, dark: bool = True, glyph: str = "◉"
) -> Text:
    """The whole two-line ticker strip, pure and width-aware.

    Line 1: identity (ticker · company, bold) + lifecycle status on the left,
    the meters block right-aligned — dropped first when ``width`` falls under
    :data:`HEADER_METERS_MIN_WIDTH`. Line 2: the phase stepper. Never more
    than two lines.
    """
    dim = muted(dark=dark)
    emph = accent(dark=dark)
    line1 = Text()
    title = st.ticker or "TinyIC"
    if st.company_name:
        title = f"{title} · {st.company_name}"
    line1.append(title, style="bold")
    if st.preset:
        line1.append(f"  preset {st.preset}", style=dim)
    if st.finished:
        if st.errored:
            line1.append("  ✗ error", style=f"bold {semantic('error', dark=dark)}")
        else:
            line1.append(
                "  ✓ complete", style=f"bold {semantic('success', dark=dark)}"
            )
    elif st.live:
        # A live feed shows a running status until a terminal event lands;
        # this is the live counterpart of the truncated-log indicator.
        line1.append(f"  {glyph} debating…", style=emph)
    elif st.truncated:
        line1.append(
            "  ⚠ incomplete (truncated log)",
            style=f"bold {semantic('warning', dark=dark)}",
        )

    if width >= HEADER_METERS_MIN_WIDTH:
        meters = build_header_meters(st, dark=dark)
        pad = width - line1.cell_len - meters.cell_len
        if pad >= 2:  # right-align; drop the meters when they would wrap
            line1.append(" " * pad)
            line1.append_text(meters)

    out = Text()
    out.append_text(line1)
    out.append("\n")
    out.append_text(build_phase_stepper(st, dark=dark, glyph=glyph))
    return out


class StatusHeader(Static):
    """The header ticker strip: identity + lifecycle, meters, phase stepper.

    A thin widget over the pure builders above: ``render`` passes its live
    content width (for the under-80-col degradation), the theme variant, and
    the current pulse frame. While a live feed is running (``state.live`` and
    not finished) the indicator breathes via a :class:`GlyphPulse`; the pulse
    stops — and the glyph rests at the static ``◉`` — the moment a terminal
    event flips ``finished``. Presentation-only: ``sync`` derives run/stop
    purely from the already-folded state.
    """

    def __init__(self, state: TownHallState) -> None:
        super().__init__(id="header")
        self.state = state
        self._pulse = GlyphPulse(self)

    def on_mount(self) -> None:
        self.sync()

    def on_resize(self, event) -> None:  # noqa: ANN001 - textual event
        self.refresh()  # re-evaluate the meters-drop threshold

    def sync(self) -> None:
        self._pulse.set_running(self.state.live and not self.state.finished)
        self.refresh()

    def _render_width(self) -> int:
        """The current content width, or a wide default before layout."""
        try:
            width = int(self.content_size.width)
        except Exception:
            width = 0
        return width if width > 0 else 120

    def render(self) -> Text:
        return build_header_text(
            self.state,
            width=self._render_width(),
            dark=is_dark(self),
            glyph=self._pulse.glyph,
        )


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
            head.append("  ")
            head.append(
                f"[{turn.stance}]", style=_stance_style(turn.stance, dark=is_dark(self))
            )
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
    """A standalone card for a structured artifact (data, memo, disagreement…).

    Kind-aware (Stage-2): ``disagreement`` renders each side with its persona's
    color, and ``collapse_metric`` renders as a caved-red / held-green verdict
    card (the CSS ``caved``/``held`` classes color its border). Every other
    kind keeps the generic title + body rendering, so a forward-compatible
    artifact still shows faithfully.
    """

    def __init__(self, artifact: ArtifactState) -> None:
        super().__init__(classes="artifact-card")
        self.artifact = artifact
        self.set_class(True, f"kind-{artifact.kind.replace('_', '-')}")
        if artifact.kind == "collapse_metric":
            caved = bool(artifact.payload.get("caved"))
            self.set_class(caved, "caved")
            self.set_class(not caved, "held")

    def on_mount(self) -> None:
        self.sync()

    def sync(self) -> None:
        self.refresh()

    def render(self) -> Text:
        art = self.artifact
        if art.kind == "collapse_metric":
            return self._render_collapse(is_dark(self))
        if art.kind == "disagreement":
            return self._render_disagreement(is_dark(self))
        text = Text()
        text.append(f"{art.title}\n", style="bold")
        if art.body:
            text.append(art.body, style="none")
        return text

    def _render_collapse(self, dark: bool) -> Text:
        p = self.artifact.payload
        persona = str(p.get("persona", "") or "?")
        caved = bool(p.get("caved"))
        dim = muted(dark=dark)
        text = Text()
        text.append("Collapse check · ", style="bold")
        text.append(persona, style=f"bold {persona_color(persona, dark=dark)}")
        text.append("\n")
        if caved:
            text.append("⚑ caved", style=f"bold {semantic('error', dark=dark)}")
        else:
            text.append("✓ held", style=f"bold {semantic('success', dark=dark)}")
        before = str(p.get("stance_before", "") or "")
        after = str(p.get("stance_after", "") or "")
        if before or after:
            text.append(f" · {before or '?'} → {after or '?'}")
        if p.get("note"):
            text.append("\n")
            text.append(_clip(str(p["note"]), 200), style=dim)
        return text

    def _render_disagreement(self, dark: bool) -> Text:
        p = self.artifact.payload
        dim = muted(dark=dark)
        text = Text()
        text.append(f"{self.artifact.title}\n", style="bold")
        if p.get("description"):
            text.append(f"{p['description']}\n")
        for side in p.get("sides") or []:
            if not isinstance(side, Mapping):
                continue
            name = str(side.get("persona", "") or "?")
            text.append("▸ ", style=dim)
            text.append(name, style=f"bold {persona_color(name, dark=dark)}")
            if side.get("position"):
                text.append(f" — {side['position']}")
            text.append("\n")
            if side.get("evidence_quote"):
                text.append(f'   "{_clip(str(side["evidence_quote"]), 160)}"\n',
                            style=f"italic {dim}")
        if p.get("resolution"):
            text.append(f"resolution · {p['resolution']}", style=dim)
        return text


# --------------------------------------------------------------------------- #
# Table cards (Stage-2): the scorecard and end-of-debate usage rollup
# --------------------------------------------------------------------------- #

class _TableCard(Vertical):
    """A bordered artifact card holding a one-line summary + a real DataTable.

    The summary line keeps long transcripts scannable even when the table is
    tall; the table itself is rebuilt only when its inputs change (theme
    variant or the derived row set), so the ~30 ms pump can re-``sync`` mounted
    cards for free. Non-focusable: drive-mode keys stay with the app.
    """

    def __init__(self, artifact: ArtifactState, *, classes: str) -> None:
        super().__init__(classes=classes)
        self.artifact = artifact
        self._summary = Static(classes="table-card-summary")
        self._table = DataTable(
            show_cursor=False, zebra_stripes=False, classes="table-card-table"
        )
        self._table.can_focus = False
        self._built_for: tuple | None = None

    def compose(self):
        yield self._summary
        yield self._table

    def on_mount(self) -> None:
        self.sync()

    def sync(self) -> None:
        dark = is_dark(self)
        key = (dark, len(self.artifact.rows))
        if key == self._built_for:
            return
        self._built_for = key
        self._summary.update(self._summary_text(dark))
        self._table.clear(columns=True)
        self._build_table(self._table, dark)

    # subclass hooks ------------------------------------------------------- #

    def _summary_text(self, dark: bool) -> Text:
        return Text(self.artifact.title, style="bold")

    def _build_table(self, table: DataTable, dark: bool) -> None:
        raise NotImplementedError


class ScorecardTable(_TableCard):
    """The verdict scorecard as a real table: member · vote · conf · mind · source."""

    def __init__(self, artifact: ArtifactState) -> None:
        super().__init__(artifact, classes="artifact-card kind-scorecard")

    def _summary_text(self, dark: bool) -> Text:
        p = self.artifact.payload
        dim = muted(dark=dark)
        text = Text()
        text.append("Scorecard", style="bold")
        text.append(" · consensus ", style=dim)
        consensus = str(p.get("consensus") or "none")
        text.append(consensus, style=_stance_style(consensus, dark=dark))
        for vote, count_key in (
            ("BUY", "bull_count"), ("HOLD", "hold_count"), ("SELL", "bear_count"),
        ):
            text.append("   ")
            text.append(
                f"{_VOTE_GLYPHS[vote]} {p.get(count_key, 0)}",
                style=_stance_style(vote, dark=dark),
            )
        return text

    def _build_table(self, table: DataTable, dark: bool) -> None:
        dim = muted(dark=dark)
        table.add_columns("member", "vote", "conf", "mind", "source")
        for row in self.artifact.rows:
            name = str(row.get("persona", "") or "?")
            color = persona_color(name, dark=dark)
            member = Text()
            member.append(persona_monogram(name), style=f"bold {color}")
            member.append(f" {name}", style=color)
            vote = str(row.get("vote", "") or "")
            vote_cell = Text(
                f" {_VOTE_GLYPHS.get(vote, '·')} {vote or '—'} ",
                style=vote_pill(vote, dark=dark),
            )
            conf = Text(str(row.get("confidence", "") or "—"))
            if row.get("changed_mind"):
                mind = Text("⚑ changed", style=f"bold {semantic('warning', dark=dark)}")
            else:
                mind = Text("—", style=dim)
            source = Text(str(row.get("source", "") or "—"), style=dim)
            table.add_row(member, vote_cell, conf, mind, source)


class UsageTable(_TableCard):
    """The end-of-debate per-model usage rollup: model · calls · tokens · cost."""

    def __init__(self, artifact: ArtifactState) -> None:
        super().__init__(artifact, classes="artifact-card kind-usage-rollup")

    def _build_table(self, table: DataTable, dark: bool) -> None:
        dim = muted(dark=dark)
        table.add_columns("model", "calls", "in", "out", "cached", "cost")
        for row in self.artifact.rows:
            model = Text(str(row.get("model_ref", "") or "?"))
            calls = Text(str(row.get("calls", 0)), justify="right")
            tin = Text(humanize_count(row.get("input_tokens", 0)), justify="right")
            tout = Text(humanize_count(row.get("output_tokens", 0)), justify="right")
            cached = Text(
                humanize_count(row.get("cached_tokens", 0)),
                style=dim, justify="right",
            )
            cost_usd = row.get("cost_usd") or 0.0
            sub_calls = row.get("subscription_calls") or 0
            if cost_usd:
                cost = Text(
                    f"${cost_usd:.4f}",
                    style=f"bold {semantic('success', dark=dark)}", justify="right",
                )
            elif sub_calls:
                cost = Text("sub", style=dim, justify="right")
            else:
                cost = Text("$0", style=dim, justify="right")
            table.add_row(model, calls, tin, tout, cached, cost)


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
        dark = is_dark(self)
        if p.stance or p.vote or p.caved:
            text.append("\n")
            if p.stance:
                text.append(p.stance, style=_stance_style(p.stance, dark=dark))
            if p.vote:
                if p.stance:
                    text.append(" · ")
                text.append(p.vote, style=_stance_style(p.vote, dark=dark))
                if p.confidence:
                    text.append(f" ({p.confidence})", style="dim")
            if p.caved:
                text.append("  ⚑ caved", style=f"bold {semantic('error', dark=dark)}")

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
