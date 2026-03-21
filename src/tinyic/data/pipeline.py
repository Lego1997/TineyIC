"""Pipeline orchestrator: assembles DataPackage from all data sources."""

import logging
from datetime import datetime
from typing import Optional

from .models import DataPackage
from .ticker_resolver import resolve_ticker
from .financials import fetch_financials
from .news import fetch_news
from .filings import fetch_filings
from .social import fetch_social_sentiment

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


def build_data_package(ticker: str) -> DataPackage:
    """Build complete DataPackage for a stock ticker.

    Fetches data from all available sources (yfinance, edgartools, xAI).
    Each source is independent -- if one fails, the others still run.
    Failed sources add entries to DataPackage.warnings.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").

    Returns:
        DataPackage with all available data.

    Raises:
        ValueError: If the ticker is invalid (cannot be resolved).
    """
    warnings: list[str] = []

    # Step 1: Validate ticker (DATA-01)
    is_valid, company_name = resolve_ticker(ticker)
    if not is_valid:
        raise ValueError(f"Invalid ticker: {ticker}")

    logger.info("Building data package for %s (%s)", ticker, company_name)

    # Step 2: Fetch company description
    description = _fetch_description(ticker)

    # Step 3: Fetch financial fundamentals (DATA-02)
    financials = fetch_financials(ticker)
    if financials is None:
        warnings.append("Financial fundamentals unavailable")

    # Step 4: Fetch SEC filings (DATA-03)
    filing_10k = fetch_filings(ticker, "10-K")
    if filing_10k is None:
        warnings.append("10-K filing unavailable")

    filing_10q = fetch_filings(ticker, "10-Q")
    if filing_10q is None:
        warnings.append("10-Q filing unavailable")

    # Step 5: Fetch news (DATA-04)
    news = fetch_news(ticker)
    if news is None:
        warnings.append("No recent news found")

    # Step 6: Fetch social sentiment (DATA-05)
    social = fetch_social_sentiment(ticker, company_name)
    if social is None:
        warnings.append("X/Twitter sentiment unavailable")

    # Step 7: Assemble DataPackage
    package = DataPackage(
        ticker=ticker.upper().strip(),
        company_name=company_name,
        description=description,
        fetched_at=datetime.now(),
        financials=financials,
        filing_10k=filing_10k,
        filing_10q=filing_10q,
        news=news,
        social=social,
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
