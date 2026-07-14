"""Credential-aware construction and priority selection for research lanes."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ..adapters._http import HttpTransport
from ..binding import ModelBinding
from ..credentials import CredentialProvider
from ..types import AuthError, ProviderError
from ._base import UnsupportedResearchModelError
from .gemini import (
    GEMINI_BASE_URL,
    GEMINI_RESEARCH_MODEL,
    GEMINI_RESEARCH_MODEL_REF,
    GeminiResearchBackend,
)
from .kimi import KimiResearchBackend
from .responses import (
    GROK_BASE_URL,
    OPENAI_BASE_URL,
    GrokResearchBackend,
    OpenAIResearchBackend,
)


RESEARCH_PROVIDER_PRIORITY: tuple[str, ...] = (
    "openai",
    "grok",
    "google",
    "kimi",
)

_CREDENTIAL_REFS: Mapping[str, tuple[str, ...]] = {
    "openai": OpenAIResearchBackend.credential_refs,
    "grok": GrokResearchBackend.credential_refs,
    "google": GeminiResearchBackend.credential_refs,
    "kimi": KimiResearchBackend.credential_refs,
}


class UnsupportedResearchProviderError(ValueError):
    reason_code = "provider_not_search_capable"


class NoSearchCapableLaneError(RuntimeError):
    """No supplied binding has a usable API-key search credential."""

    reason_code = "no_search_capable_lane"

    def __init__(self, providers: Iterable[str] = ()) -> None:
        self.providers = tuple(dict.fromkeys(providers))
        onboard = ", ".join(RESEARCH_PROVIDER_PRIORITY)
        super().__init__(
            "no_search_capable_lane: onboard an API-key credential for one of "
            f"{onboard}"
        )


def _has_provider_key(provider: str, credentials: CredentialProvider) -> bool:
    for ref in _CREDENTIAL_REFS[provider]:
        try:
            value = credentials(ref)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return True
    return False


def _api_key_credentials(
    binding: ModelBinding, credentials: CredentialProvider
) -> CredentialProvider:
    """Resolve an AuthManager candidate, or validate a direct credential seam."""

    candidates = getattr(credentials, "candidates", None)
    if callable(candidates):
        # AuthManager owns named-profile and auth_order precedence. Research
        # deliberately rejects subscription runtimes: the four search APIs are
        # Platform/API-key surfaces and their costs must be attributable.
        resolved = candidates(binding)
        for candidate in resolved:
            lane = getattr(candidate, "lane", None)
            lane_value = getattr(lane, "value", lane)
            if lane_value != "api_key":
                continue
            if _has_provider_key(binding.provider.casefold(), candidate):
                return candidate
        raise AuthError(
            "no API-key credential is configured for this research provider",
            provider=binding.provider.casefold(),
        )
    if _has_provider_key(binding.provider.casefold(), credentials):
        return credentials
    raise AuthError(
        f"missing credential for {binding.provider.casefold()} research",
        provider=binding.provider.casefold(),
    )


def make_research_backend(
    binding: ModelBinding,
    credentials: CredentialProvider,
    *,
    http: HttpTransport | None = None,
    base_url: str | None = None,
    max_searches: int | None = None,
    **kwargs: Any,
):
    """Build the explicit ``--model`` research backend for one binding.

    Construction validates that the selected lane is an API-key lane but makes
    no network call. Provider HTTP remains fully injectable via ``http``.
    """

    provider = binding.provider.casefold()
    if provider not in RESEARCH_PROVIDER_PRIORITY:
        raise UnsupportedResearchProviderError(
            f"provider {binding.provider!r} has no persona-research search tool"
        )
    if provider == "google" and binding.model != GEMINI_RESEARCH_MODEL:
        # Gemini 3 can issue an unbounded number of internal Search queries in
        # one grounded prompt. Reject it before even resolving credentials;
        # Gemini 2.5's per-grounded-prompt billing gives the factory an exact
        # run-wide unit that it can cap before every request.
        raise UnsupportedResearchModelError(
            f"{binding.model_ref} cannot enforce persona research's search "
            f"budget; use {GEMINI_RESEARCH_MODEL_REF}"
        )
    resolved_credentials = _api_key_credentials(binding, credentials)
    classes = {
        "openai": (OpenAIResearchBackend, OPENAI_BASE_URL),
        "grok": (GrokResearchBackend, GROK_BASE_URL),
        "google": (GeminiResearchBackend, GEMINI_BASE_URL),
        # ``None`` lets KimiResearchBackend honor MOONSHOT_BASE_URL (China
        # endpoint parity with the ordinary Kimi adapter).
        "kimi": (KimiResearchBackend, None),
    }
    backend_class, default_base_url = classes[provider]
    if provider == "kimi" and max_searches is not None:
        # Kimi's client-executed $web_search echo loop can make several HTTP
        # rounds for one logical query.  Carry the CLI's budget into that loop
        # so it caps total search rounds across the entire factory run.
        kwargs["max_search_rounds"] = max_searches
    return backend_class(
        binding,
        resolved_credentials,
        base_url=base_url or default_base_url,
        http=http,
        **kwargs,
    )


def _binding_values(
    bindings: Iterable[ModelBinding] | Mapping[str, ModelBinding],
) -> tuple[ModelBinding, ...]:
    values = bindings.values() if isinstance(bindings, Mapping) else bindings
    result = tuple(value for value in values if isinstance(value, ModelBinding))
    return result


def select_research_backend(
    bindings: Iterable[ModelBinding] | Mapping[str, ModelBinding],
    credentials: CredentialProvider,
    *,
    http: HttpTransport | None = None,
    http_factory: Callable[[ModelBinding], HttpTransport] | None = None,
    **kwargs: Any,
):
    """Select the first usable lane in ``openai → grok → google → kimi`` order."""

    if http is not None and http_factory is not None:
        raise ValueError("pass http or http_factory, not both")
    values = _binding_values(bindings)
    by_provider: dict[str, list[ModelBinding]] = {}
    for binding in values:
        by_provider.setdefault(binding.provider.casefold(), []).append(binding)

    tried: list[str] = []
    for provider in RESEARCH_PROVIDER_PRIORITY:
        for binding in by_provider.get(provider, ()):
            tried.append(provider)
            if provider == "google" and binding.model != GEMINI_RESEARCH_MODEL:
                continue
            try:
                resolved_credentials = _api_key_credentials(binding, credentials)
            except ProviderError:
                continue
            chosen_http = http_factory(binding) if http_factory is not None else http
            try:
                return make_research_backend(
                    binding,
                    resolved_credentials,
                    http=chosen_http,
                    **kwargs,
                )
            except ProviderError:
                continue
    raise NoSearchCapableLaneError(tried)


__all__ = [
    "NoSearchCapableLaneError",
    "RESEARCH_PROVIDER_PRIORITY",
    "UnsupportedResearchProviderError",
    "UnsupportedResearchModelError",
    "make_research_backend",
    "select_research_backend",
]
