"""Unit tests for :mod:`tinyic.persona_style` — the single persona-identity source.

Stage-1 of the TUI polish moved the committee palette out of its two duplicated
homes (``tinyic.tui.widgets`` and ``tinyic.report``) into one app-level module.
These tests lock down the product facts that every renderer now shares: the
historical dark hues, a complete light counterpart set, the two-letter
monograms, and the deterministic (SHA-1, never salted-``hash``) fallback
rotation for unknown personas.
"""

from __future__ import annotations

import hashlib

from tinyic import persona_style as ps

CANONICAL = (
    "Warren Buffett",
    "Charlie Munger",
    "Benjamin Graham",
    "Peter Lynch",
    "Howard Marks",
    "Li Lu",
)


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# --------------------------------------------------------------------------- #
# The canonical sets
# --------------------------------------------------------------------------- #

def test_dark_set_keeps_the_historical_hues():
    # These exact values are a stable product fact: replay colors and the HTML
    # export both depend on them (they predate this module).
    assert ps.PERSONA_COLORS_DARK == {
        "Warren Buffett": "#4fc3f7",
        "Charlie Munger": "#ba68c8",
        "Benjamin Graham": "#4db6ac",
        "Peter Lynch": "#ffb74d",
        "Howard Marks": "#e57373",
        "Li Lu": "#aed581",
    }


def test_light_set_is_complete_and_actually_darker():
    assert set(ps.PERSONA_COLORS_LIGHT) == set(CANONICAL)
    for name in CANONICAL:
        dark_hue = ps.PERSONA_COLORS_DARK[name]
        light_hue = ps.PERSONA_COLORS_LIGHT[name]
        assert light_hue != dark_hue
        # The light-theme counterpart must be darker (lower luminance) than the
        # dark-theme hue, or it would wash out on light ground.
        assert _luminance(light_hue) < _luminance(dark_hue), name


def test_monograms_are_the_locked_two_letter_set():
    assert ps.PERSONA_MONOGRAMS == {
        "Warren Buffett": "WB",
        "Charlie Munger": "CM",
        "Benjamin Graham": "BG",
        "Peter Lynch": "PL",
        "Howard Marks": "HM",
        "Li Lu": "LL",
    }
    assert all(len(m) == 2 for m in ps.PERSONA_MONOGRAMS.values())


# --------------------------------------------------------------------------- #
# persona_color: variants + deterministic fallback
# --------------------------------------------------------------------------- #

def test_persona_color_selects_the_variant():
    assert ps.persona_color("Warren Buffett") == "#4fc3f7"          # dark default
    assert ps.persona_color("Warren Buffett", dark=True) == "#4fc3f7"
    assert ps.persona_color("Warren Buffett", dark=False) == "#0277bd"


def test_unknown_persona_fallback_is_sha1_deterministic():
    name = "Cathie Wood"
    expected = int(hashlib.sha1(name.encode("utf-8")).hexdigest(), 16) % len(
        ps.FALLBACK_COLORS_DARK
    )
    assert ps.persona_color(name) == ps.FALLBACK_COLORS_DARK[expected]
    assert ps.persona_color(name, dark=False) == ps.FALLBACK_COLORS_LIGHT[expected]
    # Repeatable within a run too.
    assert ps.persona_color(name) == ps.persona_color(name)


def test_fallback_rotations_are_index_aligned_across_variants():
    # The same unknown persona must own the same color *family* in both themes:
    # slot i of the dark rotation is the darkened counterpart at slot i of the
    # light rotation (both derive from the canonical dicts in order).
    assert len(ps.FALLBACK_COLORS_DARK) == len(ps.FALLBACK_COLORS_LIGHT) == 6
    assert ps.FALLBACK_COLORS_DARK == tuple(ps.PERSONA_COLORS_DARK.values())
    assert ps.FALLBACK_COLORS_LIGHT == tuple(ps.PERSONA_COLORS_LIGHT.values())


# --------------------------------------------------------------------------- #
# persona_monogram fallbacks
# --------------------------------------------------------------------------- #

def test_monogram_fallbacks_are_deterministic_initials():
    assert ps.persona_monogram("Cathie Wood") == "CW"
    assert ps.persona_monogram("Michael J. Burry") == "MJ"  # first two words
    assert ps.persona_monogram("Ackman") == "AC"
    assert ps.persona_monogram("X") == "XX"
    assert ps.persona_monogram("") == "??"


# --------------------------------------------------------------------------- #
# Consumers actually share this module (no palette duplication anywhere)
# --------------------------------------------------------------------------- #

def test_tui_widgets_reexport_matches_the_source():
    from tinyic.tui.widgets import _FALLBACK_COLORS, persona_color

    assert persona_color is ps.persona_color
    assert tuple(_FALLBACK_COLORS) == ps.FALLBACK_COLORS_DARK


def test_report_delegates_to_persona_style():
    from tinyic.report import _persona_color

    assert _persona_color("Warren Buffett") == "#4fc3f7"
    # Unknown personas flow through the same deterministic rotation.
    assert _persona_color("Cathie Wood") == ps.persona_color("Cathie Wood")
