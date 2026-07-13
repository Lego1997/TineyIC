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

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .adapters import (
    anthropic_messages,
    kimi_chat,
    openai_chat,
    openai_compatible,
    openai_responses,
)
from .adapters.anthropic_messages import (
    ANTHROPIC_ADAPTIVE_EFFORTS,
    ANTHROPIC_ADAPTIVE_MODELS,
    ANTHROPIC_BUDGET_MODELS,
    ANTHROPIC_THINKING_BUDGETS,
)
from .adapters.grok_subscription import (
    GROK_CATALOG_MODEL_IDS,
    GROK_REASONING_EFFORTS,
)
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
    auth_required: bool = True

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

    def model_spec(self, model: str) -> ModelSpec | None:
        """Return the catalog entry for ``model``, if this provider has one."""

        return self.models.get(model)  # type: ignore[attr-defined]

    def is_subscription_only(self, model: str) -> bool:
        """Whether the catalog legally restricts ``model`` to a subscription lane."""

        spec = self.model_spec(model)
        return bool(spec is not None and spec.subscription_only)

    def resolve_thinking(
        self, model: str, level: ThinkingLevel, *, runtime: bool = False
    ) -> ThinkingResolution:
        """Resolve a thinking ``level`` against ``model``'s profile (FR-1.3)."""
        return self.thinking_profile(model).resolve(level, runtime=runtime)

    def new_transport(
        self, binding: ModelBinding, credentials: CredentialProvider
    ) -> Transport:
        """Build a key transport or auth-profile rotating lane for ``binding``."""
        if self.transport_factory is None:
            raise NotImplementedError(
                f"provider {self.name!r} has no transport yet; provider adapters "
                "arrive in M2 stage 2"
            )
        # Import lazily: auth.manager depends on the model binding/types seam.
        from tinyic.auth.manager import AuthManager, AuthResolutionError

        subscription_only = self.is_subscription_only(binding.model)
        if isinstance(credentials, AuthManager):
            if not self.auth_required:
                # Credentialless local providers should not manufacture an
                # auth candidate merely to satisfy the rotation wrapper.
                return self.transport_factory(binding, credentials)
            return credentials.new_transport(
                binding,
                self._new_child_transport,
                subscription_only=subscription_only,
            )
        if subscription_only:
            # Fail before an API adapter can make a Platform request for a
            # subscription-only model.
            raise AuthResolutionError(
                "subscription_required",
                provider=self.name.lower(),
                auth_profile=binding.auth_profile,
            )
        return self.transport_factory(binding, credentials)

    def _new_child_transport(
        self, binding: ModelBinding, credentials: CredentialProvider
    ) -> Transport:
        """Dispatch one already-resolved auth candidate without changing model."""

        from tinyic.auth.profiles import ProfileKind

        kind = getattr(credentials, "kind", None)
        if self.name.lower() == "openai" and kind in {
            ProfileKind.OPENAI_OAUTH,
            ProfileKind.CODEX_READTHROUGH,
        }:
            from .adapters.codex_runtime import CodexRuntimeTransport

            return CodexRuntimeTransport(binding, credentials)
        if self.name.lower() == "anthropic" and kind in {
            ProfileKind.CLAUDE_RUNTIME,
            ProfileKind.CLAUDE_OAUTH_TOKEN,
        }:
            from .adapters.claude_runtime import ClaudeRuntimeTransport

            # AuthManager has already enforced its configured policy switch
            # before a candidate reaches this factory.  The transport keeps a
            # second default-on guard as defense in depth for direct callers.
            return ClaudeRuntimeTransport(binding, credentials)
        if self.name.lower() == "grok" and kind in {
            ProfileKind.GROK_OAUTH,
            ProfileKind.GROK_READTHROUGH,
        }:
            from .adapters.grok_subscription import GrokSubscriptionTransport

            # Same wire as the key lane, different credential seam; the
            # transport keeps its own default-on policy_guard defense.
            return GrokSubscriptionTransport(binding, credentials)
        return self.transport_factory(binding, credentials)  # type: ignore[misc]


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
_GROK_BASE_URL = "https://api.x.ai/v1"
_GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
_OLLAMA_BASE_URL = openai_compatible.DEFAULT_BASE_URL
#: Env override selecting the Moonshot China endpoint (``api.moonshot.cn``);
#: unset uses the international ``api.moonshot.ai`` base.  Read when a registry
#: is built, so ``build_default_registry()`` honors a changed environment.
KIMI_BASE_URL_ENV_VAR = "MOONSHOT_BASE_URL"


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
    """The bundled providers (FR-1.2), with static seed catalogs + transports.

    Wire formats: OpenAI on canonical Chat Completions *and* the Responses API
    (dispatched per model); Anthropic on Messages; Grok (xAI) and Kimi
    (Moonshot AI) on the OpenAI Chat schema against their own base URLs; Google
    Gemini and local Ollama on the OpenAI-compatible schema.  A native Gemini
    adapter is deferred: Gemini routes via ``openai-compatible`` and its effort
    param degrades away if that endpoint rejects it.  Catalogs current as of
    2026-07-14 (see ``tinyic.usage.MODEL_PRICES_AS_OF``).
    """
    # gpt-5.6 family: reasoning_effort none|minimal|low|medium|high|xhigh
    # ("none" carries the ladder's "off"); "max" is not a value -> runtime
    # remap to xhigh.  gpt-5.6-sol serves both wire formats; it stays routed on
    # the proven Responses path, terra/luna on Chat Completions.
    _gpt56_efforts = {
        _L.OFF: "none",
        _L.MINIMAL: "minimal",
        _L.LOW: "low",
        _L.MEDIUM: "medium",
        _L.HIGH: "high",
        _L.XHIGH: "xhigh",
    }
    openai_models = [
        ModelSpec(
            "gpt-5.6-sol",
            ThinkingProfile.nested_effort(("reasoning", "effort"), _gpt56_efforts),
            wire_format=WireFormat.OPENAI_RESPONSES,
        ),
        ModelSpec(
            "gpt-5.6-terra",
            ThinkingProfile.effort("reasoning_effort", _gpt56_efforts),
        ),
        ModelSpec(
            "gpt-5.6-luna",
            ThinkingProfile.effort("reasoning_effort", _gpt56_efforts),
        ),
        # Superseded but still servable; kept as a legacy catalog entry.
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

    # fable-5 / opus-4-8 / sonnet-5 speak the adaptive/effort scheme
    # (budget_tokens is a 400 there); haiku-4-5 keeps legacy budgets.  The
    # model→scheme split lives in anthropic_messages (shared with the
    # claude_runtime subscription lane) so the lanes cannot drift.
    _anthropic_adaptive = ThinkingProfile.adaptive_effort(ANTHROPIC_ADAPTIVE_EFFORTS)
    _anthropic_budget = ThinkingProfile.budget(
        "budget_tokens", ANTHROPIC_THINKING_BUDGETS
    )
    anthropic_models = [
        *(ModelSpec(name, _anthropic_adaptive) for name in ANTHROPIC_ADAPTIVE_MODELS),
        *(ModelSpec(name, _anthropic_budget) for name in ANTHROPIC_BUDGET_MODELS),
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

    # Gemini 3.x thinking levels ride the compat endpoint's effort knob; the
    # OpenAI-compatible adapter strips it gracefully if the server rejects it.
    google_models = [
        ModelSpec(
            "gemini-3.5-flash",
            ThinkingProfile.effort(
                "reasoning_effort",
                {
                    _L.MINIMAL: "minimal",
                    _L.LOW: "low",
                    _L.MEDIUM: "medium",
                    _L.HIGH: "high",
                },
            ),
        ),
        ModelSpec(
            "gemini-3.1-pro-preview",
            ThinkingProfile.effort(
                "reasoning_effort",
                {_L.LOW: "low", _L.MEDIUM: "medium", _L.HIGH: "high"},
            ),
        ),
        ModelSpec(
            "gemini-3.1-flash-lite",
            ThinkingProfile.effort(
                "reasoning_effort",
                {
                    _L.MINIMAL: "minimal",
                    _L.LOW: "low",
                    _L.MEDIUM: "medium",
                    _L.HIGH: "high",
                },
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

    # Grok reasons with an effort dial (low|medium|high); grok-4.5 cannot
    # disable reasoning, so "off" is a config-time reject / runtime remap→low
    # via the ordinary FR-1.3 semantics.  Env var stays XAI_API_KEY (xAI's own
    # convention survives the provider rename).  The dial *and* the catalog
    # ids are shared with the subscription transport
    # (grok_subscription.GROK_REASONING_EFFORTS / GROK_CATALOG_MODEL_IDS) so
    # both lanes gate identically.
    _grok_efforts = ThinkingProfile.effort(
        "reasoning_effort", GROK_REASONING_EFFORTS
    )
    grok_models = [
        ModelSpec(name, _grok_efforts) for name in GROK_CATALOG_MODEL_IDS
    ]
    grok = Provider(
        name="grok",
        wire_format=WireFormat.OPENAI_CHAT,
        models=grok_models,
        transport_factory=openai_chat.make_factory(
            base_url=_GROK_BASE_URL,
            credential_ref="XAI_API_KEY",
            thinking_lookup=_thinking_lookup(grok_models, ThinkingProfile.omitted()),
        ),
    )

    # Kimi thinking is ON by default: any level -> {"type": "enabled"} and
    # "off" -> {"type": "disabled"}.  The KimiChatAdapter adds the opt-in
    # server-side $web_search echo protocol (params.web_search = true).
    _kimi_toggle = ThinkingProfile.toggle("thinking")
    kimi_models = [
        ModelSpec("kimi-k2.6", _kimi_toggle),
        ModelSpec("kimi-k2.5", _kimi_toggle),
    ]
    kimi = Provider(
        name="kimi",
        wire_format=WireFormat.OPENAI_CHAT,
        models=kimi_models,
        transport_factory=kimi_chat.make_factory(
            base_url=os.environ.get(KIMI_BASE_URL_ENV_VAR)
            or kimi_chat.DEFAULT_BASE_URL,
            credential_ref="MOONSHOT_API_KEY",
            thinking_lookup=_thinking_lookup(kimi_models, ThinkingProfile.omitted()),
        ),
    )

    ollama_models = [
        ModelSpec("qwen3:32b", ThinkingProfile.flag("think")),
        ModelSpec("qwen3.5", ThinkingProfile.flag("think")),
        ModelSpec("deepseek-r1:14b", ThinkingProfile.flag("think")),
        ModelSpec("llama3.1:8b", ThinkingProfile.omitted()),
        ModelSpec("gemma4", ThinkingProfile.omitted()),
    ]
    ollama = Provider(
        name="ollama",
        wire_format=WireFormat.OPENAI_COMPATIBLE,
        models=ollama_models,
        auth_required=False,
        transport_factory=openai_compatible.make_factory(
            base_url=_OLLAMA_BASE_URL,
            credential_ref=None,  # local, unauthenticated lane
            thinking_lookup=_thinking_lookup(ollama_models, ThinkingProfile.omitted()),
        ),
    )
    return (openai, anthropic, google, grok, kimi, ollama)


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
    "KIMI_BASE_URL_ENV_VAR",
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
