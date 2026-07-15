"""Layered registry for built-in and user-created investor personas."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

from tinytroupe.session import Session


CONFIGS_DIR = Path(__file__).parent / "configs"
PERSONAS_DIR_ENV_VAR = "TINYIC_PERSONAS_DIR"

BUILTIN_PERSONAS: dict[str, Path] = {
    "benjamin_graham": CONFIGS_DIR / "benjamin_graham.agent.json",
    "warren_buffett": CONFIGS_DIR / "warren_buffett.agent.json",
    "charlie_munger": CONFIGS_DIR / "charlie_munger.agent.json",
    "peter_lynch": CONFIGS_DIR / "peter_lynch.agent.json",
    "howard_marks": CONFIGS_DIR / "howard_marks.agent.json",
    "li_lu": CONFIGS_DIR / "li_lu.agent.json",
}


def personas_dir() -> Path:
    """Return ``$TINYIC_PERSONAS_DIR`` or the per-user persona directory."""
    override = os.environ.get(PERSONAS_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".tinyic" / "personas"


def _slug_from_path(path: Path) -> str:
    return path.name.removesuffix(".agent.json")


def _valid_user_file(path: Path) -> bool:
    """Reject malformed discovery entries early without exposing file content."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(raw, dict)
        and isinstance(raw.get("persona"), dict)
        and bool(str(raw["persona"].get("name") or "").strip())
    )


def registry_snapshot(*, warn: bool = True) -> dict[str, Path]:
    """Return built-ins plus valid ``*.agent.json`` user files.

    Built-ins always win. A colliding or malformed user file is ignored with a
    concise STDERR warning; file content is never echoed.
    """
    layered = dict(BUILTIN_PERSONAS)
    root = personas_dir()
    if not root.is_dir():
        return layered
    for path in sorted(root.glob("*.agent.json"), key=lambda item: item.name):
        slug = _slug_from_path(path)
        if slug in BUILTIN_PERSONAS:
            if warn:
                print(
                    f"tinyic: ignoring user persona {path.name!r}; built-in {slug!r} is protected",
                    file=sys.stderr,
                )
            continue
        if not slug or not _valid_user_file(path):
            if warn:
                print(
                    f"tinyic: ignoring invalid user persona file {path.name!r}",
                    file=sys.stderr,
                )
            continue
        layered[slug] = path
    return layered


class _LayeredRegistry(Mapping[str, Path]):
    """Compatibility mapping whose reads reflect the current user directory."""

    def __getitem__(self, key: str) -> Path:
        return registry_snapshot()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(registry_snapshot())

    def __len__(self) -> int:
        return len(registry_snapshot())


PERSONA_REGISTRY: Mapping[str, Path] = _LayeredRegistry()


def load_persona(
    name: str,
    session: Session | None = None,
    *,
    semantic_consolidation: bool = True,
):
    """Load a built-in or discovered user persona by registry slug.

    ``semantic_consolidation`` is forwarded to the persona so a debate can
    disable episode consolidation up front when no OpenAI embedding credential
    is configured (TIC-007).
    """
    registry = registry_snapshot()
    if name not in registry:
        raise KeyError(f"Unknown persona '{name}'. Available: {list_personas()}")
    config_path = registry[name]
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    from tinyic.personas.base import InvestorPersona

    display_name = name.replace("_", " ").title()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        configured_name = str((raw.get("persona") or {}).get("name") or "").strip()
        if configured_name:
            display_name = configured_name
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass  # InvestorPersona raises the authoritative load error below.
    persona_session = session if session is not None else Session()
    return InvestorPersona(
        name=display_name,
        philosophy_config_path=str(config_path),
        session=persona_session,
        semantic_consolidation=semantic_consolidation,
    )


def list_personas(*, with_origin: bool = False):
    """Return stable-sorted slugs, optionally as origin-tagged records."""
    registry = registry_snapshot()
    names = sorted(registry)
    if not with_origin:
        return names
    return [
        {
            "slug": name,
            "origin": "built_in" if name in BUILTIN_PERSONAS else "user",
            "path": str(registry[name]),
        }
        for name in names
    ]


__all__ = [
    "BUILTIN_PERSONAS",
    "CONFIGS_DIR",
    "PERSONAS_DIR_ENV_VAR",
    "PERSONA_REGISTRY",
    "list_personas",
    "load_persona",
    "personas_dir",
    "registry_snapshot",
]
