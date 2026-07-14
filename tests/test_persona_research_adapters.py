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
    UnsupportedResearchModelError,
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


def request(
    *,
    seeds: tuple[str, ...] = (),
    remaining_searches: int | None = None,
) -> SearchRequest:
    return SearchRequest(
        "Howard Marks",
        SearchQuery(
            "risk",
            "Howard Marks risk discipline primary-source memo",
            seeds,
        ),
        remaining_searches=remaining_searches,
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
                "type": "web_search_call",
                "action": {
                    "type": "search",
                    "queries": ["Howard Marks patient capital"],
                    "sources": [],
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

    result = backend.search(
        request(
            seeds=("https://www.oaktreecapital.com/insights/memos",),
            remaining_searches=2,
        )
    )

    sent = http.sent[0]
    assert sent.url == "https://api.openai.com/v1/responses"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert sent.body["tools"] == [
        {"type": "web_search", "search_context_size": "high"}
    ]
    assert sent.body["tool_choice"] == "required"
    assert sent.body["include"] == ["web_search_call.action.sources"]
    assert sent.body["max_tool_calls"] == 2
    assert "Use at most 2 provider-side search" in sent.body["input"]
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
    assert result.usage.search_calls == 2
    assert result.budget_exhausted is True
    # Token price plus two reported $0.01 web-search invocations.
    assert result.usage.cost_usd == pytest.approx(0.0211)


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

    result = backend.search(request(remaining_searches=2))

    sent = http.sent[0]
    assert sent.url == "https://api.x.ai/v1/responses"
    assert sent.body["tools"] == [{"type": "web_search"}]
    assert sent.body["parallel_tool_calls"] is False
    assert sent.body["max_turns"] == 2
    assert "Use at most 2 provider-side search" in sent.body["input"]
    assert "tool_choice" not in sent.body
    assert [item.url for item in result.evidence] == [cited, other]
    assert result.evidence[0].title == "oaktreecapital.com"
    assert result.evidence[0].excerpt == (
        "Marks frames risk as the possibility of permanent loss."
    )
    assert result.evidence[1].excerpt == text
    assert result.usage is not None
    assert result.usage.search_calls == 2
    assert result.budget_exhausted is True
    assert result.usage.cost_usd == pytest.approx(0.01016)


def test_grok_hard_caps_one_remaining_turn_and_trusts_billed_tool_usage() -> None:
    wire = {
        # Attempt rows are not billing authority when xAI supplies its nested
        # successful server-side usage count.
        "output": [
            {"type": "web_search_call", "status": "completed"},
            {"type": "web_search_call", "status": "failed"},
            {"type": "web_search_call", "status": "failed"},
        ],
        "citations": ["https://example.com/cited"],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "num_server_side_tools_used": 1,
            "server_side_tool_usage_details": {"web_search_calls": 1},
        },
    }
    http = ScriptedTransport(wire)
    backend = GrokResearchBackend(
        ModelBinding(
            "grok/grok-4.5",
            params={"max_turns": 99, "parallel_tool_calls": True},
        ),
        credentials(),
        http=http,
    )

    result = backend.search(request(remaining_searches=1))

    assert http.sent[0].body["max_turns"] == 1
    assert http.sent[0].body["parallel_tool_calls"] is False
    assert result.usage is not None
    assert result.usage.search_calls == 1
    assert result.usage.cost_usd == pytest.approx(0.00505)
    assert result.budget_exhausted is True


def test_grok_preserves_authoritative_zero_successful_searches() -> None:
    wire = {
        "output": [{"type": "web_search_call", "status": "failed"}],
        # Even returned citations must not turn xAI's authoritative zero into
        # a billable search invocation.
        "citations": ["https://example.com/cited"],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "num_server_side_tools_used": 0,
            "server_side_tool_usage_details": {"web_search_calls": 0},
        },
    }
    http = ScriptedTransport(wire)
    backend = GrokResearchBackend(
        ModelBinding("grok/grok-4.5"), credentials(), http=http
    )

    result = backend.search(request(remaining_searches=1))

    assert result.usage is not None
    assert result.usage.search_calls == 0
    assert result.usage.cost_usd == pytest.approx(0.00005)
    assert result.budget_exhausted is False


def test_grok_uses_authoritative_success_total_when_details_are_absent() -> None:
    wire = {
        "output": [
            {"type": "web_search_call", "status": "completed"},
            {"type": "web_search_call", "status": "failed"},
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "num_server_side_tools_used": 1,
        },
    }
    http = ScriptedTransport(wire)
    backend = GrokResearchBackend(
        ModelBinding("grok/grok-4.5"), credentials(), http=http
    )

    result = backend.search(request(remaining_searches=1))

    assert result.usage is not None
    assert result.usage.search_calls == 1
    assert result.usage.cost_usd == pytest.approx(0.00505)
    assert result.budget_exhausted is True


def test_grok_falls_back_to_successful_output_rows_when_usage_count_absent() -> None:
    wire = {
        "output": [
            {"type": "web_search_call", "status": "completed"},
            {"type": "web_search_call", "status": "failed"},
            {"type": "web_search_call", "status": "completed"},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    http = ScriptedTransport(wire)
    backend = GrokResearchBackend(
        ModelBinding("grok/grok-4.5"), credentials(), http=http
    )

    result = backend.search(request(remaining_searches=3))

    assert result.usage is not None
    assert result.usage.search_calls == 2
    assert result.usage.cost_usd == pytest.approx(0.01005)
    assert result.budget_exhausted is False


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
        ModelBinding("google/gemini-2.5-flash"), credentials(), http=http
    )

    result = backend.search(
        request(
            seeds=("https://www.oaktreecapital.com/insights/memos",),
            remaining_searches=2,
        )
    )

    sent = http.sent[0]
    assert sent.url == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert sent.headers["x-goog-api-key"] == KEY
    assert "authorization" not in sent.headers
    assert sent.body["tools"] == [{"type": "google_search"}]
    assert "Use at most 2 provider-side search" in sent.body["input"]
    assert [item.url for item in result.evidence] == [primary, secondary]
    assert result.evidence[0].excerpt == "Cycles are inevitable."
    assert result.evidence[0].source_type == "primary"
    assert result.usage is not None
    assert result.usage.cached_tokens == 3
    # Gemini 2.5 bills one grounded model prompt regardless of the number of
    # internal Google Search queries in that prompt.
    assert result.usage.search_calls == 1
    assert result.budget_exhausted is False
    assert result.usage.cost_usd == pytest.approx(0.0350645)


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
        ModelBinding("google/gemini-2.5-flash"), credentials(), http=http
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
    assert result.usage.search_calls == 2
    assert result.usage.input_tokens == 25
    assert result.usage.output_tokens == 9
    assert result.usage.cost_usd == pytest.approx(0.00505975)


def test_kimi_cli_budget_caps_echo_rounds_across_logical_searches() -> None:
    tool_call = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-budget",
                            "type": "builtin_function",
                            "function": {
                                "name": "$web_search",
                                "arguments": '{"results":[]}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }
    completed = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": "[Memo](https://www.oaktreecapital.com/insights/memos)",
                },
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    }
    http = ScriptedTransport(tool_call, completed, tool_call)
    backend = make_research_backend(
        ModelBinding("kimi/kimi-k2.6"),
        credentials(),
        http=http,
        max_searches=3,
    )

    assert backend.remaining_search_rounds == 3
    assert backend.search(request()).evidence
    assert backend.remaining_search_rounds == 1
    exhausted = backend.search(request())
    assert exhausted.evidence == ()
    assert exhausted.budget_exhausted is True
    assert exhausted.usage is not None
    assert exhausted.usage.calls == 1
    assert exhausted.usage.search_calls == 1
    assert exhausted.usage.cost_usd == pytest.approx(0.00500685)
    assert backend.remaining_search_rounds == 0
    assert len(http.sent) == 3


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
        ModelBinding("google/gemini-2.5-flash"),
        StaticCredentialProvider({"GOOGLE_API_KEY": KEY}),
        http=ScriptedTransport(),
    )

    assert isinstance(backend, GeminiResearchBackend)
    assert backend.model_ref == "google/gemini-2.5-flash"


def test_explicit_gemini_3_rejected_before_credential_or_network_access() -> None:
    class ForbiddenCredentials:
        def __call__(self, _ref: str) -> str:
            raise AssertionError("ineligible models must fail before credentials")

    http = ScriptedTransport()
    with pytest.raises(UnsupportedResearchModelError) as caught:
        make_research_backend(
            ModelBinding("google/gemini-3.5-flash"),
            ForbiddenCredentials(),
            http=http,
        )

    assert caught.value.reason_code == "model_not_search_budget_capable"
    assert "google/gemini-2.5-flash" in str(caught.value)
    assert http.sent == []


def test_selector_honors_openai_grok_google_kimi_priority_not_input_order() -> None:
    selected = select_research_backend(
        [
            ModelBinding("kimi/kimi-k2.6"),
            ModelBinding("google/gemini-2.5-flash"),
            ModelBinding("grok/grok-4.5"),
        ],
        StaticCredentialProvider(
            {"MOONSHOT_API_KEY": KEY, "GEMINI_API_KEY": KEY, "XAI_API_KEY": KEY}
        ),
        http=ScriptedTransport(),
    )

    assert isinstance(selected, GrokResearchBackend)


def test_selector_skips_gemini_3_and_uses_next_eligible_lane() -> None:
    selected = select_research_backend(
        [
            ModelBinding("google/gemini-3.5-flash"),
            ModelBinding("kimi/kimi-k2.6"),
        ],
        StaticCredentialProvider(
            {"GEMINI_API_KEY": KEY, "MOONSHOT_API_KEY": KEY}
        ),
        http=ScriptedTransport(),
    )

    assert isinstance(selected, KimiResearchBackend)


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
