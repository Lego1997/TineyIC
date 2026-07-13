"""OpenAI-compatible adapter (FR-1.2, ``openai-compatible`` wire format).

Covers custom-``base_url`` servers that speak the Chat Completions schema —
Ollama, vLLM, OpenRouter, LiteLLM-proxy, Gemini's OpenAI-compat endpoint.  It
reuses the Chat Completions request/stream machinery and adds the two
robustness behaviors those servers require:

* **usage may be absent** — many local/proxy servers never send a usage chunk;
  the inherited parser already yields ``FinalMessage(usage=None)`` with no
  ``Usage`` event, so callers degrade cleanly;
* **thinking params may be rejected** — a server that 400s on ``think`` /
  ``reasoning_effort`` / ``thinkingBudget`` triggers a single retry with the
  thinking parameter stripped, instead of failing the whole call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..binding import ModelBinding
from ..credentials import CredentialProvider
from ..thinking import ThinkingProfile
from ..types import ChatRequest, InvalidRequestError, Transport, WireFormat
from .openai_chat import OpenAIChatAdapter

DEFAULT_BASE_URL = "http://localhost:11434/v1"

# Substrings that mark an error body as "the thinking parameter was rejected".
_THINKING_HINTS = ("reasoning", "thinking", "think", "budget")


class OpenAICompatibleAdapter(OpenAIChatAdapter):
    """Chat Completions over a custom base URL, degrading gracefully."""

    wire_format = WireFormat.OPENAI_COMPATIBLE

    def _maybe_degrade(
        self, request: ChatRequest, body: dict[str, Any], error: InvalidRequestError
    ) -> dict[str, Any] | None:
        thinking = self._thinking_params(request)
        if not thinking:
            return None  # nothing thinking-related was sent; genuine bad request
        blob = str(getattr(error, "response_body", "") or "").lower()
        keys = [str(key).lower() for key in thinking]
        if any(hint in blob for hint in keys + list(_THINKING_HINTS)):
            # Rebuild without the thinking parameter and retry once.
            return self._build_body(request, include_thinking=False)
        return None


def make_factory(
    *,
    base_url: str = DEFAULT_BASE_URL,
    credential_ref: str | None = None,
    thinking_lookup: Callable[[str], ThinkingProfile | None] | None = None,
    **adapter_kwargs: Any,
) -> Callable[[ModelBinding, CredentialProvider], Transport]:
    """Build a compatible transport factory bound to a base URL + credential ref.

    ``credential_ref=None`` (the default) is the unauthenticated local lane
    (Ollama); pass an env-var name for key-guarded compatible servers (Gemini).
    """

    def factory(binding: ModelBinding, credentials: CredentialProvider) -> Transport:
        profile = thinking_lookup(binding.model) if thinking_lookup else None
        return OpenAICompatibleAdapter(
            binding,
            credentials,
            base_url=base_url,
            credential_ref=credential_ref,
            thinking=profile,
            **adapter_kwargs,
        )

    return factory


__all__ = ["DEFAULT_BASE_URL", "OpenAICompatibleAdapter", "make_factory"]
