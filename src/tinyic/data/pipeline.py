"""Pipeline orchestrator: assembles DataPackage from all data sources."""

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional

from ..models.credentials import CredentialProvider, EnvCredentialProvider
from .models import DataPackage
from .ticker_resolver import resolve_ticker
from .financials import fetch_financials
from .news import fetch_news
from .filings import fetch_filings
from .social import fetch_social_sentiment, social_lane_available
from .cnmarket import (
    cn_market_dependency_missing,
    detect_cn_market,
    fetch_cn_market_data,
)
from .research import build_research_brief, research_lane_available

logger = logging.getLogger(__name__)


def _run_checkpoint(checkpoint: Callable[[], None] | None) -> None:
    if checkpoint is not None:
        checkpoint()


def _checked_source_call(
    checkpoint: Callable[[], None] | None,
    operation,
    /,
    *args,
):
    """Bracket one sequential source call with the optional stop checkpoint."""
    _run_checkpoint(checkpoint)
    try:
        return operation(*args)
    finally:
        # Deliberately outside any graceful-degradation catch in a source: a
        # DebateStopRequested raised here must prevent the next network call.
        _run_checkpoint(checkpoint)


def _fetch_description(ticker: str) -> Optional[str]:
    """Extract company business description from yfinance, truncated to 500 chars."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker.upper().strip()).info or {}
        desc = info.get("longBusinessSummary", "")
        return desc[:500] if desc else None
    except Exception:
        return None


def build_data_package(
    ticker: str,
    deep_research: bool = True,
    *,
    checkpoint: Callable[[], None] | None = None,
    credentials: Optional[CredentialProvider] = None,
    aggregator_client: Any = None,
) -> DataPackage:
    """Build complete DataPackage for a stock ticker.

    Fetches data from all available sources (yfinance, edgartools, xAI).
    Each source is independent -- if one fails, the others still run.
    Failed sources add entries to DataPackage.warnings.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        deep_research: If True (default), run web search + LLM synthesis
            to produce a ResearchBrief. Set False to skip (v1 behavior).
        checkpoint: Optional callback invoked before and after every sequential
            source call. It may raise to stop before subsequent network work.
        credentials: Credential provider for the API-key-gated sources (social
            sentiment, deep research). Defaults to the process environment; the
            debate hands its ``AuthManager`` so the selected lanes are honored.
        aggregator_client: Optional binding client the deep-research synthesis
            chat call is routed through (an ordinary chat a subscription lane
            can serve); ``None`` keeps the legacy configured client.

    Returns:
        DataPackage with all available data.

    Raises:
        ValueError: If the ticker is invalid (cannot be resolved).
    """
    provider_credentials = (
        credentials if credentials is not None else EnvCredentialProvider()
    )
    warnings: list[str] = []

    # Step 1: Validate ticker (DATA-01)
    is_valid, resolved_ticker, company_name = _checked_source_call(
        checkpoint,
        resolve_ticker,
        ticker,
    )
    if not is_valid:
        raise ValueError(f"Invalid ticker: {ticker}")

    logger.info("Building data package for %s (%s)", resolved_ticker, company_name)

    # Disclose low-confidence name/fuzzy resolution: when the input did not
    # match the resolved symbol it was bound by search (e.g. "SpaceX" -> SPCX),
    # not by an exact ticker. Personas see this via DataPackage.warnings.
    if ticker.strip().upper() != resolved_ticker:
        warnings.append(
            f"Entity resolution unverified: '{ticker}' was matched by "
            f"name/fuzzy search to {resolved_ticker} ({company_name}); pass the "
            f"exact ticker symbol to skip name resolution"
        )

    # Step 2: Fetch company description
    description = _checked_source_call(
        checkpoint,
        _fetch_description,
        resolved_ticker,
    )

    # Step 3: Fetch financial fundamentals (DATA-02)
    financials = _checked_source_call(
        checkpoint,
        fetch_financials,
        resolved_ticker,
    )
    if financials is None:
        warnings.append("Financial fundamentals unavailable")

    # Step 4: Fetch SEC filings (DATA-03)
    filing_10k = _checked_source_call(
        checkpoint,
        fetch_filings,
        resolved_ticker,
        "10-K",
    )
    if filing_10k is None:
        warnings.append("10-K filing unavailable")

    filing_10q = _checked_source_call(
        checkpoint,
        fetch_filings,
        resolved_ticker,
        "10-Q",
    )
    if filing_10q is None:
        warnings.append("10-Q filing unavailable")

    # Cross-source coherence: market data with zero SEC filings is the tell for
    # a fresh IPO, a foreign private issuer, or a misresolved entity. Surface it
    # so personas with pre-listing training cutoffs don't read genuine data as
    # fabricated. The text carries "financial", so _data_ready_payload attaches
    # it to the financials source and marks it degraded.
    if financials is not None and filing_10k is None and filing_10q is None:
        detail = getattr(financials, "listing_note", None) or (
            "possible recent IPO, foreign private issuer, or misresolved entity"
        )
        warnings.append(
            f"Financial data caution: market data exists for {resolved_ticker} "
            f"but no SEC 10-K/10-Q filing was found -- {detail}; treat "
            f"fundamentals as unverified"
        )

    # Step 5: Fetch news (DATA-04)
    news = _checked_source_call(
        checkpoint,
        fetch_news,
        resolved_ticker,
    )
    if news is None:
        warnings.append("No recent news found")

    # Step 6: Fetch social sentiment (DATA-05)
    social = _checked_source_call(
        checkpoint,
        fetch_social_sentiment,
        resolved_ticker,
        company_name,
        provider_credentials,
    )
    if social is None:
        if not social_lane_available(provider_credentials):
            warnings.append(
                "X/Twitter sentiment disabled: grok API-key lane not "
                "configured (subscription lanes cannot serve x_search)"
            )
        else:
            warnings.append(
                "X/Twitter sentiment unavailable (grok API-key lane request "
                "failed)"
            )

    # Step 7: China market data (A-share / HK tickers only; optional extra)
    cn_market = None
    market = _checked_source_call(
        checkpoint,
        detect_cn_market,
        resolved_ticker,
    )
    if market is not None:
        cn_market = _checked_source_call(
            checkpoint,
            fetch_cn_market_data,
            resolved_ticker,
            company_name,
        )
        if cn_market is None:
            if cn_market_dependency_missing():
                warnings.append(
                    "China market data disabled: install the cn extra "
                    "(uv sync --extra cn)"
                )
            else:
                warnings.append("China market data unavailable")
        elif cn_market.failed_sections:
            warnings.append(
                "China market data partial: "
                + ", ".join(cn_market.failed_sections)
                + " unavailable"
            )

    # Step 8: Deep research (DATA-06) -- optional, default enabled
    research_brief = None
    if deep_research:
        research_brief = _checked_source_call(
            checkpoint,
            build_research_brief,
            resolved_ticker,
            company_name,
            description,
            provider_credentials,
            aggregator_client,
        )
        if research_brief is None:
            if not research_lane_available(provider_credentials):
                warnings.append(
                    "Deep research disabled: OpenAI API-key lane not "
                    "configured (subscription lanes cannot serve hosted web "
                    "search)"
                )
            else:
                warnings.append("Deep research unavailable")

    # Step 9: Assemble DataPackage
    package = DataPackage(
        ticker=resolved_ticker,
        company_name=company_name,
        description=description,
        fetched_at=datetime.now(timezone.utc),
        financials=financials,
        filing_10k=filing_10k,
        filing_10q=filing_10q,
        news=news,
        social=social,
        cn_market=cn_market,
        research_brief=research_brief,
        warnings=warnings,
    )

    ctx_str = package.to_context_string()
    logger.info(
        "DataPackage for %s: %d chars, %d warnings",
        ticker, len(ctx_str), len(warnings),
    )
    if warnings:
        for w in warnings:
            logger.warning("DataPackage %s: %s", ticker, w)

    return package
