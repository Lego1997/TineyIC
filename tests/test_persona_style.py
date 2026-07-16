"""Unit tests for :mod:`tinyic.persona_style` — the single persona-identity source.

These tests lock down the product facts that every renderer shares: the
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
    assert ps.persona_color("Warren Buffett", dark=False) == "#01579b"


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


def test_report_delegates_to_persona_style():
    from tinyic.report import _persona_color

    assert _persona_color("Warren Buffett") == "#4fc3f7"
    # Unknown personas flow through the same deterministic rotation.
    assert _persona_color("Cathie Wood") == ps.persona_color("Cathie Wood")


# --------------------------------------------------------------------------- #
# The medallion contrast helper (Stage-3)
# --------------------------------------------------------------------------- #

def test_contrast_foreground_picks_ink_on_light_and_paper_on_dark():
    assert ps.contrast_foreground("#ffffff") == ps.INK
    assert ps.contrast_foreground("#000000") == ps.PAPER
    assert ps.contrast_foreground("#ffb74d") == ps.INK    # light amber -> ink
    assert ps.contrast_foreground("#7b1fa2") == ps.PAPER  # deep purple -> paper


def test_contrast_foreground_meets_wcag_on_every_palette_hue():
    # Every persona color — dark set, light set — must carry its chosen
    # monogram foreground at >= 4.3:1 (WCAG AA for the medallion's bold text).
    for palette in (ps.PERSONA_COLORS_DARK, ps.PERSONA_COLORS_LIGHT):
        for name, color in palette.items():
            fg = ps.contrast_foreground(color)
            assert fg in {ps.INK, ps.PAPER}
            assert ps.contrast_ratio(color, fg) >= 4.3, (name, color, fg)


def test_palette_text_contrast_meets_wcag_on_its_theme_ground():
    # Persona names, turn-card borders, and head text render in these hues
    # directly on the registered theme grounds — not just inside medallions.
    # Enforce a 4.5:1 WCAG AA floor for each palette on its own ground so no
    # hand-picked hue slides under the bar again (Peter Lynch's original
    # #e65100 measured 3.42:1 on paper and shipped unnoticed).
    from tinyic.tui.theme import TINYIC_DARK, TINYIC_LIGHT

    for palette, ground in (
        (ps.PERSONA_COLORS_DARK, TINYIC_DARK.background),
        (ps.PERSONA_COLORS_LIGHT, TINYIC_LIGHT.background),
    ):
        for name, color in palette.items():
            ratio = ps.contrast_ratio(color, ground)
            assert ratio >= 4.5, (name, color, ground, round(ratio, 2))


def test_ink_and_paper_mirror_the_registered_theme_grounds():
    # persona_style duplicates the theme grounds to stay framework-free; this
    # pins the duplication so the two can never drift apart silently.
    from tinyic.tui.theme import TINYIC_DARK, TINYIC_LIGHT

    assert ps.INK == TINYIC_DARK.background
    assert ps.PAPER == TINYIC_LIGHT.background


def test_relative_luminance_is_wcag_anchored():
    assert ps.relative_luminance("#000000") == 0.0
    assert abs(ps.relative_luminance("#ffffff") - 1.0) < 1e-9
    # sRGB linearization: mid-gray is darker than 0.5 (gamma), around 0.2158.
    assert 0.20 < ps.relative_luminance("#808080") < 0.23


def test_contrast_helpers_tolerate_malformed_input():
    # Bad hexes degrade to mid-gray, never raise — a renderer must always get
    # a usable decision.
    for bad in ("", "#fff", "not-a-color", None):
        assert ps.relative_luminance(bad) == 0.5
        assert ps.contrast_foreground(bad) in {ps.INK, ps.PAPER}
