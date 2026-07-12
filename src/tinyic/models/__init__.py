"""TinyIC model layer (FR-1.x): bindings, thinking ladder, provider registry.

The model-agnostic backend that resolves a ``ModelBinding`` (``provider/model``
+ auth profile + thinking level + params) to a provider adapter.  This stage
(M2 stage 1) provides the core abstractions; the wire-format transports that
execute calls are added in stage 2.

Public surface::

    from tinyic.models import ModelBinding, ThinkingLevel, get_provider
"""

from __future__ import annotations

from .binding import ModelBinding, ModelRefError, parse_model_ref
from .binding_client import BindingClient, DeltaSink, UsageSink, build_transport
from .committee import Committee, TransportFactory as CommitteeTransportFactory
from .committee import build_committee
from .credentials import (
    CredentialProvider,
    EnvCredentialProvider,
    StaticCredentialProvider,
)
from .presets import (
    BUILTIN_DEFAULT_MODEL,
    BUILTIN_DEFAULT_THINKING,
    BindingSpec,
    DEFAULT_PRESET_NAME,
    Preset,
    PresetError,
    builtin_default_preset,
    load_config,
    load_preset,
    validate_preset_thinking,
)
from .routing import activate, active_client, install_client_resolver
from .registry import (
    ModelSpec,
    Provider,
    ProviderRegistry,
    TransportFactory,
    build_default_registry,
    default_registry,
    get_provider,
    list_providers,
    provider_for_binding,
    register_provider,
    resolve_binding_thinking,
)
from .thinking import (
    THINKING_LADDER,
    ThinkingLevel,
    ThinkingProfile,
    ThinkingResolution,
    ThinkingStrategy,
    UnsupportedThinkingLevelError,
    nearest_supported,
)
from .types import (
    AuthError,
    ChatMessage,
    ChatRequest,
    ChatStreamEvent,
    ErrorKind,
    FinalMessage,
    FinishReason,
    InvalidRequestError,
    ProviderError,
    RateLimitError,
    RETRYABLE_KINDS,
    ReasoningDelta,
    Role,
    TextDelta,
    TransientError,
    Transport,
    Usage,
    WireFormat,
    is_retryable,
)

__all__ = [
    # binding
    "ModelBinding",
    "ModelRefError",
    "parse_model_ref",
    # thinking
    "THINKING_LADDER",
    "ThinkingLevel",
    "ThinkingProfile",
    "ThinkingResolution",
    "ThinkingStrategy",
    "UnsupportedThinkingLevelError",
    "nearest_supported",
    # types
    "ChatMessage",
    "ChatRequest",
    "ChatStreamEvent",
    "FinalMessage",
    "FinishReason",
    "ReasoningDelta",
    "Role",
    "TextDelta",
    "Transport",
    "Usage",
    "WireFormat",
    # error taxonomy
    "ErrorKind",
    "RETRYABLE_KINDS",
    "ProviderError",
    "AuthError",
    "RateLimitError",
    "TransientError",
    "InvalidRequestError",
    "is_retryable",
    # credentials (auth seam; M3 replaces the implementation)
    "CredentialProvider",
    "EnvCredentialProvider",
    "StaticCredentialProvider",
    # registry
    "ModelSpec",
    "Provider",
    "ProviderRegistry",
    "TransportFactory",
    "register_provider",
    "get_provider",
    "list_providers",
    "provider_for_binding",
    "resolve_binding_thinking",
    "build_default_registry",
    "default_registry",
    # binding client + routing (M2 stage 3 engine integration)
    "BindingClient",
    "DeltaSink",
    "UsageSink",
    "build_transport",
    "activate",
    "active_client",
    "install_client_resolver",
    # presets + committee (FR-1.4)
    "BindingSpec",
    "Preset",
    "PresetError",
    "BUILTIN_DEFAULT_MODEL",
    "BUILTIN_DEFAULT_THINKING",
    "DEFAULT_PRESET_NAME",
    "builtin_default_preset",
    "load_config",
    "load_preset",
    "validate_preset_thinking",
    "Committee",
    "CommitteeTransportFactory",
    "build_committee",
]
