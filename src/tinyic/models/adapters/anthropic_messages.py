"""Anthropic Messages API adapter (FR-1.2, ``anthropic-messages`` wire format).

``POST /v1/messages`` with typed SSE events.  System turns become the top-level
``system`` field; ``max_tokens`` is mandatory.  Extended thinking maps the
normalized ladder to a documented **level → budget-tokens** table
(:data:`ANTHROPIC_THINKING_BUDGETS`, the single source of truth the registry
also builds its profile from), emitted as
``thinking={"type":"enabled","budget_tokens":N}``.  ``thinking_delta`` blocks
surface as ``ReasoningDelta`` (FR-1.3).  Usage is assembled from ``message_start``
(input/cache-read) and the final ``message_delta`` (output).

Auth is ``x-api-key`` plus the required ``anthropic-version`` header — the
adapter never imports the Anthropic SDK (which isn't a TinyIC dependency); it
speaks the wire directly, so it is fully offline-testable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..binding import ModelBinding
from ..credentials import CredentialProvider
from ..thinking import ThinkingLevel, ThinkingProfile
from ..types import (
    ChatRequest,
    FinishReason,
    ReasoningDelta,
    Role,
    TextDelta,
    Transport,
    Usage,
    WireFormat,
)
from ._base import BaseHttpAdapter
from ._http import HttpResponse

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
#: Minimum room reserved for the visible answer when extended thinking is on.
MIN_ANSWER_TOKENS = 1024

#: Documented normalized-level → extended-thinking budget (tokens).  The
#: registry's ``claude-opus-4-8`` profile is built from this exact table so the
#: capability gate and the wire request never drift.
ANTHROPIC_THINKING_BUDGETS: dict[ThinkingLevel, int] = {
    ThinkingLevel.LOW: 2048,
    ThinkingLevel.MEDIUM: 4096,
    ThinkingLevel.HIGH: 8192,
    ThinkingLevel.XHIGH: 16384,
    ThinkingLevel.MAX: 32768,
}

_STOP_REASONS: dict[str, FinishReason] = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "max_tokens": FinishReason.LENGTH,
    "tool_use": FinishReason.TOOL_USE,
    "refusal": FinishReason.CONTENT_FILTER,
    "pause_turn": FinishReason.STOP,
}


def finish_from_anthropic(reason: str | None) -> FinishReason:
    """Map an Anthropic ``stop_reason`` to the normalized enum."""
    if not reason:
        return FinishReason.STOP
    return _STOP_REASONS.get(reason, FinishReason.STOP)


class AnthropicMessagesAdapter(BaseHttpAdapter):
    """Adapter for the Anthropic Messages wire format."""

    wire_format = WireFormat.ANTHROPIC_MESSAGES
    endpoint_suffix = "/messages"

    def _default_headers(self, key: str | None) -> dict[str, str]:
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if key is not None:
            headers.update(self._auth_headers(key))
        return headers

    def _auth_headers(self, key: str) -> dict[str, str]:
        return {"x-api-key": key}

    def _build_body(
        self, request: ChatRequest, *, include_thinking: bool
    ) -> dict[str, Any]:
        system: list[str] = []
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role is Role.SYSTEM:
                system.append(message.content)
            else:
                messages.append(
                    {"role": message.role.value, "content": message.content}
                )

        params = self._request_params(request)
        answer_tokens = int(params.pop("max_tokens", DEFAULT_MAX_TOKENS) or DEFAULT_MAX_TOKENS)
        body: dict[str, Any] = {
            "model": request.binding.model,
            "messages": messages,
            "max_tokens": answer_tokens,
            "stream": request.stream,
        }
        if system:
            body["system"] = "\n\n".join(system)

        budget = self._thinking_params(request).get("budget_tokens") if include_thinking else None
        if budget:
            budget = int(budget)
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # Extended thinking requires max_tokens > budget, and forbids setting
            # temperature/top_p/top_k (must default): drop them defensively.
            body["max_tokens"] = budget + max(answer_tokens, MIN_ANSWER_TOKENS)
            for forbidden in ("temperature", "top_p", "top_k"):
                params.pop(forbidden, None)
        body.update(params)
        return body

    def _parse_stream(self, response: HttpResponse, request: ChatRequest) -> Any:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        have_usage = False
        finish = FinishReason.STOP

        for event in self._sse_events(response):
            payload = self._load_json(event.data)
            if not isinstance(payload, dict):
                continue
            event_type = payload.get("type") or event.event
            if event_type == "message_start":
                usage = (payload.get("message") or {}).get("usage") or {}
                input_tokens = int(usage.get("input_tokens") or 0)
                output_tokens = int(usage.get("output_tokens") or 0)
                cached_tokens = int(usage.get("cache_read_input_tokens") or 0)
                have_usage = True
            elif event_type == "content_block_delta":
                delta = payload.get("delta") or {}
                delta_type = delta.get("type")
                if delta_type == "thinking_delta":
                    value = delta.get("thinking")
                    if isinstance(value, str) and value:
                        reasoning_parts.append(value)
                        yield ReasoningDelta(value)
                elif delta_type == "text_delta":
                    value = delta.get("text")
                    if isinstance(value, str) and value:
                        text_parts.append(value)
                        yield TextDelta(value)
                # signature_delta / input_json_delta carry no visible content.
            elif event_type == "message_delta":
                stop_reason = (payload.get("delta") or {}).get("stop_reason")
                if stop_reason:
                    finish = finish_from_anthropic(stop_reason)
                usage = payload.get("usage") or {}
                if usage.get("output_tokens") is not None:
                    output_tokens = int(usage.get("output_tokens") or 0)
                    have_usage = True
                if usage.get("input_tokens") is not None:
                    input_tokens = int(usage.get("input_tokens") or input_tokens)
            elif event_type == "error":
                finish = FinishReason.ERROR
            elif event_type == "message_stop":
                break

        usage_obj = (
            Usage(input_tokens, output_tokens, cached_tokens) if have_usage else None
        )
        yield from self._emit_final(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            usage=usage_obj,
            finish_reason=finish,
        )

    def _parse_response(self, response: HttpResponse, request: ChatRequest) -> Any:
        data = self._load_json(response.read_text())
        response.close()
        if not isinstance(data, dict):
            data = {}
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    text_parts.append(value)
            elif block.get("type") == "thinking":
                value = block.get("thinking")
                if isinstance(value, str):
                    reasoning_parts.append(value)
        usage_raw = data.get("usage") or {}
        usage_obj = (
            Usage(
                int(usage_raw.get("input_tokens") or 0),
                int(usage_raw.get("output_tokens") or 0),
                int(usage_raw.get("cache_read_input_tokens") or 0),
            )
            if usage_raw
            else None
        )
        yield from self._emit_final(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            usage=usage_obj,
            finish_reason=finish_from_anthropic(data.get("stop_reason")),
        )


def make_factory(
    *,
    base_url: str = DEFAULT_BASE_URL,
    credential_ref: str | None = "ANTHROPIC_API_KEY",
    thinking_lookup: Callable[[str], ThinkingProfile | None] | None = None,
    **adapter_kwargs: Any,
) -> Callable[[ModelBinding, CredentialProvider], Transport]:
    """Build an Anthropic transport factory bound to a base URL + credential ref."""

    def factory(binding: ModelBinding, credentials: CredentialProvider) -> Transport:
        profile = thinking_lookup(binding.model) if thinking_lookup else None
        return AnthropicMessagesAdapter(
            binding,
            credentials,
            base_url=base_url,
            credential_ref=credential_ref,
            thinking=profile,
            **adapter_kwargs,
        )

    return factory


__all__ = [
    "ANTHROPIC_THINKING_BUDGETS",
    "ANTHROPIC_VERSION",
    "AnthropicMessagesAdapter",
    "DEFAULT_BASE_URL",
    "finish_from_anthropic",
    "make_factory",
]
