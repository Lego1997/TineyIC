"""Offline contract tests for the cited persona factory."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from tinyic.personas.factory import (
    AGENT_SCHEMA,
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
    quality_for,
    validate_agent_spec,
    write_artifact_pair,
)
from tinyic.personas.factory.templates import render_dossier
from tinyic.personas.registry import load_persona
from tinytroupe.agent import TinyPerson


_FIXTURES = Path(__file__).parent / "fixtures" / "persona_factory"


def _evidence(count: int = 6) -> tuple[Evidence, ...]:
    records = json.loads((_FIXTURES / "evidence.json").read_text(encoding="utf-8"))
    return tuple(Evidence(**record) for record in records[:count])


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
        bad_quote_delimiters: tuple[str, str] = ('"', '"'),
        bad_quote_text: str = "This quote never appeared",
        voice_addendum: str | None = None,
        fail_at: str | None = None,
    ) -> None:
        self.evidence = evidence
        self.bad_quote = bad_quote
        self.bad_quote_delimiters = bad_quote_delimiters
        self.bad_quote_text = bad_quote_text
        self.voice_addendum = voice_addendum
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
                opening, closing = self.bad_quote_delimiters
                text += (
                    f"\n\nThe investor said {opening}{self.bad_quote_text}{closing}. [1]"
                )
            if self.voice_addendum:
                text += f"\n\n{self.voice_addendum}"
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


class MultiSearchBackend(FakeBackend):
    """Provider-shaped fake that reports several priced searches per request."""

    def __init__(self, evidence: tuple[Evidence, ...], *, provider: str) -> None:
        super().__init__(evidence)
        self.provider = provider
        self.remaining_budgets: list[int | None] = []

    def search(self, request):
        self.remaining_budgets.append(request.remaining_searches)
        remaining = request.remaining_searches or 1
        consumed = min(2, remaining)
        start = self.search_index
        self.search_index += consumed
        return SearchResponse(
            tuple(
                self.evidence[index % len(self.evidence)]
                for index in range(start, start + consumed)
            ),
            CallUsage(
                "search",
                self.model_ref,
                input_tokens=10,
                output_tokens=2,
                cost_usd=0.001 * consumed,
                calls=1,
                search_calls=consumed,
            ),
            # Exercise the factory's actual-count stop even when an adapter
            # does not independently mark its response exhausted.
            budget_exhausted=False,
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


@pytest.mark.parametrize("provider", ["openai", "grok", "google"])
def test_factory_decrements_actual_multi_search_calls_and_stops_at_budget(
    tmp_path, provider
):
    backend = MultiSearchBackend(_evidence(), provider=provider)

    result = _factory(backend).run(
        ResearchRequest(f"{provider} Budget", tmp_path, max_searches=3)
    )

    assert backend.remaining_budgets == [3, 1]
    assert backend.search_index == 3
    assert result.usage.search_calls == 3
    assert result.specification["tinyic"]["generation"]["search_calls"] == 3


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


def test_ledger_idna_normalization_converges_dedupe_and_domain_gate():
    ledger = EvidenceLedger()
    variants = (
        "https://example.com/article",
        "https://ＥＸＡＭＰＬＥ.com/article",
        "https://𝐞𝐱𝐚𝐦𝐩𝐥𝐞.com/article",
    )
    for index, url in enumerate(variants, 1):
        assert ledger.add(Evidence(url, f"Source {index}", "Excerpt")) == 1

    assert len(ledger.items) == 1
    assert ledger.items[0].url == "https://example.com/article"

    domain_ledger = EvidenceLedger()
    for index, url in enumerate(variants, 1):
        domain_ledger.add(
            Evidence(url.replace("article", f"article-{index}"), "Source", "Excerpt")
        )
    assert domain_ledger.domains == frozenset({"example.com"})
    with pytest.raises(InsufficientSourcesError) as error:
        quality_for(domain_ledger)
    assert error.value.domain_count == 1


def test_ledger_idna_preserves_unicode_domain_semantics_and_dedupes():
    ledger = EvidenceLedger()

    assert ledger.add(
        Evidence("https://BÜCHER.example/report", "Unicode", "Excerpt")
    ) == 1
    assert ledger.add(
        Evidence("https://xn--bcher-kva.example/report", "ASCII", "Excerpt")
    ) == 1

    assert len(ledger.items) == 1
    assert ledger.items[0].url == "https://xn--bcher-kva.example/report"


def test_dossier_source_markdown_escapes_label_and_angle_destination():
    unsafe_destination = "https://example.com/a>" + "\\" + "path" + "\x01"
    dossier = render_dossier(
        investor_name="Safe Render",
        epithet="Evidence-led investor",
        sections={"philosophy": ("Evidence-backed principle. [1]",)},
        evidence=(
            Evidence(
                unsafe_destination,
                "Safe ](https://evil.example/leak) <script> [label]",
                "Excerpt",
            ),
        ),
        quality="normal",
        model_ref="openai/test",
        generated_date="2026-07-14",
        search_calls=1,
        cost_usd=0.0,
    )

    assert r"\]\(https://evil.example/leak\)" in dossier
    assert "](https://evil.example/leak)" not in dossier
    assert "&lt;script&gt;" in dossier
    assert "](<https://example.com/a%3E%5Cpath%01>)" in dossier


def test_dossier_heading_escapes_untrusted_name_and_epithet():
    dossier = render_dossier(
        investor_name='<img src=x onerror="alert(1)"> [profile](javascript:alert(1))',
        epithet='<svg onload="alert(2)"></svg> ![pixel](https://evil.example)',
        sections={"philosophy": ("Evidence-backed principle. [1]",)},
        evidence=(
            Evidence("https://example.com/source", "Source", "Excerpt"),
        ),
        quality="normal",
        model_ref="openai/test",
        generated_date="2026-07-14",
        search_calls=1,
        cost_usd=0.0,
    )

    heading = dossier.splitlines()[0]
    assert "<img" not in heading and "<svg" not in heading
    assert "](javascript:" not in heading
    assert "](https://evil.example)" not in heading
    assert "&lt;img" in heading and "&lt;svg" in heading
    assert r"\[profile\]\(javascript:alert\(1\)\)" in heading


@pytest.mark.parametrize(
    "statement",
    [
        '<img src=x onerror="alert(1)"> Supported principle. [1]',
        '<svg onload="alert(1)"></svg> Supported principle. [1]',
        '<a href="javascript:alert(1)">click</a> Supported principle. [1]',
        "[click](javascript:alert(1)) Supported principle. [1]",
        "![track](https://evil.example/pixel) Supported principle. [1]",
        "[details][unsafe] Supported principle. [1]",
        "Supported principle. [1]\n[unsafe]: javascript:alert(1)",
        "Supported principle. [1]\n[1]: https://evil.example/override",
    ],
    ids=(
        "html-img",
        "html-svg",
        "html-link",
        "markdown-link",
        "markdown-image",
        "markdown-reference-link",
        "markdown-reference-definition",
        "numeric-reference-definition",
    ),
)
def test_dossier_drops_active_html_and_markdown_but_keeps_numeric_citations(
    tmp_path, statement
):
    result = _factory(
        FakeBackend(_evidence(), voice_addendum=statement)
    ).run(ResearchRequest("Markup Safety", tmp_path))

    dossier = result.dossier_path.read_text(encoding="utf-8")
    assert statement not in dossier
    assert "The public voice emphasizes" in dossier
    assert "[1]" in dossier


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a)[leak](https://user:PATH_SECRET@evil.example/x",
        "https://example.com/archive/https://127.0.0.1/private",
    ],
)
def test_factory_rejects_path_injection_before_prompts_or_artifacts(
    tmp_path,
    url,
):
    evidence = (replace(_evidence()[0], url=url),) + _evidence()[1:]
    backend = FakeBackend(evidence)

    result = _factory(backend).run(ResearchRequest("Path Safety", tmp_path))

    assert result.source_count == 5
    provider_inputs = "\n".join(
        [request.prompt for request in backend.dossier_requests]
        + [request.prompt for request in backend.persona_requests]
    )
    artifacts = result.agent_path.read_text(
        encoding="utf-8"
    ) + result.dossier_path.read_text(encoding="utf-8")
    for forbidden in ("PATH_SECRET", "127.0.0.1", url):
        assert forbidden not in provider_inputs
        assert forbidden not in artifacts


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
    agent_text = result.agent_path.read_text(encoding="utf-8")
    assert agent_text == (_FIXTURES / "ada_value.agent.json").read_text(
        encoding="utf-8"
    )
    specification = json.loads(agent_text)
    validate_agent_spec(specification)
    assert specification["tinyic"]["generation"]["disclaimer"]
    assert specification["tinyic"]["generation"]["quality"] == "normal"
    assert len(specification["tinyic"]["sources"]) == 6
    dossier = result.dossier_path.read_text(encoding="utf-8")
    assert dossier == (_FIXTURES / "ada_value.dossier.md").read_text(encoding="utf-8")
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
    assert all(
        request.evidence == backend.dossier_requests[0].evidence
        for request in backend.dossier_requests
    )
    assert all(
        "Use ONLY the numbered evidence" in request.prompt
        for request in backend.dossier_requests
    )
    persona_prompt = backend.persona_requests[0].prompt
    assert "the complete tinyic schema_version 1 block" in persona_prompt
    assert "DOSSIER\n## philosophy" in persona_prompt
    assert "\n\nLEDGER\n[1] Source 1" in persona_prompt


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


@pytest.mark.parametrize(
    "delimiters",
    [
        ('"', '"'),
        ('“', '"'),
        ('"', '”'),
        ('‘', '’'),
        ("'", "'"),
        ('‘', "'"),
        ("'", '’'),
        ('”', '”'),
        ('’', '’'),
        ('«', '»'),
        ('「', '」'),
        ('＂', '＂'),
        ('❛', '❜'),
        ('′', '′'),
        ('ʼ', 'ʼ'),
        ('\x60', '\x60'),
        ('ʽ', 'ʽ'),
        ('ꞌ', 'ꞌ'),
        ('״', '״'),
        ('｀', '｀'),
        ('⸂', '⸃'),
        ('ʺ', 'ʺ'),
        ('ˮ', 'ˮ'),
        ('՚', '՚'),
        ('‴', '‴'),
        ('‷', '‷'),
        ('⁗', '⁗'),
        ('ˊ', 'ˊ'),
        ('ˋ', 'ˋ'),
        ('ˎ', 'ˎ'),
        ('ˏ', 'ˏ'),
        ('ߴ', 'ߴ'),
        ('ߵ', 'ߵ'),
        ('᾽', '᾽'),
        ('᾿', '᾿'),
        ('῾', '῾'),
        ('׳', '׳'),
        ('ʾ', 'ʾ'),
        ('ʿ', 'ʿ'),
        ('˒', '˒'),
        ('˓', '˓'),
        ('⸲', '⸲'),
        ('⹁', '⹁'),
        ('\u0312', '\u0312'),
        ('\u0313', '\u0313'),
        ('\u0314', '\u0314'),
        ('\u0315', '\u0315'),
        ('\u0326', '\u0326'),
    ],
    ids=(
        "double",
        "curly-open-double",
        "curly-close-double",
        "curly-single",
        "straight-single",
        "curly-open-single",
        "curly-close-single",
        "right-curly-double",
        "right-curly-single",
        "guillemets",
        "corner-brackets",
        "fullwidth-double",
        "unsupported-ornaments",
        "prime",
        "modifier-apostrophe",
        "backticks",
        "reversed-comma",
        "saltillo",
        "gershayim",
        "fullwidth-grave",
        "substitution-brackets",
        "modifier-double-prime",
        "modifier-double-apostrophe",
        "armenian-apostrophe",
        "triple-prime",
        "reversed-triple-prime",
        "quadruple-prime",
        "modifier-acute",
        "modifier-grave",
        "modifier-low-grave",
        "modifier-low-acute",
        "nko-high-apostrophe",
        "nko-low-apostrophe",
        "greek-koronis",
        "greek-psili",
        "greek-dasia",
        "hebrew-geresh",
        "right-half-ring",
        "left-half-ring",
        "centred-right-half-ring",
        "centred-left-half-ring",
        "turned-comma",
        "reversed-comma",
        "combining-turned-comma",
        "combining-comma-above",
        "combining-reversed-comma",
        "combining-comma-above-right",
        "combining-comma-below",
    ),
)
def test_unverifiable_quotes_are_dropped_from_both_artifacts(
    tmp_path, delimiters
):
    backend = FakeBackend(
        _evidence(), bad_quote=True, bad_quote_delimiters=delimiters
    )
    result = _factory(backend).run(ResearchRequest("Quote Check", tmp_path))
    specification = json.loads(result.agent_path.read_text())
    quotes = specification["tinyic"]["famous_quotes"]
    assert [quote["text"] for quote in quotes] == [
        "Buy only with a margin of safety"
    ]
    dossier = result.dossier_path.read_text()
    assert "This quote never appeared" not in dossier
    assert "Buy only with a margin of safety" in dossier


def test_single_character_quote_is_verified(tmp_path):
    backend = FakeBackend(
        _evidence(),
        bad_quote=True,
        bad_quote_delimiters=("'", "'"),
        bad_quote_text="Z",
    )

    result = _factory(backend).run(ResearchRequest("Short Quote", tmp_path))

    assert "said 'Z'" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    ("statement", "retained"),
    [
        ("> Buy only with a margin of safety [1]", True),
        (
            "> Buy only with a margin of safety [1]\n>\n"
            "> Buy only with a margin of safety [1]",
            True,
        ),
        ("> This fabricated quotation was never in the source. [1]", False),
        ("He wrote:\n> This fabricated quotation was never in the source. [1]", False),
        ("- > This fabricated quotation was never in the source. [1]", False),
        ("- - > This fabricated quotation was never in the source. [1]", False),
        ("1. - > This fabricated quotation was never in the source. [1]", False),
        ("- 1. > This fabricated quotation was never in the source. [1]", False),
        ("- He wrote:\n  > This fabricated quotation was never in the source. [1]", False),
        ("1. He wrote:\n   > This fabricated quotation was never in the source. [1]", False),
        ("10. He wrote:\n    > This fabricated quotation was never in the source. [1]", False),
        ("- Context\n  - He wrote:\n    > This fabricated quotation was never in the source. [1]", False),
        (
            "> Buy only with a margin of safety\n"
            "This fabricated quotation was never in the source. [1]",
            False,
        ),
        ("<blockquote>This fabricated quotation was never in the source.</blockquote> [1]", False),
        ("<q>This fabricated quotation was never in the source.</q> [1]", False),
        (
            '<q class="citation">Buy only with a margin of safety</q> [1]',
            False,
        ),
    ],
    ids=(
        "verbatim",
        "verbatim-with-empty-separator",
        "fabricated",
        "after-prose",
        "list-nested",
        "nested-unordered-list-markers",
        "ordered-then-unordered-list-markers",
        "unordered-then-ordered-list-markers",
        "list-continuation",
        "ordered-list-continuation",
        "four-space-ordered-continuation",
        "nested-list-continuation",
        "lazy-continuation",
        "html-blockquote",
        "html-q",
        "html-q-attribute",
    ),
)
def test_markdown_blockquotes_require_verbatim_evidence(
    tmp_path, statement, retained
):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Blockquote Check", tmp_path))

    dossier = result.dossier_path.read_text()
    if retained:
        assert statement in dossier
    else:
        assert statement not in dossier


@pytest.mark.parametrize(
    "statement",
    [
        "The investor uses `EV/EBITDA` discipline. [1]",
        "The investor uses `ticker` discipline. [1]",
        "The investor uses `price/book ratio` discipline. [1]",
        "The ratio is `price/book ratio`. [1]",
        "The famous ratio was `price/book ratio`. [1]",
        "The investor uses `model_ref` metadata. [1]",
        "The API field is `model_ref`. [1]",
        "The API field `model_ref` is stable. [1]",
        "The filing reported `EV/EBITDA` at 10x. [1]",
        "The table states `price/book ratio`. [1]",
        "The API reported `model_ref`. [1]",
        "The ratio is `P/E`. [1]",
        "The ratio is `P/B`. [1]",
        "The filing reported `13F`. [1]",
        "The filing disclosed `EV/EBITDA`. [1]",
        "The table presents `P/B`. [1]",
        "The primary valuation metric is `P/E`. [1]",
        "The ratio is `FAKE`. [1]",
        "The API field is `LIE`. [1]",
        "The investor uses `BUY` discipline. [1]",
        "The investor uses ``price ` book`` discipline. [1]",
        'The syntax uses ``"Buy only with a margin of safety"``. [1]',
    ],
)
def test_markdown_inline_code_is_not_mistaken_for_a_quotation(
    tmp_path, statement
):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Inline Code", tmp_path))

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "The report stated `FAKE`. [1]",
        "The filing disclosed `LIE`. [1]",
        "The table shows `BUY`. [1]",
        "The record reported `FAKE`. [1]",
        "The report stated <code>FAKE</code>. [1]",
        "The filing disclosed <tt>LIE</tt>. [1]",
        "The table shows <kbd>BUY</kbd>. [1]",
        "The record reported <samp>FAKE</samp>. [1]",
    ],
)
def test_reporting_sources_reject_bare_uppercase_code_tokens(
    tmp_path, statement
):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Reporting Source Bare Code", tmp_path)
    )

    assert statement not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "The report stated `EV/EBITDA`. [1]",
        "The filing disclosed `price/book ratio`. [1]",
        "The table shows `P/B`. [1]",
        "The record reported `13F`. [1]",
        "The report stated `model_ref`. [1]",
    ],
)
def test_reporting_sources_keep_structured_markdown_code(
    tmp_path, statement
):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Reporting Source Structured Code", tmp_path)
    )

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "He said `fabricated/value`. [1]",
        r"He said \`fabricated/value\`. [1]",
        "He says that `fabricated payload`. [1]",
        "He says that `fabricated`. [1]",
        "He says to everyone present that `fabricated payload`. [1]",
        "He says to everyone present that `fabricated/value`. [1]",
        "He described the method as `fabricated/value`. [1]",
        "He proclaimed `fabricated payload`. [1]",
        "He proclaimed `fabricated/value`. [1]",
        "He proclaimed publicly `fabricated/value`. [1]",
        "He proclaimed very publicly `fabricated/value`. [1]",
        "He testified `fabricated payload`. [1]",
        "His maxim is `fabricated maxim`. [1]",
        "His maxim is `fabricated/value`. [1]",
        "His famous maxim was `fabricated/value`. [1]",
        "His widely repeated famous maxim was `fabricated/value`. [1]",
        "This is `fabricated payload`. [1]",
        "Quotation: `fabricated/value`. [1]",
        "The quotation: `fabricated/value`. [1]",
        "According to him, `fabricated payload`. [1]",
        "According to him, `fabricated`. [1]",
        "In his words, `fabricated payload`. [1]",
        "As he put it, `fabricated payload`. [1]",
        "`fabricated payload`, he said. [1]",
        "`fabricated payload`\nhe said. [1]",
        "`fabricated/value`\n—he said. [1]",
        "`fabricated/value`\n— he said. [1]",
        "`fabricated/value`, according to him. [1]",
        "`fabricated/value`, in his words. [1]",
        'He wrote:\n```text\n"fabricated payload"\n``` [1]',
        "Quotation:\n```\nfabricated maxim\n```\n[1]",
        'The syntax uses ``"fabricated payload"``. [1]',
        "The syntax uses `` `fabricated` ``. [1]",
    ],
)
def test_backticks_cannot_hide_reported_speech(tmp_path, statement):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Backtick Speech", tmp_path))

    dossier = result.dossier_path.read_text()
    assert "fabricated" not in dossier


@pytest.mark.parametrize(
    "statement",
    [
        "He said the ratio is `EV/EBITDA`. [1]",
        "According to him, the ratio is `EV/EBITDA`. [1]",
        "His maxim says the investor uses `EV/EBITDA`. [1]",
        "In testimony, he explained the API field is `model_ref`. [1]",
        "Quotation: the metric is `FAKE`. [1]",
        "The quotation: the ratio is `EV/EBITDA`. [1]",
        "His maxim: the investor uses `FAKE`. [1]",
        "He proclaimed: the field is `FAKE`. [1]",
        "In his words, the API field is `FAKE`. [1]",
        "The ratio is `EV/EBITDA`, he said. [1]",
        "The ratio is `EV/EBITDA`, in his words. [1]",
        "The ratio is `EV/EBITDA`, according to him. [1]",
        "The ratio is `EV/EBITDA`, Buffett said. [1]",
        "The ratio is `EV/EBITDA`, the investor said. [1]",
    ],
)
def test_attribution_cannot_launder_technical_inline_code(tmp_path, statement):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Attributed Technical Code", tmp_path)
    )

    assert statement not in result.dossier_path.read_text()


@pytest.mark.parametrize("delimiter", ['"', "'"])
def test_inline_code_quote_must_occupy_the_entire_span(tmp_path, delimiter):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="value")
    statement = (
        f"The syntax uses `{delimiter}value{delimiter} fabricated suffix`. [1]"
    )
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Inline Code Quote Suffix", tmp_path)
    )

    assert "fabricated suffix" not in result.dossier_path.read_text()


def test_inline_code_rejects_multiple_quoted_values_in_one_span(tmp_path):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="value")
    statement = 'The syntax uses `"value" "fabricated suffix"`. [1]'
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Multiple Inline Code Quotes", tmp_path)
    )

    assert "fabricated suffix" not in result.dossier_path.read_text()


@pytest.mark.parametrize("delimiter", ['"', "'"])
def test_exact_quoted_inline_code_remains_verifiable(tmp_path, delimiter):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="value")
    statement = f"The syntax uses `{delimiter}value{delimiter}`. [1]"
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Exact Inline Code Quote", tmp_path)
    )

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize("tag", ["code", "tt"])
def test_html_inline_code_is_excluded_from_dossier(tmp_path, tag):
    statement = f"The ratio uses <{tag}>EV/EBITDA</{tag}>. [1]"
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("HTML Technical Code", tmp_path)
    )

    assert statement not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "He said <code>fabricated/value</code>. [1]",
        "He said <kbd>fabricated/value</kbd>. [1]",
        "He said <samp>fabricated/value</samp>. [1]",
        "He said <tt>fabricated/value</tt>. [1]",
        "He said <CoDe>fabricated/value</cOdE>. [1]",
        "He said <Tt>fabricated/value</tT>. [1]",
        "He wrote:<pre>fabricated maxim [1]</pre>",
        'He wrote:<pre class="quote">fabricated maxim [1]</pre>',
        "He wrote:<PrE>fabricated maxim [1]</pRe>",
        "He said <CODE>fabricated/value. [1]",
        "He said fabricated/value</CODE>. [1]",
        "He said <TT>fabricated/value. [1]",
        "He said fabricated/value</TT>. [1]",
        'The ratio uses <code class="metric">EV/EBITDA</code>. [1]',
        'The ratio uses <tt class="metric">EV/EBITDA</tt>. [1]',
        "The ratio uses <code><b>EV/EBITDA</b></code>. [1]",
    ],
)
def test_html_code_channels_fail_closed(tmp_path, statement):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Unsafe HTML Code", tmp_path)
    )

    assert statement not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "tag",
    [
        "xmp",
        "listing",
        "textarea",
        "plaintext",
        "script",
        "style",
        "iframe",
        "noembed",
        "noframes",
        "title",
    ],
)
@pytest.mark.parametrize(
    "form",
    ["matched", "opening-only", "closing-only", "mixed-case"],
)
def test_html_raw_and_literal_containers_fail_closed(tmp_path, tag, form):
    mixed = "".join(
        character.upper() if index % 2 else character
        for index, character in enumerate(tag)
    )
    if form == "matched":
        statement = f"He wrote:<{tag}>fabricated maxim</{tag}> [1]"
    elif form == "opening-only":
        statement = f"He wrote:<{tag}>fabricated maxim [1]"
    elif form == "closing-only":
        statement = f"He wrote fabricated maxim</{tag}> [1]"
    else:
        statement = f"He wrote:<{mixed}>fabricated maxim</{mixed}> [1]"
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Unsafe HTML Raw Container", tmp_path)
    )

    assert "fabricated maxim" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "~~~\nfabricated maxim\n~~~ [1]",
        "~~~markdown\nfabricated maxim\n~~~ [1]",
        "   ~~~ markdown\nfabricated maxim\n   ~~~ [1]",
        "   ```text\nfabricated maxim\n   ``` [1]",
        "- ~~~text\n  fabricated maxim\n  ~~~ [1]",
        "    fabricated maxim [1]",
        "\tfabricated maxim [1]",
        "-     fabricated maxim [1]",
        "1.     fabricated maxim [1]",
        "- -     fabricated maxim [1]",
        "1. -     fabricated maxim [1]",
        "- 1.     fabricated maxim [1]",
        "1.  \tfabricated maxim [1]",
        "- - \tfabricated maxim [1]",
    ],
    ids=(
        "tilde-fence",
        "tilde-info-string",
        "indented-tilde-fence",
        "indented-backtick-fence",
        "list-nested-fence",
        "four-space-code-block",
        "tab-code-block",
        "bullet-item-code-block",
        "ordered-item-code-block",
        "nested-bullet-code-block",
        "ordered-bullet-code-block",
        "bullet-ordered-code-block",
        "ordered-absolute-tab-stop",
        "nested-bullet-absolute-tab-stop",
    ),
)
def test_commonmark_code_blocks_cannot_hide_quotations(tmp_path, statement):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("CommonMark Code Quote", tmp_path)
    )

    assert "fabricated maxim" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "   Ordinary indented prose remains visible. [1]",
        "-    Ordinary list prose remains visible. [1]",
        "- -    Ordinary nested list prose. [1]",
        "-\t Ordinary tabbed list prose. [1]",
        "- Ordinary list prose\n    continues with indentation. [1]",
        "1. Ordinary list prose\n    continues with indentation. [1]",
    ],
    ids=(
        "three-space-prose",
        "four-space-list-padding",
        "four-space-nested-list-padding",
        "tabbed-list-padding",
        "bullet-continuation",
        "ordered-continuation",
    ),
)
def test_ordinary_indentation_is_not_mistaken_for_code(tmp_path, statement):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Ordinary Indentation", tmp_path)
    )

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "He said &ldquo;This fabricated quotation was never in the source.&rdquo; [1]",
        "He said &#34;This fabricated quotation was never in the source.&#34; [1]",
    ],
)
def test_html_entities_cannot_hide_quotation_marks(tmp_path, statement):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Entity Quote", tmp_path))

    assert "This fabricated quotation was never in the source" not in (
        result.dossier_path.read_text()
    )


def test_verbatim_quote_matching_requires_word_boundaries(tmp_path):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="marginal results")
    backend = FakeBackend(
        tuple(evidence), voice_addendum='He said "margin". [1]'
    )

    result = _factory(backend).run(
        ResearchRequest("Quote Word Boundary", tmp_path)
    )

    assert 'said "margin"' not in result.dossier_path.read_text()


def test_famous_quote_matching_requires_word_boundaries(tmp_path):
    evidence = list(_evidence())
    evidence[0] = replace(
        evidence[0], excerpt="This quote never appearedly in the source"
    )
    backend = FakeBackend(tuple(evidence), bad_quote=True)

    result = _factory(backend).run(
        ResearchRequest("Famous Quote Word Boundary", tmp_path)
    )
    specification = json.loads(result.agent_path.read_text())

    assert specification["tinyic"]["famous_quotes"] == []


def test_model_prose_evidence_cannot_verify_quotes(tmp_path):
    evidence = list(_evidence())
    evidence[0] = replace(
        evidence[0],
        excerpt="This quote never appeared",
        quote_eligible=False,
    )
    backend = FakeBackend(tuple(evidence), bad_quote=True)

    result = _factory(backend).run(
        ResearchRequest("Synthesized Evidence Quote", tmp_path)
    )
    specification = json.loads(result.agent_path.read_text())

    assert specification["tinyic"]["famous_quotes"] == []
    assert "This quote never appeared" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    ("delimiters", "quote"),
    [
        (("‘", "’"), "It’s never too late"),
        (("'", "'"), "It's never too late"),
        (("‘", "'"), "It’s never too late"),
        (("'", "’"), "It's never too late"),
    ],
    ids=("curly", "straight", "curly-straight", "straight-curly"),
)
def test_unverifiable_single_quoted_contractions_are_dropped(
    tmp_path, delimiters, quote
):
    backend = FakeBackend(
        _evidence(),
        bad_quote=True,
        bad_quote_delimiters=delimiters,
        bad_quote_text=quote,
    )

    result = _factory(backend).run(ResearchRequest("Apostrophe Quote", tmp_path))

    assert quote not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "It's clear that the investor’s discipline protects owners' capital. [1]",
        "O'Reilly's record and y'all's patience matter. [1]",
        "The rock'n'roll analogy and ma'am's reply were apt. [1]",
        "The analysis's scope, status's signal, and consensus's basis matter. [1]",
        "The business's moat and James's record matter. [1]",
        "Y'all'd agree that we shouldn't've rushed. [1]",
        "How'd value improve? There'll be value. [1]",
        "The states' pension funds and claims' evidentiary basis matter. [1]",
        "The companies' assets and managers' incentives align. [1]",
        "The firm's strategy and company's moat both matter. [1]",
        (
            "This'll work; What'd change? Why'd it fail? Where'd value go? "
            "Who've we asked? What've we learned? There're reasons. [1]"
        ),
        (
            "What'll change? Where'll value go? How'll this work? "
            "There've been gains. This'd work. When'll we know? "
            "Why'll it matter? How've we done? Where've they gone? "
            "Why've they changed? When've we seen it? How're things? "
            "Why're they ready? When're they due? When'd that change? [1]"
        ),
        (
            "What’ll change? Where’ll value go? How’ll this work? "
            "There’ve been gains. This’d work. When’ll we know? "
            "Why’ll it matter? How’ve we done? Where’ve they gone? "
            "Why’ve they changed? When’ve we seen it? How’re things? "
            "Why’re they ready? When’re they due? When’d that change? [1]"
        ),
        "The '80s rewarded discipline before the '90s. [1]",
        "’Tis clear the '80s rewarded discipline. [1]",
    ],
    ids=(
        "contractions",
        "proper-names-and-contractions",
        "single-letter-internal-apostrophes",
        "s-ending-singular-possessives",
        "business-and-proper-name-possessives",
        "chained-contractions",
        "how-and-there-contractions",
        "plural-possessives",
        "multiple-plural-possessives",
        "multiple-singular-possessives",
        "closed-ordinary-contractions",
        "completed-straight-contractions",
        "completed-curly-contractions",
        "paired-decades",
        "mixed-elision-decade",
    ),
)
def test_unquoted_apostrophe_prose_is_not_treated_as_quotes(tmp_path, statement):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Apostrophe Prose", tmp_path))

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "delimiter",
    ["'", "ʻ", "ʼ", "ʹ", "‘", "’", "‚", "‛", "′", "‹", "›", "＇"],
)
@pytest.mark.parametrize(
    "template",
    [
        "He publicly says{d} fabricated owners{d} capital is unsafe. [1]",
        "The committee reports{d} fabricated risks{d} capital impact. [1]",
        "Buffett opines{d} fabricated investors{d} returns improved. [1]",
        "He often says{d} fabricated owners{d} capital is unsafe. [1]",
        "He very publicly reports{d} fabricated risks{d} capital impact. [1]",
        "Buffett often very publicly reports{d} fabricated risks{d} capital impact. [1]",
        "This investor says{d} fabricated owners{d} capital is unsafe. [1]",
        "Our investor reports{d} fabricated risks{d} capital impact. [1]",
        "The company reports{d} fabricated risks{d} capital impact. [1]",
        "The market signals{d} fabricated investors{d} returns improved. [1]",
        "The business risks{d} fabricated owners{d} capital fell. [1]",
        "Every investor reports{d} fabricated owners{d} capital is unsafe. [1]",
        "Each investor reports{d} fabricated owners{d} capital is unsafe. [1]",
        "Many investors discuss{d} fabricated owners{d} capital is unsafe. [1]",
        "Several analysts discuss{d} fabricated risks{d} capital impact. [1]",
        "Some analysts discuss{d} fabricated risks{d} capital impact. [1]",
        "Most investors discuss{d} fabricated investors{d} returns improved. [1]",
        "Both analysts discuss{d} fabricated risks{d} capital impact. [1]",
        "Neither analyst discusses{d} fabricated owners{d} capital is unsafe. [1]",
        "Investment committees discuss{d} fabricated risks{d} capital impact. [1]",
        "Buffett's committee reports{d} fabricated owners{d} capital is unsafe. [1]",
        "Buffett and Munger discuss{d} fabricated risks{d} capital impact. [1]",
        "The investor and analyst discuss{d} fabricated owners{d} capital is unsafe. [1]",
        "Warren Buffett and Charlie Munger discuss{d} fabricated investors{d} returns improved. [1]",
    ],
)
def test_reporting_shaped_plural_boundaries_require_quote_verification(
    tmp_path, delimiter, template
):
    statement = template.format(d=delimiter)
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Reporting Shaped Apostrophe", tmp_path)
    )

    assert "fabricated" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "delimiter",
    ["'", "ʻ", "ʼ", "ʹ", "‘", "’", "‚", "‛", "′", "‹", "›", "＇"],
)
@pytest.mark.parametrize(
    "template",
    [
        "The states{d} pension funds and claims{d} evidentiary basis matter. [1]",
        "The companies{d} assets and managers{d} incentives align. [1]",
        "Investors{d} Berkshire holdings and analysts{d} estimates improved. [1]",
        "The business risks{d} impact and owners{d} capital allocation matter. [1]",
        "The company reports{d} findings and analysts{d} estimates align. [1]",
        "The market signals{d} value and investors{d} interests align. [1]",
    ],
)
def test_multiple_generic_possessives_remain_prose_across_single_marks(
    tmp_path, delimiter, template
):
    statement = template.format(d=delimiter)
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Generic Possessive Delimiters", tmp_path)
    )

    assert statement in result.dossier_path.read_text()


def test_governed_later_possessive_overrides_structural_ambiguity(tmp_path):
    statement = "We compare reports' conclusions with managers' forecasts. [1]"
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Governed Possessive Boundary", tmp_path)
    )

    assert statement in result.dossier_path.read_text()


def test_ungoverned_later_boundary_still_closes_reporting_shaped_quote(
    tmp_path,
):
    statement = "We compare reports' fabricated managers' forecasts. [1]"
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Ungoverned Possessive Boundary", tmp_path)
    )

    assert "fabricated" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "The '80s rewarded owners' capital discipline. [1]",
        "’Tis the investor’s discipline that protects owners’ capital. [1]",
        "’Tis sensible, ’cause discipline matters. [1]",
    ],
    ids=(
        "decade-plus-possessive",
        "elision-plus-possessive",
        "paired-elisions",
    ),
)
def test_ambiguous_boundary_apostrophe_sequences_fail_closed(
    tmp_path, statement
):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Ambiguous Apostrophe Prose", tmp_path)
    )

    assert statement not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "The investor said 'owners' fabricated capital'. [1]",
        "The investor said ‘owners’ fabricated capital’. [1]",
    ],
    ids=("straight", "curly"),
)
def test_plural_possessive_inside_quote_does_not_truncate_verification(
    tmp_path, statement
):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="owners")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Possessive Quote", tmp_path))

    assert "fabricated capital" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "He said 'Tis fabricated owners'. [1]",
        "He said ’Tis fabricated owners’. [1]",
        "He said '80s fabricated owners'. [1]",
        "He said 'value' fabricated owners'. [1]",
        "He said ‘value’ fabricated owners’. [1]",
        "He said 'Tis fabricated owners' yesterday. [1]",
        "He said ‘Tis fabricated owners’ yesterday. [1]",
        "He said '80s fabricated owners' yesterday. [1]",
        "He said 'value' fabricated owners' yesterday. [1]",
        "He says 'Tis fabricated owners' yesterday. [1]",
        "'Tis fabricated owners' yesterday. [1]",
        "'Tis fabricated owners' publicly. [1]",
        "’Tis fabricated owners’ again. [1]",
        "He insisted 'Tis fabricated owners' in testimony. [1]",
        "He said emphatically 'Tis fabricated owners' to reporters. [1]",
        "He said 'value' fabricated owners' publicly. [1]",
        "'Tis fabricated owners' during testimony. [1]",
        "’Tis fabricated owners’ after lunch. [1]",
        "'Tis fabricated owners' before lunch. [1]",
        "'Tis fabricated owners' by design. [1]",
        "'Tis fabricated owners' as written. [1]",
        "'Tis fabricated owners' while speaking. [1]",
        "'Tis fabricated owners' now. [1]",
        "'Tis fabricated owners' then. [1]",
        "'Tis fabricated owners' Buffett recalled. [1]",
        "He said 'value' fabricated owners' during testimony. [1]",
        "'Tis fabricated owners' [1]",
        "'80s fabricated owners' [1]",
        "He said 'value' fabricated owners' [1]",
        "'Tis fabricated owners' Warren Buffett recalled. [1]",
        "'Tis fabricated owners' Buffett later recalled. [1]",
        "'Tis fabricated owners' Buffett reportedly said. [1]",
        "He said 'value' fabricated owners' Warren Buffett recalled. [1]",
        "'Tis fabricated owners' later that day. [1]",
        "He said 'value' fabricated owners' later that day. [1]",
        "'Tis fabricated owners' Buffett thinks. [1]",
        "'Tis fabricated owners' Buffett recalls. [1]",
        "'Tis fabricated owners' Buffett notes. [1]",
        "'Tis fabricated owners' Buffett argues. [1]",
        "'Tis fabricated owners' Buffett has said. [1]",
        "'Tis fabricated owners' Buffett will reply. [1]",
        "'Tis fabricated owners' Buffett may respond. [1]",
        "'Tis fabricated owners' Warren Buffett thinks. [1]",
        "He said 'value' fabricated owners' Buffett has said. [1]",
        "'Tis fabricated owners' Buffett predicts. [1]",
        "'Tis fabricated owners' Buffett expects. [1]",
        "'Tis fabricated owners' Buffett remarks. [1]",
        "'Tis fabricated owners' Buffett cautions. [1]",
        "'Tis fabricated owners' Buffett asserts. [1]",
        "'Tis fabricated owners' Buffett opines. [1]",
        "'Tis fabricated owners' Buffett is saying. [1]",
        "'Tis fabricated owners' Buffett was quoted. [1]",
        "'Tis fabricated owners' Buffett had noted. [1]",
        "'Tis fabricated owners' Buffett does argue. [1]",
        "'Tis fabricated owners' Buffett can explain. [1]",
        "'Tis fabricated owners' Buffett should warn. [1]",
        "'Tis fabricated owners' Buffett must reply. [1]",
        "'Tis fabricated owners' Buffett would add. [1]",
        "'Tis fabricated owners' because. [1]",
        "'Tis fabricated owners' later. [1]",
        "'Tis fabricated owners' there. [1]",
        "'Tis fabricated owners' whether. [1]",
        "'Tis fabricated owners' publicly to reporters. [1]",
        "'Tis fabricated owners' quickly after lunch. [1]",
    ],
    ids=(
        "straight-elision",
        "curly-elision",
        "decade",
        "straight-trailing-delimiter",
        "curly-trailing-delimiter",
        "straight-elision-attribution",
        "curly-elision-attribution",
        "decade-attribution",
        "verified-prefix-attribution",
        "quote-introducer-variant",
        "paragraph-leading-elision-quote",
        "paragraph-leading-publicly",
        "paragraph-leading-again",
        "unknown-introducer-in",
        "distant-introducer-to",
        "verified-prefix-publicly",
        "during-testimony",
        "after-lunch",
        "before-lunch",
        "by-design",
        "as-written",
        "while-speaking",
        "now",
        "then",
        "proper-name-attribution",
        "verified-prefix-during",
        "citation-adjacent-elision",
        "citation-adjacent-decade",
        "citation-adjacent-prefix",
        "full-name-attribution",
        "proper-name-later-recalled",
        "proper-name-reportedly-said",
        "verified-prefix-full-name",
        "later-that-day",
        "verified-prefix-later-that-day",
        "buffett-thinks",
        "buffett-recalls",
        "buffett-notes",
        "buffett-argues",
        "buffett-has-said",
        "buffett-will-reply",
        "buffett-may-respond",
        "warren-buffett-thinks",
        "verified-prefix-has-said",
        "buffett-predicts",
        "buffett-expects",
        "buffett-remarks",
        "buffett-cautions",
        "buffett-asserts",
        "buffett-opines",
        "buffett-is-saying",
        "buffett-was-quoted",
        "buffett-had-noted",
        "buffett-does-argue",
        "buffett-can-explain",
        "buffett-should-warn",
        "buffett-must-reply",
        "buffett-would-add",
        "because",
        "later",
        "there",
        "whether",
        "publicly-to-reporters",
        "quickly-after-lunch",
    ),
)
def test_elisions_and_trailing_delimiters_cannot_bypass_quote_verification(
    tmp_path, statement
):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="owners and value")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Elision Quote", tmp_path))

    assert "fabricated" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "'Tis fabricated owners' Buffett thought. [1]",
        "'Tis fabricated owners' Buffett felt. [1]",
        "'Tis fabricated owners' Buffett knew. [1]",
        "'Tis fabricated owners' Buffett read. [1]",
        "'Tis fabricated owners' Buffett heard. [1]",
        "'Tis fabricated owners' Buffett put it plainly. [1]",
        "'Tis fabricated owners' Buffett shares his view. [1]",
        "'Tis fabricated owners' Buffett estimates the value. [1]",
        "'Tis fabricated owners' Buffett profits from the claim. [1]",
        "'Tis fabricated owners' Buffett returns to the point. [1]",
        "'Tis fabricated owners' Buffett brings this up. [1]",
        "'Tis fabricated owners' Buffett ought to reply. [1]",
        "'Tis fabricated owners' buffett predicts. [1]",
        "He said 'value' fabricated owners' Buffett thought. [1]",
        "He said 'value' fabricated owners' Buffett shares his view. [1]",
        "He said 'value' fabricated owners' Buffett brings this up. [1]",
        "He said 'value' fabricated owners' Buffett ought to reply. [1]",
        "He said 'value' fabricated owners' buffett predicts. [1]",
        "He says'fabricated owners'. [1]",
        "He says’fabricated owners’. [1]",
        "The report claims'fabricated risks'. [1]",
        "He says'all fabricated owners'. [1]",
        "He says'am fabricated owners'. [1]",
        "The report claims'all fabricated risks'. [1]",
        "He says'd fabricated owners'. [1]",
        "He says'll fabricated owners'. [1]",
        "He says'm fabricated owners'. [1]",
        "He says'n fabricated owners'. [1]",
        "He says're fabricated owners'. [1]",
        "He says's fabricated owners'. [1]",
        "He says't fabricated owners'. [1]",
        "He says've fabricated owners'. [1]",
        "I'fabricated owners'. [1]",
        "He says x'fabricated owners'. [1]",
        "He claims’re fabricated owners’. [1]",
        "He asserts's fabricated risks'. [1]",
        "He predicts's fabricated owners'. [1]",
        "He announces's fabricated loss'. [1]",
        "He asserted's fabricated risks'. [1]",
        "He announced's fabricated risks'. [1]",
        "He testified's fabricated risks'. [1]",
        "They maintain't-bills protect investors'. [1]",
        "He says' fabricated risks'. [1]",
        "He proclaim's fabricated risks'. [1]",
        "He reports' fabricated risks'. [1]",
        "Buffett cautions' fabricated risks'. [1]",
        "He alleges' fabricated risks'. [1]",
        "Buffett opines' fabricated risks'. [1]",
        "The investment committee reports' fabricated risks'. [1]",
        "A famous investor says' fabricated risks'. [1]",
        "He publicly reports' fabricated risks'. [1]",
        "He reportedly says' fabricated risks'. [1]",
        "Buffett publicly cautions' fabricated risks'. [1]",
        "Warren Buffett publicly opines' fabricated risks'. [1]",
    ],
)
def test_ambiguous_elision_boundaries_fail_closed_without_verb_allowlists(
    tmp_path, statement
):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="owners and value")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Structural Quote Boundary", tmp_path)
    )

    assert "fabricated" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "He said 'owners' and 'capital'. [1]",
        "The rules are 'avoid permanent loss' and 'stay patient'. [1]",
    ],
    ids=("plural-ending", "phrase-ending"),
)
def test_consecutive_single_quoted_phrases_are_verified_independently(
    tmp_path, statement
):
    evidence = list(_evidence())
    evidence[0] = replace(
        evidence[0], excerpt="owners capital avoid permanent loss stay patient"
    )
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Consecutive Quotes", tmp_path))

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        'He said "value" "quality". [1]',
        'He said "Buy"; she said "Hold". [1]',
        'He contrasted "value" with "quality". [1]',
        'He called "value", then "quality". [1]',
        'He ranked "alpha" followed by "beta". [1]',
        "He ranked 'alpha' and then 'beta'. [1]",
        "He chose “alpha” rather than “beta”. [1]",
        "He weighed «alpha» against «beta». [1]",
        'He moved from "alpha" to "beta". [1]',
        'He said "alpha". Then he said "beta". [1]',
        'He called one "alpha", another "beta". [1]',
        'He compared "value" to "quality". [1]',
        'He preferred "value" over "quality". [1]',
        'He chose "Buy" rather than "Hold". [1]',
        "He ranked ‘alpha’ followed by ‘beta’. [1]",
    ],
    ids=(
        "whitespace-separated",
        "semicolon-attribution",
        "contrasted-with",
        "called-then",
        "followed-by",
        "single-and-then",
        "curly-rather-than",
        "guillemets-against",
        "moved-from-to",
        "sentence-boundary",
        "called-one-another",
        "compared-to",
        "preferred-over",
        "buy-rather-than-hold",
        "curly-single-followed-by",
    ),
)
def test_sequential_double_quoted_phrases_are_verified_independently(
    tmp_path, statement
):
    evidence = list(_evidence())
    evidence[0] = replace(
        evidence[0], excerpt="alpha beta value quality Buy Hold"
    )
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Sequential Quotes", tmp_path))

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    ("opening", "closing"),
    [("\"", "\""), ("'", "'"), ("“", "”"), ("«", "»")],
    ids=("straight-double", "straight-single", "curly-double", "guillemets"),
)
@pytest.mark.parametrize(
    "template",
    [
        "He said {o}alpha{c}; later he said {o}beta{c}. [1]",
        "He said {o}alpha{c}; the analyst replied {o}beta{c}. [1]",
        "He compared {o}alpha{c} as opposed to {o}beta{c}. [1]",
        "He chose {o}alpha{c} instead of {o}beta{c}. [1]",
        "He placed {o}alpha{c} before {o}beta{c}. [1]",
        "He considered {o}alpha{c} after {o}beta{c}. [1]",
        "He paired {o}alpha{c} alongside {o}beta{c}. [1]",
        "He ranked {o}alpha{c}, followed immediately by {o}beta{c}. [1]",
        "He considered {o}alpha{c}, and finally {o}beta{c}. [1]",
        "He considered {o}alpha{c}, or perhaps {o}beta{c}. [1]",
        "He assessed {o}alpha{c} relative to {o}beta{c}. [1]",
        "He said {o}alpha{c}. Later he said {o}beta{c}. [1]",
        "He said {o}alpha{c}.\nThen {o}beta{c} followed. [1]",
        "He said {o}alpha{c}; Buffett said {o}beta{c}. [1]",
        "He said {o}alpha{c}; Warren Buffett said {o}beta{c}. [1]",
    ],
)
def test_sequential_quotes_use_open_vocabulary_local_structure(
    tmp_path, opening, closing, template
):
    statement = template.format(o=opening, c=closing)
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="alpha beta")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Open Vocabulary Sequential Quotes", tmp_path)
    )

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    ("opening", "closing"),
    [("\"", "\""), ("'", "'"), ("“", "”"), ("«", "»")],
    ids=("straight-double", "straight-single", "curly-double", "guillemets"),
)
@pytest.mark.parametrize("fabricated_first", [False, True])
def test_each_open_vocabulary_sequential_quote_is_verified_independently(
    tmp_path, opening, closing, fabricated_first
):
    first = "fabricated" if fabricated_first else "alpha"
    second = "beta" if fabricated_first else "fabricated"
    statement = (
        f"He compared {opening}{first}{closing} as opposed to "
        f"{opening}{second}{closing}. [1]"
    )
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="alpha beta")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Sequential Quote Independent Check", tmp_path)
    )

    assert "fabricated" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    ("opening", "closing"),
    [("\"", "\""), ("'", "'"), ("“", "”"), ("«", "»")],
    ids=("straight-double", "straight-single", "curly-double", "guillemets"),
)
@pytest.mark.parametrize(
    "separator",
    ["/", "—", "–", "|", "&", "+", ",", ":", ";"],
    ids=(
        "slash",
        "em-dash",
        "en-dash",
        "pipe",
        "ampersand",
        "plus",
        "comma",
        "colon",
        "semicolon",
    ),
)
def test_coherent_sequential_quotes_allow_punctuation_only_separators(
    tmp_path, opening, closing, separator
):
    statement = (
        f"He compared {opening}alpha{closing}{separator}"
        f"{opening}beta{closing}. [1]"
    )
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="alpha beta")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Punctuation Sequential Quotes", tmp_path)
    )

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    ("opening", "closing"),
    [("\"", "\""), ("'", "'"), ("“", "”"), ("«", "»")],
    ids=("straight-double", "straight-single", "curly-double", "guillemets"),
)
@pytest.mark.parametrize(
    "separator",
    ["/", "—", "–", "|", "&", "+", ",", ":", ";"],
    ids=(
        "slash",
        "em-dash",
        "en-dash",
        "pipe",
        "ampersand",
        "plus",
        "comma",
        "colon",
        "semicolon",
    ),
)
def test_punctuation_only_sequential_quotes_still_verify_each_span(
    tmp_path, opening, closing, separator
):
    statement = (
        f"He compared {opening}alpha{closing}{separator}"
        f"{opening}fabricated{closing}. [1]"
    )
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="alpha beta")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Punctuation Sequential Verification", tmp_path)
    )

    assert "fabricated" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    ("outer_open", "inner_open", "inner_close", "outer_close"),
    [
        ('"', '"', '"', '"'),
        ("'", "'", "'", "'"),
        ("“", "“", "”", "”"),
        ("«", "«", "»", "»"),
    ],
    ids=("straight-double", "straight-single", "curly-double", "guillemets"),
)
@pytest.mark.parametrize(
    "separator",
    ["/", "—", "–", "|", "&", "+", ",", ":", ";"],
    ids=(
        "slash",
        "em-dash",
        "en-dash",
        "pipe",
        "ampersand",
        "plus",
        "comma",
        "colon",
        "semicolon",
    ),
)
def test_punctuation_wrapped_inner_prose_is_not_a_sequential_separator(
    tmp_path, outer_open, inner_open, inner_close, outer_close, separator
):
    statement = (
        f"He said {outer_open}verified{inner_open}{separator}fabricated{separator}"
        f"{inner_close}verified{outer_close}. [1]"
    )
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="verified")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Punctuation Wrapped Nested Quote", tmp_path)
    )

    assert "fabricated" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "separator",
    ["\n", "\t"],
    ids=("newline", "tab"),
)
def test_verbatim_quotes_normalize_internal_whitespace(tmp_path, separator):
    statement = (
        f'He said "Buy only with a{separator}margin of safety". [1]'
    )
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Whitespace Normalized Quote", tmp_path)
    )

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    ("statement", "excerpt"),
    [
        ("He said 'protect owners' for investors' benefit. [1]", "protect owners"),
        ("He said 'avoid loss' for owners' capital. [1]", "avoid loss"),
        ("He said 'owners' and '$5'. [1]", "owners and $5"),
        ("He said 'owners' and '?'. [1]", "owners and ?"),
        ("He said 'owners' and '💎'. [1]", "owners and 💎"),
    ],
    ids=(
        "later-possessive",
        "later-possessive-loss",
        "currency-leading",
        "punctuation-leading",
        "emoji-leading",
    ),
)
def test_quote_boundaries_do_not_consume_later_possessives_or_symbol_quotes(
    tmp_path, statement, excerpt
):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt=excerpt)
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Quote Boundaries", tmp_path))

    assert statement in result.dossier_path.read_text()


def test_paragraph_leading_elision_quote_is_verified_when_balanced(tmp_path):
    statement = "'Tis risky'. [1]"
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="Tis risky")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Balanced Elision", tmp_path))

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "He said 'verified 'fabricated' verified'. [1]",
        "He said ‘verified ‘fabricated’ verified’. [1]",
        'He said "verified "fabricated" verified". [1]',
        "He said “verified “fabricated” verified”. [1]",
        "He said 'verified 'Tis fabricated' verified'. [1]",
        "He said 'verified '80s fabricated' verified'. [1]",
        'He said ""fabricated"". [1]',
        "He said ''fabricated''. [1]",
        'He said "verified“fabricated”verified". [1]',
        'He said “verified"fabricated"verified”. [1]',
        'He said "verified"fabricated"verified". [1]',
    ],
    ids=(
        "straight-single",
        "curly-single",
        "straight-double",
        "curly-double",
        "nested-leading-elision",
        "nested-leading-decade",
        "empty-double-pairs",
        "empty-single-pairs",
        "adjacent-mixed-curly",
        "adjacent-mixed-straight",
        "adjacent-straight",
    ),
)
def test_same_delimiter_nested_quotes_fail_closed(tmp_path, statement):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="verified")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Nested Quote", tmp_path))

    assert "fabricated" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    ("outer_open", "inner_open", "inner_close", "outer_close"),
    [
        ('"', '“', '”', '"'),
        ('“', '"', '"', '”'),
        ("'", "‘", "’", "'"),
        ("‘", "'", "'", "’"),
    ],
    ids=(
        "double-curly-inner",
        "double-straight-inner",
        "single-curly-inner",
        "single-straight-inner",
    ),
)
@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [("(", ")"), ("—", "—"), ("$", "$"), ("[", "]"), ("/", "/")],
    ids=("parentheses", "em-dash", "currency", "brackets", "slashes"),
)
def test_punctuation_adjacent_nested_quotes_fail_closed(
    tmp_path,
    outer_open,
    inner_open,
    inner_close,
    outer_close,
    prefix,
    suffix,
):
    statement = (
        f"He said {outer_open}verified{inner_open}{prefix}fabricated{suffix}"
        f"{inner_close}verified{outer_close}. [1]"
    )
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="verified")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Nested Punctuation", tmp_path))

    assert "fabricated" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        'He said "verified“ fabricated ”verified". [1]',
        'He said “verified" fabricated "verified”. [1]',
        "He said 'verified‘ fabricated ’verified'. [1]",
        "He said ‘verified' fabricated 'verified’. [1]",
        'He said "verified“\tfabricated\t”verified". [1]',
        'He said "verified“\nfabricated\n”verified". [1]',
        'He said "verified“\u00a0fabricated\u00a0”verified". [1]',
    ],
)
def test_whitespace_nested_quotes_fail_closed(tmp_path, statement):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="verified")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Nested Quote Whitespace", tmp_path)
    )

    assert "fabricated" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "He called it 'value'; it protects owners' stated interests. [1]",
        "He called it 'value'; compare investors' later returns. [1]",
        'He called it "value"; it protects owners\' added capital. [1]',
        "The record improved in '08. [1]",
        "Fiscal '24 saw margin expansion. [1]",
        "In fiscal '24, the record improved. [1]",
        "Class of '99 investors favored patience. [1]",
        "He said 'value', but investors' capital remained patient. [1]",
        "He said 'value' and investors' returns improved. [1]",
        "He said 'value' and shareholders' Berkshire returns improved. [1]",
        "He said 'protect owners' while dancin'. [1]",
        "Investors' monthly returns improved. [1]",
        "Investors' yearly returns improved. [1]",
        "Investors' publicly stated goals differ. [1]",
        "Analysts' early estimates improved. [1]",
        "Owners' only option was patience. [1]",
        "Owners' in-house counsel agreed. [1]",
        "He said 'protect owners' while tradin'. [1]",
        "He said 'protect owners' while holdin'. [1]",
        "He said 'protect owners' while compoundin'. [1]",
        "He said 'protect owners' while workin'. [1]",
        "Investors' Berkshire holdings grew. [1]",
        "Shareholders' S&P 500 holdings grew. [1]",
        "Investors' New York holdings grew. [1]",
        "Investors' U.S. holdings grew. [1]",
        "Investors' Berkshire holdings, however, grew. [1]",
        "Investors' Berkshire holdings; they grew. [1]",
        "Investors' Berkshire pricing improved. [1]",
        "Investors' Berkshire funding improved. [1]",
        "Investors' Berkshire supply improved. [1]",
        "Investors' New York financing improved. [1]",
        "Investors' S&P 500 weighting improved. [1]",
        "Investors' Berkshire profits improved. [1]",
        "Investors' Berkshire returns improved. [1]",
        "Investors' Berkshire revenues improved. [1]",
        "Investors' Berkshire ally objected. [1]",
        "Analysts' Berkshire anomaly persisted. [1]",
        "Investors' Berkshire claims increased. [1]",
        "Investors' Berkshire dividends increased. [1]",
        "Shareholders' S&P 500 dividends grew. [1]",
        "Investors' Berkshire preferred shares increased. [1]",
    ],
    ids=(
        "stated-interests",
        "later-returns",
        "after-double-quote",
        "abbreviated-year",
        "fiscal-year",
        "governed-fiscal-year",
        "class-year",
        "post-quote-but-possessive",
        "post-quote-and-possessive",
        "post-quote-and-proper-possessive",
        "trailing-elision",
        "monthly-returns",
        "yearly-returns",
        "publicly-stated-goals",
        "early-estimates",
        "only-option",
        "in-house-counsel",
        "tradin-elision",
        "holdin-elision",
        "compoundin-elision",
        "workin-elision",
        "berkshire-holdings",
        "sp500-holdings",
        "new-york-holdings",
        "us-holdings",
        "berkshire-holdings-comma",
        "berkshire-holdings-semicolon",
        "berkshire-pricing",
        "berkshire-funding",
        "berkshire-supply",
        "new-york-financing",
        "sp500-weighting",
        "berkshire-profits",
        "berkshire-returns",
        "berkshire-revenues",
        "berkshire-ally",
        "berkshire-anomaly",
        "berkshire-claims",
        "berkshire-dividends",
        "sp500-dividends",
        "berkshire-preferred-shares",
    ),
)
def test_local_apostrophe_forms_remain_valid_after_quotes(tmp_path, statement):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="value protect owners")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Local Apostrophes", tmp_path))

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "He said 'value', however investors' capital improved. [1]",
        "He said 'value', yet investors' capital improved. [1]",
        "He said 'value', because investors' capital improved. [1]",
        "He said 'value', while investors' capital improved. [1]",
        "He said 'value', so investors' capital improved. [1]",
        "He said 'value', as investors' capital improved. [1]",
        "He said 'value', although investors' capital improved. [1]",
    ],
)
def test_clause_boundary_preserves_post_quote_possessives(tmp_path, statement):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt="value")
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Clause Possessive", tmp_path))

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "Calendar '24 improved. [1]",
        "Q4 '24 improved. [1]",
        "Tax year '24 improved. [1]",
        "Summer '24 improved. [1]",
        "Performance versus '23 improved. [1]",
        "Since Q1 '24 performance improved. [1]",
        "January '24 improved. [1]",
        "December ’23 improved. [1]",
        "H1 '24 improved. [1]",
        "H2 ’24 improved. [1]",
        "CY '24 improved. [1]",
        "1Q '24 improved. [1]",
        "FQ1 '24 improved. [1]",
        "Early '24 improved. [1]",
        "Late '24 improved. [1]",
        "During '23 and '24 performance improved. [1]",
        "FY '23 and '24 performance improved. [1]",
    ],
)
def test_finance_year_apostrophes_are_not_quotes(tmp_path, statement):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Finance Years", tmp_path))

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "mark",
    ["\u0300", "\u0301", "\u030b", "\u030f", "\u200d\u0301"],
)
def test_standalone_combining_marks_cannot_hide_quotes(tmp_path, mark):
    statement = f"He said {mark}fabricated claim{mark}. [1]"
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Combining Mark Quote", tmp_path)
    )

    assert "fabricated claim" not in result.dossier_path.read_text()


@pytest.mark.parametrize("mark", ["\u0374", "\u1fef"])
def test_nfkc_quote_compatibility_characters_fail_closed(tmp_path, mark):
    statement = f"He said {mark}fabricated claim{mark}. [1]"
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("NFKC Quote Compatibility", tmp_path)
    )

    assert "fabricated claim" not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "The ＡＰＩ field remains ordinary prose. [1]",
        "The ① metric and ㎏ measure remain ordinary prose. [1]",
    ],
)
def test_benign_nfkc_compatibility_text_remains_prose(tmp_path, statement):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Benign NFKC Prose", tmp_path)
    )

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "mark",
    ["\u0301", "\u030b", "\u0313", "\u0314", "\u200d\u0301"],
)
def test_decomposed_accent_in_prose_is_not_a_quote(tmp_path, mark):
    statement = f"The cafe{mark}'s business improved. [1]"
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Decomposed Accent Prose", tmp_path)
    )

    assert statement in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "excerpt",
    ["value\u0301ly", "value\u200dly", "value\u200cly", "value\ufe0fly"],
)
def test_verbatim_quote_boundaries_include_unicode_marks(tmp_path, excerpt):
    evidence = list(_evidence())
    evidence[0] = replace(evidence[0], excerpt=excerpt)
    statement = 'He said "value". [1]'
    backend = FakeBackend(tuple(evidence), voice_addendum=statement)

    result = _factory(backend).run(
        ResearchRequest("Unicode Quote Boundary", tmp_path)
    )

    assert statement not in result.dossier_path.read_text()


@pytest.mark.parametrize(
    "statement",
    [
        "The investor said 'This quote never appeared. [1]",
        "The investor said ‘This quote never appeared. [1]",
        "The investor said “This quote never appeared. [1]",
        "The investor said ”This quote never appeared. [1]",
        "He said 'Tis fabricated owners. [1]",
        "He said '80s fabricated owners. [1]",
        "He said ’Tis fabricated owners. [1]",
        "He predicts 'Tis fabricated owners. [1]",
        "He whispers 'Tis fabricated owners. [1]",
        "He predicts '80s fabricated owners. [1]",
        "He said, 'Tis fabricated owners. [1]",
    ],
    ids=(
        "straight-single",
        "curly-single",
        "left-double",
        "right-double",
        "straight-elision",
        "abbreviated-year",
        "right-curly-elision",
        "arbitrary-present-introducer",
        "arbitrary-introducer",
        "arbitrary-year-introducer",
        "comma-introducer",
    ),
)
def test_unmatched_quote_openers_fail_closed(tmp_path, statement):
    backend = FakeBackend(_evidence(), voice_addendum=statement)

    result = _factory(backend).run(ResearchRequest("Malformed Quote", tmp_path))

    dossier = result.dossier_path.read_text()
    assert "This quote never appeared" not in dossier
    assert "fabricated owners" not in dossier


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


def test_dual_write_cleans_first_stage_when_second_staging_fails(
    tmp_path, monkeypatch
):
    import tinyic.personas.factory.pipeline as factory_pipeline

    original = factory_pipeline._stage_file
    calls = 0

    def fail_second_stage(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second staging failure")
        return original(path, content)

    monkeypatch.setattr(factory_pipeline, "_stage_file", fail_second_stage)
    with pytest.raises(OSError, match="second staging"):
        write_artifact_pair(
            tmp_path / "staging.agent.json",
            b"agent",
            tmp_path / "staging.dossier.md",
            b"dossier",
            force=False,
        )

    assert not list(tmp_path.iterdir())


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


def test_published_schema_requires_generation_and_nonempty_tinyic_text():
    tinyic_schema = AGENT_SCHEMA["properties"]["tinyic"]

    assert "generation" in tinyic_schema["required"]
    assert tinyic_schema["properties"]["epithet"]["minLength"] == 1
    assert tinyic_schema["properties"]["philosophy_hook"]["minLength"] == 1
    assert (
        tinyic_schema["properties"]["decision_checklist"]["items"][
            "minLength"
        ]
        == 1
    )


@pytest.mark.parametrize("invalid_date", ["20260715", "2026-W29-3"])
def test_schema_dates_require_calendar_yyyy_mm_dd(invalid_date):
    specification = json.loads(
        (_FIXTURES / "ada_value.agent.json").read_text(encoding="utf-8")
    )
    specification["tinyic"]["sources"][0]["accessed"] = invalid_date
    specification["tinyic"]["generation"]["date"] = invalid_date

    with pytest.raises(SchemaValidationError) as error:
        validate_agent_spec(specification)

    message = str(error.value)
    assert "tinyic.sources[0].accessed" in message
    assert "tinyic.generation.date" in message


@pytest.mark.parametrize(
    "path",
    [
        "root.unverified_blob",
        "persona.private_life",
        "persona.occupation.private",
        "persona.personality.private",
        "persona.behaviors.private",
        "persona.preferences.private",
        "tinyic.private_notes",
        "tinyic.famous_quotes[0].private",
        "tinyic.sources[0].private",
        "tinyic.generation.private",
    ],
)
def test_schema_rejects_unknown_fields_at_every_object_level(path):
    specification = json.loads(
        (_FIXTURES / "ada_value.agent.json").read_text(encoding="utf-8")
    )
    if path == "root.unverified_blob":
        specification["unverified_blob"] = "forbidden"
    elif path == "persona.private_life":
        specification["persona"]["private_life"] = "forbidden"
    elif path == "persona.occupation.private":
        specification["persona"]["occupation"]["private"] = "forbidden"
    elif path == "persona.personality.private":
        specification["persona"]["personality"]["private"] = "forbidden"
    elif path == "persona.behaviors.private":
        specification["persona"]["behaviors"]["private"] = "forbidden"
    elif path == "persona.preferences.private":
        specification["persona"]["preferences"]["private"] = []
    elif path == "tinyic.private_notes":
        specification["tinyic"]["private_notes"] = "forbidden"
    elif path == "tinyic.famous_quotes[0].private":
        specification["tinyic"]["famous_quotes"][0]["private"] = "forbidden"
    elif path == "tinyic.sources[0].private":
        specification["tinyic"]["sources"][0]["private"] = "forbidden"
    else:
        specification["tinyic"]["generation"]["private"] = "forbidden"

    with pytest.raises(SchemaValidationError) as error:
        validate_agent_spec(specification)

    assert path in str(error.value)


def test_factory_rejects_unknown_model_fields_without_writes(tmp_path):
    class UnknownFieldBackend(FakeBackend):
        def synthesize_persona(self, request):
            response = super().synthesize_persona(request)
            response.specification["persona"]["medical_history"] = "private"
            response.specification["tinyic"]["private_notes"] = "private"
            response.specification["unverified_blob"] = "private"
            return response

    with pytest.raises(SchemaValidationError) as error:
        _factory(UnknownFieldBackend(_evidence())).run(
            ResearchRequest("Strict Persona", tmp_path)
        )

    message = str(error.value)
    assert "persona.medical_history" in message
    assert "tinyic.private_notes" in message
    assert "root.unverified_blob" in message
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("phrase", "category"),
    [
        ("He is 76 years old. [1]", "age"),
        ("His dau\u200bghter manages the family trust. [1]", "family"),
        ("She was diagnosed with cancer. [1]", "health"),
        ("He currently lives at 123 Main Street. [1]", "residence"),
        (
            "This persona is officially endorsed by the represented investor. [1]",
            "affiliation or endorsement",
        ),
    ],
)
def test_dossier_scope_gate_rejects_excluded_personal_content_before_persona(
    tmp_path, phrase, category
):
    backend = FakeBackend(_evidence(), voice_addendum=phrase)

    with pytest.raises(SchemaValidationError) as error:
        _factory(backend).run(ResearchRequest("Scoped Dossier", tmp_path))

    assert f"dossier.voice[1] contains excluded {category} content" in str(error.value)
    assert backend.persona_requests == []
    assert not list(tmp_path.iterdir())


_MODEL_GENERATED_CLAIM_PATHS = (
    ("persona", "occupation", "title"),
    ("persona", "occupation", "organization"),
    ("persona", "style"),
    ("persona", "beliefs", 0),
    ("persona", "skills", 0),
    ("persona", "other_facts", 0),
    ("persona", "personality", "traits", 0),
    ("persona", "behaviors", "general", 0),
    ("persona", "preferences", "interests", 0),
    ("persona", "preferences", "likes", 0),
    ("persona", "preferences", "dislikes", 0),
    ("tinyic", "epithet"),
    ("tinyic", "philosophy_hook"),
    ("tinyic", "decision_checklist", 0),
    ("tinyic", "signal_rules", 0),
    ("tinyic", "red_flags", 0),
    ("tinyic", "famous_quotes", 0, "text"),
)


def _set_model_generated_claim(specification, path, value):
    parent = specification
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = value
    if path == ("tinyic", "epithet"):
        specification["persona"]["occupation"]["description"] = value


@pytest.mark.parametrize(
    "path",
    _MODEL_GENERATED_CLAIM_PATHS,
    ids=lambda path: ".".join(map(str, path)),
)
def test_persona_scope_gate_covers_every_model_generated_claim_surface(
    tmp_path, path
):
    class PrivateClaimBackend(FakeBackend):
        def synthesize_persona(self, request):
            response = super().synthesize_persona(request)
            _set_model_generated_claim(
                response.specification,
                path,
                "His daughter manages the family trust.",
            )
            return response

    with pytest.raises(SchemaValidationError) as error:
        _factory(PrivateClaimBackend(_evidence())).run(
            ResearchRequest("Scoped Persona", tmp_path)
        )

    assert "contains excluded family content" in str(error.value)
    assert not list(tmp_path.iterdir())


def test_scope_gate_keeps_investment_context_with_overlapping_words(tmp_path):
    statement = (
        "The family-owned business maintains healthy margins, serves "
        "retirement-age customers, owns residential real estate, and management "
        "endorsed disciplined capital allocation. [1]"
    )

    result = _factory(
        FakeBackend(_evidence(), voice_addendum=statement)
    ).run(ResearchRequest("Conservative Scope", tmp_path))

    assert statement in result.dossier_path.read_text(encoding="utf-8")


def test_factory_owns_identity_and_neutral_temperament(tmp_path):
    class ForgedIdentityBackend(FakeBackend):
        def synthesize_persona(self, request):
            response = super().synthesize_persona(request)
            response.specification["persona"]["name"] = "Someone Else"
            response.specification["tinyic"]["schema_version"] = 999
            response.specification["tinyic"]["temperament"] = "contrarian"
            return response

    result = _factory(ForgedIdentityBackend(_evidence())).run(
        ResearchRequest("Factory Identity", tmp_path)
    )
    specification = json.loads(result.agent_path.read_text())

    assert specification["type"] == "TinyPerson"
    assert specification["persona"]["name"] == "Factory Identity"
    assert specification["tinyic"]["schema_version"] == 1
    assert specification["tinyic"]["temperament"] == "balanced"


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://journal.example/private?access_token=TEST_SECRET_DO_NOT_USE"
        "&X-Amz-Signature=SIGNED_VALUE",
        "https://journal.example/private?year=1%3Baccess_token=TEST_SECRET_DO_NOT_USE",
        "https://journal.example/private?access%255Ftoken=TEST_SECRET_DO_NOT_USE",
        "https://journal.example/private?%D0%B0ccess_token=TEST_SECRET_DO_NOT_USE",
        "https://journal.example/private?code=TEST_SECRET_DO_NOT_USE",
        "https://journal.example/private;jsessionid=TEST_SECRET_DO_NOT_USE",
    ],
)
def test_factory_drops_credential_bearing_source_before_dual_artifacts(
    tmp_path, unsafe_url
):
    secret = "TEST_SECRET_DO_NOT_USE"
    unsafe = replace(
        _evidence()[-1],
        url=unsafe_url,
    )
    backend = FakeBackend(_evidence()[:-1] + (unsafe,))

    result = _factory(backend).run(
        ResearchRequest("Secret URL Source", tmp_path)
    )

    agent_text = result.agent_path.read_text(encoding="utf-8")
    dossier_text = result.dossier_path.read_text(encoding="utf-8")
    assert secret not in agent_text
    assert secret not in dossier_text
    assert result.source_count == 5


def test_backend_cannot_mutate_factory_owned_sources_or_generation(tmp_path):
    class MutatingMetadataBackend(FakeBackend):
        def synthesize_persona(self, request):
            request.generation["model_ref"] = "forged/backend"
            request.generation["search_calls"] = 777
            request.source_records[0]["title"] = "Forged title"
            request.source_records[0]["url"] = "https://forged.example/source"
            return super().synthesize_persona(request)

    result = _factory(MutatingMetadataBackend(_evidence())).run(
        ResearchRequest("Immutable Metadata", tmp_path)
    )
    specification = json.loads(result.agent_path.read_text(encoding="utf-8"))
    generation = specification["tinyic"]["generation"]
    first_source = specification["tinyic"]["sources"][0]

    assert generation["model_ref"] == "openai/fake-research"
    assert generation["search_calls"] == 6
    assert first_source["title"] == "Source 1"
    assert first_source["url"] == "https://letters.example/source-1"
    dossier = result.dossier_path.read_text(encoding="utf-8")
    assert "Forged title" not in dossier
    assert "forged.example" not in dossier
