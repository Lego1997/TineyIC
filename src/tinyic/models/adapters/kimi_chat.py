"""Kimi (Moonshot AI) Chat Completions adapter (FR-1.2, ``openai-chat`` wire).

Kimi speaks the canonical Chat Completions schema against the Moonshot base
URL (``api.moonshot.ai`` internationally, ``api.moonshot.cn`` in China), so the
inherited :class:`OpenAIChatAdapter` machinery covers the ordinary path
end-to-end — including ``delta.reasoning_content`` streaming (Kimi's thinking
is ON by default and disabled via ``{"thinking": {"type": "disabled"}}``, both
rendered by the registry's toggle profile).

What this subclass adds is the server-side ``$web_search`` builtin tool, an
opt-in per binding (``params.web_search = true``):

* the tool is declared as ``{"type": "builtin_function",
  "function": {"name": "$web_search"}}``;
* when the model finishes a round with ``finish_reason="tool_calls"``, the
  *server* executes the search — the client merely **echoes** the returned
  ``function.arguments`` back verbatim as the ``tool`` message content and
  re-sends, looping until a non-tool finish;
* only prose (``delta.content``) surfaces as ``TextDelta`` and only
  ``delta.reasoning_content`` as ``ReasoningDelta`` — tool-call frames never
  leak into the visible stream, so the StreamingActionScanner sees prose only;
* usage is summed across every round and reported once, in the normal terminal
  ``Usage`` + ``FinalMessage`` pair.

Moonshot requires thinking to be **disabled** for ``$web_search``: a binding
that enables the tool at any thinking level other than ``off`` is rejected at
transport construction (config time), before any request is made.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..binding import ModelBinding
from ..credentials import CredentialProvider
from ..thinking import ThinkingLevel, ThinkingProfile
from ..types import (
    ChatRequest,
    ChatStreamEvent,
    FinishReason,
    InvalidRequestError,
    ReasoningDelta,
    TextDelta,
    TransientError,
    Transport,
    Usage,
)
from ._http import HttpResponse
from .openai_chat import (
    OpenAIChatAdapter,
    finish_from_openai,
    usage_from_openai_chat,
)

DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"
CN_BASE_URL = "https://api.moonshot.cn/v1"

#: The opt-in binding param (``params.web_search = true``) — a TinyIC-level
#: knob, stripped from the wire body.
WEB_SEARCH_PARAM = "web_search"
WEB_SEARCH_TOOL_NAME = "$web_search"
#: Ceiling on echo rounds so a misbehaving server can never loop us forever.
MAX_WEB_SEARCH_ROUNDS = 8


def _web_search_tool() -> dict[str, Any]:
    return {
        "type": "builtin_function",
        "function": {"name": WEB_SEARCH_TOOL_NAME},
    }


@dataclass
class _Round:
    """What one request/response round produced."""

    text: str = ""
    reasoning: str = ""
    usage: Usage | None = None
    finish: FinishReason = FinishReason.STOP
    #: index -> {"id", "type", "name", "arguments"} accumulated across deltas.
    tool_calls: dict[int, dict[str, str]] = field(default_factory=dict)

    def ordered_tool_calls(self) -> list[dict[str, str]]:
        return [self.tool_calls[index] for index in sorted(self.tool_calls)]


def _merge_usage(total: Usage | None, extra: Usage | None) -> Usage | None:
    if extra is None:
        return total
    if total is None:
        return extra
    return Usage(
        input_tokens=total.input_tokens + extra.input_tokens,
        output_tokens=total.output_tokens + extra.output_tokens,
        cached_tokens=total.cached_tokens + extra.cached_tokens,
    )


class KimiChatAdapter(OpenAIChatAdapter):
    """Chat Completions against Moonshot, plus the ``$web_search`` echo loop."""

    def __init__(self, binding: ModelBinding, *args: Any, **kwargs: Any) -> None:
        super().__init__(binding, *args, **kwargs)
        if bool(binding.params.get(WEB_SEARCH_PARAM)) and (
            binding.thinking_level is not ThinkingLevel.OFF
        ):
            # Config-time gate: Moonshot's server-side $web_search requires
            # thinking disabled; fail at construction, never mid-debate.
            raise InvalidRequestError(
                "kimi $web_search requires thinking 'off' "
                f"(binding {binding.model_ref!r} sets "
                f"{binding.thinking_level.value!r})",
                provider=self._provider_name,
            )

    # ``web_search`` is a TinyIC knob, not a Moonshot request field.
    def _request_params(self, request: ChatRequest) -> dict[str, Any]:
        params = super()._request_params(request)
        params.pop(WEB_SEARCH_PARAM, None)
        return params

    def generate(self, request: ChatRequest) -> Iterator[ChatStreamEvent]:
        if not bool(request.binding.params.get(WEB_SEARCH_PARAM)):
            yield from super().generate(request)
            return
        yield from self._generate_with_web_search(request)

    # -- the builtin_function echo protocol ---------------------------------

    def _generate_with_web_search(
        self, request: ChatRequest
    ) -> Iterator[ChatStreamEvent]:
        base_body = self._build_body(request, include_thinking=True)
        conversation: list[dict[str, Any]] = list(base_body["messages"])
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        total_usage: Usage | None = None

        for _round in range(MAX_WEB_SEARCH_ROUNDS):
            body = dict(base_body)
            body["messages"] = list(conversation)
            body["tools"] = [_web_search_tool()]
            response = self._send_with_retry(self._http_request(request, body))

            outcome = _Round()
            if request.stream:
                yield from self._stream_round(response, outcome)
            else:
                self._read_round(response, outcome)
            if outcome.text:
                text_parts.append(outcome.text)
            if outcome.reasoning:
                reasoning_parts.append(outcome.reasoning)
            total_usage = _merge_usage(total_usage, outcome.usage)

            calls = outcome.ordered_tool_calls()
            if outcome.finish is FinishReason.TOOL_USE and calls:
                # The server ran the search; echo its arguments back verbatim.
                conversation.append(_assistant_echo(outcome.text, calls))
                conversation.extend(_tool_echo(call) for call in calls)
                continue

            yield from self._emit_final(
                text="".join(text_parts),
                reasoning="".join(reasoning_parts),
                usage=total_usage,
                finish_reason=outcome.finish,
            )
            return

        raise TransientError(
            f"kimi {WEB_SEARCH_TOOL_NAME} did not converge within "
            f"{MAX_WEB_SEARCH_ROUNDS} rounds",
            provider=self._provider_name,
        )

    def _stream_round(
        self, response: HttpResponse, outcome: _Round
    ) -> Iterator[ChatStreamEvent]:
        """One streamed round: yield prose/reasoning deltas, collect the rest."""
        text_parts: list[str] = []
        reasoning_parts: list[str] = []

        for event in self._sse_events(response):
            if event.data.strip() == "[DONE]":
                break
            chunk = self._load_json(event.data)
            if not isinstance(chunk, dict):
                continue
            reported = usage_from_openai_chat(chunk.get("usage"))
            if reported is not None:
                outcome.usage = reported
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
                _collect_tool_call_deltas(outcome, delta.get("tool_calls"))
                reason = choice.get("finish_reason")
                if reason:
                    outcome.finish = finish_from_openai(reason)

        outcome.text = "".join(text_parts)
        outcome.reasoning = "".join(reasoning_parts)

    def _read_round(self, response: HttpResponse, outcome: _Round) -> None:
        """One non-streamed round: parse the full JSON body into ``outcome``."""
        data = self._load_json(response.read_text())
        response.close()
        if not isinstance(data, dict):
            data = {}
        outcome.usage = usage_from_openai_chat(data.get("usage"))
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return
        message = choices[0].get("message") or {}
        outcome.text = message.get("content") or ""
        reasoning = message.get("reasoning_content")
        outcome.reasoning = reasoning if isinstance(reasoning, str) else ""
        outcome.finish = finish_from_openai(choices[0].get("finish_reason"))
        for index, call in enumerate(message.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            outcome.tool_calls[index] = {
                "id": str(call.get("id") or f"call-{index}"),
                "type": str(call.get("type") or "builtin_function"),
                "name": str(function.get("name") or ""),
                "arguments": str(function.get("arguments") or ""),
            }


def _collect_tool_call_deltas(outcome: _Round, raw: Any) -> None:
    """Accumulate streamed ``delta.tool_calls`` fragments by index."""
    if not isinstance(raw, list):
        return
    for fragment in raw:
        if not isinstance(fragment, dict):
            continue
        index = fragment.get("index")
        index = index if isinstance(index, int) else 0
        entry = outcome.tool_calls.setdefault(
            index,
            {"id": f"call-{index}", "type": "builtin_function", "name": "", "arguments": ""},
        )
        if fragment.get("id"):
            entry["id"] = str(fragment["id"])
        if fragment.get("type"):
            entry["type"] = str(fragment["type"])
        function = fragment.get("function") or {}
        if function.get("name"):
            entry["name"] = str(function["name"])
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            entry["arguments"] += arguments


def _assistant_echo(text: str, calls: list[dict[str, str]]) -> dict[str, Any]:
    """The assistant turn replayed back to the server, tool calls included."""
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {
                "id": call["id"],
                "type": call["type"],
                "function": {
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            }
            for call in calls
        ],
    }


def _tool_echo(call: dict[str, str]) -> dict[str, Any]:
    """The tool turn: ``function.arguments`` echoed back **verbatim**."""
    return {
        "role": "tool",
        "tool_call_id": call["id"],
        "name": call["name"],
        "content": call["arguments"],
    }


def make_factory(
    *,
    base_url: str = DEFAULT_BASE_URL,
    credential_ref: str | None = "MOONSHOT_API_KEY",
    thinking_lookup: Callable[[str], ThinkingProfile | None] | None = None,
    **adapter_kwargs: Any,
) -> Callable[[ModelBinding, CredentialProvider], Transport]:
    """Build a Kimi transport factory bound to a base URL + credential ref."""

    def factory(binding: ModelBinding, credentials: CredentialProvider) -> Transport:
        profile = thinking_lookup(binding.model) if thinking_lookup else None
        return KimiChatAdapter(
            binding,
            credentials,
            base_url=base_url,
            credential_ref=credential_ref,
            thinking=profile,
            **adapter_kwargs,
        )

    return factory


__all__ = [
    "CN_BASE_URL",
    "DEFAULT_BASE_URL",
    "KimiChatAdapter",
    "MAX_WEB_SEARCH_ROUNDS",
    "WEB_SEARCH_PARAM",
    "WEB_SEARCH_TOOL_NAME",
    "make_factory",
]
