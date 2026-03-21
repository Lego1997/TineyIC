"""Unit tests for Phase 3 data pipeline (Plan 01 + Plan 02).

All external calls are mocked -- no network access required.
Plan 01: models, ticker_resolver, financials, news (21 tests)
Plan 02: filings, social, pipeline + live API integration (14+ tests)
"""

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tinyic.data.models import (
    DataPackage,
    FinancialData,
    FilingSummary,
    NewsSummary,
    SocialSentiment,
)
from tinyic.data.ticker_resolver import resolve_ticker
from tinyic.data.financials import fetch_financials
from tinyic.data.news import fetch_news
from tinyic.data.filings import fetch_filings
from tinyic.data.social import fetch_social_sentiment
from tinyic.data.pipeline import build_data_package


# ---------------------------------------------------------------------------
# Test Data Models
# ---------------------------------------------------------------------------

class TestDataModels:
    """Pydantic model creation, serialization, defaults, partial data."""

    def test_data_package_serialization(self):
        """DataPackage round-trips through JSON serialization."""
        fin = FinancialData(pe_ratio=28.5, revenue=394_000_000_000)
        pkg = DataPackage(
            ticker="AAPL",
            company_name="Apple Inc.",
            fetched_at=datetime(2026, 1, 15, 12, 0, 0),
            financials=fin,
        )
        json_str = pkg.model_dump_json()
        restored = DataPackage.model_validate_json(json_str)
        assert restored.ticker == "AAPL"
        assert restored.company_name == "Apple Inc."
        assert restored.financials.pe_ratio == 28.5
        assert restored.financials.revenue == 394_000_000_000

    def test_data_package_to_context_string(self):
        """to_context_string() excludes None fields."""
        pkg = DataPackage(
            ticker="MSFT",
            company_name="Microsoft Corporation",
            fetched_at=datetime(2026, 1, 15),
            financials=FinancialData(pe_ratio=35.0),
        )
        ctx = pkg.to_context_string()
        assert "MSFT" in ctx
        assert "pe_ratio" in ctx
        # None fields should be excluded
        assert "filing_10k" not in ctx
        assert "social" not in ctx

    def test_data_package_warnings(self):
        """DataPackage serializes warnings list."""
        pkg = DataPackage(
            ticker="AAPL",
            company_name="Apple Inc.",
            fetched_at=datetime(2026, 1, 15),
            warnings=["News fetch failed", "Social fetch skipped"],
        )
        json_str = pkg.model_dump_json()
        restored = DataPackage.model_validate_json(json_str)
        assert len(restored.warnings) == 2
        assert "News fetch failed" in restored.warnings

    def test_data_package_defaults(self):
        """DataPackage Optional fields default to None, lists to empty."""
        pkg = DataPackage(
            ticker="GOOG",
            company_name="Alphabet Inc.",
            fetched_at=datetime(2026, 1, 15),
        )
        assert pkg.financials is None
        assert pkg.filing_10k is None
        assert pkg.filing_10q is None
        assert pkg.news is None
        assert pkg.social is None
        assert pkg.description is None
        assert pkg.warnings == []

    def test_financial_data_partial(self):
        """FinancialData with only pe_ratio set, all others None."""
        fin = FinancialData(pe_ratio=15.2)
        assert fin.pe_ratio == 15.2
        assert fin.pb_ratio is None
        assert fin.revenue is None
        assert fin.income_summary is None
        assert fin.balance_summary is None

    def test_filing_summary_requires_form_type(self):
        """FilingSummary requires form_type, others optional."""
        filing = FilingSummary(form_type="10-K")
        assert filing.form_type == "10-K"
        assert filing.filing_date is None
        assert filing.text_summary is None

    def test_news_summary_defaults(self):
        """NewsSummary.articles defaults to empty list."""
        news = NewsSummary()
        assert news.articles == []

    def test_social_sentiment_requires_query(self):
        """SocialSentiment requires query, lists default empty."""
        social = SocialSentiment(query="AAPL stock")
        assert social.query == "AAPL stock"
        assert social.summary is None
        assert social.bullish_points == []
        assert social.bearish_points == []


# ---------------------------------------------------------------------------
# Test Ticker Resolver
# ---------------------------------------------------------------------------

class TestTickerResolver:
    """Mock yfinance.Ticker to test resolve_ticker."""

    @patch("yfinance.Ticker")
    def test_resolve_ticker_valid(self, mock_ticker_cls):
        """Valid ticker returns (True, company_name)."""
        mock_instance = MagicMock()
        mock_instance.info = {"longName": "Apple Inc.", "shortName": "Apple"}
        mock_ticker_cls.return_value = mock_instance

        is_valid, name = resolve_ticker("AAPL")
        assert is_valid is True
        assert name == "Apple Inc."
        mock_ticker_cls.assert_called_once_with("AAPL")

    @patch("yfinance.Ticker")
    def test_resolve_ticker_invalid(self, mock_ticker_cls):
        """Invalid ticker (no name in info) returns (False, "")."""
        mock_instance = MagicMock()
        mock_instance.info = {}
        mock_ticker_cls.return_value = mock_instance

        is_valid, name = resolve_ticker("XYZNOTREAL")
        assert is_valid is False
        assert name == ""

    @patch("yfinance.Ticker")
    def test_resolve_ticker_exception(self, mock_ticker_cls):
        """Exception during resolution returns (False, "")."""
        mock_ticker_cls.side_effect = Exception("Network error")

        is_valid, name = resolve_ticker("AAPL")
        assert is_valid is False
        assert name == ""

    @patch("yfinance.Ticker")
    def test_resolve_ticker_normalizes_input(self, mock_ticker_cls):
        """Ticker input is uppercased and stripped."""
        mock_instance = MagicMock()
        mock_instance.info = {"longName": "Apple Inc."}
        mock_ticker_cls.return_value = mock_instance

        is_valid, name = resolve_ticker("aapl ")
        assert is_valid is True
        assert name == "Apple Inc."
        mock_ticker_cls.assert_called_once_with("AAPL")

    @patch("yfinance.Ticker")
    def test_resolve_ticker_shortname_fallback(self, mock_ticker_cls):
        """Falls back to shortName when longName is missing."""
        mock_instance = MagicMock()
        mock_instance.info = {"shortName": "Apple"}
        mock_ticker_cls.return_value = mock_instance

        is_valid, name = resolve_ticker("AAPL")
        assert is_valid is True
        assert name == "Apple"


# ---------------------------------------------------------------------------
# Test Fetch Financials
# ---------------------------------------------------------------------------

class TestFetchFinancials:
    """Mock yfinance.Ticker to test fetch_financials."""

    @patch("yfinance.Ticker")
    def test_fetch_financials_valid(self, mock_ticker_cls):
        """Valid ticker returns FinancialData with ratios and statement summaries."""
        # Build realistic DataFrames
        income_data = {
            pd.Timestamp("2025-09-30"): {
                "Total Revenue": 394_328_000_000,
                "Gross Profit": 180_683_000_000,
                "Operating Income": 123_216_000_000,
                "Net Income": 96_995_000_000,
            }
        }
        mock_income_df = pd.DataFrame(income_data)

        balance_data = {
            pd.Timestamp("2025-09-30"): {
                "Total Assets": 352_583_000_000,
                "Total Debt": 111_088_000_000,
                "Stockholders Equity": 62_146_000_000,
                "Cash And Cash Equivalents": 29_965_000_000,
            }
        }
        mock_balance_df = pd.DataFrame(balance_data)

        mock_instance = MagicMock()
        mock_instance.info = {
            "trailingPE": 28.5,
            "priceToBook": 48.2,
            "profitMargins": 0.246,
            "returnOnEquity": 1.56,
            "debtToEquity": 178.7,
            "marketCap": 2_800_000_000_000,
            "totalRevenue": 394_328_000_000,
            "netIncomeToCommon": 96_995_000_000,
            "freeCashflow": 111_443_000_000,
            "dividendYield": 0.0055,
        }
        mock_instance.income_stmt = mock_income_df
        mock_instance.balance_sheet = mock_balance_df
        mock_ticker_cls.return_value = mock_instance

        result = fetch_financials("AAPL")
        assert result is not None
        assert result.pe_ratio == 28.5
        assert result.revenue == 394_328_000_000
        assert result.income_summary is not None
        assert "Total Revenue" in result.income_summary
        assert result.income_summary["Total Revenue"] == 394_328_000_000
        assert result.balance_summary is not None
        assert "Total Assets" in result.balance_summary

    @patch("yfinance.Ticker")
    def test_fetch_financials_empty_statements(self, mock_ticker_cls):
        """Ratios returned but empty DataFrames yield no summaries."""
        mock_instance = MagicMock()
        mock_instance.info = {"trailingPE": 28.5, "totalRevenue": 394_000_000_000}
        mock_instance.income_stmt = pd.DataFrame()  # empty
        mock_instance.balance_sheet = pd.DataFrame()  # empty
        mock_ticker_cls.return_value = mock_instance

        result = fetch_financials("AAPL")
        assert result is not None
        assert result.pe_ratio == 28.5
        assert result.revenue == 394_000_000_000
        assert result.income_summary is None
        assert result.balance_summary is None

    @patch("yfinance.Ticker")
    def test_fetch_financials_failure(self, mock_ticker_cls):
        """Exception returns None."""
        mock_ticker_cls.side_effect = Exception("Network error")

        result = fetch_financials("AAPL")
        assert result is None

    @patch("yfinance.Ticker")
    def test_fetch_financials_normalizes_ticker(self, mock_ticker_cls):
        """Ticker input is uppercased and stripped."""
        mock_instance = MagicMock()
        mock_instance.info = {"trailingPE": 15.0}
        mock_instance.income_stmt = pd.DataFrame()
        mock_instance.balance_sheet = pd.DataFrame()
        mock_ticker_cls.return_value = mock_instance

        result = fetch_financials("msft ")
        assert result is not None
        mock_ticker_cls.assert_called_once_with("MSFT")


# ---------------------------------------------------------------------------
# Test Fetch News
# ---------------------------------------------------------------------------

class TestFetchNews:
    """Mock yfinance.Ticker to test fetch_news."""

    @patch("yfinance.Ticker")
    def test_fetch_news_valid(self, mock_ticker_cls):
        """Valid ticker returns NewsSummary with articles."""
        mock_instance = MagicMock()
        mock_instance.get_news.return_value = [
            {
                "content": {
                    "title": "Apple Reports Record Q4 Revenue",
                    "provider": {"displayName": "Reuters"},
                    "canonicalUrl": {"url": "https://example.com/1"},
                    "pubDate": "2026-01-15T10:00:00Z",
                }
            },
            {
                "content": {
                    "title": "Apple Vision Pro Sales Surge",
                    "provider": {"displayName": "Bloomberg"},
                    "canonicalUrl": {"url": "https://example.com/2"},
                    "pubDate": "2026-01-15T12:00:00Z",
                }
            },
        ]
        mock_ticker_cls.return_value = mock_instance

        result = fetch_news("AAPL")
        assert result is not None
        assert len(result.articles) == 2
        assert result.articles[0]["title"] == "Apple Reports Record Q4 Revenue"
        assert result.articles[0]["publisher"] == "Reuters"
        assert result.articles[1]["publisher"] == "Bloomberg"

    @patch("yfinance.Ticker")
    def test_fetch_news_empty(self, mock_ticker_cls):
        """Empty news list returns None."""
        mock_instance = MagicMock()
        mock_instance.get_news.return_value = []
        mock_ticker_cls.return_value = mock_instance

        result = fetch_news("XYZNOTREAL")
        assert result is None

    @patch("yfinance.Ticker")
    def test_fetch_news_failure(self, mock_ticker_cls):
        """Exception returns None."""
        mock_ticker_cls.side_effect = Exception("Network error")

        result = fetch_news("AAPL")
        assert result is None

    @patch("yfinance.Ticker")
    def test_fetch_news_respects_count(self, mock_ticker_cls):
        """Count parameter limits articles."""
        mock_instance = MagicMock()
        mock_instance.get_news.return_value = [
            {
                "content": {
                    "title": f"Article {i}",
                    "provider": {"displayName": "Test"},
                    "canonicalUrl": {"url": f"https://example.com/{i}"},
                    "pubDate": "2026-01-15T10:00:00Z",
                }
            }
            for i in range(10)
        ]
        mock_ticker_cls.return_value = mock_instance

        result = fetch_news("AAPL", count=3)
        assert result is not None
        # get_news is called with count=3
        mock_instance.get_news.assert_called_once_with(count=3)
        # Articles sliced to count
        assert len(result.articles) <= 3


# ---------------------------------------------------------------------------
# Test Fetch Filings (Plan 02)
# ---------------------------------------------------------------------------

class TestFetchFilings:
    """Mock edgartools Company to test fetch_filings."""

    def setup_method(self):
        """Reset identity flag between tests."""
        import tinyic.data.filings as filings_mod
        filings_mod._identity_set = False

    @patch("edgar.set_identity")
    @patch("edgar.Company")
    def test_fetch_filings_10k_valid(self, mock_company_cls, mock_set_identity):
        """Valid 10-K filing returns FilingSummary with form_type and text."""
        # Setup mock chain
        mock_filing = MagicMock()
        mock_filing.filing_date = "2025-10-30"
        mock_filing_obj = MagicMock()
        mock_filing_obj.__getitem__ = MagicMock(
            side_effect=lambda key: f"Section content for {key}..." * 50
        )
        mock_filing.obj.return_value = mock_filing_obj
        mock_filing.text.return_value = "Fallback text content"

        mock_filings = MagicMock()
        mock_filings.latest.return_value = mock_filing
        mock_filings.__bool__ = MagicMock(return_value=True)

        mock_company = MagicMock()
        mock_company.get_filings.return_value = mock_filings
        mock_company_cls.return_value = mock_company

        result = fetch_filings("AAPL", "10-K")
        assert result is not None
        assert result.form_type == "10-K"
        assert result.text_summary is not None
        assert len(result.text_summary) <= 3000

    @patch("edgar.set_identity")
    @patch("edgar.Company")
    def test_fetch_filings_10q_valid(self, mock_company_cls, mock_set_identity):
        """Valid 10-Q filing returns FilingSummary with text under 2000 chars."""
        mock_filing = MagicMock()
        mock_filing.filing_date = "2025-07-15"
        mock_filing_obj = MagicMock()
        mock_filing_obj.__getitem__ = MagicMock(
            side_effect=lambda key: f"Quarterly content for {key}..." * 30
        )
        mock_filing.obj.return_value = mock_filing_obj

        mock_filings = MagicMock()
        mock_filings.latest.return_value = mock_filing
        mock_filings.__bool__ = MagicMock(return_value=True)

        mock_company = MagicMock()
        mock_company.get_filings.return_value = mock_filings
        mock_company_cls.return_value = mock_company

        result = fetch_filings("AAPL", "10-Q")
        assert result is not None
        assert result.form_type == "10-Q"
        assert result.text_summary is not None
        assert len(result.text_summary) <= 2000

    @patch("edgar.set_identity")
    @patch("edgar.Company")
    def test_fetch_filings_no_filings(self, mock_company_cls, mock_set_identity):
        """No filings found returns None."""
        mock_filings = MagicMock()
        mock_filings.__bool__ = MagicMock(return_value=False)

        mock_company = MagicMock()
        mock_company.get_filings.return_value = mock_filings
        mock_company_cls.return_value = mock_company

        result = fetch_filings("XYZNOTREAL", "10-K")
        assert result is None

    @patch("edgar.set_identity")
    @patch("edgar.Company")
    def test_fetch_filings_exception(self, mock_company_cls, mock_set_identity):
        """Exception during fetch returns None (never raises)."""
        mock_company_cls.side_effect = Exception("SEC API error")

        result = fetch_filings("AAPL", "10-K")
        assert result is None

    @patch("edgar.set_identity")
    @patch("edgar.Company")
    def test_fetch_filings_text_truncation(self, mock_company_cls, mock_set_identity):
        """Long filing text is truncated to max_chars."""
        mock_filing = MagicMock()
        mock_filing.filing_date = "2025-10-30"
        # Structured access fails, fallback to text()
        mock_filing.obj.side_effect = Exception("obj() failed")
        mock_filing.text.return_value = "X" * 5000  # 5000 chars, should be truncated

        mock_filings = MagicMock()
        mock_filings.latest.return_value = mock_filing
        mock_filings.__bool__ = MagicMock(return_value=True)

        mock_company = MagicMock()
        mock_company.get_filings.return_value = mock_filings
        mock_company_cls.return_value = mock_company

        result = fetch_filings("AAPL", "10-K")
        assert result is not None
        assert len(result.text_summary) <= 3000


# ---------------------------------------------------------------------------
# Test Fetch Social Sentiment (Plan 02)
# ---------------------------------------------------------------------------

class TestFetchSocialSentiment:
    """Mock xAI API (OpenAI SDK) to test fetch_social_sentiment."""

    @patch.dict(os.environ, {"XAI_API_KEY": "test-key"})
    @patch("openai.OpenAI")
    def test_fetch_social_valid(self, mock_openai_cls):
        """Valid API key and response returns SocialSentiment."""
        mock_response = MagicMock()
        mock_response.output_text = (
            "Bullish sentiment on AAPL. Key points: strong iPhone sales..."
        )
        mock_client = MagicMock()
        mock_client.responses.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        result = fetch_social_sentiment("AAPL", "Apple Inc.")
        assert result is not None
        assert result.query == "$AAPL Apple Inc."
        assert "Bullish" in result.summary or "bullish" in result.summary

    @patch.dict(os.environ, {}, clear=True)
    def test_fetch_social_no_key(self):
        """Missing XAI_API_KEY returns None."""
        # Ensure XAI_API_KEY is not set
        os.environ.pop("XAI_API_KEY", None)
        result = fetch_social_sentiment("AAPL", "Apple Inc.")
        assert result is None

    @patch.dict(os.environ, {"XAI_API_KEY": "test-key"})
    @patch("openai.OpenAI")
    def test_fetch_social_api_error(self, mock_openai_cls):
        """API exception returns None (never raises)."""
        mock_client = MagicMock()
        mock_client.responses.create.side_effect = Exception("API timeout")
        mock_openai_cls.return_value = mock_client

        result = fetch_social_sentiment("AAPL", "Apple Inc.")
        assert result is None


# ---------------------------------------------------------------------------
# Test Build Data Package (Plan 02)
# ---------------------------------------------------------------------------

class TestBuildDataPackage:
    """Mock all fetchers to test build_data_package orchestrator."""

    @patch("tinyic.data.pipeline.fetch_social_sentiment")
    @patch("tinyic.data.pipeline.fetch_news")
    @patch("tinyic.data.pipeline.fetch_filings")
    @patch("tinyic.data.pipeline.fetch_financials")
    @patch("tinyic.data.pipeline.resolve_ticker")
    @patch("tinyic.data.pipeline._fetch_description")
    def test_build_data_package_valid(
        self, mock_desc, mock_resolve, mock_fin, mock_filings, mock_news, mock_social
    ):
        """All sources succeed -> DataPackage with all fields, no warnings."""
        mock_resolve.return_value = (True, "Apple Inc.")
        mock_desc.return_value = "Apple designs and sells consumer electronics."
        mock_fin.return_value = FinancialData(pe_ratio=28.5, revenue=394_000_000_000)
        mock_filings.side_effect = [
            FilingSummary(form_type="10-K", text_summary="Business section..."),
            FilingSummary(form_type="10-Q", text_summary="Quarterly update..."),
        ]
        mock_news.return_value = NewsSummary(
            articles=[{"title": "Apple reports earnings", "publisher": "Reuters"}]
        )
        mock_social.return_value = SocialSentiment(
            query="$AAPL", summary="Mixed sentiment"
        )

        pkg = build_data_package("AAPL")
        assert pkg.ticker == "AAPL"
        assert pkg.company_name == "Apple Inc."
        assert pkg.financials is not None
        assert pkg.filing_10k is not None
        assert pkg.filing_10q is not None
        assert pkg.news is not None
        assert pkg.social is not None
        assert len(pkg.warnings) == 0

    @patch("tinyic.data.pipeline.resolve_ticker")
    def test_build_data_package_invalid_ticker(self, mock_resolve):
        """Invalid ticker raises ValueError."""
        mock_resolve.return_value = (False, "")

        with pytest.raises(ValueError, match="Invalid ticker"):
            build_data_package("XYZNOTREAL")

    @patch("tinyic.data.pipeline.fetch_social_sentiment")
    @patch("tinyic.data.pipeline.fetch_news")
    @patch("tinyic.data.pipeline.fetch_filings")
    @patch("tinyic.data.pipeline.fetch_financials")
    @patch("tinyic.data.pipeline.resolve_ticker")
    @patch("tinyic.data.pipeline._fetch_description")
    def test_build_data_package_partial_failure(
        self, mock_desc, mock_resolve, mock_fin, mock_filings, mock_news, mock_social
    ):
        """Some sources fail -> DataPackage has warnings for failed sources."""
        mock_resolve.return_value = (True, "Apple Inc.")
        mock_desc.return_value = None
        mock_fin.return_value = FinancialData(pe_ratio=28.5)
        mock_filings.side_effect = [
            FilingSummary(form_type="10-K", text_summary="10-K content"),
            None,  # 10-Q fails
        ]
        mock_news.return_value = None  # News fails
        mock_social.return_value = None  # Social fails

        pkg = build_data_package("AAPL")
        assert pkg.ticker == "AAPL"
        assert pkg.financials is not None
        assert pkg.filing_10k is not None
        assert pkg.filing_10q is None
        assert pkg.news is None
        assert pkg.social is None
        # 3 warnings: 10-Q, news, social
        assert len(pkg.warnings) == 3
        assert any("10-Q" in w for w in pkg.warnings)
        assert any("news" in w.lower() for w in pkg.warnings)
        assert any("sentiment" in w.lower() or "twitter" in w.lower() for w in pkg.warnings)

    @patch("tinyic.data.pipeline.fetch_social_sentiment")
    @patch("tinyic.data.pipeline.fetch_news")
    @patch("tinyic.data.pipeline.fetch_filings")
    @patch("tinyic.data.pipeline.fetch_financials")
    @patch("tinyic.data.pipeline.resolve_ticker")
    @patch("tinyic.data.pipeline._fetch_description")
    def test_build_data_package_all_sources_fail(
        self, mock_desc, mock_resolve, mock_fin, mock_filings, mock_news, mock_social
    ):
        """All data sources fail -> DataPackage has 5 warnings but doesn't crash."""
        mock_resolve.return_value = (True, "Apple Inc.")
        mock_desc.return_value = None
        mock_fin.return_value = None
        mock_filings.return_value = None
        mock_news.return_value = None
        mock_social.return_value = None

        pkg = build_data_package("AAPL")
        assert pkg.ticker == "AAPL"
        assert pkg.company_name == "Apple Inc."
        assert pkg.financials is None
        assert pkg.filing_10k is None
        assert pkg.filing_10q is None
        assert pkg.news is None
        assert pkg.social is None
        # 5 warnings: financials, 10-K, 10-Q, news, social
        assert len(pkg.warnings) == 5

    @patch("tinyic.data.pipeline.fetch_social_sentiment")
    @patch("tinyic.data.pipeline.fetch_news")
    @patch("tinyic.data.pipeline.fetch_filings")
    @patch("tinyic.data.pipeline.fetch_financials")
    @patch("tinyic.data.pipeline.resolve_ticker")
    @patch("tinyic.data.pipeline._fetch_description")
    def test_build_data_package_context_string_budget(
        self, mock_desc, mock_resolve, mock_fin, mock_filings, mock_news, mock_social
    ):
        """Full DataPackage.to_context_string() is under 12000 chars."""
        mock_resolve.return_value = (True, "Apple Inc.")
        mock_desc.return_value = "Apple designs consumer electronics." * 5
        mock_fin.return_value = FinancialData(
            pe_ratio=28.5, pb_ratio=48.2, profit_margin=0.246,
            roe=1.56, debt_to_equity=178.7, market_cap=2_800_000_000_000,
            revenue=394_000_000_000, net_income=96_000_000_000,
            free_cash_flow=111_000_000_000, dividend_yield=0.0055,
            income_summary={"Total Revenue": 394_000_000_000, "Net Income": 96_000_000_000},
            balance_summary={"Total Assets": 352_000_000_000, "Total Debt": 111_000_000_000},
        )
        mock_filings.side_effect = [
            FilingSummary(form_type="10-K", text_summary="Business section..." * 100),
            FilingSummary(form_type="10-Q", text_summary="Quarterly update..." * 80),
        ]
        mock_news.return_value = NewsSummary(
            articles=[
                {"title": f"News article {i}", "publisher": "Reuters", "link": f"https://example.com/{i}"}
                for i in range(8)
            ]
        )
        mock_social.return_value = SocialSentiment(
            query="$AAPL Apple Inc.",
            summary="Mixed sentiment. Bullish on iPhone. Bearish on regulation." * 10,
        )

        pkg = build_data_package("AAPL")
        ctx = pkg.to_context_string()
        assert len(ctx) < 12000, f"Context string is {len(ctx)} chars, should be under 12000"


# ---------------------------------------------------------------------------
# Test Live API Integration (Plan 02 -- requires network)
# ---------------------------------------------------------------------------

class TestLiveAPIIntegration:
    """Live API integration tests -- requires network access.

    These tests are marked with @pytest.mark.live_api and skipped by default.
    Run with: uv run pytest -m live_api
    """

    @pytest.mark.live_api
    def test_live_aapl(self):
        """End-to-end build_data_package for AAPL with real APIs."""
        pkg = build_data_package("AAPL")
        assert pkg.ticker == "AAPL"
        assert "Apple" in pkg.company_name
        assert pkg.financials is not None
        assert pkg.financials.pe_ratio is not None
        ctx = pkg.to_context_string()
        assert len(ctx) < 12000, f"Context string is {len(ctx)} chars, should be under 12000"
        # Print for manual inspection
        print(f"\n--- DataPackage for AAPL ---")
        print(f"Company: {pkg.company_name}")
        print(f"Description: {pkg.description[:100] if pkg.description else 'None'}...")
        print(f"Financials: PE={pkg.financials.pe_ratio}, Revenue={pkg.financials.revenue}")
        print(f"Filing 10-K: {'Yes' if pkg.filing_10k else 'No'}")
        print(f"Filing 10-Q: {'Yes' if pkg.filing_10q else 'No'}")
        print(f"News: {len(pkg.news.articles) if pkg.news else 0} articles")
        print(f"Social: {'Yes' if pkg.social else 'No'}")
        print(f"Warnings: {pkg.warnings}")
        print(f"Context string length: {len(ctx)} chars")
