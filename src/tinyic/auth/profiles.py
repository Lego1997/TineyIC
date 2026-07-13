"""Named auth profiles with keyring-first, secret-safe persistence (FR-2.1).

The profile store persists one schema-v1 document, either as a single OS
keyring item or (only when the keyring cannot be used) as
``~/.tinyic/credentials.json`` with mode ``0600``::

    {
      "version": 1,
      "profiles": {
        "openai:work": {
          "kind": "api_key",
          "lane": "api_key",
          "secret": "...",
          "expires_at": null
        }
      },
      "auth_order": {"openai": ["openai:work"]}
    }

``AuthProfile.public_dict()`` is the only public serialization and deliberately
omits secret material.  Storage serialization stays private to this module.
A working keyring that returns a missing item is authoritative: that condition
does not cause a plaintext-file fallback.  Once selected, a keyring write
failure is reported safely rather than silently creating a second credential
source.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol


SCHEMA_VERSION = 1
KEYRING_SERVICE = "tinyic.auth"
KEYRING_USERNAME = "profiles-v1"
_COMPONENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ProfileStoreError(RuntimeError):
    """A safe, secret-free profile persistence error."""


class AuthLane(StrEnum):
    """Billing/authentication lane used by a profile."""

    API_KEY = "api_key"
    SUBSCRIPTION = "subscription"


class ProfileKind(StrEnum):
    """Credential ownership and runtime mechanism for an auth profile."""

    API_KEY = "api_key"
    OPENAI_OAUTH = "openai_oauth"
    CODEX_READTHROUGH = "codex_readthrough"
    CLAUDE_RUNTIME = "claude_runtime"
    CLAUDE_OAUTH_TOKEN = "claude_oauth_token"


_SECRET_KINDS = frozenset(
    {
        ProfileKind.API_KEY,
        ProfileKind.CLAUDE_OAUTH_TOKEN,
    }
)
_SECRETLESS_KINDS = frozenset(
    {
        # OAuth initiated through TinyIC is still owned and refreshed by the
        # official Codex app-server.  Keeping this route marker secretless
        # prevents a copied refresh token from becoming a competing owner.
        ProfileKind.OPENAI_OAUTH,
        ProfileKind.CODEX_READTHROUGH,
        ProfileKind.CLAUDE_RUNTIME,
    }
)
_PROVIDER_SCOPED_KINDS = {
    ProfileKind.OPENAI_OAUTH: "openai",
    ProfileKind.CODEX_READTHROUGH: "openai",
    ProfileKind.CLAUDE_RUNTIME: "anthropic",
    ProfileKind.CLAUDE_OAUTH_TOKEN: "anthropic",
}


def _normalise_component(value: str, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid {label}")
    canonical = value.strip().lower()
    if not _COMPONENT_RE.fullmatch(canonical):
        raise ValueError(f"invalid {label}")
    return canonical


def _normalise_ref(ref: str) -> tuple[str, str, str]:
    if not isinstance(ref, str):
        raise ValueError("invalid auth profile reference")
    pieces = ref.strip().split(":")
    if len(pieces) != 2:
        raise ValueError("invalid auth profile reference")
    try:
        provider = _normalise_component(pieces[0], label="auth profile reference")
        name = _normalise_component(pieces[1], label="auth profile reference")
    except ValueError:
        raise ValueError("invalid auth profile reference") from None
    return f"{provider}:{name}", provider, name


def _normalise_provider(provider: str) -> str:
    return _normalise_component(provider, label="provider")


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid credential expiry")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid credential expiry") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("credential expiry must be timezone-aware")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, repr=False)
class AuthProfile:
    """An immutable named credential.

    ``ref`` is canonicalized to lowercase ``provider:name`` form.  Secretless
    kinds represent read-through or runtime-owned credentials; TinyIC must not
    copy those external tools' tokens into this object.
    """

    ref: str
    kind: ProfileKind
    lane: AuthLane
    secret: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None
    provider: str = field(init=False)
    name: str = field(init=False)

    def __post_init__(self) -> None:
        canonical, provider, name = _normalise_ref(self.ref)
        try:
            kind = ProfileKind(self.kind)
        except (TypeError, ValueError):
            raise ValueError("invalid auth profile kind") from None
        try:
            lane = AuthLane(self.lane)
        except (TypeError, ValueError):
            raise ValueError("invalid auth lane") from None

        expected_provider = _PROVIDER_SCOPED_KINDS.get(kind)
        if expected_provider is not None and provider != expected_provider:
            raise ValueError(
                f"auth profile kind {kind.value!r} is incompatible with provider"
            )

        secret = self.secret
        if kind in _SECRET_KINDS and (
            not isinstance(secret, str) or not secret.strip()
        ):
            raise ValueError(f"auth profile kind {kind.value!r} requires a secret")
        if kind in _SECRETLESS_KINDS and secret is not None:
            raise ValueError(
                f"auth profile kind {kind.value!r} must use external read-through"
            )
        if kind is ProfileKind.API_KEY and lane is not AuthLane.API_KEY:
            raise ValueError("API-key profiles must use the api_key lane")
        if kind is not ProfileKind.API_KEY and lane is not AuthLane.SUBSCRIPTION:
            raise ValueError("subscription profiles must use the subscription lane")

        expiry = self.expires_at
        if expiry is not None:
            if not isinstance(expiry, datetime):
                raise ValueError("credential expiry must be a datetime")
            if expiry.tzinfo is None or expiry.utcoffset() is None:
                raise ValueError("credential expiry must be timezone-aware")
            expiry = expiry.astimezone(UTC)

        object.__setattr__(self, "ref", canonical)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "lane", lane)
        object.__setattr__(self, "expires_at", expiry)

    def __repr__(self) -> str:
        expiry = _format_datetime(self.expires_at)
        return (
            "AuthProfile("
            f"ref={self.ref!r}, kind={self.kind.value!r}, lane={self.lane.value!r}, "
            f"expires_at={expiry!r}, secret={'<redacted>' if self.secret else None!r}"
            ")"
        )

    def public_dict(self) -> dict[str, object]:
        """Return secret-free profile metadata suitable for UI/doctor output."""
        return {
            "ref": self.ref,
            "provider": self.provider,
            "name": self.name,
            "kind": self.kind.value,
            "lane": self.lane.value,
            "expires_at": _format_datetime(self.expires_at),
            "has_secret": self.secret is not None,
        }

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Return whether the profile is expired at ``now`` (UTC by default)."""
        if self.expires_at is None:
            return False
        current = datetime.now(UTC) if now is None else now
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("expiration clock must be timezone-aware")
        return self.expires_at <= current.astimezone(UTC)

    def _storage_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "lane": self.lane.value,
            "secret": self.secret,
            "expires_at": _format_datetime(self.expires_at),
        }

    @classmethod
    def _from_storage(cls, ref: str, value: object) -> AuthProfile:
        if not isinstance(value, Mapping):
            raise ValueError("invalid stored auth profile")
        required = {"kind", "lane", "secret", "expires_at"}
        if set(value) != required:
            raise ValueError("invalid stored auth profile")
        secret = value["secret"]
        if secret is not None and not isinstance(secret, str):
            raise ValueError("invalid stored auth profile")
        return cls(
            ref=ref,
            kind=ProfileKind(value["kind"]),
            lane=AuthLane(value["lane"]),
            secret=secret,
            expires_at=_parse_datetime(value["expires_at"]),
        )


class KeyringBackend(Protocol):
    """Subset of the ``keyring`` module/backend API used by the store."""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...


def _system_keyring() -> KeyringBackend:
    try:
        import keyring
    except ImportError:
        return _UnavailableKeyring()
    try:
        backend = keyring.get_keyring()
    except Exception:
        return _UnavailableKeyring()
    return backend if _keyring_is_usable(backend) else _UnavailableKeyring()


def _keyring_is_usable(backend: KeyringBackend) -> bool:
    """Reject keyring's null/fail backends while accepting injected fakes."""

    try:
        priority = getattr(backend, "priority", None)
    except Exception:
        return False
    if priority is None:
        # Test/custom backends implementing only the documented protocol have
        # no keyring-specific priority; capability is established by calls.
        return True
    try:
        return float(priority) > 0
    except (TypeError, ValueError):
        return False


class _UnavailableKeyring:
    def get_password(self, service: str, username: str) -> str | None:
        raise RuntimeError("keyring unavailable")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("keyring unavailable")


def _empty_document() -> dict[str, object]:
    return {"version": SCHEMA_VERSION, "profiles": {}, "auth_order": {}}


class ProfileStore:
    """Keyring-first persistence and ordered lookup for named auth profiles."""

    def __init__(
        self,
        *,
        keyring_backend: KeyringBackend | None = None,
        path: str | os.PathLike[str] | None = None,
    ) -> None:
        if keyring_backend is None:
            self._keyring = _system_keyring()
        elif _keyring_is_usable(keyring_backend):
            self._keyring = keyring_backend
        else:
            self._keyring = _UnavailableKeyring()
        self.path = (
            Path.home() / ".tinyic" / "credentials.json"
            if path is None
            else Path(path).expanduser()
        )
        self._mode: str | None = None
        self._lock = threading.RLock()

    def get(self, ref: str) -> AuthProfile | None:
        canonical, _, _ = _normalise_ref(ref)
        with self._lock:
            profiles, _ = self._decoded_document(self._load_document())
            return profiles.get(canonical)

    def put(self, profile: AuthProfile) -> AuthProfile:
        if not isinstance(profile, AuthProfile):
            raise TypeError("profile must be an AuthProfile")
        with self._lock:
            document = self._load_document()
            profiles, auth_order = self._decoded_document(document)
            profiles[profile.ref] = profile
            self._write_document(self._encoded_document(profiles, auth_order))
        return profile

    def delete(self, ref: str) -> bool:
        canonical, _, _ = _normalise_ref(ref)
        with self._lock:
            document = self._load_document()
            profiles, auth_order = self._decoded_document(document)
            if canonical not in profiles:
                return False
            del profiles[canonical]
            for provider, order in tuple(auth_order.items()):
                remaining = tuple(item for item in order if item != canonical)
                if remaining:
                    auth_order[provider] = remaining
                else:
                    auth_order.pop(provider, None)
            self._write_document(self._encoded_document(profiles, auth_order))
            return True

    def list(self, *, provider: str | None = None) -> tuple[AuthProfile, ...]:
        canonical_provider = (
            None if provider is None else _normalise_provider(provider)
        )
        with self._lock:
            profiles, _ = self._decoded_document(self._load_document())
        return tuple(
            profile
            for _, profile in sorted(profiles.items())
            if canonical_provider is None or profile.provider == canonical_provider
        )

    def set_auth_order(
        self, provider: str, refs: Iterable[str]
    ) -> tuple[str, ...]:
        canonical_provider = _normalise_provider(provider)
        with self._lock:
            document = self._load_document()
            profiles, auth_order = self._decoded_document(document)
            order: list[str] = []
            seen: set[str] = set()
            for ref in refs:
                canonical, ref_provider, _ = _normalise_ref(ref)
                if ref_provider != canonical_provider:
                    raise ValueError(
                        f"auth profile {canonical!r} belongs to provider "
                        f"{ref_provider!r}, not {canonical_provider!r}"
                    )
                if canonical not in profiles:
                    raise ValueError(f"unknown auth profile {canonical!r}")
                if canonical not in seen:
                    order.append(canonical)
                    seen.add(canonical)
            if order:
                auth_order[canonical_provider] = tuple(order)
            else:
                auth_order.pop(canonical_provider, None)
            self._write_document(self._encoded_document(profiles, auth_order))
            return tuple(order)

    def get_auth_order(self, provider: str) -> tuple[str, ...]:
        canonical_provider = _normalise_provider(provider)
        with self._lock:
            _, auth_order = self._decoded_document(self._load_document())
        return auth_order.get(canonical_provider, ())

    def resolve(
        self, provider: str, explicit_ref: str | None = None
    ) -> tuple[AuthProfile, ...]:
        """Resolve an explicit profile, then remaining configured fallbacks.

        Expiry and usage-limit usability are intentionally not filtered here;
        the auth manager classifies those states and advances through this
        stable order without mutating it or changing the selected model.
        """
        canonical_provider = _normalise_provider(provider)
        with self._lock:
            profiles, auth_order = self._decoded_document(self._load_document())
        refs: list[str] = []
        if explicit_ref is not None:
            canonical, ref_provider, _ = _normalise_ref(explicit_ref)
            if ref_provider != canonical_provider:
                raise ValueError(
                    f"auth profile {canonical!r} belongs to provider "
                    f"{ref_provider!r}, not {canonical_provider!r}"
                )
            if canonical not in profiles:
                raise ValueError(f"unknown auth profile {canonical!r}")
            refs.append(canonical)
        refs.extend(
            ref
            for ref in auth_order.get(canonical_provider, ())
            if ref not in refs
        )
        return tuple(profiles[ref] for ref in refs)

    def _load_document(self) -> dict[str, object]:
        if self._mode is None:
            try:
                payload = self._keyring.get_password(
                    KEYRING_SERVICE, KEYRING_USERNAME
                )
            except Exception:
                self._mode = "file"
            else:
                self._mode = "keyring"
                return self._parse_document(payload)

        if self._mode == "keyring":
            try:
                payload = self._keyring.get_password(
                    KEYRING_SERVICE, KEYRING_USERNAME
                )
            except Exception:
                raise ProfileStoreError("OS keyring is unavailable") from None
            return self._parse_document(payload)
        return self._read_file()

    def _write_document(self, document: dict[str, object]) -> None:
        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if self._mode == "keyring":
            try:
                self._keyring.set_password(
                    KEYRING_SERVICE, KEYRING_USERNAME, payload
                )
            except Exception:
                raise ProfileStoreError("could not update OS keyring") from None
            return
        self._write_file(payload)

    @staticmethod
    def _parse_document(payload: str | None) -> dict[str, object]:
        if payload is None:
            return _empty_document()
        try:
            parsed = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            raise ProfileStoreError("credential store is invalid") from None
        if not isinstance(parsed, dict):
            raise ProfileStoreError("credential store is invalid")
        return parsed

    @staticmethod
    def _decoded_document(
        document: Mapping[str, object],
    ) -> tuple[dict[str, AuthProfile], dict[str, tuple[str, ...]]]:
        try:
            if set(document) != {"version", "profiles", "auth_order"}:
                raise ValueError
            if document["version"] != SCHEMA_VERSION:
                raise ValueError
            raw_profiles = document["profiles"]
            raw_order = document["auth_order"]
            if not isinstance(raw_profiles, Mapping) or not isinstance(
                raw_order, Mapping
            ):
                raise ValueError

            profiles: dict[str, AuthProfile] = {}
            for raw_ref, value in raw_profiles.items():
                if not isinstance(raw_ref, str):
                    raise ValueError
                profile = AuthProfile._from_storage(raw_ref, value)
                if profile.ref != raw_ref or profile.ref in profiles:
                    raise ValueError
                profiles[profile.ref] = profile

            auth_order: dict[str, tuple[str, ...]] = {}
            for raw_provider, raw_refs in raw_order.items():
                if not isinstance(raw_provider, str) or not isinstance(
                    raw_refs, list
                ):
                    raise ValueError
                provider = _normalise_provider(raw_provider)
                if provider != raw_provider:
                    raise ValueError
                order: list[str] = []
                for raw_ref in raw_refs:
                    if not isinstance(raw_ref, str):
                        raise ValueError
                    ref, ref_provider, _ = _normalise_ref(raw_ref)
                    if (
                        ref != raw_ref
                        or ref_provider != provider
                        or ref not in profiles
                        or ref in order
                    ):
                        raise ValueError
                    order.append(ref)
                if not order:
                    raise ValueError
                auth_order[provider] = tuple(order)
            return profiles, auth_order
        except (KeyError, TypeError, ValueError):
            raise ProfileStoreError("credential store is invalid") from None

    @staticmethod
    def _encoded_document(
        profiles: Mapping[str, AuthProfile],
        auth_order: Mapping[str, tuple[str, ...]],
    ) -> dict[str, object]:
        return {
            "version": SCHEMA_VERSION,
            "profiles": {
                ref: profile._storage_dict()
                for ref, profile in sorted(profiles.items())
            },
            "auth_order": {
                provider: list(refs)
                for provider, refs in sorted(auth_order.items())
            },
        }

    def _read_file(self) -> dict[str, object]:
        try:
            if self.path.parent.is_symlink() or self.path.is_symlink():
                raise ProfileStoreError("credential store path is unsafe")
            payload = self.path.read_text(encoding="utf-8")
            os.chmod(self.path, 0o600)
        except FileNotFoundError:
            return _empty_document()
        except ProfileStoreError:
            raise
        except OSError:
            raise ProfileStoreError("could not read credential store") from None
        return self._parse_document(payload)

    def _write_file(self, payload: str) -> None:
        parent = self.path.parent
        temporary: str | None = None
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if parent.is_symlink() or self.path.is_symlink():
                raise ProfileStoreError("credential store path is unsafe")
            os.chmod(parent, 0o700)
            fd, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=parent
            )
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except ProfileStoreError:
            raise
        except OSError:
            raise ProfileStoreError("could not update credential store") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass


__all__ = [
    "AuthLane",
    "AuthProfile",
    "ProfileKind",
    "ProfileStore",
    "ProfileStoreError",
]
