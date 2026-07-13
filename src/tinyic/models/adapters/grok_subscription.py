"""Grok (xAI) subscription transport: OpenAI Chat wire + OAuth bearer seam.

The subscription lane speaks exactly the same wire as the API-key lane — plain
Chat Completions against ``https://api.x.ai/v1`` — so this transport is a thin
credential seam over :class:`~.openai_chat.OpenAIChatAdapter` rather than a
runtime subprocess (unlike the Codex/Claude lanes).  The bearer comes from a
:class:`tinyic.auth.grok.GrokTokenSource`:

* ``ProfileKind.GROK_READTHROUGH`` — reads the official grok CLI's
  ``~/.grok/auth.json`` (never writes it; stale tokens refresh in memory only);
* ``ProfileKind.GROK_OAUTH`` — decodes the TinyIC-owned token document stored
  as the profile secret.

The subscription base URL is configurable via ``GROK_SUBSCRIPTION_BASE_URL``
(default ``https://api.x.ai/v1``).  Parts of the ecosystem route CLI-lane chat
via ``https://cli-chat-proxy.grok.com/v1``; TinyIC does not hardcode that —
point the env var there if your account requires it.

Entitlement is server-side: a 403 whose body reports a missing/exhausted Grok
subscription is normalized to :class:`~tinyic.models.types.UsageLimitError`
(reason ``subscription_inactive``) *before any output*, so the auth-order
rotation can advance to the next profile (e.g. an ``XAI_API_KEY`` overflow)
without changing the model.  Subscription usage metering rides the existing
``AuthManager.record_success`` seam — xAI publishes no quota API, so only the
local rolling estimate applies.

Like the Anthropic lane, the whole transport is behind a default-on legal
switch (``[auth.grok] policy_guard`` in ``tinyic.toml``): when disabled it
raises before reading any token or touching the filesystem/network.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterator

from tinyic.auth.grok import (
    GrokAuthReason,
    GrokTokenError,
    GrokTokenSource,
    entitlement_reason,
    file_token_loader,
    profile_token_loader,
)
from tinyic.auth.profiles import ProfileKind

from ..binding import ModelBinding
from ..credentials import CredentialProvider
from ..thinking import ThinkingLevel, ThinkingProfile
from ..types import (
    AuthError,
    ChatRequest,
    ChatStreamEvent,
    ProviderError,
    UsageLimitError,
)
from ._http import HttpResponse
from .openai_chat import OpenAIChatAdapter

DEFAULT_SUBSCRIPTION_BASE_URL = "https://api.x.ai/v1"
#: Env override for the subscription-lane endpoint (see module docstring).
GROK_SUBSCRIPTION_BASE_URL_ENV_VAR = "GROK_SUBSCRIPTION_BASE_URL"
#: Internal sentinel ref; the bearer never comes from the environment.
GROK_SUBSCRIPTION_TOKEN_REF = "GROK_SUBSCRIPTION_ACCESS_TOKEN"

#: Grok's reasoning-effort dial, shared with the registry catalog so the two
#: lanes can never drift.
GROK_REASONING_EFFORTS: dict[ThinkingLevel, str] = {
    ThinkingLevel.LOW: "low",
    ThinkingLevel.MEDIUM: "medium",
    ThinkingLevel.HIGH: "high",
}

_TOKEN_ERROR_MESSAGES: dict[GrokAuthReason, str] = {
    GrokAuthReason.MISSING_CREDENTIAL: "Grok sign-in was not found",
    GrokAuthReason.EXPIRED: "Grok sign-in has expired",
    GrokAuthReason.INVALID_CREDENTIAL: "Grok sign-in is invalid",
    GrokAuthReason.SUBSCRIPTION_INACTIVE: (
        "The Grok subscription is inactive or out of resources"
    ),
}

_GROK_SUBSCRIPTION_KINDS = frozenset(
    {ProfileKind.GROK_OAUTH, ProfileKind.GROK_READTHROUGH}
)


class GrokPolicyError(AuthError):
    """The legal/policy kill switch disabled Grok subscription use."""

    reason_code = "policy_disabled"

    def __init__(self) -> None:
        super().__init__(
            "The Grok subscription lane is disabled by policy_guard.",
            provider="grok",
        )


class GrokSubscriptionTransport(OpenAIChatAdapter):
    """Chat Completions with a session-local OAuth bearer (never persisted)."""

    def __init__(
        self,
        binding: ModelBinding,
        credentials: CredentialProvider | None,
        *,
        base_url: str | None = None,
        auth_path: os.PathLike[str] | str | None = None,
        http_post: Any = None,
        clock: Any = None,
        policy_guard: bool = True,
        environ: Mapping[str, str] | None = None,
        thinking: ThinkingProfile | None = None,
        token_source: GrokTokenSource | None = None,
        **adapter_kwargs: Any,
    ) -> None:
        if binding.provider.casefold() != "grok":
            raise ValueError("Grok subscription transport requires a grok/* binding")
        if not isinstance(policy_guard, bool):
            raise TypeError("policy_guard must be a boolean")
        credential_guard = getattr(credentials, "grok_policy_guard", True)
        if not isinstance(credential_guard, bool):
            raise TypeError("credential policy guard must be a boolean")
        # A disabled manager guard cannot be re-enabled by constructing the
        # transport directly with its default arguments.
        self._grok_policy_guard = policy_guard and credential_guard

        environment = os.environ if environ is None else environ
        resolved_base = (
            base_url
            or environment.get(GROK_SUBSCRIPTION_BASE_URL_ENV_VAR)
            or DEFAULT_SUBSCRIPTION_BASE_URL
        )

        if token_source is None:
            kind = getattr(credentials, "kind", None)
            if kind is ProfileKind.GROK_READTHROUGH:
                # Explicit path wins; None means the CLI's ~/.grok/auth.json.
                loader = file_token_loader(
                    None if auth_path is None else Path(auth_path)
                )
            elif kind is ProfileKind.GROK_OAUTH:
                ref = getattr(credentials, "ref", None)
                if credentials is None or not isinstance(ref, str):
                    raise ValueError(
                        "Grok OAuth transport requires a resolved auth candidate"
                    )
                loader = profile_token_loader(lambda: credentials(ref))
            else:
                raise ValueError(
                    "Grok subscription transport requires a grok subscription "
                    "profile (grok_oauth or grok_readthrough)"
                )
            token_source = GrokTokenSource(loader, http_post=http_post, clock=clock)
        self._token_source = token_source
        self.auth_profile = (
            binding.auth_profile
            or getattr(credentials, "ref", None)
            or "grok:subscription"
        )

        super().__init__(
            binding,
            credentials,
            base_url=resolved_base,
            credential_ref=GROK_SUBSCRIPTION_TOKEN_REF,
            thinking=(
                thinking
                if thinking is not None
                else ThinkingProfile.effort(
                    "reasoning_effort", GROK_REASONING_EFFORTS
                )
            ),
            provider_name="grok",
            **adapter_kwargs,
        )

    def generate(self, request: ChatRequest) -> Iterator[ChatStreamEvent]:
        # This ordering is a legal boundary: when disabled, do not read a
        # token, touch ~/.grok, or open a connection.
        if not self._grok_policy_guard:
            raise GrokPolicyError()
        yield from super().generate(request)

    def _resolve_key(self) -> str | None:
        if not self._grok_policy_guard:
            raise GrokPolicyError()
        try:
            return self._token_source.access_token()
        except GrokTokenError as error:
            # Fixed messages only; the underlying error may describe local
            # file state and must not travel further than this adapter.
            raise AuthError(
                _TOKEN_ERROR_MESSAGES.get(
                    error.reason, "Grok sign-in is unavailable"
                ),
                provider="grok",
            ) from None

    def _classify_http_error(self, response: HttpResponse) -> ProviderError:
        error = super()._classify_http_error(response)
        body = getattr(error, "response_body", "")
        if entitlement_reason(response.status_code, body) is not None:
            # Raised before any stream output, so the rotating transport may
            # advance to the next auth profile without any replay risk.
            limited = UsageLimitError(
                "The Grok subscription is inactive or out of resources.",
                retry_after=error.retry_after,
                provider="grok",
            )
            limited.reason_code = GrokAuthReason.SUBSCRIPTION_INACTIVE.value
            return limited
        return error


__all__ = [
    "DEFAULT_SUBSCRIPTION_BASE_URL",
    "GROK_REASONING_EFFORTS",
    "GROK_SUBSCRIPTION_BASE_URL_ENV_VAR",
    "GROK_SUBSCRIPTION_TOKEN_REF",
    "GrokPolicyError",
    "GrokSubscriptionTransport",
]
