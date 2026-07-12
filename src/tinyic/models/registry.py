"""Provider registry and the built-in v1 provider catalog (FR-1.2).

A :class:`Provider` descriptor owns a wire format, a static v1 model catalog,
per-model :class:`ThinkingProfile`s, and a transport factory.  ``register_provider``
adds providers to a registry; ``ModelBinding``s resolve to a provider by the
leading ``provider/`` segment of their ``model_ref``.

Transports themselves arrive in M2 stage 2; here a provider's
``transport_factory`` may be ``None`` and :meth:`Provider.new_transport` raises
a clear error until an adapter wires it.  The static catalogs below are v1
seeds — small, representative, and meant to be extended per provider — but the
thinking profiles are already accurate enough to drive the capability-gate
matrix (M2 DoD).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .adapters import (
    anthropic_messages,
    openai_chat,
    openai_compatible,
    openai_responses,
)
from .adapters.anthropic_messages import ANTHROPIC_THINKING_BUDGETS
from .binding import ModelBinding
from .credentials import CredentialProvider
from .thinking import ThinkingLevel as _L
from .thinking import ThinkingProfile, ThinkingResolution
from .types import Transport, WireFormat

#: A provider builds a bound transport from a binding + credential seam.
TransportFactory = Callable[[ModelBinding, CredentialProvider], Transport]


@dataclass(frozen=True)
class ModelSpec:
    """A catalog entry: a model id and its thinking capability.

    ``wire_format`` overrides the provider's default for the rare model that
    speaks a different family (e.g. an OpenAI Responses-only model); ``None``
    inherits the provider's wire format.
    """

    model_id: str
    thinking: ThinkingProfile
    wire_format: WireFormat | None = None
    subscription_only: bool = False


@dataclass(frozen=True)
class Provider:
    """A provider descriptor (FR-1.2).

    Adding a provider is a new :class:`Provider` plus a registry entry — no
    changes elsewhere.
    """

    name: str
    wire_format: WireFormat
    models: Iterable[ModelSpec] = ()
    default_thinking: ThinkingProfile = field(default_factory=ThinkingProfile.omitted)
    transport_factory: TransportFactory | None = None

    def __post_init__(self) -> None:
        catalog: dict[str, ModelSpec] = {}
        for spec in self.models:
            catalog[spec.model_id] = spec
        object.__setattr__(self, "models", catalog)

    def catalog(self) -> tuple[str, ...]:
        """The static v1 model ids this provider ships, in insertion order."""
        return tuple(self.models)  # type: ignore[arg-type]

    def thinking_profile(self, model: str) -> ThinkingProfile:
        """The thinking profile for ``model`` (provider default if unlisted).

        Unknown models fall back to ``default_thinking`` rather than raising, so
        custom/local models (Ollama, OpenAI-compatible endpoints) still work; the
        conservative default is to omit the thinking parameter.
        """
        spec = self.models.get(model)  # type: ignore[attr-defined]
        return spec.thinking if spec is not None else self.default_thinking

    def wire_format_for(self, model: str) -> WireFormat:
        """The wire format for ``model`` (its override, else the provider's)."""
        spec = self.models.get(model)  # type: ignore[attr-defined]
        if spec is not None and spec.wire_format is not None:
            return spec.wire_format
        return self.wire_format

    def resolve_thinking(
        self, model: str, level: ThinkingLevel, *, runtime: bool = False
    ) -> ThinkingResolution:
        """Resolve a thinking ``level`` against ``model``'s profile (FR-1.3)."""
        return self.thinking_profile(model).resolve(level, runtime=runtime)

    def new_transport(
        self, binding: ModelBinding, credentials: CredentialProvider
    ) -> Transport:
        """Build a transport for ``binding`` (raises until stage 2 wires it)."""
        if self.transport_factory is None:
            raise NotImplementedError(
                f"provider {self.name!r} has no transport yet; provider adapters "
                "arrive in M2 stage 2"
            )
        return self.transport_factory(binding, credentials)


# Re-export for annotations without a second import name at call sites.
ThinkingLevel = _L


class ProviderRegistry:
    """A mutable set of providers keyed by case-insensitive name.

    Instantiate a fresh registry for isolation (tests build their own rather
    than mutating the module default), or use the module-level
    ``register_provider``/``get_provider`` helpers over the shared default.
    """

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider, *, replace: bool = False) -> Provider:
        key = provider.name.lower()
        if key in self._providers and not replace:
            raise ValueError(f"provider {provider.name!r} is already registered")
        self._providers[key] = provider
        return provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name.lower(), None)

    def get(self, name: str) -> Provider:
        try:
            return self._providers[name.lower()]
        except KeyError:
            known = ", ".join(self.names()) or "(none)"
            raise KeyError(
                f"unknown provider {name!r}; registered: {known}"
            ) from None

    def provider_for(self, binding: ModelBinding) -> Provider:
        """Resolve the provider a ``binding``'s ``model_ref`` selects."""
        return self.get(binding.provider)

    def resolve_thinking(
        self, binding: ModelBinding, *, runtime: bool = False
    ) -> ThinkingResolution:
        """Resolve ``binding``'s thinking level against its model (FR-1.3)."""
        provider = self.get(binding.provider)
        return provider.resolve_thinking(
            binding.model, binding.thinking_level, runtime=runtime
        )

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.lower() in self._providers

    def __len__(self) -> int:
        return len(self._providers)


# Provider endpoints and credential env-var references for the v1 key lanes
# (M2 wires the transports here; M3 adds subscription runtimes + auth profiles).
_OPENAI_BASE_URL = openai_chat.DEFAULT_BASE_URL
_ANTHROPIC_BASE_URL = anthropic_messages.DEFAULT_BASE_URL
_XAI_BASE_URL = "https://api.x.ai/v1"
_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
_OLLAMA_BASE_URL = openai_compatible.DEFAULT_BASE_URL


def _thinking_lookup(
    models: Iterable[ModelSpec], default: ThinkingProfile
) -> Callable[[str], ThinkingProfile]:
    """A per-model ``ThinkingProfile`` lookup for a provider's transport factory.

    Mirrors :meth:`Provider.thinking_profile` but is a standalone closure so the
    factory (built here, before the ``Provider`` exists) stays decoupled from the
    registry — adapters never import back into it.
    """
    table = {spec.model_id: spec.thinking for spec in models}

    def lookup(model: str) -> ThinkingProfile:
        return table.get(model, default)

    return lookup


def _openai_dispatch_factory(
    models: Iterable[ModelSpec],
    default_thinking: ThinkingProfile,
    *,
    base_url: str,
    credential_ref: str | None,
) -> TransportFactory:
    """OpenAI serves *both* wire formats — dispatch Chat vs Responses per model."""
    models = tuple(models)
    lookup = _thinking_lookup(models, default_thinking)
    wire = {
        spec.model_id: (spec.wire_format or WireFormat.OPENAI_CHAT) for spec in models
    }
    chat = openai_chat.make_factory(
        base_url=base_url, credential_ref=credential_ref, thinking_lookup=lookup
    )
    responses = openai_responses.make_factory(
        base_url=base_url, credential_ref=credential_ref, thinking_lookup=lookup
    )

    def factory(binding: ModelBinding, credentials: CredentialProvider) -> Transport:
        fmt = wire.get(binding.model, WireFormat.OPENAI_CHAT)
        chosen = responses if fmt is WireFormat.OPENAI_RESPONSES else chat
        return chosen(binding, credentials)

    return factory


def _builtin_providers() -> tuple[Provider, ...]:
    """The v1 bundled providers (FR-1.2), with static seed catalogs + transports.

    Wire formats: OpenAI on canonical Chat Completions *and* the Responses API
    (dispatched per model); Anthropic on Messages; xAI and DeepSeek on the
    OpenAI Chat schema against their own base URLs; Google Gemini and local
    Ollama on the OpenAI-compatible schema.  A native Gemini adapter is deferred
    (smallest M2 interpretation): Gemini routes via ``openai-compatible`` and its
    ``thinkingBudget`` degrades away if that endpoint rejects it.  Subscription
    runtimes (Codex, Claude) and auth profiles arrive in M3.
    """
    openai_models = [
        ModelSpec(
            "gpt-5.2",
            ThinkingProfile.effort(
                "reasoning_effort",
                {
                    _L.MINIMAL: "minimal",
                    _L.LOW: "low",
                    _L.MEDIUM: "medium",
                    _L.HIGH: "high",
                    _L.XHIGH: "xhigh",
                },
            ),
        ),
        # A Responses-API model so "openai (both formats)" is reachable end to
        # end.  Flagged subscription_only (its auth lane is M3); M2 exercises the
        # wire routing (Chat vs Responses), which is format- not auth-driven.
        ModelSpec(
            "gpt-5.6-sol",
            ThinkingProfile.nested_effort(
                ("reasoning", "effort"),
                {
                    _L.MINIMAL: "minimal",
                    _L.LOW: "low",
                    _L.MEDIUM: "medium",
                    _L.HIGH: "high",
                    _L.XHIGH: "xhigh",
                },
            ),
            wire_format=WireFormat.OPENAI_RESPONSES,
            subscription_only=True,
        ),
    ]
    # Unknown OpenAI models: assume a standard low/medium/high effort knob.
    openai_default_thinking = ThinkingProfile.effort(
        "reasoning_effort",
        {_L.LOW: "low", _L.MEDIUM: "medium", _L.HIGH: "high"},
    )
    openai = Provider(
        name="openai",
        wire_format=WireFormat.OPENAI_CHAT,
        models=openai_models,
        default_thinking=openai_default_thinking,
        transport_factory=_openai_dispatch_factory(
            openai_models,
            openai_default_thinking,
            base_url=_OPENAI_BASE_URL,
            credential_ref="OPENAI_API_KEY",
        ),
    )

    anthropic_models = [
        ModelSpec(
            "claude-opus-4-8",
            ThinkingProfile.budget("budget_tokens", ANTHROPIC_THINKING_BUDGETS),
        ),
    ]
    anthropic = Provider(
        name="anthropic",
        wire_format=WireFormat.ANTHROPIC_MESSAGES,
        models=anthropic_models,
        transport_factory=anthropic_messages.make_factory(
            base_url=_ANTHROPIC_BASE_URL,
            credential_ref="ANTHROPIC_API_KEY",
            thinking_lookup=_thinking_lookup(
                anthropic_models, ThinkingProfile.omitted()
            ),
        ),
    )

    google_models = [
        ModelSpec(
            "gemini-2.5-pro",
            ThinkingProfile.budget(
                "thinkingBudget",
                {_L.LOW: 1024, _L.MEDIUM: 8192, _L.HIGH: 24576},
            ),
        ),
    ]
    google = Provider(
        name="google",
        wire_format=WireFormat.OPENAI_COMPATIBLE,
        models=google_models,
        transport_factory=openai_compatible.make_factory(
            base_url=_GEMINI_OPENAI_BASE_URL,
            credential_ref="GEMINI_API_KEY",
            thinking_lookup=_thinking_lookup(google_models, ThinkingProfile.omitted()),
        ),
    )

    xai_models = [ModelSpec("grok-4", ThinkingProfile.omitted())]
    xai = Provider(
        name="xai",
        # Grok reasons by default with no thinking parameter -> omit entirely.
        wire_format=WireFormat.OPENAI_CHAT,
        models=xai_models,
        transport_factory=openai_chat.make_factory(
            base_url=_XAI_BASE_URL,
            credential_ref="XAI_API_KEY",
            thinking_lookup=_thinking_lookup(xai_models, ThinkingProfile.omitted()),
        ),
    )

    deepseek_models = [
        ModelSpec(
            "deepseek-reasoner",
            ThinkingProfile.effort(
                "reasoning_effort",
                {_L.LOW: "low", _L.MEDIUM: "medium", _L.HIGH: "high"},
            ),
        ),
    ]
    deepseek = Provider(
        name="deepseek",
        wire_format=WireFormat.OPENAI_CHAT,
        models=deepseek_models,
        transport_factory=openai_chat.make_factory(
            base_url=_DEEPSEEK_BASE_URL,
            credential_ref="DEEPSEEK_API_KEY",
            thinking_lookup=_thinking_lookup(
                deepseek_models, ThinkingProfile.omitted()
            ),
        ),
    )

    ollama_models = [ModelSpec("qwen3:32b", ThinkingProfile.flag("think"))]
    ollama = Provider(
        name="ollama",
        wire_format=WireFormat.OPENAI_COMPATIBLE,
        models=ollama_models,
        transport_factory=openai_compatible.make_factory(
            base_url=_OLLAMA_BASE_URL,
            credential_ref=None,  # local, unauthenticated lane
            thinking_lookup=_thinking_lookup(ollama_models, ThinkingProfile.omitted()),
        ),
    )
    return (openai, anthropic, google, xai, deepseek, ollama)


def build_default_registry() -> ProviderRegistry:
    """A fresh registry populated with the built-in v1 providers."""
    registry = ProviderRegistry()
    for provider in _builtin_providers():
        registry.register(provider)
    return registry


_DEFAULT_REGISTRY = build_default_registry()


def default_registry() -> ProviderRegistry:
    """The shared module-level registry (built-ins pre-registered)."""
    return _DEFAULT_REGISTRY


def register_provider(provider: Provider, *, replace: bool = False) -> Provider:
    """Register ``provider`` in the shared default registry (FR-1.2)."""
    return _DEFAULT_REGISTRY.register(provider, replace=replace)


def get_provider(name: str) -> Provider:
    """Look up a provider by name in the shared default registry."""
    return _DEFAULT_REGISTRY.get(name)


def list_providers() -> tuple[str, ...]:
    """Names of providers in the shared default registry."""
    return _DEFAULT_REGISTRY.names()


def provider_for_binding(binding: ModelBinding) -> Provider:
    """Resolve a binding to its provider via the shared default registry."""
    return _DEFAULT_REGISTRY.provider_for(binding)


def resolve_binding_thinking(
    binding: ModelBinding, *, runtime: bool = False
) -> ThinkingResolution:
    """Resolve a binding's thinking level via the shared default registry."""
    return _DEFAULT_REGISTRY.resolve_thinking(binding, runtime=runtime)


__all__ = [
    "ModelSpec",
    "Provider",
    "ProviderRegistry",
    "TransportFactory",
    "build_default_registry",
    "default_registry",
    "get_provider",
    "list_providers",
    "provider_for_binding",
    "register_provider",
    "resolve_binding_thinking",
]
