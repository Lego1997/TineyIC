"""The model catalog service: static registry catalogs + explicit live refresh.

One normalized view of "which models can this install run", consumed by
``tinyic models`` and the onboarding wizard's model step.  The **static** side
is the provider registry's shipped catalogs (:mod:`tinyic.models.registry`)
enriched with pinned context/output metadata; the **live** side queries each
provider's model-listing endpoint and merges the result over the static view.

Live refresh rules
------------------

* Refresh happens **only on explicit request** (``tinyic models --refresh`` or
  the wizard's refresh action) — the static snapshot never touches the network.
* Credentials ride the same seams the adapters use: an
  :class:`~tinyic.auth.manager.AuthManager` (stored profiles + env fallback) or
  any :class:`~tinyic.models.credentials.CredentialProvider`.  Secrets go only
  into request *headers* (never URLs) and never into listings, warnings, or
  errors.
* Each provider degrades **independently**: a missing credential, network
  fault, HTTP error, or unparseable body falls back to that provider's static
  catalog with a stable warning code, while other providers still refresh.

Per-provider live sources
-------------------------

* ``anthropic`` — ``GET https://api.anthropic.com/v1/models`` (``x-api-key`` +
  ``anthropic-version``); metadata-rich (``max_input_tokens``, ``max_tokens``,
  ``capabilities.thinking``).
* ``google`` — ``GET {generativelanguage}/v1beta/models`` with the
  ``x-goog-api-key`` header (equivalent to the documented ``?key=`` query
  parameter, chosen so the secret never appears in a URL); metadata-rich
  (``inputTokenLimit``, ``outputTokenLimit``, ``thinking``).
* ``grok`` — ``GET https://api.x.ai/v1/language-models`` (Bearer
  ``XAI_API_KEY``); metadata-rich (``context_length``, aliases, pricing).
* ``kimi`` — ``GET {moonshot base}/models`` (Bearer ``MOONSHOT_API_KEY``;
  honors ``MOONSHOT_BASE_URL`` for the China endpoint); metadata-rich
  (``context_length``, ``supports_reasoning``).
* ``ollama`` — ``GET http://localhost:11434/api/tags`` (no auth): the locally
  installed models.
* ``openai`` — **static only**: OpenAI's ``GET /v1/models`` returns bare model
  ids with no context/thinking metadata, so a live merge would add nothing;
  a refresh request degrades to the static catalog with ``refresh_unsupported``.

Static entries a live listing does not mention are *kept* (source ``static``)
rather than dropped, so a partial or lagging provider listing can never make a
shipped model disappear from the pickers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .adapters._http import HttpConnectionError, HttpRequest, HttpTransport
from .adapters.anthropic_messages import ANTHROPIC_VERSION
from .credentials import CredentialProvider

CATALOG_SCHEMA_VERSION = 1

#: Canonical provider presentation order (CLI table, wizard rows).
PROVIDER_ORDER: tuple[str, ...] = (
    "openai",
    "anthropic",
    "grok",
    "google",
    "kimi",
    "ollama",
)

ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models?limit=100"
GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GROK_MODELS_URL = "https://api.x.ai/v1/language-models"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

#: Stable warning codes a degraded per-provider refresh can carry.
WARNING_MISSING_CREDENTIAL = "missing_credential"
WARNING_REFRESH_FAILED = "refresh_failed"
WARNING_REFRESH_UNSUPPORTED = "refresh_unsupported"

#: Pinned context/output metadata for the shipped static catalogs (2026-07-14,
#: from official provider docs).  ``None`` means the number is not pinned; a
#: live refresh fills what the provider's listing publishes.
_STATIC_METADATA: dict[tuple[str, str], tuple[int | None, int | None]] = {
    ("openai", "gpt-5.6-sol"): (1_050_000, 128_000),
    ("openai", "gpt-5.6-terra"): (None, None),
    ("openai", "gpt-5.6-luna"): (None, None),
    ("openai", "gpt-5.2"): (400_000, None),
    ("anthropic", "claude-fable-5"): (1_000_000, 128_000),
    ("anthropic", "claude-opus-4-8"): (1_000_000, 128_000),
    ("anthropic", "claude-sonnet-5"): (1_000_000, 128_000),
    ("anthropic", "claude-haiku-4-5"): (200_000, 64_000),
    ("grok", "grok-4.5"): (500_000, None),
    ("grok", "grok-4.3"): (1_000_000, None),
    ("grok", "grok-4.20"): (1_000_000, None),
    ("kimi", "kimi-k2.6"): (256_000, None),
    ("kimi", "kimi-k2.5"): (256_000, None),
}


class CatalogRefreshError(Exception):
    """One provider's live refresh failed (secret-free; carries only a code)."""

    def __init__(self, warning: str = WARNING_REFRESH_FAILED) -> None:
        super().__init__(warning)
        self.warning = warning


@dataclass(frozen=True)
class ModelListing:
    """One normalized catalog entry (static or live-enriched)."""

    model_id: str
    provider: str
    context_window: int | None = None
    max_output: int | None = None
    thinking_capable: bool | None = None
    source: str = "static"  # "static" | "live"

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "context_window": self.context_window,
            "max_output": self.max_output,
            "thinking_capable": self.thinking_capable,
            "source": self.source,
        }


@dataclass(frozen=True)
class ProviderCatalog:
    """One provider's merged catalog plus its refresh outcome."""

    provider: str
    listings: tuple[ModelListing, ...]
    refreshed: bool = False
    warning: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "refreshed": self.refreshed,
            "warning": self.warning,
            "models": [listing.to_dict() for listing in self.listings],
        }


@dataclass(frozen=True)
class _LiveEntry:
    """A leniently parsed row from one provider's live listing."""

    model_id: str
    context_window: int | None = None
    max_output: int | None = None
    thinking_capable: bool | None = None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


class CatalogService:
    """Merge the static registry catalogs with optional per-provider live data.

    All network access flows through an injected :class:`HttpTransport` (a
    fake in tests; a lazily built ``HttpxTransport`` in production) and only
    runs inside :meth:`snapshot` with ``refresh=True``.
    """

    def __init__(
        self,
        *,
        registry=None,
        credentials: CredentialProvider | None = None,
        http: HttpTransport | None = None,
        environ: Mapping[str, str] | None = None,
        config_path: str | None = None,
    ) -> None:
        if registry is None:
            from .registry import default_registry

            registry = default_registry()
        self._registry = registry
        self._credentials = credentials
        self._http = http
        self._environ = environ
        self._config_path = config_path

    # -- public API --------------------------------------------------------- #

    def snapshot(
        self, provider: str | None = None, *, refresh: bool = False
    ) -> tuple[ProviderCatalog, ...]:
        """The merged catalog per provider, in canonical presentation order.

        ``provider`` limits the snapshot to one provider (case-insensitive;
        unknown names raise ``KeyError`` via the registry).  ``refresh=True``
        performs the explicit live refresh; failures degrade per provider.
        """
        if provider is not None:
            names: tuple[str, ...] = (self._registry.get(provider).name.lower(),)
        else:
            registered = set(self._registry.names())
            ordered = [name for name in PROVIDER_ORDER if name in registered]
            ordered.extend(sorted(registered - set(ordered)))
            names = tuple(ordered)
        return tuple(self._one(name, refresh=refresh) for name in names)

    def can_refresh(self, provider: str) -> bool:
        """Whether an explicit refresh could do anything for ``provider``.

        Local Ollama always can (unauthenticated enumeration); OpenAI never
        can (bare-id listing); API-backed providers need a resolvable key.
        """
        provider = provider.strip().lower()
        if provider == "ollama":
            return True
        if provider == "openai":
            return False
        try:
            return self._api_key(provider) is not None
        except Exception:
            return False

    # -- assembly ------------------------------------------------------------ #

    def _one(self, provider: str, *, refresh: bool) -> ProviderCatalog:
        static = self._static_listings(provider)
        if not refresh:
            return ProviderCatalog(provider, tuple(static))
        fetcher = self._fetchers().get(provider)
        if fetcher is None:
            return ProviderCatalog(
                provider, tuple(static), warning=WARNING_REFRESH_UNSUPPORTED
            )
        try:
            entries = fetcher()
        except CatalogRefreshError as exc:
            return ProviderCatalog(provider, tuple(static), warning=exc.warning)
        except Exception:
            return ProviderCatalog(
                provider, tuple(static), warning=WARNING_REFRESH_FAILED
            )
        return ProviderCatalog(
            provider, self._merge(provider, static, entries), refreshed=True
        )

    def _static_listings(self, provider: str) -> list[ModelListing]:
        descriptor = self._registry.get(provider)
        listings: list[ModelListing] = []
        for model_id in descriptor.catalog():
            context, max_output = _STATIC_METADATA.get(
                (provider, model_id), (None, None)
            )
            listings.append(
                ModelListing(
                    model_id=model_id,
                    provider=provider,
                    context_window=context,
                    max_output=max_output,
                    thinking_capable=descriptor.thinking_profile(
                        model_id
                    ).accepts_thinking,
                    source="static",
                )
            )
        return listings

    @staticmethod
    def _merge(
        provider: str, static: list[ModelListing], entries: list[_LiveEntry]
    ) -> tuple[ModelListing, ...]:
        by_id = {listing.model_id: listing for listing in static}
        order = [listing.model_id for listing in static]
        for entry in entries:
            base = by_id.get(entry.model_id)
            if base is None:
                order.append(entry.model_id)
            by_id[entry.model_id] = ModelListing(
                model_id=entry.model_id,
                provider=provider,
                context_window=(
                    entry.context_window
                    if entry.context_window is not None
                    else (base.context_window if base else None)
                ),
                max_output=(
                    entry.max_output
                    if entry.max_output is not None
                    else (base.max_output if base else None)
                ),
                thinking_capable=(
                    entry.thinking_capable
                    if entry.thinking_capable is not None
                    else (base.thinking_capable if base else None)
                ),
                source="live",
            )
        return tuple(by_id[model_id] for model_id in order)

    # -- live fetchers -------------------------------------------------------- #

    def _fetchers(self) -> dict[str, Callable[[], list[_LiveEntry]]]:
        return {
            "anthropic": self._live_anthropic,
            "google": self._live_google,
            "grok": self._live_grok,
            "kimi": self._live_kimi,
            "ollama": self._live_ollama,
        }

    def _live_anthropic(self) -> list[_LiveEntry]:
        key = self._require_key("anthropic")
        payload = self._get_json(
            ANTHROPIC_MODELS_URL,
            headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
        )
        entries: list[_LiveEntry] = []
        for row in self._rows(payload, "data"):
            model_id = row.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            capabilities = row.get("capabilities")
            thinking = (
                _bool_or_none(capabilities.get("thinking"))
                if isinstance(capabilities, Mapping)
                else None
            )
            entries.append(
                _LiveEntry(
                    model_id=model_id,
                    context_window=_int_or_none(row.get("max_input_tokens")),
                    max_output=_int_or_none(row.get("max_tokens")),
                    thinking_capable=thinking,
                )
            )
        return entries

    def _live_google(self) -> list[_LiveEntry]:
        key = self._require_key("google")
        # Key rides the x-goog-api-key header, never the URL (see module doc).
        payload = self._get_json(GOOGLE_MODELS_URL, headers={"x-goog-api-key": key})
        entries: list[_LiveEntry] = []
        for row in self._rows(payload, "models"):
            name = row.get("name")
            if not isinstance(name, str) or not name:
                continue
            model_id = name.removeprefix("models/")
            entries.append(
                _LiveEntry(
                    model_id=model_id,
                    context_window=_int_or_none(row.get("inputTokenLimit")),
                    max_output=_int_or_none(row.get("outputTokenLimit")),
                    thinking_capable=_bool_or_none(row.get("thinking")),
                )
            )
        return entries

    def _live_grok(self) -> list[_LiveEntry]:
        key = self._require_key("grok")
        payload = self._get_json(
            GROK_MODELS_URL, headers={"Authorization": f"Bearer {key}"}
        )
        entries: list[_LiveEntry] = []
        for row in [*self._rows(payload, "models"), *self._rows(payload, "data")]:
            model_id = row.get("id") or row.get("model")
            if not isinstance(model_id, str) or not model_id:
                continue
            entries.append(
                _LiveEntry(
                    model_id=model_id,
                    context_window=_int_or_none(
                        row.get("context_length") or row.get("context_window")
                    ),
                    max_output=_int_or_none(row.get("max_output_tokens")),
                )
            )
        return entries

    def _live_kimi(self) -> list[_LiveEntry]:
        key = self._require_key("kimi")
        payload = self._get_json(
            f"{self._kimi_base_url()}/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        entries: list[_LiveEntry] = []
        for row in [*self._rows(payload, "data"), *self._rows(payload, "models")]:
            model_id = row.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            entries.append(
                _LiveEntry(
                    model_id=model_id,
                    context_window=_int_or_none(row.get("context_length")),
                    thinking_capable=_bool_or_none(row.get("supports_reasoning")),
                )
            )
        return entries

    def _live_ollama(self) -> list[_LiveEntry]:
        payload = self._get_json(OLLAMA_TAGS_URL, headers={})
        entries: list[_LiveEntry] = []
        for row in self._rows(payload, "models"):
            name = row.get("name") or row.get("model")
            if isinstance(name, str) and name:
                entries.append(_LiveEntry(model_id=name))
        return entries

    # -- seams ----------------------------------------------------------------- #

    def _kimi_base_url(self) -> str:
        import os

        from .adapters.kimi_chat import DEFAULT_BASE_URL
        from .registry import KIMI_BASE_URL_ENV_VAR

        environ = os.environ if self._environ is None else self._environ
        return (environ.get(KIMI_BASE_URL_ENV_VAR) or DEFAULT_BASE_URL).rstrip("/")

    def _require_key(self, provider: str) -> str:
        key = self._api_key(provider)
        if key is None:
            raise CatalogRefreshError(WARNING_MISSING_CREDENTIAL)
        return key

    def _api_key(self, provider: str) -> str | None:
        """Resolve an API key through the adapters' own credential seams."""
        from tinyic.auth.manager import DEFAULT_PROVIDER_ENV_REFS, AuthManager

        credentials = self._resolve_credentials()
        if isinstance(credentials, AuthManager):
            from tinyic.auth.profiles import AuthLane

            binding = self._inventory_binding(provider)
            try:
                candidates = credentials.candidates(binding)
            except Exception:
                candidates = ()
            for candidate in candidates:
                if candidate.lane is not AuthLane.API_KEY:
                    continue
                secret = candidate(candidate.credential_ref or candidate.ref)
                if secret:
                    return secret
            return None
        for ref in DEFAULT_PROVIDER_ENV_REFS.get(provider, ()):
            secret = credentials(ref)
            if secret:
                return secret
        return None

    def _inventory_binding(self, provider: str):
        from .binding import ModelBinding

        catalog = self._registry.get(provider).catalog()
        model = catalog[0] if catalog else "catalog-probe"
        return ModelBinding(f"{provider}/{model}")

    def _resolve_credentials(self) -> CredentialProvider:
        if self._credentials is None:
            from tinyic.auth.manager import AuthManager

            self._credentials = AuthManager.from_config(self._config_path)
        return self._credentials

    def _get_json(self, url: str, *, headers: Mapping[str, str]) -> Any:
        response = None
        try:
            response = self._transport().send(
                HttpRequest(method="GET", url=url, headers=headers, timeout=15.0)
            )
            if response.status_code != 200:
                raise CatalogRefreshError(WARNING_REFRESH_FAILED)
            return json.loads(response.read_text())
        except CatalogRefreshError:
            raise
        except (HttpConnectionError, ValueError):
            raise CatalogRefreshError(WARNING_REFRESH_FAILED) from None
        finally:
            if response is not None:
                response.close()

    @staticmethod
    def _rows(payload: Any, key: str) -> list[Mapping[str, Any]]:
        if not isinstance(payload, Mapping):
            return []
        rows = payload.get(key)
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, Mapping)]

    def _transport(self) -> HttpTransport:
        if self._http is None:
            from .adapters._http import HttpxTransport

            self._http = HttpxTransport(timeout=15.0)
        return self._http


# ------------------------------------------------------------------------- #
# Rendering — the ``tinyic models`` machine document and human table
# ------------------------------------------------------------------------- #

def _iso_millis(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("catalog clock must return a timezone-aware datetime")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def to_document(
    catalogs: tuple[ProviderCatalog, ...],
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """The stable, secret-free ``tinyic models --json`` schema-v1 document."""
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "generated_at": _iso_millis(now),
        "providers": [catalog.to_dict() for catalog in catalogs],
    }


def _fmt_int(value: int | None) -> str:
    return f"{value:,}" if isinstance(value, int) else "-"


def _fmt_thinking(value: bool | None) -> str:
    if value is None:
        return "?"
    return "yes" if value else "no"


def render_human(catalogs: tuple[ProviderCatalog, ...]) -> str:
    """Render the aligned, doctor-style human table (plus degrade warnings)."""
    refreshed = any(catalog.refreshed for catalog in catalogs)
    heading = "TinyIC models" + (" (live-refreshed)" if refreshed else "")
    lines = [
        heading,
        f"{'PROVIDER':<10} {'MODEL':<28} {'CONTEXT':>10} {'MAX OUT':>9} "
        f"{'THINKING':<8} SOURCE",
    ]
    for catalog in catalogs:
        for listing in catalog.listings:
            lines.append(
                f"{listing.provider:<10} {listing.model_id:<28} "
                f"{_fmt_int(listing.context_window):>10} "
                f"{_fmt_int(listing.max_output):>9} "
                f"{_fmt_thinking(listing.thinking_capable):<8} {listing.source}"
            )
    for catalog in catalogs:
        if catalog.warning:
            lines.append(
                f"[WARN] {catalog.provider}: live refresh degraded "
                f"({catalog.warning}) — showing the static catalog."
            )
    return "\n".join(lines)


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "CatalogRefreshError",
    "CatalogService",
    "ModelListing",
    "PROVIDER_ORDER",
    "ProviderCatalog",
    "render_human",
    "to_document",
]
