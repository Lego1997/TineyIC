"""Persona registry for loading investor personas by name."""

from pathlib import Path


CONFIGS_DIR = Path(__file__).parent / "configs"

PERSONA_REGISTRY: dict[str, Path] = {
    "benjamin_graham": CONFIGS_DIR / "benjamin_graham.agent.json",
    "warren_buffett": CONFIGS_DIR / "warren_buffett.agent.json",
    "charlie_munger": CONFIGS_DIR / "charlie_munger.agent.json",
    "peter_lynch": CONFIGS_DIR / "peter_lynch.agent.json",
    "howard_marks": CONFIGS_DIR / "howard_marks.agent.json",
    "li_lu": CONFIGS_DIR / "li_lu.agent.json",
}


def load_persona(name: str):
    """Load an investor persona by registry name.

    Args:
        name: Snake_case registry key (e.g. "warren_buffett").

    Returns:
        InvestorPersona instance with philosophy config loaded.

    Raises:
        KeyError: If name is not in the registry.
        FileNotFoundError: If the config file does not exist.
    """
    if name not in PERSONA_REGISTRY:
        raise KeyError(
            f"Unknown persona '{name}'. Available: {list_personas()}"
        )
    config_path = PERSONA_REGISTRY[name]
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    # Lazy import to avoid circular imports
    from tinyic.personas.base import InvestorPersona

    display_name = name.replace("_", " ").title()
    return InvestorPersona(name=display_name, philosophy_config_path=str(config_path))


def list_personas() -> list[str]:
    """Return sorted list of all registered persona names."""
    return sorted(PERSONA_REGISTRY.keys())
