"""Opt-in one-call live smokes for persona-research search adapters.

These tests are deselected by the repository's default marker expression. They
must collect offline and only spend quota when explicitly selected with the
matching provider credential present.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tinyic.cli import main
from tinyic.models import EnvCredentialProvider, ModelBinding
from tinyic.models.research import make_research_backend
from tinyic.personas.factory import SearchQuery, SearchRequest, validate_agent_spec


pytestmark = pytest.mark.live_api


def _request() -> SearchRequest:
    return SearchRequest(
        "Howard Marks",
        SearchQuery(
            "philosophy",
            "Howard Marks investment philosophy primary-source Oaktree memo",
            ("https://www.oaktreecapital.com/insights/memos",),
        ),
    )


def _require_any(*refs: str) -> None:
    if not any(str(os.environ.get(ref) or "").strip() for ref in refs):
        pytest.skip(f"set one of {', '.join(refs)} to run this live smoke")


def _assert_cited(result) -> None:
    assert result.evidence
    assert all(item.url.startswith(("http://", "https://")) for item in result.evidence)
    assert result.usage is not None
    assert result.usage.calls >= 1
    assert result.usage.cost_usd is not None


def test_openai_research_search_live() -> None:
    _require_any("OPENAI_API_KEY")
    backend = make_research_backend(
        ModelBinding("openai/gpt-5.6-sol"), EnvCredentialProvider()
    )
    try:
        _assert_cited(backend.search(_request()))
    finally:
        backend.close()

def test_grok_research_search_live() -> None:
    _require_any("XAI_API_KEY")
    backend = make_research_backend(
        ModelBinding("grok/grok-4.5"), EnvCredentialProvider()
    )
    try:
        _assert_cited(backend.search(_request()))
    finally:
        backend.close()


def test_gemini_research_search_live() -> None:
    _require_any("GEMINI_API_KEY", "GOOGLE_API_KEY")
    backend = make_research_backend(
        ModelBinding("google/gemini-3.5-flash"), EnvCredentialProvider()
    )
    try:
        _assert_cited(backend.search(_request()))
    finally:
        backend.close()


def test_kimi_research_search_live() -> None:
    _require_any("MOONSHOT_API_KEY", "KIMI_API_KEY")
    backend = make_research_backend(
        ModelBinding("kimi/kimi-k2.6", thinking_level="off"),
        EnvCredentialProvider(),
    )
    try:
        _assert_cited(backend.search(_request()))
    finally:
        backend.close()


def test_persona_research_cli_howard_marks_live() -> None:
    """Opt-in live factory smoke through the public CLI boundary.

    ``howard_marks`` is a protected built-in, so this intentionally uses the
    explicit non-built-in slug recorded as the frozen-plan collision deviation.
    """
    requested = str(os.environ.get("TINYIC_LIVE_RESEARCH_MODEL") or "").strip()
    lanes = (
        ("openai/gpt-5.6-sol", ("OPENAI_API_KEY",)),
        ("grok/grok-4.5", ("XAI_API_KEY",)),
        ("google/gemini-3.5-flash", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
        ("kimi/kimi-k2.6", ("MOONSHOT_API_KEY", "KIMI_API_KEY")),
    )
    if requested:
        provider = requested.partition("/")[0].casefold()
        refs = next(
            (refs for model_ref, refs in lanes if model_ref.startswith(f"{provider}/")),
            (),
        )
        _require_any(*refs)
        model_ref = requested
    else:
        model_ref = next(
            (
                candidate
                for candidate, refs in lanes
                if any(str(os.environ.get(ref) or "").strip() for ref in refs)
            ),
            "",
        )
        if not model_ref:
            pytest.skip("set a supported research provider API key")

    slug = "howard_marks_live_research"
    assert (
        main(
            [
                "persona",
                "research",
                "Howard Marks",
                "--model",
                model_ref,
                "--slug",
                slug,
                "--max-searches",
                "4",
                "--yes",
                "--force",
            ]
        )
        == 0
    )
    output_dir = Path(os.environ["TINYIC_PERSONAS_DIR"])
    agent_path = output_dir / f"{slug}.agent.json"
    dossier_path = output_dir / f"{slug}.dossier.md"
    assert agent_path.is_file() and dossier_path.is_file()
    validate_agent_spec(json.loads(agent_path.read_text(encoding="utf-8")))
    assert "## Sources" in dossier_path.read_text(encoding="utf-8")
