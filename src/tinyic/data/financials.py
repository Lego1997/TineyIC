"""yfinance financial fundamentals fetcher."""

import logging
from typing import Optional

import pandas as pd
import yfinance as yf

from .models import FinancialData

logger = logging.getLogger(__name__)


def fetch_financials(ticker: str) -> Optional[FinancialData]:
    """Fetch financial fundamentals via yfinance.

    Returns FinancialData with key ratios and statement summaries,
    or None if fetching fails.
    """
    try:
        import yfinance as yf

        t = yf.Ticker(ticker.upper().strip())
        info = t.info or {}

        # Key ratios from .info
        ratios = {
            "pe_ratio": info.get("trailingPE"),
            "pb_ratio": info.get("priceToBook"),
            "profit_margin": info.get("profitMargins"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "market_cap": info.get("marketCap"),
            "revenue": info.get("totalRevenue"),
            "net_income": info.get("netIncomeToCommon"),
            "free_cash_flow": info.get("freeCashflow"),
            "dividend_yield": info.get("dividendYield"),
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
