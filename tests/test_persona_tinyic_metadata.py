"""Regression pins for v2.2's file-sourced TinyIC persona metadata."""

from __future__ import annotations

import json

from tinyic.personas.registry import PERSONA_REGISTRY, load_persona


# Copied verbatim from the pre-v2.2 prompts.PHILOSOPHY_HOOKS dictionary before
# that centralized name lookup was removed.
LEGACY_HOOKS = {
    "warren_buffett": (
        "You evaluate businesses based on durable competitive moats "
        "and owner earnings -- not market sentiment."
    ),
    "charlie_munger": (
        "You apply mental models from multiple disciplines and look for "
        "businesses so good an idiot could run them."
    ),
    "benjamin_graham": (
        "You demand quantitative margin of safety -- intrinsic value "
        "backed by hard numbers, not stories."
    ),
    "peter_lynch": (
        "You find investments in everyday life -- growth at a reasonable "
        "price, not abstract financial engineering."
    ),
    "howard_marks": (
        "You focus on where we stand in the cycle, risk/reward asymmetry, "
        "and second-level thinking."
    ),
    "li_lu": (
        "You seek companies with enduring competitive advantages in large "
        "addressable markets, especially in Asia."
    ),
}


def test_builtin_hooks_are_byte_identical_and_load_from_files():
    for slug, expected in LEGACY_HOOKS.items():
        raw = json.loads(PERSONA_REGISTRY[slug].read_text(encoding="utf-8"))
        assert raw["tinyic"]["philosophy_hook"] == expected
        persona = load_persona(slug)
        assert persona.philosophy_hook == expected
        assert persona.epithet
        assert persona.tinyic["schema_version"] == 1


def test_legacy_temperament_is_shadowed_by_tinyic_block(tmp_path):
    from tinyic.personas.base import InvestorPersona

    path = tmp_path / "metadata.agent.json"
    path.write_text(
        json.dumps(
            {
                "type": "TinyPerson",
                "temperament": "conciliatory",
                "tinyic": {
                    "schema_version": 1,
                    "temperament": "contrarian",
                    "epithet": "Test investor",
                    "philosophy_hook": "Demand evidence.",
                    "sources": [],
                },
                "persona": {"name": "Metadata Test", "beliefs": []},
            }
        ),
        encoding="utf-8",
    )
    persona = InvestorPersona("Metadata Test", str(path))
    assert persona.temperament == "contrarian"
    assert persona.philosophy_hook == "Demand evidence."


def test_central_philosophy_hook_dictionary_is_gone():
    from tinyic.debate import prompts

    assert not hasattr(prompts, "PHILOSOPHY_HOOKS")
