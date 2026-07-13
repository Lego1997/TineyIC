"""Stage-3 TUI polish: the persona theater.

Covers the five Stage-3 deliverables, purely offline:

* **the bench** — medallion cards (monogram block with a contrast-computed
  foreground on the persona's color), the model/thinking/temperament badge,
  attention as the prominent cognitive line, the speaking spotlight (persona-
  colored border + badge), the thinking spinner cue, and idle-bench muting;
* **turn cards** — the left border wears the speaker's color, with the
  selected/interrupted states keeping their warning/error claim, and the
  challenge target rendered as arrow + medallion in the target's color;
* **act headers** — the centered, letter-spaced ``ACT II · CROSS-EXAMINATION``
  rule built purely from folded phase events (DA credited, completed acts
  muted-checked, the verdict act accent-weighted);
* **the verdict reveal** — the scorecard's double-rule frame, idempotent under
  re-render;
* **wizard parity** — the onboarding status marks route through the theme's
  semantic hues.

Everything folds recorded/synthetic events only: replay and live share the
fold, so every treatment here renders identically on both paths.
"""

from __future__ import annotations

import queue

from tinyic.persona_style import contrast_foreground, persona_color
from tinyic.tui import theme
from tinyic.tui.app import TownHallApp
from tinyic.tui.events import parse_event, read_events
from tinyic.tui.state import PersonaState, PhaseState, TownHallState, TurnState
from tinyic.tui.widgets import (
    PersonaCard,
    PhaseBanner,
    ScorecardTable,
    TurnCard,
    build_act_header,
    persona_medallion,
)

from tests.support import synthetic_events as G


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _ev(type_: str, **payload):
    return parse_event({"type": type_, "payload": payload})


def _fold(events) -> TownHallState:
    state = TownHallState()
    for event in events:
        state.dispatch(event)
    return state


def _committee_prefix():
    """Synthetic events for a two-member committee with one opened turn."""
    return [
        _ev(
            "debate_started",
            ticker="AAPL",
            personas=[
                {"name": "Warren Buffett", "model_ref": "openai/gpt-5.6"},
                {"name": "Howard Marks", "model_ref": "anthropic/claude-opus-4-8"},
            ],
        ),
        _ev("phase_started", phase="opening", index=0),
        _ev(
            "turn_started",
            turn_id="t1", persona="Warren Buffett", phase="opening",
            role="statement",
        ),
    ]


def _events_until_first_turn():
    """The recorded fixture, cut just after the first ``turn_started``."""
    events = read_events(G.FIXTURE_PATH)
    for i, event in enumerate(events):
        if event.type == "turn_started":
            return events[: i + 1]
    raise AssertionError("fixture has no turn_started")


def _span_styles(text) -> dict[str, str]:
    return {text.plain[s.start:s.end]: str(s.style) for s in text.spans}


# --------------------------------------------------------------------------- #
# The medallion (monogram block + contrast-computed foreground)
# --------------------------------------------------------------------------- #

def test_medallion_is_the_monogram_on_the_persona_color():
    med = persona_medallion("Warren Buffett")
    assert med.plain == " WB "
    style = str(med.spans[0].style)
    assert "on #4fc3f7" in style
    assert contrast_foreground("#4fc3f7") in style


def test_medallion_tracks_the_light_palette():
    med = persona_medallion("Warren Buffett", dark=False)
    style = str(med.spans[0].style)
    assert "on #01579b" in style
    assert contrast_foreground("#01579b") in style


def test_medallion_handles_unknown_personas():
    med = persona_medallion("Cathie Wood")
    assert med.plain == " CW "
    color = persona_color("Cathie Wood")
    assert f"on {color}" in str(med.spans[0].style)


# --------------------------------------------------------------------------- #
# The bench: identity, hierarchy, spotlight, spinner, idle muting
# --------------------------------------------------------------------------- #

def test_bench_card_leads_with_medallion_and_badge_line():
    card = PersonaCard(PersonaState(
        name="Warren Buffett", model_ref="openai/gpt-5.6",
        thinking_level="high", temperament="balanced",
    ))
    rendered = card.render()
    assert rendered.plain.startswith(" WB  Warren Buffett")
    assert "openai/gpt-5.6" in rendered.plain
    assert "think high" in rendered.plain
    assert "— balanced" in rendered.plain  # temperament glyph + word


def test_bench_attention_is_prominent_goal_and_mood_muted():
    card = PersonaCard(PersonaState(
        name="Li Lu", attention="cross-exam data quality",
        goal="convince the committee", mood="confident",
    ))
    rendered = card.render()
    styles = _span_styles(rendered)
    dim = theme.muted(dark=True)
    # Attention: pointed with the persona's color, value at full weight
    # (no muted span); goal/mood lines are fully muted.
    assert "▸ " in rendered.plain
    assert "cross-exam data quality" in rendered.plain
    assert styles.get("goal convince the committee") == dim
    assert styles.get("mood confident") == dim
    assert "cross-exam data quality" not in styles  # unstyled == default fg


def test_bench_thinking_spinner_then_speaking_badge():
    thinking = PersonaCard(PersonaState(
        name="Warren Buffett", speaking=True, thinking_active=True,
    ))
    assert "thinking" in thinking.render().plain
    assert "◗ speaking" not in thinking.render().plain

    talking = PersonaCard(PersonaState(name="Warren Buffett", speaking=True))
    assert "◗ speaking" in talking.render().plain
    assert "thinking" not in talking.render().plain


def test_thinking_active_folds_from_think_and_talk_events():
    events = _committee_prefix()
    state = _fold(events + [_ev("think_delta", turn_id="t1", text="hmm")])
    assert state.personas["Warren Buffett"].thinking_active is True

    # The first talk fragment ends the spinner.
    state.dispatch(_ev("talk_delta", turn_id="t1", text="I say"))
    assert state.personas["Warren Buffett"].thinking_active is False


def test_thinking_active_clears_on_interrupt_and_completion():
    base = _committee_prefix() + [_ev("think_delta", turn_id="t1", text="…")]

    interrupted = _fold(base + [
        _ev("turn_interrupted", turn_id="t1", persona="Warren Buffett", by="user"),
    ])
    assert interrupted.personas["Warren Buffett"].thinking_active is False

    completed = _fold(base + [
        _ev("turn_completed", turn_id="t1", persona="Warren Buffett"),
    ])
    assert completed.personas["Warren Buffett"].thinking_active is False


async def test_bench_spotlights_the_speaker_and_mutes_the_idle():
    # Mid-debate is a *live* fact: feed the prefix through an open queue (no
    # sentinel, no terminal event) so the stream is still in flight. A replayed
    # truncated log settles these cues instead (see test_tui_motion).
    live_queue: queue.Queue = queue.Queue()
    for event in _events_until_first_turn():
        live_queue.put(event)
    app = TownHallApp.live(live_queue, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()

        cards = {c.persona.name: c for c in app.query(PersonaCard)}
        speaker = next(n for n, c in cards.items() if c.persona.speaking)

        spotlight = cards[speaker]
        assert spotlight.has_class("speaking")
        assert not spotlight.has_class("idle")
        # The spotlight border wears the persona's own color.
        assert (
            spotlight.styles.border.top[1].hex.lower()
            == persona_color(speaker, dark=True)
        )
        for name, card in cards.items():
            if name != speaker:
                assert card.has_class("idle")
                assert not card.has_class("speaking")


async def test_bench_unmutes_when_no_one_holds_the_floor():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()  # finished debate: nobody is speaking
        await pilot.pause()
        for card in app.query(PersonaCard):
            assert not card.has_class("idle")
            assert not card.has_class("speaking")


# --------------------------------------------------------------------------- #
# Turn cards: the persona-colored left border + medallion head
# --------------------------------------------------------------------------- #

def _turn(**kwargs) -> TurnState:
    base = dict(
        key="turn-x", turn_id="x", persona="Warren Buffett",
        phase="opening", role="statement",
    )
    base.update(kwargs)
    return TurnState(**base)


def test_turn_card_border_wears_the_persona_color():
    card = TurnCard(_turn(completed=True))
    card._sync_border()  # the full sync path is covered by the pilot test below
    style, color = card.styles.border_left
    assert style == "thick"
    assert color.hex.lower() == persona_color("Warren Buffett", dark=True)


def test_turn_card_border_keeps_warning_and_error_claims():
    selected = TurnCard(_turn())
    selected.selected = True
    selected._sync_border()
    assert (
        selected.styles.border_left[1].hex.lower()
        == theme.semantic("warning", dark=True)
    )

    interrupted = TurnCard(_turn(interrupted=True, interrupted_by="user"))
    interrupted._sync_border()
    assert (
        interrupted.styles.border_left[1].hex.lower()
        == theme.semantic("error", dark=True)
    )
    # Interrupted outranks selected: state that screams stays screaming.
    interrupted.selected = True
    interrupted._sync_border()
    assert (
        interrupted.styles.border_left[1].hex.lower()
        == theme.semantic("error", dark=True)
    )


def test_turn_head_leads_with_the_medallion():
    head = TurnCard(_turn())._head_text()
    assert head.plain.startswith(" WB  Warren Buffett")
    assert "⟨opening · statement⟩" in head.plain


def test_turn_head_challenge_target_gets_arrow_and_medallion():
    head = TurnCard(_turn(
        persona="Charlie Munger", phase="cross_exam", role="challenge",
        target_persona="Warren Buffett",
    ))._head_text()
    assert "→  WB  Warren Buffett" in head.plain
    styles = _span_styles(head)
    target_color = persona_color("Warren Buffett", dark=True)
    assert styles.get(" Warren Buffett") == f"italic {target_color}"


async def test_turn_card_borders_recolor_across_a_replay():
    # Every mounted card ends the replay wearing its own speaker's color —
    # the 100-turn-replay survival check in miniature.
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()
        cards = list(app.query(TurnCard))
        assert cards
        selected_key = app._effective_selected_key()
        for card in cards:
            expected = (
                theme.semantic("warning", dark=True)
                if card.turn.key == selected_key
                else persona_color(card.turn.persona, dark=True)
            )
            assert card.styles.border_left[1].hex.lower() == expected


# --------------------------------------------------------------------------- #
# Act headers
# --------------------------------------------------------------------------- #

def test_act_header_letter_spaces_and_centers_when_wide():
    header = build_act_header(
        PhaseState(key="p", phase="cross_exam", index=1), width=120
    )
    line = header.plain.splitlines()[0]
    assert "A C T   I I" in line
    assert "C R O S S - E X A M I N A T I O N" in line
    assert len(line) == 120  # rules fill the full width
    assert line.startswith("─") and line.endswith("─")


def test_act_header_drops_letter_spacing_when_narrow():
    header = build_act_header(
        PhaseState(key="p", phase="cross_exam", index=1), width=40
    )
    assert "ACT II · CROSS-EXAMINATION" in header.plain


def test_act_header_credits_the_devils_advocate():
    header = build_act_header(
        PhaseState(key="p", phase="cross_exam", index=1, da_persona="Howard Marks"),
        width=80,
    )
    credit_line = header.plain.splitlines()[1]
    assert "devil's advocate · Howard Marks" in credit_line
    styles = _span_styles(header)
    assert styles.get("Howard Marks") == (
        f"italic {persona_color('Howard Marks', dark=True)}"
    )


def test_completed_act_gets_the_muted_check_treatment():
    header = build_act_header(
        PhaseState(key="p", phase="opening", index=0, completed=True, turn_count=6),
        width=60,
    )
    assert "✓ ACT I · OPENING · 6 turns" in header.plain
    dim = theme.muted(dark=True)
    title_span = next(
        s for s in header.spans if "OPENING" in header.plain[s.start:s.end]
    )
    assert str(title_span.style) == dim


def test_verdict_act_header_takes_accent_weight():
    header = build_act_header(
        PhaseState(key="p", phase="verdict", index=3), width=60
    )
    accent = theme.accent_color(dark=True)
    styles = set(_span_styles(header).values())
    assert f"bold {accent}" in styles  # the title
    assert accent in styles            # the rules


def test_verdict_accent_yields_to_the_completed_treatment():
    header = build_act_header(
        PhaseState(key="p", phase="verdict", index=3, completed=True, turn_count=6),
        width=60,
    )
    assert "✓" in header.plain
    assert theme.accent_color(dark=True) not in {
        str(s.style) for s in header.spans
    }


async def test_phase_banners_render_as_act_headers():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()
        banners = list(app.query(PhaseBanner))
        assert len(banners) == 4
        first = str(banners[0].render())
        assert "ACT I" in first
        assert "✓" in first  # completed act: the muted check treatment
        assert all(b.has_class("completed") for b in banners)


# --------------------------------------------------------------------------- #
# The verdict reveal: a double-rule frame, idempotent under re-render
# --------------------------------------------------------------------------- #

async def test_scorecard_lands_in_a_double_rule_frame():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()
        card = app.query(ScorecardTable).first()
        for edge in ("border_top", "border_bottom", "border_left", "border_right"):
            assert getattr(card.styles, edge)[0] == "double"


async def test_verdict_reveal_is_idempotent_under_rerender():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()
        card = app.query(ScorecardTable).first()
        rows_before = card._table.row_count
        built_for = card._built_for

        # Re-render the world twice more: the frame neither duplicates nor
        # rebuilds — the same single card, the same rows, the same build key.
        app._sync()
        app._sync()
        await pilot.pause()
        cards = list(app.query(ScorecardTable))
        assert len(cards) == 1 and cards[0] is card
        assert card._table.row_count == rows_before
        assert card._built_for == built_for
        assert card.styles.border_top[0] == "double"


# --------------------------------------------------------------------------- #
# Wizard parity: status marks through the theme's semantic hues
# --------------------------------------------------------------------------- #

def test_wizard_status_marks_route_through_semantic_hues():
    from tinyic.tui.onboard import status_mark

    for status, kind in (("ok", "success"), ("warning", "warning"), ("error", "error")):
        for dark in (True, False):
            glyph, style = status_mark(status, dark=dark)
            assert glyph in {"✓", "▲", "✗"}
            assert style == f"bold {theme.semantic(kind, dark=dark)}"
    # The two variants actually differ (the light-terminal fix).
    assert status_mark("ok", dark=True) != status_mark("ok", dark=False)


def test_wizard_status_mark_tolerates_unknown_statuses():
    from tinyic.tui.onboard import status_mark

    glyph, style = status_mark("someday-status", dark=True)
    assert glyph == "·"
    assert style == theme.muted(dark=True)
