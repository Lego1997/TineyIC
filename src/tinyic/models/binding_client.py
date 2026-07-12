"""``BindingClient`` — a legacy-client shim backed by a ModelBinding + adapter.

This is the object the M2 model layer routes ``tinytroupe.clients.client()`` to
(via :mod:`tinyic.models.routing`).  It presents the small slice of the vendored
OpenAI client surface the debate path actually uses — ``send_message`` returning
``{"role": "assistant", "content": ...}`` and ``get_cost_stats`` — but executes
the call through a bound :class:`~tinyic.models.types.Transport` (a wire
adapter) instead of the global OpenAI client.  A single binding client therefore
makes one persona (or the aggregator) speak through its *own*
``provider/model`` + thinking level, which is what turns FR-1.4 per-persona
heterogeneity from configuration into real behavior.

Usage is captured **at the adapter boundary** — from the provider's own
terminal ``Usage`` event — and surfaced through the ``on_usage`` callback, so the
engine attributes tokens per call without diffing a shared process-global
counter (closing M1's concurrent-contamination handoff).  Streaming reasoning
and text fragments surface through ``on_reasoning`` / ``on_text`` so the
orchestrator can emit ``think_delta`` / ``talk_delta`` events for a routed turn.
The callbacks are plain attributes the orchestrator rebinds per turn; a client
serves one persona sequentially across the debate.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tinytroupe import utils as _tt_utils

from .binding import ModelBinding
from .credentials import CredentialProvider
from .types import (
    ChatMessage,
    ChatRequest,
    FinalMessage,
    ReasoningDelta,
    TextDelta,
    Transport,
    Usage,
)

#: Callback receiving each streamed fragment (reasoning or visible text).
DeltaSink = Callable[[str], None]
#: Callback receiving a call's terminal usage with its ``model_ref``.
UsageSink = Callable[[Usage, str], None]

_COUNTER_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "model_calls",
    "cached_calls",
)


def _message_text(content: Any) -> str:
    """Flatten a chat message ``content`` to text.

    Debate messages are plain strings; multimodal content (a list of parts) is
    reduced to its text parts so a binding client never crashes on a vision
    payload, even though the debate path never sends one.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts: list[str] = []
        for part in content:
            if isinstance(part, Mapping):
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _schema_instruction(response_format: Any) -> str | None:
    """Render a schema-in-prompt instruction for a structured-output request.

    M2's in-house adapters do not yet negotiate native structured outputs on the
    wire, so a requested ``response_format`` becomes an explicit instruction
    (the documented fallback in FR-1.3/B11).  Offline tests replay recorded JSON
    regardless; this only shapes what a live provider is asked for.
    """
    if response_format is None:
        return None
    schema: Any = None
    model_schema = getattr(response_format, "model_json_schema", None)
    if callable(model_schema):
        try:
            schema = model_schema()
        except Exception:  # pragma: no cover - defensive
            schema = None
    if schema is not None:
        import json

        return (
            "Respond with a single JSON object that validates against this "
            "JSON Schema. Output only the JSON, with no surrounding prose:\n"
            + json.dumps(schema, ensure_ascii=False)
        )
    if isinstance(response_format, Mapping):
        # OpenAI's generic {"type": "json_object"} descriptor.
        return "Respond with a single valid JSON object and nothing else."
    return None


class BindingClient:
    """A ModelBinding-backed stand-in for the vendored ``client()`` object."""

    def __init__(
        self,
        binding: ModelBinding,
        transport: Transport,
        *,
        stream: bool = True,
        on_reasoning: DeltaSink | None = None,
        on_text: DeltaSink | None = None,
        on_usage: UsageSink | None = None,
    ) -> None:
        self.binding = binding
        self._transport = transport
        self._stream = stream
        #: Rebound per turn by the orchestrator; ``None`` outside a routed turn.
        self.on_reasoning = on_reasoning
        self.on_text = on_text
        self.on_usage = on_usage
        self._lock = threading.Lock()
        self._stats = {field: 0 for field in _COUNTER_FIELDS}

    # -- the slice of the legacy client surface the debate path uses --------

    def send_message(
        self,
        current_messages: Sequence[Mapping[str, Any]],
        *,
        dedent_messages: bool = True,
        model: str | None = None,
        temperature: float | None = None,
        response_format: Any = None,
        enable_pydantic_model_return: bool = False,
        **_ignored: Any,
    ) -> Any:
        """Execute one call through the bound adapter, legacy-compatible.

        Returns ``{"role": "assistant", "content": <text>}`` (or the parsed
        Pydantic model when ``enable_pydantic_model_return`` is set), matching
        the vendored client so the act loop, extractor, and memo synthesis need
        no changes.  Unknown keyword knobs (``model`` overrides, penalties, …)
        are accepted and ignored: the binding is the authoritative model choice.
        """
        chat_messages = self._build_messages(
            current_messages,
            dedent_messages=dedent_messages,
            response_format=response_format,
        )
        request = ChatRequest(
            messages=chat_messages,
            binding=self._effective_binding(temperature),
            stream=self._stream,
        )

        text_parts: list[str] = []
        final: FinalMessage | None = None
        streamed_usage: Usage | None = None
        for event in self._transport.generate(request):
            if isinstance(event, ReasoningDelta):
                if event.text and self.on_reasoning is not None:
                    self.on_reasoning(event.text)
            elif isinstance(event, TextDelta):
                if event.text:
                    text_parts.append(event.text)
                    if self.on_text is not None:
                        self.on_text(event.text)
            elif isinstance(event, Usage):
                streamed_usage = event
            elif isinstance(event, FinalMessage):
                final = event

        text = final.text if final is not None else "".join(text_parts)
        usage = (final.usage if final is not None else None) or streamed_usage
        self._record_usage(usage)

        raw_message = {"role": "assistant", "content": text}
        if enable_pydantic_model_return:
            model_cls = response_format if _is_pydantic_model(response_format) else None
            return _tt_utils.to_pydantic_or_sanitized_dict(raw_message, model=model_cls)
        return _tt_utils.sanitize_dict(raw_message)

    def get_cost_stats(self) -> dict[str, Any]:
        """Return this binding's own cumulative, model-attributed counters."""
        with self._lock:
            stats: dict[str, Any] = {field: self._stats[field] for field in _COUNTER_FIELDS}
        if stats["model_calls"] or stats["cached_calls"]:
            stats["by_model"] = {
                self.binding.model_ref: {
                    field: stats[field] for field in _COUNTER_FIELDS
                }
            }
        return stats

    def reset_cost_stats(self) -> None:
        with self._lock:
            self._stats = {field: 0 for field in _COUNTER_FIELDS}

    def set_api_cache(self, cache_api_calls: bool, cache_file_name: str | None = None) -> None:
        """Accept the legacy cache-config call; adapters own their own caching."""

    # -- internals ----------------------------------------------------------

    def _effective_binding(self, temperature: float | None) -> ModelBinding:
        if temperature is None:
            return self.binding
        return self.binding.with_params(temperature=temperature)

    def _build_messages(
        self,
        current_messages: Sequence[Mapping[str, Any]],
        *,
        dedent_messages: bool,
        response_format: Any,
    ) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for message in current_messages:
            content = _message_text(message.get("content"))
            if dedent_messages and content:
                content = _tt_utils.dedent(content)
            messages.append(ChatMessage(role=message.get("role", "user"), content=content))
        instruction = _schema_instruction(response_format)
        if instruction is not None:
            messages.append(ChatMessage(role="system", content=instruction))
        return messages

    def _record_usage(self, usage: Usage | None) -> None:
        if usage is None:
            return
        with self._lock:
            self._stats["input_tokens"] += usage.input_tokens
            self._stats["output_tokens"] += usage.output_tokens
            self._stats["total_tokens"] += usage.total_tokens
            self._stats["model_calls"] += 1
        if self.on_usage is not None:
            self.on_usage(usage, self.binding.model_ref)


def _is_pydantic_model(candidate: Any) -> bool:
    from pydantic import BaseModel

    return isinstance(candidate, type) and issubclass(candidate, BaseModel)


def build_transport(
    binding: ModelBinding,
    credentials: CredentialProvider,
    *,
    registry: Any | None = None,
) -> Transport:
    """Resolve ``binding`` to a bound wire transport via the provider registry."""
    if registry is None:
        from .registry import default_registry

        registry = default_registry()
    return registry.provider_for(binding).new_transport(binding, credentials)


__all__ = [
    "BindingClient",
    "DeltaSink",
    "UsageSink",
    "build_transport",
]
