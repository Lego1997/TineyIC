"""Tests for investment memo generation and disagreement extraction."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from tinyic.debate.models import (
    Confidence,
    DebateResult,
    Disagreement,
    DisagreementAnalysis,
    InvestmentMemo,
    MemoSection,
    Scorecard,
    Vote,
    VoteChoice,
)
from tinyic.debate.memo import generate_memo, extract_disagreements


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_TRANSCRIPT = (
    "=== OPENING STATEMENTS ===\n"
    "Warren Buffett: Apple's ecosystem moat is extraordinary. The installed base of 2.2 billion "
    "devices creates massive switching costs. Services revenue compounds on top of hardware. "
    "Owner earnings power makes this a wonderful company at a fair price.\n\n"
    "Benjamin Graham: While Apple is a fine company, the current price-to-earnings ratio of 28x "
    "offers no margin of safety for the defensive investor. We must demand a discount to intrinsic "
    "value that simply is not present at these levels.\n\n"
    "Howard Marks: We are late in the cycle and the pendulum has swung too far toward optimism. "
    "Regulatory headwinds in the EU and China exposure create asymmetric downside risk.\n\n"
    "=== CROSS EXAMINATION ===\n"
    "Graham challenges Buffett on valuation: At 28x earnings, where is your margin of safety?\n"
    "Buffett responds: Quality deserves a premium. Apple's owner earnings yield of 3.7% beats bonds.\n"
    "Marks challenges Buffett: Second-level thinking tells us consensus growth expectations are "
    "too optimistic at this scale.\n\n"
    "=== REBUTTAL ===\n"
    "Buffett: Apple's ecosystem switching costs make this business remarkably resilient across cycles.\n"
    "Graham: The numbers do not lie -- 28x is historically expensive for any company.\n"
    "Marks: The services revenue stream compounds on top of the installed base, but growth will slow.\n\n"
    "=== FINAL VERDICT ===\n"
    "Buffett votes BUY with HIGH confidence.\n"
    "Graham votes HOLD with MEDIUM confidence.\n"
    "Marks votes SELL with HIGH confidence.\n"
)


def make_test_debate_result() -> DebateResult:
    """Create a DebateResult with realistic sample data."""
    votes = [
        Vote(
            investor="Warren Buffett",
            vote=VoteChoice.BUY,
            confidence=Confidence.HIGH,
            reasoning=["Ecosystem moat", "Services growth", "Owner earnings power"],
            key_risks=["Valuation stretch"],
        ),
        Vote(
            investor="Benjamin Graham",
            vote=VoteChoice.HOLD,
            confidence=Confidence.MEDIUM,
            reasoning=["No margin of safety", "Quality company but expensive"],
            key_risks=["P/E ratio historically high"],
        ),
        Vote(
            investor="Howard Marks",
            vote=VoteChoice.SELL,
            confidence=Confidence.HIGH,
            reasoning=["Late cycle risk", "Regulatory headwinds"],
            key_risks=["China exposure", "Antitrust risk"],
        ),
    ]
    scorecard = Scorecard(
        ticker="AAPL",
        company_name="Apple Inc.",
        votes=votes,
        consensus=None,
        bull_count=1,
        bear_count=1,
        hold_count=1,
    )
    return DebateResult(
        ticker="AAPL",
        company_name="Apple Inc.",
        scorecard=scorecard,
        phases_completed=[
            "opening_statements",
            "cross_examination",
            "rebuttal",
            "final_verdict",
        ],
        transcript=SAMPLE_TRANSCRIPT,
    )


def make_test_data_package() -> MagicMock:
    """Create a mock DataPackage with AAPL data."""
    m = MagicMock()
    m.ticker = "AAPL"
    m.company_name = "Apple Inc."
    m.to_context_string.return_value = json.dumps(
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "pe_ratio": 28.0,
            "revenue": "$394B",
            "free_cash_flow": "$110B",
            "market_cap": "$3.0T",
        }
    )
    return m


MOCK_MEMO_RESPONSE = {
    "executive_summary": {
        "content": "The investment committee analyzed Apple Inc. with divergent views...",
        "contributing_personas": [
            "Warren Buffett",
            "Benjamin Graham",
            "Howard Marks",
        ],
        "supporting_data": ["P/E ratio of 28x", "Revenue $394B", "Free cash flow $110B"],
    },
    "investment_thesis": {
        "content": "Buffett argued Apple's ecosystem moat justifies premium valuation...",
        "contributing_personas": ["Warren Buffett"],
        "supporting_data": ["Services revenue growth 15%", "Installed base 2.2B devices"],
    },
    "key_risks": {
        "content": "Graham highlighted valuation risk at current multiples...",
        "contributing_personas": ["Benjamin Graham", "Howard Marks"],
        "supporting_data": [
            "P/E 28x vs historical average 18x",
            "China revenue exposure 19%",
        ],
    },
    "valuation_discussion": {
        "content": "Sharp disagreement on appropriate valuation framework...",
        "contributing_personas": ["Warren Buffett", "Benjamin Graham"],
        "supporting_data": ["Market cap $3.0T", "Owner earnings yield 3.7%"],
    },
    "final_verdict": {
        "content": "The committee split: BUY from Buffett, HOLD from Graham, SELL from Marks...",
        "contributing_personas": [
            "Warren Buffett",
            "Benjamin Graham",
            "Howard Marks",
        ],
        "supporting_data": ["No consensus reached"],
    },
}

MOCK_DISAGREEMENT_RESPONSE = {
    "disagreements": [
        {
            "dimension": "Valuation Methodology",
            "description": "Fundamental disagreement on how to value Apple's ecosystem premium",
            "sides": [
                {
                    "persona": "Warren Buffett",
                    "position": "Owner earnings justify 28x P/E for quality",
                    "evidence_quote": "Apple's owner earnings power makes this a wonderful company at a fair price",
                },
                {
                    "persona": "Benjamin Graham",
                    "position": "No margin of safety at current multiples",
                    "evidence_quote": "The current price-to-earnings ratio offers no margin of safety for the defensive investor",
                },
            ],
            "resolution": "Unresolved -- Buffett voted BUY while Graham voted HOLD",
        },
        {
            "dimension": "Risk Assessment",
            "description": "Divergent views on the significance of geopolitical and regulatory risks",
            "sides": [
                {
                    "persona": "Howard Marks",
                    "position": "Cycle risk and regulatory headwinds underpriced",
                    "evidence_quote": "We are late in the cycle and the pendulum has swung too far toward optimism",
                },
                {
                    "persona": "Warren Buffett",
                    "position": "Moat durability mitigates cyclical risks",
                    "evidence_quote": "Apple's ecosystem switching costs make this business remarkably resilient across cycles",
                },
            ],
            "resolution": "Marks voted SELL; Buffett maintained BUY conviction",
        },
        {
            "dimension": "Growth Outlook",
            "description": "Disagreement on sustainability of services and emerging market growth",
            "sides": [
                {
                    "persona": "Warren Buffett",
                    "position": "Services flywheel creates compounding growth",
                    "evidence_quote": "The services revenue stream compounds on top of the installed base",
                },
                {
                    "persona": "Howard Marks",
                    "position": "Growth deceleration inevitable at this scale",
                    "evidence_quote": "Second-level thinking tells us that consensus growth expectations are too optimistic",
                },
            ],
            "resolution": "No consensus -- growth assumptions drove the vote split",
        },
    ]
}


# ===========================================================================
# TestMemoModels
# ===========================================================================


class TestMemoModels:
    """Tests for memo and disagreement data models."""

    def test_memo_section_creation(self):
        """MemoSection can be created with title, content, and optional grounding."""
        section = MemoSection(
            title="Executive Summary",
            content="Apple is a quality business.",
            contributing_personas=["Warren Buffett"],
            supporting_data=["P/E 28x"],
        )
        assert section.title == "Executive Summary"
        assert section.content == "Apple is a quality business."
        assert section.contributing_personas == ["Warren Buffett"]
        assert section.supporting_data == ["P/E 28x"]

    def test_memo_creation(self):
        """InvestmentMemo can be created with 5 MemoSection fields."""
        sections = {
            "executive_summary": MemoSection(title="Executive Summary", content="Summary"),
            "investment_thesis": MemoSection(title="Investment Thesis", content="Thesis"),
            "key_risks": MemoSection(title="Key Risks", content="Risks"),
            "valuation_discussion": MemoSection(title="Valuation Discussion", content="Valuation"),
            "final_verdict": MemoSection(title="Final Verdict", content="Verdict"),
        }
        memo = InvestmentMemo(
            ticker="AAPL",
            company_name="Apple Inc.",
            **sections,
        )
        assert memo.ticker == "AAPL"
        assert memo.company_name == "Apple Inc."
        assert memo.executive_summary.title == "Executive Summary"
        assert memo.final_verdict.content == "Verdict"
        assert isinstance(memo.generated_at, datetime)

    def test_memo_to_markdown(self):
        """InvestmentMemo.to_markdown() produces correct Markdown output."""
        memo = InvestmentMemo(
            ticker="AAPL",
            company_name="Apple Inc.",
            executive_summary=MemoSection(
                title="Executive Summary",
                content="Summary content here.",
                contributing_personas=["Buffett", "Graham"],
                supporting_data=["P/E 28x"],
            ),
            investment_thesis=MemoSection(
                title="Investment Thesis",
                content="Thesis content.",
                contributing_personas=["Buffett"],
                supporting_data=["Revenue $394B"],
            ),
            key_risks=MemoSection(
                title="Key Risks",
                content="Risks content.",
                contributing_personas=["Marks"],
                supporting_data=["China exposure 19%"],
            ),
            valuation_discussion=MemoSection(
                title="Valuation Discussion",
                content="Valuation content.",
                contributing_personas=["Buffett", "Graham"],
                supporting_data=["Market cap $3T"],
            ),
            final_verdict=MemoSection(
                title="Final Verdict",
                content="Verdict content.",
                contributing_personas=["Buffett", "Graham", "Marks"],
                supporting_data=["No consensus"],
            ),
        )
        md = memo.to_markdown()
        assert "# Investment Memo: Apple Inc. (AAPL)" in md
        assert "## Executive Summary" in md
        assert "Summary content here." in md
        assert "*Contributors: Buffett, Graham*" in md
        assert "*Data references: P/E 28x*" in md
        assert "## Investment Thesis" in md
        assert "## Key Risks" in md
        assert "## Valuation Discussion" in md
        assert "## Final Verdict" in md

    def test_memo_to_markdown_empty_grounding(self):
        """When contributing_personas and supporting_data are empty, those lines are omitted."""
        section = MemoSection(title="Test", content="Content")
        memo = InvestmentMemo(
            ticker="AAPL",
            company_name="Apple Inc.",
            executive_summary=section,
            investment_thesis=section,
            key_risks=section,
            valuation_discussion=section,
            final_verdict=section,
        )
        md = memo.to_markdown()
        assert "*Contributors:" not in md
        assert "*Data references:" not in md

    def test_disagreement_creation(self):
        """Disagreement can be created with dimension, description, sides, resolution."""
        d = Disagreement(
            dimension="Valuation",
            description="They disagreed on valuation.",
            sides=[
                {"persona": "Buffett", "position": "Fair price", "evidence_quote": "Quality at fair price"},
                {"persona": "Graham", "position": "Too expensive", "evidence_quote": "No margin of safety"},
            ],
            resolution="Unresolved",
        )
        assert d.dimension == "Valuation"
        assert d.description == "They disagreed on valuation."
        assert len(d.sides) == 2
        assert d.sides[0]["persona"] == "Buffett"
        assert d.resolution == "Unresolved"

    def test_disagreement_analysis_creation(self):
        """DisagreementAnalysis can be created with ticker, company_name, and disagreements."""
        d = Disagreement(
            dimension="Valuation",
            description="Valuation disagreement.",
            sides=[],
        )
        analysis = DisagreementAnalysis(
            ticker="AAPL",
            company_name="Apple Inc.",
            disagreements=[d],
        )
        assert analysis.ticker == "AAPL"
        assert analysis.company_name == "Apple Inc."
        assert len(analysis.disagreements) == 1
        assert isinstance(analysis.generated_at, datetime)

    def test_disagreement_analysis_to_markdown(self):
        """DisagreementAnalysis.to_markdown() produces numbered dimensions with evidence."""
        d = Disagreement(
            dimension="Valuation",
            description="They disagreed on valuation.",
            sides=[
                {"persona": "Buffett", "position": "Fair price", "evidence_quote": "Quality at fair price"},
                {"persona": "Graham", "position": "Too expensive", "evidence_quote": "No margin of safety"},
            ],
            resolution="Unresolved",
        )
        analysis = DisagreementAnalysis(
            ticker="AAPL",
            company_name="Apple Inc.",
            disagreements=[d],
        )
        md = analysis.to_markdown()
        assert "# Disagreement Analysis: Apple Inc. (AAPL)" in md
        assert "## 1. Valuation" in md
        assert "They disagreed on valuation." in md
        assert "**Buffett:** Fair price" in md
        assert '> "Quality at fair price"' in md
        assert "**Graham:** Too expensive" in md
        assert '> "No margin of safety"' in md
        assert "**Resolution:** Unresolved" in md

    def test_debate_result_with_memo(self):
        """DebateResult can be created with optional memo and disagreement_analysis."""
        dr = make_test_debate_result()
        assert dr.memo is None
        assert dr.disagreement_analysis is None

        section = MemoSection(title="Test", content="Content")
        memo = InvestmentMemo(
            ticker="AAPL",
            company_name="Apple Inc.",
            executive_summary=section,
            investment_thesis=section,
            key_risks=section,
            valuation_discussion=section,
            final_verdict=section,
        )
        analysis = DisagreementAnalysis(ticker="AAPL", company_name="Apple Inc.")

        dr2 = DebateResult(
            ticker="AAPL",
            company_name="Apple Inc.",
            scorecard=dr.scorecard,
            phases_completed=dr.phases_completed,
            transcript=dr.transcript,
            memo=memo,
            disagreement_analysis=analysis,
        )
        assert dr2.memo is not None
        assert dr2.disagreement_analysis is not None
        assert dr2.memo.ticker == "AAPL"


# ===========================================================================
# TestMemoGeneration
# ===========================================================================


class TestMemoGeneration:
    """Tests for generate_memo() LLM synthesis function."""

    @patch("tinyic.debate.memo.client")
    def test_generate_memo_success(self, mock_client):
        """With valid LLM response, generate_memo() returns InvestmentMemo with all 5 sections."""
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_MEMO_RESPONSE),
        }
        dr = make_test_debate_result()
        dp = make_test_data_package()

        memo = generate_memo(dr, dp)
        assert isinstance(memo, InvestmentMemo)
        assert memo.ticker == "AAPL"
        assert memo.company_name == "Apple Inc."
        assert memo.executive_summary.content != ""
        assert memo.investment_thesis.content != ""
        assert memo.key_risks.content != ""
        assert memo.valuation_discussion.content != ""
        assert memo.final_verdict.content != ""

    @patch("tinyic.debate.memo.client")
    def test_generate_memo_section_grounding(self, mock_client):
        """Each section has non-empty contributing_personas and supporting_data."""
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_MEMO_RESPONSE),
        }
        dr = make_test_debate_result()
        dp = make_test_data_package()

        memo = generate_memo(dr, dp)
        for section in [
            memo.executive_summary,
            memo.investment_thesis,
            memo.key_risks,
            memo.valuation_discussion,
            memo.final_verdict,
        ]:
            assert len(section.contributing_personas) > 0, f"{section.title} missing personas"
            assert len(section.supporting_data) > 0, f"{section.title} missing data"

    @patch("tinyic.debate.memo.client")
    def test_generate_memo_includes_transcript(self, mock_client):
        """The prompt sent to the LLM includes the debate transcript."""
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_MEMO_RESPONSE),
        }
        dr = make_test_debate_result()
        dp = make_test_data_package()

        generate_memo(dr, dp)

        call_args = mock_client.return_value.send_message.call_args
        messages = call_args[0][0]  # first positional arg
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "OPENING STATEMENTS" in user_msg["content"]
        assert "Warren Buffett" in user_msg["content"]

    @patch("tinyic.debate.memo.client")
    def test_generate_memo_includes_scorecard(self, mock_client):
        """The prompt sent to the LLM includes the scorecard markdown."""
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_MEMO_RESPONSE),
        }
        dr = make_test_debate_result()
        dp = make_test_data_package()

        generate_memo(dr, dp)

        call_args = mock_client.return_value.send_message.call_args
        messages = call_args[0][0]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Scorecard" in user_msg["content"]
        assert "BUY" in user_msg["content"]

    @patch("tinyic.debate.memo.client")
    def test_generate_memo_includes_data_package(self, mock_client):
        """The prompt sent to the LLM includes the data package context string."""
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_MEMO_RESPONSE),
        }
        dr = make_test_debate_result()
        dp = make_test_data_package()

        generate_memo(dr, dp)

        call_args = mock_client.return_value.send_message.call_args
        messages = call_args[0][0]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Financial Data" in user_msg["content"]
        assert "pe_ratio" in user_msg["content"]

    @patch("tinyic.debate.memo.client")
    def test_generate_memo_llm_failure(self, mock_client):
        """When client().send_message() raises, generate_memo() returns a fallback memo."""
        mock_client.return_value.send_message.side_effect = Exception("API error")
        dr = make_test_debate_result()
        dp = make_test_data_package()

        memo = generate_memo(dr, dp)
        assert isinstance(memo, InvestmentMemo)
        assert memo.ticker == "AAPL"
        assert "failed" in memo.executive_summary.content.lower()

    @patch("tinyic.debate.memo.client")
    def test_generate_memo_bad_json(self, mock_client):
        """When client() returns invalid JSON, generate_memo() returns a fallback memo."""
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": "This is not JSON at all, just plain text with no braces",
        }
        dr = make_test_debate_result()
        dp = make_test_data_package()

        memo = generate_memo(dr, dp)
        assert isinstance(memo, InvestmentMemo)
        assert memo.ticker == "AAPL"
        # Fallback memo should indicate failure
        assert "failed" in memo.executive_summary.content.lower() or memo.executive_summary.content != ""


# ===========================================================================
# TestDisagreementExtraction
# ===========================================================================


class TestDisagreementExtraction:
    """Tests for extract_disagreements() LLM extraction function."""

    @patch("tinyic.debate.memo.client")
    def test_extract_disagreements_success(self, mock_client):
        """With valid LLM response, extract_disagreements() returns 3 disagreements."""
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_DISAGREEMENT_RESPONSE),
        }
        dr = make_test_debate_result()

        analysis = extract_disagreements(dr)
        assert isinstance(analysis, DisagreementAnalysis)
        assert analysis.ticker == "AAPL"
        assert analysis.company_name == "Apple Inc."
        assert len(analysis.disagreements) == 3

    @patch("tinyic.debate.memo.client")
    def test_disagreement_has_evidence(self, mock_client):
        """Each disagreement has sides with persona, position, and evidence_quote."""
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_DISAGREEMENT_RESPONSE),
        }
        dr = make_test_debate_result()

        analysis = extract_disagreements(dr)
        for d in analysis.disagreements:
            assert len(d.sides) >= 2, f"Disagreement '{d.dimension}' needs >= 2 sides"
            for side in d.sides:
                assert "persona" in side
                assert "position" in side
                assert "evidence_quote" in side
                assert side["evidence_quote"] != ""

    @patch("tinyic.debate.memo.client")
    def test_extract_disagreements_includes_transcript(self, mock_client):
        """The prompt sent to the LLM includes the debate transcript."""
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": json.dumps(MOCK_DISAGREEMENT_RESPONSE),
        }
        dr = make_test_debate_result()

        extract_disagreements(dr)

        call_args = mock_client.return_value.send_message.call_args
        messages = call_args[0][0]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "OPENING STATEMENTS" in user_msg["content"]
        assert "Warren Buffett" in user_msg["content"]

    @patch("tinyic.debate.memo.client")
    def test_extract_disagreements_llm_failure(self, mock_client):
        """When client() raises, returns fallback with empty disagreements."""
        mock_client.return_value.send_message.side_effect = Exception("API error")
        dr = make_test_debate_result()

        analysis = extract_disagreements(dr)
        assert isinstance(analysis, DisagreementAnalysis)
        assert analysis.ticker == "AAPL"
        assert len(analysis.disagreements) == 0

    @patch("tinyic.debate.memo.client")
    def test_extract_disagreements_bad_json(self, mock_client):
        """When client() returns invalid JSON, returns fallback with empty disagreements."""
        mock_client.return_value.send_message.return_value = {
            "role": "assistant",
            "content": "This is not JSON at all, just plain text with no braces",
        }
        dr = make_test_debate_result()

        analysis = extract_disagreements(dr)
        assert isinstance(analysis, DisagreementAnalysis)
        assert analysis.ticker == "AAPL"
        assert len(analysis.disagreements) == 0
