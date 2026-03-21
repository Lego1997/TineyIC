"""Phase 3: Financial data pipeline.

Public API:
  resolve_ticker(ticker) -> (is_valid, company_name)
  fetch_financials(ticker) -> Optional[FinancialData]
  fetch_news(ticker) -> Optional[NewsSummary]
  build_data_package(ticker) -> DataPackage  (added in Plan 02)
"""

from .models import (
    DataPackage,
    FinancialData,
    FilingSummary,
    NewsSummary,
    SocialSentiment,
)
from .ticker_resolver import resolve_ticker
from .financials import fetch_financials
from .news import fetch_news

__all__ = [
    "DataPackage",
    "FinancialData",
    "FilingSummary",
    "NewsSummary",
    "SocialSentiment",
    "resolve_ticker",
    "fetch_financials",
    "fetch_news",
]
