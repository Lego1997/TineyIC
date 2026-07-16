"""Ordered auth-profile rotation around unchanged provider child factories.

The wrapper changes authentication, never model selection.  A child factory
still receives ``(binding, credentials)`` and the candidate itself implements
the credential-provider callable.  Rotation is permitted only when a child
raises :class:`UsageLimitError` before producing its first event.  Once any
event exists—even one filtered by this wrapper—the call is never replayed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace

from tinyic.auth.manager import (
    AuthCandidate,
    AuthManager,
    AuthResolutionError,
)
from tinyic.auth.profiles import AuthLane
from tinyic.models.binding import ModelBinding
from tinyic.models.credentials import CredentialProvider
from tinyic.models.types import (
    AuthError,
    ChatRequest,
    ChatStreamEvent,
    FinalMessage,
    InvalidRequestError,
    ProviderError,
    RateLimitError,
    Transport,
    TransientError,
    Usage,
    UsageLimitError,
    UsageWindow,
)


ChildTransportFactory = Callable[
    [ModelBinding, CredentialProvider], Transport
]


class RotatingTransport:
    """Keep one model fixed while selecting from an ordered auth chain."""

    def __init__(
        self,
        binding: ModelBinding,
        candidates: Sequence[AuthCandidate],
        child_factory: ChildTransportFactory,
        *,
        manager: AuthManager,
        subscription_only: bool = False,
    ) -> None:
        self.binding = binding
        self._manager = manager
        self._child_factory = child_factory
        supplied = tuple(candidates)
        matching = tuple(
            candidate
            for candidate in supplied
            if candidate.provider == binding.provider.lower()
        )
        if not matching and supplied:
            raise AuthResolutionError(
                "lane_incompatible",
                provider=binding.provider.lower(),
                auth_profile=binding.auth_profile,
            )
        if subscription_only:
            filtered = tuple(
                candidate
                for candidate in matching
                if candidate.lane is AuthLane.SUBSCRIPTION
            )
            if not filtered and matching:
                raise AuthResolutionError(
                    "subscription_required",
                    provider=binding.provider.lower(),
                    auth_profile=binding.auth_profile,
                )
            matching = filtered
        if not matching:
            raise AuthResolutionError(
                "missing_credential",
                provider=binding.provider.lower(),
                auth_profile=binding.auth_profile,
            )
        self._candidates = matching
        self._transports: dict[int, Transport] = {}
        self._selected_index: int | None = None
        self._state_lock = threading.RLock()

    @property
    def selected_profile_ref(self) -> str | None:
        """The sticky profile selected by the latest successful call."""
        with self._state_lock:
            if self._selected_index is None:
                return None
            return self._candidates[self._selected_index].ref

    @property
    def active_profile_ref(self) -> str | None:
        """The sticky successful profile, else the first currently usable one."""

        with self._state_lock:
            selected = self._selected_index
        if selected is not None:
            candidate = self._candidates[selected]
            if not self._manager.is_usage_limited(candidate):
                return candidate.ref
        for candidate in self._candidates:
            if not self._manager.is_usage_limited(candidate):
                return candidate.ref
        return None

    def generate(self, request: ChatRequest) -> Iterator[ChatStreamEvent]:
        """Generate once, rotating only on a pre-output usage-limit block."""
        for index in self._candidate_indices():
            candidate = self._candidates[index]
            if self._manager.is_usage_limited(candidate):
                continue

            seen_child_event = False
            saw_usage_event = False
            buffered_final: FinalMessage | None = None
            terminal_usage: Usage | None = None
            try:
                transport = self._transport_for(index, candidate)
                candidate_binding = request.binding.with_auth_profile(candidate.ref)
                # Authentication may change on fallback; the selected model and
                # all call-level params remain exactly those in the request.
                if candidate_binding.model_ref != self.binding.model_ref:
                    raise ValueError("request model does not match bound transport")
                candidate_request = ChatRequest(
                    request.messages,
                    candidate_binding,
                    stream=request.stream,
                )
                for child_event in transport.generate(candidate_request):
                    seen_child_event = True
                    event = _decorate_event(child_event, candidate)
                    if isinstance(event, UsageWindow) and (
                        candidate.lane is AuthLane.SUBSCRIPTION
                    ):
                        # Low-level subscription runtimes remain useful directly
                        # and may expose their own meter.  The committee wrapper
                        # suppresses it so the manager emits one authoritative,
                        # debate-isolated snapshot after confirmed success.
                        continue
                    if isinstance(event, Usage):
                        saw_usage_event = True
                        terminal_usage = event
                    if isinstance(event, FinalMessage):
                        if event.usage is not None:
                            terminal_usage = event.usage
                        buffered_final = event
                        continue
                    yield event
            except UsageLimitError as error:
                self._manager.mark_usage_limited(
                    candidate, retry_after=error.retry_after
                )
                if seen_child_event or error.partial_output:
                    # Replaying after a delta/usage/final child event could
                    # duplicate prose or charges.  A non-streaming child can
                    # also flag provider output that it intentionally did not
                    # yield. Suppress provider text and expose only a fixed
                    # normalized failure.
                    raise UsageLimitError(
                        "The active auth profile reached its usage limit.",
                        partial_output=True,
                        retry_after=error.retry_after,
                        provider=binding_provider(self.binding),
                    ) from None
                continue
            except ProviderError as error:
                raise _sanitized_provider_error(
                    error, provider=binding_provider(self.binding)
                ) from None

            with self._state_lock:
                self._selected_index = index
            if terminal_usage is None:
                # Official subscription runtimes may report message success
                # without token counts.  A zero-token record still preserves
                # FR-1.5's one-usage-event-per-call contract.
                terminal_usage = Usage(
                    auth_profile=candidate.ref,
                    lane=candidate.lane.value,
                )
                yield terminal_usage
            elif not saw_usage_event:
                # Some SDKs attach accounting only to their terminal object.
                # Promote it so the engine still receives one usage event for
                # every completed model call (FR-1.5).
                yield terminal_usage
            if buffered_final is not None and buffered_final.usage is None:
                buffered_final = replace(
                    buffered_final, usage=terminal_usage
                )
            window = self._manager.record_success(candidate)
            if window is not None:
                yield window
            if buffered_final is not None:
                yield buffered_final
            return

        raise AuthResolutionError(
            "usage_limited",
            provider=binding_provider(self.binding),
            auth_profile=self.binding.auth_profile,
        )

    def _candidate_indices(self) -> tuple[int, ...]:
        with self._state_lock:
            selected = self._selected_index
        indices = tuple(range(len(self._candidates)))
        if selected is None:
            return indices
        return (selected,) + tuple(index for index in indices if index != selected)

    def _transport_for(
        self, index: int, candidate: AuthCandidate
    ) -> Transport:
        with self._state_lock:
            cached = self._transports.get(index)
            if cached is not None:
                return cached
            candidate_binding = self.binding.with_auth_profile(candidate.ref)
            # The legacy factory signature is deliberately unchanged.
            transport = self._child_factory(candidate_binding, candidate)
            self._transports[index] = transport
            return transport


def _decorate_usage(usage: Usage, candidate: AuthCandidate) -> Usage:
    return replace(
        usage,
        auth_profile=candidate.ref,
        lane=candidate.lane.value,
    )


def _sanitized_provider_error(
    error: ProviderError, *, provider: str
) -> ProviderError:
    """Preserve error semantics without reflecting provider-controlled text."""
    kwargs = {
        "retry_after": error.retry_after,
        "provider": provider,
    }
    if isinstance(error, AuthError):
        return AuthError("The selected credential was rejected.", **kwargs)
    if isinstance(error, RateLimitError):
        return RateLimitError("The provider rate limit was reached.", **kwargs)
    if isinstance(error, InvalidRequestError):
        return InvalidRequestError("The provider rejected the request.", **kwargs)
    if isinstance(error, TransientError):
        return TransientError("The provider is temporarily unavailable.", **kwargs)
    return ProviderError(
        "The provider request failed.", kind=error.kind, **kwargs
    )


def _decorate_event(
    event: ChatStreamEvent, candidate: AuthCandidate
) -> ChatStreamEvent:
    if isinstance(event, Usage):
        return _decorate_usage(event, candidate)
    if isinstance(event, FinalMessage) and event.usage is not None:
        return replace(event, usage=_decorate_usage(event.usage, candidate))
    return event


def binding_provider(binding: ModelBinding) -> str:
    """Return a canonical provider label without reflecting model/user text."""
    return binding.provider.strip().lower()


__all__ = ["ChildTransportFactory", "RotatingTransport"]
