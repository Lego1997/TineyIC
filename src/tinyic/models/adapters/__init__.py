"""Provider wire-format adapters (M2 stage 2, FR-1.2).

Four adapters over four wire families, each mapping normalized
:class:`~tinyic.models.types.ChatRequest`s to a provider's HTTP/SSE wire and
back to normalized stream events:

* :class:`~tinyic.models.adapters.openai_chat.OpenAIChatAdapter` — ``openai-chat``
* :class:`~tinyic.models.adapters.openai_responses.OpenAIResponsesAdapter` — ``openai-responses``
* :class:`~tinyic.models.adapters.anthropic_messages.AnthropicMessagesAdapter` — ``anthropic-messages``
* :class:`~tinyic.models.adapters.openai_compatible.OpenAICompatibleAdapter` — ``openai-compatible``

Provider-flavored subclasses stay in their own modules (e.g.
:class:`~tinyic.models.adapters.kimi_chat.KimiChatAdapter`, which adds
Moonshot's server-side ``$web_search`` echo protocol on the ``openai-chat``
wire).

Each module also exposes a ``make_factory(...)`` builder that binds a base URL,
credential reference, and per-model thinking-profile lookup into a
:class:`~tinyic.models.registry.TransportFactory`.  Adapters depend only on the
HTTP/SSE/retry seams and the normalized types — never on the registry — so the
dependency graph stays one-directional (registry → adapters).
"""

from __future__ import annotations

from . import (
    anthropic_messages,
    kimi_chat,
    openai_chat,
    openai_compatible,
    openai_responses,
)
from ._http import (
    HttpConnectionError,
    HttpRequest,
    HttpResponse,
    HttpTransport,
    HttpxTransport,
)
from ._retry import (
    RetryPolicy,
    compute_backoff,
    error_kind_for_status,
    parse_retry_after,
)
from ._sse import SseEvent, iter_sse_events
from .anthropic_messages import (
    ANTHROPIC_ADAPTIVE_EFFORTS,
    ANTHROPIC_THINKING_BUDGETS,
    AnthropicMessagesAdapter,
)
from .kimi_chat import KimiChatAdapter
from .openai_chat import OpenAIChatAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .openai_responses import OpenAIResponsesAdapter

__all__ = [
    # adapters
    "AnthropicMessagesAdapter",
    "KimiChatAdapter",
    "OpenAIChatAdapter",
    "OpenAICompatibleAdapter",
    "OpenAIResponsesAdapter",
    # adapter modules (for their make_factory builders)
    "anthropic_messages",
    "kimi_chat",
    "openai_chat",
    "openai_compatible",
    "openai_responses",
    # thinking tables (single source of truth shared with the registry)
    "ANTHROPIC_ADAPTIVE_EFFORTS",
    "ANTHROPIC_THINKING_BUDGETS",
    # HTTP seam
    "HttpConnectionError",
    "HttpRequest",
    "HttpResponse",
    "HttpTransport",
    "HttpxTransport",
    # SSE + retry primitives
    "SseEvent",
    "iter_sse_events",
    "RetryPolicy",
    "compute_backoff",
    "error_kind_for_status",
    "parse_retry_after",
]
