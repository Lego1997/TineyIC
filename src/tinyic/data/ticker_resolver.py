"""Ticker validation and company name resolution via yfinance.

Supports:
- Direct ticker lookup (AAPL, MSFT)
- International tickers with exchange suffixes (0700.HK, 7203.T, SAP.DE)
- Company name search ("Apple", "Tencent", "Toyota")
- Fallback chain: .info -> fast_info -> Search API -> invalid
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Pattern: looks like a ticker symbol (uppercase letters/digits, optional .SUFFIX)
_TICKER_PATTERN = re.compile(r"^[A-Z0-9]{1,5}(\.[A-Z]{1,4})?$")


def _looks_like_ticker(query: str) -> bool:
    """Heuristic: does the input look like a ticker symbol?

    Returns True for: AAPL, MSFT, 0700.HK, SAP.DE, MC.PA, 7203.T, aapl, sap.de
    Returns False for: Apple, Tencent Holdings, toyota motor

    Key insight: mixed-case words like "Apple" are company names, not tickers.
    Pure uppercase, pure lowercase, or numeric-prefixed inputs are treated as tickers.
    A word with an initial capital followed by lowercase (title case) is a name.
    """
    stripped = query.strip()
    if not stripped:
        return False
    # If it contains spaces, it's a name (e.g., "Tencent Holdings")
    if " " in stripped:
        return False
    # Check against pattern using uppercased version (handles lowercase tickers like "aapl")
    uppercased = stripped.upper()
    if not _TICKER_PATTERN.match(uppercased):
        return False
    # If original has mixed case (title case like "Apple"), it's a company name
    # Pure upper ("AAPL"), pure lower ("aapl"), or starts with digit ("0700.HK") are tickers
    if stripped[0].isalpha() and stripped[0].isupper() and any(c.islower() for c in stripped):
        return False
    return True


def _normalize_ticker(raw: str) -> str:
    """Normalize a ticker-like input: uppercase and strip.

    Handles exchange suffixes: 'sap.de' -> 'SAP.DE', '0700.hk' -> '0700.HK'.
    """
    return raw.strip().upper()


def _try_info(symbol: str) -> Optional[tuple[str, str]]:
    """Try yfinance Ticker.info to get company name.

    Returns (ticker_symbol, company_name) or None.
    """
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        info = t.info or {}
        name = info.get("longName") or info.get("shortName") or ""
        if name:
            return (symbol, name)
    except Exception as e:
        logger.debug("Ticker.info failed for '%s': %s", symbol, e)
    return None


def _try_fast_info(symbol: str) -> bool:
    """Try yfinance Ticker.fast_info to validate ticker existence.

    fast_info is a lightweight alternative to .info. It cannot return a company
    name, but can confirm that a ticker is valid (has market data).

    Returns True if the ticker is valid (fast_info.currency accessible), False otherwise.
    """
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        _ = t.fast_info.currency  # Raises KeyError if ticker is invalid
        return True
    except Exception as e:
        logger.debug("Ticker.fast_info failed for '%s': %s", symbol, e)
    return False


def _try_search(query: str) -> Optional[tuple[str, str]]:
    """Try yfinance Search API to find a ticker.

    Returns (ticker_symbol, company_name) for the best tradable-security match,
    or None. Only EQUITY (preferred) and ETF quotes are accepted: a name search
    must fail closed rather than silently bind to a non-security quote such as a
    CRYPTOCURRENCY, MUTUALFUND, OPTION, INDEX, or CURRENCY (e.g. a private
    company whose only quote is a tokenized-stock crypto listing).
    """
    try:
        import yfinance as yf

        results = yf.Search(query)
        if not results.quotes:
            return None

        # Prefer EQUITY, then ETF; reject every other quote type (fail closed).
        for accepted_type in ("EQUITY", "ETF"):
            for quote in results.quotes:
                if quote.get("quoteType", "").upper() == accepted_type:
                    symbol = quote.get("symbol", "")
                    name = quote.get("longname") or quote.get("shortname") or ""
                    if symbol and name:
                        return (symbol, name)

    except Exception as e:
        logger.debug("Search failed for '%s': %s", query, e)
    return None


def resolve_ticker(query: str) -> tuple[bool, str, str]:
    """Validate a stock ticker or company name and resolve to ticker + company name.

    Supports direct ticker symbols (AAPL), international tickers (0700.HK),
    and company name search ("Apple", "Tencent").

    Fallback chain for ticker-like input:
    1. .info (full metadata with company name)
    2. fast_info (lightweight validity check, then Search for name)
    3. Search API (handles partial/misspelled tickers)
    4. Return invalid

    Fallback chain for name-like input:
    1. Search API
    2. Direct lookup (edge case: unusual ticker format)
    3. Return invalid

    Args:
        query: Ticker symbol or company name.

    Returns:
        (is_valid, ticker_symbol, company_name).
        ticker_symbol and company_name are empty strings if invalid.
    """
    query = query.strip()
    if not query:
        return (False, "", "")

    if _looks_like_ticker(query):
        # Input looks like a ticker -- try direct lookup first
        symbol = _normalize_ticker(query)

        # Layer 1: .info (gets company name directly)
        result = _try_info(symbol)
        if result:
            return (True, result[0], result[1])

        # Layer 2: fast_info (validates ticker exists, then Search for name)
        if _try_fast_info(symbol):
            logger.info("fast_info confirmed '%s' exists, searching for name", symbol)
            search_result = _try_search(symbol)
            if search_result:
                return (True, search_result[0], search_result[1])
            # fast_info says valid but can't find name -- return symbol as name
            return (True, symbol, symbol)

        # Layer 3: Search API (handles partial/misspelled tickers)
        logger.info("Direct lookup failed for '%s', trying search", symbol)
        result = _try_search(symbol)
        if result:
            return (True, result[0], result[1])

        # Layer 4: Invalid
        logger.warning("Could not resolve ticker '%s'", query)
        return (False, "", "")
    else:
        # Input looks like a company name -- search first
        result = _try_search(query)
        if result:
            return (True, result[0], result[1])

        # Edge case: maybe it's an unusual ticker format -- try direct
        normalized = _normalize_ticker(query)
        result = _try_info(normalized)
        if result:
            return (True, result[0], result[1])

        logger.warning("Could not resolve '%s'", query)
        return (False, "", "")
