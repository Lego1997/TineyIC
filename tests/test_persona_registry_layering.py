"""User persona discovery layers beneath the protected built-in six."""

from __future__ import annotations

import json
import os
from pathlib import Path

from tinyic.personas.registry import (
    PERSONA_REGISTRY,
    list_personas,
    load_persona,
    registry_snapshot,
)


def _write_persona(slug: str, name: str) -> Path:
    root = Path(os.environ["TINYIC_PERSONAS_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent / "fixtures" / "test_investor.agent.json"
    spec = json.loads(source.read_text(encoding="utf-8"))
    spec["persona"]["name"] = name
    spec["tinyic"] = {
        "schema_version": 1,
        "epithet": "A test user investor",
        "philosophy_hook": "Demand evidence.",
        "temperament": "balanced",
        "sources": [],
    }
    path = root / f"{slug}.agent.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def test_user_persona_is_discovered_loaded_and_origin_tagged():
    path = _write_persona("jane_doe", "Jane Doe")
    assert PERSONA_REGISTRY["jane_doe"] == path
    assert "jane_doe" in list_personas()
    persona = load_persona("jane_doe")
    assert persona.name == "Jane Doe"
    records = {item["slug"]: item for item in list_personas(with_origin=True)}
    assert records["jane_doe"]["origin"] == "user"
    assert records["warren_buffett"]["origin"] == "built_in"
    persona.session.unregister_agent(persona)
    persona.session.close()


def test_builtin_shadow_is_ignored_with_warning(capsys):
    _write_persona("warren_buffett", "Imposter")
    registry = registry_snapshot()
    assert registry["warren_buffett"].parent.name == "configs"
    assert "built-in 'warren_buffett' is protected" in capsys.readouterr().err


def test_malformed_user_file_is_ignored(capsys):
    root = Path(os.environ["TINYIC_PERSONAS_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "broken.agent.json").write_text("{not-json", encoding="utf-8")
    assert "broken" not in registry_snapshot()
    assert "invalid user persona file" in capsys.readouterr().err
