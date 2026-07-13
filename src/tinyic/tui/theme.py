"""TinyIC's registered Textual themes plus the theme-aware Rich-style seam.

Two registered :class:`textual.theme.Theme` objects give the product one look
in both polarities (Stage-1 of the TUI polish):

* ``tinyic-dark`` — the default: deep warm charcoal ground with an
  amber-leaning accent (financial-terminal energy, not a costume). All the
  existing ``$panel`` / ``$accent`` / ``$boost`` design tokens in the apps' CSS
  resolve against it, including the generated ``-lighten-N`` shades.
* ``tinyic-light`` — a deliberate counterpart (warm paper ground, darkened
  amber/steel accents), not an inversion; every token was picked to keep the
  same hierarchy readable on light terminals.

Both :class:`~tinyic.tui.app.TownHallApp` and
:class:`~tinyic.tui.onboard.OnboardApp` call :func:`register` at construction
and default to dark; the documented ``d`` key cycles dark ↔ light via
:func:`toggle`.

**The style seam.** Textual CSS resolves ``$tokens`` per theme automatically,
but the widgets also build :class:`rich.text.Text` content with literal style
strings — those can't reference theme variables, which is exactly how the old
light-terminal breakers ("bold black on yellow" pills, hard-coded "dim"/"bold
cyan") happened. The helpers below (:func:`pill`, :func:`muted`,
:func:`accent`) are the one place such content styles come from: widgets ask
for the style *for the active variant* (via :func:`is_dark` on themselves) and
re-render on theme change, so Rich content tracks the theme without any new
render-time dependency. Unmounted widgets (pure unit tests) fall back to dark —
the historical styles.
"""

from __future__ import annotations

from textual.theme import Theme

__all__ = [
    "DARK_THEME_NAME",
    "LIGHT_THEME_NAME",
    "TINYIC_DARK",
    "TINYIC_LIGHT",
    "register",
    "toggle",
    "is_dark",
    "pill",
    "muted",
    "accent",
]

DARK_THEME_NAME = "tinyic-dark"
LIGHT_THEME_NAME = "tinyic-light"

# ---------------------------------------------------------------------------
# The two registered themes
# ---------------------------------------------------------------------------

TINYIC_DARK = Theme(
    name=DARK_THEME_NAME,
    # Amber-leaning core on deep warm charcoal: the terminal-grade data look.
    primary="#e8a33d",
    secondary="#7fb4ca",
    accent="#ffb454",
    warning="#e5c07b",
    error="#e06c75",
    success="#98c379",
    foreground="#e6e1d7",
    background="#14120f",
    surface="#1b1814",
    panel="#242019",
    dark=True,
)

TINYIC_LIGHT = Theme(
    name=LIGHT_THEME_NAME,
    # Deliberate counterparts: same families, darkened for warm-paper ground.
    primary="#9a6200",
    secondary="#33658a",
    accent="#8a5300",
    warning="#8a6d00",
    error="#b3261e",
    success="#2e7d4f",
    foreground="#2a2419",
    background="#f7f3ea",
    surface="#efe8da",
    panel="#e7decb",
    dark=False,
)


def register(app) -> None:
    """Register both TinyIC themes on ``app`` and default it to dark.

    Called from each app's ``__init__`` (before mount), so the first paint is
    already ``tinyic-dark`` — no default-theme flash.
    """
    app.register_theme(TINYIC_DARK)
    app.register_theme(TINYIC_LIGHT)
    app.theme = DARK_THEME_NAME


def toggle(app) -> str:
    """Cycle the app between ``tinyic-dark`` and ``tinyic-light`` (the ``d`` key).

    Returns the new theme name. Any non-TinyIC active theme flips to dark first
    so the cycle is always well-defined.
    """
    new_name = (
        LIGHT_THEME_NAME if app.theme == DARK_THEME_NAME else DARK_THEME_NAME
    )
    app.theme = new_name
    return new_name


def is_dark(node) -> bool:
    """Best-effort darkness of the active theme for an app *or* widget.

    Defaults to ``True`` (the historical dark styling) whenever no live app is
    reachable — e.g. widgets constructed bare in unit tests, or content built
    before mount — so the seam never crashes a renderer.
    """
    try:
        theme_obj = getattr(node, "current_theme", None)
        if theme_obj is None:
            theme_obj = node.app.current_theme
        return bool(theme_obj.dark)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# The Rich-content style seam (pill / muted / accent)
# ---------------------------------------------------------------------------
# Foreground of every pill is the theme's *ground* color (charcoal on dark,
# paper on light) so the badge stays high-contrast in both variants — the fix
# for the old "bold black on yellow" light-terminal breaker.

_PILL_STYLES: dict[bool, dict[str, str]] = {
    True: {
        "steer": "bold #14120f on #e5c07b",
        "queue": "bold #14120f on #7fb4ca",
    },
    False: {
        "steer": "bold #f7f3ea on #8a6d00",
        "queue": "bold #f7f3ea on #33658a",
    },
}

_MUTED = {True: "#8d8471", False: "#6f6653"}
_ACCENT = {True: "bold #ffb454", False: "bold #8a5300"}


def pill(kind: str, *, dark: bool = True) -> str:
    """The badge/pill style for a steering mode chip (``steer`` | ``queue``).

    Unknown kinds get the ``steer`` styling so a forward-compatible mode from a
    newer event log still renders as a legible pill.
    """
    table = _PILL_STYLES[bool(dark)]
    return table.get(kind, table["steer"])


def muted(*, dark: bool = True) -> str:
    """The de-emphasized label style (replaces raw ``dim`` where it matters)."""
    return _MUTED[bool(dark)]


def accent(*, dark: bool = True) -> str:
    """The amber emphasis style (replaces the hard-coded ``bold cyan``)."""
    return _ACCENT[bool(dark)]
