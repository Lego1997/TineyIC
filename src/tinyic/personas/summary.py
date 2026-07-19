"""File-derived persona metadata summaries (CLI + studio).

Moved verbatim out of ``persona_cli`` so ``persona list``/``show`` and the
studio API render the same per-persona metadata from one implementation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "dossier_candidate",
    "persona_summary",
    "registry_summaries",
    "source_count",
]


def source_count(value: Any) -> int:
    """Count source entries across the list/dict shapes personas use."""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return sum(source_count(item) for item in value.values())
    return 0


def dossier_candidate(agent_path: Path, slug: str) -> Path:
    """The dossier path paired with one agent file."""
    return agent_path.with_name(f"{slug}.dossier.md")


def persona_summary(slug: str, origin: str, path: Path) -> dict[str, Any]:
    """Read one agent file's display metadata without loading the persona."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("persona"), dict):
        raise ValueError("agent file does not contain a persona object")
    persona = raw["persona"]
    tinyic = raw.get("tinyic") if isinstance(raw.get("tinyic"), dict) else {}
    generation = (
        tinyic.get("generation")
        if isinstance(tinyic.get("generation"), dict)
        else {}
    )
    name = str(persona.get("name") or "").strip()
    if not name:
        raise ValueError("agent file does not contain a persona name")
    dossier = dossier_candidate(path, slug)
    sources = tinyic.get("sources")
    return {
        "slug": slug,
        "name": name,
        "epithet": str(tinyic.get("epithet") or "").strip() or None,
        "temperament": (
            str(tinyic.get("temperament") or raw.get("temperament") or "").strip()
            or None
        ),
        "philosophy_hook": (
            str(tinyic.get("philosophy_hook") or "").strip() or None
        ),
        "origin": origin,
        "source_count": source_count(sources),
        "quality": str(generation.get("quality") or "").strip() or None,
        "model_ref": str(generation.get("model_ref") or "").strip() or None,
        "generated_date": str(generation.get("date") or "").strip() or None,
        "agent_path": str(path),
        "dossier_path": str(dossier) if dossier.is_file() else None,
    }


def registry_summaries(registry_module: Any) -> list[dict[str, Any]]:
    """Summaries for the whole layered registry, stable-sorted by slug."""
    entries = registry_module.list_personas(with_origin=True)
    summaries: list[dict[str, Any]] = []
    for entry in entries:
        summaries.append(
            persona_summary(
                str(entry["slug"]),
                str(entry["origin"]),
                Path(str(entry["path"])),
            )
        )
    return summaries
