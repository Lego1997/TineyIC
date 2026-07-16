"""The credential seam between the model layer and auth (M3).

Every transport is handed an opaque
``CredentialProvider`` callable that turns a credential *reference* into a
secret string (or ``None`` when absent). M3's default implementation is
``tinyic.auth.AuthManager``: keyring/``0600`` profiles, ordered ``auth_order``
fallback, and official subscription runtimes behind the unchanged call
signature. ``EnvCredentialProvider`` remains a compatibility/test utility.

Secrets resolved here must never be placed into events, logs, or exports.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class CredentialProvider(Protocol):
    """Resolve a credential reference to a secret, or ``None`` if unavailable.

    ``ref`` is an opaque provider/adapter-defined key: an adapter may request
    its historical environment name (``"OPENAI_API_KEY"``), while the auth
    manager resolves the already-selected named profile behind this seam.
    """

    def __call__(self, ref: str) -> str | None: ...


class EnvCredentialProvider:
    """A :class:`CredentialProvider` backed by environment variables.

    ``ref`` is treated as an environment variable name.  A blank/whitespace
    value resolves to ``None`` so an empty export is not mistaken for a secret.
    """

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ: Mapping[str, str] = os.environ if environ is None else environ

    def __call__(self, ref: str) -> str | None:
        if not ref:
            return None
        value = self._environ.get(ref)
        if value is None:
            return None
        value = value.strip()
        return value or None


class StaticCredentialProvider:
    """A fixed ``ref -> secret`` map, for tests and fully offline transports."""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        self._secrets = dict(secrets)

    def __call__(self, ref: str) -> str | None:
        return self._secrets.get(ref)


__all__ = [
    "CredentialProvider",
    "EnvCredentialProvider",
    "StaticCredentialProvider",
]
