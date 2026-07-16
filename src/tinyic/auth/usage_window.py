"""Thread-safe local rolling-window estimates for subscription auth profiles."""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from tinyic.models.types import UsageWindow


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_millis(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RollingUsageMeter:
    """Count successful TinyIC messages per profile over a rolling duration.

    The meter never stores prompts, model output, account identity, or auth
    material. Its snapshots are therefore safe to place on the event stream.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        window: timedelta = timedelta(hours=5),
    ) -> None:
        if window <= timedelta(0):
            raise ValueError("usage window must be positive")
        self._clock = clock or _utc_now
        self._window = window
        self._events: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = threading.Lock()

    def record(self, auth_profile: str, *, estimate: int = 0) -> UsageWindow:
        """Record one completed message and return the resulting snapshot."""
        if not auth_profile:
            raise ValueError("auth_profile is required")
        now = self._normalized_now()
        with self._lock:
            events = self._events[auth_profile]
            self._prune(events, now)
            events.append(now)
            return self._snapshot_locked(auth_profile, events, estimate)

    def snapshot(self, auth_profile: str, *, estimate: int = 0) -> UsageWindow:
        """Return the current snapshot without incrementing the count."""
        if not auth_profile:
            raise ValueError("auth_profile is required")
        now = self._normalized_now()
        with self._lock:
            events = self._events[auth_profile]
            self._prune(events, now)
            return self._snapshot_locked(auth_profile, events, estimate)

    def _normalized_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("usage-meter clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)

    def _prune(self, events: deque[datetime], now: datetime) -> None:
        cutoff = now - self._window
        while events and events[0] <= cutoff:
            events.popleft()

    def _snapshot_locked(
        self, auth_profile: str, events: deque[datetime], estimate: int
    ) -> UsageWindow:
        if estimate < 0:
            raise ValueError("window estimate cannot be negative")
        resets_at = _iso_millis(events[0] + self._window) if events else None
        return UsageWindow(
            auth_profile=auth_profile,
            window_used_msgs=len(events),
            window_estimate_msgs=estimate,
            resets_at=resets_at,
        )


__all__ = ["RollingUsageMeter"]
