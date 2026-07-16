"""Regression tests for strict JSON-array validation in persona artifacts."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tinyic.personas.factory import (
    DossierSynthesisResponse,
    Evidence,
    PersonaFactory,
    PersonaSynthesisResponse,
    ResearchRequest,
    SchemaValidationError,
    SearchResponse,
    validate_agent_spec,
)


_FIXTURES = Path(__file__).parent / "fixtures" / "persona_factory"


def _specification() -> dict:
    return json.loads(
        (_FIXTURES / "ada_value.agent.json").read_text(encoding="utf-8")
    )


def _evidence() -> tuple[Evidence, ...]:
    records = json.loads(
        (_FIXTURES / "evidence.json").read_text(encoding="utf-8")
    )
    return tuple(Evidence(**record) for record in records)


@pytest.mark.parametrize(
    "path",
    [
        ("persona", "beliefs"),
        ("persona", "skills"),
        ("persona", "personality", "traits"),
        ("persona", "preferences", "interests"),
        ("persona", "preferences", "likes"),
        ("persona", "preferences", "dislikes"),
        ("persona", "behaviors", "general"),
        ("persona", "other_facts"),
        ("tinyic", "decision_checklist"),
        ("tinyic", "signal_rules"),
        ("tinyic", "red_flags"),
        ("tinyic", "famous_quotes"),
        ("tinyic", "sources"),
    ],
)
def test_manual_schema_requires_json_lists_for_every_array(path: tuple[str, ...]) -> None:
    specification = _specification()
    parent = specification
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = tuple(parent[path[-1]])

    with pytest.raises(SchemaValidationError) as error:
        validate_agent_spec(specification)

    assert ".".join(path) in str(error.value)


class _TupleBackedPersonaBackend:
    model_ref = "openai/fake-research"
    provider = "openai"

    def __init__(self, field: str) -> None:
        self.field = field
        self.evidence = _evidence()
        self.search_index = 0

    def search(self, _request) -> SearchResponse:
        item = self.evidence[self.search_index]
        self.search_index += 1
        return SearchResponse((item,))

    def synthesize_dossier(self, _request) -> DossierSynthesisResponse:
        return DossierSynthesisResponse("Supported public-record summary. [1]")

    def synthesize_persona(self, _request) -> PersonaSynthesisResponse:
        specification = _specification()
        if self.field == "other_facts":
            values = specification["persona"]["other_facts"]
            specification["persona"]["other_facts"] = tuple(values)
        else:
            values = specification["tinyic"]["famous_quotes"]
            specification["tinyic"]["famous_quotes"] = tuple(values)
        return PersonaSynthesisResponse(specification)

    def verify(self, _request):
        raise AssertionError("schema-invalid model output must not reach verification")


@pytest.mark.parametrize("field", ["other_facts", "famous_quotes"])
def test_factory_rejects_tuple_backed_claims_before_artifact_writes(
    tmp_path: Path,
    field: str,
) -> None:
    writes: list[tuple] = []

    def record_write(*args, **kwargs) -> None:
        writes.append((args, kwargs))

    factory = PersonaFactory(
        _TupleBackedPersonaBackend(field),
        protected_slugs=(),
        clock=lambda: date(2026, 7, 14),
        writer=record_write,
    )

    with pytest.raises(SchemaValidationError) as error:
        factory.run(ResearchRequest("Ada Value", output_dir=tmp_path))

    expected_path = (
        "persona.other_facts"
        if field == "other_facts"
        else "tinyic.famous_quotes"
    )
    assert expected_path in str(error.value)
    assert writes == []
    assert not (tmp_path / "ada_value.agent.json").exists()
    assert not (tmp_path / "ada_value.dossier.md").exists()


@pytest.mark.parametrize("invalid", [True, False, 1.0, "1", None, [], {}])
def test_schema_version_requires_a_non_bool_integer_one(invalid) -> None:
    specification = _specification()
    specification["tinyic"]["schema_version"] = invalid

    with pytest.raises(SchemaValidationError) as error:
        validate_agent_spec(specification)

    assert "tinyic.schema_version must equal 1" in error.value.issues


@pytest.mark.parametrize(
    "invalid",
    [[], {}, ["balanced"], {"balanced": True}],
    ids=["empty-list", "empty-object", "nonempty-list", "nonempty-object"],
)
def test_unhashable_enum_values_accumulate_schema_issues(invalid) -> None:
    specification = _specification()
    specification["tinyic"]["temperament"] = invalid
    specification["tinyic"]["sources"][0]["type"] = invalid
    specification["tinyic"]["generation"]["quality"] = invalid

    with pytest.raises(SchemaValidationError) as error:
        validate_agent_spec(specification)

    message = str(error.value)
    assert "tinyic.temperament" in message
    assert "tinyic.sources[0].type" in message
    assert "tinyic.generation.quality" in message


_UNKNOWN_OBJECT_PATHS = (
    ((), "root"),
    (("persona",), "persona"),
    (("persona", "occupation"), "persona.occupation"),
    (("persona", "personality"), "persona.personality"),
    (("persona", "preferences"), "persona.preferences"),
    (("persona", "behaviors"), "persona.behaviors"),
    (("tinyic",), "tinyic"),
    (("tinyic", "sources", 0), "tinyic.sources[0]"),
    (("tinyic", "famous_quotes", 0), "tinyic.famous_quotes[0]"),
    (("tinyic", "generation"), "tinyic.generation"),
)


@pytest.mark.parametrize(("path", "diagnostic_path"), _UNKNOWN_OBJECT_PATHS)
def test_unknown_field_names_are_redacted_at_every_object_level(
    path: tuple[str | int, ...],
    diagnostic_path: str,
) -> None:
    specification = _specification()
    target = specification
    for part in path:
        target = target[part]
    secret_key = (
        "TEST_SECRET_DO_NOT_ECHO\nAuthorization: Bearer PRIVATE_VALUE_"
        + "x" * 10_000
    )
    target[secret_key] = "forbidden"

    with pytest.raises(SchemaValidationError) as error:
        validate_agent_spec(specification)

    message = str(error.value)
    assert f"{diagnostic_path}.<redacted> is not allowed" in message
    assert "TEST_SECRET_DO_NOT_ECHO" not in message
    assert "PRIVATE_VALUE" not in message
    assert "\n" not in message
    assert len(message) < 500


def test_unknown_field_diagnostics_are_bounded_per_object() -> None:
    specification = _specification()
    specification["persona"].update(
        {
            f"provider_controlled_{index}_{'x' * 100}": "forbidden"
            for index in range(500)
        }
    )
    specification["persona"][999] = "forbidden"

    with pytest.raises(SchemaValidationError) as error:
        validate_agent_spec(specification)

    assert error.value.issues == ("persona.<redacted> is not allowed",)
