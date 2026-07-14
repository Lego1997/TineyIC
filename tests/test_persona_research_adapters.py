"""Offline wire-contract tests for persona-research provider backends."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from tinyic.models import ModelBinding, StaticCredentialProvider
from tinyic.models.adapters._http import HttpRequest
from tinyic.models.research import (
    GeminiResearchBackend,
    GrokResearchBackend,
    KimiResearchBackend,
    NoSearchCapableLaneError,
    OpenAIResearchBackend,
    make_research_backend,
    select_research_backend,
)
from tinyic.personas.factory import (
    DossierSynthesisRequest,
    Evidence,
    ResearchBackend,
    SearchQuery,
    SearchRequest,
    VerificationRequest,
)


KEY = "test-key-never-log"


@dataclass
class FakeResponse:
    status_code: int = 200
    body: str = "{}"
    headers: dict[str, str] = field(default_factory=dict)
    closed: bool = False

    def header(self, name: str) -> str | None:
        return next(
            (
                value
                for key, value in self.headers.items()
                if key.casefold() == name.casefold()
            ),
            None,
        )

    def iter_lines(self):
        yield from ()

    def read_text(self) -> str:
        return self.body

    def close(self) -> None:
        self.closed = True


class ScriptedTransport:
    def __init__(self, *responses: dict) -> None:
        self.responses = [FakeResponse(body=json.dumps(item)) for item in responses]
        self.sent: list[HttpRequest] = []
        self.closed = False

    def send(self, request: HttpRequest) -> FakeResponse:
        self.sent.append(request)
        assert self.responses, "research backend sent an unscripted request"
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def credentials(**overrides: str) -> StaticCredentialProvider:
    values = {
        "OPENAI_API_KEY": KEY,
        "XAI_API_KEY": KEY,
        "GEMINI_API_KEY": KEY,
        "MOONSHOT_API_KEY": KEY,
    }
    values.update(overrides)
    return StaticCredentialProvider(values)


def request(*, seeds: tuple[str, ...] = ()) -> SearchRequest:
    return SearchRequest(
        "Howard Marks",
        SearchQuery(
            "risk",
            "Howard Marks risk discipline primary-source memo",
            seeds,
        ),
    )


def _annotation(text: str, phrase: str, url: str, title: str) -> dict:
    start = text.index(phrase)
    return {
        "type": "url_citation",
        "url": url,
        "title": title,
        "start_index": start,
        "end_index": start + len(phrase),
    }


def test_openai_forces_web_search_includes_sources_and_normalizes_citations() -> None:
    text = "Risk control comes first. Patient capital follows."
    primary = "https://www.oaktreecapital.com/insights/memos/risk"
    secondary = "https://www.sec.gov/Archives/example"
    wire = {
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "type": "search",
                    "queries": ["Howard Marks risk control"],
                    "sources": [
                        {"type": "url", "url": primary},
                        {"type": "url", "url": secondary, "title": "SEC filing"},
                    ],
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [
                            _annotation(text, "Risk control comes first", primary, "Oaktree memo")
                        ],
                    }
                ],
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 7},
        },
    }
    http = ScriptedTransport(wire)
    backend = OpenAIResearchBackend(
        ModelBinding("openai/gpt-5.6-sol"), credentials(), http=http
    )

    result = backend.search(request(seeds=("https://www.oaktreecapital.com/insights/memos",)))

    sent = http.sent[0]
    assert sent.url == "https://api.openai.com/v1/responses"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert sent.body["tools"] == [
        {"type": "web_search", "search_context_size": "high"}
    ]
    assert sent.body["tool_choice"] == "required"
    assert sent.body["include"] == ["web_search_call.action.sources"]
    assert "Howard Marks risk discipline" in sent.body["input"]
    assert result.evidence == (
        Evidence(
            primary,
            "Oaktree memo",
            "Risk control comes first",
            request().query.text,
            "openai",
            "primary",
        ),
        Evidence(
            secondary,
            "SEC filing",
            text,
            request().query.text,
            "openai",
            "secondary",
        ),
    )
    assert result.usage is not None
    assert result.usage.purpose == "search"
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 20
    assert result.usage.cached_tokens == 7
    # Token price plus the required $0.01 web-search invocation.
    assert result.usage.cost_usd == pytest.approx(0.0111)


def test_grok_uses_responses_and_merges_flat_and_inline_citations() -> None:
    text = (
        "Marks frames risk as the possibility of permanent loss."
        "[[1]](https://oaktreecapital.com/risk)"
    )
    cited = "https://oaktreecapital.com/risk"
    other = "https://example.org/marks-profile"
    marker = f"[[1]]({cited})"
    wire = {
        "citations": [cited, other, "http://localhost/private"],
        "output": [
            {"type": "web_search_call", "status": "completed"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [
                            _annotation(text, marker, cited, "1")
                        ],
                    }
                ],
            },
        ],
        "usage": {"input_tokens": 50, "output_tokens": 10},
    }
    http = ScriptedTransport(wire)
    backend = GrokResearchBackend(
        ModelBinding("grok/grok-4.5"), credentials(), http=http
    )

    result = backend.search(request())

    sent = http.sent[0]
    assert sent.url == "https://api.x.ai/v1/responses"
    assert sent.body["tools"] == [{"type": "web_search"}]
    assert "tool_choice" not in sent.body
    assert [item.url for item in result.evidence] == [cited, other]
    assert result.evidence[0].title == "oaktreecapital.com"
    assert result.evidence[0].excerpt == (
        "Marks frames risk as the possibility of permanent loss."
    )
    assert result.evidence[1].excerpt == text
    assert result.usage is not None
    assert result.usage.cost_usd == pytest.approx(0.00516)


def test_gemini_interactions_google_search_uses_grounding_rows_and_annotations() -> None:
    text = "The memo says cycles are inevitable and investor behavior matters."
    primary = "https://www.oaktreecapital.com/insights/memos/cycles"
    secondary = "https://example.net/marks"
    wire = {
        "steps": [
            {
                "type": "google_search_call",
                "arguments": {"queries": ["Marks cycles", "Marks risk"]},
            },
            {
                "type": "google_search_result",
                "result": [
                    {
                        "url": primary,
                        "title": "Oaktree Cycles",
                        "snippet": "Cycles are inevitable.",
                    },
                    {
                        "url": secondary,
                        "title": "Investor profile",
                        "snippet": "A public profile.",
                    },
                ],
            },
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "annotations": [
                            _annotation(text, "cycles are inevitable", primary, "Oaktree Cycles")
                        ],
                    }
                ],
            },
        ],
        "usage": {
            "total_input_tokens": 90,
            "total_output_tokens": 15,
            "total_cached_tokens": 3,
        },
    }
    http = ScriptedTransport(wire)
    backend = GeminiResearchBackend(
        ModelBinding("google/gemini-3.5-flash"), credentials(), http=http
    )

    result = backend.search(request(seeds=("https://www.oaktreecapital.com/insights/memos",)))

    sent = http.sent[0]
    assert sent.url == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert sent.headers["x-goog-api-key"] == KEY
    assert "authorization" not in sent.headers
    assert sent.body["tools"] == [{"type": "google_search"}]
    assert [item.url for item in result.evidence] == [primary, secondary]
    assert result.evidence[0].excerpt == "Cycles are inevitable."
    assert result.evidence[0].source_type == "primary"
    assert result.usage is not None
    assert result.usage.cached_tokens == 3
    # Two queries were reported by the one search call.
    assert result.usage.cost_usd == pytest.approx(0.028093)


def test_gemini_normalizes_legacy_grounding_metadata_without_legacy_request() -> None:
    text = "Risk means more things can happen than will happen."
    url = "https://www.oaktreecapital.com/insights/memos/risk"
    phrase = "more things can happen than will happen"
    wire = {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "groundingMetadata": {
                    "groundingChunks": [{"web": {"uri": url, "title": "Risk memo"}}],
                    "groundingSupports": [
                        {
                            "segment": {
                                "startIndex": text.index(phrase),
                                "endIndex": text.index(phrase) + len(phrase),
                            },
                            "groundingChunkIndices": [0],
                        }
                    ],
                },
            }
        ],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 4},
    }
    http = ScriptedTransport(wire)
    backend = GeminiResearchBackend(
        ModelBinding("google/gemini-3.5-flash"), credentials(), http=http
    )

    result = backend.search(request())

    assert result.evidence[0].url == url
    assert result.evidence[0].excerpt == phrase
    assert http.sent[0].url.endswith("/interactions")


def test_kimi_echoes_builtin_results_verbatim_and_extracts_only_final_prose_urls() -> None:
    arguments = '{"results":[{"url":"https://tool-only.invalid/result"}]}'
    first = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-7",
                            "type": "builtin_function",
                            "function": {
                                "name": "$web_search",
                                "arguments": arguments,
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    final_text = (
        "Oaktree emphasizes risk control in its "
        "[memo archive](https://www.oaktreecapital.com/insights/memos)."
    )
    second = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": final_text},
            }
        ],
        "usage": {"prompt_tokens": 15, "completion_tokens": 7},
    }
    http = ScriptedTransport(first, second)
    backend = KimiResearchBackend(
        ModelBinding("kimi/kimi-k2.6", thinking_level="high"),
        credentials(),
        http=http,
    )

    result = backend.search(request())

    assert backend.binding.thinking_level.value == "off"
    assert backend.degraded is True
    assert backend.citation_quality == "prose_urls"
    assert len(http.sent) == 2
    first_body = http.sent[0].body
    assert first_body["tools"] == [
        {"type": "builtin_function", "function": {"name": "$web_search"}}
    ]
    assert first_body["thinking"] == {"type": "disabled"}
    assert "complete http(s) source URL" in first_body["messages"][0]["content"]
    echoed = http.sent[1].body["messages"]
    assert echoed[1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-7",
                "type": "builtin_function",
                "function": {"name": "$web_search", "arguments": arguments},
            }
        ],
    }
    assert echoed[2] == {
        "role": "tool",
        "tool_call_id": "call-7",
        "name": "$web_search",
        "content": arguments,
    }
    assert [item.url for item in result.evidence] == [
        "https://www.oaktreecapital.com/insights/memos"
    ]
    assert "tool-only.invalid" not in result.evidence[0].excerpt
    assert result.usage is not None
    assert result.usage.calls == 2
    assert result.usage.input_tokens == 25
    assert result.usage.output_tokens == 9
    assert result.usage.cost_usd == pytest.approx(0.00505975)


def test_non_search_methods_share_usage_and_json_normalization() -> None:
    dossier_wire = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Evidence-led prose. [1]"}],
            }
        ],
        "usage": {"input_tokens": 20, "output_tokens": 5},
    }
    verify_wire = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": '```json\n{"claim-1":[1,1,2],"bad":"no"}\n```',
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 30, "output_tokens": 8},
    }
    http = ScriptedTransport(dossier_wire, verify_wire)
    backend = OpenAIResearchBackend(
        ModelBinding("openai/gpt-5.6-sol"), credentials(), http=http
    )
    evidence = (Evidence("https://example.com/a", "A", "Excerpt"),)

    dossier = backend.synthesize_dossier(
        DossierSynthesisRequest("Howard Marks", "risk", "Risk", "Prompt", evidence)
    )
    verified = backend.verify(
        VerificationRequest("Howard Marks", "Verify", (), evidence)
    )

    assert isinstance(backend, ResearchBackend)
    assert dossier.text == "Evidence-led prose. [1]"
    assert dossier.usage is not None and dossier.usage.purpose == "synthesis"
    assert verified.supported == {"claim-1": (1, 2)}
    assert verified.usage is not None
    assert verified.usage.purpose == "verification"
    assert all("tools" not in sent.body for sent in http.sent)


def test_factory_supports_explicit_binding_and_google_credential_alias() -> None:
    backend = make_research_backend(
        ModelBinding("google/gemini-3.5-flash"),
        StaticCredentialProvider({"GOOGLE_API_KEY": KEY}),
        http=ScriptedTransport(),
    )

    assert isinstance(backend, GeminiResearchBackend)
    assert backend.model_ref == "google/gemini-3.5-flash"


def test_selector_honors_openai_grok_google_kimi_priority_not_input_order() -> None:
    selected = select_research_backend(
        [
            ModelBinding("kimi/kimi-k2.6"),
            ModelBinding("google/gemini-3.5-flash"),
            ModelBinding("grok/grok-4.5"),
        ],
        StaticCredentialProvider(
            {"MOONSHOT_API_KEY": KEY, "GEMINI_API_KEY": KEY, "XAI_API_KEY": KEY}
        ),
        http=ScriptedTransport(),
    )

    assert isinstance(selected, GrokResearchBackend)


def test_selector_skips_uncredentialed_higher_priority_lane() -> None:
    selected = select_research_backend(
        [ModelBinding("openai/gpt-5.6-sol"), ModelBinding("kimi/kimi-k2.6")],
        StaticCredentialProvider({"MOONSHOT_API_KEY": KEY}),
        http=ScriptedTransport(),
    )

    assert isinstance(selected, KimiResearchBackend)


def test_selector_reports_no_search_capable_lane_without_touching_network() -> None:
    with pytest.raises(NoSearchCapableLaneError) as caught:
        select_research_backend(
            [ModelBinding("openai/gpt-5.6-sol"), ModelBinding("kimi/kimi-k2.6")],
            StaticCredentialProvider({}),
            http=ScriptedTransport(),
        )

    assert caught.value.reason_code == "no_search_capable_lane"
    assert KEY not in str(caught.value)
