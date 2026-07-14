"""Offline contract tests for the Grok (xAI) subscription lane (v2.1 stage B).

Everything here is mocked: the OAuth token endpoint is an injected callable,
the API is a scripted fake HTTP transport, and the ``~/.grok/auth.json``
read-through runs against tmp files.  The never-writes guarantee is enforced
with a write-protected file.
"""

from __future__ import annotations

import json
import os
import stat
import urllib.error
from pathlib import Path

import pytest

from tinyic.auth import grok as grok_mod
from tinyic.auth.grok import (
    GROK_AUTH_FILE_KEY,
    GROK_AUTH_FILE_LEGACY_KEY,
    GROK_DEVICE_AUTHORIZATION_URL,
    GROK_DISCOVERY_URL,
    GROK_TOKEN_URL,
    GrokAuthReason,
    GrokDeviceCodeFlow,
    GrokDeviceLoginSession,
    GrokTokenError,
    GrokTokenSource,
    GrokTokens,
    file_token_loader,
    parse_grok_auth_document,
    probe_grok_auth,
    probe_grok_profile_secret,
    profile_token_loader,
    refresh_grok_tokens,
    tokens_from_profile_secret,
    tokens_to_profile_secret,
)
from tinyic.auth.manager import AuthManager, AuthResolutionError
from tinyic.auth.profiles import (
    AuthLane,
    AuthProfile,
    ProfileKind,
    ProfileStore,
)
from tinyic.models.adapters.grok_subscription import (
    DEFAULT_SUBSCRIPTION_BASE_URL,
    GROK_SUBSCRIPTION_BASE_URL_ENV_VAR,
    GrokPolicyError,
    GrokSubscriptionTransport,
)
from tinyic.models.binding import ModelBinding
from tinyic.models.registry import default_registry
from tinyic.models.thinking import ThinkingLevel
from tinyic.models.types import (
    AuthError,
    ChatMessage,
    ChatRequest,
    FinalMessage,
    Role,
    TextDelta,
    UsageLimitError,
)


NOW = 1_800_000_000.0  # a fixed offline "now" (epoch seconds)


class MemoryKeyring:
    def __init__(self) -> None:
        self.value: str | None = None

    def get_password(self, _service: str, _username: str) -> str | None:
        return self.value

    def set_password(self, _service: str, _username: str, value: str) -> None:
        self.value = value


def _store(tmp_path: Path, *profiles: AuthProfile) -> ProfileStore:
    store = ProfileStore(
        keyring_backend=MemoryKeyring(), path=tmp_path / "credentials.json"
    )
    for profile in profiles:
        store.put(profile)
    for provider in {profile.provider for profile in profiles}:
        store.set_auth_order(
            provider,
            [p.ref for p in profiles if p.provider == provider],
        )
    return store


def _write_auth_file(
    path: Path,
    *,
    key: str = GROK_AUTH_FILE_KEY,
    token_field: str = "key",
    access: str = "grok-access-token",
    refresh: str | None = "grok-refresh-token",
    expires_at: object = NOW + 3600,
) -> Path:
    entry: dict[str, object] = {token_field: access}
    if refresh is not None:
        entry["refresh_token"] = refresh
    if expires_at is not None:
        entry["expires_at"] = expires_at
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: entry}), encoding="utf-8")
    return path


def _oauth_profile(secret: str | None = None) -> AuthProfile:
    if secret is None:
        secret = tokens_to_profile_secret(
            GrokTokens("stored-access", "stored-refresh", NOW + 3600)
        )
    return AuthProfile(
        "grok:supergrok", ProfileKind.GROK_OAUTH, AuthLane.SUBSCRIPTION, secret
    )


# --------------------------------------------------------------------------
# profile kinds
# --------------------------------------------------------------------------


def test_grok_profile_kinds_enforce_secret_lane_and_provider_scoping():
    oauth = _oauth_profile()
    assert oauth.lane is AuthLane.SUBSCRIPTION
    assert oauth.secret is not None

    readthrough = AuthProfile(
        "grok:grok-cli", ProfileKind.GROK_READTHROUGH, AuthLane.SUBSCRIPTION
    )
    assert readthrough.secret is None

    with pytest.raises(ValueError, match="requires a secret"):
        AuthProfile("grok:x", ProfileKind.GROK_OAUTH, AuthLane.SUBSCRIPTION)
    with pytest.raises(ValueError, match="read-through"):
        AuthProfile(
            "grok:x",
            ProfileKind.GROK_READTHROUGH,
            AuthLane.SUBSCRIPTION,
            "leaked",
        )
    with pytest.raises(ValueError, match="incompatible with provider"):
        AuthProfile(
            "openai:x", ProfileKind.GROK_OAUTH, AuthLane.SUBSCRIPTION, "tok"
        )
    with pytest.raises(ValueError, match="subscription lane"):
        AuthProfile("grok:x", ProfileKind.GROK_OAUTH, AuthLane.API_KEY, "tok")


def test_grok_profiles_round_trip_through_the_store(tmp_path):
    store = _store(tmp_path, _oauth_profile())
    loaded = store.get("grok:supergrok")
    assert loaded is not None and loaded.kind is ProfileKind.GROK_OAUTH
    assert tokens_from_profile_secret(loaded.secret).access_token == (
        "stored-access"
    )
    public = json.dumps(loaded.public_dict())
    assert "stored-access" not in public and "stored-refresh" not in public


# --------------------------------------------------------------------------
# ~/.grok/auth.json parsing (lenient, current + legacy shapes)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", [GROK_AUTH_FILE_KEY, GROK_AUTH_FILE_LEGACY_KEY])
@pytest.mark.parametrize("token_field", ["key", "access_token", "token"])
def test_auth_file_parsing_accepts_both_keys_and_lenient_field_names(
    tmp_path, key, token_field
):
    path = _write_auth_file(
        tmp_path / "auth.json", key=key, token_field=token_field
    )
    tokens = parse_grok_auth_document(json.loads(path.read_text()))
    assert tokens is not None
    assert tokens.access_token == "grok-access-token"
    assert tokens.refresh_token == "grok-refresh-token"
    assert tokens.expires_at == NOW + 3600


def test_auth_file_parsing_coerces_millisecond_and_iso_expiries(tmp_path):
    millis = parse_grok_auth_document(
        {GROK_AUTH_FILE_KEY: {"key": "a", "expires_at": (NOW + 60) * 1000}}
    )
    assert millis.expires_at == pytest.approx(NOW + 60)
    iso = parse_grok_auth_document(
        {GROK_AUTH_FILE_KEY: {"key": "a", "expires_at": "2027-01-01T00:00:00Z"}}
    )
    assert iso.expires_at is not None


def test_auth_file_parsing_handles_flat_and_unknown_key_documents():
    flat = parse_grok_auth_document({"access_token": "a", "refresh_token": "r"})
    assert flat is not None and flat.access_token == "a"
    scanned = parse_grok_auth_document(
        {"https://auth.x.ai::other-client": {"token": "b"}}
    )
    assert scanned is not None and scanned.access_token == "b"
    assert parse_grok_auth_document({"unrelated": 3}) is None
    assert parse_grok_auth_document("not-a-mapping") is None


def test_probe_reports_missing_corrupt_expired_and_ok(tmp_path):
    missing = probe_grok_auth(tmp_path / "absent" / "auth.json", now=NOW)
    assert missing.reason is GrokAuthReason.MISSING_CREDENTIAL

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert (
        probe_grok_auth(corrupt, now=NOW).reason
        is GrokAuthReason.INVALID_CREDENTIAL
    )

    expired = _write_auth_file(
        tmp_path / "expired.json", refresh=None, expires_at=NOW - 10
    )
    assert probe_grok_auth(expired, now=NOW).reason is GrokAuthReason.EXPIRED

    # An expired access token is expired even when the file carries a refresh
    # token: TinyIC never redeems the CLI's refresh token, so read-through
    # usability rests on the access token alone.
    refreshable = _write_auth_file(
        tmp_path / "refreshable.json", expires_at=NOW - 10
    )
    assert probe_grok_auth(refreshable, now=NOW).reason is GrokAuthReason.EXPIRED

    ok = _write_auth_file(tmp_path / "ok.json")
    probe = probe_grok_auth(ok, now=NOW)
    assert probe.ok and probe.source == "grok_file"
    # Probe output is secret-free.
    assert "grok-access-token" not in repr(probe)


def test_profile_secret_probe_mirrors_the_file_probe():
    assert probe_grok_profile_secret(None, now=NOW).reason is (
        GrokAuthReason.MISSING_CREDENTIAL
    )
    assert probe_grok_profile_secret("{broken", now=NOW).reason is (
        GrokAuthReason.INVALID_CREDENTIAL
    )
    fresh = tokens_to_profile_secret(GrokTokens("a", None, NOW + 3600))
    assert probe_grok_profile_secret(fresh, now=NOW).ok
    dead = tokens_to_profile_secret(GrokTokens("a", None, NOW - 5))
    assert probe_grok_profile_secret(dead, now=NOW).reason is (
        GrokAuthReason.EXPIRED
    )
    # Unlike the read-through file, a TinyIC-owned document with a refresh
    # token stays usable when stale: TinyIC owns that grant and may redeem it.
    refreshable = tokens_to_profile_secret(GrokTokens("a", "r", NOW - 5))
    assert probe_grok_profile_secret(refreshable, now=NOW).ok


# --------------------------------------------------------------------------
# refresh + skew, and the read-through ownership boundary
# --------------------------------------------------------------------------


def _profile_secret_loader(secret: str):
    return profile_token_loader(lambda: secret)


def test_refresh_applies_the_two_minute_skew():
    calls: list[dict[str, str]] = []

    def http_post(url: str, form):
        calls.append(dict(form))
        return {
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "expires_in": 3600,
        }

    near = tokens_to_profile_secret(GrokTokens("a", "own-refresh", NOW + 90))
    source = GrokTokenSource(
        _profile_secret_loader(near), http_post=http_post, clock=lambda: NOW
    )
    # 90s from expiry is inside the 120s skew: refresh must fire.
    assert source.access_token() == "fresh-access"
    assert calls and calls[0]["grant_type"] == "refresh_token"
    assert calls[0]["refresh_token"] == "own-refresh"

    calls.clear()
    far = tokens_to_profile_secret(GrokTokens("far-access", "r", NOW + 600))
    relaxed = GrokTokenSource(
        _profile_secret_loader(far), http_post=http_post, clock=lambda: NOW
    )
    assert relaxed.access_token() == "far-access"
    assert calls == []  # comfortably fresh: no refresh traffic


def test_readthrough_never_redeems_the_cli_refresh_token(tmp_path):
    """FR-2.2 regression: redemption rotates the CLI's refresh token
    server-side, so the read-through lane must report ``expired`` instead of
    ever calling the refresh grant — and, of course, never write the file."""

    path = _write_auth_file(tmp_path / ".grok" / "auth.json", expires_at=NOW - 5)
    original = path.read_bytes()
    # Write-protect the file and its directory: any write attempt would raise.
    os.chmod(path, stat.S_IRUSR)
    os.chmod(path.parent, stat.S_IRUSR | stat.S_IXUSR)

    def forbidden_post(url, form):
        raise AssertionError(
            "read-through lane redeemed the grok CLI's refresh token"
        )

    try:
        source = GrokTokenSource(
            file_token_loader(path),
            http_post=forbidden_post,
            clock=lambda: NOW,
            allow_refresh=False,
        )
        with pytest.raises(GrokTokenError) as excinfo:
            source.access_token()
        assert excinfo.value.reason is GrokAuthReason.EXPIRED
        assert "grok-refresh-token" not in str(excinfo.value)
    finally:
        os.chmod(path.parent, stat.S_IRWXU)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    assert path.read_bytes() == original


def test_readthrough_transport_reports_expired_without_a_refresh_grant(tmp_path):
    """End to end through the transport: a stale ~/.grok/auth.json (refresh
    token present) is a fixed-message AuthError, with zero OAuth traffic."""

    path = _write_auth_file(tmp_path / "auth.json", expires_at=NOW - 5)

    def forbidden_post(url, form):
        raise AssertionError(
            "read-through transport redeemed the grok CLI's refresh token"
        )

    transport = GrokSubscriptionTransport(
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        _readthrough_candidate(),
        auth_path=path,
        http_post=forbidden_post,
        clock=lambda: NOW,
        environ={},
    )
    request = ChatRequest(
        [ChatMessage(Role.USER, "hi")],
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        stream=False,
    )
    with pytest.raises(AuthError, match="expired"):
        list(transport.generate(request))


def test_token_source_refreshes_from_the_rotated_refresh_token():
    """A server that rotates refresh tokens on use must see the rotated token
    on the second refresh, not a replay of the original (already-consumed)
    one from the unchanged underlying document."""

    stale = tokens_to_profile_secret(GrokTokens("old", "original-refresh", NOW - 5))
    forms: list[dict[str, str]] = []

    def http_post(url: str, form):
        forms.append(dict(form))
        count = len(forms)
        return {
            "access_token": f"access-{count}",
            "refresh_token": f"rotated-{count}",
            "expires_in": 60,
        }

    clock_values = iter([NOW, NOW + 3600])
    source = GrokTokenSource(
        _profile_secret_loader(stale),
        http_post=http_post,
        clock=lambda: next(clock_values),
    )
    assert source.access_token() == "access-1"
    assert source.access_token() == "access-2"  # first token's TTL passed
    assert [form["refresh_token"] for form in forms] == [
        "original-refresh",
        "rotated-1",
    ]


def test_refresh_failures_are_reason_coded_and_secret_free():
    expired_no_refresh = GrokTokens("a", None, NOW - 5)
    with pytest.raises(GrokTokenError) as excinfo:
        refresh_grok_tokens(expired_no_refresh, http_post=None, now=NOW)
    assert excinfo.value.reason is GrokAuthReason.EXPIRED

    rejected = GrokTokens("a", "refresh-secret-77", NOW - 5)
    with pytest.raises(GrokTokenError) as excinfo:
        refresh_grok_tokens(
            rejected,
            http_post=lambda url, form: {"error": "invalid_grant"},
            now=NOW,
        )
    assert excinfo.value.reason is GrokAuthReason.EXPIRED
    assert "refresh-secret-77" not in str(excinfo.value)
    assert "<redacted>" in repr(rejected)


def test_token_source_maps_missing_and_corrupt_sources(tmp_path):
    missing = GrokTokenSource(
        file_token_loader(tmp_path / "none.json"), clock=lambda: NOW
    )
    with pytest.raises(GrokTokenError) as excinfo:
        missing.access_token()
    assert excinfo.value.reason is GrokAuthReason.MISSING_CREDENTIAL

    corrupt_profile = GrokTokenSource(
        profile_token_loader(lambda: "{broken"), clock=lambda: NOW
    )
    with pytest.raises(GrokTokenError) as excinfo:
        corrupt_profile.access_token()
    assert excinfo.value.reason is GrokAuthReason.INVALID_CREDENTIAL


# --------------------------------------------------------------------------
# policy guard (config -> manager -> transport), incl. stored-profile refs
# --------------------------------------------------------------------------


def test_load_config_parses_the_grok_policy_guard(tmp_path):
    from tinyic.models.presets import PresetError, load_config

    path = tmp_path / "tinyic.toml"
    path.write_text(
        "[auth.grok]\npolicy_guard = false\n\n"
        "[presets.default]\nmodel = \"grok/grok-4.5\"\nthinking = \"high\"\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config["auth"]["grok"] == {"policy_guard": False}
    assert config["auth"]["anthropic"] == {"policy_guard": True}

    bad = tmp_path / "bad.toml"
    bad.write_text(
        "[auth.grok]\npolicy_guard = \"false\"\n\n"
        "[presets.default]\nmodel = \"grok/grok-4.5\"\nthinking = \"high\"\n",
        encoding="utf-8",
    )
    with pytest.raises(PresetError, match="auth.grok.policy_guard"):
        load_config(bad)


def test_from_config_plumbs_and_owns_the_grok_guard(tmp_path):
    path = tmp_path / "tinyic.toml"
    path.write_text(
        "[auth.grok]\npolicy_guard = false\n\n"
        "[presets.default]\nmodel = \"grok/grok-4.5\"\nthinking = \"high\"\n",
        encoding="utf-8",
    )
    manager = AuthManager.from_config(
        path, store=_store(tmp_path / "s"), environ={}
    )
    assert manager.grok_policy_guard is False
    assert manager.anthropic_policy_guard is True

    with pytest.raises(TypeError, match="grok_policy_guard"):
        AuthManager.from_config(
            path,
            store=_store(tmp_path / "s2"),
            environ={},
            grok_policy_guard=True,
        )


def test_guard_off_skips_grok_subscription_candidates_and_keeps_key_overflow(
    tmp_path,
):
    store = _store(tmp_path, _oauth_profile())
    binding = ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH)

    guarded = AuthManager(store, environ={}, grok_policy_guard=True)
    refs = [candidate.ref for candidate in guarded.candidates(binding)]
    assert refs == ["grok:supergrok"]

    disabled = AuthManager(store, environ={}, grok_policy_guard=False)
    with pytest.raises(AuthResolutionError) as excinfo:
        disabled.candidates(binding)
    assert excinfo.value.reason_code == "policy_disabled"

    # An XAI_API_KEY profile remains available as overflow — the guard only
    # removes the subscription lane, never the provider.
    overflow = AuthManager(
        store, environ={"XAI_API_KEY": "xai-key"}, grok_policy_guard=False
    )
    lanes = [candidate.lane for candidate in overflow.candidates(binding)]
    assert lanes == [AuthLane.API_KEY]


def test_guard_off_suppresses_stored_grok_profile_refs_in_call(tmp_path):
    """The b9e889f precedent: the guard covers the credential-provider seam."""
    secret = tokens_to_profile_secret(GrokTokens("seam-access", "r", NOW + 3600))
    store = _store(tmp_path, _oauth_profile(secret))

    enabled = AuthManager(store, environ={}, grok_policy_guard=True)
    assert enabled("grok:supergrok") == secret

    disabled = AuthManager(store, environ={}, grok_policy_guard=False)
    assert disabled("grok:supergrok") is None


def test_transport_guard_blocks_before_any_token_read(tmp_path):
    def forbidden_loader():
        raise AssertionError("policy-disabled transport read a token")

    transport = GrokSubscriptionTransport(
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        None,
        policy_guard=False,
        token_source=GrokTokenSource(forbidden_loader, clock=lambda: NOW),
    )
    request = ChatRequest(
        [ChatMessage(Role.USER, "hi")],
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        stream=False,
    )
    with pytest.raises(GrokPolicyError) as excinfo:
        list(transport.generate(request))
    assert excinfo.value.reason_code == "policy_disabled"


def test_transport_honors_a_disabled_manager_guard_on_credentials():
    class DisabledCandidate:
        kind = ProfileKind.GROK_READTHROUGH
        ref = "grok:grok-cli"
        grok_policy_guard = False

        def __call__(self, _ref):
            return None

    transport = GrokSubscriptionTransport(
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        DisabledCandidate(),
    )
    request = ChatRequest(
        [ChatMessage(Role.USER, "hi")],
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        stream=False,
    )
    with pytest.raises(GrokPolicyError):
        list(transport.generate(request))


# --------------------------------------------------------------------------
# the transport: wire shape, base URL, entitlement 403 -> rotation
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, body: str, headers=None) -> None:
        self.status_code = status_code
        self._body = body
        self._headers = headers or {}

    def header(self, name: str):
        for key, value in self._headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def iter_lines(self):
        yield from self._body.split("\n")

    def read_text(self) -> str:
        return self._body

    def close(self) -> None:
        pass


class ScriptedHttp:
    def __init__(self, *responses) -> None:
        self._responses = list(responses)
        self.sent = []

    def send(self, request):
        self.sent.append(request)
        return self._responses.pop(0)


def _completion_body(text: str = "hello") -> str:
    return json.dumps(
        {
            "choices": [
                {"message": {"content": text}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
    )


def _readthrough_candidate() -> AuthProfile:
    from tinyic.auth.manager import AuthCandidate

    return AuthCandidate(
        AuthProfile(
            "grok:grok-cli", ProfileKind.GROK_READTHROUGH, AuthLane.SUBSCRIPTION
        )
    )


def test_transport_sends_bearer_from_the_auth_file_to_api_x_ai(tmp_path):
    path = _write_auth_file(tmp_path / "auth.json")
    http = ScriptedHttp(FakeResponse(200, _completion_body()))
    transport = GrokSubscriptionTransport(
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        _readthrough_candidate(),
        auth_path=path,
        clock=lambda: NOW,
        http=http,
        environ={},
    )
    request = ChatRequest(
        [ChatMessage(Role.USER, "hi")],
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        stream=False,
    )
    events = list(transport.generate(request))
    final = [event for event in events if isinstance(event, FinalMessage)]
    assert final and final[0].text == "hello"

    sent = http.sent[0]
    assert sent.url == f"{DEFAULT_SUBSCRIPTION_BASE_URL}/chat/completions"
    assert sent.headers["authorization"] == "Bearer grok-access-token"
    assert sent.body["model"] == "grok-4.5"
    assert sent.body["reasoning_effort"] == "high"


def test_subscription_base_url_env_override_is_honored(tmp_path):
    path = _write_auth_file(tmp_path / "auth.json")
    http = ScriptedHttp(FakeResponse(200, _completion_body()))
    transport = GrokSubscriptionTransport(
        ModelBinding("grok/grok-4.3", thinking_level=ThinkingLevel.LOW),
        _readthrough_candidate(),
        auth_path=path,
        clock=lambda: NOW,
        http=http,
        environ={
            GROK_SUBSCRIPTION_BASE_URL_ENV_VAR: (
                "https://cli-chat-proxy.grok.com/v1"
            )
        },
    )
    request = ChatRequest(
        [ChatMessage(Role.USER, "hi")],
        ModelBinding("grok/grok-4.3", thinking_level=ThinkingLevel.LOW),
        stream=False,
    )
    list(transport.generate(request))
    assert http.sent[0].url == (
        "https://cli-chat-proxy.grok.com/v1/chat/completions"
    )


def test_entitlement_403_is_a_pre_output_usage_limit(tmp_path):
    path = _write_auth_file(tmp_path / "auth.json")
    body = (
        "You have either run out of available resources or do not have an "
        "active Grok subscription."
    )
    http = ScriptedHttp(FakeResponse(403, body))
    transport = GrokSubscriptionTransport(
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        _readthrough_candidate(),
        auth_path=path,
        clock=lambda: NOW,
        http=http,
        environ={},
    )
    request = ChatRequest(
        [ChatMessage(Role.USER, "hi")],
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        stream=False,
    )
    with pytest.raises(UsageLimitError) as excinfo:
        list(transport.generate(request))
    assert excinfo.value.reason_code == "subscription_inactive"
    assert excinfo.value.partial_output is False
    # The provider-controlled body text never crosses the adapter boundary.
    assert "run out of available resources" not in str(excinfo.value)


def test_plain_403_stays_an_auth_error(tmp_path):
    path = _write_auth_file(tmp_path / "auth.json")
    http = ScriptedHttp(FakeResponse(403, json.dumps({"error": "forbidden"})))
    transport = GrokSubscriptionTransport(
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        _readthrough_candidate(),
        auth_path=path,
        clock=lambda: NOW,
        http=http,
        environ={},
    )
    request = ChatRequest(
        [ChatMessage(Role.USER, "hi")],
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        stream=False,
    )
    with pytest.raises(AuthError):
        list(transport.generate(request))


def test_rotation_advances_past_an_entitlement_403_without_replay(tmp_path):
    """The whole seam: subscription 403 -> UsageLimitError -> next profile."""
    auth_file = _write_auth_file(tmp_path / "auth.json")
    store = _store(
        tmp_path,
        AuthProfile(
            "grok:grok-cli", ProfileKind.GROK_READTHROUGH, AuthLane.SUBSCRIPTION
        ),
        AuthProfile("grok:key", ProfileKind.API_KEY, AuthLane.API_KEY, "xai-k"),
    )
    manager = AuthManager(store, environ={})
    binding = ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH)

    entitlement_body = "No active Grok subscription."
    fallback_events = [TextDelta("ok"), FinalMessage(text="ok")]

    def child_factory(child_binding, candidate):
        if candidate.kind is ProfileKind.GROK_READTHROUGH:
            return GrokSubscriptionTransport(
                child_binding,
                candidate,
                auth_path=auth_file,
                clock=lambda: NOW,
                http=ScriptedHttp(FakeResponse(403, entitlement_body)),
                environ={},
            )

        class KeyTransport:
            def generate(self, _request):
                yield from fallback_events

        return KeyTransport()

    rotating = manager.new_transport(binding, child_factory)
    request = ChatRequest([ChatMessage(Role.USER, "hi")], binding, stream=True)
    events = list(rotating.generate(request))

    texts = [event.text for event in events if isinstance(event, TextDelta)]
    assert texts == ["ok"]
    assert manager.is_usage_limited("grok:grok-cli")
    assert rotating.selected_profile_ref == "grok:key"
    finals = [event for event in events if isinstance(event, FinalMessage)]
    assert len(finals) == 1  # never replayed, never duplicated


def test_readthrough_aliases_share_one_runtime_owner(tmp_path):
    store = _store(
        tmp_path,
        AuthProfile(
            "grok:cli-a", ProfileKind.GROK_READTHROUGH, AuthLane.SUBSCRIPTION
        ),
        AuthProfile(
            "grok:cli-b", ProfileKind.GROK_READTHROUGH, AuthLane.SUBSCRIPTION
        ),
    )
    manager = AuthManager(store, environ={})
    binding = ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH)
    refs = [candidate.ref for candidate in manager.candidates(binding)]
    # Two aliases of the one ~/.grok login are not independent fallbacks.
    assert refs == ["grok:cli-a"]


def test_registry_dispatches_grok_subscription_kinds_to_the_transport(tmp_path):
    from tinyic.auth.manager import AuthCandidate

    provider = default_registry().get("grok")
    binding = ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH)
    readthrough = provider._new_child_transport(
        binding.with_auth_profile("grok:grok-cli"), _readthrough_candidate()
    )
    assert isinstance(readthrough, GrokSubscriptionTransport)

    oauth = provider._new_child_transport(
        binding.with_auth_profile("grok:supergrok"),
        AuthCandidate(_oauth_profile()),
    )
    assert isinstance(oauth, GrokSubscriptionTransport)


def test_oauth_profile_candidate_feeds_the_stored_token_document(tmp_path):
    from tinyic.auth.manager import AuthCandidate

    candidate = AuthCandidate(_oauth_profile())
    http = ScriptedHttp(FakeResponse(200, _completion_body("stored")))
    transport = GrokSubscriptionTransport(
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        candidate,
        clock=lambda: NOW,
        http=http,
        environ={},
    )
    request = ChatRequest(
        [ChatMessage(Role.USER, "hi")],
        ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH),
        stream=False,
    )
    list(transport.generate(request))
    assert http.sent[0].headers["authorization"] == "Bearer stored-access"


def test_oauth_lane_persists_rotated_tokens_back_to_the_profile_store(tmp_path):
    """TinyIC owns GROK_OAUTH documents: after a refresh rotates the refresh
    token, the stored profile must hold the rotated document so the next
    process never replays the consumed one."""

    stale = tokens_to_profile_secret(
        GrokTokens("old-access", "old-refresh", NOW - 5)
    )
    store = _store(tmp_path, _oauth_profile(stale))
    manager = AuthManager(store, environ={})
    binding = ModelBinding("grok/grok-4.5", thinking_level=ThinkingLevel.HIGH)
    (candidate,) = manager.candidates(binding)

    oauth_forms: list[dict[str, str]] = []

    def http_post(url: str, form):
        oauth_forms.append(dict(form))
        return {
            "access_token": "new-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        }

    http = ScriptedHttp(FakeResponse(200, _completion_body()))
    transport = GrokSubscriptionTransport(
        binding,
        candidate,
        http_post=http_post,
        clock=lambda: NOW,
        http=http,
        environ={},
    )
    request = ChatRequest([ChatMessage(Role.USER, "hi")], binding, stream=False)
    list(transport.generate(request))

    assert oauth_forms and oauth_forms[0]["refresh_token"] == "old-refresh"
    assert http.sent[0].headers["authorization"] == "Bearer new-access"
    stored = store.get("grok:supergrok")
    tokens = tokens_from_profile_secret(stored.secret)
    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "rotated-refresh"


def test_subscription_lane_omits_reasoning_for_out_of_catalog_models(tmp_path):
    """Same capability gate as the key lane: an out-of-catalog model gets the
    reasoning parameter omitted, never the catalog models' effort dial."""

    path = _write_auth_file(tmp_path / "auth.json")
    http = ScriptedHttp(FakeResponse(200, _completion_body()))
    binding = ModelBinding("grok/grok-4", thinking_level=ThinkingLevel.HIGH)
    transport = GrokSubscriptionTransport(
        binding,
        _readthrough_candidate(),
        auth_path=path,
        clock=lambda: NOW,
        http=http,
        environ={},
    )
    request = ChatRequest([ChatMessage(Role.USER, "hi")], binding, stream=False)
    list(transport.generate(request))
    assert "reasoning_effort" not in http.sent[0].body


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def _doctor_manager(tmp_path, *profiles, environ=None, **kwargs):
    return AuthManager(
        _store(tmp_path, *profiles),
        environ={} if environ is None else environ,
        **kwargs,
    )


def test_doctor_reports_the_grok_subscription_lane_from_the_injected_probe(
    tmp_path,
):
    from tinyic.auth.doctor import run_doctor

    report = run_doctor(
        manager=_doctor_manager(tmp_path),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        grok_probe=lambda: "ok",
        runtime_locator=lambda _command: None,
    )
    row = next(
        probe
        for probe in report.probes
        if (probe.provider, probe.lane) == ("grok", "subscription")
    )
    assert row.reason_code == "ok"
    assert row.message == "Grok subscription sign-in is available."
    assert row.required is False


def test_doctor_surfaces_subscription_inactive_as_a_known_reason(tmp_path):
    from tinyic.auth.doctor import run_doctor

    report = run_doctor(
        manager=_doctor_manager(tmp_path),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        grok_probe=lambda: "subscription_inactive",
        runtime_locator=lambda _command: None,
    )
    row = next(
        probe
        for probe in report.probes
        if (probe.provider, probe.lane) == ("grok", "subscription")
    )
    assert row.reason_code == "subscription_inactive"
    assert row.status.value == "warning"


def test_doctor_guard_off_reports_policy_disabled_without_probing(tmp_path):
    from tinyic.auth.doctor import run_doctor

    def forbidden_probe():
        raise AssertionError("policy-disabled doctor probed the grok lane")

    report = run_doctor(
        manager=_doctor_manager(tmp_path, grok_policy_guard=False),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        grok_probe=forbidden_probe,
        runtime_locator=lambda _command: None,
    )
    row = next(
        probe
        for probe in report.probes
        if (probe.provider, probe.lane) == ("grok", "subscription")
    )
    assert row.reason_code == "policy_disabled"
    assert row.message == (
        "The Grok subscription lane is disabled by policy_guard."
    )


def test_doctor_probes_a_stored_grok_oauth_profile_not_the_cli_file(tmp_path):
    from tinyic.auth.doctor import run_doctor

    def forbidden_probe():
        raise AssertionError("stored-profile doctor read the CLI file probe")

    report = run_doctor(
        manager=_doctor_manager(tmp_path, _oauth_profile()),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        grok_probe=forbidden_probe,
        runtime_locator=lambda _command: None,
    )
    row = next(
        probe
        for probe in report.probes
        if (probe.provider, probe.lane) == ("grok", "subscription")
    )
    assert row.reason_code == "ok"
    assert row.auth_profile == "grok:supergrok"
    assert "stored-access" not in report.to_json()


# --------------------------------------------------------------------------
# device-code state machine (pure logic, no network)
# --------------------------------------------------------------------------


class ScriptedOAuth:
    """Scripted http_post seam recording every form it receives."""

    def __init__(self, start_payload, poll_payloads) -> None:
        self.start_payload = start_payload
        self.poll_payloads = list(poll_payloads)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, form):
        self.calls.append((url, dict(form)))
        if form.get("grant_type") == (
            "urn:ietf:params:oauth:grant-type:device_code"
        ):
            return self.poll_payloads.pop(0)
        return self.start_payload


_START = {
    "device_code": "dev-code",
    "user_code": "ABCD-1234",
    "verification_uri": "https://auth.x.ai/activate",
    "interval": 5,
    "expires_in": 900,
}


def test_device_flow_walks_pending_slow_down_then_success():
    oauth = ScriptedOAuth(
        _START,
        [
            {"error": "authorization_pending"},
            {"error": "slow_down"},
            {
                "access_token": "device-access",
                "refresh_token": "device-refresh",
                "expires_in": 3600,
            },
        ],
    )
    flow = GrokDeviceCodeFlow(http_post=oauth, clock=lambda: NOW)
    challenge = flow.start()
    assert flow.state == "pending"
    assert challenge.user_code == "ABCD-1234"
    assert challenge.verification_url == "https://auth.x.ai/activate"
    assert "dev-code" not in repr(challenge)

    assert flow.poll() is None
    assert flow.interval == 5.0
    assert flow.poll() is None
    assert flow.interval == 10.0  # slow_down widened the interval
    tokens = flow.poll()
    assert flow.state == "complete"
    assert tokens.access_token == "device-access"
    assert tokens.refresh_token == "device-refresh"
    assert tokens.expires_at == pytest.approx(NOW + 3600)

    start_form = oauth.calls[0][1]
    assert start_form["client_id"]
    assert start_form["code_challenge_method"] == "S256"
    poll_form = oauth.calls[1][1]
    assert poll_form["device_code"] == "dev-code"
    assert poll_form["code_verifier"]


def test_device_flow_terminal_denied_and_expired_states():
    denied = GrokDeviceCodeFlow(
        http_post=ScriptedOAuth(_START, [{"error": "access_denied"}]),
        clock=lambda: NOW,
    )
    denied.start()
    with pytest.raises(GrokTokenError) as excinfo:
        denied.poll()
    assert denied.state == "denied"
    assert excinfo.value.reason is GrokAuthReason.INVALID_CREDENTIAL

    expired = GrokDeviceCodeFlow(
        http_post=ScriptedOAuth(_START, [{"error": "expired_token"}]),
        clock=lambda: NOW,
    )
    expired.start()
    with pytest.raises(GrokTokenError):
        expired.poll()
    assert expired.state == "expired"

    clock_values = iter([NOW, NOW + 901])
    timed_out = GrokDeviceCodeFlow(
        http_post=ScriptedOAuth(_START, []),
        clock=lambda: next(clock_values),
    )
    timed_out.start()
    with pytest.raises(GrokTokenError) as excinfo:
        timed_out.poll()  # local clock passed expires_in: no network poll
    assert timed_out.state == "expired"
    assert excinfo.value.reason is GrokAuthReason.EXPIRED


def test_device_login_session_yields_a_persistable_profile_secret():
    oauth = ScriptedOAuth(
        _START,
        [
            {"error": "authorization_pending"},
            {"access_token": "sess-access", "refresh_token": "sess-refresh"},
        ],
    )
    slept: list[float] = []
    with GrokDeviceLoginSession(
        flow_factory=lambda: GrokDeviceCodeFlow(
            http_post=oauth, clock=lambda: NOW
        ),
        sleep=slept.append,
    ) as session:
        challenge = session.start(None)
        result = session.wait(challenge)
    assert result.ok
    assert slept == [5.0, 5.0]
    tokens = tokens_from_profile_secret(result.profile_secret)
    assert tokens.access_token == "sess-access"
    assert "sess-access" not in repr(result)

    # The secret round-trips into a valid GROK_OAUTH profile.
    profile = AuthProfile(
        "grok:supergrok",
        ProfileKind.GROK_OAUTH,
        AuthLane.SUBSCRIPTION,
        result.profile_secret,
    )
    assert probe_grok_profile_secret(profile.secret, now=NOW).ok


def test_device_login_session_reports_denial_as_a_reason_code():
    oauth = ScriptedOAuth(_START, [{"error": "access_denied"}])
    with GrokDeviceLoginSession(
        flow_factory=lambda: GrokDeviceCodeFlow(
            http_post=oauth, clock=lambda: NOW
        ),
        sleep=lambda _s: None,
    ) as session:
        challenge = session.start(None)
        result = session.wait(challenge)
    assert not result.ok
    assert result.reason is GrokAuthReason.INVALID_CREDENTIAL
    assert result.profile_secret is None


# --------------------------------------------------------------------------
# the onboarding wizard's grok subscription branch
# --------------------------------------------------------------------------


def _grok_report(*, subscription_ok: bool = False):
    from tinyic.auth.doctor import ProbeResult, ProbeStatus, build_report

    def _probe(lane, status, reason):
        return ProbeResult(
            provider="grok",
            lane=lane,
            auth_profile=None,
            model_ref=None,
            required=False,
            status=status,
            reason_code=reason,
            message="probe detail",
        )

    return build_report(
        [
            _probe("api_key", ProbeStatus.WARNING, "missing_credential"),
            _probe(
                "subscription",
                ProbeStatus.OK if subscription_ok else ProbeStatus.WARNING,
                "ok" if subscription_ok else "missing_credential",
            ),
        ],
        preset="default",
    )


def _wizard(
    tmp_path,
    *,
    report=None,
    grok_login_factory=None,
    browser_opener=None,
    **manager_kwargs,
):
    from tinyic.tui.onboard import OnboardController

    manager = AuthManager(_store(tmp_path), environ={}, **manager_kwargs)
    controller = OnboardController(
        manager=manager,
        report_factory=lambda: report if report is not None else _grok_report(),
        verify_probe=lambda _binding, _candidate: "ok",
        grok_login_factory=grok_login_factory,
        browser_opener=browser_opener,
    )
    controller.start()
    return controller


def _choice_ids(controller):
    return [choice.id for choice in controller.choices()]


def _select(controller, choice_id):
    controller.select_index(_choice_ids(controller).index(choice_id))


def test_wizard_offers_the_grok_subscription_branch(tmp_path):
    controller = _wizard(tmp_path, report=_grok_report(subscription_ok=True))
    controller.activate()  # DETECT -> CHOOSE (grok is the only plan)
    assert _choice_ids(controller) == ["subscription", "api_key", "skip"]
    subscription = controller.choices()[0]
    assert subscription.enabled
    assert "policy" in (subscription.note or "").casefold()

    _select(controller, "subscription")
    controller.activate()  # CHOOSE subscription -> CONNECT_SUB menu
    assert _choice_ids(controller) == ["reuse", "device", "back"]


def test_wizard_guard_off_disables_the_grok_subscription_choice(tmp_path):
    controller = _wizard(tmp_path, grok_policy_guard=False)
    controller.activate()
    subscription = controller.choices()[0]
    assert subscription.id == "subscription" and not subscription.enabled
    assert "auth.grok.policy_guard" in (subscription.disabled_reason or "")

    kind, payload = controller.activate()
    assert (kind, payload) == ("navigate", None)
    assert controller.current_plan().error == "policy_disabled"


def test_wizard_reuse_persists_a_grok_readthrough_marker(tmp_path):
    controller = _wizard(tmp_path, report=_grok_report(subscription_ok=True))
    controller.activate()  # -> CHOOSE
    _select(controller, "subscription")
    controller.activate()  # -> CONNECT_SUB menu
    kind, verify = controller.activate()  # highlighted: "reuse"
    assert kind == "verify"
    assert verify() is True

    stored = controller.store.get("grok:grok-cli")
    assert stored is not None
    assert stored.kind is ProfileKind.GROK_READTHROUGH
    assert stored.secret is None
    assert controller.store.get_auth_order("grok") == ("grok:grok-cli",)


def test_wizard_device_login_persists_tinyic_owned_grok_tokens(tmp_path):
    secret = tokens_to_profile_secret(
        GrokTokens("wizard-access", "wizard-refresh", NOW + 3600)
    )

    class FakeGrokSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def start(self, _mode):
            from tinyic.auth.grok import GrokDeviceChallenge

            return GrokDeviceChallenge(
                verification_url="https://auth.x.ai/activate",
                user_code="ABCD-1234",
                interval=5.0,
                expires_at=None,
            )

        def wait(self, _challenge):
            from tinyic.auth.grok import GrokAuthReason, GrokLoginResult

            return GrokLoginResult(GrokAuthReason.OK, secret)

    controller = _wizard(tmp_path, grok_login_factory=FakeGrokSession)
    controller.activate()  # -> CHOOSE
    controller.activate()  # -> CONNECT_SUB menu (device only: not detected)
    assert _choice_ids(controller) == ["device", "back"]
    kind, _payload = controller.activate()
    assert kind == "device"
    assert controller.sub_stage == "device_wait"
    assert controller.challenge.user_code == "ABCD-1234"

    assert controller.finish_subscription_login() is True
    stored = controller.store.get("grok:supergrok")
    assert stored is not None
    assert stored.kind is ProfileKind.GROK_OAUTH
    assert stored.secret == secret
    assert controller.store.get_auth_order("grok") == ("grok:supergrok",)
    plan = controller.plans[0]
    assert plan.outcome == "verified" and plan.persisted_lane == "subscription"


def test_wizard_device_denial_surfaces_the_reason_without_persisting(tmp_path):
    class DeniedGrokSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def start(self, _mode):
            from tinyic.auth.grok import GrokDeviceChallenge

            return GrokDeviceChallenge(
                verification_url="https://auth.x.ai/activate",
                user_code="ABCD-1234",
                interval=5.0,
                expires_at=None,
            )

        def wait(self, _challenge):
            from tinyic.auth.grok import GrokAuthReason, GrokLoginResult

            return GrokLoginResult(GrokAuthReason.INVALID_CREDENTIAL)

    controller = _wizard(tmp_path, grok_login_factory=DeniedGrokSession)
    controller.activate()
    controller.activate()
    controller.activate()  # device
    assert controller.finish_subscription_login() is False
    assert controller.plans[0].error == "invalid_credential"
    assert controller.store.get("grok:supergrok") is None


# --------------------------------------------------------------------------
# device endpoint resolution (the /oauth2/device/code regression)
# --------------------------------------------------------------------------


def test_device_authorization_default_matches_the_published_endpoint():
    # auth.x.ai's discovery document names /oauth2/device/code; the old
    # conventional /oauth2/device/authorization guess 404s, which surfaced in
    # the wizard as a bogus runtime_unavailable.
    assert GROK_DEVICE_AUTHORIZATION_URL == "https://auth.x.ai/oauth2/device/code"


def test_device_flow_prefers_discovery_endpoints():
    oauth = ScriptedOAuth(
        _START,
        [{"access_token": "a", "refresh_token": "r", "expires_in": 60}],
    )
    fetched: list[str] = []

    def getter(url):
        fetched.append(url)
        return {
            "device_authorization_endpoint": "https://auth.x.ai/custom/device",
            "token_endpoint": "https://auth.x.ai/custom/token",
        }

    flow = GrokDeviceCodeFlow(http_post=oauth, http_get=getter, clock=lambda: NOW)
    flow.start()
    flow.poll()
    assert fetched == [GROK_DISCOVERY_URL]
    assert oauth.calls[0][0] == "https://auth.x.ai/custom/device"
    assert oauth.calls[1][0] == "https://auth.x.ai/custom/token"


def test_device_flow_keeps_defaults_when_discovery_fails(tmp_path):
    def unreachable(_url):
        raise GrokTokenError(
            GrokAuthReason.NETWORK_UNREACHABLE, "discovery down"
        )

    oauth = ScriptedOAuth(_START, [])
    flow = GrokDeviceCodeFlow(
        http_post=oauth, http_get=unreachable, clock=lambda: NOW
    )
    flow.start()
    assert oauth.calls[0][0] == GROK_DEVICE_AUTHORIZATION_URL


def test_device_flow_ignores_off_issuer_discovery_endpoints():
    oauth = ScriptedOAuth(
        _START,
        [{"access_token": "a", "refresh_token": "r", "expires_in": 60}],
    )
    flow = GrokDeviceCodeFlow(
        http_post=oauth,
        http_get=lambda _url: {
            "device_authorization_endpoint": "https://evil.example/device",
            "token_endpoint": "http://auth.x.ai/oauth2/token",  # not https
        },
        clock=lambda: NOW,
    )
    flow.start()
    flow.poll()
    assert oauth.calls[0][0] == GROK_DEVICE_AUTHORIZATION_URL
    assert oauth.calls[1][0] == GROK_TOKEN_URL


def test_device_flow_without_a_getter_performs_no_discovery():
    oauth = ScriptedOAuth(_START, [])
    flow = GrokDeviceCodeFlow(http_post=oauth, clock=lambda: NOW)
    flow.start()
    assert [url for url, _form in oauth.calls] == [GROK_DEVICE_AUTHORIZATION_URL]


def test_default_post_maps_connection_failure_to_network_unreachable(
    monkeypatch,
):
    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(GrokTokenError) as excinfo:
        grok_mod._default_http_post(GROK_DEVICE_AUTHORIZATION_URL, {"a": "b"})
    assert excinfo.value.reason is GrokAuthReason.NETWORK_UNREACHABLE


def test_default_get_maps_connection_failure_to_network_unreachable(
    monkeypatch,
):
    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(GrokTokenError) as excinfo:
        grok_mod._default_http_get(GROK_DISCOVERY_URL)
    assert excinfo.value.reason is GrokAuthReason.NETWORK_UNREACHABLE


def test_production_login_session_wires_discovery_into_the_flow():
    session = GrokDeviceLoginSession()
    with session:
        assert session._flow is not None
        assert session._flow._http_get is grok_mod._default_http_get


# --------------------------------------------------------------------------
# wizard reason mapping for device-lane failures
# --------------------------------------------------------------------------


class _StartFailingGrokSession:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def start(self, _mode):
        raise self._error


def test_wizard_maps_grok_start_network_failure_to_network_unreachable(
    tmp_path,
):
    from tinyic.tui.onboard import REASON_HINTS

    controller = _wizard(
        tmp_path,
        grok_login_factory=lambda: _StartFailingGrokSession(
            OSError("connection refused")
        ),
    )
    controller.activate()  # -> CHOOSE
    controller.activate()  # -> CONNECT_SUB menu
    kind, payload = controller.activate()  # device
    assert (kind, payload) == ("navigate", None)
    assert controller.plans[0].error == "network_unreachable"
    assert "network_unreachable" in REASON_HINTS


def test_wizard_passes_reason_coded_start_failures_through(tmp_path):
    controller = _wizard(
        tmp_path,
        grok_login_factory=lambda: _StartFailingGrokSession(
            GrokTokenError(
                GrokAuthReason.NETWORK_UNREACHABLE,
                "Grok OAuth endpoint is unreachable",
            )
        ),
    )
    controller.activate()
    controller.activate()
    controller.activate()  # device
    assert controller.plans[0].error == "network_unreachable"


def test_wizard_maps_grok_wait_network_failure_to_network_unreachable(
    tmp_path,
):
    class WaitFailingGrokSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def start(self, _mode):
            from tinyic.auth.grok import GrokDeviceChallenge

            return GrokDeviceChallenge(
                verification_url="https://auth.x.ai/activate",
                user_code="ABCD-1234",
                interval=5.0,
                expires_at=None,
            )

        def wait(self, _challenge):
            raise OSError("connection reset")

    controller = _wizard(
        tmp_path, grok_login_factory=WaitFailingGrokSession
    )
    controller.activate()
    controller.activate()
    controller.activate()  # device
    assert controller.finish_subscription_login() is False
    assert controller.plans[0].error == "network_unreachable"
    assert controller.store.get("grok:supergrok") is None


def test_device_wait_screen_names_the_provider_being_signed_into(tmp_path):
    # The device-wait screen was hardcoded "Sign in with ChatGPT", which on
    # the Grok lane made a healthy xAI device code look like a wrong-account
    # flow.
    from tinyic.tui.onboard import OnboardApp, device_heading

    class PendingGrokSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def start(self, _mode):
            from tinyic.auth.grok import GrokDeviceChallenge

            return GrokDeviceChallenge(
                verification_url=(
                    "https://accounts.x.ai/oauth2/device?user_code=GMWD-JJWS"
                ),
                user_code="GMWD-JJWS",
                interval=5.0,
                expires_at=None,
            )

    controller = _wizard(tmp_path, grok_login_factory=PendingGrokSession)
    controller.activate()
    controller.activate()
    controller.activate()  # device -> device_wait
    assert controller.sub_stage == "device_wait"
    body = OnboardApp(controller, threaded_verify=False).render_body().plain
    assert "Sign in with Grok" in body
    assert "GMWD-JJWS" in body
    assert "ChatGPT" not in body
    # The OpenAI device flow really does sign into ChatGPT — unchanged.
    assert device_heading("openai") == "Sign in with ChatGPT"


class _PendingDeviceGrokSession:
    """start() yields a pending device challenge; the test never wait()s."""

    VERIFICATION_URL = "https://accounts.x.ai/oauth2/device?user_code=GMWD-JJWS"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def start(self, _mode):
        from tinyic.auth.grok import GrokDeviceChallenge

        return GrokDeviceChallenge(
            verification_url=self.VERIFICATION_URL,
            user_code="GMWD-JJWS",
            interval=5.0,
            expires_at=None,
        )


def _recording_opener(opened):
    def _open(url):
        opened.append(url)
        return True

    return _open


def test_device_login_auto_opens_the_verification_url_once(tmp_path):
    # Copy/paste out of a TUI is painful, so entering device_wait launches the
    # browser at the verification URL (which pre-fills the code) exactly once.
    from tinyic.tui.onboard import OnboardApp

    opened: list[str] = []
    controller = _wizard(
        tmp_path,
        grok_login_factory=_PendingDeviceGrokSession,
        browser_opener=_recording_opener(opened),
    )
    controller.activate()
    controller.activate()
    controller.activate()  # device -> device_wait (+ auto-open)
    assert controller.sub_stage == "device_wait"
    assert opened == [_PendingDeviceGrokSession.VERIFICATION_URL]

    # Re-rendering the device screen must not re-open the browser.
    app = OnboardApp(controller, threaded_verify=False)
    body = app.render_body().plain
    app.render_body()
    assert opened == [_PendingDeviceGrokSession.VERIFICATION_URL]
    # The screen and the status bar both advertise the manual re-open key.
    assert "press o to reopen" in body
    assert "o open browser" in app._render_status().plain


def test_open_verification_url_reopens_only_while_pending(tmp_path):
    opened: list[str] = []
    controller = _wizard(
        tmp_path,
        grok_login_factory=_PendingDeviceGrokSession,
        browser_opener=_recording_opener(opened),
    )
    # No pending challenge yet: nothing to open.
    assert controller.open_verification_url() is False
    assert opened == []

    controller.activate()
    controller.activate()
    controller.activate()  # device_wait (auto-open #1)
    assert controller.open_verification_url() is True  # the `o` binding
    assert opened == [_PendingDeviceGrokSession.VERIFICATION_URL] * 2

    # Cancelling the login clears the challenge; `o` becomes a no-op again.
    assert controller.back() is True
    assert controller.open_verification_url() is False
    assert opened == [_PendingDeviceGrokSession.VERIFICATION_URL] * 2


def test_non_https_verification_url_is_never_auto_opened(tmp_path):
    class _HttpChallengeSession(_PendingDeviceGrokSession):
        VERIFICATION_URL = "http://accounts.x.ai/oauth2/device"

    opened: list[str] = []
    controller = _wizard(
        tmp_path,
        grok_login_factory=_HttpChallengeSession,
        browser_opener=_recording_opener(opened),
    )
    controller.activate()
    controller.activate()
    controller.activate()
    assert controller.sub_stage == "device_wait"  # sign-in itself still works
    assert opened == []


def test_browser_opener_failure_never_breaks_the_device_screen(tmp_path):
    from tinyic.tui.onboard import OnboardApp

    def _exploding_opener(_url):
        raise RuntimeError("no browser on this box")

    controller = _wizard(
        tmp_path,
        grok_login_factory=_PendingDeviceGrokSession,
        browser_opener=_exploding_opener,
    )
    controller.activate()
    controller.activate()
    controller.activate()
    assert controller.sub_stage == "device_wait"
    body = OnboardApp(controller, threaded_verify=False).render_body().plain
    assert "GMWD-JJWS" in body  # manual URL + code remain the fallback


def test_default_browser_opener_routes_through_webbrowser(no_real_browser):
    # The conftest guard replaces webbrowser.open with a recorder, which both
    # proves the default opener's wiring and keeps the offline suite from ever
    # popping a real browser tab.
    from tinyic.tui.onboard import _default_browser_opener

    assert _default_browser_opener("https://example.invalid/device") is True
    assert no_real_browser == ["https://example.invalid/device"]


def test_default_wizard_auto_open_is_absorbed_by_the_suite_guard(
    tmp_path, no_real_browser
):
    # A controller built WITHOUT an injected opener (production default) must
    # hit the webbrowser seam — and nothing else — when it enters device_wait.
    controller = _wizard(tmp_path, grok_login_factory=_PendingDeviceGrokSession)
    controller.activate()
    controller.activate()
    controller.activate()
    assert controller.sub_stage == "device_wait"
    assert no_real_browser == [_PendingDeviceGrokSession.VERIFICATION_URL]
