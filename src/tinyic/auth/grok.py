"""Grok (xAI) subscription authentication: read-through and device-code OAuth.

Two lanes share this module (mirroring :mod:`tinyic.auth.openai`'s split
between read-through inspection and a wizard-driven login):

* **Read-through** (``ProfileKind.GROK_READTHROUGH``) — the official xAI CLI
  ("grok" / Grok Build) caches its OAuth tokens in ``~/.grok/auth.json``.
  TinyIC *reads* that file to reuse an existing sign-in and NEVER writes,
  refreshes-in-place, or rotates it: a stale access token is refreshed
  **in memory only, per session**, so the CLI's own file stays byte-identical
  (the same ownership discipline as the Codex lane).
* **TinyIC-owned OAuth** (``ProfileKind.GROK_OAUTH``) — the onboarding wizard
  runs an RFC 8628 device-code flow (plus PKCE S256, matching the official
  client's flow) against ``https://auth.x.ai`` using the *public* desktop
  client id.  The browser step belongs to the user; TinyIC prints the URL and
  user code.  The resulting token document is TinyIC's own credential and is
  persisted through the existing keyring-first profile store as the profile
  secret.

Entitlement is enforced server-side: OAuth can succeed while inference returns
a 403 whose body says the account has no active Grok subscription.  That
condition maps to the stable, secret-free reason code ``subscription_inactive``.

Reason codes stay deliberately small because :mod:`tinyic.auth.doctor` and the
onboarding wizard consume this module as a probe seam.  Results carry
provenance, never token values, account identity, or raw endpoint error text.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


GROK_ISSUER = "https://auth.x.ai"
GROK_DISCOVERY_URL = f"{GROK_ISSUER}/.well-known/openid-configuration"
GROK_AUTHORIZE_URL = f"{GROK_ISSUER}/oauth2/authorize"
GROK_TOKEN_URL = f"{GROK_ISSUER}/oauth2/token"
#: Conventional RFC 8628 endpoint; the issuer's discovery document
#: (``GROK_DISCOVERY_URL``) is authoritative and may override this default.
GROK_DEVICE_AUTHORIZATION_URL = f"{GROK_ISSUER}/oauth2/device/authorization"
#: The official CLI's *public* desktop client id — an OAuth public-client
#: identifier, not a secret.
GROK_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
GROK_OAUTH_SCOPE = (
    "openid profile email offline_access grok-cli:access api:access"
)
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

#: ``~/.grok/auth.json`` entry keys used by the official CLI.
GROK_AUTH_FILE_KEY = f"{GROK_ISSUER}::{GROK_CLIENT_ID}"
GROK_AUTH_FILE_LEGACY_KEY = "https://accounts.x.ai/sign-in"

#: A token within this many seconds of expiry is treated as already expired.
TOKEN_EXPIRY_SKEW_SECONDS = 120.0

_TOKEN_FIELDS = ("key", "access_token", "token")
_EXPIRY_FIELDS = ("expires_at", "expiresAt", "expires")

#: Server-side entitlement text markers (matched case-insensitively against
#: the fixed known 403 body; the body itself is never reflected anywhere).
GROK_ENTITLEMENT_MARKERS = (
    "grok subscription",
    "run out of available resources",
)

#: Injectable ``(url, form) -> parsed-JSON-mapping`` POST seam.  OAuth error
#: bodies (``{"error": "authorization_pending"}`` …) must be *returned*, not
#: raised, so the callers can drive the RFC 8628 state machine.
HttpPostForm = Callable[[str, Mapping[str, str]], Mapping[str, Any]]


class GrokAuthReason(str, Enum):
    """Stable reason codes returned by the Grok subscription probes."""

    OK = "ok"
    MISSING_CREDENTIAL = "missing_credential"
    EXPIRED = "expired"
    INVALID_CREDENTIAL = "invalid_credential"
    SUBSCRIPTION_INACTIVE = "subscription_inactive"


@dataclass(frozen=True)
class GrokAuthProbe:
    """Secret-free result of probing a Grok subscription credential source."""

    reason: GrokAuthReason
    source: str
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason is GrokAuthReason.OK


class GrokTokenError(RuntimeError):
    """A reason-coded, secret-free Grok credential failure."""

    def __init__(self, reason: GrokAuthReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, repr=False)
class GrokTokens:
    """One OAuth token set.  ``expires_at`` is epoch seconds (or unknown)."""

    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None

    def __repr__(self) -> str:
        return (
            "GrokTokens(access_token='<redacted>', "
            f"refresh_token={'<redacted>' if self.refresh_token else None!r}, "
            f"expires_at={self.expires_at!r})"
        )

    def is_fresh(
        self, *, now: float, skew: float = TOKEN_EXPIRY_SKEW_SECONDS
    ) -> bool:
        if self.expires_at is None:
            return True
        return (self.expires_at - skew) > now


def default_grok_auth_path(home: Path | None = None) -> Path:
    """The official CLI's credential cache location."""

    base = Path.home() if home is None else Path(home)
    return base.expanduser() / ".grok" / "auth.json"


def _coerce_expiry(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        # Some builds serialize epoch milliseconds.
        return number / 1000 if number > 10_000_000_000 else number
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return _coerce_expiry(float(stripped))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _tokens_from_entry(entry: Any) -> GrokTokens | None:
    """Leniently decode one credential entry (field names vary per build)."""

    if not isinstance(entry, Mapping):
        return None
    access: str | None = None
    for name in _TOKEN_FIELDS:
        value = entry.get(name)
        if isinstance(value, str) and value.strip():
            access = value.strip()
            break
    if access is None:
        return None
    refresh = entry.get("refresh_token")
    refresh = refresh.strip() if isinstance(refresh, str) and refresh.strip() else None
    expiry: float | None = None
    for name in _EXPIRY_FIELDS:
        expiry = _coerce_expiry(entry.get(name))
        if expiry is not None:
            break
    return GrokTokens(access, refresh, expiry)


def parse_grok_auth_document(raw: Any) -> GrokTokens | None:
    """Decode a ``~/.grok/auth.json`` document (current or legacy shape)."""

    if not isinstance(raw, Mapping):
        return None
    for key in (GROK_AUTH_FILE_KEY, GROK_AUTH_FILE_LEGACY_KEY):
        tokens = _tokens_from_entry(raw.get(key))
        if tokens is not None:
            return tokens
    # Some builds flatten the entry to the document root.
    tokens = _tokens_from_entry(raw)
    if tokens is not None:
        return tokens
    # Last resort: the first value that decodes as a credential entry.
    for value in raw.values():
        tokens = _tokens_from_entry(value)
        if tokens is not None:
            return tokens
    return None


def _read_auth_file(path: Path) -> GrokTokens | None:
    """Read-only parse of the CLI's auth file; missing file → ``None``.

    Unreadable or structurally invalid content raises
    :class:`GrokTokenError` (``invalid_credential``) so callers can
    distinguish "not signed in" from "corrupt".
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise GrokTokenError(
            GrokAuthReason.INVALID_CREDENTIAL,
            "Grok CLI credential file is unreadable or invalid",
        ) from None
    tokens = parse_grok_auth_document(raw)
    if tokens is None:
        raise GrokTokenError(
            GrokAuthReason.INVALID_CREDENTIAL,
            "Grok CLI credential file is not a Grok sign-in",
        )
    return tokens


def file_token_loader(
    path: Path | None = None, *, home: Path | None = None
) -> Callable[[], GrokTokens | None]:
    """A :class:`GrokTokenSource` loader over the CLI's auth file (read-only)."""

    resolved = path if path is not None else default_grok_auth_path(home)

    def load() -> GrokTokens | None:
        return _read_auth_file(Path(resolved))

    return load


def tokens_to_profile_secret(tokens: GrokTokens) -> str:
    """Serialize a TinyIC-owned token set for profile-store persistence."""

    return json.dumps(
        {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
        },
        separators=(",", ":"),
    )


def tokens_from_profile_secret(secret: Any) -> GrokTokens | None:
    """Decode a stored ``GROK_OAUTH`` profile secret; ``None`` when invalid."""

    if not isinstance(secret, str) or not secret.strip():
        return None
    try:
        raw = json.loads(secret)
    except (TypeError, ValueError):
        return None
    return _tokens_from_entry(raw)


def profile_token_loader(
    secret_provider: Callable[[], str | None],
) -> Callable[[], GrokTokens | None]:
    """A loader over a stored profile secret (a TinyIC-owned token document)."""

    def load() -> GrokTokens | None:
        secret = secret_provider()
        if secret is None:
            return None
        tokens = tokens_from_profile_secret(secret)
        if tokens is None:
            raise GrokTokenError(
                GrokAuthReason.INVALID_CREDENTIAL,
                "Stored Grok credential is not a valid token document",
            )
        return tokens

    return load


def _default_http_post(url: str, form: Mapping[str, str]) -> Mapping[str, Any]:
    """Form-encoded POST returning the parsed JSON body, even on OAuth errors.

    HTTP error bodies that parse as JSON are *returned* (RFC 8628 delivers
    ``authorization_pending``/``slow_down`` as 400s); anything else raises a
    sanitized error that never reflects the URL, form, or response text.
    """

    data = urllib.parse.urlencode(dict(form)).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        try:
            parsed = json.loads(body)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, Mapping):
            return parsed
        raise GrokTokenError(
            GrokAuthReason.INVALID_CREDENTIAL,
            "Grok OAuth endpoint rejected the request",
        ) from None
    except Exception:
        raise GrokTokenError(
            GrokAuthReason.INVALID_CREDENTIAL,
            "Grok OAuth endpoint is unreachable",
        ) from None
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, Mapping):
        raise GrokTokenError(
            GrokAuthReason.INVALID_CREDENTIAL,
            "Grok OAuth endpoint returned an invalid response",
        )
    return parsed


def refresh_grok_tokens(
    tokens: GrokTokens,
    *,
    http_post: HttpPostForm | None = None,
    token_url: str = GROK_TOKEN_URL,
    client_id: str = GROK_CLIENT_ID,
    now: float | None = None,
) -> GrokTokens:
    """Exchange a refresh token for a fresh token set — in memory only.

    This never persists anything: read-through callers must leave the CLI's
    file untouched, and profile-owned callers persist through the profile
    store explicitly if they choose to.
    """

    if not tokens.refresh_token:
        raise GrokTokenError(
            GrokAuthReason.EXPIRED,
            "Grok sign-in has expired and cannot be refreshed",
        )
    poster = http_post or _default_http_post
    try:
        payload = poster(
            token_url,
            {
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": client_id,
            },
        )
    except GrokTokenError:
        raise
    except Exception:
        raise GrokTokenError(
            GrokAuthReason.INVALID_CREDENTIAL,
            "Grok sign-in could not be refreshed",
        ) from None
    if not isinstance(payload, Mapping):
        raise GrokTokenError(
            GrokAuthReason.INVALID_CREDENTIAL,
            "Grok token endpoint returned an invalid response",
        )
    error = payload.get("error")
    if isinstance(error, str) and error:
        reason = (
            GrokAuthReason.EXPIRED
            if error == "invalid_grant"
            else GrokAuthReason.INVALID_CREDENTIAL
        )
        raise GrokTokenError(reason, "Grok sign-in refresh was rejected")
    access = payload.get("access_token")
    if not isinstance(access, str) or not access.strip():
        raise GrokTokenError(
            GrokAuthReason.INVALID_CREDENTIAL,
            "Grok token endpoint returned no access token",
        )
    refresh = payload.get("refresh_token")
    refresh = (
        refresh.strip()
        if isinstance(refresh, str) and refresh.strip()
        else tokens.refresh_token
    )
    expires_in = payload.get("expires_in")
    instant = time.time() if now is None else now
    expires_at = (
        instant + float(expires_in)
        if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool)
        else None
    )
    return GrokTokens(access.strip(), refresh, expires_at)


class GrokTokenSource:
    """An in-memory access-token cache with skewed, session-local refresh.

    ``loader`` re-reads the underlying source (CLI file or stored profile) so
    an externally refreshed CLI login is picked up; when the loaded token is
    stale, the refresh grant runs and the result is cached **in memory only**.
    Nothing here ever writes a file or the profile store.
    """

    def __init__(
        self,
        loader: Callable[[], GrokTokens | None],
        *,
        http_post: HttpPostForm | None = None,
        clock: Callable[[], float] | None = None,
        token_url: str = GROK_TOKEN_URL,
        client_id: str = GROK_CLIENT_ID,
        skew: float = TOKEN_EXPIRY_SKEW_SECONDS,
    ) -> None:
        if skew < 0:
            raise ValueError("token expiry skew cannot be negative")
        self._loader = loader
        self._http_post = http_post
        self._clock = clock or time.time
        self._token_url = token_url
        self._client_id = client_id
        self._skew = skew
        self._tokens: GrokTokens | None = None
        self._lock = threading.Lock()

    def access_token(self) -> str:
        """Return a currently fresh access token, refreshing in memory."""

        with self._lock:
            now = float(self._clock())
            cached = self._tokens
            if cached is not None and cached.is_fresh(now=now, skew=self._skew):
                return cached.access_token
            loaded = self._loader()
            if loaded is None:
                raise GrokTokenError(
                    GrokAuthReason.MISSING_CREDENTIAL,
                    "Grok sign-in was not found",
                )
            if loaded.is_fresh(now=now, skew=self._skew):
                self._tokens = loaded
                return loaded.access_token
            refreshed = refresh_grok_tokens(
                loaded,
                http_post=self._http_post,
                token_url=self._token_url,
                client_id=self._client_id,
                now=now,
            )
            self._tokens = refreshed
            return refreshed.access_token


# --------------------------------------------------------------------------
# probes (doctor / onboarding seam)
# --------------------------------------------------------------------------


def _probe_tokens(
    tokens: GrokTokens, *, source: str, now: float | None = None
) -> GrokAuthProbe:
    instant = time.time() if now is None else now
    if tokens.is_fresh(now=instant) or tokens.refresh_token:
        return GrokAuthProbe(
            GrokAuthReason.OK, source, "Grok sign-in is available"
        )
    return GrokAuthProbe(
        GrokAuthReason.EXPIRED, source, "Grok sign-in has expired"
    )


def probe_grok_auth(
    path: Path | None = None,
    *,
    home: Path | None = None,
    now: float | None = None,
) -> GrokAuthProbe:
    """Probe the official CLI's credential file without taking ownership."""

    resolved = path if path is not None else default_grok_auth_path(home)
    try:
        tokens = _read_auth_file(Path(resolved))
    except GrokTokenError as error:
        return GrokAuthProbe(error.reason, "grok_file", str(error))
    if tokens is None:
        return GrokAuthProbe(
            GrokAuthReason.MISSING_CREDENTIAL,
            "grok_file",
            "Grok CLI sign-in was not found",
        )
    return _probe_tokens(tokens, source="grok_file", now=now)


def probe_grok_profile_secret(
    secret: Any, *, now: float | None = None
) -> GrokAuthProbe:
    """Probe a stored ``GROK_OAUTH`` profile secret (a token document)."""

    if secret is None:
        return GrokAuthProbe(
            GrokAuthReason.MISSING_CREDENTIAL,
            "grok_profile",
            "Grok sign-in was not found",
        )
    tokens = tokens_from_profile_secret(secret)
    if tokens is None:
        return GrokAuthProbe(
            GrokAuthReason.INVALID_CREDENTIAL,
            "grok_profile",
            "Stored Grok credential is not a valid token document",
        )
    return _probe_tokens(tokens, source="grok_profile", now=now)


def entitlement_reason(status: int, body_text: str) -> str | None:
    """Map the server-side entitlement 403 to ``subscription_inactive``."""

    if status != 403 or not isinstance(body_text, str):
        return None
    lowered = body_text.casefold()
    if any(marker in lowered for marker in GROK_ENTITLEMENT_MARKERS):
        return GrokAuthReason.SUBSCRIPTION_INACTIVE.value
    return None


# --------------------------------------------------------------------------
# device-code flow (RFC 8628 + PKCE S256)
# --------------------------------------------------------------------------


@dataclass(frozen=True, repr=False)
class GrokDeviceChallenge:
    """Non-secret user instructions from the device-authorization response."""

    verification_url: str
    user_code: str
    interval: float
    expires_at: float | None

    def __repr__(self) -> str:
        return (
            "GrokDeviceChallenge("
            f"verification_url={self.verification_url!r}, "
            f"user_code={self.user_code!r}, interval={self.interval!r}"
            ")"
        )


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class GrokDeviceCodeFlow:
    """The wizard-facing device-code state machine (pure logic, no I/O).

    All network happens through the injected ``http_post`` seam.  States:
    ``idle`` → :meth:`start` → ``pending`` → :meth:`poll` … →
    ``complete`` | ``denied`` | ``expired``.  ``poll`` returns ``None`` while
    authorization is pending and honors ``slow_down`` by widening the
    interval; terminal failures raise reason-coded :class:`GrokTokenError`.
    """

    def __init__(
        self,
        *,
        http_post: HttpPostForm | None = None,
        clock: Callable[[], float] | None = None,
        client_id: str = GROK_CLIENT_ID,
        scope: str = GROK_OAUTH_SCOPE,
        device_authorization_url: str = GROK_DEVICE_AUTHORIZATION_URL,
        token_url: str = GROK_TOKEN_URL,
        use_pkce: bool = True,
    ) -> None:
        self._http_post = http_post or _default_http_post
        self._clock = clock or time.time
        self._client_id = client_id
        self._scope = scope
        self._device_authorization_url = device_authorization_url
        self._token_url = token_url
        self._use_pkce = bool(use_pkce)
        self._state = "idle"
        self._device_code: str | None = None
        self._verifier: str | None = None
        self._interval = 5.0
        self._expires_at: float | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def interval(self) -> float:
        return self._interval

    def start(self) -> GrokDeviceChallenge:
        if self._state != "idle":
            raise RuntimeError("Grok device flow was already started")
        form = {"client_id": self._client_id, "scope": self._scope}
        if self._use_pkce:
            self._verifier, challenge = _pkce_pair()
            form["code_challenge"] = challenge
            form["code_challenge_method"] = "S256"
        payload = self._http_post(self._device_authorization_url, form)
        device_code = payload.get("device_code")
        user_code = payload.get("user_code")
        verification = (
            payload.get("verification_uri_complete")
            or payload.get("verification_uri")
            or payload.get("verification_url")
        )
        if (
            not isinstance(device_code, str)
            or not device_code
            or not isinstance(user_code, str)
            or not user_code
            or not isinstance(verification, str)
            or not verification
        ):
            raise GrokTokenError(
                GrokAuthReason.INVALID_CREDENTIAL,
                "Grok device authorization returned an invalid challenge",
            )
        interval = payload.get("interval")
        self._interval = (
            float(interval)
            if isinstance(interval, (int, float)) and not isinstance(interval, bool)
            and interval > 0
            else 5.0
        )
        expires_in = payload.get("expires_in")
        self._expires_at = (
            float(self._clock()) + float(expires_in)
            if isinstance(expires_in, (int, float))
            and not isinstance(expires_in, bool)
            else None
        )
        self._device_code = device_code
        self._state = "pending"
        return GrokDeviceChallenge(
            verification_url=verification,
            user_code=user_code,
            interval=self._interval,
            expires_at=self._expires_at,
        )

    def poll(self) -> GrokTokens | None:
        """One token poll: tokens on success, ``None`` while pending."""

        if self._state != "pending" or self._device_code is None:
            raise RuntimeError("Grok device flow is not pending")
        now = float(self._clock())
        if self._expires_at is not None and now >= self._expires_at:
            self._state = "expired"
            raise GrokTokenError(
                GrokAuthReason.EXPIRED, "Grok device sign-in expired"
            )
        form = {
            "grant_type": _DEVICE_GRANT,
            "device_code": self._device_code,
            "client_id": self._client_id,
        }
        if self._use_pkce and self._verifier:
            form["code_verifier"] = self._verifier
        payload = self._http_post(self._token_url, form)
        error = payload.get("error")
        if error == "authorization_pending":
            return None
        if error == "slow_down":
            self._interval += 5.0
            return None
        if error == "access_denied":
            self._state = "denied"
            raise GrokTokenError(
                GrokAuthReason.INVALID_CREDENTIAL,
                "Grok device sign-in was denied",
            )
        if error == "expired_token":
            self._state = "expired"
            raise GrokTokenError(
                GrokAuthReason.EXPIRED, "Grok device sign-in expired"
            )
        if isinstance(error, str) and error:
            self._state = "denied"
            raise GrokTokenError(
                GrokAuthReason.INVALID_CREDENTIAL,
                "Grok device sign-in failed",
            )
        access = payload.get("access_token")
        if not isinstance(access, str) or not access.strip():
            raise GrokTokenError(
                GrokAuthReason.INVALID_CREDENTIAL,
                "Grok token endpoint returned no access token",
            )
        refresh = payload.get("refresh_token")
        refresh = (
            refresh.strip()
            if isinstance(refresh, str) and refresh.strip()
            else None
        )
        expires_in = payload.get("expires_in")
        expires_at = (
            now + float(expires_in)
            if isinstance(expires_in, (int, float))
            and not isinstance(expires_in, bool)
            else None
        )
        self._state = "complete"
        return GrokTokens(access.strip(), refresh, expires_at)


@dataclass(frozen=True, repr=False)
class GrokLoginResult:
    """Wizard-facing outcome; the secret rides only in ``profile_secret``."""

    reason: GrokAuthReason
    profile_secret: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason is GrokAuthReason.OK

    def __repr__(self) -> str:
        return (
            "GrokLoginResult("
            f"reason={self.reason.value!r}, "
            f"has_secret={self.profile_secret is not None}"
            ")"
        )


class GrokDeviceLoginSession:
    """One device-code login matching the wizard's start/wait protocol.

    Mirrors :class:`tinyic.auth.openai.CodexLoginSession`'s shape (context
    manager; ``start(mode)`` → challenge; ``wait(challenge)`` → result) so the
    onboarding wizard drives both lanes through one seam.  Only device-code
    mode exists here; the browser step always belongs to the user.
    """

    def __init__(
        self,
        *,
        flow_factory: Callable[[], GrokDeviceCodeFlow] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._flow_factory = flow_factory or GrokDeviceCodeFlow
        self._sleep = sleep or time.sleep
        self._flow: GrokDeviceCodeFlow | None = None

    def __enter__(self) -> "GrokDeviceLoginSession":
        self._flow = self._flow_factory()
        return self

    def __exit__(self, *_args: object) -> None:
        self._flow = None

    def start(self, mode: object = None) -> GrokDeviceChallenge:
        if self._flow is None:
            raise RuntimeError("Grok login session is not open")
        return self._flow.start()

    def wait(self, challenge: GrokDeviceChallenge) -> GrokLoginResult:
        if self._flow is None:
            raise RuntimeError("Grok login session is not open")
        while True:
            self._sleep(self._flow.interval)
            try:
                tokens = self._flow.poll()
            except GrokTokenError as error:
                return GrokLoginResult(error.reason)
            except Exception:
                return GrokLoginResult(GrokAuthReason.INVALID_CREDENTIAL)
            if tokens is not None:
                return GrokLoginResult(
                    GrokAuthReason.OK, tokens_to_profile_secret(tokens)
                )


__all__ = [
    "GROK_AUTH_FILE_KEY",
    "GROK_AUTH_FILE_LEGACY_KEY",
    "GROK_AUTHORIZE_URL",
    "GROK_CLIENT_ID",
    "GROK_DEVICE_AUTHORIZATION_URL",
    "GROK_DISCOVERY_URL",
    "GROK_ENTITLEMENT_MARKERS",
    "GROK_ISSUER",
    "GROK_OAUTH_SCOPE",
    "GROK_TOKEN_URL",
    "GrokAuthProbe",
    "GrokAuthReason",
    "GrokDeviceChallenge",
    "GrokDeviceCodeFlow",
    "GrokDeviceLoginSession",
    "GrokLoginResult",
    "GrokTokenError",
    "GrokTokenSource",
    "GrokTokens",
    "TOKEN_EXPIRY_SKEW_SECONDS",
    "default_grok_auth_path",
    "entitlement_reason",
    "file_token_loader",
    "parse_grok_auth_document",
    "probe_grok_auth",
    "probe_grok_profile_secret",
    "profile_token_loader",
    "refresh_grok_tokens",
    "tokens_from_profile_secret",
    "tokens_to_profile_secret",
]
