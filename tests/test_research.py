"""Unit tests for Phase 12 deep research pipeline.

All external calls are mocked -- no network access or API keys required.
"""

import json
import os
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pytest

from tinyic.data.models import DataPackage, FinancialData, ResearchBrief
from tinyic.data.research import build_research_brief


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

MOCK_SEARCH_COMPANY = (
    "Apple Inc. has a powerful ecosystem moat driven by its integrated hardware-software "
    "platform with 2.2 billion active devices. Revenue is diversified across iPhone (52%), "
    "Services (22%), Mac (10%), iPad (8%), and Wearables (8%). Tim Cook has led disciplined "
    "capital allocation with $110B in buybacks over the past 3 years. The company's services "
    "segment has 40%+ gross margins and is growing at 15% YoY."
)

MOCK_SEARCH_ENVIRONMENT = (
    "The smartphone market is maturing globally with 1% annual growth, but Apple is gaining "
    "share in premium segments. AI integration into iOS (Apple Intelligence) is a key catalyst "
    "for the iPhone 17 cycle in Q4 2026. Regulatory headwinds include EU DMA compliance and "
    "ongoing DOJ antitrust scrutiny. Wall Street analysts are split: Morgan Stanley maintains "
    "Overweight with $260 PT citing services growth, while BofA has Neutral citing peak margins. "
    "Recent Q1 2026 earnings beat expectations with $124B revenue (+6% YoY)."
)

MOCK_SYNTHESIS_RESPONSE = {
    "business_model": (
        "Apple operates a vertically integrated ecosystem with 2.2B active devices. "
        "Revenue is diversified: iPhone 52%, Services 22%, Mac 10%, iPad 8%, Wearables 8%. "
        "The ecosystem moat creates high switching costs and enables premium pricing."
    ),
    "industry_trends": (
        "Global smartphone market growing at 1% annually, but Apple gaining premium share. "
        "AI integration across consumer devices is the dominant secular trend. "
        "EU DMA and US DOJ antitrust actions create regulatory headwinds."
    ),
    "management": (
        "Tim Cook has executed disciplined capital allocation: $110B in buybacks over 3 years. "
        "Services segment expansion under Cook has diversified revenue and improved margins. "
        "Management has navigated supply chain challenges and geopolitical tensions effectively."
    ),
    "recent_catalysts": (
        "Q1 2026 earnings beat: $124B revenue (+6% YoY) exceeded consensus. "
        "Apple Intelligence rollout across iOS creating iPhone 17 upgrade cycle expectations. "
        "Services growth sustained at 15% YoY with 40%+ gross margins."
    ),
    "analyst_perspectives": (
        "Bull case (Morgan Stanley, Overweight, $260 PT): Services flywheel and AI-driven "
        "upgrade cycle will drive earnings growth. Bear case (BofA, Neutral): Peak margins, "
        "regulatory risk, and China exposure limit upside at current valuations."
    ),
}


# ---------------------------------------------------------------------------
# Test: ResearchBrief Model
# ---------------------------------------------------------------------------

class TestResearchBriefModel:
    """Tests for the ResearchBrief Pydantic model."""

    def test_research_brief_creation(self):
        rb = ResearchBrief(
            business_model="Ecosystem moat",
            industry_trends="AI integration",
            management="Disciplined capital allocation",
            recent_catalysts="Earnings beat",
            analyst_perspectives="Bull/bear split",
        )
        assert rb.business_model == "Ecosystem moat"
        assert rb.industry_trends == "AI integration"
        assert rb.management == "Disciplined capital allocation"
        assert rb.recent_catalysts == "Earnings beat"
        assert rb.analyst_perspectives == "Bull/bear split"

    def test_research_brief_defaults(self):
        rb = ResearchBrief()
        assert rb.business_model == ""
        assert rb.industry_trends == ""
        assert rb.management == ""
        assert rb.recent_catalysts == ""
        assert rb.analyst_perspectives == ""

    def test_research_brief_serialization(self):
        rb = ResearchBrief(business_model="Test moat", analyst_perspectives="Bullish")
        json_str = rb.model_dump_json()
        restored = ResearchBrief.model_validate_json(json_str)
        assert restored.business_model == "Test moat"
        assert restored.analyst_perspectives == "Bullish"


# ---------------------------------------------------------------------------
# Test: build_research_brief
# ---------------------------------------------------------------------------

class TestBuildResearchBrief:
    """Tests for web search + LLM synthesis pipeline."""

    @patch("tinyic.data.research.client")
    @patch("tinyic.data.research._web_search")
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_build_research_brief_success(self, mock_search, mock_client):
        mock_search.side_effect = [MOCK_SEARCH_COMPANY, MOCK_SEARCH_ENVIRONMENT]
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_SYNTHESIS_RESPONSE),
        }
        result = build_research_brief("AAPL", "Apple Inc.", "Technology company")
        assert result is not None
        assert isinstance(result, ResearchBrief)
        assert "ecosystem" in result.business_model.lower()
        assert result.industry_trends != ""
        assert result.management != ""
        assert result.recent_catalysts != ""
        assert result.analyst_perspectives != ""

    @patch("tinyic.data.research.client")
    @patch("tinyic.data.research._web_search")
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_web_search_company_called(self, mock_search, mock_client):
        mock_search.side_effect = [MOCK_SEARCH_COMPANY, MOCK_SEARCH_ENVIRONMENT]
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_SYNTHESIS_RESPONSE),
        }
        build_research_brief("AAPL", "Apple Inc.", "Tech company")
        # First call is company-focused search
        company_query = mock_search.call_args_list[0][0][0]
        assert "AAPL" in company_query or "Apple" in company_query

    @patch("tinyic.data.research.client")
    @patch("tinyic.data.research._web_search")
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_web_search_environment_called(self, mock_search, mock_client):
        mock_search.side_effect = [MOCK_SEARCH_COMPANY, MOCK_SEARCH_ENVIRONMENT]
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_SYNTHESIS_RESPONSE),
        }
        build_research_brief("AAPL", "Apple Inc.", "Tech company")
        # Second call is environment-focused search
        env_query = mock_search.call_args_list[1][0][0]
        assert "AAPL" in env_query or "Apple" in env_query

    @patch("tinyic.data.research.client")
    @patch("tinyic.data.research._web_search")
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_synthesis_prompt_includes_search_results(self, mock_search, mock_client):
        mock_search.side_effect = [MOCK_SEARCH_COMPANY, MOCK_SEARCH_ENVIRONMENT]
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_SYNTHESIS_RESPONSE),
        }
        build_research_brief("AAPL", "Apple Inc.", "Tech company")
        # Check synthesis call includes search results
        messages = mock_client.return_value.send_message.call_args[0][0]
        user_msg = [m for m in messages if m["role"] == "user"][0]["content"]
        assert MOCK_SEARCH_COMPANY in user_msg or "ecosystem" in user_msg.lower()

    @patch("tinyic.data.research.client")
    @patch("tinyic.data.research._web_search")
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_synthesis_prompt_requests_json(self, mock_search, mock_client):
        mock_search.side_effect = [MOCK_SEARCH_COMPANY, MOCK_SEARCH_ENVIRONMENT]
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_SYNTHESIS_RESPONSE),
        }
        build_research_brief("AAPL", "Apple Inc.", "Tech company")
        messages = mock_client.return_value.send_message.call_args[0][0]
        system_msg = [m for m in messages if m["role"] == "system"][0]["content"]
        assert "business_model" in system_msg
        assert "industry_trends" in system_msg
        assert "management" in system_msg
        assert "recent_catalysts" in system_msg
        assert "analyst_perspectives" in system_msg
        assert "JSON" in system_msg

    @patch.dict(os.environ, {}, clear=True)
    def test_build_research_brief_no_api_key(self):
        # Ensure OPENAI_API_KEY is not set
        os.environ.pop("OPENAI_API_KEY", None)
        result = build_research_brief("AAPL", "Apple Inc.", None)
        assert result is None

    @patch("tinyic.data.research._web_search")
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_build_research_brief_search_failure(self, mock_search):
        mock_search.side_effect = Exception("Network error")
        result = build_research_brief("AAPL", "Apple Inc.", None)
        assert result is None

    @patch("tinyic.data.research.client")
    @patch("tinyic.data.research._web_search")
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_build_research_brief_synthesis_failure(self, mock_search, mock_client):
        mock_search.side_effect = [MOCK_SEARCH_COMPANY, MOCK_SEARCH_ENVIRONMENT]
        mock_client.return_value.send_message.side_effect = Exception("LLM error")
        result = build_research_brief("AAPL", "Apple Inc.", None)
        assert result is None

    @patch("tinyic.data.research.client")
    @patch("tinyic.data.research._web_search")
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_build_research_brief_bad_json(self, mock_search, mock_client):
        mock_search.side_effect = [MOCK_SEARCH_COMPANY, MOCK_SEARCH_ENVIRONMENT]
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": "This is not valid JSON at all",
        }
        result = build_research_brief("AAPL", "Apple Inc.", None)
        assert result is None

    @patch("tinyic.data.research.client")
    @patch("tinyic.data.research._web_search")
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_build_research_brief_partial_search(self, mock_search, mock_client):
        # First search fails, second succeeds
        mock_search.side_effect = [Exception("Timeout"), MOCK_SEARCH_ENVIRONMENT]
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_SYNTHESIS_RESPONSE),
        }
        result = build_research_brief("AAPL", "Apple Inc.", "Tech company")
        # Should still succeed with partial data
        assert result is not None
        assert isinstance(result, ResearchBrief)


# ---------------------------------------------------------------------------
# Test: Pipeline integration
# ---------------------------------------------------------------------------

class TestPipelineResearchIntegration:
    """Tests for build_data_package deep_research parameter."""

    @patch("tinyic.data.pipeline.fetch_social_sentiment", return_value=None)
    @patch("tinyic.data.pipeline.fetch_news", return_value=None)
    @patch("tinyic.data.pipeline.fetch_filings", return_value=None)
    @patch("tinyic.data.pipeline.fetch_financials", return_value=None)
    @patch("tinyic.data.pipeline._fetch_description", return_value="A tech company")
    @patch("tinyic.data.pipeline.resolve_ticker", return_value=(True, "AAPL", "Apple Inc."))
    @patch("tinyic.data.pipeline.build_research_brief")
    def test_build_data_package_with_research(self, mock_research, *mocks):
        mock_research.return_value = ResearchBrief(business_model="Test moat")
        from tinyic.data.pipeline import build_data_package
        pkg = build_data_package("AAPL", deep_research=True)
        assert pkg.research_brief is not None
        assert pkg.research_brief.business_model == "Test moat"
        mock_research.assert_called_once()

    @patch("tinyic.data.pipeline.fetch_social_sentiment", return_value=None)
    @patch("tinyic.data.pipeline.fetch_news", return_value=None)
    @patch("tinyic.data.pipeline.fetch_filings", return_value=None)
    @patch("tinyic.data.pipeline.fetch_financials", return_value=None)
    @patch("tinyic.data.pipeline._fetch_description", return_value="A tech company")
    @patch("tinyic.data.pipeline.resolve_ticker", return_value=(True, "AAPL", "Apple Inc."))
    @patch("tinyic.data.pipeline.build_research_brief")
    def test_build_data_package_without_research(self, mock_research, *mocks):
        from tinyic.data.pipeline import build_data_package
        pkg = build_data_package("AAPL", deep_research=False)
        assert pkg.research_brief is None
        mock_research.assert_not_called()

    @patch("tinyic.data.pipeline.fetch_social_sentiment", return_value=None)
    @patch("tinyic.data.pipeline.fetch_news", return_value=None)
    @patch("tinyic.data.pipeline.fetch_filings", return_value=None)
    @patch("tinyic.data.pipeline.fetch_financials", return_value=None)
    @patch("tinyic.data.pipeline._fetch_description", return_value="A tech company")
    @patch("tinyic.data.pipeline.resolve_ticker", return_value=(True, "AAPL", "Apple Inc."))
    @patch("tinyic.data.pipeline.build_research_brief")
    def test_build_data_package_default_deep_research(self, mock_research, *mocks):
        mock_research.return_value = ResearchBrief(business_model="Default on")
        from tinyic.data.pipeline import build_data_package
        pkg = build_data_package("AAPL")  # No deep_research arg -- should default True
        mock_research.assert_called_once()

    @patch("tinyic.data.pipeline.fetch_social_sentiment", return_value=None)
    @patch("tinyic.data.pipeline.fetch_news", return_value=None)
    @patch("tinyic.data.pipeline.fetch_filings", return_value=None)
    @patch("tinyic.data.pipeline.fetch_financials", return_value=None)
    @patch("tinyic.data.pipeline._fetch_description", return_value="A tech company")
    @patch("tinyic.data.pipeline.resolve_ticker", return_value=(True, "AAPL", "Apple Inc."))
    @patch("tinyic.data.pipeline.build_research_brief", return_value=None)
    def test_build_data_package_research_failure_adds_warning(self, *mocks):
        from tinyic.data.pipeline import build_data_package
        pkg = build_data_package("AAPL", deep_research=True)
        assert pkg.research_brief is None
        assert any("research" in w.lower() for w in pkg.warnings)

    def test_context_string_includes_research(self):
        rb = ResearchBrief(
            business_model="Strong ecosystem moat",
            analyst_perspectives="Bullish consensus",
        )
        pkg = DataPackage(
            ticker="AAPL",
            company_name="Apple Inc.",
            fetched_at=datetime.now(),
            research_brief=rb,
        )
        ctx = pkg.to_context_string()
        assert "Strong ecosystem moat" in ctx
        assert "Bullish consensus" in ctx

    def test_context_string_without_research(self):
        pkg = DataPackage(
            ticker="AAPL",
            company_name="Apple Inc.",
            fetched_at=datetime.now(),
        )
        ctx = pkg.to_context_string()
        assert "research_brief" not in ctx
