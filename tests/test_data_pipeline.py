"""Unit tests for Phase 3 data pipeline (Plan 01 + Plan 02).

All external calls are mocked -- no network access required.
Plan 01: models, ticker_resolver, financials, news (21 tests)
Plan 02: filings, social, pipeline + live API integration (14+ tests)
"""

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import tinyic.data.pipeline as pipeline_module
from tinyic.data.models import (
    DataPackage,
    FinancialData,
    FilingSummary,
    NewsSummary,
    ResearchBrief,
    SocialSentiment,
)
from tinyic.data.ticker_resolver import resolve_ticker
from tinyic.data.financials import fetch_financials
from tinyic.data.news import fetch_news
from tinyic.data.filings import fetch_filings
from tinyic.data.social import fetch_social_sentiment
from tinyic.data.pipeline import build_data_package
from tinyic.debate.control import DebateStopRequested, RunControl


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
    """Test ticker resolution with enhanced resolver."""

    @patch("yfinance.Ticker")
    def test_resolve_ticker_valid(self, mock_ticker_cls):
        """Valid ticker returns (True, symbol, company_name)."""
        mock_instance = MagicMock()
        mock_instance.info = {"longName": "Apple Inc.", "shortName": "Apple"}
        mock_ticker_cls.return_value = mock_instance

        is_valid, symbol, name = resolve_ticker("AAPL")
        assert is_valid is True
        assert symbol == "AAPL"
        assert name == "Apple Inc."

    @patch("yfinance.Ticker")
    def test_resolve_ticker_invalid_falls_through(self, mock_ticker_cls):
        """Invalid ticker with no search results returns (False, '', '')."""
        mock_instance = MagicMock()
        mock_instance.info = {}
        mock_ticker_cls.return_value = mock_instance

        with patch("yfinance.Search") as mock_search:
            mock_search.return_value = MagicMock(quotes=[])
            is_valid, symbol, name = resolve_ticker("XYZNOTREAL")
            assert is_valid is False
            assert symbol == ""
            assert name == ""

    @patch("yfinance.Ticker")
    def test_resolve_ticker_exception_falls_through(self, mock_ticker_cls):
        """Exception in .info falls through to search."""
        mock_ticker_cls.side_effect = Exception("Network error")

        with patch("yfinance.Search") as mock_search:
            mock_search.return_value = MagicMock(quotes=[])
            is_valid, symbol, name = resolve_ticker("AAPL")
            assert is_valid is False
            assert symbol == ""
            assert name == ""

    @patch("yfinance.Ticker")
    def test_resolve_ticker_normalizes_input(self, mock_ticker_cls):
        """Ticker input is uppercased and stripped."""
        mock_instance = MagicMock()
        mock_instance.info = {"longName": "Apple Inc."}
        mock_ticker_cls.return_value = mock_instance

        is_valid, symbol, name = resolve_ticker("aapl ")
        assert is_valid is True
        assert name == "Apple Inc."
        mock_ticker_cls.assert_called_once_with("AAPL")

    @patch("yfinance.Ticker")
    def test_resolve_ticker_shortname_fallback(self, mock_ticker_cls):
        """Falls back to shortName when longName is missing."""
        mock_instance = MagicMock()
        mock_instance.info = {"shortName": "Apple"}
        mock_ticker_cls.return_value = mock_instance

        is_valid, symbol, name = resolve_ticker("AAPL")
        assert is_valid is True
        assert name == "Apple"


class TestResolveTickerNameSearch:
    """Test company name resolution via yfinance Search."""

    @patch("yfinance.Search")
    def test_name_search_apple(self, mock_search):
        """'Apple' resolves to AAPL via search."""
        mock_search.return_value = MagicMock(quotes=[
            {"symbol": "AAPL", "longname": "Apple Inc.", "shortname": "Apple Inc.", "quoteType": "EQUITY"},
        ])
        is_valid, symbol, name = resolve_ticker("Apple")
        assert is_valid is True
        assert symbol == "AAPL"
        assert name == "Apple Inc."

    @patch("yfinance.Search")
    def test_name_search_tencent(self, mock_search):
        """'Tencent' resolves to 0700.HK via search."""
        mock_search.return_value = MagicMock(quotes=[
            {"symbol": "0700.HK", "longname": "Tencent Holdings Limited", "shortname": "TENCENT", "quoteType": "EQUITY"},
        ])
        is_valid, symbol, name = resolve_ticker("Tencent")
        assert is_valid is True
        assert symbol == "0700.HK"
        assert name == "Tencent Holdings Limited"

    @patch("yfinance.Search")
    def test_name_search_prefers_equity(self, mock_search):
        """Search prefers EQUITY results over other quote types."""
        mock_search.return_value = MagicMock(quotes=[
            {"symbol": "AAPL240621C00100000", "longname": "AAPL Option", "quoteType": "OPTION"},
            {"symbol": "AAPL", "longname": "Apple Inc.", "shortname": "Apple", "quoteType": "EQUITY"},
        ])
        is_valid, symbol, name = resolve_ticker("Apple")
        assert is_valid is True
        assert symbol == "AAPL"
        assert name == "Apple Inc."

    @patch("yfinance.Search")
    def test_name_search_no_results(self, mock_search):
        """Search with no results returns invalid."""
        mock_search.return_value = MagicMock(quotes=[])
        # Also need to patch Ticker for the fallback path
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value = MagicMock(info={})
            is_valid, symbol, name = resolve_ticker("xyznonexistentcompany")
            assert is_valid is False

    @patch("yfinance.Search")
    def test_name_search_exception(self, mock_search):
        """Search exception falls through gracefully."""
        mock_search.side_effect = Exception("API error")
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value = MagicMock(info={})
            is_valid, symbol, name = resolve_ticker("Apple")
            assert is_valid is False


class TestResolveTickerInternational:
    """Test international ticker resolution."""

    @patch("yfinance.Ticker")
    def test_hong_kong_ticker(self, mock_ticker_cls):
        """0700.HK resolves to Tencent."""
        mock_instance = MagicMock()
        mock_instance.info = {"longName": "Tencent Holdings Limited"}
        mock_ticker_cls.return_value = mock_instance

        is_valid, symbol, name = resolve_ticker("0700.HK")
        assert is_valid is True
        assert symbol == "0700.HK"
        assert name == "Tencent Holdings Limited"
        mock_ticker_cls.assert_called_once_with("0700.HK")

    @patch("yfinance.Ticker")
    def test_tokyo_ticker(self, mock_ticker_cls):
        """7203.T resolves to Toyota."""
        mock_instance = MagicMock()
        mock_instance.info = {"longName": "Toyota Motor Corporation"}
        mock_ticker_cls.return_value = mock_instance

        is_valid, symbol, name = resolve_ticker("7203.T")
        assert is_valid is True
        assert symbol == "7203.T"

    @patch("yfinance.Ticker")
    def test_german_ticker(self, mock_ticker_cls):
        """SAP.DE resolves correctly."""
        mock_instance = MagicMock()
        mock_instance.info = {"longName": "SAP SE"}
        mock_ticker_cls.return_value = mock_instance

        is_valid, symbol, name = resolve_ticker("SAP.DE")
        assert is_valid is True
        assert symbol == "SAP.DE"

    @patch("yfinance.Ticker")
    def test_lowercase_international_normalizes(self, mock_ticker_cls):
        """sap.de normalizes to SAP.DE."""
        mock_instance = MagicMock()
        mock_instance.info = {"longName": "SAP SE"}
        mock_ticker_cls.return_value = mock_instance

        is_valid, symbol, name = resolve_ticker("sap.de")
        assert is_valid is True
        mock_ticker_cls.assert_called_once_with("SAP.DE")


class TestResolveTickerFallback:
    """Test fallback chain behavior: .info -> fast_info -> Search -> invalid."""

    def test_info_fails_fast_info_succeeds(self):
        """When .info returns no name but fast_info validates, use Search for name."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_instance = MagicMock()
            mock_instance.info = {}  # .info fails (no name)
            mock_instance.fast_info.currency = "USD"  # fast_info succeeds
            mock_ticker.return_value = mock_instance

            with patch("yfinance.Search") as mock_search:
                mock_search.return_value = MagicMock(quotes=[
                    {"symbol": "AAPL", "longname": "Apple Inc.", "quoteType": "EQUITY"},
                ])
                is_valid, symbol, name = resolve_ticker("AAPL")
                assert is_valid is True
                assert symbol == "AAPL"
                assert name == "Apple Inc."

    def test_info_fails_fast_info_fails_search_succeeds(self):
        """When both .info and fast_info fail, fall back to Search API."""
        with patch("yfinance.Ticker") as mock_ticker:
            from unittest.mock import PropertyMock
            mock_instance = MagicMock()
            mock_instance.info = {}
            type(mock_instance.fast_info).currency = PropertyMock(side_effect=KeyError("currency"))
            mock_ticker.return_value = mock_instance

            with patch("yfinance.Search") as mock_search:
                mock_search.return_value = MagicMock(quotes=[
                    {"symbol": "AAPL", "longname": "Apple Inc.", "quoteType": "EQUITY"},
                ])
                is_valid, symbol, name = resolve_ticker("AAPL")
                assert is_valid is True
                assert symbol == "AAPL"
                assert name == "Apple Inc."

    def test_info_exception_search_succeeds(self):
        """When .info throws, fall back through fast_info and Search."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.side_effect = Exception("HTTP 500")

            with patch("yfinance.Search") as mock_search:
                mock_search.return_value = MagicMock(quotes=[
                    {"symbol": "MSFT", "longname": "Microsoft Corporation", "quoteType": "EQUITY"},
                ])
                is_valid, symbol, name = resolve_ticker("MSFT")
                assert is_valid is True
                assert symbol == "MSFT"

    def test_fast_info_valid_but_search_empty_returns_symbol_as_name(self):
        """When fast_info confirms validity but Search returns nothing, use symbol as name."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_instance = MagicMock()
            mock_instance.info = {}
            mock_instance.fast_info.currency = "HKD"
            mock_ticker.return_value = mock_instance

            with patch("yfinance.Search") as mock_search:
                mock_search.return_value = MagicMock(quotes=[])
                is_valid, symbol, name = resolve_ticker("0700.HK")
                assert is_valid is True
                assert symbol == "0700.HK"
                assert name == "0700.HK"  # Falls back to symbol as name

    def test_all_strategies_fail(self):
        """When all fallback strategies fail, returns invalid."""
        with patch("yfinance.Ticker") as mock_ticker:
            from unittest.mock import PropertyMock
            mock_instance = MagicMock()
            mock_instance.info = {}
            type(mock_instance.fast_info).currency = PropertyMock(side_effect=KeyError("currency"))
            mock_ticker.return_value = mock_instance

            with patch("yfinance.Search") as mock_search:
                mock_search.return_value = MagicMock(quotes=[])
                is_valid, symbol, name = resolve_ticker("XYZNOTREAL")
                assert is_valid is False
                assert symbol == ""
                assert name == ""

    def test_empty_input(self):
        """Empty input returns invalid immediately."""
        is_valid, symbol, name = resolve_ticker("")
        assert is_valid is False
        assert symbol == ""
        assert name == ""

    def test_whitespace_input(self):
        """Whitespace-only input returns invalid."""
        is_valid, symbol, name = resolve_ticker("   ")
        assert is_valid is False


class TestLooksLikeTicker:
    """Test the ticker vs name heuristic."""

    def test_standard_us_tickers(self):
        from tinyic.data.ticker_resolver import _looks_like_ticker
        assert _looks_like_ticker("AAPL") is True
        assert _looks_like_ticker("MSFT") is True
        assert _looks_like_ticker("A") is True

    def test_international_tickers(self):
        from tinyic.data.ticker_resolver import _looks_like_ticker
        assert _looks_like_ticker("0700.HK") is True
        assert _looks_like_ticker("SAP.DE") is True
        assert _looks_like_ticker("7203.T") is True
        assert _looks_like_ticker("MC.PA") is True

    def test_company_names(self):
        from tinyic.data.ticker_resolver import _looks_like_ticker
        assert _looks_like_ticker("Apple") is False
        assert _looks_like_ticker("Tencent Holdings") is False
        assert _looks_like_ticker("toyota motor") is False


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
        # Setup mock chain: html() returns None to skip HTMLParser path,
        # markdown() returns structured markdown for section extraction
        mock_filing = MagicMock()
        mock_filing.filing_date = "2025-10-30"
        mock_filing.html.return_value = None
        mock_filing.markdown.return_value = (
            "# Item 1 - Business\n" + "Business description content. " * 50 + "\n"
            "# Item 1A - Risk Factors\n" + "Risk factors content. " * 50 + "\n"
            "# Item 7 - MD&A\n" + "Management discussion content. " * 50 + "\n"
        )

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
        mock_filing.html.return_value = None
        mock_filing.markdown.return_value = (
            "# Part I - Financial Information\n" + "Financial statements. " * 30 + "\n"
            "# Item 1 - Financial Statements\n" + "Quarterly financials. " * 30 + "\n"
            "# Item 2 - MD&A\n" + "Quarterly discussion. " * 30 + "\n"
        )

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
        # HTMLParser path fails, fallback to markdown() with long text
        mock_filing.html.return_value = None
        mock_filing.markdown.return_value = "X" * 5000  # 5000 chars, should be truncated

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
        request = mock_client.responses.create.call_args.kwargs
        assert request["tools"] == [{"type": "x_search"}]
        assert request["model"] == "grok-4.3"

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

    @patch("tinyic.data.pipeline.build_research_brief")
    @patch("tinyic.data.pipeline.fetch_social_sentiment")
    @patch("tinyic.data.pipeline.fetch_news")
    @patch("tinyic.data.pipeline.fetch_filings")
    @patch("tinyic.data.pipeline.fetch_financials")
    @patch("tinyic.data.pipeline.resolve_ticker")
    @patch("tinyic.data.pipeline._fetch_description")
    def test_build_data_package_valid(
        self, mock_desc, mock_resolve, mock_fin, mock_filings, mock_news, mock_social,
        mock_research,
    ):
        """All sources succeed -> DataPackage with all fields, no warnings."""
        mock_resolve.return_value = (True, "AAPL", "Apple Inc.")
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
        mock_research.return_value = ResearchBrief(business_model="Ecosystem moat")

        pkg = build_data_package("AAPL")
        assert pkg.ticker == "AAPL"
        assert pkg.company_name == "Apple Inc."
        assert pkg.financials is not None
        assert pkg.filing_10k is not None
        assert pkg.filing_10q is not None
        assert pkg.news is not None
        assert pkg.social is not None
        assert pkg.research_brief is not None
        assert len(pkg.warnings) == 0
        assert pkg.fetched_at.utcoffset() == timedelta(0)

    @patch("tinyic.data.pipeline.resolve_ticker")
    def test_build_data_package_invalid_ticker(self, mock_resolve):
        """Invalid ticker raises ValueError."""
        mock_resolve.return_value = (False, "", "")

        with pytest.raises(ValueError, match="Invalid ticker"):
            build_data_package("XYZNOTREAL")

    def test_stop_after_source_prevents_all_later_network_calls(
        self, monkeypatch
    ):
        control = RunControl()
        calls: list[str] = []

        def record(name, value=None, *, stop=False):
            def operation(*_args):
                calls.append(name)
                if stop:
                    control.stop()
                return value

            return operation

        monkeypatch.setattr(
            pipeline_module,
            "resolve_ticker",
            record("resolve", (True, "AAPL", "Apple Inc.")),
        )
        monkeypatch.setattr(
            pipeline_module,
            "_fetch_description",
            record("description", "Apple"),
        )
        monkeypatch.setattr(
            pipeline_module,
            "fetch_financials",
            record("financials", None, stop=True),
        )
        for name in (
            "fetch_filings",
            "fetch_news",
            "fetch_social_sentiment",
            "detect_cn_market",
            "fetch_cn_market_data",
            "build_research_brief",
        ):
            monkeypatch.setattr(
                pipeline_module,
                name,
                record(name),
            )

        with pytest.raises(DebateStopRequested):
            build_data_package("AAPL", checkpoint=control.check_stop)
        assert calls == ["resolve", "description", "financials"]

    def test_stop_after_social_prevents_paid_research(self, monkeypatch):
        control = RunControl()
        research_called = False

        monkeypatch.setattr(
            pipeline_module,
            "resolve_ticker",
            lambda _ticker: (True, "AAPL", "Apple Inc."),
        )
        monkeypatch.setattr(pipeline_module, "_fetch_description", lambda _t: None)
        monkeypatch.setattr(pipeline_module, "fetch_financials", lambda _t: None)
        monkeypatch.setattr(
            pipeline_module, "fetch_filings", lambda _t, _form: None
        )
        monkeypatch.setattr(pipeline_module, "fetch_news", lambda _t: None)

        def stop_after_social(_ticker, _company):
            control.stop()
            return None

        def research(*_args):
            nonlocal research_called
            research_called = True

        monkeypatch.setattr(
            pipeline_module,
            "fetch_social_sentiment",
            stop_after_social,
        )
        monkeypatch.setattr(pipeline_module, "detect_cn_market", lambda _t: None)
        monkeypatch.setattr(pipeline_module, "build_research_brief", research)

        with pytest.raises(DebateStopRequested):
            build_data_package("AAPL", checkpoint=control.check_stop)
        assert research_called is False

    @patch("tinyic.data.pipeline.build_research_brief", return_value=None)
    @patch("tinyic.data.pipeline.fetch_social_sentiment")
    @patch("tinyic.data.pipeline.fetch_news")
    @patch("tinyic.data.pipeline.fetch_filings")
    @patch("tinyic.data.pipeline.fetch_financials")
    @patch("tinyic.data.pipeline.resolve_ticker")
    @patch("tinyic.data.pipeline._fetch_description")
    def test_build_data_package_partial_failure(
        self, mock_desc, mock_resolve, mock_fin, mock_filings, mock_news, mock_social,
        mock_research,
    ):
        """Some sources fail -> DataPackage has warnings for failed sources."""
        mock_resolve.return_value = (True, "AAPL", "Apple Inc.")
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
        # 4 warnings: 10-Q, news, social, deep research
        assert len(pkg.warnings) == 4
        assert any("10-Q" in w for w in pkg.warnings)
        assert any("news" in w.lower() for w in pkg.warnings)
        assert any("sentiment" in w.lower() or "twitter" in w.lower() for w in pkg.warnings)
        assert any("research" in w.lower() for w in pkg.warnings)

    @patch("tinyic.data.pipeline.build_research_brief", return_value=None)
    @patch("tinyic.data.pipeline.fetch_social_sentiment")
    @patch("tinyic.data.pipeline.fetch_news")
    @patch("tinyic.data.pipeline.fetch_filings")
    @patch("tinyic.data.pipeline.fetch_financials")
    @patch("tinyic.data.pipeline.resolve_ticker")
    @patch("tinyic.data.pipeline._fetch_description")
    def test_build_data_package_all_sources_fail(
        self, mock_desc, mock_resolve, mock_fin, mock_filings, mock_news, mock_social,
        mock_research,
    ):
        """All data sources fail -> DataPackage has 6 warnings but doesn't crash."""
        mock_resolve.return_value = (True, "AAPL", "Apple Inc.")
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
        # 6 warnings: financials, 10-K, 10-Q, news, social, deep research
        assert len(pkg.warnings) == 6

    @patch("tinyic.data.pipeline.build_research_brief")
    @patch("tinyic.data.pipeline.fetch_social_sentiment")
    @patch("tinyic.data.pipeline.fetch_news")
    @patch("tinyic.data.pipeline.fetch_filings")
    @patch("tinyic.data.pipeline.fetch_financials")
    @patch("tinyic.data.pipeline.resolve_ticker")
    @patch("tinyic.data.pipeline._fetch_description")
    def test_build_data_package_context_string_budget(
        self, mock_desc, mock_resolve, mock_fin, mock_filings, mock_news, mock_social,
        mock_research,
    ):
        """Full DataPackage.to_context_string() is under 20000 chars."""
        mock_resolve.return_value = (True, "AAPL", "Apple Inc.")
        mock_desc.return_value = "Apple designs consumer electronics." * 5
        mock_research.return_value = ResearchBrief(business_model="Ecosystem moat" * 50)
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
        assert len(ctx) < 20000, f"Context string is {len(ctx)} chars, should be under 20000"


# ---------------------------------------------------------------------------
# Test Live API Integration (Plan 02 -- requires network)
# ---------------------------------------------------------------------------

class TestLiveAPIIntegration:
    """Live API integration tests -- requires network access.

    These tests are marked with @pytest.mark.live_api and skipped by default.
    Run with: uv run pytest -m live_api
    """

    @pytest.mark.live_api
    @pytest.mark.timeout(120)
    def test_live_aapl(self):
        """End-to-end build_data_package for AAPL with real APIs.

        Note: This test only requires network access (yfinance + edgartools),
        NOT an OPENAI_API_KEY. It will pass without API keys when run with
        ``-m live_api``. The other 6 live_api tests skip via the has_api_key
        fixture when no key is present.
        """
        pkg = build_data_package("AAPL")
        assert pkg.ticker == "AAPL"
        assert "Apple" in pkg.company_name
        assert pkg.financials is not None
        assert pkg.financials.pe_ratio is not None
        ctx = pkg.to_context_string()
        assert len(ctx) < 20000, f"Context string is {len(ctx)} chars, should be under 20000"
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
