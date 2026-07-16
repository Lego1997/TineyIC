"""Single source of truth for the committee's visual identity.

App-level on purpose (not ``tui/``): the static HTML exporter
(:mod:`tinyic.report`) consumes this palette, and the zero-build web assets keep
a test-pinned hand-synchronized copy. No Textual import reaches export paths.

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
    "INK",
    "PAPER",
    "persona_color",
    "persona_monogram",
    "relative_luminance",
    "contrast_ratio",
    "contrast_foreground",
]

# The two candidate monogram foregrounds — the registered themes' ground colors
# (tinyic-dark's charcoal, tinyic-light's paper), duplicated here as plain
# constants so this module stays framework-free (no Textual import).
INK = "#14120f"
PAPER = "#f7f3ea"

# The six canonical committee members. The dark set is the historical Town Hall
# palette (a stable product fact — the HTML export and replay colors depend on
# it); the light set darkens each hue in place for legibility on light ground.
# Both sets clear a 4.5:1 WCAG contrast ratio for text on their own theme
# ground (:data:`INK` for dark, :data:`PAPER` for light) — enforced by
# ``tests/test_persona_style.py``, which is how Lynch's original #e65100
# (3.42:1 on paper) got caught and darkened.
PERSONA_COLORS_DARK: dict[str, str] = {
    "Warren Buffett": "#4fc3f7",
    "Charlie Munger": "#ba68c8",
    "Benjamin Graham": "#4db6ac",
    "Peter Lynch": "#ffb74d",
    "Howard Marks": "#e57373",
    "Li Lu": "#aed581",
}

PERSONA_COLORS_LIGHT: dict[str, str] = {
    "Warren Buffett": "#01579b",
    "Charlie Munger": "#7b1fa2",
    "Benjamin Graham": "#00695c",
    "Peter Lynch": "#bf360c",
    "Howard Marks": "#c62828",
    "Li Lu": "#33691e",
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


def relative_luminance(color: str) -> float:
    """WCAG relative luminance of a ``#rrggbb`` color (0.0 black … 1.0 white).

    Malformed input degrades to mid-gray (0.5) rather than raising — a renderer
    asking about a bad hex should still get a usable contrast decision.
    """
    try:
        raw = color.lstrip("#")
        if len(raw) != 6:
            raise ValueError(color)
        channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    except (ValueError, AttributeError):
        return 0.5
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two ``#rrggbb`` colors (1.0 … 21.0)."""
    lighter, darker = sorted(
        (relative_luminance(a), relative_luminance(b)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def contrast_foreground(background: str) -> str:
    """The higher-contrast monogram foreground (:data:`INK` or :data:`PAPER`)
    for text sitting on ``background`` — e.g. the medallion monogram block.

    Pure luminance math (WCAG contrast ratio), so both persona palettes and any
    fallback hue get a legible foreground in both theme variants.
    """
    ink = contrast_ratio(background, INK)
    paper = contrast_ratio(background, PAPER)
    return INK if ink >= paper else PAPER


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
