"""Opt-in one-call live smokes for persona-research search adapters.

These tests are deselected by the repository's default marker expression. They
must collect offline and only spend quota when explicitly selected with the
matching provider credential present.
"""

from __future__ import annotations

import os

import pytest

from tinyic.models import EnvCredentialProvider, ModelBinding
from tinyic.models.research import make_research_backend
from tinyic.personas.factory import SearchQuery, SearchRequest


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
