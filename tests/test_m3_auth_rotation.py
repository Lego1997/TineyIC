"""FR-2.1 tests for profile resolution and usage-limit rotation.

These tests are deliberately transport-only and offline.  Provider SDKs,
OAuth servers, and subscription runtimes are represented by tiny iterator
fakes so the auth-order rules cannot accidentally acquire a network path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tinyic.auth.manager import (
    AuthCandidate,
    AuthManager,
    AuthResolutionError,
)
from tinyic.auth.profiles import (
    AuthLane,
    AuthProfile,
    ProfileKind,
    ProfileStore,
)
from tinyic.models.adapters.rotating import RotatingTransport
from tinyic.models.adapters.claude_runtime import ClaudeRuntimeTransport
from tinyic.models.binding import ModelBinding
from tinyic.models.credentials import CredentialProvider
from tinyic.models.types import (
    AuthError,
    ChatMessage,
    ChatRequest,
    FinalMessage,
    InvalidRequestError,
    RateLimitError,
    Role,
    TextDelta,
    Transport,
    TransientError,
    Usage,
    UsageLimitError,
    UsageWindow,
)


class MemoryKeyring:
    def __init__(self) -> None:
        self.value: str | None = None

    def get_password(self, _service: str, _username: str) -> str | None:
        return self.value

    def set_password(
        self, _service: str, _username: str, password: str
    ) -> None:
        self.value = password


def make_store(tmp_path: Path, *profiles: AuthProfile) -> ProfileStore:
    store = ProfileStore(
        keyring_backend=MemoryKeyring(), path=tmp_path / "unused.json"
    )
    for profile in profiles:
        store.put(profile)
    return store


def api_profile(
    ref: str,
    secret: str,
    *,
    expires_at: datetime | None = None,
) -> AuthProfile:
    return AuthProfile(
        ref,
        ProfileKind.API_KEY,
        AuthLane.API_KEY,
        secret,
        expires_at,
    )


def subscription_profile(
    ref: str, *, kind: ProfileKind = ProfileKind.CODEX_READTHROUGH
) -> AuthProfile:
    secret = (
        "user-minted-claude-token"
        if kind is ProfileKind.CLAUDE_OAUTH_TOKEN
        else None
    )
    return AuthProfile(
        ref,
        kind,
        AuthLane.SUBSCRIPTION,
        secret,
    )


def request(binding: ModelBinding) -> ChatRequest:
    return ChatRequest(
        [ChatMessage(Role.USER, "Analyze AAPL")], binding, stream=True
    )


class ScriptedTransport:
    def __init__(
        self,
        script: Callable[[ChatRequest], Iterator[object]],
    ) -> None:
        self._script = script
        self.requests: list[ChatRequest] = []

    def generate(self, value: ChatRequest) -> Iterator[object]:
        self.requests.append(value)
        yield from self._script(value)


def test_manager_is_a_credential_provider_for_named_keys_and_legacy_env(
    tmp_path: Path,
) -> None:
    first = api_profile("openai:first", "sk-first-distinct")
    second = api_profile("openai:second", "sk-second-distinct")
    manager = AuthManager(
        make_store(tmp_path, first, second),
        environ={"OPENAI_API_KEY": "  sk-env-fallback  "},
    )

    assert isinstance(manager, CredentialProvider)
    assert manager("openai:first") == "sk-first-distinct"
    assert manager("openai:second") == "sk-second-distinct"
    assert manager("OPENAI_API_KEY") == "sk-env-fallback"


def test_selected_candidate_releases_a_secret_only_for_its_allowed_refs(
    tmp_path: Path,
) -> None:
    profile = api_profile("openai:work", "scoped-secret")
    manager = AuthManager(make_store(tmp_path, profile), environ={})
    candidate = manager.candidates(
        ModelBinding("openai/gpt-5.2", auth_profile=profile.ref)
    )[0]

    assert candidate(profile.ref) == "scoped-secret"
    assert candidate("OPENAI_API_KEY") == "scoped-secret"
    assert candidate("ANTHROPIC_API_KEY") is None
    assert candidate("CLAUDE_CODE_OAUTH_TOKEN") is None
    assert manager("MISSING_KEY") is None


def test_user_minted_claude_oauth_token_is_a_subscription_candidate(
    tmp_path: Path,
) -> None:
    token = "user-minted-headless-token"
    manager = AuthManager(
        make_store(tmp_path),
        environ={"CLAUDE_CODE_OAUTH_TOKEN": token},
    )

    candidates = manager.candidates(
        ModelBinding("anthropic/claude-opus-4-8"),
        subscription_only=True,
    )

    assert len(candidates) == 1
    assert candidates[0].kind is ProfileKind.CLAUDE_OAUTH_TOKEN
    assert candidates[0].lane is AuthLane.SUBSCRIPTION
    assert candidates[0]("CLAUDE_CODE_OAUTH_TOKEN") == token
    assert token not in repr(candidates[0])


def test_anthropic_policy_guard_blocks_subscription_before_reading_token(
    tmp_path: Path,
) -> None:
    subscription = subscription_profile(
        "anthropic:claude-max", kind=ProfileKind.CLAUDE_RUNTIME
    )
    store = make_store(tmp_path, subscription)
    store.set_auth_order("anthropic", [subscription.ref])

    class GuardedEnvironment(dict[str, str]):
        token_reads = 0

        def get(self, key, default=None):
            if key == "CLAUDE_CODE_OAUTH_TOKEN":
                self.token_reads += 1
                raise AssertionError("policy-disabled path read the Claude token")
            return super().get(key, default)

    environment = GuardedEnvironment()
    manager = AuthManager(
        store,
        environ=environment,
        anthropic_policy_guard=False,
    )

    with pytest.raises(AuthResolutionError) as caught:
        manager.candidates(ModelBinding("anthropic/claude-opus-4-8"))

    assert caught.value.reason_code == "policy_disabled"
    assert environment.token_reads == 0


def test_anthropic_policy_guard_blocks_direct_token_resolution(
    tmp_path: Path,
) -> None:
    class GuardedEnvironment(dict[str, str]):
        token_reads = 0

        def get(self, key, default=None):
            if key == "CLAUDE_CODE_OAUTH_TOKEN":
                self.token_reads += 1
                raise AssertionError("policy-disabled path read the Claude token")
            return super().get(key, default)

    environment = GuardedEnvironment()
    manager = AuthManager(
        make_store(tmp_path),
        environ=environment,
        anthropic_policy_guard=False,
    )

    assert manager("CLAUDE_CODE_OAUTH_TOKEN") is None
    assert environment.token_reads == 0


def test_anthropic_policy_guard_skips_subscription_and_keeps_api_key_overflow(
    tmp_path: Path,
) -> None:
    subscription = subscription_profile(
        "anthropic:claude-max", kind=ProfileKind.CLAUDE_RUNTIME
    )
    overflow = api_profile("anthropic:overflow", "anthropic-key")
    store = make_store(tmp_path, subscription, overflow)
    store.set_auth_order("anthropic", [subscription.ref, overflow.ref])
    manager = AuthManager(
        store,
        environ={"CLAUDE_CODE_OAUTH_TOKEN": "must-not-be-read"},
        anthropic_policy_guard=False,
    )

    candidates = manager.candidates(
        ModelBinding(
            "anthropic/claude-opus-4-8",
            auth_profile=subscription.ref,
        )
    )

    assert [candidate.ref for candidate in candidates] == [overflow.ref]
    assert candidates[0].lane is AuthLane.API_KEY


def test_candidates_put_explicit_first_then_auth_order_then_transient_env(
    tmp_path: Path,
) -> None:
    subscription = subscription_profile("openai:chatgpt")
    overflow = api_profile("openai:overflow", "overflow-secret")
    explicit = api_profile("openai:explicit", "explicit-secret")
    store = make_store(tmp_path, subscription, overflow, explicit)
    store.set_auth_order(
        "openai", ["openai:chatgpt", "openai:overflow"]
    )
    manager = AuthManager(
        store, environ={"OPENAI_API_KEY": "environment-secret"}
    )

    candidates = manager.candidates(
        ModelBinding(
            "openai/gpt-5.2", auth_profile="openai:explicit"
        )
    )

    assert [candidate.ref for candidate in candidates] == [
        "openai:explicit",
        "openai:chatgpt",
        "openai:overflow",
        "openai:env-fallback",
    ]
    assert [candidate.source for candidate in candidates] == [
        "profile",
        "profile",
        "profile",
        "environment",
    ]


def test_kimi_environment_fallback_prefers_moonshot_then_kimi_env_var(
    tmp_path: Path,
) -> None:
    """Kimi's two-var env tuple: MOONSHOT_API_KEY wins over the KIMI_API_KEY
    fallback (mirroring google's GEMINI/GOOGLE pair)."""
    store = make_store(tmp_path)
    binding = ModelBinding("kimi/kimi-k2.6")

    both = AuthManager(
        store,
        environ={
            "MOONSHOT_API_KEY": "moonshot-secret",
            "KIMI_API_KEY": "kimi-secret",
        },
    )
    (candidate,) = both.candidates(binding)
    assert candidate.ref == "kimi:env-fallback"
    assert candidate.credential_ref == "MOONSHOT_API_KEY"
    assert candidate("MOONSHOT_API_KEY") == "moonshot-secret"

    fallback_only = AuthManager(
        store, environ={"KIMI_API_KEY": "kimi-secret"}
    )
    (candidate,) = fallback_only.candidates(binding)
    assert candidate.credential_ref == "KIMI_API_KEY"
    assert candidate("KIMI_API_KEY") == "kimi-secret"

    with pytest.raises(AuthResolutionError) as caught:
        AuthManager(store, environ={}).candidates(binding)
    assert caught.value.reason_code == "missing_credential"


def test_documented_env_alias_reaches_the_adapters_hardcoded_ref(
    tmp_path: Path,
) -> None:
    """Regression: a user who followed the docs and set only KIMI_API_KEY (or
    GOOGLE_API_KEY) must satisfy the adapter, which asks the candidate for the
    provider's primary ref (MOONSHOT_API_KEY / GEMINI_API_KEY)."""

    store = make_store(tmp_path)

    kimi = AuthManager(store, environ={"KIMI_API_KEY": "kimi-secret"})
    (candidate,) = kimi.candidates(ModelBinding("kimi/kimi-k2.6"))
    # The adapter's factory hardcodes credential_ref="MOONSHOT_API_KEY".
    assert candidate("MOONSHOT_API_KEY") == "kimi-secret"
    # Scoping stays provider-local: unrelated refs still resolve to nothing.
    assert candidate("OPENAI_API_KEY") is None

    google = AuthManager(store, environ={"GOOGLE_API_KEY": "google-secret"})
    (candidate,) = google.candidates(ModelBinding("google/gemini-3.5-flash"))
    assert candidate("GEMINI_API_KEY") == "google-secret"

    # A stored API-key profile answers the alias set too.
    stored = AuthManager(
        make_store(tmp_path, api_profile("kimi:main", "stored-secret")),
        environ={},
    )
    stored.store.set_auth_order("kimi", ["kimi:main"])
    (candidate,) = stored.candidates(ModelBinding("kimi/kimi-k2.6"))
    assert candidate("MOONSHOT_API_KEY") == "stored-secret"
    assert candidate("KIMI_API_KEY") == "stored-secret"


def test_kimi_adapter_resolves_a_kimi_alias_key_end_to_end(
    tmp_path: Path,
) -> None:
    """The exact seam that used to fail: KimiChatAdapter._resolve_key asks for
    MOONSHOT_API_KEY while the env fallback matched KIMI_API_KEY."""

    from tinyic.models.adapters.kimi_chat import DEFAULT_BASE_URL, KimiChatAdapter

    manager = AuthManager(
        make_store(tmp_path), environ={"KIMI_API_KEY": "kimi-alias-secret"}
    )
    binding = ModelBinding("kimi/kimi-k2.6")
    (candidate,) = manager.candidates(binding)
    adapter = KimiChatAdapter(
        binding,
        candidate,
        base_url=DEFAULT_BASE_URL,
        credential_ref="MOONSHOT_API_KEY",
    )

    assert adapter._resolve_key() == "kimi-alias-secret"


@pytest.mark.parametrize(
    ("provider", "model", "first_kind", "second_kind"),
    [
        (
            "openai",
            "gpt-5.6-sol",
            ProfileKind.CODEX_READTHROUGH,
            ProfileKind.OPENAI_OAUTH,
        ),
        (
            "anthropic",
            "claude-opus-4-8",
            ProfileKind.CLAUDE_RUNTIME,
            ProfileKind.CLAUDE_RUNTIME,
        ),
    ],
)
def test_runtime_owned_profile_aliases_do_not_rotate_the_same_external_login(
    tmp_path: Path,
    provider: str,
    model: str,
    first_kind: ProfileKind,
    second_kind: ProfileKind,
) -> None:
    first = subscription_profile(f"{provider}:first", kind=first_kind)
    second = subscription_profile(f"{provider}:second", kind=second_kind)
    store = make_store(tmp_path, first, second)
    store.set_auth_order(provider, [first.ref, second.ref])
    manager = AuthManager(store, environ={})

    candidates = manager.candidates(ModelBinding(f"{provider}/{model}"))

    assert [candidate.ref for candidate in candidates] == [first.ref]


def test_expired_profiles_are_skipped_and_report_a_safe_reason_when_exhausted(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 13, tzinfo=UTC)
    secret = "expired-super-secret"
    expired = api_profile(
        "openai:expired", secret, expires_at=now - timedelta(seconds=1)
    )
    live = api_profile("openai:live", "live-secret")
    store = make_store(tmp_path, expired, live)
    store.set_auth_order("openai", [expired.ref, live.ref])
    manager = AuthManager(store, environ={}, clock=lambda: now)

    candidates = manager.candidates(
        ModelBinding("openai/gpt-5.2", auth_profile=expired.ref)
    )
    assert [candidate.ref for candidate in candidates] == [live.ref]

    expired_only = make_store(tmp_path / "other", expired)
    expired_only.set_auth_order("openai", [expired.ref])
    with pytest.raises(AuthResolutionError) as caught:
        AuthManager(
            expired_only, environ={}, clock=lambda: now
        ).candidates(ModelBinding("openai/gpt-5.2"))
    assert caught.value.reason_code == "expired"
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_usage_blocks_are_manager_local_and_expire_when_retry_after_elapses(
    tmp_path: Path,
) -> None:
    instant = [datetime(2026, 7, 13, tzinfo=UTC)]
    first = api_profile("openai:first", "first-secret")
    second = api_profile("openai:second", "second-secret")
    store = make_store(tmp_path, first, second)
    store.set_auth_order("openai", [first.ref, second.ref])
    one = AuthManager(store, environ={}, clock=lambda: instant[0])
    two = AuthManager(store, environ={}, clock=lambda: instant[0])
    binding = ModelBinding("openai/gpt-5.2")

    one.mark_usage_limited(first.ref, retry_after=30)
    assert [candidate.ref for candidate in one.candidates(binding)] == [
        second.ref
    ]
    assert [candidate.ref for candidate in two.candidates(binding)] == [
        first.ref,
        second.ref,
    ]

    instant[0] += timedelta(seconds=31)
    assert [candidate.ref for candidate in one.candidates(binding)] == [
        first.ref,
        second.ref,
    ]


def test_rotating_transport_rotates_pre_output_then_sticks_without_model_change(
    tmp_path: Path,
) -> None:
    first = api_profile("openai:first", "first-secret")
    second = api_profile("openai:second", "second-secret")
    store = make_store(tmp_path, first, second)
    store.set_auth_order("openai", [first.ref, second.ref])
    manager = AuthManager(store, environ={})
    binding = ModelBinding("openai/gpt-5.2")
    factory_calls: list[tuple[str, str, str | None]] = []

    def factory(
        candidate_binding: ModelBinding,
        credentials: CredentialProvider,
    ) -> Transport:
        assert isinstance(credentials, AuthCandidate)
        candidate = credentials
        factory_calls.append(
            (
                candidate.ref,
                candidate_binding.model_ref,
                credentials("OPENAI_API_KEY"),
            )
        )
        if candidate.ref == first.ref:
            return ScriptedTransport(
                lambda _request: (_ for _ in ()).throw(
                    UsageLimitError("first profile is full")
                )
            )

        def success(_request: ChatRequest) -> Iterator[object]:
            usage = Usage(3, 2, 1)
            yield usage
            yield FinalMessage("BUY", usage=usage)

        return ScriptedTransport(success)

    transport = RotatingTransport(
        binding,
        manager.candidates(binding),
        factory,
        manager=manager,
    )

    first_events = list(transport.generate(request(binding)))
    second_events = list(transport.generate(request(binding)))

    assert [call[0] for call in factory_calls] == [first.ref, second.ref]
    assert {call[1] for call in factory_calls} == {binding.model_ref}
    assert factory_calls[0][2] == "first-secret"
    assert factory_calls[1][2] == "second-secret"
    assert transport.selected_profile_ref == second.ref
    for events in (first_events, second_events):
        usage = next(event for event in events if isinstance(event, Usage))
        assert usage.auth_profile == second.ref
        assert usage.lane == AuthLane.API_KEY.value
        final = next(
            event for event in events if isinstance(event, FinalMessage)
        )
        assert final.usage == usage
        assert not any(isinstance(event, UsageWindow) for event in events)


def test_subscription_success_uses_manager_meter_once_per_completed_call(
    tmp_path: Path,
) -> None:
    subscription = subscription_profile("openai:chatgpt")
    store = make_store(tmp_path, subscription)
    store.set_auth_order("openai", [subscription.ref])
    manager = AuthManager(
        store,
        environ={},
        window_estimates={subscription.ref: 100},
    )
    binding = ModelBinding("openai/gpt-5.6-sol")

    def factory(
        _binding: ModelBinding, _credentials: CredentialProvider
    ) -> Transport:
        def success(_request: ChatRequest) -> Iterator[object]:
            usage = Usage(10, 4)
            # A child-runtime estimate is ignored: the manager owns one
            # per-committee meter and emits exactly one authoritative snapshot.
            yield UsageWindow(subscription.ref, 999, 999)
            yield usage
            yield FinalMessage("HOLD", usage=usage)

        return ScriptedTransport(success)

    transport = RotatingTransport(
        binding,
        manager.candidates(binding, subscription_only=True),
        factory,
        manager=manager,
        subscription_only=True,
    )

    first = list(transport.generate(request(binding)))
    second = list(transport.generate(request(binding)))

    for expected, events in enumerate((first, second), start=1):
        windows = [
            event for event in events if isinstance(event, UsageWindow)
        ]
        assert len(windows) == 1
        assert windows[0].auth_profile == subscription.ref
        assert windows[0].window_used_msgs == expected
        assert windows[0].window_estimate_msgs == 100
        usage = next(event for event in events if isinstance(event, Usage))
        assert usage.auth_profile == subscription.ref
        assert usage.lane == AuthLane.SUBSCRIPTION.value
        assert isinstance(events[-1], FinalMessage)


def test_subscription_success_without_token_counts_synthesizes_zero_usage(
    tmp_path: Path,
) -> None:
    subscription = subscription_profile("openai:chatgpt")
    store = make_store(tmp_path, subscription)
    store.set_auth_order("openai", [subscription.ref])
    manager = AuthManager(store, environ={})
    binding = ModelBinding("openai/gpt-5.6-sol")

    def factory(
        _binding: ModelBinding, _credentials: CredentialProvider
    ) -> Transport:
        return ScriptedTransport(
            lambda _request: iter([FinalMessage("HOLD")])
        )

    events = list(
        RotatingTransport(
            binding,
            manager.candidates(binding, subscription_only=True),
            factory,
            manager=manager,
            subscription_only=True,
        ).generate(request(binding))
    )

    usage = next(event for event in events if isinstance(event, Usage))
    assert usage == Usage(
        auth_profile=subscription.ref, lane=AuthLane.SUBSCRIPTION.value
    )
    assert events[-1] == FinalMessage("HOLD", usage=usage)
    assert len([event for event in events if isinstance(event, UsageWindow)]) == 1


def test_final_only_child_usage_is_promoted_to_a_standalone_usage_event(
    tmp_path: Path,
) -> None:
    subscription = subscription_profile("openai:chatgpt")
    store = make_store(tmp_path, subscription)
    store.set_auth_order("openai", [subscription.ref])
    manager = AuthManager(store, environ={})
    binding = ModelBinding("openai/gpt-5.6-sol")

    def factory(_binding, _credentials):
        return ScriptedTransport(
            lambda _request: iter([FinalMessage("HOLD", usage=Usage(4, 2))])
        )

    events = list(
        RotatingTransport(
            binding,
            manager.candidates(binding, subscription_only=True),
            factory,
            manager=manager,
            subscription_only=True,
        ).generate(request(binding))
    )

    usages = [event for event in events if isinstance(event, Usage)]
    assert len(usages) == 1
    assert usages[0].auth_profile == subscription.ref
    assert events[-1].usage == usages[0]


def test_usage_limit_after_any_event_is_not_replayed_and_is_sanitized(
    tmp_path: Path,
) -> None:
    secret = "provider-error-secret"
    first = api_profile("openai:first", "first-key")
    second = api_profile("openai:second", "second-key")
    store = make_store(tmp_path, first, second)
    store.set_auth_order("openai", [first.ref, second.ref])
    manager = AuthManager(store, environ={})
    binding = ModelBinding("openai/gpt-5.2")
    factory_calls: list[str] = []

    def factory(
        _binding: ModelBinding, credentials: CredentialProvider
    ) -> Transport:
        candidate = credentials
        assert isinstance(candidate, AuthCandidate)
        factory_calls.append(candidate.ref)

        def partial(_request: ChatRequest) -> Iterator[object]:
            yield TextDelta("partial")
            raise UsageLimitError(f"limit for token {secret}")

        return ScriptedTransport(partial)

    transport = RotatingTransport(
        binding,
        manager.candidates(binding),
        factory,
        manager=manager,
    )
    stream = transport.generate(request(binding))

    assert next(stream) == TextDelta("partial")
    with pytest.raises(UsageLimitError) as caught:
        next(stream)
    assert factory_calls == [first.ref]
    assert manager.is_usage_limited(first.ref)
    assert [candidate.ref for candidate in manager.candidates(binding)] == [
        second.ref
    ]
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_claude_nonstreaming_partial_output_is_never_rotated(
    tmp_path: Path,
) -> None:
    first = subscription_profile(
        "anthropic:first", kind=ProfileKind.CLAUDE_RUNTIME
    )
    second = subscription_profile(
        "anthropic:second", kind=ProfileKind.CLAUDE_RUNTIME
    )
    store = make_store(tmp_path, first, second)
    store.set_auth_order("anthropic", [first.ref, second.ref])
    manager = AuthManager(store, environ={})
    binding = ModelBinding("anthropic/claude-opus-4-8")
    factory_calls: list[str] = []

    def factory(candidate_binding, credentials):
        assert isinstance(credentials, AuthCandidate)
        factory_calls.append(credentials.ref)

        def runner(_invocation):
            if credentials.ref == first.ref:
                return iter(
                    [
                        {
                            "type": "stream_event",
                            "event": {
                                "type": "content_block_delta",
                                "delta": {
                                    "type": "text_delta",
                                    "text": "partial",
                                },
                            },
                        },
                        {
                            "type": "rate_limit_event",
                            "rate_limit_info": {"status": "rejected"},
                        },
                    ]
                )
            return iter([{"type": "result", "result": "must not run"}])

        return ClaudeRuntimeTransport(
            candidate_binding,
            credentials,
            sdk_available=lambda: True,
            sdk_runner=runner,
        )

    transport = RotatingTransport(
        binding,
        manager.candidates(binding),
        factory,
        manager=manager,
    )
    nonstreaming = ChatRequest(
        [ChatMessage(Role.USER, "Analyze AAPL")], binding, stream=False
    )

    with pytest.raises(UsageLimitError):
        list(transport.generate(nonstreaming))

    assert factory_calls == [first.ref]
    assert manager.is_usage_limited(first.ref)


@pytest.mark.parametrize(
    "error_type",
    [AuthError, RateLimitError, InvalidRequestError, TransientError],
)
def test_all_provider_errors_are_sanitized_at_the_profile_boundary(
    tmp_path: Path, error_type: type[Exception]
) -> None:
    secret = "opaque-not-pattern-9xQ7"
    auth = api_profile("openai:work", secret)
    manager = AuthManager(make_store(tmp_path, auth), environ={})
    binding = ModelBinding("openai/gpt-5.2", auth_profile=auth.ref)

    def failing(
        _binding: ModelBinding, _credentials: CredentialProvider
    ) -> Transport:
        return ScriptedTransport(
            lambda _request: (_ for _ in ()).throw(
                error_type(
                    f"provider echoed opaque {secret}",
                    retry_after=17,
                    provider="openai",
                )
            )
        )

    transport = RotatingTransport(
        binding,
        manager.candidates(binding),
        failing,
        manager=manager,
    )

    with pytest.raises(error_type) as caught:
        list(transport.generate(request(binding)))
    assert caught.value.retry_after == 17
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_subscription_only_rejects_key_candidates_before_child_creation(
    tmp_path: Path,
) -> None:
    secret = "must-not-leak-key"
    key = api_profile("openai:key", secret)
    store = make_store(tmp_path, key)
    store.set_auth_order("openai", [key.ref])
    manager = AuthManager(store, environ={"OPENAI_API_KEY": "env-secret"})
    binding = ModelBinding("openai/gpt-5.6-sol")

    with pytest.raises(AuthResolutionError) as manager_error:
        manager.candidates(binding, subscription_only=True)
    assert manager_error.value.reason_code == "subscription_required"

    key_candidates = manager.candidates(binding)
    with pytest.raises(AuthResolutionError) as transport_error:
        RotatingTransport(
            binding,
            key_candidates,
            lambda _binding, _credentials: pytest.fail(
                "key child must not be created"
            ),
            manager=manager,
            subscription_only=True,
        )
    assert transport_error.value.reason_code == "subscription_required"
    assert secret not in str(transport_error.value)
    assert secret not in repr(transport_error.value)


def test_wrong_provider_profile_is_a_safe_lane_incompatibility(
    tmp_path: Path,
) -> None:
    secret = "anthropic-secret"
    anthropic = api_profile("anthropic:work", secret)
    manager = AuthManager(make_store(tmp_path, anthropic), environ={})

    with pytest.raises(AuthResolutionError) as caught:
        manager.candidates(
            ModelBinding(
                "openai/gpt-5.2", auth_profile="anthropic:work"
            )
        )

    assert caught.value.reason_code == "lane_incompatible"
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_missing_and_exhausted_usage_errors_are_reason_coded_and_secret_safe(
    tmp_path: Path,
) -> None:
    missing = AuthManager(make_store(tmp_path), environ={})
    with pytest.raises(AuthResolutionError) as missing_error:
        missing.candidates(ModelBinding("openai/gpt-5.2"))
    assert missing_error.value.reason_code == "missing_credential"

    secret = "quota-error-secret"
    only = api_profile("openai:only", "key")
    store = make_store(tmp_path / "quota", only)
    store.set_auth_order("openai", [only.ref])
    manager = AuthManager(store, environ={})
    binding = ModelBinding("openai/gpt-5.2")

    def exhausted(
        _binding: ModelBinding, _credentials: CredentialProvider
    ) -> Transport:
        return ScriptedTransport(
            lambda _request: (_ for _ in ()).throw(
                UsageLimitError(f"quota response {secret}")
            )
        )

    transport = RotatingTransport(
        binding,
        manager.candidates(binding),
        exhausted,
        manager=manager,
    )
    with pytest.raises(AuthResolutionError) as exhausted_error:
        list(transport.generate(request(binding)))
    assert exhausted_error.value.reason_code == "usage_limited"
    assert secret not in str(exhausted_error.value)
    assert secret not in repr(exhausted_error.value)
