"""Per-committee auth-profile resolution and usage-limit state (FR-2.1).

``AuthManager`` is both the M3 replacement for the legacy environment-only
``CredentialProvider`` and the owner of one committee's auth rotation state.
It is intentionally an ordinary instance: blocked profiles and rolling usage
counts never live in module globals, so simultaneous debates cannot contaminate
one another.

Candidate order is stable and deliberately simple: a binding's explicit
profile, the provider's remaining ``auth_order`` entries, then a transient
legacy environment-key profile.  Expired and locally usage-limited candidates
are skipped.  Resolution failures expose only stable reason codes and safe
provider/profile metadata; credential values and provider error text never
cross this module's public errors.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from tinyic.models.binding import ModelBinding
from tinyic.models.credentials import CredentialProvider
from tinyic.models.types import AuthError, UsageWindow

from .profiles import AuthLane, AuthProfile, ProfileKind, ProfileStore
from .usage_window import RollingUsageMeter


DEFAULT_PROVIDER_ENV_REFS: Mapping[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}
CLAUDE_CODE_OAUTH_TOKEN_REF = "CLAUDE_CODE_OAUTH_TOKEN"

_SAFE_MESSAGES = {
    "missing_credential": "No usable credential is configured for this provider.",
    "expired": "All configured credentials for this provider are expired.",
    "usage_limited": "All usable credentials are temporarily usage-limited.",
    "lane_incompatible": "The selected auth profile belongs to an incompatible lane.",
    "subscription_required": "This model requires a subscription auth profile.",
    "policy_disabled": "The Anthropic subscription lane is disabled by policy_guard.",
}


class AuthResolutionError(AuthError):
    """A secret-free, reason-coded failure to select an auth candidate."""

    def __init__(
        self,
        reason_code: str,
        *,
        provider: str,
        auth_profile: str | None = None,
    ) -> None:
        if reason_code not in _SAFE_MESSAGES:
            raise ValueError("unknown auth resolution reason")
        super().__init__(_SAFE_MESSAGES[reason_code], provider=provider)
        self.reason_code = reason_code
        self.auth_profile = auth_profile


@dataclass(frozen=True, repr=False)
class AuthCandidate:
    """One resolved profile, callable through the credential-provider seam.

    Existing adapter factories keep their ``(binding, credentials) ->
    Transport`` signature.  A rotating transport passes the chosen candidate
    as ``credentials``; API-key adapters therefore receive exactly the selected
    named key when they ask for their historical environment reference.
    Runtime-owned, secretless subscription profiles naturally return ``None``.
    """

    profile: AuthProfile
    source: Literal["profile", "environment"] = "profile"
    credential_ref: str | None = None

    @property
    def ref(self) -> str:
        return self.profile.ref

    @property
    def provider(self) -> str:
        return self.profile.provider

    @property
    def kind(self) -> ProfileKind:
        return self.profile.kind

    @property
    def lane(self) -> AuthLane:
        return self.profile.lane

    def __call__(self, ref: str) -> str | None:
        if not ref or self.profile.secret is None:
            return None
        # A candidate is already scoped to one bound provider.  Accept both its
        # named ref and the adapter's historical env-var ref without exposing
        # other manager/store credentials to that adapter.
        if ref not in {self.ref, self.credential_ref}:
            return None
        value = self.profile.secret.strip()
        return value or None

    def __repr__(self) -> str:
        return (
            "AuthCandidate("
            f"ref={self.ref!r}, kind={self.kind.value!r}, "
            f"lane={self.lane.value!r}, source={self.source!r}, "
            "credential='<redacted>'"
            ")"
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalise_env_refs(
    values: Mapping[str, str | Sequence[str]] | None,
) -> dict[str, tuple[str, ...]]:
    source = DEFAULT_PROVIDER_ENV_REFS if values is None else values
    normalized: dict[str, tuple[str, ...]] = {}
    for provider, raw_refs in source.items():
        refs = (raw_refs,) if isinstance(raw_refs, str) else tuple(raw_refs)
        cleaned = tuple(
            ref.strip()
            for ref in refs
            if isinstance(ref, str) and ref.strip()
        )
        if cleaned:
            normalized[provider.strip().lower()] = cleaned
    return normalized


class AuthManager:
    """Resolve auth profiles and own one committee's temporary block state."""

    def __init__(
        self,
        store: ProfileStore | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        provider_env_refs: Mapping[str, str | Sequence[str]] | None = None,
        clock: Callable[[], datetime] | None = None,
        meter: RollingUsageMeter | None = None,
        window_estimates: Mapping[str, int] | None = None,
        anthropic_policy_guard: bool = True,
    ) -> None:
        if not isinstance(anthropic_policy_guard, bool):
            raise TypeError("anthropic_policy_guard must be a boolean")
        self.store = store or ProfileStore()
        self._environ = os.environ if environ is None else environ
        self._provider_env_refs = _normalise_env_refs(provider_env_refs)
        self._clock = clock or _utc_now
        self.meter = meter or RollingUsageMeter(clock=self._clock)
        self._window_estimates = dict(window_estimates or {})
        self._anthropic_policy_guard = anthropic_policy_guard
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in self._window_estimates.values()
        ):
            raise ValueError("usage-window estimates must be non-negative integers")
        # ``None`` means blocked for the lifetime of this manager.  A datetime
        # is a retry-after deadline and is pruned lazily by ``is_usage_limited``.
        self._blocked: dict[str, datetime | None] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_config(
        cls,
        config_path: str | os.PathLike[str] | None = None,
        **kwargs,
    ) -> "AuthManager":
        """Build a manager using validated policy switches from ``tinyic.toml``."""

        from tinyic.models.presets import load_config

        config = load_config(config_path)
        if "anthropic_policy_guard" in kwargs:
            raise TypeError(
                "from_config owns anthropic_policy_guard; construct AuthManager "
                "directly for programmatic policy control"
            )
        return cls(
            anthropic_policy_guard=config["auth"]["anthropic"][
                "policy_guard"
            ],
            **kwargs,
        )

    @property
    def anthropic_policy_guard(self) -> bool:
        """Whether official Claude subscription plumbing may be considered."""

        return self._anthropic_policy_guard

    def __call__(self, ref: str) -> str | None:
        """Resolve a named profile ref or a backward-compatible env ref."""
        if not isinstance(ref, str) or not ref:
            return None
        if (
            ref == CLAUDE_CODE_OAUTH_TOKEN_REF
            and not self._anthropic_policy_guard
        ):
            # The guard is a legal boundary, including for callers that use
            # the credential-provider seam directly instead of candidates().
            return None
        if ":" in ref:
            try:
                profile = self.store.get(ref)
            except ValueError:
                profile = None
            if (
                profile is not None
                and profile.provider == "anthropic"
                and profile.lane is AuthLane.SUBSCRIPTION
                and not self._anthropic_policy_guard
            ):
                # The guard is a legal boundary: a stored Anthropic
                # subscription profile must not resolve through the
                # credential-provider seam when policy_guard is off, mirroring
                # candidates()' suppression of that lane (not only the literal
                # CLAUDE_CODE_OAUTH_TOKEN env ref guarded above).
                return None
            if (
                profile is not None
                and not profile.is_expired(now=self._now())
                and not self.is_usage_limited(profile.ref)
                and profile.secret is not None
            ):
                value = profile.secret.strip()
                return value or None
            return None
        value = self._environ.get(ref)
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    def candidates(
        self,
        binding: ModelBinding,
        *,
        subscription_only: bool = False,
    ) -> tuple[AuthCandidate, ...]:
        """Return currently usable candidates in explicit/order/env order."""
        provider = binding.provider.strip().lower()
        profiles: list[AuthProfile] = []
        seen: set[str] = set()
        saw_missing = False
        saw_expired = False
        saw_blocked = False
        saw_incompatible = False
        saw_api_key = False
        saw_policy_disabled = False

        if binding.auth_profile:
            try:
                explicit = self.store.get(binding.auth_profile)
            except ValueError:
                explicit = None
                saw_incompatible = True
            if explicit is None:
                saw_missing = True
            elif explicit.provider != provider:
                saw_incompatible = True
            else:
                profiles.append(explicit)
                seen.add(explicit.ref)

        for ref in self.store.get_auth_order(provider):
            if ref in seen:
                continue
            profile = self.store.get(ref)
            if profile is not None:
                profiles.append(profile)
                seen.add(profile.ref)

        usable: list[AuthCandidate] = []
        now = self._now()
        runtime_owners: set[str] = set()
        for profile in profiles:
            runtime_owner = self._runtime_owner(profile)
            if runtime_owner is not None:
                if runtime_owner in runtime_owners:
                    # Route-marker aliases share one external CLI login. They
                    # are not independent quota fallbacks and must never cause
                    # a replay against the same underlying account.
                    continue
                runtime_owners.add(runtime_owner)
            if (
                provider == "anthropic"
                and profile.lane is AuthLane.SUBSCRIPTION
                and not self._anthropic_policy_guard
            ):
                saw_policy_disabled = True
                continue
            if profile.is_expired(now=now):
                saw_expired = True
                continue
            if self.is_usage_limited(profile.ref):
                saw_blocked = True
                continue
            if subscription_only and profile.lane is not AuthLane.SUBSCRIPTION:
                saw_api_key = True
                continue
            usable.append(
                AuthCandidate(
                    profile,
                    credential_ref=self._profile_credential_ref(profile),
                )
            )

        # ``claude setup-token`` produces a user-owned, inference-only token
        # for headless official-runtime use.  Treat it as a transient
        # subscription profile; never persist or expose the value.
        if provider == "anthropic" and self._anthropic_policy_guard:
            token = self(CLAUDE_CODE_OAUTH_TOKEN_REF)
            transient_ref = "anthropic:claude-oauth-env"
            if token is not None and transient_ref not in seen:
                transient = AuthProfile(
                    transient_ref,
                    ProfileKind.CLAUDE_OAUTH_TOKEN,
                    AuthLane.SUBSCRIPTION,
                    token,
                )
                if self.is_usage_limited(transient.ref):
                    saw_blocked = True
                else:
                    usable.append(
                        AuthCandidate(
                            transient,
                            source="environment",
                            credential_ref=CLAUDE_CODE_OAUTH_TOKEN_REF,
                        )
                    )

        env_ref, env_secret = self._provider_environment_credential(provider)
        if env_ref is not None and env_secret is not None:
            transient_ref = f"{provider}:env-fallback"
            if subscription_only:
                saw_api_key = True
            elif transient_ref not in seen:
                transient = AuthProfile(
                    transient_ref,
                    ProfileKind.API_KEY,
                    AuthLane.API_KEY,
                    env_secret,
                )
                if self.is_usage_limited(transient.ref):
                    saw_blocked = True
                else:
                    usable.append(
                        AuthCandidate(
                            transient,
                            source="environment",
                            credential_ref=env_ref,
                        )
                    )

        if usable:
            return tuple(usable)
        if saw_policy_disabled:
            reason = "policy_disabled"
        elif subscription_only and saw_api_key:
            reason = "subscription_required"
        elif saw_blocked:
            reason = "usage_limited"
        elif saw_expired:
            reason = "expired"
        elif saw_incompatible:
            reason = "lane_incompatible"
        else:
            reason = "missing_credential"
        raise AuthResolutionError(
            reason,
            provider=provider,
            auth_profile=binding.auth_profile,
        )

    def mark_usage_limited(
        self,
        candidate: AuthCandidate | str,
        *,
        retry_after: float | None = None,
    ) -> None:
        """Temporarily block one profile without mutating persisted order."""
        ref = candidate.ref if isinstance(candidate, AuthCandidate) else candidate
        if not isinstance(ref, str) or not ref:
            raise ValueError("auth profile reference is required")
        deadline: datetime | None = None
        if retry_after is not None:
            if isinstance(retry_after, bool) or retry_after < 0:
                raise ValueError("retry_after must be non-negative")
            deadline = self._now() + timedelta(seconds=float(retry_after))
        with self._lock:
            self._blocked[ref] = deadline

    def is_usage_limited(self, candidate: AuthCandidate | str) -> bool:
        """Return whether this manager currently blocks ``candidate``."""
        ref = candidate.ref if isinstance(candidate, AuthCandidate) else candidate
        with self._lock:
            if ref not in self._blocked:
                return False
            deadline = self._blocked[ref]
            if deadline is not None and self._now() >= deadline:
                del self._blocked[ref]
                return False
            return True

    def record_success(self, candidate: AuthCandidate) -> UsageWindow | None:
        """Record one completed subscription message for the local estimate."""
        if candidate.lane is not AuthLane.SUBSCRIPTION:
            return None
        estimate = self._window_estimates.get(candidate.ref, 0)
        return self.meter.record(candidate.ref, estimate=estimate)

    def new_transport(
        self,
        binding: ModelBinding,
        child_factory: Callable[[ModelBinding, CredentialProvider], object],
        *,
        subscription_only: bool = False,
    ):
        """Build a rotating transport while preserving the child-factory seam."""
        from tinyic.models.adapters.rotating import RotatingTransport

        return RotatingTransport(
            binding,
            self.candidates(binding, subscription_only=subscription_only),
            child_factory,
            manager=self,
            subscription_only=subscription_only,
        )

    def _provider_environment_credential(
        self, provider: str
    ) -> tuple[str | None, str | None]:
        for ref in self._provider_env_refs.get(provider, ()):
            value = self(ref)
            if value is not None:
                return ref, value
        return None, None

    def _profile_credential_ref(self, profile: AuthProfile) -> str | None:
        """Return the sole adapter ref allowed to receive this profile secret."""

        if profile.kind is ProfileKind.CLAUDE_OAUTH_TOKEN:
            return CLAUDE_CODE_OAUTH_TOKEN_REF
        if profile.kind is ProfileKind.API_KEY:
            refs = self._provider_env_refs.get(profile.provider, ())
            return refs[0] if refs else None
        return None

    @staticmethod
    def _runtime_owner(profile: AuthProfile) -> str | None:
        if profile.kind in {
            ProfileKind.OPENAI_OAUTH,
            ProfileKind.CODEX_READTHROUGH,
        }:
            return "openai:codex-runtime"
        if profile.kind is ProfileKind.CLAUDE_RUNTIME:
            return "anthropic:claude-runtime"
        return None

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("auth-manager clock must be timezone-aware")
        return now.astimezone(UTC)


__all__ = [
    "AuthCandidate",
    "AuthManager",
    "AuthResolutionError",
    "CLAUDE_CODE_OAUTH_TOKEN_REF",
    "DEFAULT_PROVIDER_ENV_REFS",
]
