"""Pipeline orchestrator: assembles DataPackage from all data sources."""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from .models import DataPackage
from .ticker_resolver import resolve_ticker
from .financials import fetch_financials
from .news import fetch_news
from .filings import fetch_filings
from .social import fetch_social_sentiment
from .cnmarket import (
    cn_market_dependency_missing,
    detect_cn_market,
    fetch_cn_market_data,
)
from .research import build_research_brief

logger = logging.getLogger(__name__)


def _fetch_description(ticker: str) -> Optional[str]:
    """Extract company business description from yfinance, truncated to 500 chars."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker.upper().strip()).info or {}
        desc = info.get("longBusinessSummary", "")
        return desc[:500] if desc else None
    except Exception:
        return None


def build_data_package(ticker: str, deep_research: bool = True) -> DataPackage:
    """Build complete DataPackage for a stock ticker.

    Fetches data from all available sources (yfinance, edgartools, xAI).
    Each source is independent -- if one fails, the others still run.
    Failed sources add entries to DataPackage.warnings.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        deep_research: If True (default), run web search + LLM synthesis
            to produce a ResearchBrief. Set False to skip (v1 behavior).

    Returns:
        DataPackage with all available data.

    Raises:
        ValueError: If the ticker is invalid (cannot be resolved).
    """
    warnings: list[str] = []

    # Step 1: Validate ticker (DATA-01)
    is_valid, resolved_ticker, company_name = resolve_ticker(ticker)
    if not is_valid:
        raise ValueError(f"Invalid ticker: {ticker}")

    logger.info("Building data package for %s (%s)", resolved_ticker, company_name)

    # Step 2: Fetch company description
    description = _fetch_description(resolved_ticker)

    # Step 3: Fetch financial fundamentals (DATA-02)
    financials = fetch_financials(resolved_ticker)
    if financials is None:
        warnings.append("Financial fundamentals unavailable")

    # Step 4: Fetch SEC filings (DATA-03)
    filing_10k = fetch_filings(resolved_ticker, "10-K")
    if filing_10k is None:
        warnings.append("10-K filing unavailable")

    filing_10q = fetch_filings(resolved_ticker, "10-Q")
    if filing_10q is None:
        warnings.append("10-Q filing unavailable")

    # Step 5: Fetch news (DATA-04)
    news = fetch_news(resolved_ticker)
    if news is None:
        warnings.append("No recent news found")

    # Step 6: Fetch social sentiment (DATA-05)
    social = fetch_social_sentiment(resolved_ticker, company_name)
    if social is None:
        if not os.getenv("XAI_API_KEY"):
            warnings.append(
                "X/Twitter sentiment disabled: XAI_API_KEY not configured"
            )
        else:
            warnings.append("X/Twitter sentiment unavailable")

    # Step 7: China market data (A-share / HK tickers only; optional extra)
    cn_market = None
    if detect_cn_market(resolved_ticker) is not None:
        cn_market = fetch_cn_market_data(resolved_ticker, company_name)
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
        research_brief = build_research_brief(resolved_ticker, company_name, description)
        if research_brief is None:
            if not os.getenv("OPENAI_API_KEY"):
                warnings.append(
                    "Deep research disabled: OPENAI_API_KEY not configured"
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
