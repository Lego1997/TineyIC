"""Parsing rules for the optional user-overlay committee list."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tinyic.models.presets import PresetError, load_config
from tinyic.headless import resolve_personas


def _overlay(text: str) -> None:
    path = Path(os.environ["TINYIC_USER_CONFIG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_overlay_committee_is_returned_in_stable_order():
    _overlay('committee = ["warren_buffett", "howard_marks"]\n')
    config = load_config("tinyic.toml")
    assert config["committee"] == ["warren_buffett", "howard_marks"]
    assert resolve_personas(None, config_path="tinyic.toml") == [
        "warren_buffett",
        "howard_marks",
    ]
    assert resolve_personas(
        "charlie_munger,li_lu", config_path="tinyic.toml"
    ) == ["charlie_munger", "li_lu"]


@pytest.mark.parametrize(
    "value",
    [
        '["warren_buffett"]',
        '["a", "b", "c", "d", "e", "f", "g"]',
        '["warren_buffett", "warren_buffett"]',
        '"warren_buffett"',
    ],
)
def test_overlay_committee_shape_is_validated(value):
    _overlay(f"committee = {value}\n")
    with pytest.raises(PresetError, match="committee"):
        load_config("tinyic.toml")
