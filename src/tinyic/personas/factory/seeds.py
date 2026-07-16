"""Canonical public-record search hints for investor-persona research.

These URLs are passed to search-capable providers as hints.  The local process
does not fetch them, which keeps the core offline-testable and avoids brittle
publisher scraping.
"""

from __future__ import annotations

import re

from .types import QueryPlan, SearchQuery, SeedSource


EDGAR_13F_SEED = SeedSource(
    title="SEC EDGAR full-text search (13F-HR)",
    url="https://efts.sec.gov/LATEST/search-index",
    source_type="primary",
    kind="edgar_13f",
)

CANONICAL_SEEDS: dict[str, tuple[SeedSource, ...]] = {
    "warren_buffett": (
        SeedSource(
            "Berkshire Hathaway shareholder letters",
            "https://www.berkshirehathaway.com/letters/letters.html",
        ),
        SeedSource(
            "The Superinvestors of Graham-and-Doddsville",
            "https://www.grahamanddoddsville.net/files/buffett__superinvestors_of_graham_and_doddsville.pdf",
        ),
        EDGAR_13F_SEED,
    ),
    "howard_marks": (
        SeedSource(
            "Oaktree Capital memos",
            "https://www.oaktreecapital.com/insights/memos",
        ),
        EDGAR_13F_SEED,
    ),
    "charlie_munger": (
        SeedSource(
            "A Lesson on Worldly Wisdom",
            "https://fs.blog/great-talks/a-lesson-on-worldly-wisdom/",
        ),
        SeedSource(
            "Charlie Munger archive",
            "https://worldlypartners.com/charlie-munger-archive/",
        ),
        EDGAR_13F_SEED,
    ),
    "li_lu": (
        SeedSource(
            "Li Lu's 2006 Columbia lecture",
            "https://brianlangis.wordpress.com/wp-content/uploads/2018/03/li-lus-talk-at-columbia-2006.pdf",
        ),
        EDGAR_13F_SEED,
    ),
}

_ANGLES: tuple[tuple[str, str], ...] = (
    (
        "philosophy",
        "{name} investment philosophy principles primary sources letters memos lectures",
    ),
    (
        "decision_process",
        "{name} investment decision process checklist valuation methodology public record",
    ),
    (
        "risk",
        "{name} risk discipline red flags downside permanent loss public statements",
    ),
    (
        "track_record",
        "{name} public investment positions track record filings contemporaneous sources",
    ),
    (
        "voice",
        "{name} verbatim investment quotes transcript interview memo source",
    ),
    (
        "criticism",
        "{name} investment approach criticism limitations counterpoints reputable sources",
    ),
)


def canonical_name_key(name: str) -> str:
    """Normalize a public name for seed lookup, without putting it in a URL."""
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def canonical_seeds(name: str) -> tuple[SeedSource, ...]:
    """Return curated hints plus the generic EDGAR seam for unknown names."""
    return CANONICAL_SEEDS.get(canonical_name_key(name), (EDGAR_13F_SEED,))


def plan_queries(name: str, *, max_searches: int = 12) -> QueryPlan:
    """Build the deterministic six-angle STORM-style research plan."""
    cleaned = " ".join(str(name).split())
    if not cleaned:
        raise ValueError("investor_name must not be blank")
    if not isinstance(max_searches, int) or isinstance(max_searches, bool):
        raise ValueError("max_searches must be an integer")
    if not 1 <= max_searches <= 16:
        raise ValueError("max_searches must be between 1 and 16")
    seeds = canonical_seeds(cleaned)
    seed_urls = tuple(seed.url for seed in seeds)
    queries = tuple(
        SearchQuery(angle, template.format(name=cleaned), seed_urls)
        for angle, template in _ANGLES[:max_searches]
    )
    return QueryPlan(cleaned, queries, seeds)


__all__ = [
    "CANONICAL_SEEDS",
    "EDGAR_13F_SEED",
    "canonical_name_key",
    "canonical_seeds",
    "plan_queries",
]
