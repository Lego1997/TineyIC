"""Dependency-free validation for generated ``*.agent.json`` artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import urlsplit


VALID_TEMPERAMENTS = frozenset({"conciliatory", "balanced", "contrarian"})

# Published for callers that want to expose or compile the Appendix-A schema.
AGENT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["type", "persona", "tinyic"],
    "properties": {
        "type": {"const": "TinyPerson"},
        "persona": {"type": "object"},
        "tinyic": {
            "type": "object",
            "required": [
                "schema_version",
                "epithet",
                "philosophy_hook",
                "temperament",
                "decision_checklist",
                "signal_rules",
                "red_flags",
                "famous_quotes",
                "sources",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "epithet": {"type": "string", "maxLength": 80},
                "philosophy_hook": {"type": "string", "maxLength": 240},
                "temperament": {"enum": sorted(VALID_TEMPERAMENTS)},
                "decision_checklist": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 8,
                    "items": {"type": "string"},
                },
                "signal_rules": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string"},
                },
                "red_flags": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string"},
                },
                "famous_quotes": {"type": "array"},
                "sources": {"type": "array"},
                "generation": {"type": "object"},
            },
        },
    },
}


class SchemaValidationError(ValueError):
    """Raised only after collecting all actionable schema failures."""

    reason_code = "invalid_persona_schema"

    def __init__(self, issues: Sequence[str]):
        self.issues = tuple(issues)
        super().__init__("invalid persona schema: " + "; ".join(self.issues))


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _strings(
    value: Any,
    path: str,
    issues: list[str],
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> list[str]:
    if not _is_sequence(value):
        issues.append(f"{path} must be an array of strings")
        return []
    result = list(value)
    if len(result) < minimum:
        issues.append(f"{path} must contain at least {minimum} items")
    if maximum is not None and len(result) > maximum:
        issues.append(f"{path} must contain at most {maximum} items")
    for index, item in enumerate(result):
        if not isinstance(item, str) or not item.strip():
            issues.append(f"{path}[{index}] must be a non-empty string")
    return result


def _nonempty_string(
    value: Any, path: str, issues: list[str], *, maximum: int | None = None
) -> str:
    if not isinstance(value, str) or not value.strip():
        issues.append(f"{path} must be a non-empty string")
        return ""
    if maximum is not None and len(value) > maximum:
        issues.append(f"{path} must be at most {maximum} characters")
    return value


def _valid_public_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not (
        parsed.username or parsed.password
    )


def validate_agent_spec(specification: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate Appendix A and generated TinyTroupe persona content.

    Returns the original mapping for fluent use and raises one error containing
    all discovered paths.  No transitive JSON-schema dependency is required.
    """
    if not isinstance(specification, Mapping):
        raise SchemaValidationError(("root must be an object",))
    issues: list[str] = []
    if specification.get("type") != "TinyPerson":
        issues.append("type must equal 'TinyPerson'")

    persona = specification.get("persona")
    if not isinstance(persona, Mapping):
        issues.append("persona must be an object")
        persona = {}
    # Generated artifacts model an investment method, not a private life. The
    # provider prompt forbids these fields and validation makes that boundary
    # enforceable even when a model ignores the prompt.
    for forbidden in (
        "age",
        "family",
        "gender",
        "health",
        "relationships",
        "residence",
        "sexual_orientation",
    ):
        if forbidden in persona:
            issues.append(f"persona.{forbidden} is not allowed in generated personas")
    _nonempty_string(persona.get("name"), "persona.name", issues)
    _nonempty_string(persona.get("style"), "persona.style", issues)
    _strings(persona.get("beliefs"), "persona.beliefs", issues, maximum=15)
    _strings(persona.get("skills"), "persona.skills", issues)
    _strings(persona.get("other_facts"), "persona.other_facts", issues)

    occupation = persona.get("occupation")
    if not isinstance(occupation, Mapping):
        issues.append("persona.occupation must be an object")
        occupation = {}
    _nonempty_string(occupation.get("description"), "persona.occupation.description", issues)
    personality = persona.get("personality")
    if not isinstance(personality, Mapping):
        issues.append("persona.personality must be an object")
        personality = {}
    _strings(personality.get("traits"), "persona.personality.traits", issues)
    behaviors = persona.get("behaviors")
    if not isinstance(behaviors, Mapping):
        issues.append("persona.behaviors must be an object")
        behaviors = {}
    _strings(behaviors.get("general"), "persona.behaviors.general", issues)
    preferences = persona.get("preferences")
    if not isinstance(preferences, Mapping):
        issues.append("persona.preferences must be an object")
        preferences = {}
    for key in ("interests", "likes", "dislikes"):
        _strings(preferences.get(key), f"persona.preferences.{key}", issues)

    tinyic = specification.get("tinyic")
    if not isinstance(tinyic, Mapping):
        issues.append("tinyic must be an object")
        tinyic = {}
    if tinyic.get("schema_version") != 1:
        issues.append("tinyic.schema_version must equal 1")
    epithet = _nonempty_string(tinyic.get("epithet"), "tinyic.epithet", issues, maximum=80)
    _nonempty_string(
        tinyic.get("philosophy_hook"), "tinyic.philosophy_hook", issues, maximum=240
    )
    temperament = tinyic.get("temperament")
    if temperament not in VALID_TEMPERAMENTS:
        issues.append(
            "tinyic.temperament must be conciliatory, balanced, or contrarian"
        )
    _strings(
        tinyic.get("decision_checklist"),
        "tinyic.decision_checklist",
        issues,
        minimum=3,
        maximum=8,
    )
    _strings(tinyic.get("signal_rules"), "tinyic.signal_rules", issues, maximum=8)
    _strings(tinyic.get("red_flags"), "tinyic.red_flags", issues, maximum=8)

    sources = tinyic.get("sources")
    if not _is_sequence(sources):
        issues.append("tinyic.sources must be an array")
        sources = []
    for index, source in enumerate(sources):
        path = f"tinyic.sources[{index}]"
        if not isinstance(source, Mapping):
            issues.append(f"{path} must be an object")
            continue
        _nonempty_string(source.get("title"), f"{path}.title", issues)
        if not _valid_public_url(source.get("url")):
            issues.append(f"{path}.url must be an http(s) URL without credentials")
        if source.get("type") not in {"primary", "secondary"}:
            issues.append(f"{path}.type must be primary or secondary")
        accessed = source.get("accessed")
        try:
            date.fromisoformat(str(accessed))
        except (TypeError, ValueError):
            issues.append(f"{path}.accessed must be YYYY-MM-DD")

    quotes = tinyic.get("famous_quotes")
    if not _is_sequence(quotes):
        issues.append("tinyic.famous_quotes must be an array")
        quotes = []
    for index, quote in enumerate(quotes):
        path = f"tinyic.famous_quotes[{index}]"
        if not isinstance(quote, Mapping):
            issues.append(f"{path} must be an object")
            continue
        _nonempty_string(quote.get("text"), f"{path}.text", issues)
        source = quote.get("source")
        if not isinstance(source, int) or isinstance(source, bool) or not 1 <= source <= len(sources):
            issues.append(f"{path}.source must be a 1-based index into tinyic.sources")

    if occupation.get("description") != epithet:
        issues.append("persona.occupation.description must equal tinyic.epithet")

    generation = tinyic.get("generation")
    if not isinstance(generation, Mapping):
        issues.append("tinyic.generation must be an object for generated personas")
        generation = {}
    if generation.get("generated_by") != "tinyic persona research":
        issues.append("tinyic.generation.generated_by is invalid")
    _nonempty_string(generation.get("model_ref"), "tinyic.generation.model_ref", issues)
    try:
        date.fromisoformat(str(generation.get("date")))
    except (TypeError, ValueError):
        issues.append("tinyic.generation.date must be YYYY-MM-DD")
    search_calls = generation.get("search_calls")
    if not isinstance(search_calls, int) or isinstance(search_calls, bool) or search_calls < 0:
        issues.append("tinyic.generation.search_calls must be a non-negative integer")
    if generation.get("quality") not in {"normal", "thin"}:
        issues.append("tinyic.generation.quality must be normal or thin")
    _nonempty_string(
        generation.get("disclaimer"), "tinyic.generation.disclaimer", issues
    )

    if issues:
        raise SchemaValidationError(issues)
    return specification


__all__ = [
    "AGENT_SCHEMA",
    "SchemaValidationError",
    "VALID_TEMPERAMENTS",
    "validate_agent_spec",
]
