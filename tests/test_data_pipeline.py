"""Unit tests for Phase 3 data pipeline (Plan 01).

All yfinance calls are mocked -- no network access required.
"""

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
                "title": "Apple Reports Record Q4 Revenue",
                "publisher": "Reuters",
                "link": "https://example.com/1",
                "providerPublishTime": 1700000000,
            },
            {
                "title": "Apple Vision Pro Sales Surge",
                "publisher": "Bloomberg",
                "link": "https://example.com/2",
                "providerPublishTime": 1700100000,
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
            {"title": f"Article {i}", "publisher": "Test", "link": f"https://example.com/{i}"}
            for i in range(10)
        ]
        mock_ticker_cls.return_value = mock_instance

        result = fetch_news("AAPL", count=3)
        assert result is not None
        # get_news is called with count=3
        mock_instance.get_news.assert_called_once_with(count=3)
        # Articles sliced to count
        assert len(result.articles) <= 3
