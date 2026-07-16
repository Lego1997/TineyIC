"""Bounded retry/backoff policy and HTTP-status error classification (FR-1.2).

The retry loop lives in :class:`~tinyic.models.adapters._base.BaseHttpAdapter`;
this module owns the two pure pieces it needs so they can be tested and reused
without a live call: the status→:class:`ErrorKind` map and the backoff
schedule.  Only :data:`RETRYABLE_KINDS` (rate-limit + transient) are ever
retried; auth and invalid-request failures fail fast.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..types import ErrorKind


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff, retry-after aware, deterministic by default.

    ``max_retries`` is the number of *retries* after the first attempt (so the
    total attempt count is ``max_retries + 1``).  ``jitter`` is added verbatim
    to every computed delay; it defaults to ``0.0`` so tests are deterministic —
    production callers can pass a small random value.
    """

    max_retries: int = 2
    base_delay: float = 0.5
    max_delay: float = 8.0
    multiplier: float = 2.0
    jitter: float = 0.0


def error_kind_for_status(status: int) -> ErrorKind:
    """Map an HTTP status code to a normalized :class:`ErrorKind`.

    Shared by every OpenAI-family and Anthropic adapter; the handful of
    provider-specific codes (e.g. Anthropic's ``529 Overloaded``) are folded
    into the transient bucket here so classification stays one place.
    """
    if status in (401, 403):
        return ErrorKind.AUTH
    if status == 429:
        return ErrorKind.RATE_LIMIT
    if status in (408, 409, 425, 500, 502, 503, 504, 529):
        return ErrorKind.TRANSIENT
    if 400 <= status < 500:
        # Other 4xx (400/404/405/415/422/…) are the caller's fault: fail fast.
        return ErrorKind.INVALID_REQUEST
    if status >= 500:
        return ErrorKind.TRANSIENT
    # Not an error status; callers only invoke this for status >= 400.
    return ErrorKind.TRANSIENT


def compute_backoff(
    attempt: int, policy: RetryPolicy, retry_after: float | None = None
) -> float:
    """Delay before the next attempt (``attempt`` is 0-based: 0 = first retry).

    A provider-supplied ``retry_after`` (from the ``Retry-After`` header on a
    rate-limit response) takes precedence, clamped to ``max_delay``; otherwise
    the delay grows geometrically from ``base_delay`` and is clamped to
    ``max_delay``.  ``jitter`` is added last.
    """
    if retry_after is not None and retry_after >= 0:
        base = min(retry_after, policy.max_delay)
    else:
        base = min(policy.base_delay * (policy.multiplier**attempt), policy.max_delay)
    return base + policy.jitter


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header value in *seconds* (delta-seconds only).

    HTTP-date forms are ignored (return ``None``) — providers TinyIC targets use
    the numeric delta-seconds form, and a wrong date parse is worse than falling
    back to the exponential schedule.
    """
    if value is None:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


__all__ = [
    "RetryPolicy",
    "compute_backoff",
    "error_kind_for_status",
    "parse_retry_after",
]
