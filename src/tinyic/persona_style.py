"""Single source of truth for the committee's visual identity.

App-level on purpose (not ``tui/``): both the Textual Town Hall
(:mod:`tinyic.tui.widgets`) and the static HTML exporter (:mod:`tinyic.report`)
colorize personas, and they must colorize *identically* — one palette here,
consumed by every renderer, with no Textual import so the export path stays
TUI-free.

Each canonical committee member has:

* a **dark** hue — the original Town Hall palette, tuned for dark terminals
  (and the self-contained HTML export, which keeps this set for byte-stable
  output);
* a **light** hue — a darkened counterpart of the *same color family*, so a
  light terminal keeps contrast without changing who "owns" which color;
* a **two-letter monogram** (WB · CM · BG · PL · HM · LL) for compact persona
  chips where a full name won't fit (e.g. a 38-col committee sidebar).

Unknown personas fall back deterministically: a SHA-1 digest of the name (never
the salted builtin ``hash``) picks a slot in the fallback rotation, so a replay
colors an unfamiliar committee identically in every process, and the dark/light
fallback rotations are index-aligned — the same persona lands on the same color
family in both variants.
"""

from __future__ import annotations

import hashlib

__all__ = [
    "PERSONA_COLORS_DARK",
    "PERSONA_COLORS_LIGHT",
    "PERSONA_MONOGRAMS",
    "FALLBACK_COLORS_DARK",
    "FALLBACK_COLORS_LIGHT",
    "persona_color",
    "persona_monogram",
]

# The six canonical committee members. The dark set is the historical Town Hall
# palette (a stable product fact — the HTML export and replay colors depend on
# it); the light set darkens each hue in place for legibility on light ground.
PERSONA_COLORS_DARK: dict[str, str] = {
    "Warren Buffett": "#4fc3f7",
    "Charlie Munger": "#ba68c8",
    "Benjamin Graham": "#4db6ac",
    "Peter Lynch": "#ffb74d",
    "Howard Marks": "#e57373",
    "Li Lu": "#aed581",
}

PERSONA_COLORS_LIGHT: dict[str, str] = {
    "Warren Buffett": "#0277bd",
    "Charlie Munger": "#7b1fa2",
    "Benjamin Graham": "#00695c",
    "Peter Lynch": "#e65100",
    "Howard Marks": "#c62828",
    "Li Lu": "#558b2f",
}

PERSONA_MONOGRAMS: dict[str, str] = {
    "Warren Buffett": "WB",
    "Charlie Munger": "CM",
    "Benjamin Graham": "BG",
    "Peter Lynch": "PL",
    "Howard Marks": "HM",
    "Li Lu": "LL",
}

# Fallback rotations for unknown personas — index-aligned across variants so a
# given unknown name keeps its color family when the theme flips.
FALLBACK_COLORS_DARK: tuple[str, ...] = tuple(PERSONA_COLORS_DARK.values())
FALLBACK_COLORS_LIGHT: tuple[str, ...] = tuple(PERSONA_COLORS_LIGHT.values())


def _fallback_index(name: str) -> int:
    """Deterministic, process-independent slot for an unknown persona name."""
    digest = int(hashlib.sha1((name or "").encode("utf-8")).hexdigest(), 16)
    return digest % len(FALLBACK_COLORS_DARK)


def persona_color(name: str, *, dark: bool = True) -> str:
    """A stable display color for a persona name.

    ``dark`` selects the palette variant (dark terminals / the HTML export use
    the dark set; a light theme passes ``dark=False``). Unknown personas hash
    into the fallback rotation via SHA-1 — never the salted builtin ``hash`` —
    so a replay colorizes the same committee identically every time.
    """
    table = PERSONA_COLORS_DARK if dark else PERSONA_COLORS_LIGHT
    if name in table:
        return table[name]
    fallback = FALLBACK_COLORS_DARK if dark else FALLBACK_COLORS_LIGHT
    return fallback[_fallback_index(name)]


def persona_monogram(name: str) -> str:
    """A two-letter monogram for a persona (``Warren Buffett`` -> ``WB``).

    Unknown personas derive deterministically from their name: initials of the
    first two words, or the first two letters of a single-word name, uppercased.
    An empty name renders as ``"??"`` rather than crashing a renderer.
    """
    if name in PERSONA_MONOGRAMS:
        return PERSONA_MONOGRAMS[name]
    words = [w for w in (name or "").split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if words and len(words[0]) >= 2:
        return words[0][:2].upper()
    if words:
        return (words[0][0] * 2).upper()
    return "??"
