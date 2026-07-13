"""yfinance financial fundamentals fetcher."""

import logging
import math
from numbers import Real
from typing import Optional

import pandas as pd
import yfinance as yf

from .models import FinancialData

logger = logging.getLogger(__name__)


def _finite_number(value) -> Optional[float]:
    """Return a finite float for provider numerics, otherwise ``None``."""
    if not isinstance(value, Real) or isinstance(value, bool):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _normalize_dividend_yield(info: dict) -> tuple[Optional[float], Optional[str]]:
    """Normalize either yfinance dividend-yield convention to a ratio.

    yfinance installations have returned both fractions (``0.0055``) and
    percentages (``0.55``).  When annual dividend and price are available,
    select the interpretation closest to that independent ratio.  Otherwise,
    use a conservative magnitude heuristic and retain the detected source unit.
    """
    raw = _finite_number(info.get("dividendYield"))
    if raw is None:
        return None, None

    fraction_candidate = raw
    percent_candidate = raw / 100.0
    dividend_rate = _finite_number(info.get("dividendRate"))
    current_price = _finite_number(info.get("currentPrice"))
    corroborated = (
        dividend_rate / current_price
        if dividend_rate is not None
        and current_price is not None
        and current_price > 0
        else None
    )

    if corroborated is not None:
        if abs(percent_candidate - corroborated) < abs(
            fraction_candidate - corroborated
        ):
            return percent_candidate, "percent"
        return fraction_candidate, "fraction"

    # A raw yield above 20% is much more likely to be percentage-form data.
    if raw > 0.20:
        return percent_candidate, "percent"
    return fraction_candidate, "fraction"


def fetch_financials(ticker: str) -> Optional[FinancialData]:
    """Fetch financial fundamentals via yfinance.

    Returns FinancialData with key ratios and statement summaries,
    or None if fetching fails.
    """
    try:
        import yfinance as yf

        t = yf.Ticker(ticker.upper().strip())
        info = t.info or {}

        raw_debt_to_equity = _finite_number(info.get("debtToEquity"))
        debt_to_equity = (
            raw_debt_to_equity / 100.0
            if raw_debt_to_equity is not None
            else None
        )
        dividend_yield, dividend_yield_source_unit = _normalize_dividend_yield(
            info
        )

        # Key ratios from .info.  yfinance reports debtToEquity as a percent;
        # FinancialData stores model-boundary ratios as fractions.
        ratios = {
            "pe_ratio": info.get("trailingPE"),
            "pb_ratio": info.get("priceToBook"),
            "profit_margin": info.get("profitMargins"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": debt_to_equity,
            "market_cap": info.get("marketCap"),
            "revenue": info.get("totalRevenue"),
            "net_income": info.get("netIncomeToCommon"),
            "free_cash_flow": info.get("freeCashflow"),
            "dividend_yield": dividend_yield,
            "currency": info.get("currency"),
            "dividend_yield_source_unit": dividend_yield_source_unit,
        }

        # Income statement summary (most recent year)
        income_summary = None
        try:
            income_df = t.income_stmt
            if income_df is not None and not income_df.empty:
                # First column is most recent year
                latest = income_df.iloc[:, 0]
                income_summary = {}
                for row_name in ["Total Revenue", "Gross Profit", "Operating Income", "Net Income"]:
                    if row_name in latest.index:
                        val = latest[row_name]
                        if not pd.isna(val):
                            income_summary[row_name] = float(val)
        except Exception as e:
            logger.warning("Could not fetch income statement for %s: %s", ticker, e)

        # Balance sheet summary (most recent year)
        balance_summary = None
        try:
            balance_df = t.balance_sheet
            if balance_df is not None and not balance_df.empty:
                latest = balance_df.iloc[:, 0]
                balance_summary = {}
                for row_name in ["Total Assets", "Total Debt", "Stockholders Equity",
                                 "Cash And Cash Equivalents"]:
                    if row_name in latest.index:
                        val = latest[row_name]
                        if not pd.isna(val):
                            balance_summary[row_name] = float(val)
        except Exception as e:
            logger.warning("Could not fetch balance sheet for %s: %s", ticker, e)

        return FinancialData(
            income_summary=income_summary,
            balance_summary=balance_summary,
            **{k: v for k, v in ratios.items() if v is not None},
        )

    except Exception as e:
        logger.warning("Failed to fetch financials for %s: %s", ticker, e)
        return None


def fetch_price_history(ticker: str, period: str = "1y") -> list[dict]:
    """Fetch daily closing prices from yfinance.

    Args:
        ticker: Stock ticker symbol.
        period: yfinance period string (default "1y").

    Returns:
        List of {"date": "YYYY-MM-DD", "close": float} dicts, or empty list on failure.
    """
    try:
        hist = yf.Ticker(ticker.upper().strip()).history(period=period)
        if hist is None or hist.empty:
            return []
        result = []
        for date, row in hist.iterrows():
            close = row.get("Close")
            if close is not None and not pd.isna(close):
                result.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "close": round(float(close), 2),
                })
        return result
    except Exception as e:
        logger.warning("Failed to fetch price history for %s: %s", ticker, e)
        return []
