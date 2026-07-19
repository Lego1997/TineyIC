"""Dependency-free validation for generated ``*.agent.json`` artifacts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from .url_safety import public_host_is_allowed, url_contains_sensitive_material


VALID_TEMPERAMENTS = frozenset({"conciliatory", "balanced", "contrarian"})
ROOT_KEYS = frozenset({"type", "persona", "tinyic"})
PERSONA_KEYS = frozenset(
    {
        "name",
        "occupation",
        "style",
        "personality",
        "beliefs",
        "skills",
        "preferences",
        "behaviors",
        "other_facts",
    }
)
OCCUPATION_KEYS = frozenset({"description", "organization", "title"})
PERSONALITY_KEYS = frozenset({"traits"})
BEHAVIOR_KEYS = frozenset({"general"})
PREFERENCE_KEYS = frozenset({"interests", "likes", "dislikes"})
TINYIC_KEYS = frozenset(
    {
        "schema_version",
        "epithet",
        "philosophy_hook",
        "temperament",
        "decision_checklist",
        "signal_rules",
        "red_flags",
        "famous_quotes",
        "sources",
        "generation",
    }
)
QUOTE_KEYS = frozenset({"text", "source"})
SOURCE_KEYS = frozenset({"title", "url", "type", "accessed"})
GENERATION_KEYS = frozenset(
    {
        "generated_by",
        "model_ref",
        "date",
        "search_calls",
        "quality",
        "disclaimer",
    }
)
# Unknown mapping keys originate in provider output and can contain credentials,
# control characters, or arbitrarily large strings.  Only these fixed,
# programmer-authored privacy-boundary labels are safe to reflect verbatim in
# an actionable diagnostic; every other name is redacted.
SAFE_UNKNOWN_FIELD_LABELS = frozenset(
    {
        "family",
        "medical_history",
        "private",
        "private_life",
        "private_notes",
        "unverified_blob",
    }
)

# Published for callers that want to expose or compile the Appendix-A schema.
AGENT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "persona", "tinyic"],
    "properties": {
        "type": {"const": "TinyPerson"},
        "persona": {
            "type": "object",
            "additionalProperties": False,
            "required": sorted(PERSONA_KEYS),
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "occupation": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["description"],
                    "properties": {
                        "description": {"type": "string", "minLength": 1},
                        "organization": {"type": "string", "minLength": 1},
                        "title": {"type": "string", "minLength": 1},
                    },
                },
                "style": {"type": "string", "minLength": 1},
                "personality": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["traits"],
                    "properties": {
                        "traits": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        }
                    },
                },
                "beliefs": {
                    "type": "array",
                    "maxItems": 15,
                    "items": {"type": "string", "minLength": 1},
                },
                "skills": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "preferences": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": sorted(PREFERENCE_KEYS),
                    "properties": {
                        key: {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        }
                        for key in sorted(PREFERENCE_KEYS)
                    },
                },
                "behaviors": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["general"],
                    "properties": {
                        "general": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        }
                    },
                },
                "other_facts": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
            },
        },
        "tinyic": {
            "type": "object",
            "additionalProperties": False,
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
                "generation",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "epithet": {"type": "string", "minLength": 1, "maxLength": 80},
                "philosophy_hook": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 240,
                },
                "temperament": {"enum": sorted(VALID_TEMPERAMENTS)},
                "decision_checklist": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 8,
                    "items": {"type": "string", "minLength": 1},
                },
                "signal_rules": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string", "minLength": 1},
                },
                "red_flags": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string", "minLength": 1},
                },
                "famous_quotes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": sorted(QUOTE_KEYS),
                        "properties": {
                            "text": {"type": "string", "minLength": 1},
                            "source": {"type": "integer", "minimum": 1},
                        },
                    },
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": sorted(SOURCE_KEYS),
                        "properties": {
                            "title": {"type": "string", "minLength": 1},
                            "url": {"type": "string", "minLength": 1},
                            "type": {"enum": ["primary", "secondary"]},
                            "accessed": {
                                "type": "string",
                                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                            },
                        },
                    },
                },
                "generation": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": sorted(GENERATION_KEYS),
                    "properties": {
                        "generated_by": {
                            "const": "tinyic persona research"
                        },
                        "model_ref": {"type": "string", "minLength": 1},
                        "date": {
                            "type": "string",
                            "pattern": r"^\d{4}-\d{2}-\d{2}$",
                        },
                        "search_calls": {"type": "integer", "minimum": 0},
                        "quality": {"enum": ["normal", "thin"]},
                        "disclaimer": {"type": "string", "minLength": 1},
                    },
                },
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
    # JSON Schema's ``array`` type is represented by ``list`` after decoding.
    # Accepting arbitrary Python sequences here creates a mismatch with the
    # published schema and, more importantly, lets tuple-backed model output
    # bypass list-only filtering later in the factory pipeline.
    return isinstance(value, list)


def _reject_unknown(
    value: Mapping[str, Any],
    path: str,
    allowed: frozenset[str],
    issues: list[str],
) -> None:
    safe_labels: set[str] = set()
    has_redacted_label = False
    for key in value:
        if isinstance(key, str) and key in allowed:
            continue
        if isinstance(key, str) and key in SAFE_UNKNOWN_FIELD_LABELS:
            safe_labels.add(key)
        else:
            has_redacted_label = True

    # Deduplicate and bound diagnostics per object.  Crucially, arbitrary keys
    # are never formatted, converted to strings, or otherwise echoed.
    for key in sorted(safe_labels):
        issues.append(f"{path}.{key} is not allowed")
    if has_redacted_label:
        issues.append(f"{path}.<redacted> is not allowed")


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
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() in {"http", "https"}
        and bool(parsed.hostname)
        and public_host_is_allowed(parsed.hostname)
        and not (parsed.username or parsed.password)
        and not url_contains_sensitive_material(value)
    )


def _valid_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", value
    ) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _sources_issues(sources: Any, issues: list[str]) -> list[Any]:
    """Validate ``tinyic.sources`` entries; return the coerced list for quoting."""
    if not _is_sequence(sources):
        issues.append("tinyic.sources must be an array")
        return []
    for index, source in enumerate(sources):
        path = f"tinyic.sources[{index}]"
        if not isinstance(source, Mapping):
            issues.append(f"{path} must be an object")
            continue
        _reject_unknown(source, path, SOURCE_KEYS, issues)
        _nonempty_string(source.get("title"), f"{path}.title", issues)
        if not _valid_public_url(source.get("url")):
            issues.append(f"{path}.url must be an http(s) URL without credentials")
        source_type = source.get("type")
        if not isinstance(source_type, str) or source_type not in {
            "primary",
            "secondary",
        }:
            issues.append(f"{path}.type must be primary or secondary")
        if not _valid_iso_date(source.get("accessed")):
            issues.append(f"{path}.accessed must be YYYY-MM-DD")
    return sources


def _quotes_issues(quotes: Any, sources: list[Any], issues: list[str]) -> None:
    """Validate ``tinyic.famous_quotes`` against the coerced sources list."""
    if not _is_sequence(quotes):
        issues.append("tinyic.famous_quotes must be an array")
        return
    for index, quote in enumerate(quotes):
        path = f"tinyic.famous_quotes[{index}]"
        if not isinstance(quote, Mapping):
            issues.append(f"{path} must be an object")
            continue
        _reject_unknown(quote, path, QUOTE_KEYS, issues)
        _nonempty_string(quote.get("text"), f"{path}.text", issues)
        source = quote.get("source")
        if not isinstance(source, int) or isinstance(source, bool) or not 1 <= source <= len(sources):
            issues.append(f"{path}.source must be a 1-based index into tinyic.sources")


def _generation_issues(generation: Any, issues: list[str]) -> None:
    """Validate the factory ``tinyic.generation`` provenance block."""
    if not isinstance(generation, Mapping):
        issues.append("tinyic.generation must be an object for generated personas")
        generation = {}
    _reject_unknown(generation, "tinyic.generation", GENERATION_KEYS, issues)
    if generation.get("generated_by") != "tinyic persona research":
        issues.append("tinyic.generation.generated_by is invalid")
    _nonempty_string(generation.get("model_ref"), "tinyic.generation.model_ref", issues)
    if not _valid_iso_date(generation.get("date")):
        issues.append("tinyic.generation.date must be YYYY-MM-DD")
    search_calls = generation.get("search_calls")
    if not isinstance(search_calls, int) or isinstance(search_calls, bool) or search_calls < 0:
        issues.append("tinyic.generation.search_calls must be a non-negative integer")
    quality = generation.get("quality")
    if not isinstance(quality, str) or quality not in {"normal", "thin"}:
        issues.append("tinyic.generation.quality must be normal or thin")
    _nonempty_string(
        generation.get("disclaimer"), "tinyic.generation.disclaimer", issues
    )


def validate_agent_spec(specification: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate Appendix A and generated TinyTroupe persona content.

    Returns the original mapping for fluent use and raises one error containing
    all discovered paths.  No transitive JSON-schema dependency is required.
    """
    if not isinstance(specification, Mapping):
        raise SchemaValidationError(("root must be an object",))
    issues: list[str] = []
    _reject_unknown(specification, "root", ROOT_KEYS, issues)
    if specification.get("type") != "TinyPerson":
        issues.append("type must equal 'TinyPerson'")

    persona = specification.get("persona")
    if not isinstance(persona, Mapping):
        issues.append("persona must be an object")
        persona = {}
    # Generated artifacts model an investment method, not a private life. The
    # strict allowlist enforces that boundary even when a model ignores it.
    _reject_unknown(persona, "persona", PERSONA_KEYS, issues)
    _nonempty_string(persona.get("name"), "persona.name", issues)
    _nonempty_string(persona.get("style"), "persona.style", issues)
    _strings(persona.get("beliefs"), "persona.beliefs", issues, maximum=15)
    _strings(persona.get("skills"), "persona.skills", issues)
    _strings(persona.get("other_facts"), "persona.other_facts", issues)

    occupation = persona.get("occupation")
    if not isinstance(occupation, Mapping):
        issues.append("persona.occupation must be an object")
        occupation = {}
    _reject_unknown(
        occupation, "persona.occupation", OCCUPATION_KEYS, issues
    )
    _nonempty_string(occupation.get("description"), "persona.occupation.description", issues)
    for key in ("organization", "title"):
        if key in occupation:
            _nonempty_string(
                occupation.get(key), f"persona.occupation.{key}", issues
            )
    personality = persona.get("personality")
    if not isinstance(personality, Mapping):
        issues.append("persona.personality must be an object")
        personality = {}
    _reject_unknown(
        personality, "persona.personality", PERSONALITY_KEYS, issues
    )
    _strings(personality.get("traits"), "persona.personality.traits", issues)
    behaviors = persona.get("behaviors")
    if not isinstance(behaviors, Mapping):
        issues.append("persona.behaviors must be an object")
        behaviors = {}
    _reject_unknown(behaviors, "persona.behaviors", BEHAVIOR_KEYS, issues)
    _strings(behaviors.get("general"), "persona.behaviors.general", issues)
    preferences = persona.get("preferences")
    if not isinstance(preferences, Mapping):
        issues.append("persona.preferences must be an object")
        preferences = {}
    _reject_unknown(
        preferences, "persona.preferences", PREFERENCE_KEYS, issues
    )
    for key in ("interests", "likes", "dislikes"):
        _strings(preferences.get(key), f"persona.preferences.{key}", issues)

    tinyic = specification.get("tinyic")
    if not isinstance(tinyic, Mapping):
        issues.append("tinyic must be an object")
        tinyic = {}
    _reject_unknown(tinyic, "tinyic", TINYIC_KEYS, issues)
    schema_version = tinyic.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        issues.append("tinyic.schema_version must equal 1")
    epithet = _nonempty_string(tinyic.get("epithet"), "tinyic.epithet", issues, maximum=80)
    _nonempty_string(
        tinyic.get("philosophy_hook"), "tinyic.philosophy_hook", issues, maximum=240
    )
    temperament = tinyic.get("temperament")
    if not isinstance(temperament, str) or temperament not in VALID_TEMPERAMENTS:
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

    sources = _sources_issues(tinyic.get("sources"), issues)
    _quotes_issues(tinyic.get("famous_quotes"), sources, issues)

    if occupation.get("description") != epithet:
        issues.append("persona.occupation.description must equal tinyic.epithet")

    _generation_issues(tinyic.get("generation"), issues)

    if issues:
        raise SchemaValidationError(issues)
    return specification


def validate_user_agent_spec(specification: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a hand-maintained persona file (the relaxed registry contract).

    Factory-generated artifacts satisfy :func:`validate_agent_spec`.  Hand
    files — including copies of the built-in six, which predate the generated
    schema — are held to the shape the registry and orchestrator actually
    consume, plus the ``tinyic`` block's per-field constraints where those
    fields are present.  Unknown keys are tolerated: legacy fields ride along.
    """
    if not isinstance(specification, Mapping):
        raise SchemaValidationError(("root must be an object",))
    issues: list[str] = []
    if specification.get("type") != "TinyPerson":
        issues.append("type must equal 'TinyPerson'")
    persona = specification.get("persona")
    if not isinstance(persona, Mapping):
        issues.append("persona must be an object")
    else:
        _nonempty_string(persona.get("name"), "persona.name", issues)

    tinyic = specification.get("tinyic")
    if tinyic is None:
        tinyic = {}
    if not isinstance(tinyic, Mapping):
        issues.append("tinyic must be an object when present")
        tinyic = {}

    schema_version = tinyic.get("schema_version")
    if schema_version is not None and schema_version != 1:
        issues.append("tinyic.schema_version must equal 1")
    if "epithet" in tinyic:
        _nonempty_string(tinyic.get("epithet"), "tinyic.epithet", issues, maximum=80)
    if "philosophy_hook" in tinyic:
        _nonempty_string(
            tinyic.get("philosophy_hook"), "tinyic.philosophy_hook", issues, maximum=240
        )
    temperament = tinyic.get("temperament")
    if temperament is not None and temperament not in VALID_TEMPERAMENTS:
        issues.append(
            "tinyic.temperament must be conciliatory, balanced, or contrarian"
        )
    for key in ("decision_checklist", "signal_rules", "red_flags"):
        if key in tinyic:
            _strings(tinyic.get(key), f"tinyic.{key}", issues)
    sources = tinyic.get("sources")
    if sources is not None:
        sources = _sources_issues(sources, issues)
    else:
        sources = []
    if "famous_quotes" in tinyic:
        _quotes_issues(tinyic.get("famous_quotes"), sources, issues)
    if "generation" in tinyic:
        # A hand file that carries provenance honors the strict block.
        _generation_issues(tinyic.get("generation"), issues)

    if issues:
        raise SchemaValidationError(issues)
    return specification


__all__ = [
    "AGENT_SCHEMA",
    "SchemaValidationError",
    "VALID_TEMPERAMENTS",
    "validate_agent_spec",
    "validate_user_agent_spec",
]
