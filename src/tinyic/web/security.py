"""Localhost capability security for the TinyIC web face.

Binding to loopback is necessary but not sufficient: a hostile web page can
still target a localhost service, and DNS rebinding can supply an attacker
controlled ``Host`` header.  This module keeps the four independent controls
used by :mod:`tinyic.web.server` small and directly testable: exact Host and
Origin allowlists, a per-process capability token, and JSON-only POSTs.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from enum import Enum
from http.cookies import CookieError, SimpleCookie

__all__ = [
    "CACHE_CONTROL",
    "CAPABILITY_COOKIE",
    "CSP_POLICY",
    "AuthStatus",
    "SecurityPolicy",
    "allowed_hosts",
    "allowed_origins",
    "generate_capability_token",
    "is_json_content_type",
]

CACHE_CONTROL = "no-store"
CAPABILITY_COOKIE = "tinyic_token"
CSP_POLICY = (
    "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
)


def _generate_capability_cookie_name() -> str:
    """Return a browser-cookie name unique to one server instance."""
    return f"{CAPABILITY_COOKIE}_{secrets.token_hex(8)}"


def generate_capability_token() -> str:
    """Return a fresh 32-byte URL-safe capability token."""
    return secrets.token_urlsafe(32)


def allowed_hosts(port: int) -> frozenset[str]:
    """Return the exact HTTP Host values accepted for *port*."""
    return frozenset(
        {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        }
    )


def allowed_origins(port: int) -> frozenset[str]:
    """Return the exact same-origin values accepted for *port*."""
    return frozenset(f"http://{host}" for host in allowed_hosts(port))


def is_json_content_type(value: str | None) -> bool:
    """Whether a Content-Type is exactly JSON, allowing normal parameters."""
    if not isinstance(value, str):
        return False
    media_type, _, _parameters = value.partition(";")
    return media_type.strip().casefold() == "application/json"


class AuthStatus(str, Enum):
    """Outcome of checking cookie/Bearer capability credentials."""

    OK = "ok"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True)
class SecurityPolicy:
    """One server's immutable capability and localhost allowlists.

    The token is deliberately excluded from ``repr`` so diagnostics cannot
    disclose it accidentally.  The only intended disclosure is the launch URL
    printed by :class:`~tinyic.web.server.WebServer`.
    """

    port: int
    token: str = field(default_factory=generate_capability_token, repr=False)
    cookie_name: str = field(default_factory=_generate_capability_cookie_name)

    def __post_init__(self) -> None:
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise TypeError("port must be an integer")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if not isinstance(self.token, str) or not self.token:
            raise ValueError("token must be a non-empty string")
        if not self.token.isascii() or any(
            not (character.isalnum() or character in "-_")
            for character in self.token
        ):
            raise ValueError("token must contain only URL-safe token characters")
        cookie_name_characters = frozenset(
            "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        )
        if (
            not isinstance(self.cookie_name, str)
            or not self.cookie_name
            or any(
                character not in cookie_name_characters
                for character in self.cookie_name
            )
        ):
            raise ValueError("cookie_name must be a valid HTTP cookie name")

    @property
    def hosts(self) -> frozenset[str]:
        return allowed_hosts(self.port)

    @property
    def origins(self) -> frozenset[str]:
        return allowed_origins(self.port)

    def valid_host(self, value: str | None) -> bool:
        """Require one literal allowlisted Host value (DNS-rebinding guard)."""
        return isinstance(value, str) and value.strip() in self.hosts

    def valid_origin(self, value: str | None) -> bool:
        """Accept an absent Origin or one literal same-origin value."""
        return value is None or (
            isinstance(value, str) and value.strip() in self.origins
        )

    def matches_token(self, candidate: str | None) -> bool:
        """Compare a supplied capability without timing-dependent equality."""
        if not isinstance(candidate, str):
            return False
        try:
            return secrets.compare_digest(candidate, self.token)
        except TypeError:  # pragma: no cover - defensive for exotic str types
            return False

    def authenticate(
        self,
        *,
        cookie_header: str | None,
        authorization: str | None,
    ) -> AuthStatus:
        """Authenticate a cookie or ``Authorization: Bearer`` credential.

        A valid credential wins even if the other channel is malformed.  This
        matters when a command-line client sends Bearer auth while a browser's
        stale cookie is also present.
        """
        candidates: list[str] = []
        credentials_present = False

        if cookie_header:
            try:
                cookies = SimpleCookie()
                cookies.load(cookie_header)
                morsel = cookies.get(self.cookie_name)
            except (CookieError, TypeError):
                morsel = None
                credentials_present = True
            if morsel is not None:
                credentials_present = True
                candidates.append(morsel.value)

        if authorization is not None:
            credentials_present = True
            scheme, separator, value = authorization.strip().partition(" ")
            if separator and scheme.casefold() == "bearer" and value.strip():
                candidates.append(value.strip())

        if any(self.matches_token(candidate) for candidate in candidates):
            return AuthStatus.OK
        return AuthStatus.INVALID if credentials_present else AuthStatus.MISSING
