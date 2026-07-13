"""Pure, app-free tests for the Town Hall widgets (``tinyic.tui.widgets``).

These construct widgets directly (no ``run_test`` pilot) and inspect what they
render, locking down the review findings that live entirely in the view layer:
the per-turn stance badge (FR-5.1), the persona mind-view expansion (FR-5.2),
and the process-independent persona color (determinism nit).
"""

from __future__ import annotations

import hashlib

from rich.markdown import Markdown
from rich.text import Text

from tinyic.tui.state import PersonaState, TurnState
from tinyic.tui.widgets import (
    _FALLBACK_COLORS,
    PersonaCard,
    TurnCard,
    persona_color,
    speech_markdown,
)


# --------------------------------------------------------------------------- #
# persona_color determinism (replay colors must not depend on hash seeding)
# --------------------------------------------------------------------------- #

def test_persona_color_keeps_canonical_hues():
    assert persona_color("Warren Buffett") == "#4fc3f7"
    assert persona_color("Li Lu") == "#aed581"


def test_persona_color_is_stable_for_unknown_names():
    # An unknown persona hashes into the fallback rotation via a stable SHA-1
    # digest, so the index is process-independent (unlike the salted builtin
    # hash). Assert the exact expected slot for a fixed name — this is what two
    # differently-seeded interpreter runs would both have to produce.
    name = "Cathie Wood"
    expected_index = (
        int(hashlib.sha1(name.encode("utf-8")).hexdigest(), 16) % len(_FALLBACK_COLORS)
    )
    assert persona_color(name) == _FALLBACK_COLORS[expected_index]
    # Repeatable within a run, too.
    assert persona_color(name) == persona_color(name)


# --------------------------------------------------------------------------- #
# Per-turn stance badge (FR-5.1)
# --------------------------------------------------------------------------- #

def test_turn_card_renders_stance_badge_when_known():
    verdict = TurnState(
        key="turn-x", turn_id="x", persona="Warren Buffett",
        phase="verdict", role="verdict", stance="BUY",
    )
    assert "[BUY]" in str(TurnCard(verdict)._head_text())

    opening = TurnState(
        key="turn-o", turn_id="o", persona="Benjamin Graham",
        phase="opening", role="statement", stance="bearish",
    )
    assert "[bearish]" in str(TurnCard(opening)._head_text())


def test_turn_card_omits_badge_when_stance_unknown():
    plain = TurnState(
        key="turn-y", turn_id="y", persona="Warren Buffett",
        phase="opening", role="statement",
    )
    assert "[" not in str(TurnCard(plain)._head_text())


# --------------------------------------------------------------------------- #
# Persona mind view (FR-5.2: `m`)
# --------------------------------------------------------------------------- #

def test_persona_card_hides_reasoning_until_expanded():
    persona = PersonaState(name="Li Lu", think="Weighing owner economics privately.")
    card = PersonaCard(persona)
    assert "Weighing owner economics" not in str(card.render())  # collapsed
    card.expanded = True
    rendered = str(card.render())
    assert "mind" in rendered
    assert "Weighing owner economics" in rendered


def test_persona_card_expanded_without_reasoning_shows_placeholder():
    card = PersonaCard(PersonaState(name="Ghost"))
    card.expanded = True
    assert "no private reasoning yet" in str(card.render())


# --------------------------------------------------------------------------- #
# Live-think highlight block (FR-5.1)
# --------------------------------------------------------------------------- #

def test_turn_card_live_think_text_shows_streaming_thought():
    turn = TurnState(
        key="turn-1", turn_id="t1", persona="Warren Buffett",
        phase="opening", role="statement",
    )
    turn.thinking_streaming = True
    turn.thinking = "weighing the durable moat"
    assert turn.thinking_live is True
    live = str(TurnCard(turn)._live_think_text())
    assert "thinking" in live
    assert "weighing the durable moat" in live


# --------------------------------------------------------------------------- #
# Markdown speech (Stage-1): plain while streaming, rendered once final
# --------------------------------------------------------------------------- #

def _speech_turn(**kwargs) -> TurnState:
    return TurnState(
        key="turn-md", turn_id="md", persona="Warren Buffett",
        phase="opening", role="statement", **kwargs,
    )


def test_completed_speech_renders_markdown_and_is_cached():
    card = TurnCard(_speech_turn(speech="**Buy** the moat", speech_final=True))
    rendered = card._speech_renderable()
    assert isinstance(rendered, Markdown)
    # Content-keyed cache: the same final text is parsed exactly once.
    assert card._speech_renderable() is rendered


def test_streaming_speech_stays_plain_incremental_text():
    # Mid-stream (no talk_completed, not completed): never parsed as markdown,
    # even when it already contains markdown syntax.
    card = TurnCard(_speech_turn(speech="partial **stream", speech_final=False))
    rendered = card._speech_renderable()
    assert isinstance(rendered, Text)
    assert rendered.plain == "partial **stream"


def test_turn_completed_flag_alone_also_triggers_markdown():
    # A log with talk deltas but no talk_completed still gets the rendered form
    # once its turn completes.
    card = TurnCard(_speech_turn(speech="- point one\n- point two", completed=True))
    assert isinstance(card._speech_renderable(), Markdown)


def test_malformed_markdown_never_crashes_the_renderer():
    nasty = [
        "```python\nunclosed fence",
        "| a | b |\n|---|\nbroken table row",
        "[stray [nested] brackets](",
        "*",
        "> quote\n\n``` \n\n***",
    ]
    for text in nasty:
        card = TurnCard(_speech_turn(speech=text, speech_final=True))
        rendered = card._speech_renderable()  # must not raise
        assert rendered is not None


def test_markdown_layer_failure_falls_back_to_plain_text(monkeypatch):
    import tinyic.tui.widgets as widgets_module

    class _Boom:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("markdown exploded")

    monkeypatch.setattr(widgets_module, "Markdown", _Boom)
    rendered = speech_markdown("**bold** claim")
    assert isinstance(rendered, Text)
    assert rendered.plain == "**bold** claim"


def test_empty_speech_placeholder_unchanged():
    card = TurnCard(_speech_turn(speech="", speech_final=True))
    rendered = card._speech_renderable()
    assert isinstance(rendered, Text)
    assert rendered.plain == "…"


# --------------------------------------------------------------------------- #
# Interrupt badge (FR-5.3): esc affordance vs. system interruption
# --------------------------------------------------------------------------- #

def test_turn_card_interrupt_badge_reads_as_esc_for_user():
    turn = TurnState(
        key="turn-i", turn_id="i", persona="Warren Buffett",
        phase="cross_exam", role="response",
        interrupted=True, interrupted_by="user", interrupt_disposition="cancelled",
    )
    head = str(TurnCard(turn)._head_text())
    assert "interrupted" in head
    assert "(esc)" in head          # the user's esc affordance result
    assert "cancelled" in head      # disposition surfaced


def test_turn_card_interrupt_badge_names_a_system_source():
    turn = TurnState(
        key="turn-i", turn_id="i", persona="Howard Marks",
        phase="cross_exam", role="response",
        interrupted=True, interrupted_by="system",
        interrupt_disposition="discarded_on_arrival",
    )
    head = str(TurnCard(turn)._head_text())
    assert "interrupted" in head
    assert "(system)" in head
    assert "(esc)" not in head


def test_turn_card_omits_interrupt_badge_when_not_interrupted():
    turn = TurnState(
        key="turn-ok", turn_id="ok", persona="Li Lu",
        phase="opening", role="statement",
    )
    assert "interrupted" not in str(TurnCard(turn)._head_text())
