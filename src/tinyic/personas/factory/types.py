"""Value objects and provider seams for cited persona research.

The factory deliberately knows nothing about credentials or wire formats.  A
provider integration implements :class:`ResearchBackend`; tests and offline
callers can supply the same protocol without touching the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CallUsage:
    """Usage attributed to one backend operation.

    ``calls`` counts model/HTTP operations. ``search_calls`` is the budgeted
    search unit when known (provider tool invocations, or Kimi echo rounds) so
    a multi-search response is never mistaken for one search.
    """

    purpose: str
    model_ref: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None
    calls: int = 1
    search_calls: int | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "cached_tokens", "calls"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.search_calls is not None and (
            not isinstance(self.search_calls, int)
            or isinstance(self.search_calls, bool)
            or self.search_calls < 0
        ):
            raise ValueError("search_calls must be a non-negative integer or None")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("cost_usd must be non-negative or None")


@dataclass(frozen=True)
class UsageSummary:
    """Roll-up across search, synthesis, and verification calls."""

    calls: int
    search_calls: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float | None
    by_purpose: Mapping[str, Mapping[str, int | float | None]]

    @classmethod
    def from_records(
        cls, records: Sequence[CallUsage], *, search_calls: int
    ) -> "UsageSummary":
        buckets: dict[str, dict[str, int | float | None]] = {}
        known_cost = 0.0
        cost_reported = False
        cost_unknown = False
        for record in records:
            bucket = buckets.setdefault(
                record.purpose,
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            bucket["calls"] = int(bucket["calls"] or 0) + record.calls
            bucket["input_tokens"] = int(bucket["input_tokens"] or 0) + record.input_tokens
            bucket["output_tokens"] = int(bucket["output_tokens"] or 0) + record.output_tokens
            bucket["cached_tokens"] = int(bucket["cached_tokens"] or 0) + record.cached_tokens
            if record.cost_usd is None:
                cost_unknown = True
                bucket["cost_usd"] = None
            else:
                cost_reported = True
                known_cost += record.cost_usd
                if bucket["cost_usd"] is not None:
                    bucket["cost_usd"] = round(float(bucket["cost_usd"]) + record.cost_usd, 8)

        # A mixed priced/unpriced run must not present a misleading partial sum.
        total_cost = None if cost_unknown or not cost_reported else round(known_cost, 8)
        return cls(
            calls=sum(record.calls for record in records),
            search_calls=search_calls,
            input_tokens=sum(record.input_tokens for record in records),
            output_tokens=sum(record.output_tokens for record in records),
            cached_tokens=sum(record.cached_tokens for record in records),
            cost_usd=total_cost,
            by_purpose={key: dict(value) for key, value in sorted(buckets.items())},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "search_calls": self.search_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "cost_usd": self.cost_usd,
            "by_purpose": {key: dict(value) for key, value in self.by_purpose.items()},
        }


@dataclass(frozen=True)
class Evidence:
    """One provider-returned, citable public-record excerpt.

    The quote_eligible flag is true only for source-owned text, never for the
    research model's synthesized prose.
    """

    url: str
    title: str
    excerpt: str
    query: str = ""
    provider: str = ""
    source_type: str = "secondary"
    accessed: str | None = None
    quote_eligible: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.quote_eligible, bool):
            raise TypeError("quote_eligible must be a boolean")


@dataclass(frozen=True)
class SeedSource:
    """Curated search hint; TinyIC never fetches it directly."""

    title: str
    url: str
    source_type: str = "primary"
    kind: str = "url"


@dataclass(frozen=True)
class SearchQuery:
    angle: str
    text: str
    seed_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryPlan:
    investor_name: str
    queries: tuple[SearchQuery, ...]
    seeds: tuple[SeedSource, ...] = ()


@dataclass(frozen=True)
class SearchRequest:
    investor_name: str
    query: SearchQuery
    remaining_searches: int | None = None

    def __post_init__(self) -> None:
        if self.remaining_searches is not None and (
            not isinstance(self.remaining_searches, int)
            or isinstance(self.remaining_searches, bool)
            or self.remaining_searches < 1
        ):
            raise ValueError("remaining_searches must be a positive integer or None")


@dataclass(frozen=True)
class SearchResponse:
    evidence: tuple[Evidence, ...]
    usage: CallUsage | None = None
    budget_exhausted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not isinstance(self.budget_exhausted, bool):
            raise TypeError("budget_exhausted must be a boolean")


@dataclass(frozen=True)
class DossierSynthesisRequest:
    investor_name: str
    section_key: str
    section_title: str
    prompt: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class DossierSynthesisResponse:
    text: str
    usage: CallUsage | None = None


@dataclass(frozen=True)
class PersonaSynthesisRequest:
    investor_name: str
    slug: str
    prompt: str
    dossier_sections: Mapping[str, tuple[str, ...]]
    evidence: tuple[Evidence, ...]
    source_records: tuple[Mapping[str, Any], ...]
    generation: Mapping[str, Any]


@dataclass(frozen=True)
class PersonaSynthesisResponse:
    specification: Mapping[str, Any]
    usage: CallUsage | None = None


@dataclass(frozen=True)
class AtomicClaim:
    """One independently retainable fact submitted to the verifier."""

    claim_id: str
    text: str
    citations: tuple[int, ...] = ()
    path: tuple[str | int, ...] = ()
    quote: bool = False


@dataclass(frozen=True)
class VerificationRequest:
    investor_name: str
    prompt: str
    claims: tuple[AtomicClaim, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class VerificationResponse:
    """Evidence mappings for supported claims; omitted ids are unsupported."""

    supported: Mapping[str, tuple[int, ...]]
    usage: CallUsage | None = None


@runtime_checkable
class ResearchBackend(Protocol):
    """Only boundary through which the persona factory may call a provider."""

    model_ref: str
    provider: str

    def search(self, request: SearchRequest) -> SearchResponse:
        """Return structured evidence for one planned query."""

    def synthesize_dossier(
        self, request: DossierSynthesisRequest
    ) -> DossierSynthesisResponse:
        """Write one section using only ``request.evidence``."""

    def synthesize_persona(
        self, request: PersonaSynthesisRequest
    ) -> PersonaSynthesisResponse:
        """Return a complete TinyTroupe-compatible persona specification."""

    def verify(self, request: VerificationRequest) -> VerificationResponse:
        """Map each supported atomic claim to one or more ledger entries."""


@dataclass(frozen=True)
class ResearchRequest:
    investor_name: str
    output_dir: Path | str | None = None
    slug: str | None = None
    max_searches: int = 12
    force: bool = False


@dataclass(frozen=True)
class ResearchResult:
    investor_name: str
    slug: str
    agent_path: Path
    dossier_path: Path
    source_count: int
    domain_count: int
    quality: str
    usage: UsageSummary
    specification: Mapping[str, Any] = field(repr=False)


__all__ = [
    "AtomicClaim",
    "CallUsage",
    "DossierSynthesisRequest",
    "DossierSynthesisResponse",
    "Evidence",
    "PersonaSynthesisRequest",
    "PersonaSynthesisResponse",
    "QueryPlan",
    "ResearchBackend",
    "ResearchRequest",
    "ResearchResult",
    "SearchQuery",
    "SearchRequest",
    "SearchResponse",
    "SeedSource",
    "UsageSummary",
    "VerificationRequest",
    "VerificationResponse",
]
