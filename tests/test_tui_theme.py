"""Tests for the Stage-1 theme layer (``tinyic.tui.theme``).

Covers: the two registered themes and their polarity, the dark-default +
``d``-key cycle on both apps (Town Hall and the onboarding wizard), the
pill/muted/accent style seam under both variants (the light-terminal-breaker
fix), and that composer typing still owns the ``d`` key.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from textual.widgets import Input

from tinyic.tui import theme
from tinyic.tui.app import TownHallApp
from tinyic.tui.onboard import OnboardApp, OnboardScreen
from tinyic.tui.widgets import SteeringNote

from tests.support import synthetic_events as G
from tests.test_m3_onboard_wizard import _controller


# --------------------------------------------------------------------------- #
# The Theme objects
# --------------------------------------------------------------------------- #

def test_theme_objects_are_a_dark_light_pair():
    assert theme.TINYIC_DARK.name == theme.DARK_THEME_NAME == "tinyic-dark"
    assert theme.TINYIC_LIGHT.name == theme.LIGHT_THEME_NAME == "tinyic-light"
    assert theme.TINYIC_DARK.dark is True
    assert theme.TINYIC_LIGHT.dark is False
    # The light theme is a deliberate counterpart, not a copy: every core token
    # differs from its dark twin.
    for attr in ("primary", "accent", "foreground", "background", "surface", "panel"):
        assert getattr(theme.TINYIC_DARK, attr) != getattr(theme.TINYIC_LIGHT, attr), attr


# --------------------------------------------------------------------------- #
# The style seam (pill / muted / accent)
# --------------------------------------------------------------------------- #

def test_pill_styles_differ_per_mode_and_variant_and_never_use_black():
    for dark in (True, False):
        steer = theme.pill("steer", dark=dark)
        queue = theme.pill("queue", dark=dark)
        assert steer != queue
        # Every pill is fg-on-bg and never the old literal "black on yellow".
        assert " on " in steer and " on " in queue
        assert "black" not in steer and "black" not in queue
    assert theme.pill("steer", dark=True) != theme.pill("steer", dark=False)
    assert theme.pill("queue", dark=True) != theme.pill("queue", dark=False)


def test_pill_tolerates_unknown_modes():
    # A forward-compatible steering mode from a newer log still gets a pill.
    assert theme.pill("nudge", dark=True) == theme.pill("steer", dark=True)
    assert theme.pill("nudge", dark=False) == theme.pill("steer", dark=False)


def test_muted_and_accent_track_the_variant():
    assert theme.muted(dark=True) != theme.muted(dark=False)
    assert theme.accent(dark=True) != theme.accent(dark=False)
    # No raw "dim"/"cyan" — concrete theme-tuned styles.
    for style in (theme.muted(dark=True), theme.muted(dark=False)):
        assert style != "dim"
    for style in (theme.accent(dark=True), theme.accent(dark=False)):
        assert "cyan" not in style


def test_is_dark_defaults_dark_when_no_app_is_reachable():
    # Bare objects / unmounted widgets fall back to the historical dark styling.
    assert theme.is_dark(object()) is True


def test_declared_textual_floor_supports_the_theme_layer():
    # ``textual.theme.Theme`` / App.register_theme / App.theme / current_theme
    # first shipped in Textual 0.86.0. The dev lockfile pins a modern release,
    # so only this guard keeps the *declared* floor honest: a pip install
    # resolving anything older would crash ``tinyic debate`` at import.
    pyproject = (
        Path(__file__).resolve().parents[1] / "src" / "tinyic" / "pyproject.toml"
    )
    deps = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    requirement = next(d for d in deps if d.startswith("textual"))
    match = re.search(r">=\s*(\d+)\.(\d+)", requirement)
    assert match is not None, f"textual needs a >= floor: {requirement!r}"
    floor = (int(match.group(1)), int(match.group(2)))
    assert floor >= (0, 86), f"theme layer needs textual >= 0.86: {requirement!r}"


# --------------------------------------------------------------------------- #
# Town Hall: registered, dark by default, `d` cycles
# --------------------------------------------------------------------------- #

async def test_town_hall_registers_and_defaults_to_tinyic_dark():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == theme.DARK_THEME_NAME
        assert theme.DARK_THEME_NAME in app.available_themes
        assert theme.LIGHT_THEME_NAME in app.available_themes
        assert app.current_theme.dark is True


async def test_town_hall_d_key_cycles_dark_light_dark():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert app.theme == theme.LIGHT_THEME_NAME
        assert app.current_theme.dark is False
        await pilot.press("d")
        await pilot.pause()
        assert app.theme == theme.DARK_THEME_NAME


async def test_composer_typing_still_owns_the_d_key():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.set_focus(app._composer_input)
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        # Typing wins: the character lands in the input, the theme stays put.
        assert app._composer_input.value == "d"
        assert app.theme == theme.DARK_THEME_NAME


# --------------------------------------------------------------------------- #
# The pill fix under both themes (the old "bold black on yellow" breaker)
# --------------------------------------------------------------------------- #

async def test_steering_note_pill_tracks_the_active_theme():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.replay_all_now()
        await pilot.pause()
        note = app.query(SteeringNote).first()
        mode = note.steer.mode or "steer"

        spans = note.render().spans
        assert spans[0].style == theme.pill(mode, dark=True)

        await pilot.press("d")  # -> tinyic-light
        await pilot.pause()
        spans = note.render().spans
        assert spans[0].style == theme.pill(mode, dark=False)


async def test_mode_chip_uses_the_pill_seam_in_both_themes():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        chip = app._mode_chip.content
        assert chip.plain.strip() == "STEER"
        assert chip.spans[0].style == theme.pill("steer", dark=True)

        await pilot.press("d")  # -> tinyic-light; _sync re-renders the chip
        await pilot.pause()
        chip = app._mode_chip.content
        assert chip.spans[0].style == theme.pill("steer", dark=False)
        assert "black" not in str(chip.spans[0].style)


# --------------------------------------------------------------------------- #
# Onboarding wizard: same themes, same default, same key
# --------------------------------------------------------------------------- #

async def test_onboard_registers_defaults_dark_and_d_cycles(tmp_path):
    app = OnboardApp(_controller(tmp_path), threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == theme.DARK_THEME_NAME
        assert theme.LIGHT_THEME_NAME in app.available_themes
        await pilot.press("d")
        await pilot.pause()
        assert app.theme == theme.LIGHT_THEME_NAME
        await pilot.press("d")
        await pilot.pause()
        assert app.theme == theme.DARK_THEME_NAME


async def test_wizard_masked_key_field_owns_the_d_key_and_friends(tmp_path):
    # The exact hunted collision: nearly every API key contains a ``d`` (and
    # digits, ``j``/``k``, ``q``). While the masked field has focus, the
    # focused-input guard at the top of ``OnboardApp.on_key`` must win over the
    # char-routing block below it — every character lands in the secret; no
    # theme flip, no list navigation, no quit. If anyone reorders that guard
    # below the routing, this fails on the first keystroke.
    controller = _controller(tmp_path)
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")   # begin -> openai CHOOSE
        await pilot.pause()
        await pilot.press("down")    # highlight the api_key lane
        await pilot.press("enter")   # -> CONNECT_KEY (focuses the masked input)
        await pilot.pause()
        assert controller.screen is OnboardScreen.CONNECT_KEY
        assert app.focused is app.query_one("#key-input", Input)

        await pilot.press("d", "1", "j", "k", "q")
        await pilot.pause()
        assert app.query_one("#key-input", Input).value == "d1jkq"
        assert app.theme == theme.DARK_THEME_NAME            # d did not flip
        assert controller.screen is OnboardScreen.CONNECT_KEY  # 1/j/k did not move
        assert app.return_code is None                       # q did not quit
