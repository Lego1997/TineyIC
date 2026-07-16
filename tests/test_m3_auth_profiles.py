"""FR-2.1 acceptance tests for named auth profiles and secure storage."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from keyring.backends.null import Keyring as NullKeyring

from tinyic.auth.profiles import (
    AuthLane,
    AuthProfile,
    ProfileKind,
    ProfileStore,
    ProfileStoreError,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeKeyring:
    """Minimal keyring-like backend with inspectable storage."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.values: dict[tuple[str, str], str] = {}
        self.get_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.get_calls.append((service, username))
        if self.unavailable:
            raise RuntimeError("no usable keyring")
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.set_calls.append((service, username, password))
        if self.unavailable:
            raise RuntimeError("no usable keyring")
        self.values[(service, username)] = password


def profile(
    ref: str,
    secret: str | None = "super-secret-token",
    *,
    kind: ProfileKind = ProfileKind.API_KEY,
    lane: AuthLane = AuthLane.API_KEY,
    expires_at: datetime | None = None,
) -> AuthProfile:
    return AuthProfile(
        ref=ref,
        kind=kind,
        lane=lane,
        secret=secret,
        expires_at=expires_at,
    )


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "openai",
        ":work",
        "openai:",
        "openai:work:extra",
        "open ai:work",
        "openai:work/profile",
    ],
)
def test_profile_rejects_malformed_references_without_echoing_secret(ref: str) -> None:
    secret = "never-print-this-secret"

    with pytest.raises(ValueError) as caught:
        profile(ref, secret)

    assert secret not in str(caught.value)


def test_profile_is_immutable_canonical_and_safe_to_display() -> None:
    secret = "never-print-this-secret"
    expires = datetime(2026, 7, 13, 8, 30, tzinfo=UTC)

    auth = profile("  OpenAI:Work-Key  ", secret, expires_at=expires)

    assert auth.ref == "openai:work-key"
    assert auth.provider == "openai"
    assert auth.name == "work-key"
    assert auth.public_dict() == {
        "ref": "openai:work-key",
        "provider": "openai",
        "name": "work-key",
        "kind": "api_key",
        "lane": "api_key",
        "expires_at": "2026-07-13T08:30:00Z",
        "has_secret": True,
    }
    assert secret not in repr(auth)
    assert secret not in json.dumps(auth.public_dict())
    with pytest.raises((AttributeError, TypeError)):
        auth.ref = "openai:other"  # type: ignore[misc]


def test_profile_validates_kind_secret_and_timezone_without_secret_leaks() -> None:
    secret = "never-print-this-secret"

    with pytest.raises(ValueError, match="requires a secret") as missing:
        profile("openai:key", None)
    with pytest.raises(ValueError, match="timezone-aware") as naive:
        profile(
            "openai:key",
            secret,
            expires_at=datetime(2026, 7, 13, 8, 30),
        )
    runtime = profile(
        "anthropic:claude-code",
        None,
        kind=ProfileKind.CLAUDE_RUNTIME,
        lane=AuthLane.SUBSCRIPTION,
    )

    assert runtime.secret is None
    assert secret not in str(missing.value)
    assert secret not in str(naive.value)


def test_openai_oauth_profile_cannot_store_a_copied_refresh_token() -> None:
    """Codex owns OAuth refresh rotation; TinyIC stores only a route marker."""
    copied = "oauth-access-and-refresh-material"

    with pytest.raises(ValueError, match="external read-through") as caught:
        profile(
            "openai:chatgpt",
            copied,
            kind=ProfileKind.OPENAI_OAUTH,
            lane=AuthLane.SUBSCRIPTION,
        )

    route = profile(
        "openai:chatgpt",
        None,
        kind=ProfileKind.OPENAI_OAUTH,
        lane=AuthLane.SUBSCRIPTION,
    )
    assert route.secret is None
    assert copied not in str(caught.value)


@pytest.mark.parametrize(
    ("ref", "kind", "secret"),
    [
        ("anthropic:codex", ProfileKind.CODEX_READTHROUGH, None),
        ("openai:claude", ProfileKind.CLAUDE_RUNTIME, None),
        (
            "openai:claude-token",
            ProfileKind.CLAUDE_OAUTH_TOKEN,
            "user-minted-token",
        ),
    ],
)
def test_subscription_runtime_kinds_are_provider_scoped(
    ref: str, kind: ProfileKind, secret: str | None
) -> None:
    with pytest.raises(ValueError, match="incompatible with provider"):
        profile(ref, secret, kind=kind, lane=AuthLane.SUBSCRIPTION)


def test_profile_expiration_uses_an_injectable_clock() -> None:
    expiry = datetime(2026, 7, 13, 8, 30, tzinfo=UTC)
    auth = profile("openai:work", expires_at=expiry)

    assert not auth.is_expired(now=expiry - timedelta(microseconds=1))
    assert auth.is_expired(now=expiry)
    assert not profile("openai:no-expiry").is_expired(now=expiry)


def test_keyring_first_roundtrip_update_list_delete_uses_one_schema_item(
    tmp_path: Path,
) -> None:
    backend = FakeKeyring()
    fallback = tmp_path / ".tinyic" / "credentials.json"
    store = ProfileStore(keyring_backend=backend, path=fallback)
    original = profile("openai:work", "first")
    updated = profile("openai:work", "second")
    other = profile("anthropic:team", "third")

    store.put(original)
    assert store.get("OPENAI:WORK") == original
    store.put(updated)
    store.put(other)

    assert store.get("openai:work") == updated
    assert store.list() == (other, updated)
    assert store.list(provider="OPENAI") == (updated,)
    assert not fallback.exists()
    assert len(backend.values) == 1
    stored = json.loads(next(iter(backend.values.values())))
    assert stored["version"] == 1
    assert set(stored) == {"version", "profiles", "auth_order"}
    assert stored["profiles"]["openai:work"]["secret"] == "second"

    assert store.delete("openai:work") is True
    assert store.delete("openai:work") is False
    assert store.get("openai:work") is None


def test_functioning_empty_keyring_does_not_read_or_create_plaintext_fallback(
    tmp_path: Path,
) -> None:
    fallback = tmp_path / ".tinyic" / "credentials.json"
    fallback.parent.mkdir()
    fallback.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "openai:file": {
                        "kind": "api_key",
                        "lane": "api_key",
                        "secret": "plaintext-secret",
                        "expires_at": None,
                    }
                },
                "auth_order": {},
            }
        ),
        encoding="utf-8",
    )
    backend = FakeKeyring()
    store = ProfileStore(keyring_backend=backend, path=fallback)

    assert store.get("openai:file") is None
    store.put(profile("openai:keyring", "keyring-secret"))

    # A working keyring returning "not found" is still authoritative; the
    # existing fallback remains untouched rather than becoming a second source.
    assert "plaintext-secret" in fallback.read_text(encoding="utf-8")
    assert "keyring-secret" not in fallback.read_text(encoding="utf-8")


def test_unavailable_keyring_falls_back_to_atomic_0600_file_despite_umask(
    tmp_path: Path,
) -> None:
    backend = FakeKeyring(unavailable=True)
    fallback = tmp_path / ".tinyic" / "credentials.json"
    store = ProfileStore(keyring_backend=backend, path=fallback)
    previous_umask = os.umask(0)
    try:
        store.put(profile("openai:work"))
    finally:
        os.umask(previous_umask)

    assert store.get("openai:work") == profile("openai:work")
    assert stat.S_IMODE(fallback.stat().st_mode) == 0o600
    assert stat.S_IMODE(fallback.parent.stat().st_mode) == 0o700
    assert not list(fallback.parent.glob(".credentials.json.*.tmp"))


def test_local_tinyic_state_directory_is_gitignored() -> None:
    """A test HOME inside the checkout must not make credentials committable."""
    assert ".tinyic/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_null_keyring_falls_back_to_roundtrip_safe_0600_file(
    tmp_path: Path,
) -> None:
    fallback = tmp_path / ".tinyic" / "credentials.json"
    item = profile("openai:work", "opaque-secret")

    ProfileStore(keyring_backend=NullKeyring(), path=fallback).put(item)
    reopened = ProfileStore(keyring_backend=NullKeyring(), path=fallback)

    assert reopened.get(item.ref) == item
    assert fallback.is_file()
    assert stat.S_IMODE(fallback.stat().st_mode) == 0o600


def test_failed_atomic_replace_preserves_the_previous_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tinyic.auth.profiles as profiles_module

    fallback = tmp_path / ".tinyic" / "credentials.json"
    store = ProfileStore(
        keyring_backend=FakeKeyring(unavailable=True), path=fallback
    )
    store.put(profile("openai:work", "old-secret"))
    before = fallback.read_bytes()

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated replace failure with old-secret")

    monkeypatch.setattr(profiles_module.os, "replace", fail_replace)

    with pytest.raises(ProfileStoreError) as caught:
        store.put(profile("openai:work", "new-secret"))

    assert "old-secret" not in str(caught.value)
    assert "new-secret" not in str(caught.value)
    assert fallback.read_bytes() == before
    assert not list(fallback.parent.glob(".credentials.json.*.tmp"))


def test_keyring_errors_do_not_echo_secrets_or_switch_to_plaintext(
    tmp_path: Path,
) -> None:
    class FailingWriteKeyring(FakeKeyring):
        def set_password(
            self, service: str, username: str, password: str
        ) -> None:
            raise RuntimeError(f"backend rejected {password}")

    fallback = tmp_path / ".tinyic" / "credentials.json"
    store = ProfileStore(keyring_backend=FailingWriteKeyring(), path=fallback)
    secret = "never-print-this-secret"

    with pytest.raises(ProfileStoreError) as caught:
        store.put(profile("openai:work", secret))

    assert secret not in str(caught.value)
    assert not fallback.exists()


def test_auth_order_deduplicates_and_resolves_explicit_profile_first(
    tmp_path: Path,
) -> None:
    store = ProfileStore(keyring_backend=FakeKeyring(), path=tmp_path / "creds")
    subscription = profile(
        "openai:subscription",
        None,
        kind=ProfileKind.OPENAI_OAUTH,
        lane=AuthLane.SUBSCRIPTION,
    )
    overflow = profile("openai:overflow", "api-key")
    explicit = profile("openai:explicit", "other-api-key")
    for item in (subscription, overflow, explicit):
        store.put(item)

    order = store.set_auth_order(
        "OPENAI",
        ["OpenAI:Subscription", "openai:subscription", "openai:overflow"],
    )

    assert order == ("openai:subscription", "openai:overflow")
    assert store.get_auth_order("openai") == order
    assert store.resolve("openai") == (subscription, overflow)
    assert store.resolve("openai", explicit_ref="OPENAI:EXPLICIT") == (
        explicit,
        subscription,
        overflow,
    )
    assert store.resolve("openai", explicit_ref="openai:subscription") == (
        subscription,
        overflow,
    )


def test_auth_order_rejects_malformed_cross_provider_and_unknown_refs(
    tmp_path: Path,
) -> None:
    store = ProfileStore(keyring_backend=FakeKeyring(), path=tmp_path / "creds")
    store.put(profile("openai:work"))
    store.put(profile("anthropic:work"))

    with pytest.raises(ValueError, match="belongs to provider"):
        store.set_auth_order("openai", ["anthropic:work"])
    with pytest.raises(ValueError, match="unknown auth profile"):
        store.set_auth_order("openai", ["openai:missing"])
    with pytest.raises(ValueError, match="invalid auth profile reference"):
        store.set_auth_order("openai", ["not-a-ref"])
    with pytest.raises(ValueError, match="belongs to provider"):
        store.resolve("openai", explicit_ref="anthropic:work")


def test_deleting_a_profile_removes_it_from_auth_order(tmp_path: Path) -> None:
    store = ProfileStore(keyring_backend=FakeKeyring(), path=tmp_path / "creds")
    store.put(profile("openai:first"))
    store.put(profile("openai:second"))
    store.set_auth_order("openai", ["openai:first", "openai:second"])

    store.delete("openai:first")

    assert store.get_auth_order("openai") == ("openai:second",)


def test_malformed_persisted_store_returns_a_safe_error(tmp_path: Path) -> None:
    fallback = tmp_path / ".tinyic" / "credentials.json"
    fallback.parent.mkdir()
    fallback.write_text("not-json secret-value", encoding="utf-8")
    store = ProfileStore(
        keyring_backend=FakeKeyring(unavailable=True), path=fallback
    )

    with pytest.raises(ProfileStoreError) as caught:
        store.list()

    assert str(caught.value) == "credential store is invalid"
    assert "secret-value" not in str(caught.value)


def test_plaintext_fallback_refuses_a_symlinked_credentials_directory(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "attacker-controlled"
    actual.mkdir()
    linked = tmp_path / ".tinyic"
    linked.symlink_to(actual, target_is_directory=True)
    store = ProfileStore(
        keyring_backend=FakeKeyring(unavailable=True),
        path=linked / "credentials.json",
    )

    with pytest.raises(ProfileStoreError, match="path is unsafe"):
        store.list()

    assert not (actual / "credentials.json").exists()
