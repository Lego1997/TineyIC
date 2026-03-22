"""Tests for the debate engine: models, prompts, orchestrator, extraction, and scorecard."""

from unittest.mock import MagicMock, patch

import pytest

from tinyic.constants import MAX_PERSONAS, MIN_PERSONAS
from tinyic.data.models import DataPackage
from tinyic.debate.models import (
    Confidence,
    DebatePhase,
    DebateResult,
    Scorecard,
    Vote,
    VoteChoice,
)
from tinyic.debate.extraction import extract_votes, build_scorecard, _parse_list_field, _parse_bool
from tinyic.debate.orchestrator import DebateOrchestrator
from tinyic.debate.prompts import CONTEXT_PREAMBLE, PHASE_PROMPTS
from tinytroupe.environment.tiny_world import TinyWorld


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_persona(name: str) -> MagicMock:
    """Create a mock persona that satisfies TinyWorld.add_agent requirements."""
    m = MagicMock()
    m.name = name
    m.environment = None
    m.act.return_value = [{"type": "TALK", "content": f"{name} says something"}]
    m.pop_latest_actions.return_value = [
        {"type": "TALK", "content": f"{name} says something", "target": ""}
    ]
    return m


def make_mock_data_package() -> MagicMock:
    """Create a mock DataPackage with AAPL data."""
    m = MagicMock(spec=DataPackage)
    m.ticker = "AAPL"
    m.company_name = "Apple Inc."
    m.to_context_string.return_value = '{"ticker": "AAPL"}'
    return m


@pytest.fixture(autouse=True)
def clear_environments():
    """Clear TinyWorld.all_environments before and after each test."""
    TinyWorld.all_environments.clear()
    yield
    TinyWorld.all_environments.clear()


# ===========================================================================
# TestDebateModels
# ===========================================================================


class TestDebateModels:
    """Tests for debate data models and enums."""

    def test_debate_phase_values(self):
        """All 6 DebatePhase members exist with correct string values."""
        assert DebatePhase.SETUP.value == "setup"
        assert DebatePhase.OPENING.value == "opening_statements"
        assert DebatePhase.CROSS_EXAM.value == "cross_examination"
        assert DebatePhase.REBUTTAL.value == "rebuttal"
        assert DebatePhase.VERDICT.value == "final_verdict"
        assert DebatePhase.COMPLETE.value == "complete"
        assert len(DebatePhase) == 6

    def test_vote_choice_values(self):
        """BUY, HOLD, SELL enum members exist."""
        assert VoteChoice.BUY.value == "BUY"
        assert VoteChoice.HOLD.value == "HOLD"
        assert VoteChoice.SELL.value == "SELL"
        assert len(VoteChoice) == 3

    def test_vote_fuzzy_validator(self):
        """Fuzzy matching maps 'STRONG BUY' -> BUY, etc."""
        assert Vote(investor="A", vote="STRONG BUY", confidence="HIGH").vote == VoteChoice.BUY
        assert Vote(investor="B", vote="CONDITIONAL SELL", confidence="LOW").vote == VoteChoice.SELL
        assert Vote(investor="C", vote="MAYBE HOLD", confidence="MEDIUM").vote == VoteChoice.HOLD
        # Exact match also works
        assert Vote(investor="D", vote="BUY", confidence="HIGH").vote == VoteChoice.BUY

    def test_scorecard_to_markdown(self):
        """Scorecard.to_markdown() renders a valid markdown table."""
        votes = [
            Vote(investor="Graham", vote="BUY", confidence="HIGH", reasoning=["cheap P/E", "strong balance sheet"]),
            Vote(investor="Marks", vote="SELL", confidence="MEDIUM", reasoning=["overvalued", "cycle peak"]),
        ]
        sc = Scorecard(
            ticker="AAPL",
            company_name="Apple Inc.",
            votes=votes,
            consensus=VoteChoice.BUY,
            bull_count=1,
            bear_count=1,
            hold_count=0,
        )
        md = sc.to_markdown()
        assert "Apple Inc." in md
        assert "AAPL" in md
        assert "| Investor |" in md
        assert "Graham" in md
        assert "Marks" in md
        assert "BUY" in md
        assert "SELL" in md
        assert "Bulls: 1" in md
        assert "Bears: 1" in md

    def test_scorecard_consensus(self):
        """Consensus reflects majority when set."""
        votes = [
            Vote(investor="A", vote="BUY", confidence="HIGH"),
            Vote(investor="B", vote="BUY", confidence="MEDIUM"),
            Vote(investor="C", vote="SELL", confidence="LOW"),
        ]
        sc = Scorecard(
            ticker="MSFT",
            company_name="Microsoft",
            votes=votes,
            consensus=VoteChoice.BUY,
            bull_count=2,
            bear_count=1,
            hold_count=0,
        )
        assert sc.consensus == VoteChoice.BUY
        assert sc.bull_count == 2

    def test_debate_result_creation(self):
        """DebateResult can be constructed with all fields."""
        sc = Scorecard(
            ticker="GOOG",
            company_name="Alphabet",
            votes=[],
            consensus=VoteChoice.HOLD,
        )
        result = DebateResult(
            ticker="GOOG",
            company_name="Alphabet",
            scorecard=sc,
            phases_completed=["opening_statements", "cross_examination"],
        )
        assert result.ticker == "GOOG"
        assert len(result.phases_completed) == 2
        assert result.created_at is not None


# ===========================================================================
# TestDebateOrchestrator
# ===========================================================================


class TestDebateOrchestrator:
    """Tests for the DebateOrchestrator TinyWorld subclass."""

    def test_init_min_personas(self):
        """Passing fewer than MIN_PERSONAS raises ValueError."""
        dp = make_mock_data_package()
        with pytest.raises(ValueError, match="At least"):
            DebateOrchestrator(name="test", personas=[make_mock_persona("A")], data_package=dp)

    def test_init_max_personas(self):
        """Passing more than MAX_PERSONAS raises ValueError."""
        dp = make_mock_data_package()
        personas = [make_mock_persona(f"P{i}") for i in range(MAX_PERSONAS + 1)]
        with pytest.raises(ValueError, match="At most"):
            DebateOrchestrator(name="test", personas=personas, data_package=dp)

    def test_init_valid(self):
        """Valid persona count creates orchestrator in SETUP phase."""
        dp = make_mock_data_package()
        personas = [make_mock_persona(f"P{i}") for i in range(MIN_PERSONAS)]
        orch = DebateOrchestrator(name="test_valid", personas=personas, data_package=dp)
        assert orch.current_phase == DebatePhase.SETUP
        assert len(orch.agents) == MIN_PERSONAS
        assert orch.data_package is dp

    def test_inject_context(self):
        """inject_context() broadcasts data to all agents via listen()."""
        dp = make_mock_data_package()
        personas = [make_mock_persona("Alpha"), make_mock_persona("Beta")]
        orch = DebateOrchestrator(name="test_ctx", personas=personas, data_package=dp)

        orch.inject_context()

        # broadcast() calls agent.listen() for each agent
        for agent in orch.agents:
            agent.listen.assert_called()
            call_args = agent.listen.call_args
            speech = call_args[0][0]
            assert "AAPL" in speech
            assert "Apple Inc." in speech

    def test_four_phases(self):
        """run_debate() completes all 4 phases and marks COMPLETE."""
        dp = make_mock_data_package()
        personas = [make_mock_persona("Alpha"), make_mock_persona("Beta")]
        orch = DebateOrchestrator(name="test_phases", personas=personas, data_package=dp)

        orch.run_debate()

        assert orch.current_phase == DebatePhase.COMPLETE
        assert orch.is_complete is True
        assert len(orch._phase_history) == 4
        expected = [p.value for p in DebateOrchestrator.PHASE_ORDER]
        assert orch._phase_history == expected

    def test_sequential_turns(self):
        """Agents act in stable order (not shuffled) during each phase."""
        dp = make_mock_data_package()
        personas = [make_mock_persona("First"), make_mock_persona("Second"), make_mock_persona("Third")]
        orch = DebateOrchestrator(name="test_seq", personas=personas, data_package=dp)

        orch.run_debate()

        # Each agent should have been called to act() 4 times (once per phase)
        for agent in orch.agents:
            assert agent.act.call_count == 4

    def test_context_injection(self):
        """After inject_context(), all agents received the broadcast."""
        dp = make_mock_data_package()
        personas = [make_mock_persona("X"), make_mock_persona("Y"), make_mock_persona("Z")]
        orch = DebateOrchestrator(name="test_ctx2", personas=personas, data_package=dp)

        orch.inject_context()

        for agent in orch.agents:
            agent.listen.assert_called_once()
            speech = agent.listen.call_args[0][0]
            assert "AAPL" in speech

    def test_phase_prompts_used(self):
        """broadcast_internal_goal is called with text from PHASE_PROMPTS."""
        dp = make_mock_data_package()
        personas = [make_mock_persona("Alpha"), make_mock_persona("Beta")]
        orch = DebateOrchestrator(name="test_prompts", personas=personas, data_package=dp)

        orch.run_debate()

        # internalize_goal is called once per phase per agent = 4 calls per agent
        for agent in orch.agents:
            assert agent.internalize_goal.call_count == 4
            # Collect all goal texts
            goal_texts = [call[0][0] for call in agent.internalize_goal.call_args_list]
            # Verify each phase prompt was used (with company name substituted)
            for phase in DebateOrchestrator.PHASE_ORDER:
                expected_prompt = PHASE_PROMPTS[phase].format(company="Apple Inc.")
                assert expected_prompt in goal_texts

    def test_extra_step_after_complete(self):
        """Calling _step after all phases returns empty dict and stays COMPLETE."""
        dp = make_mock_data_package()
        personas = [make_mock_persona("A"), make_mock_persona("B")]
        orch = DebateOrchestrator(name="test_extra", personas=personas, data_package=dp)

        orch.run_debate()
        assert orch.is_complete

        # Extra step should be a no-op
        result = orch._step()
        assert result == {}
        assert orch.current_phase == DebatePhase.COMPLETE


# ===========================================================================
# TestVoteExtraction
# ===========================================================================


class TestVoteExtraction:
    """Tests for extract_votes() using mocked ResultsExtractor."""

    @patch("tinyic.debate.extraction.ResultsExtractor")
    def test_extract_votes_success(self, MockExtractor):
        """extract_votes returns parsed Vote objects from extraction results."""
        mock_extractor = MockExtractor.return_value
        mock_extractor.extract_results_from_agents.return_value = [
            {
                "vote": "BUY",
                "confidence": "HIGH",
                "reasoning": "- strong moat\n- good management",
                "key_risks": "- valuation high",
                "changed_mind": "false",
            },
            {
                "vote": "SELL",
                "confidence": "MEDIUM",
                "reasoning": "- overvalued\n- cycle peak",
                "key_risks": "- revenue slowdown",
                "changed_mind": "true",
            },
        ]

        dp = make_mock_data_package()
        personas = [make_mock_persona("Alpha"), make_mock_persona("Beta")]
        orch = DebateOrchestrator(name="test_ev1", personas=personas, data_package=dp)

        votes = extract_votes(orch)

        assert len(votes) == 2
        assert votes[0].investor == "Alpha"
        assert votes[0].vote == VoteChoice.BUY
        assert votes[0].confidence == Confidence.HIGH
        # Reasoning was parsed from string to list
        assert isinstance(votes[0].reasoning, list)
        assert len(votes[0].reasoning) == 2
        assert "strong moat" in votes[0].reasoning[0]
        assert votes[0].changed_mind is False

        assert votes[1].investor == "Beta"
        assert votes[1].vote == VoteChoice.SELL
        assert votes[1].changed_mind is True

    @patch("tinyic.debate.extraction.ResultsExtractor")
    def test_extract_votes_fuzzy_vote(self, MockExtractor):
        """Fuzzy vote matching: 'STRONG BUY' -> BUY."""
        mock_extractor = MockExtractor.return_value
        mock_extractor.extract_results_from_agents.return_value = [
            {"vote": "STRONG BUY", "confidence": "HIGH"},
            {"vote": "CONDITIONAL SELL", "confidence": "MEDIUM"},
        ]

        dp = make_mock_data_package()
        personas = [make_mock_persona("Buffett"), make_mock_persona("Graham")]
        orch = DebateOrchestrator(name="test_fuzzy", personas=personas, data_package=dp)

        votes = extract_votes(orch)

        assert len(votes) == 2
        assert votes[0].vote == VoteChoice.BUY
        assert votes[1].vote == VoteChoice.SELL

    @patch("tinyic.debate.extraction.ResultsExtractor")
    def test_extract_votes_extraction_failure(self, MockExtractor):
        """When extraction raises, fallback HOLD/LOW votes are returned."""
        mock_extractor = MockExtractor.return_value
        mock_extractor.extract_results_from_agents.side_effect = Exception("API error")

        dp = make_mock_data_package()
        personas = [make_mock_persona("Alpha"), make_mock_persona("Beta")]
        orch = DebateOrchestrator(name="test_fail", personas=personas, data_package=dp)

        votes = extract_votes(orch)

        assert len(votes) == 2
        for v in votes:
            assert v.vote == VoteChoice.HOLD
            assert v.confidence == Confidence.LOW
            assert "Extraction failed" in v.reasoning

    @patch("tinyic.debate.extraction.ResultsExtractor")
    def test_extract_votes_missing_fields(self, MockExtractor):
        """Missing fields get sensible defaults (confidence=MEDIUM, reasoning=[])."""
        mock_extractor = MockExtractor.return_value
        mock_extractor.extract_results_from_agents.return_value = [
            {"vote": "BUY"},
            {"vote": "SELL"},
        ]

        dp = make_mock_data_package()
        personas = [make_mock_persona("Sparse"), make_mock_persona("Minimal")]
        orch = DebateOrchestrator(name="test_sparse", personas=personas, data_package=dp)

        votes = extract_votes(orch)

        assert len(votes) == 2
        # Check first vote has sensible defaults
        assert votes[0].vote == VoteChoice.BUY
        assert votes[0].confidence == Confidence.MEDIUM
        assert votes[0].reasoning == []
        assert votes[0].key_risks == []
        assert votes[0].changed_mind is False
        # Second vote also gets defaults
        assert votes[1].vote == VoteChoice.SELL
        assert votes[1].confidence == Confidence.MEDIUM


# ===========================================================================
# TestBuildScorecard
# ===========================================================================


class TestBuildScorecard:
    """Tests for build_scorecard() consensus and aggregation logic."""

    def test_scorecard_consensus_majority(self):
        """3 BUY + 1 SELL -> consensus=BUY, bull_count=3, bear_count=1."""
        votes = [
            Vote(investor="A", vote="BUY", confidence="HIGH"),
            Vote(investor="B", vote="BUY", confidence="MEDIUM"),
            Vote(investor="C", vote="BUY", confidence="LOW"),
            Vote(investor="D", vote="SELL", confidence="HIGH"),
        ]
        sc = build_scorecard(votes, "AAPL", "Apple Inc.")

        assert sc.consensus == VoteChoice.BUY
        assert sc.bull_count == 3
        assert sc.bear_count == 1
        assert sc.hold_count == 0
        assert sc.ticker == "AAPL"
        assert sc.company_name == "Apple Inc."

    def test_scorecard_consensus_tie(self):
        """2 BUY + 2 SELL -> consensus=None (no strict majority)."""
        votes = [
            Vote(investor="A", vote="BUY", confidence="HIGH"),
            Vote(investor="B", vote="BUY", confidence="MEDIUM"),
            Vote(investor="C", vote="SELL", confidence="HIGH"),
            Vote(investor="D", vote="SELL", confidence="MEDIUM"),
        ]
        sc = build_scorecard(votes, "MSFT", "Microsoft")

        assert sc.consensus is None
        assert sc.bull_count == 2
        assert sc.bear_count == 2

    def test_scorecard_to_markdown_format(self):
        """Markdown output contains table headers, investor names, vote values."""
        votes = [
            Vote(investor="Buffett", vote="BUY", confidence="HIGH", reasoning=["wide moat"]),
            Vote(investor="Graham", vote="HOLD", confidence="MEDIUM", reasoning=["fair value"]),
        ]
        sc = build_scorecard(votes, "GOOG", "Alphabet")
        md = sc.to_markdown()

        assert "| Investor |" in md
        assert "| Vote |" in md
        assert "Buffett" in md
        assert "Graham" in md
        assert "BUY" in md
        assert "HOLD" in md
        assert "Alphabet" in md
        assert "GOOG" in md


# ===========================================================================
# TestRunDebate
# ===========================================================================


class TestRunDebate:
    """Tests for the top-level run_debate() convenience function."""

    def test_run_debate_min_personas(self):
        """run_debate with 1 persona name raises ValueError."""
        with pytest.raises(ValueError, match="At least"):
            from tinyic.debate import run_debate
            run_debate("AAPL", ["warren_buffett"])

    @patch("tinyic.debate.build_scorecard")
    @patch("tinyic.debate.extract_votes")
    @patch("tinyic.debate.DebateOrchestrator")
    @patch("tinyic.data.pipeline.build_data_package")
    @patch("tinyic.personas.registry.load_persona")
    def test_run_debate_wiring(self, mock_load, mock_build_dp, mock_orch_cls, mock_extract, mock_scorecard):
        """run_debate wires orchestrator, extraction, and scorecard correctly."""
        from tinyic.debate import run_debate

        # Setup mocks
        mock_persona_a = MagicMock()
        mock_persona_a.name = "Warren Buffett"
        mock_persona_b = MagicMock()
        mock_persona_b.name = "Benjamin Graham"
        mock_load.side_effect = [mock_persona_a, mock_persona_b]

        mock_dp = make_mock_data_package()
        mock_build_dp.return_value = mock_dp

        mock_orch = mock_orch_cls.return_value
        mock_orch._phase_history = ["opening_statements", "cross_examination", "rebuttal", "final_verdict"]
        mock_orch.pretty_current_interactions.return_value = "Debate transcript..."

        mock_votes = [
            Vote(investor="Warren Buffett", vote="BUY", confidence="HIGH"),
            Vote(investor="Benjamin Graham", vote="HOLD", confidence="MEDIUM"),
        ]
        mock_extract.return_value = mock_votes

        mock_sc = Scorecard(
            ticker="AAPL",
            company_name="Apple Inc.",
            votes=mock_votes,
            consensus=VoteChoice.BUY,
            bull_count=1,
            bear_count=0,
            hold_count=1,
        )
        mock_scorecard.return_value = mock_sc

        # Execute
        result = run_debate("AAPL", ["warren_buffett", "benjamin_graham"])

        # Verify all components were called
        assert mock_load.call_count == 2
        mock_build_dp.assert_called_once_with("AAPL")
        mock_orch_cls.assert_called_once()
        mock_orch.run_debate.assert_called_once()
        mock_extract.assert_called_once_with(mock_orch)
        mock_scorecard.assert_called_once()

        # Verify result
        assert isinstance(result, DebateResult)
        assert result.ticker == "AAPL"
        assert result.company_name == "Apple Inc."
        assert result.scorecard is mock_sc
        assert len(result.phases_completed) == 4
        assert result.transcript == "Debate transcript..."
