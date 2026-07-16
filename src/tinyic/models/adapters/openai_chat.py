"""OpenAI Chat Completions adapter (FR-1.2, ``openai-chat`` wire format).

Canonical ``POST /chat/completions`` with SSE token streaming.  Streaming
requests set ``stream_options={"include_usage": true}`` so the terminal usage
chunk is emitted (review B11).  Thinking maps to the flat top-level
``reasoning_effort`` knob (OpenAI's gpt-5.x family and Grok's effort dial both
land here via their registry profiles).  Reasoning tokens streamed as
``delta.reasoning_content`` (Kimi-style reasoners speak this same wire) surface
as ``ReasoningDelta``, unifying "watch it think" (FR-1.3).  This is also the
base for Grok, Kimi (via :mod:`.kimi_chat`), and — via a subclass — the
OpenAI-compatible lane (Ollama/vLLM/proxies).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..binding import ModelBinding
from ..credentials import CredentialProvider
from ..thinking import ThinkingProfile
from ..types import (
    ChatRequest,
    FinishReason,
    ReasoningDelta,
    TextDelta,
    Transport,
    Usage,
    WireFormat,
)
from ._base import BaseHttpAdapter
from ._http import HttpResponse

DEFAULT_BASE_URL = "https://api.openai.com/v1"

_FINISH_REASONS: dict[str, FinishReason] = {
    "stop": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "content_filter": FinishReason.CONTENT_FILTER,
    "tool_calls": FinishReason.TOOL_USE,
    "function_call": FinishReason.TOOL_USE,
}


def finish_from_openai(reason: str | None) -> FinishReason:
    """Map an OpenAI-family ``finish_reason`` to the normalized enum."""
    if not reason:
        return FinishReason.STOP
    return _FINISH_REASONS.get(reason, FinishReason.STOP)


def usage_from_openai_chat(raw: Any) -> Usage | None:
    """Normalize a Chat Completions ``usage`` object (``None`` if absent/blank)."""
    if not isinstance(raw, dict):
        return None
    details = raw.get("prompt_tokens_details")
    cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    return Usage(
        input_tokens=int(raw.get("prompt_tokens") or 0),
        output_tokens=int(raw.get("completion_tokens") or 0),
        cached_tokens=int(cached or 0),
    )


class OpenAIChatAdapter(BaseHttpAdapter):
    """Adapter for the OpenAI Chat Completions wire format."""

    wire_format = WireFormat.OPENAI_CHAT
    endpoint_suffix = "/chat/completions"

    def _build_body(
        self, request: ChatRequest, *, include_thinking: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.binding.model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "stream": request.stream,
        }
        if request.stream:
            body["stream_options"] = {"include_usage": True}
        body.update(self._request_params(request))
        if include_thinking:
            body.update(self._thinking_params(request))
        return body

    def _parse_stream(
        self, response: HttpResponse, request: ChatRequest
    ) -> Any:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: Usage | None = None
        finish = FinishReason.STOP

        for event in self._sse_events(response):
            payload = event.data.strip()
            if payload == "[DONE]":
                break
            chunk = self._load_json(event.data)
            if not isinstance(chunk, dict):
                continue  # malformed frame — skip, never abort the stream
            reported = usage_from_openai_chat(chunk.get("usage"))
            if reported is not None:
                usage = reported
            for choice in chunk.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    reasoning_parts.append(reasoning)
                    yield ReasoningDelta(reasoning)
                content = delta.get("content")
                if isinstance(content, str) and content:
                    text_parts.append(content)
                    yield TextDelta(content)
                reason = choice.get("finish_reason")
                if reason:
                    finish = finish_from_openai(reason)

        yield from self._emit_final(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            usage=usage,
            finish_reason=finish,
        )

    def _parse_response(
        self, response: HttpResponse, request: ChatRequest
    ) -> Any:
        data = self._load_json(response.read_text())
        response.close()
        if not isinstance(data, dict):
            data = {}
        text = ""
        reasoning = ""
        finish = FinishReason.STOP
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            text = message.get("content") or ""
            candidate = message.get("reasoning_content")
            reasoning = candidate if isinstance(candidate, str) else ""
            finish = finish_from_openai(choices[0].get("finish_reason"))
        yield from self._emit_final(
            text=text,
            reasoning=reasoning,
            usage=usage_from_openai_chat(data.get("usage")),
            finish_reason=finish,
        )


def make_factory(
    *,
    base_url: str = DEFAULT_BASE_URL,
    credential_ref: str | None,
    thinking_lookup: Callable[[str], ThinkingProfile | None] | None = None,
    **adapter_kwargs: Any,
) -> Callable[[ModelBinding, CredentialProvider], Transport]:
    """Build a transport factory bound to a base URL + credential ref."""

    def factory(binding: ModelBinding, credentials: CredentialProvider) -> Transport:
        profile = thinking_lookup(binding.model) if thinking_lookup else None
        return OpenAIChatAdapter(
            binding,
            credentials,
            base_url=base_url,
            credential_ref=credential_ref,
            thinking=profile,
            **adapter_kwargs,
        )

    return factory


__all__ = [
    "DEFAULT_BASE_URL",
    "OpenAIChatAdapter",
    "finish_from_openai",
    "make_factory",
    "usage_from_openai_chat",
]
