"""Pydantic data models for the financial data pipeline."""

import json
from datetime import datetime
from numbers import Real
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
    currency: Optional[str] = None
    dividend_yield_source_unit: Optional[str] = None
    # Recent-listing note (e.g. fresh IPO) used by the pipeline to contextualize
    # a "market data but no SEC filing" signal; not part of the LLM-facing
    # context dict (it rides the pipeline warning instead).
    listing_note: Optional[str] = None
    # Summarized statements (most recent year, key rows as dict)
    income_summary: Optional[dict] = None
    balance_summary: Optional[dict] = None

    def to_context_dict(self) -> dict:
        """Return the model-facing representation with explicit units.

        Values remain plain numbers on the boundary model so application code
        can calculate and render them normally.  Only the LLM-context boundary
        wraps each number as ``{"value": ..., "unit": ...}``.
        """
        amount_unit = self.currency or "unknown_currency"
        field_units = {
            "pe_ratio": "x",
            "pb_ratio": "x",
            "profit_margin": "ratio",
            "roe": "ratio",
            "debt_to_equity": "ratio",
            "market_cap": amount_unit,
            "revenue": amount_unit,
            "net_income": amount_unit,
            "free_cash_flow": amount_unit,
        }

        context: dict = {}
        for field_name, unit in field_units.items():
            value = getattr(self, field_name)
            if value is not None:
                context[field_name] = {"value": value, "unit": unit}

        if self.dividend_yield is not None:
            dividend = {"value": self.dividend_yield, "unit": "ratio"}
            if self.dividend_yield_source_unit is not None:
                dividend["source_unit"] = self.dividend_yield_source_unit
            context["dividend_yield"] = dividend

        if self.income_summary is not None:
            context["income_summary"] = self._unitize_statement(
                self.income_summary, amount_unit
            )
        if self.balance_summary is not None:
            context["balance_summary"] = self._unitize_statement(
                self.balance_summary, amount_unit
            )
        if self.currency is not None:
            context["currency"] = self.currency

        return context

    @classmethod
    def _unitize_statement(cls, value, unit: str):
        """Recursively label every numeric statement value with ``unit``."""
        if isinstance(value, dict):
            if "value" in value and "unit" in value:
                return value
            return {
                key: cls._unitize_statement(item, unit)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._unitize_statement(item, unit) for item in value]
        if isinstance(value, Real) and not isinstance(value, bool):
            return {"value": value, "unit": unit}
        return value


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


class CNStatements(BaseModel):
    """Key line items from the latest reported fiscal year (A-share or HK).

    Keys are English (language-neutral for downstream prompts); when the
    source label was Chinese/ambiguous, the original label is preserved in
    ``source_labels`` keyed by the same English name.  Amounts are raw
    currency units (not 万元 / thousands).
    """
    period: Optional[str] = None       # report-period label, e.g. "2025-12-31"
    # "CNY" for A-share.  Always None for HK: EastMoney's F10 statement rows
    # carry no currency column and HK issuers report in HKD, CNY, or USD --
    # ``currency_note`` spells that out for the persona-facing context.
    currency: Optional[str] = None
    currency_note: Optional[str] = None
    income: dict = Field(default_factory=dict)
    balance: dict = Field(default_factory=dict)
    cash_flow: dict = Field(default_factory=dict)
    source_labels: dict = Field(default_factory=dict)


class CNRatios(BaseModel):
    """Financial-analysis indicators (EPS, ROE, margins; ``*_pct`` = percent)."""
    period: Optional[str] = None
    ratios: dict = Field(default_factory=dict)
    source_labels: dict = Field(default_factory=dict)


class CNValuation(BaseModel):
    """Valuation snapshot (A-share; EastMoney ``stock_value_em``)."""
    as_of: Optional[str] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    market_cap: Optional[float] = None  # total market cap, CNY


class CNAnalystConsensus(BaseModel):
    """Sell-side consensus (A-share; EastMoney profit forecast)."""
    report_count: Optional[int] = None
    ratings: dict = Field(default_factory=dict)        # buy/overweight/neutral/underweight/sell -> count
    consensus_eps: dict = Field(default_factory=dict)  # year ("2026") -> forecast EPS


class CNMarketData(BaseModel):
    """China-market fundamentals (A-share / HK) from akshare (optional extra).

    Every section is Optional: each sub-fetch degrades independently, and
    ``failed_sections`` records which ones failed so the pipeline can surface
    a "partial" warning.
    """
    market: str  # "a_share" | "hk"
    source: str = "akshare (EastMoney/Sina)"
    statements: Optional[CNStatements] = None
    ratios: Optional[CNRatios] = None
    valuation: Optional[CNValuation] = None
    consensus: Optional[CNAnalystConsensus] = None
    profile: Optional[str] = None  # HK company profile text, truncated
    failed_sections: list[str] = Field(default_factory=list)


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
    cn_market: Optional[CNMarketData] = None
    research_brief: Optional[ResearchBrief] = None
    warnings: list[str] = Field(default_factory=list)

    def to_context_string(self) -> str:
        """Format as a string suitable for LLM context injection.

        Returns JSON excluding None fields, capped at ~20K chars.
        """
        data = self.model_dump(mode="json", exclude_none=True)
        if self.financials is not None:
            data["financials"] = self.financials.to_context_dict()

        def serialize() -> str:
            return json.dumps(data, indent=2, ensure_ascii=False)

        json_str = serialize()
        if len(json_str) > 20000:
            # Truncate to fit budget
            for key in ["research_brief", "cn_market", "social", "filing_10q", "filing_10k"]:
                if key in data and len(json_str) > 20000:
                    if key == "research_brief" and isinstance(data[key], dict):
                        for field in data[key]:
                            if isinstance(data[key][field], str) and len(data[key][field]) > 800:
                                data[key][field] = data[key][field][:800] + "..."
                    elif key == "cn_market" and isinstance(data[key], dict):
                        profile = data[key].get("profile")
                        if isinstance(profile, str) and len(profile) > 400:
                            data[key]["profile"] = profile[:400] + "..."
                        for section in ("statements", "ratios"):
                            if isinstance(data[key].get(section), dict):
                                data[key][section].pop("source_labels", None)
                    elif isinstance(data[key], dict) and "text_summary" in data[key]:
                        data[key]["text_summary"] = data[key]["text_summary"][:1500] + "..."
                    elif isinstance(data[key], dict) and "summary" in data[key]:
                        data[key]["summary"] = data[key]["summary"][:1000] + "..."
                    json_str = serialize()
        return json_str
