"""Pydantic data models for the financial data pipeline."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class FinancialData(BaseModel):
    """Summarized financial fundamentals from yfinance."""
    # Key ratios (from Ticker.info)
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    profit_margin: Optional[float] = None
    roe: Optional[float] = None
    debt_to_equity: Optional[float] = None
    market_cap: Optional[float] = None
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    free_cash_flow: Optional[float] = None
    dividend_yield: Optional[float] = None
    # Summarized statements (most recent year, key rows as dict)
    income_summary: Optional[dict] = None
    balance_summary: Optional[dict] = None


class FilingSummary(BaseModel):
    """Summarized SEC filing (10-K or 10-Q)."""
    form_type: str  # "10-K" or "10-Q"
    filing_date: Optional[str] = None
    period_end: Optional[str] = None
    text_summary: Optional[str] = None  # Truncated text, max ~3000 chars for 10-K, ~2000 for 10-Q


class NewsSummary(BaseModel):
    """Recent news articles from yfinance."""
    articles: list[dict] = Field(default_factory=list)
    # Each dict: {title: str, publisher: str, link: str, publish_time: str|None}


class SocialSentiment(BaseModel):
    """X/Twitter sentiment from xAI API."""
    query: str
    summary: Optional[str] = None  # Grok's analysis of X/Twitter sentiment
    bullish_points: list[str] = Field(default_factory=list)
    bearish_points: list[str] = Field(default_factory=list)


class ResearchBrief(BaseModel):
    """LLM-synthesized research brief from web search results.

    Each field covers one dimension of the investment research:
    business model, industry context, management, catalysts, and analyst views.
    """
    business_model: str = ""       # Competitive moat, revenue model, key advantages
    industry_trends: str = ""      # Macro tailwinds/headwinds, sector dynamics
    management: str = ""           # Track record, capital allocation, insider activity
    recent_catalysts: str = ""     # Last 6 months: product launches, deals, earnings surprises
    analyst_perspectives: str = "" # Bull/bear cases from public analyst commentary


class DataPackage(BaseModel):
    """Complete data bundle for persona consumption.

    Assembled by build_data_package(). Each sub-model is Optional
    because any data source can fail independently (graceful degradation).
    """
    ticker: str
    company_name: str
    description: Optional[str] = None
    fetched_at: datetime
    financials: Optional[FinancialData] = None
    filing_10k: Optional[FilingSummary] = None
    filing_10q: Optional[FilingSummary] = None
    news: Optional[NewsSummary] = None
    social: Optional[SocialSentiment] = None
    research_brief: Optional[ResearchBrief] = None
    warnings: list[str] = Field(default_factory=list)

    def to_context_string(self) -> str:
        """Format as a string suitable for LLM context injection.

        Returns JSON excluding None fields, capped at ~20K chars.
        """
        json_str = self.model_dump_json(indent=2, exclude_none=True)
        if len(json_str) > 20000:
            # Truncate to fit budget
            data = self.model_dump(exclude_none=True)
            for key in ["research_brief", "social", "filing_10q", "filing_10k"]:
                if key in data and len(json_str) > 20000:
                    if key == "research_brief" and isinstance(data[key], dict):
                        for field in data[key]:
                            if isinstance(data[key][field], str) and len(data[key][field]) > 800:
                                data[key][field] = data[key][field][:800] + "..."
                    elif isinstance(data[key], dict) and "text_summary" in data[key]:
                        data[key]["text_summary"] = data[key]["text_summary"][:1500] + "..."
                    elif isinstance(data[key], dict) and "summary" in data[key]:
                        data[key]["summary"] = data[key]["summary"][:1000] + "..."
                    json_str = self.__class__.model_validate(data).model_dump_json(indent=2, exclude_none=True)
        return json_str
