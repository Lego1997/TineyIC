"""The credential seam between the model layer and auth (M3).

M2 designs the seam only: a transport is handed an opaque
``CredentialProvider`` callable that turns a credential *reference* into a
secret string (or ``None`` when absent).  For now the sole implementation is an
environment-variable lookup.  M3 (FR-2.x) replaces it with the profile store —
keyring / ``0600`` file, ordered ``auth_order`` fallback, and the subscription
runtimes — **without changing this call signature**, so nothing downstream of
the seam has to change.

Secrets resolved here must never be placed into events, logs, or exports.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class CredentialProvider(Protocol):
    """Resolve a credential reference to a secret, or ``None`` if unavailable.

    ``ref`` is an opaque provider/adapter-defined key.  Today it is an
    environment-variable name (``"OPENAI_API_KEY"``); M3 will resolve richer
    references such as ``"openai:work"`` behind the same signature.
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
