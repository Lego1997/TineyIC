"""Offline unit tests for the M2 stage-2 wire adapters (FR-1.2).

Every provider behavior is exercised against **fake transports replaying
recorded SSE fixtures** (``tests/fixtures/wire/``) — there is zero network here.
Coverage: normalized delta ordering, usage presence/absence, the
thinking-parameter include/omit/remap decision per profile, reasoning capture
across all three streaming families, retry classification + bounded backoff,
graceful degradation on OpenAI-compatible servers, and malformed-frame
resilience.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tinyic.models import (
    AuthError,
    ChatMessage,
    ChatRequest,
    ErrorKind,
    FinalMessage,
    FinishReason,
    InvalidRequestError,
    ModelBinding,
    RateLimitError,
    ReasoningDelta,
    StaticCredentialProvider,
    TextDelta,
    ThinkingLevel,
    ThinkingProfile,
    TransientError,
    Transport,
    Usage,
)
from tinyic.models.adapters._http import HttpConnectionError, HttpRequest
from tinyic.models.adapters._retry import (
    RetryPolicy,
    compute_backoff,
    error_kind_for_status,
    parse_retry_after,
)
from tinyic.models.adapters._sse import SseEvent, iter_sse_events
from tinyic.models.adapters.anthropic_messages import (
    AnthropicMessagesAdapter,
    finish_from_anthropic,
)
from tinyic.models.adapters.openai_chat import OpenAIChatAdapter, finish_from_openai
from tinyic.models.adapters.openai_compatible import OpenAICompatibleAdapter
from tinyic.models.adapters.openai_responses import OpenAIResponsesAdapter

L = ThinkingLevel
KEY = "test-key"
FIXTURES = Path(__file__).parent / "fixtures" / "wire"

# A profile shared by several tests: flat effort supporting low/medium/high.
EFFORT = ThinkingProfile.effort(
    "reasoning_effort", {L.LOW: "low", L.MEDIUM: "medium", L.HIGH: "high"}
)
NESTED = ThinkingProfile.nested_effort(
    ("reasoning", "effort"), {L.MEDIUM: "medium", L.HIGH: "high"}
)
BUDGET = ThinkingProfile.budget(
    "budget_tokens", {L.LOW: 2048, L.MEDIUM: 4096, L.HIGH: 8192}
)
FLAG = ThinkingProfile.flag("think")


# --------------------------------------------------------------------------
# offline fakes
# --------------------------------------------------------------------------


def sse_lines(name: str) -> list[str]:
    """Load a recorded SSE fixture as a line list (blank lines preserved)."""
    return (FIXTURES / name).read_text().split("\n")


@dataclass
class FakeResponse:
    """A scripted HTTP response: streaming SSE lines, or a full body/error."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    lines: list[str] | None = None
    body: str = ""
    closed: bool = False

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def iter_lines(self):
        yield from (self.lines or [])

    def read_text(self) -> str:
        return "\n".join(self.lines) if self.lines is not None else self.body

    def close(self) -> None:
        self.closed = True


class ScriptedTransport:
    """A fake :class:`HttpTransport` that replays scripted responses in order.

    Each ``send`` pops the next scripted item; an exception item is raised (to
    simulate a connection fault).  ``sent`` records every :class:`HttpRequest`
    so tests can assert on the wire the adapter built.
    """

    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.sent: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> FakeResponse:
        self.sent.append(request)
        assert self._responses, "adapter sent more requests than were scripted"
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:  # pragma: no cover - trivial
        pass


def creds() -> StaticCredentialProvider:
    return StaticCredentialProvider(
        {
            "OPENAI_API_KEY": KEY,
            "ANTHROPIC_API_KEY": KEY,
            "XAI_API_KEY": KEY,
            "DEEPSEEK_API_KEY": KEY,
            "GEMINI_API_KEY": KEY,
        }
    )


def messages() -> list[ChatMessage]:
    return [
        ChatMessage("system", "You are a value investor."),
        ChatMessage("user", "Analyze AAPL."),
    ]


def no_sleep():
    """Return ``(recorded_delays, sleep_fn)`` so tests can assert on backoff."""
    recorded: list[float] = []
    return recorded, recorded.append


def kinds(events) -> list[str]:
    return [type(event).__name__ for event in events]


# --------------------------------------------------------------------------
# OpenAI Chat Completions
# --------------------------------------------------------------------------


def _openai_chat(fixture, *, stream=True, thinking=None, level="medium", params=None,
                 responses=None, base_url="https://api.openai.com/v1",
                 credential_ref="OPENAI_API_KEY", retry=None, sleep=None,
                 credentials=None):
    binding = ModelBinding("openai/gpt-5.2", thinking_level=level, params=params or {})
    scripted = responses or [FakeResponse(200, lines=sse_lines(fixture))]
    transport = ScriptedTransport(scripted)
    adapter = OpenAIChatAdapter(
        binding,
        credentials or creds(),
        base_url=base_url,
        credential_ref=credential_ref,
        thinking=thinking,
        http=transport,
        retry=retry or RetryPolicy(max_retries=2, base_delay=0.0),
        sleep=sleep or (lambda _d: None),
    )
    request = ChatRequest(messages(), binding, stream=stream)
    return transport, list(adapter.generate(request))


def test_openai_chat_stream_orders_deltas_and_extracts_usage():
    transport, events = _openai_chat("openai_chat_stream.sse")
    assert kinds(events) == ["TextDelta", "TextDelta", "Usage", "FinalMessage"]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hello", ", world"]
    # the standalone Usage precedes the terminal FinalMessage and matches it
    assert events[-2] == Usage(12, 3, 4)
    final = events[-1]
    assert isinstance(final, FinalMessage)
    assert final.text == "Hello, world"
    assert final.usage == Usage(12, 3, 4)
    assert final.finish_reason is FinishReason.STOP


def test_openai_chat_stream_surfaces_reasoning_content():
    # DeepSeek-style reasoners stream delta.reasoning_content over this wire.
    _transport, events = _openai_chat("openai_chat_reasoning_stream.sse")
    assert kinds(events) == [
        "ReasoningDelta",
        "ReasoningDelta",
        "TextDelta",
        "TextDelta",
        "Usage",
        "FinalMessage",
    ]
    final = events[-1]
    assert final.reasoning == "Let me think."
    assert final.text == "Answer: 42"


def test_openai_chat_stream_without_usage_yields_no_usage_event():
    _transport, events = _openai_chat("openai_chat_no_usage.sse")
    assert not any(isinstance(event, Usage) for event in events)
    final = events[-1]
    assert final.usage is None
    assert final.text == "No usage here"


def test_openai_chat_stream_skips_malformed_frames():
    _transport, events = _openai_chat("openai_chat_malformed.sse")
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["good1", "good2"]
    final = events[-1]
    assert final.text == "good1good2"
    assert final.usage == Usage(5, 2, 0)


def test_openai_chat_request_wire_shape_and_thinking_included():
    transport, _events = _openai_chat(
        "openai_chat_stream.sse", thinking=EFFORT, level="high"
    )
    request = transport.sent[0]
    assert request.method == "POST"
    assert request.url == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["accept"] == "text/event-stream"
    body = request.body
    assert body["model"] == "gpt-5.2"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["reasoning_effort"] == "high"
    assert body["messages"][0] == {"role": "system", "content": "You are a value investor."}


def test_openai_chat_omits_thinking_when_profile_has_no_knob():
    transport, _events = _openai_chat(
        "openai_chat_stream.sse", thinking=ThinkingProfile.omitted(), level="high"
    )
    assert "reasoning_effort" not in transport.sent[0].body


def test_openai_chat_remaps_unsupported_level_at_runtime():
    # EFFORT supports low/medium/high; MAX remaps to the nearest supported (high).
    transport, _events = _openai_chat(
        "openai_chat_stream.sse", thinking=EFFORT, level="max"
    )
    assert transport.sent[0].body["reasoning_effort"] == "high"


def test_openai_chat_no_thinking_param_without_profile():
    transport, _events = _openai_chat("openai_chat_stream.sse", thinking=None)
    assert "reasoning_effort" not in transport.sent[0].body


def test_openai_chat_non_streaming_parses_message_and_usage():
    body = (
        '{"choices":[{"message":{"content":"Hi","reasoning_content":"hmm"},'
        '"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1,'
        '"total_tokens":4}}'
    )
    transport, events = _openai_chat(
        "openai_chat_stream.sse",
        stream=False,
        responses=[FakeResponse(200, body=body)],
    )
    assert kinds(events) == ["Usage", "FinalMessage"]
    final = events[-1]
    assert final.text == "Hi"
    assert final.reasoning == "hmm"
    assert final.usage == Usage(3, 1, 0)
    sent = transport.sent[0]
    assert sent.body["stream"] is False
    assert "stream_options" not in sent.body


def test_missing_credential_raises_auth_error_before_any_send():
    binding = ModelBinding("openai/gpt-5.2")
    transport = ScriptedTransport([])
    adapter = OpenAIChatAdapter(
        binding,
        StaticCredentialProvider({}),
        base_url="https://api.openai.com/v1",
        credential_ref="OPENAI_API_KEY",
        http=transport,
    )
    with pytest.raises(AuthError) as excinfo:
        list(adapter.generate(ChatRequest(messages(), binding, stream=True)))
    assert "OPENAI_API_KEY" in str(excinfo.value)
    assert excinfo.value.kind is ErrorKind.AUTH
    assert transport.sent == []  # never hit the wire


# --------------------------------------------------------------------------
# error taxonomy + bounded retry/backoff (shared base machinery)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,error_cls,retryable",
    [
        (401, AuthError, False),
        (403, AuthError, False),
        (429, RateLimitError, True),
        (400, InvalidRequestError, False),
        (404, InvalidRequestError, False),
        (422, InvalidRequestError, False),
        (500, TransientError, True),
        (503, TransientError, True),
        (529, TransientError, True),
    ],
)
def test_http_status_maps_to_error_taxonomy(status, error_cls, retryable):
    binding = ModelBinding("openai/gpt-5.2")
    transport = ScriptedTransport(
        [FakeResponse(status, body='{"error":{"message":"boom"}}')]
    )
    adapter = OpenAIChatAdapter(
        binding,
        creds(),
        base_url="https://api.openai.com/v1",
        credential_ref="OPENAI_API_KEY",
        http=transport,
        retry=RetryPolicy(max_retries=0),
        sleep=lambda _d: None,
    )
    with pytest.raises(error_cls) as excinfo:
        list(adapter.generate(ChatRequest(messages(), binding, stream=True)))
    error = excinfo.value
    assert error.kind is error_cls.kind
    assert error.retryable is retryable
    assert error.provider == "openai"
    assert "boom" in str(error)
    assert len(transport.sent) == 1  # max_retries=0 -> single attempt


def test_transient_error_retries_then_succeeds():
    sleeps, sleep = no_sleep()
    transport, events = _openai_chat(
        "openai_chat_stream.sse",
        responses=[
            FakeResponse(503, body='{"error":{"message":"overloaded"}}'),
            FakeResponse(200, lines=sse_lines("openai_chat_stream.sse")),
        ],
        retry=RetryPolicy(max_retries=2, base_delay=0.5, multiplier=2.0),
        sleep=sleep,
    )
    assert len(transport.sent) == 2
    assert events[-1].text == "Hello, world"
    assert sleeps == [0.5]  # one backoff: base_delay * 2**0


def test_rate_limit_honors_retry_after_then_exhausts():
    sleeps, sleep = no_sleep()

    def limited():
        return FakeResponse(
            429, headers={"Retry-After": "2"}, body='{"error":{"message":"slow"}}'
        )

    transport = ScriptedTransport([limited(), limited(), limited()])
    binding = ModelBinding("openai/gpt-5.2")
    adapter = OpenAIChatAdapter(
        binding,
        creds(),
        base_url="https://api.openai.com/v1",
        credential_ref="OPENAI_API_KEY",
        http=transport,
        retry=RetryPolicy(max_retries=2, base_delay=0.5, max_delay=8.0),
        sleep=sleep,
    )
    with pytest.raises(RateLimitError) as excinfo:
        list(adapter.generate(ChatRequest(messages(), binding, stream=True)))
    assert len(transport.sent) == 3  # 1 + 2 retries
    assert sleeps == [2.0, 2.0]  # Retry-After honored on each retry
    assert excinfo.value.retry_after == 2.0


def test_non_retryable_error_is_not_retried():
    binding = ModelBinding("openai/gpt-5.2")
    transport = ScriptedTransport(
        [
            FakeResponse(401, body='{"error":{"message":"bad key"}}'),
            FakeResponse(200, lines=sse_lines("openai_chat_stream.sse")),
        ]
    )
    adapter = OpenAIChatAdapter(
        binding,
        creds(),
        base_url="https://api.openai.com/v1",
        credential_ref="OPENAI_API_KEY",
        http=transport,
        retry=RetryPolicy(max_retries=3),
        sleep=lambda _d: None,
    )
    with pytest.raises(AuthError):
        list(adapter.generate(ChatRequest(messages(), binding, stream=True)))
    assert len(transport.sent) == 1  # auth failure short-circuits the retry loop


def test_connection_fault_is_transient_and_retried():
    transport, events = _openai_chat(
        "openai_chat_stream.sse",
        responses=[
            HttpConnectionError("connection reset"),
            FakeResponse(200, lines=sse_lines("openai_chat_stream.sse")),
        ],
        retry=RetryPolicy(max_retries=1, base_delay=0.0),
    )
    assert len(transport.sent) == 2
    assert events[-1].text == "Hello, world"


# --------------------------------------------------------------------------
# OpenAI Responses
# --------------------------------------------------------------------------


def _openai_responses(fixture, *, stream=True, thinking=None, level="medium",
                      params=None, responses=None):
    binding = ModelBinding(
        "openai/gpt-5.6-sol", thinking_level=level, params=params or {}
    )
    transport = ScriptedTransport(
        responses or [FakeResponse(200, lines=sse_lines(fixture))]
    )
    adapter = OpenAIResponsesAdapter(
        binding,
        creds(),
        base_url="https://api.openai.com/v1",
        credential_ref="OPENAI_API_KEY",
        thinking=thinking,
        http=transport,
        sleep=lambda _d: None,
    )
    return transport, list(adapter.generate(ChatRequest(messages(), binding, stream=stream)))


def test_openai_responses_stream_captures_reasoning_summary_and_text():
    _transport, events = _openai_responses(
        "openai_responses_stream.sse", thinking=NESTED, level="high"
    )
    assert kinds(events) == [
        "ReasoningDelta",
        "ReasoningDelta",
        "TextDelta",
        "TextDelta",
        "Usage",
        "FinalMessage",
    ]
    final = events[-1]
    assert final.reasoning == "Weighing options."
    assert final.text == "Buy signal."
    assert final.usage == Usage(30, 12, 6)
    assert final.finish_reason is FinishReason.STOP


def test_openai_responses_maps_thinking_to_nested_reasoning_and_routes_system():
    transport, _events = _openai_responses(
        "openai_responses_stream.sse", thinking=NESTED, level="high"
    )
    body = transport.sent[0].body
    assert body["reasoning"]["effort"] == "high"
    assert body["reasoning"]["summary"] == "auto"  # request streamed summaries
    assert "input" in body and "messages" not in body
    assert body["instructions"] == "You are a value investor."
    assert body["input"][0] == {"role": "user", "content": "Analyze AAPL."}
    assert transport.sent[0].url == "https://api.openai.com/v1/responses"


def test_openai_responses_normalizes_flat_effort_param():
    # Even a flat reasoning_effort profile is reshaped to nested reasoning.effort.
    transport, _events = _openai_responses(
        "openai_responses_stream.sse", thinking=EFFORT, level="high"
    )
    assert transport.sent[0].body["reasoning"]["effort"] == "high"


def test_openai_responses_non_streaming_parses_output_items():
    body = (
        '{"status":"completed","output":['
        '{"type":"reasoning","summary":[{"type":"summary_text","text":"ponder"}]},'
        '{"type":"message","content":[{"type":"output_text","text":"Sell."}]}],'
        '"usage":{"input_tokens":9,"output_tokens":4,"input_tokens_details":{"cached_tokens":2}}}'
    )
    _transport, events = _openai_responses(
        "openai_responses_stream.sse",
        stream=False,
        responses=[FakeResponse(200, body=body)],
    )
    assert kinds(events) == ["Usage", "FinalMessage"]
    final = events[-1]
    assert final.text == "Sell."
    assert final.reasoning == "ponder"
    assert final.usage == Usage(9, 4, 2)


# --------------------------------------------------------------------------
# Anthropic Messages
# --------------------------------------------------------------------------


def _anthropic(fixture, *, stream=True, thinking=None, level="medium", params=None,
               responses=None):
    binding = ModelBinding(
        "anthropic/claude-opus-4-8", thinking_level=level, params=params or {}
    )
    transport = ScriptedTransport(
        responses or [FakeResponse(200, lines=sse_lines(fixture))]
    )
    adapter = AnthropicMessagesAdapter(
        binding,
        creds(),
        base_url="https://api.anthropic.com/v1",
        credential_ref="ANTHROPIC_API_KEY",
        thinking=thinking,
        http=transport,
        sleep=lambda _d: None,
    )
    return transport, list(adapter.generate(ChatRequest(messages(), binding, stream=stream)))


def test_anthropic_stream_captures_thinking_and_assembles_split_usage():
    _transport, events = _anthropic(
        "anthropic_messages_stream.sse", thinking=BUDGET, level="high"
    )
    # signature_delta carries no visible content and is dropped
    assert kinds(events) == ["ReasoningDelta", "TextDelta", "Usage", "FinalMessage"]
    final = events[-1]
    assert final.reasoning == "Consider moat."
    assert final.text == "Strong hold."
    # input from message_start, output from the final message_delta, cache_read too
    assert final.usage == Usage(25, 14, 5)
    assert final.finish_reason is FinishReason.STOP


def test_anthropic_request_maps_budget_system_and_headers():
    transport, _events = _anthropic(
        "anthropic_messages_stream.sse",
        thinking=BUDGET,
        level="high",
        params={"temperature": 0.7},
    )
    request = transport.sent[0]
    assert request.url == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == KEY
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    body = request.body
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 8192}
    assert body["max_tokens"] > 8192  # must exceed the thinking budget
    assert body["system"] == "You are a value investor."
    assert body["messages"] == [{"role": "user", "content": "Analyze AAPL."}]
    # extended thinking forbids temperature: it is dropped
    assert "temperature" not in body


def test_anthropic_without_thinking_keeps_params_and_default_max_tokens():
    transport, _events = _anthropic(
        "anthropic_messages_stream.sse",
        thinking=ThinkingProfile.omitted(),
        level="high",
        params={"temperature": 0.5},
    )
    body = transport.sent[0].body
    assert "thinking" not in body
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 4096


def test_anthropic_non_streaming_parses_content_blocks():
    body = (
        '{"content":[{"type":"thinking","thinking":"weigh"},'
        '{"type":"text","text":"Buy."}],"stop_reason":"end_turn",'
        '"usage":{"input_tokens":11,"output_tokens":6,"cache_read_input_tokens":3}}'
    )
    _transport, events = _anthropic(
        "anthropic_messages_stream.sse",
        stream=False,
        responses=[FakeResponse(200, body=body)],
    )
    assert kinds(events) == ["Usage", "FinalMessage"]
    final = events[-1]
    assert final.text == "Buy."
    assert final.reasoning == "weigh"
    assert final.usage == Usage(11, 6, 3)


# --------------------------------------------------------------------------
# OpenAI-compatible (Ollama / vLLM / proxies / Gemini-compat)
# --------------------------------------------------------------------------


def _compatible(fixture, *, thinking=None, level="high", credential_ref=None,
                responses=None, stream=True, retry=None):
    binding = ModelBinding("ollama/qwen3:32b", thinking_level=level)
    transport = ScriptedTransport(
        responses or [FakeResponse(200, lines=sse_lines(fixture))]
    )
    adapter = OpenAICompatibleAdapter(
        binding,
        creds(),
        base_url="http://localhost:11434/v1",
        credential_ref=credential_ref,
        thinking=thinking,
        http=transport,
        retry=retry or RetryPolicy(max_retries=0),
        sleep=lambda _d: None,
    )
    return transport, list(adapter.generate(ChatRequest(messages(), binding, stream=stream)))


def test_compatible_stream_degrades_without_usage():
    _transport, events = _compatible("ollama_compatible_stream.sse", thinking=FLAG)
    assert not any(isinstance(event, Usage) for event in events)
    final = events[-1]
    assert final.usage is None
    assert final.text == "Local model."


def test_compatible_sends_think_flag_and_no_auth_when_unauthenticated():
    transport, _events = _compatible(
        "ollama_compatible_stream.sse", thinking=FLAG, credential_ref=None
    )
    request = transport.sent[0]
    assert request.body["think"] is True
    assert "authorization" not in request.headers


def test_compatible_degrades_when_server_rejects_thinking():
    error_body = '{"error":{"message":"unknown field: reasoning_effort"}}'
    transport, events = _compatible(
        "ollama_compatible_stream.sse",
        thinking=EFFORT,
        credential_ref="GEMINI_API_KEY",
        responses=[
            FakeResponse(400, body=error_body),
            FakeResponse(200, lines=sse_lines("ollama_compatible_stream.sse")),
        ],
    )
    assert len(transport.sent) == 2
    assert "reasoning_effort" in transport.sent[0].body  # first attempt tried it
    assert "reasoning_effort" not in transport.sent[1].body  # retried stripped
    assert events[-1].text == "Local model."


def test_compatible_non_thinking_bad_request_does_not_degrade():
    error_body = '{"error":{"message":"context length exceeded"}}'
    transport = ScriptedTransport([FakeResponse(400, body=error_body)])
    binding = ModelBinding("ollama/qwen3:32b", thinking_level="high")
    adapter = OpenAICompatibleAdapter(
        binding,
        creds(),
        base_url="http://localhost:11434/v1",
        credential_ref=None,
        thinking=EFFORT,
        http=transport,
        retry=RetryPolicy(max_retries=0),
        sleep=lambda _d: None,
    )
    with pytest.raises(InvalidRequestError):
        list(adapter.generate(ChatRequest(messages(), binding, stream=True)))
    assert len(transport.sent) == 1  # a genuine bad request is not retried


# --------------------------------------------------------------------------
# cross-adapter interchangeability (M2 DoD: one debate over 3 adapters)
# --------------------------------------------------------------------------


def test_three_adapters_produce_normalized_streams_from_one_request_shape():
    """The same normalized request runs through 3 wire families → same shape."""
    cases = [
        (_openai_chat, "openai_chat_stream.sse", "Hello, world"),
        (_openai_responses, "openai_responses_stream.sse", "Buy signal."),
        (_anthropic, "anthropic_messages_stream.sse", "Strong hold."),
    ]
    for runner, fixture, expected_text in cases:
        _transport, events = runner(fixture)
        assert isinstance(events[-1], FinalMessage)
        assert events[-1].text == expected_text
        assert isinstance(events[-1].usage, Usage)
        # deltas then exactly one Usage then exactly one terminal FinalMessage
        assert kinds(events).count("FinalMessage") == 1
        assert kinds(events).count("Usage") == 1
        assert kinds(events)[-2:] == ["Usage", "FinalMessage"]


def test_registry_built_transports_satisfy_the_transport_protocol():
    from tinyic.models import get_provider

    binding = ModelBinding("anthropic/claude-opus-4-8")
    transport = get_provider("anthropic").new_transport(binding, creds())
    assert isinstance(transport, Transport)
    assert isinstance(transport, AnthropicMessagesAdapter)


# --------------------------------------------------------------------------
# SSE parser + retry primitives (unit)
# --------------------------------------------------------------------------


def test_sse_parser_dispatches_event_and_data_frames():
    lines = ["event: message_start", 'data: {"a":1}', "", "data: [DONE]", ""]
    assert list(iter_sse_events(lines)) == [
        SseEvent("message_start", '{"a":1}'),
        SseEvent(None, "[DONE]"),
    ]


def test_sse_parser_joins_multiline_data_and_skips_comments():
    lines = [": keep-alive", "data: line1", "data: line2", "", ""]
    assert list(iter_sse_events(lines)) == [SseEvent(None, "line1\nline2")]


def test_sse_parser_dispatches_trailing_event_without_blank_line():
    assert list(iter_sse_events(["data: last"])) == [SseEvent(None, "last")]


def test_sse_parser_strips_single_leading_space_only():
    # "data:  x" -> one leading space stripped, so value is " x"
    assert list(iter_sse_events(["data:  x", ""])) == [SseEvent(None, " x")]


def test_error_kind_for_status_map():
    assert error_kind_for_status(401) is ErrorKind.AUTH
    assert error_kind_for_status(403) is ErrorKind.AUTH
    assert error_kind_for_status(429) is ErrorKind.RATE_LIMIT
    assert error_kind_for_status(500) is ErrorKind.TRANSIENT
    assert error_kind_for_status(529) is ErrorKind.TRANSIENT
    assert error_kind_for_status(422) is ErrorKind.INVALID_REQUEST
    assert error_kind_for_status(404) is ErrorKind.INVALID_REQUEST


def test_compute_backoff_exponential_and_retry_after():
    policy = RetryPolicy(base_delay=0.5, multiplier=2.0, max_delay=8.0)
    assert compute_backoff(0, policy) == 0.5
    assert compute_backoff(1, policy) == 1.0
    assert compute_backoff(2, policy) == 2.0
    assert compute_backoff(10, policy) == 8.0  # clamped to max_delay
    assert compute_backoff(0, policy, retry_after=5.0) == 5.0
    assert compute_backoff(0, policy, retry_after=100.0) == 8.0  # clamped


def test_parse_retry_after_seconds_only():
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("  2.5 ") == 2.5
    assert parse_retry_after(None) is None
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert parse_retry_after("-1") is None


def test_finish_reason_mappings():
    assert finish_from_anthropic("max_tokens") is FinishReason.LENGTH
    assert finish_from_anthropic("tool_use") is FinishReason.TOOL_USE
    assert finish_from_anthropic("refusal") is FinishReason.CONTENT_FILTER
    assert finish_from_anthropic("end_turn") is FinishReason.STOP
    assert finish_from_anthropic(None) is FinishReason.STOP
    assert finish_from_openai("length") is FinishReason.LENGTH
    assert finish_from_openai("content_filter") is FinishReason.CONTENT_FILTER
    assert finish_from_openai("tool_calls") is FinishReason.TOOL_USE
    assert finish_from_openai(None) is FinishReason.STOP
