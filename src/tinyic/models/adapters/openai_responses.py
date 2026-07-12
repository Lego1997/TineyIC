"""OpenAI Responses API adapter (FR-1.2, ``openai-responses`` wire format).

``POST /responses`` with typed SSE events.  System turns become top-level
``instructions``; the remaining turns are the ``input`` list.  Thinking maps to
nested ``reasoning.effort`` (the profile may hand us either the nested shape or
a flat ``reasoning_effort`` — both are normalized here), and ``reasoning.summary``
is requested so the model streams reasoning **summaries**, which surface as
``ReasoningDelta`` (FR-1.3; raw reasoning tokens are not exposed by this API).
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
    Role,
    TextDelta,
    Transport,
    Usage,
    WireFormat,
)
from ._base import BaseHttpAdapter
from ._http import HttpResponse

DEFAULT_BASE_URL = "https://api.openai.com/v1"

_TEXT_DELTA_TYPE = "response.output_text.delta"
_REASONING_DELTA_TYPES = frozenset(
    {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}
)
_TERMINAL_TYPES = frozenset(
    {"response.completed", "response.incomplete", "response.failed"}
)


def usage_from_openai_responses(raw: Any) -> Usage | None:
    """Normalize a Responses ``usage`` object (``None`` if absent/blank)."""
    if not isinstance(raw, dict):
        return None
    details = raw.get("input_tokens_details")
    cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    return Usage(
        input_tokens=int(raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        cached_tokens=int(cached or 0),
    )


def _finish_from_responses(response_obj: dict[str, Any], event_type: str) -> FinishReason:
    if event_type == "response.failed":
        return FinishReason.ERROR
    reason = (response_obj.get("incomplete_details") or {}).get("reason")
    if event_type == "response.incomplete" or response_obj.get("status") == "incomplete":
        if reason in ("max_output_tokens", "max_tokens"):
            return FinishReason.LENGTH
        if reason == "content_filter":
            return FinishReason.CONTENT_FILTER
        return FinishReason.STOP
    return FinishReason.STOP


class OpenAIResponsesAdapter(BaseHttpAdapter):
    """Adapter for the OpenAI Responses wire format."""

    wire_format = WireFormat.OPENAI_RESPONSES
    endpoint_suffix = "/responses"

    def _build_body(
        self, request: ChatRequest, *, include_thinking: bool
    ) -> dict[str, Any]:
        instructions: list[str] = []
        input_items: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role is Role.SYSTEM:
                instructions.append(message.content)
            else:
                input_items.append(
                    {"role": message.role.value, "content": message.content}
                )
        body: dict[str, Any] = {
            "model": request.binding.model,
            "input": input_items,
            "stream": request.stream,
        }
        if instructions:
            body["instructions"] = "\n\n".join(instructions)
        body.update(self._request_params(request))
        if include_thinking:
            self._apply_reasoning(body, self._thinking_params(request))
        return body

    @staticmethod
    def _apply_reasoning(body: dict[str, Any], params: dict[str, Any]) -> None:
        if not params:
            return
        reasoning = dict(body.get("reasoning") or {})
        nested = params.get("reasoning")
        if isinstance(nested, dict):
            reasoning.update(nested)
        if "reasoning_effort" in params:
            reasoning["effort"] = params["reasoning_effort"]
        if "effort" in params:
            reasoning["effort"] = params["effort"]
        if reasoning:
            reasoning.setdefault("summary", "auto")
            body["reasoning"] = reasoning

    def _parse_stream(self, response: HttpResponse, request: ChatRequest) -> Any:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: Usage | None = None
        finish = FinishReason.STOP

        for event in self._sse_events(response):
            payload = self._load_json(event.data)
            if not isinstance(payload, dict):
                continue
            event_type = payload.get("type") or event.event
            if event_type == _TEXT_DELTA_TYPE:
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    text_parts.append(delta)
                    yield TextDelta(delta)
            elif event_type in _REASONING_DELTA_TYPES:
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    reasoning_parts.append(delta)
                    yield ReasoningDelta(delta)
            elif event_type in _TERMINAL_TYPES:
                response_obj = payload.get("response") or {}
                reported = usage_from_openai_responses(response_obj.get("usage"))
                if reported is not None:
                    usage = reported
                finish = _finish_from_responses(response_obj, event_type)
            elif event_type == "error":
                finish = FinishReason.ERROR

        yield from self._emit_final(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            usage=usage,
            finish_reason=finish,
        )

    def _parse_response(self, response: HttpResponse, request: ChatRequest) -> Any:
        data = self._load_json(response.read_text())
        response.close()
        if not isinstance(data, dict):
            data = {}
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for block in item.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        value = block.get("text")
                        if isinstance(value, str):
                            text_parts.append(value)
            elif item.get("type") == "reasoning":
                for block in item.get("summary") or []:
                    if isinstance(block, dict) and block.get("type") == "summary_text":
                        value = block.get("text")
                        if isinstance(value, str):
                            reasoning_parts.append(value)
        yield from self._emit_final(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            usage=usage_from_openai_responses(data.get("usage")),
            finish_reason=_finish_from_responses(data, "response.completed"),
        )


def make_factory(
    *,
    base_url: str = DEFAULT_BASE_URL,
    credential_ref: str | None,
    thinking_lookup: Callable[[str], ThinkingProfile | None] | None = None,
    **adapter_kwargs: Any,
) -> Callable[[ModelBinding, CredentialProvider], Transport]:
    """Build a Responses transport factory bound to a base URL + credential ref."""

    def factory(binding: ModelBinding, credentials: CredentialProvider) -> Transport:
        profile = thinking_lookup(binding.model) if thinking_lookup else None
        return OpenAIResponsesAdapter(
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
    "OpenAIResponsesAdapter",
    "make_factory",
    "usage_from_openai_responses",
]
