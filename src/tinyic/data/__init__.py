"""Phase 3: Financial data pipeline.

Public API:
  resolve_ticker(ticker) -> (is_valid, company_name)
  fetch_financials(ticker) -> Optional[FinancialData]
  fetch_news(ticker) -> Optional[NewsSummary]
  fetch_filings(ticker, form_type) -> Optional[FilingSummary]
  fetch_social_sentiment(ticker, company_name) -> Optional[SocialSentiment]
  build_data_package(ticker) -> DataPackage
"""

from .models import (
    DataPackage,
    FinancialData,
    FilingSummary,
    NewsSummary,
    ResearchBrief,
    SocialSentiment,
)
from .ticker_resolver import resolve_ticker
from .financials import fetch_financials
from .news import fetch_news
from .filings import fetch_filings
from .social import fetch_social_sentiment
from .research import build_research_brief
from .pipeline import build_data_package

__all__ = [
    "DataPackage",
    "FinancialData",
    "FilingSummary",
    "NewsSummary",
    "ResearchBrief",
    "SocialSentiment",
    "resolve_ticker",
    "fetch_financials",
    "fetch_news",
    "fetch_filings",
    "fetch_social_sentiment",
    "build_research_brief",
    "build_data_package",
]
