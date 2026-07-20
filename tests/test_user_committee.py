"""Round-trip and validation tests for the user-overlay committee writer."""

from __future__ import annotations

import pytest

from tinyic.models import presets
from tinyic.models.presets import PresetError


def test_set_and_clear_committee(tmp_path, monkeypatch):
    overlay = tmp_path / "tinyic.toml"
    monkeypatch.setenv(presets.USER_CONFIG_ENV_VAR, str(overlay))
    written = presets.set_user_committee(["warren_buffett", "li_lu"])
    assert written == overlay
    assert presets.load_config().get("committee") == ["warren_buffett", "li_lu"]
    presets.set_user_committee(None)
    assert presets.load_config().get("committee") is None


def test_round_trips_toml_dates_in_overlay(tmp_path, monkeypatch):
    # tomllib parses bare TOML dates into datetime.date; the overlay writer
    # must re-serialize them instead of failing the whole committee save.
    overlay = tmp_path / "tinyic.toml"
    overlay.write_text("last_onboarded = 2026-01-01\n", encoding="utf-8")
    monkeypatch.setenv(presets.USER_CONFIG_ENV_VAR, str(overlay))
    presets.set_user_committee(["warren_buffett", "li_lu"])
    text = overlay.read_text(encoding="utf-8")
    assert "last_onboarded = 2026-01-01" in text
    assert presets.load_config().get("committee") == ["warren_buffett", "li_lu"]


def test_preserves_other_overlay_content(tmp_path, monkeypatch):
    overlay = tmp_path / "tinyic.toml"
    overlay.write_text("[auth.openai]\npolicy_guard = true\n", encoding="utf-8")
    monkeypatch.setenv(presets.USER_CONFIG_ENV_VAR, str(overlay))
    presets.set_user_committee(["warren_buffett", "li_lu", "howard_marks"])
    assert presets.load_config().get("committee") == [
        "warren_buffett",
        "li_lu",
        "howard_marks",
    ]
    text = overlay.read_text(encoding="utf-8")
    assert "[auth.openai]" in text
    assert "policy_guard = true" in text
    assert not overlay.with_suffix(".tmp").exists()  # staged tmp cleaned by os.replace


@pytest.mark.parametrize(
    "names",
    [
        ["warren_buffett"],                              # too few
        ["a", "b", "c", "d", "e", "f", "g"],             # too many
        ["warren_buffett", "warren_buffett"],            # duplicate
        ["warren_buffett", " "],                         # blank
    ],
)
def test_rejects_invalid_shapes(tmp_path, monkeypatch, names):
    monkeypatch.setenv(presets.USER_CONFIG_ENV_VAR, str(tmp_path / "tinyic.toml"))
    with pytest.raises(PresetError):
        presets.set_user_committee(names)


def test_resolve_personas_picks_up_overlay(tmp_path, monkeypatch):
    monkeypatch.setenv(presets.USER_CONFIG_ENV_VAR, str(tmp_path / "tinyic.toml"))
    presets.set_user_committee(["warren_buffett", "li_lu"])
    from tinyic.headless import resolve_personas

    assert resolve_personas(None) == ["warren_buffett", "li_lu"]
