"""Offline contract tests for the cited persona factory."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from tinyic.personas.factory import (
    BuiltinPersonaCollisionError,
    CallUsage,
    DossierSynthesisResponse,
    Evidence,
    EvidenceLedger,
    InsufficientSourcesError,
    LOW_SOURCE_WARNING,
    PersonaCollisionError,
    PersonaFactory,
    PersonaSynthesisResponse,
    ResearchRequest,
    SchemaValidationError,
    SearchResponse,
    VerificationResponse,
    canonical_seeds,
    plan_queries,
    validate_agent_spec,
    write_artifact_pair,
)
from tinyic.personas.registry import load_persona
from tinytroupe.agent import TinyPerson


def _evidence(count: int = 6) -> tuple[Evidence, ...]:
    hosts = (
        "letters.example",
        "archive.example",
        "regulator.example",
        "university.example",
        "newspaper.example",
        "journal.example",
    )
    result = []
    for index, host in enumerate(hosts[:count], 1):
        excerpt = (
            "Buy only with a margin of safety and demand evidence before conviction."
            if index == 1
            else f"Public record excerpt {index} documents investment method and risk discipline."
        )
        result.append(
            Evidence(
                f"https://{host}/source-{index}",
                f"Source {index}",
                excerpt,
                source_type="primary" if index == 1 else "secondary",
            )
        )
    return tuple(result)


def _valid_spec(request, *, include_bad_quote: bool = False):
    quotes = [
        {
            "text": "Buy only with a margin of safety",
            "source": 1,
        }
    ]
    if include_bad_quote:
        quotes.append({"text": "This quote never appeared", "source": 1})
    epithet = "Evidence-led public-market investor"
    return {
        "type": "TinyPerson",
        "persona": {
            "name": request.investor_name,
            "occupation": {"description": epithet},
            "style": "Measured and evidence-led, with explicit attention to downside.",
            "personality": {"traits": ["Patient", "Risk-conscious"]},
            "beliefs": ["I require a margin of safety before investing."],
            "skills": ["Public-record investment analysis"],
            "preferences": {
                "interests": ["Business quality"],
                "likes": ["Evidence-backed valuation"],
                "dislikes": ["Unsupported certainty"],
            },
            "behaviors": {
                "general": ["Tests the downside case before discussing upside."]
            },
            "other_facts": ["Uses public evidence to explain conclusions."],
        },
        "tinyic": {
            "schema_version": 1,
            "epithet": epithet,
            "philosophy_hook": "Demand evidence, protect the downside, and insist on a margin of safety.",
            "temperament": "balanced",
            "decision_checklist": [
                "Understand the business.",
                "Estimate downside value.",
                "Demand a margin of safety.",
            ],
            "signal_rules": ["Price below conservative value -> investigate."],
            "red_flags": ["Unsupported management claims"],
            "famous_quotes": quotes,
            "sources": [dict(item) for item in request.source_records],
            "generation": dict(request.generation),
        },
    }


class FakeBackend:
    model_ref = "openai/fake-research"
    provider = "openai"

    def __init__(
        self,
        evidence: tuple[Evidence, ...],
        *,
        bad_quote: bool = False,
        fail_at: str | None = None,
    ) -> None:
        self.evidence = evidence
        self.bad_quote = bad_quote
        self.fail_at = fail_at
        self.search_index = 0
        self.dossier_requests = []
        self.persona_requests = []
        self.verify_requests = []

    def search(self, request):
        if self.fail_at == "search":
            raise RuntimeError("search failed")
        item = self.evidence[self.search_index % len(self.evidence)]
        self.search_index += 1
        return SearchResponse(
            (item,),
            CallUsage("search", self.model_ref, 10, 2, cost_usd=0.001),
        )

    def synthesize_dossier(self, request):
        if self.fail_at == "dossier":
            raise RuntimeError("dossier failed")
        self.dossier_requests.append(request)
        if request.section_key == "voice":
            text = 'The public voice emphasizes "Buy only with a margin of safety". [1]'
            if self.bad_quote:
                text += '\n\nThe investor said "This quote never appeared". [1]'
        else:
            text = f"The cited public record supports this {request.section_title.casefold()} summary. [1]"
        return DossierSynthesisResponse(
            text,
            CallUsage(f"dossier:{request.section_key}", self.model_ref, 20, 8, cost_usd=0.002),
        )

    def synthesize_persona(self, request):
        if self.fail_at == "persona":
            raise RuntimeError("persona failed")
        self.persona_requests.append(request)
        return PersonaSynthesisResponse(
            _valid_spec(request, include_bad_quote=self.bad_quote),
            CallUsage("persona", self.model_ref, 40, 20, cost_usd=0.003),
        )

    def verify(self, request):
        if self.fail_at == "verify":
            raise RuntimeError("verify failed")
        self.verify_requests.append(request)
        supported = {
            claim.claim_id: claim.citations or (1,)
            for claim in request.claims
        }
        return VerificationResponse(
            supported,
            CallUsage("verify", self.model_ref, 30, 5, cost_usd=0.002),
        )


@pytest.fixture(autouse=True)
def _clear_tinytroupe_registry():
    yield
    TinyPerson.all_agents.clear()


def _factory(backend, **kwargs):
    return PersonaFactory(
        backend,
        protected_slugs=(),
        clock=lambda: date(2026, 7, 14),
        **kwargs,
    )


def test_query_plan_has_six_angles_and_canonical_seed_hints():
    plan = plan_queries("Warren Buffett")
    assert [query.angle for query in plan.queries] == [
        "philosophy",
        "decision_process",
        "risk",
        "track_record",
        "voice",
        "criticism",
    ]
    assert canonical_seeds("Warren Buffett")[0].url.endswith("letters/letters.html")
    assert all(query.seed_urls for query in plan.queries)
    assert "Warren Buffett" in plan.queries[0].text


def test_request_defaults_to_environment_persona_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYIC_PERSONAS_DIR", str(tmp_path))
    result = _factory(FakeBackend(_evidence())).run(ResearchRequest("Default Dir"))
    assert result.agent_path.parent == tmp_path
    assert result.dossier_path.parent == tmp_path


def test_ledger_deduplicates_canonical_urls_and_keeps_richer_primary_entry():
    ledger = EvidenceLedger()
    first = Evidence(
        "https://Example.com/article/?utm_source=test#part",
        "First",
        "short excerpt",
    )
    second = Evidence(
        "https://example.com/article",
        "Second",
        "a much richer excerpt from the same public page",
        source_type="primary",
    )
    assert ledger.add(first) == 1
    assert ledger.add(second) == 1
    assert len(ledger.items) == 1
    assert ledger.items[0].title == "Second"
    assert ledger.items[0].source_type == "primary"


def test_quality_gate_counts_subdomains_as_one_independent_domain(tmp_path):
    evidence = (
        Evidence("https://a.publisher.example/article", "A", "Excerpt A"),
        Evidence("https://b.publisher.example/other", "B", "Excerpt B"),
    )
    backend = FakeBackend(evidence)
    with pytest.raises(InsufficientSourcesError) as error:
        _factory(backend).run(ResearchRequest("Subdomain Person", tmp_path))
    assert error.value.domain_count == 1


def test_end_to_end_writes_cited_schema_valid_loadable_artifacts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TINYIC_PERSONAS_DIR", str(tmp_path))
    backend = FakeBackend(_evidence())
    result = _factory(backend).run(
        ResearchRequest("Ada Value", tmp_path, max_searches=6)
    )

    assert result.quality == "normal"
    assert result.source_count == 6
    assert result.domain_count == 6
    assert result.agent_path.exists() and result.dossier_path.exists()
    specification = json.loads(result.agent_path.read_text())
    validate_agent_spec(specification)
    assert specification["tinyic"]["generation"]["disclaimer"]
    assert specification["tinyic"]["generation"]["quality"] == "normal"
    assert len(specification["tinyic"]["sources"]) == 6
    dossier = result.dossier_path.read_text()
    assert "**Disclaimer:**" in dossier
    for heading in (
        "Philosophy",
        "Methodology & decision process",
        "Risk discipline & red flags",
        "Track record highlights (public)",
        "Voice",
    ):
        assert f"## {heading}" in dossier
    body_before_sources = dossier.split("## Sources", 1)[0]
    claim_paragraphs = [
        block
        for block in body_before_sources.split("\n\n")
        if block and not block.startswith(("#", ">"))
    ]
    assert claim_paragraphs
    assert all("[1]" in paragraph for paragraph in claim_paragraphs)
    loaded = load_persona("ada_value")
    assert loaded.get("beliefs") == ["I require a margin of safety before investing."]
    assert result.usage.search_calls == 6
    assert result.usage.calls == 13  # 6 search + 5 dossier + persona + verify
    assert result.usage.cost_usd == pytest.approx(0.021)
    # Every dossier synthesis call receives only the immutable ledger snapshot.
    assert all(request.evidence == backend.dossier_requests[0].evidence for request in backend.dossier_requests)


def test_three_to_four_domains_write_thin_warning(tmp_path):
    backend = FakeBackend(_evidence(4))
    result = _factory(backend).run(ResearchRequest("Thin Investor", tmp_path))
    assert result.quality == "thin"
    assert result.domain_count == 4
    assert LOW_SOURCE_WARNING in result.dossier_path.read_text()
    spec = json.loads(result.agent_path.read_text())
    assert spec["tinyic"]["generation"]["quality"] == "thin"


def test_five_domains_without_primary_is_thin(tmp_path):
    secondary = tuple(replace(item, source_type="secondary") for item in _evidence(5))
    backend = FakeBackend(secondary)
    result = _factory(backend).run(ResearchRequest("Secondary Sources", tmp_path))
    assert result.domain_count == 5
    assert result.quality == "thin"


def test_fewer_than_three_domains_refuses_without_writes(tmp_path):
    backend = FakeBackend(_evidence(2))
    with pytest.raises(InsufficientSourcesError) as error:
        _factory(backend).run(ResearchRequest("Under Sourced", tmp_path))
    assert error.value.reason_code == "insufficient_sources"
    assert not list(tmp_path.iterdir())
    assert backend.dossier_requests == []


def test_unverifiable_quotes_are_dropped_from_both_artifacts(tmp_path):
    backend = FakeBackend(_evidence(), bad_quote=True)
    result = _factory(backend).run(ResearchRequest("Quote Check", tmp_path))
    specification = json.loads(result.agent_path.read_text())
    quotes = specification["tinyic"]["famous_quotes"]
    assert [quote["text"] for quote in quotes] == [
        "Buy only with a margin of safety"
    ]
    dossier = result.dossier_path.read_text()
    assert "This quote never appeared" not in dossier
    assert "Buy only with a margin of safety" in dossier


def test_collision_requires_force_and_force_replaces_both(tmp_path):
    first = _factory(FakeBackend(_evidence()))
    request = ResearchRequest("Collision Person", tmp_path)
    result = first.run(request)
    old_agent = result.agent_path.read_bytes()
    old_dossier = result.dossier_path.read_bytes()

    with pytest.raises(PersonaCollisionError):
        _factory(FakeBackend(_evidence())).run(request)
    assert result.agent_path.read_bytes() == old_agent
    assert result.dossier_path.read_bytes() == old_dossier

    replacement = _factory(FakeBackend(_evidence())).run(
        replace(request, force=True)
    )
    assert replacement.agent_path.exists() and replacement.dossier_path.exists()


def test_builtin_collision_is_refused_even_with_force(tmp_path):
    factory = PersonaFactory(FakeBackend(_evidence()))
    with pytest.raises(BuiltinPersonaCollisionError):
        factory.run(ResearchRequest("Warren Buffett", tmp_path, force=True))
    assert not list(tmp_path.iterdir())


def test_backend_failure_between_stages_leaves_no_partial_files(tmp_path):
    backend = FakeBackend(_evidence(), fail_at="verify")
    with pytest.raises(RuntimeError, match="verify failed"):
        _factory(backend).run(ResearchRequest("Interrupted Person", tmp_path))
    assert not list(tmp_path.iterdir())


def test_dual_write_rolls_back_when_second_atomic_replace_fails(tmp_path):
    agent = tmp_path / "rollback.agent.json"
    dossier = tmp_path / "rollback.dossier.md"
    calls = 0

    def fail_second(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second rename failure")
        os.replace(source, destination)

    with pytest.raises(OSError, match="second rename"):
        write_artifact_pair(
            agent,
            b"agent",
            dossier,
            b"dossier",
            force=False,
            replace_fn=fail_second,
        )
    assert not agent.exists()
    assert not dossier.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_schema_validation_reports_actionable_paths():
    backend = FakeBackend(_evidence())
    # Build through the fake's same structure without running provider stages.
    class Request:
        investor_name = "Invalid Person"
        source_records = tuple(
            {
                "title": item.title,
                "url": item.url,
                "type": item.source_type,
                "accessed": "2026-07-14",
            }
            for item in _evidence()
        )
        generation = {
            "generated_by": "tinyic persona research",
            "model_ref": backend.model_ref,
            "date": "2026-07-14",
            "search_calls": 6,
            "quality": "normal",
            "disclaimer": "Educational simulation.",
        }

    specification = _valid_spec(Request())
    specification["tinyic"]["decision_checklist"] = ["Only one"]
    specification["tinyic"]["famous_quotes"][0]["source"] = 99
    specification["persona"]["occupation"]["description"] = "Mismatch"
    specification["persona"]["family"] = "must never be generated"
    with pytest.raises(SchemaValidationError) as error:
        validate_agent_spec(specification)
    message = str(error.value)
    assert "tinyic.decision_checklist" in message
    assert "tinyic.famous_quotes[0].source" in message
    assert "persona.occupation.description" in message
    assert "persona.family" in message
