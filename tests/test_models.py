"""Offline unit tests for the M2 model layer (FR-1.1, FR-1.2, FR-1.3 skeleton).

Covers ``provider/model`` parsing, the thinking-ladder resolution semantics,
the ``ModelBinding`` value object, the provider registry + built-in catalog,
the normalized request/stream types, the error taxonomy, and the M3 auth seam.
No network is touched.
"""

from __future__ import annotations

import dataclasses

import pytest

from tinyic.models import (
    THINKING_LADDER,
    AuthError,
    ChatMessage,
    ChatRequest,
    CredentialProvider,
    EnvCredentialProvider,
    ErrorKind,
    FinalMessage,
    FinishReason,
    InvalidRequestError,
    ModelBinding,
    ModelRefError,
    ModelSpec,
    Provider,
    ProviderError,
    ProviderRegistry,
    RateLimitError,
    ReasoningDelta,
    Role,
    StaticCredentialProvider,
    TextDelta,
    ThinkingLevel,
    ThinkingProfile,
    TransientError,
    Transport,
    Usage,
    UnsupportedThinkingLevelError,
    WireFormat,
    build_default_registry,
    default_registry,
    get_provider,
    is_retryable,
    list_providers,
    parse_model_ref,
    provider_for_binding,
    register_provider,
    resolve_binding_thinking,
)
from tinyic.models.thinking import nearest_supported

L = ThinkingLevel


@pytest.fixture(autouse=True)
def _restore_default_registry():
    """Isolate tests that register providers into the shared default registry."""
    registry = default_registry()
    before = dict(registry._providers)  # noqa: SLF001 - test guard
    yield
    registry._providers.clear()  # noqa: SLF001
    registry._providers.update(before)  # noqa: SLF001


# --------------------------------------------------------------------------
# model_ref parsing (FR-1.1)
# --------------------------------------------------------------------------


def test_parse_model_ref_splits_provider_and_model():
    assert parse_model_ref("anthropic/claude-opus-4-8") == (
        "anthropic",
        "claude-opus-4-8",
    )


def test_parse_model_ref_splits_on_first_slash_only():
    # OpenRouter-style ids keep their embedded slashes in the model segment.
    assert parse_model_ref("openrouter/meta-llama/llama-3") == (
        "openrouter",
        "meta-llama/llama-3",
    )


def test_parse_model_ref_preserves_colons_and_case():
    assert parse_model_ref("ollama/Qwen3:32b") == ("ollama", "Qwen3:32b")


def test_parse_model_ref_trims_surrounding_whitespace():
    assert parse_model_ref("  openai / gpt-5.2 ") == ("openai", "gpt-5.2")


@pytest.mark.parametrize(
    "bad",
    ["gpt-5.2", "openai/", "/gpt-5.2", "   /   ", "openai/   "],
)
def test_parse_model_ref_rejects_malformed(bad):
    with pytest.raises(ModelRefError):
        parse_model_ref(bad)


def test_parse_model_ref_rejects_non_string():
    with pytest.raises(ModelRefError):
        parse_model_ref(None)  # type: ignore[arg-type]


def test_model_ref_error_has_reason_code():
    assert ModelRefError.reason_code == "invalid_model_ref"


# --------------------------------------------------------------------------
# ModelBinding (FR-1.1)
# --------------------------------------------------------------------------


def test_binding_derives_provider_and_model():
    binding = ModelBinding("openai/gpt-5.2")
    assert binding.provider == "openai"
    assert binding.model == "gpt-5.2"


def test_binding_defaults():
    binding = ModelBinding("openai/gpt-5.2")
    assert binding.auth_profile is None
    assert binding.thinking_level is ThinkingLevel.MEDIUM
    assert binding.params == {}


def test_binding_normalizes_thinking_level_from_string_case_insensitively():
    assert ModelBinding("openai/gpt-5.2", thinking_level="HIGH").thinking_level is (
        ThinkingLevel.HIGH
    )
    assert ModelBinding("openai/gpt-5.2", thinking_level="xhigh").thinking_level is (
        ThinkingLevel.XHIGH
    )


def test_binding_rejects_bad_model_ref():
    with pytest.raises(ModelRefError):
        ModelBinding("no-slash-here")


def test_binding_is_frozen():
    binding = ModelBinding("openai/gpt-5.2")
    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.thinking_level = ThinkingLevel.LOW  # type: ignore[misc]


def test_binding_copies_params_defensively():
    source = {"temperature": 0.2}
    binding = ModelBinding("openai/gpt-5.2", params=source)
    source["temperature"] = 0.9
    assert binding.params == {"temperature": 0.2}


def test_binding_override_helpers_return_new_immutable_instances():
    binding = ModelBinding("openai/gpt-5.2", params={"temperature": 0.2})

    hotter = binding.with_thinking("low")
    assert hotter.thinking_level is ThinkingLevel.LOW
    assert binding.thinking_level is ThinkingLevel.MEDIUM  # original untouched

    profiled = binding.with_auth_profile("openai:work")
    assert profiled.auth_profile == "openai:work"
    assert binding.auth_profile is None

    tuned = binding.with_params(top_p=0.9)
    assert tuned.params == {"temperature": 0.2, "top_p": 0.9}
    assert binding.params == {"temperature": 0.2}


# --------------------------------------------------------------------------
# ThinkingLevel + ladder (FR-1.3)
# --------------------------------------------------------------------------


def test_ladder_is_the_seven_normalized_levels_in_order():
    assert THINKING_LADDER == (
        L.OFF,
        L.MINIMAL,
        L.LOW,
        L.MEDIUM,
        L.HIGH,
        L.XHIGH,
        L.MAX,
    )
    assert [level.rank for level in THINKING_LADDER] == list(range(7))


def test_thinking_level_parses_by_name_or_value_case_insensitively():
    assert ThinkingLevel("off") is L.OFF
    assert ThinkingLevel("OFF") is L.OFF
    assert ThinkingLevel("Medium") is L.MEDIUM
    with pytest.raises(ValueError):
        ThinkingLevel("ultra")


def test_nearest_supported_clamps_and_breaks_ties_toward_higher():
    supported = (L.LOW, L.HIGH)  # ranks 2 and 4
    # equidistant from MEDIUM (rank 3) -> tie resolves to the stronger level
    assert nearest_supported(L.MEDIUM, supported) is L.HIGH
    # below the range clamps up; above clamps down
    assert nearest_supported(L.OFF, supported) is L.LOW
    assert nearest_supported(L.MAX, supported) is L.HIGH


def test_nearest_supported_requires_non_empty():
    with pytest.raises(ValueError):
        nearest_supported(L.HIGH, ())


# --------------------------------------------------------------------------
# ThinkingProfile resolution (FR-1.3)
# --------------------------------------------------------------------------


def test_profile_normalizes_supported_to_ladder_order_and_dedupes():
    profile = ThinkingProfile.effort(
        "reasoning_effort", {L.HIGH: "high", L.LOW: "low", L.MEDIUM: "medium"}
    )
    assert profile.supported == (L.LOW, L.MEDIUM, L.HIGH)


def test_supported_level_renders_params_without_remap():
    profile = ThinkingProfile.effort(
        "reasoning_effort", {L.LOW: "low", L.MEDIUM: "medium", L.HIGH: "high"}
    )
    resolution = profile.resolve(L.HIGH)
    assert resolution.effective is L.HIGH
    assert resolution.params == {"reasoning_effort": "high"}
    assert resolution.remapped is False
    assert resolution.omitted is False


def test_config_time_unsupported_level_errors_listing_valid_set():
    profile = ThinkingProfile.effort(
        "reasoning_effort", {L.LOW: "low", L.MEDIUM: "medium", L.HIGH: "high"}
    )
    with pytest.raises(UnsupportedThinkingLevelError) as excinfo:
        profile.resolve(L.OFF)
    error = excinfo.value
    assert error.requested is L.OFF
    assert error.supported == (L.LOW, L.MEDIUM, L.HIGH)
    assert error.reason_code == "unsupported_thinking_level"
    # the message must actually list the valid options
    assert "low" in str(error) and "high" in str(error)


def test_runtime_override_remaps_to_nearest_supported():
    profile = ThinkingProfile.effort(
        "reasoning_effort", {L.LOW: "low", L.MEDIUM: "medium", L.HIGH: "high"}
    )
    resolution = profile.resolve(L.MAX, runtime=True)
    assert resolution.effective is L.HIGH  # nearest supported to MAX
    assert resolution.remapped is True
    assert resolution.params == {"reasoning_effort": "high"}
    assert resolution.requested is L.MAX


def test_model_rejecting_parameter_omits_entirely_and_never_errors():
    profile = ThinkingProfile.omitted()
    assert profile.accepts_thinking is False
    for runtime in (False, True):
        resolution = profile.resolve(L.HIGH, runtime=runtime)
        assert resolution.omitted is True
        assert resolution.effective is None
        assert resolution.params == {}
        assert resolution.remapped is False


def test_profile_supports_and_accepts_thinking_queries():
    profile = ThinkingProfile.budget("budget_tokens", {L.LOW: 1, L.HIGH: 2})
    assert profile.accepts_thinking is True
    assert profile.supports(L.LOW) is True
    assert profile.supports(L.MEDIUM) is False


def test_strategy_shapes():
    flat = ThinkingProfile.effort("reasoning_effort", {L.HIGH: "high"})
    assert flat.resolve(L.HIGH).params == {"reasoning_effort": "high"}

    nested = ThinkingProfile.nested_effort(("reasoning", "effort"), {L.HIGH: "high"})
    assert nested.resolve(L.HIGH).params == {"reasoning": {"effort": "high"}}

    budget = ThinkingProfile.budget("budget_tokens", {L.MEDIUM: 4096})
    assert budget.resolve(L.MEDIUM).params == {"budget_tokens": 4096}

    flag = ThinkingProfile.flag("think")
    assert flag.resolve(L.OFF).params == {"think": False}
    assert flag.resolve(L.HIGH).params == {"think": True}


def test_resolution_params_are_detached_copies():
    profile = ThinkingProfile.nested_effort(("reasoning", "effort"), {L.HIGH: "high"})
    first = profile.resolve(L.HIGH).params
    first["reasoning"]["effort"] = "mutated"
    # a fresh resolve must not see the mutation
    assert profile.resolve(L.HIGH).params == {"reasoning": {"effort": "high"}}


# --------------------------------------------------------------------------
# Provider registry (FR-1.2)
# --------------------------------------------------------------------------


def test_default_registry_has_the_v1_bundled_providers():
    assert set(list_providers()) == {
        "openai",
        "anthropic",
        "google",
        "xai",
        "deepseek",
        "ollama",
    }


def test_default_registry_helpers_share_one_instance():
    assert get_provider("openai") is default_registry().get("openai")


def test_get_provider_is_case_insensitive():
    assert get_provider("OpenAI") is get_provider("openai")


def test_get_provider_unknown_names_the_registered_set():
    with pytest.raises(KeyError) as excinfo:
        get_provider("acme")
    assert "acme" in str(excinfo.value)
    assert "openai" in str(excinfo.value)


def test_builtin_wire_formats():
    # openai + xai + deepseek speak canonical Chat Completions; anthropic
    # speaks Messages; google (Gemini) and ollama use the compatible schema.
    for chat in ("openai", "xai", "deepseek"):
        assert get_provider(chat).wire_format is WireFormat.OPENAI_CHAT
    assert get_provider("anthropic").wire_format is WireFormat.ANTHROPIC_MESSAGES
    for compatible in ("google", "ollama"):
        assert get_provider(compatible).wire_format is WireFormat.OPENAI_COMPATIBLE


def test_registry_register_duplicate_and_replace_semantics():
    registry = ProviderRegistry()
    provider = Provider("acme", WireFormat.OPENAI_COMPATIBLE)
    registry.register(provider)
    assert "acme" in registry
    assert len(registry) == 1

    with pytest.raises(ValueError):
        registry.register(Provider("acme", WireFormat.OPENAI_CHAT))

    replacement = Provider("acme", WireFormat.OPENAI_CHAT)
    registry.register(replacement, replace=True)
    assert registry.get("acme") is replacement

    registry.unregister("acme")
    assert "acme" not in registry


def test_module_register_provider_targets_the_default_registry():
    register_provider(Provider("acme", WireFormat.OPENAI_COMPATIBLE))
    assert "acme" in list_providers()
    # the autouse fixture restores the default registry afterward


def test_build_default_registry_is_isolated_from_the_shared_default():
    fresh = build_default_registry()
    fresh.register(Provider("acme", WireFormat.OPENAI_COMPATIBLE))
    assert "acme" in fresh
    assert "acme" not in default_registry()


def test_provider_catalog_and_thinking_profile_fallback():
    openai = get_provider("openai")
    # the catalog now seeds both a Chat model and a Responses model
    assert openai.catalog() == ("gpt-5.2", "gpt-5.6-sol")
    # a listed model uses its own profile
    assert openai.thinking_profile("gpt-5.2").supports(L.XHIGH) is True
    # the Responses model routes on the Responses wire format
    assert openai.wire_format_for("gpt-5.6-sol") is WireFormat.OPENAI_RESPONSES
    # an unlisted model falls back to the provider default (low/medium/high)
    fallback = openai.thinking_profile("some-unlisted-model")
    assert fallback.supported == (L.LOW, L.MEDIUM, L.HIGH)


def test_provider_wire_format_for_uses_model_override():
    spec = ModelSpec(
        "responsey",
        ThinkingProfile.omitted(),
        wire_format=WireFormat.OPENAI_RESPONSES,
    )
    provider = Provider("hybrid", WireFormat.OPENAI_CHAT, models=[spec])
    assert provider.wire_format_for("responsey") is WireFormat.OPENAI_RESPONSES
    assert provider.wire_format_for("unlisted") is WireFormat.OPENAI_CHAT


def test_resolve_binding_thinking_end_to_end():
    binding = ModelBinding("openai/gpt-5.2", thinking_level="high")
    resolution = resolve_binding_thinking(binding)
    assert resolution.effective is L.HIGH
    assert resolution.params == {"reasoning_effort": "high"}


def test_resolve_binding_thinking_config_error_bubbles_up():
    binding = ModelBinding("openai/gpt-5.2", thinking_level="off")
    with pytest.raises(UnsupportedThinkingLevelError):
        resolve_binding_thinking(binding)


def test_provider_for_binding_resolves_by_ref():
    assert provider_for_binding(ModelBinding("xai/grok-4")) is get_provider("xai")


# --------------------------------------------------------------------------
# Thinking-gate capability matrix (M2 DoD foundation)
# --------------------------------------------------------------------------


def _all_builtin_models():
    for name in list_providers():
        provider = get_provider(name)
        for model in provider.catalog():
            yield name, provider, model


@pytest.mark.parametrize("name,provider,model", list(_all_builtin_models()))
def test_thinking_gate_matrix_across_builtin_providers(name, provider, model):
    """Every (built-in model x ladder level) obeys the FR-1.3 semantics."""
    profile = provider.thinking_profile(model)
    for level in THINKING_LADDER:
        if not profile.accepts_thinking:
            # No knob -> always omit, never error, at config or runtime.
            assert provider.resolve_thinking(model, level).omitted
            assert provider.resolve_thinking(model, level, runtime=True).omitted
        elif profile.supports(level):
            resolution = provider.resolve_thinking(model, level)
            assert resolution.effective is level
            assert resolution.remapped is False
            assert resolution.params  # a supported level renders something
        else:
            # config-time -> error; runtime -> remap to the nearest supported
            with pytest.raises(UnsupportedThinkingLevelError):
                provider.resolve_thinking(model, level)
            remapped = provider.resolve_thinking(model, level, runtime=True)
            assert remapped.remapped is True
            assert remapped.effective in profile.supported
            assert remapped.effective is nearest_supported(level, profile.supported)


# --------------------------------------------------------------------------
# Normalized request/stream types (FR-1.2)
# --------------------------------------------------------------------------


def test_chat_message_coerces_role_from_string():
    message = ChatMessage("user", "hello")
    assert message.role is Role.USER
    assert ChatMessage("SYSTEM", "s").role is Role.SYSTEM


def test_chat_request_freezes_messages_to_a_tuple():
    binding = ModelBinding("openai/gpt-5.2")
    request = ChatRequest([ChatMessage("user", "q")], binding, stream=True)
    assert isinstance(request.messages, tuple)
    assert request.stream is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.stream = False  # type: ignore[misc]


def test_usage_total_tokens():
    assert Usage(input_tokens=10, output_tokens=4, cached_tokens=3).total_tokens == 14


def test_final_message_defaults():
    final = FinalMessage("answer")
    assert final.reasoning == ""
    assert final.usage is None
    assert final.finish_reason is FinishReason.STOP


def test_stream_event_types_are_distinct_payloads():
    assert TextDelta("a").text == "a"
    assert ReasoningDelta("b").text == "b"


# --------------------------------------------------------------------------
# Error taxonomy (FR-1.2 retry classification)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_cls,kind,retryable",
    [
        (AuthError, ErrorKind.AUTH, False),
        (RateLimitError, ErrorKind.RATE_LIMIT, True),
        (TransientError, ErrorKind.TRANSIENT, True),
        (InvalidRequestError, ErrorKind.INVALID_REQUEST, False),
    ],
)
def test_error_taxonomy_kinds_and_retry_classification(error_cls, kind, retryable):
    error = error_cls("boom")
    assert error.kind is kind
    assert error.retryable is retryable
    assert is_retryable(error) is retryable
    assert isinstance(error, ProviderError)


def test_rate_limit_error_carries_retry_after():
    error = RateLimitError("slow down", retry_after=1.5, provider="openai")
    assert error.retry_after == 1.5
    assert error.provider == "openai"


def test_base_provider_error_kind_can_be_overridden_per_instance():
    error = ProviderError("weird", kind=ErrorKind.RATE_LIMIT)
    assert error.retryable is True


def test_is_retryable_ignores_non_provider_errors():
    assert is_retryable(ValueError("x")) is False


# --------------------------------------------------------------------------
# Credential seam (M3-replaceable, FR-2.x)
# --------------------------------------------------------------------------


def test_env_credential_provider_reads_injected_environ():
    provider = EnvCredentialProvider({"OPENAI_API_KEY": " sk-abc ", "BLANK": "   "})
    assert provider("OPENAI_API_KEY") == "sk-abc"  # trimmed
    assert provider("BLANK") is None  # blank -> absent
    assert provider("MISSING") is None
    assert provider("") is None


def test_env_credential_provider_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("TINYIC_TEST_SECRET", "value")
    assert EnvCredentialProvider()("TINYIC_TEST_SECRET") == "value"


def test_static_credential_provider():
    provider = StaticCredentialProvider({"ref": "secret"})
    assert provider("ref") == "secret"
    assert provider("other") is None


def test_credential_providers_satisfy_the_protocol():
    assert isinstance(EnvCredentialProvider({}), CredentialProvider)
    assert isinstance(StaticCredentialProvider({}), CredentialProvider)


# --------------------------------------------------------------------------
# Transport seam wiring (stage-2 contract)
# --------------------------------------------------------------------------


class _FakeTransport:
    """A minimal stage-2-style transport used to prove the seam wiring."""

    def __init__(self, binding, credentials):
        self.binding = binding
        self.credentials = credentials

    def generate(self, request):
        yield ReasoningDelta("thinking...")
        yield TextDelta("hello")
        yield FinalMessage("hello", reasoning="thinking...", usage=Usage(5, 2, 0))


def test_provider_without_transport_factory_raises_clear_error():
    # A provider with no wired adapter (all built-ins now have one) still
    # raises a clear NotImplementedError from the seam.
    bare = Provider("bare", WireFormat.OPENAI_COMPATIBLE)
    binding = ModelBinding("bare/model")
    with pytest.raises(NotImplementedError):
        bare.new_transport(binding, StaticCredentialProvider({}))


def test_builtin_providers_have_wired_transports():
    # M2 stage 2: every bundled provider builds a real adapter transport.
    creds = StaticCredentialProvider(
        {
            "OPENAI_API_KEY": "k",
            "ANTHROPIC_API_KEY": "k",
            "XAI_API_KEY": "k",
            "DEEPSEEK_API_KEY": "k",
            "GEMINI_API_KEY": "k",
        }
    )
    refs = {
        "openai/gpt-5.2": "OpenAIChatAdapter",
        "openai/gpt-5.6-sol": "OpenAIResponsesAdapter",
        "anthropic/claude-opus-4-8": "AnthropicMessagesAdapter",
        "google/gemini-2.5-pro": "OpenAICompatibleAdapter",
        "xai/grok-4": "OpenAIChatAdapter",
        "deepseek/deepseek-reasoner": "OpenAIChatAdapter",
        "ollama/qwen3:32b": "OpenAICompatibleAdapter",
    }
    for model_ref, adapter_name in refs.items():
        binding = ModelBinding(model_ref)
        transport = provider_for_binding(binding).new_transport(binding, creds)
        assert isinstance(transport, Transport)
        assert type(transport).__name__ == adapter_name


def test_transport_factory_receives_binding_and_credential_seam():
    seen = {}

    def factory(binding, credentials):
        seen["binding"] = binding
        seen["credentials"] = credentials
        return _FakeTransport(binding, credentials)

    registry = build_default_registry()
    registry.register(
        Provider("fake", WireFormat.OPENAI_COMPATIBLE, transport_factory=factory)
    )
    binding = ModelBinding("fake/model-x")
    credentials = StaticCredentialProvider({"ref": "secret"})

    transport = registry.get("fake").new_transport(binding, credentials)

    assert isinstance(transport, Transport)
    assert seen["binding"] is binding
    assert seen["credentials"] is credentials

    events = list(transport.generate(ChatRequest([ChatMessage("user", "q")], binding)))
    assert [type(event).__name__ for event in events] == [
        "ReasoningDelta",
        "TextDelta",
        "FinalMessage",
    ]
    assert events[-1].usage.total_tokens == 7
