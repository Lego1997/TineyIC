"""Normalized request/response types shared across provider adapters.

These are the in-process types every wire-format adapter maps to and from
(FR-1.2).  They are deliberately independent of the JSONL event schema in
``docs/event-schema.md``: the debate engine translates these normalized
call-level objects into persisted events (``think_delta``/``talk_delta``/
``usage``/…).  Keeping the two layers separate lets adapters be unit-tested
against fake transports without constructing a debate.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Protocol, runtime_checkable

from .binding import ModelBinding


class WireFormat(str, Enum):
    """The four request/response wire families adapters implement (FR-1.2)."""

    OPENAI_CHAT = "openai-chat"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    OPENAI_COMPATIBLE = "openai-compatible"


class Role(str, Enum):
    """Chat message roles the normalized request understands."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            return cls.__members__.get(value.strip().upper())
        return None


@dataclass(frozen=True)
class ChatMessage:
    """One normalized chat message."""

    role: Role
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            object.__setattr__(self, "role", Role(self.role))


@dataclass(frozen=True)
class ChatRequest:
    """A normalized chat completion request handed to a transport."""

    messages: Sequence[ChatMessage]
    binding: ModelBinding
    stream: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))


class FinishReason(str, Enum):
    """Normalized completion finish reasons."""

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    TOOL_USE = "tool_use"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class Usage:
    """Token accounting for a single model call.

    Also emitted as a stream event so streaming callers observe usage the
    moment a provider reports it (``stream_options={"include_usage": true}``).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class TextDelta:
    """An incremental fragment of visible answer text (-> ``talk_delta``)."""

    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    """An incremental fragment of reasoning/thinking text (-> ``think_delta``).

    Unifies "watch it think" across providers: OpenAI reasoning summaries,
    Anthropic thinking blocks, and normalized ``reasoning_content`` all arrive
    here (FR-1.3).
    """

    text: str


@dataclass(frozen=True)
class FinalMessage:
    """The terminal event of a call: the assembled message + usage."""

    text: str
    reasoning: str = ""
    usage: Usage | None = None
    finish_reason: FinishReason = FinishReason.STOP


# A transport yields a stream of these; non-streaming calls yield a single
# ``FinalMessage`` (optionally preceded by one ``Usage``).
ChatStreamEvent = TextDelta | ReasoningDelta | Usage | FinalMessage


@runtime_checkable
class Transport(Protocol):
    """The adapter seam (implemented by M2 stage-2 provider adapters).

    A transport is created by a provider's transport factory with a bound
    :class:`ModelBinding` and an opaque credential provider, then invoked with
    normalized :class:`ChatRequest`s.
    """

    def generate(self, request: ChatRequest) -> Iterator[ChatStreamEvent]:
        """Execute ``request`` and yield normalized stream events."""
        ...


# --- error taxonomy driving retry classification (FR-1.2) ----------------


class ErrorKind(str, Enum):
    """Normalized provider error categories."""

    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    INVALID_REQUEST = "invalid_request"


#: Kinds a retry/backoff loop should retry; the rest fail fast.
RETRYABLE_KINDS: frozenset[ErrorKind] = frozenset(
    {ErrorKind.RATE_LIMIT, ErrorKind.TRANSIENT}
)


class ProviderError(Exception):
    """Base class for normalized provider failures.

    Adapters classify raw SDK/HTTP errors into one of the four
    :class:`ErrorKind` subclasses so the retry loop is provider-agnostic.
    """

    kind: ClassVar[ErrorKind] = ErrorKind.TRANSIENT

    def __init__(
        self,
        message: str = "",
        *,
        kind: ErrorKind | None = None,
        retry_after: float | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message)
        if kind is not None:
            # Instance override for callers that raise the base type directly.
            self.kind = kind
        self.retry_after = retry_after
        self.provider = provider

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS


class AuthError(ProviderError):
    """Missing/invalid/expired credentials — not retryable."""

    kind: ClassVar[ErrorKind] = ErrorKind.AUTH


class RateLimitError(ProviderError):
    """Rate/usage limit hit — retryable, honoring ``retry_after``."""

    kind: ClassVar[ErrorKind] = ErrorKind.RATE_LIMIT


class TransientError(ProviderError):
    """Transient server/network fault — retryable with backoff."""

    kind: ClassVar[ErrorKind] = ErrorKind.TRANSIENT


class InvalidRequestError(ProviderError):
    """Malformed request the provider rejected — not retryable."""

    kind: ClassVar[ErrorKind] = ErrorKind.INVALID_REQUEST


def is_retryable(error: BaseException) -> bool:
    """Whether ``error`` is a provider error a retry loop should retry."""
    return isinstance(error, ProviderError) and error.retryable


__all__ = [
    "ChatMessage",
    "ChatRequest",
    "ChatStreamEvent",
    "ErrorKind",
    "FinalMessage",
    "FinishReason",
    "RETRYABLE_KINDS",
    "ReasoningDelta",
    "Role",
    "TextDelta",
    "Transport",
    "Usage",
    "WireFormat",
    "AuthError",
    "InvalidRequestError",
    "ProviderError",
    "RateLimitError",
    "TransientError",
    "is_retryable",
]
