"""API-key-lane credential resolution for the optional data sources.

The social-sentiment and deep-research sources reach hosted search surfaces
(xAI ``x_search``, OpenAI ``web_search``) that are Platform/API-key billed.
Their credentials must therefore resolve through the same auth seam the debate
uses, and only from an API-key lane: a selected subscription/OAuth runtime must
never be assumed to serve them, and its usage would not be attributable.  This
mirrors the doctrine already encoded in ``models/research/selector.py``.

Secrets resolved here must never reach events, logs, or exports.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from ..models.binding import ModelBinding
from ..models.credentials import CredentialProvider


def _first_secret(
    provider: CredentialProvider, refs: Iterable[str]
) -> Optional[str]:
    for ref in refs:
        try:
            value = provider(ref)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_api_key_secret(
    binding: ModelBinding,
    refs: Iterable[str],
    credentials: CredentialProvider,
) -> Optional[str]:
    """Return an API-key-lane secret for ``binding``'s provider, or ``None``.

    When ``credentials`` exposes ``candidates`` (an ``AuthManager``) it owns
    named-profile and ``auth_order`` precedence; only its API-key-lane
    candidates are accepted, so a selected subscription lane is never mistaken
    for a search credential.  A plain :class:`CredentialProvider` (e.g.
    ``EnvCredentialProvider``) is read directly for the provider's documented
    references.
    """
    refs = tuple(refs)
    candidates = getattr(credentials, "candidates", None)
    if callable(candidates):
        try:
            resolved = candidates(binding)
        except Exception:
            # No usable candidate for this provider (AuthResolutionError et al.)
            return None
        for candidate in resolved:
            lane = getattr(candidate, "lane", None)
            lane_value = getattr(lane, "value", lane)
            if lane_value != "api_key":
                continue
            secret = _first_secret(candidate, refs)
            if secret:
                return secret
        return None
    return _first_secret(credentials, refs)


def has_api_key_lane(
    binding: ModelBinding,
    refs: Iterable[str],
    credentials: CredentialProvider,
) -> bool:
    """Whether an API-key-lane secret is resolvable (makes no network call)."""
    return resolve_api_key_secret(binding, refs, credentials) is not None


__all__ = ["has_api_key_lane", "resolve_api_key_secret"]
