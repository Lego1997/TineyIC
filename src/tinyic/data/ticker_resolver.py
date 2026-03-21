"""Ticker validation and company name resolution via yfinance."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_ticker(ticker: str) -> tuple[bool, str]:
    """Validate a stock ticker and resolve to company name.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL", "msft").

    Returns:
        (is_valid, company_name) -- company_name is empty string if invalid.
    """
    try:
        import yfinance as yf

        t = yf.Ticker(ticker.upper().strip())
        info = t.info or {}
        name = info.get("longName") or info.get("shortName") or ""
        if not name:
            logger.warning("Ticker '%s' resolved but no company name found", ticker)
            return (False, "")
        return (True, name)
    except Exception as e:
        logger.warning("Failed to resolve ticker '%s': %s", ticker, e)
        return (False, "")
