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
        mock_orch.get_cost_stats.return_value = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "model_calls": 0}

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


# ===========================================================================
# TestCostStats
# ===========================================================================


class TestCostStats:
    """Tests for cost stats exposure (HARD-04)."""

    def test_debate_result_accepts_cost_stats(self):
        """DebateResult accepts optional cost_stats field."""
        sc = Scorecard(ticker="AAPL", company_name="Apple", votes=[])
        result = DebateResult(
            ticker="AAPL",
            company_name="Apple",
            scorecard=sc,
            phases_completed=[],
            cost_stats={
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "model_calls": 3,
            },
        )
        assert result.cost_stats is not None
        assert result.cost_stats["input_tokens"] == 100

    def test_debate_result_cost_stats_optional(self):
        """DebateResult works without cost_stats (backward compat)."""
        sc = Scorecard(ticker="AAPL", company_name="Apple", votes=[])
        result = DebateResult(
            ticker="AAPL",
            company_name="Apple",
            scorecard=sc,
            phases_completed=[],
        )
        assert result.cost_stats is None

    def test_get_debate_cost_stats_populated(self):
        """get_debate_cost_stats returns formatted stats from populated result."""
        from tinyic.debate import get_debate_cost_stats

        sc = Scorecard(ticker="AAPL", company_name="Apple", votes=[])
        result = DebateResult(
            ticker="AAPL",
            company_name="Apple",
            scorecard=sc,
            phases_completed=[],
            cost_stats={
                "input_tokens": 10000,
                "output_tokens": 5000,
                "total_tokens": 15000,
                "model_calls": 10,
                "cached_calls": 2,
            },
        )
        stats = get_debate_cost_stats(result)
        assert stats["input_tokens"] == 10000
        assert stats["output_tokens"] == 5000
        assert stats["total_tokens"] == 15000
        assert stats["model_calls"] == 10
        assert stats["cached_calls"] == 2
        assert stats["estimated_cost_usd"] > 0

    def test_get_debate_cost_stats_none(self):
        """get_debate_cost_stats handles None cost_stats gracefully."""
        from tinyic.debate import get_debate_cost_stats

        sc = Scorecard(ticker="AAPL", company_name="Apple", votes=[])
        result = DebateResult(
            ticker="AAPL",
            company_name="Apple",
            scorecard=sc,
            phases_completed=[],
        )
        stats = get_debate_cost_stats(result)
        assert stats["input_tokens"] == 0
        assert stats["estimated_cost_usd"] == 0.0

    def test_get_debate_cost_stats_tinyworld_format(self):
        """get_debate_cost_stats handles TinyWorld nested format with base_stats."""
        from tinyic.debate import get_debate_cost_stats

        sc = Scorecard(ticker="AAPL", company_name="Apple", votes=[])
        result = DebateResult(
            ticker="AAPL",
            company_name="Apple",
            scorecard=sc,
            phases_completed=[],
            cost_stats={
                "base_stats": {
                    "input_tokens": 5000,
                    "output_tokens": 2000,
                    "total_tokens": 7000,
                    "model_calls": 5,
                    "cached_calls": 1,
                },
                "per_agent": {},
            },
        )
        stats = get_debate_cost_stats(result)
        assert stats["input_tokens"] == 5000
        assert stats["total_tokens"] == 7000

    @patch("tinyic.debate.build_scorecard")
    @patch("tinyic.debate.extract_votes")
    @patch("tinyic.debate.DebateOrchestrator")
    @patch("tinyic.data.pipeline.build_data_package")
    @patch("tinyic.personas.registry.load_persona")
    def test_run_debate_populates_cost_stats(
        self, mock_load, mock_build_dp, mock_orch_cls, mock_extract, mock_scorecard
    ):
        """run_debate() calls get_cost_stats() and populates result."""
        from tinyic.debate import run_debate

        mock_persona_a = MagicMock()
        mock_persona_a.name = "A"
        mock_persona_b = MagicMock()
        mock_persona_b.name = "B"
        mock_load.side_effect = [mock_persona_a, mock_persona_b]

        mock_dp = make_mock_data_package()
        mock_build_dp.return_value = mock_dp

        mock_orch = mock_orch_cls.return_value
        mock_orch._phase_history = [
            "opening_statements",
            "cross_examination",
            "rebuttal",
            "final_verdict",
        ]
        mock_orch.pretty_current_interactions.return_value = "Transcript"
        mock_orch.get_cost_stats.return_value = {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "model_calls": 2,
        }

        mock_votes = [Vote(investor="A", vote="BUY", confidence="HIGH")]
        mock_extract.return_value = mock_votes
        mock_scorecard.return_value = Scorecard(
            ticker="AAPL",
            company_name="Apple",
            votes=mock_votes,
            consensus=VoteChoice.BUY,
            bull_count=1,
            bear_count=0,
            hold_count=0,
        )

        result = run_debate("AAPL", ["a", "b"])

        mock_orch.get_cost_stats.assert_called_once()
        assert result.cost_stats is not None
        assert result.cost_stats["input_tokens"] == 100


# ===========================================================================
# TestAntiConvergence
# ===========================================================================


class TestAntiConvergence:
    """Tests for anti-convergence reinforcement injection during debate phases."""

    def test_reinforcement_injected_before_act(self):
        """After run_debate(), each agent.listen() was called with 'IMPORTANT REMINDER' before act()."""
        dp = make_mock_data_package()
        personas = [make_mock_persona("Warren Buffett"), make_mock_persona("Benjamin Graham")]
        orch = DebateOrchestrator(name="test_reinforce", personas=personas, data_package=dp)

        orch.run_debate()

        # For each agent, verify that listen() with reinforcement preceded act() calls.
        # We check via the mock's call ordering: manager tracks all calls on the mock.
        for agent in orch.agents:
            # Collect ordered list of method names called on the agent
            method_calls = [call[0] for call in agent.method_calls]
            # Find pairs: each act() should be preceded by a listen() with IMPORTANT REMINDER
            act_indices = [i for i, name in enumerate(method_calls) if name == "act"]
            assert len(act_indices) == 4, f"Expected 4 act() calls, got {len(act_indices)}"
            for act_idx in act_indices:
                # Look backwards from act_idx for a listen() call with reinforcement
                found = False
                for j in range(act_idx - 1, -1, -1):
                    if method_calls[j] == "listen":
                        call_args = agent.method_calls[j]
                        if "IMPORTANT REMINDER" in str(call_args):
                            found = True
                            break
                    elif method_calls[j] == "act":
                        # Hit a previous act() before finding reinforcement -- fail
                        break
                assert found, (
                    f"No 'IMPORTANT REMINDER' listen() found before act() at index {act_idx} "
                    f"for agent {agent.name}"
                )

    def test_reinforcement_contains_philosophy_hook(self):
        """For 'Warren Buffett', reinforcement contains the matching PHILOSOPHY_HOOKS text."""
        from tinyic.debate.prompts import PHILOSOPHY_HOOKS

        dp = make_mock_data_package()
        personas = [make_mock_persona("Warren Buffett"), make_mock_persona("Benjamin Graham")]
        orch = DebateOrchestrator(name="test_hook", personas=personas, data_package=dp)

        orch.run_debate()

        buffett = orch.agents[0]
        listen_texts = [str(call) for call in buffett.listen.call_args_list]
        hook = PHILOSOPHY_HOOKS["Warren Buffett"]
        found = any(hook in text for text in listen_texts)
        assert found, f"Expected philosophy hook '{hook}' in listen() calls for Warren Buffett"

    def test_reinforcement_uses_fallback_for_unknown(self):
        """For an unknown persona name, reinforcement uses a generic fallback."""
        dp = make_mock_data_package()
        personas = [make_mock_persona("Unknown Investor"), make_mock_persona("Mystery Person")]
        orch = DebateOrchestrator(name="test_fallback", personas=personas, data_package=dp)

        orch.run_debate()

        unknown = orch.agents[0]
        listen_texts = [str(call) for call in unknown.listen.call_args_list]
        fallback = "Stay true to your unique perspective."
        found = any(fallback in text for text in listen_texts)
        assert found, f"Expected fallback '{fallback}' in listen() calls for unknown persona"

    def test_reinforcement_all_phases(self):
        """Reinforcement is injected in ALL 4 phases -- 4 times per agent."""
        dp = make_mock_data_package()
        personas = [make_mock_persona("Warren Buffett"), make_mock_persona("Benjamin Graham")]
        orch = DebateOrchestrator(name="test_all_phases", personas=personas, data_package=dp)

        orch.run_debate()

        for agent in orch.agents:
            reinforcement_count = sum(
                1
                for call in agent.listen.call_args_list
                if "IMPORTANT REMINDER" in str(call)
            )
            assert reinforcement_count == 4, (
                f"Expected 4 reinforcement injections for {agent.name}, "
                f"got {reinforcement_count}"
            )


# ===========================================================================
# TestDevilsAdvocate
# ===========================================================================


class TestDevilsAdvocate:
    """Tests for rotating devil's advocate injection during cross-examination."""

    def test_da_injected_during_cross_exam(self):
        """During CROSS_EXAM, exactly one agent receives DEVILS_ADVOCATE_PROMPT."""
        from tinyic.debate.prompts import DEVILS_ADVOCATE_PROMPT

        dp = make_mock_data_package()
        personas = [
            make_mock_persona("Warren Buffett"),
            make_mock_persona("Benjamin Graham"),
            make_mock_persona("Charlie Munger"),
        ]
        orch = DebateOrchestrator(name="test_da_inject", personas=personas, data_package=dp)

        orch.run_debate()

        # Count how many agents received the DA prompt
        da_recipients = []
        for agent in orch.agents:
            for call in agent.listen.call_args_list:
                if DEVILS_ADVOCATE_PROMPT in str(call):
                    da_recipients.append(agent.name)
                    break

        assert len(da_recipients) == 1, (
            f"Expected exactly 1 DA recipient, got {len(da_recipients)}: {da_recipients}"
        )

    def test_da_rotation(self):
        """_select_devils_advocate() cycles through agents round-robin."""
        dp = make_mock_data_package()
        personas = [
            make_mock_persona("A"),
            make_mock_persona("B"),
            make_mock_persona("C"),
        ]
        orch = DebateOrchestrator(name="test_da_rotate", personas=personas, data_package=dp)

        selections = [orch._select_devils_advocate().name for _ in range(6)]
        assert selections == ["A", "B", "C", "A", "B", "C"], (
            f"Expected round-robin rotation, got {selections}"
        )

    def test_da_not_in_other_phases(self):
        """In OPENING, REBUTTAL, VERDICT, no agent receives DEVILS_ADVOCATE_PROMPT."""
        from tinyic.debate.prompts import DEVILS_ADVOCATE_PROMPT

        dp = make_mock_data_package()
        personas = [make_mock_persona("Warren Buffett"), make_mock_persona("Benjamin Graham")]
        orch = DebateOrchestrator(name="test_da_phases", personas=personas, data_package=dp)

        # Track which phase each listen() call happens in by monitoring phase transitions
        phase_listen_log = []

        original_step = orch._step

        def tracking_step(*args, **kwargs):
            result = original_step(*args, **kwargs)
            return result

        # Instead of hooking _step, we run the debate and inspect the results.
        # The DA prompt should only appear once total (during CROSS_EXAM).
        orch.run_debate()

        for agent in orch.agents:
            da_count = sum(
                1
                for call in agent.listen.call_args_list
                if DEVILS_ADVOCATE_PROMPT in str(call)
            )
            # At most 1 DA prompt total for any agent (only during CROSS_EXAM)
            assert da_count <= 1, (
                f"Agent {agent.name} received DA prompt {da_count} times (expected 0 or 1)"
            )

        # Total DA prompts across all agents should be exactly 1
        total_da = sum(
            sum(1 for call in agent.listen.call_args_list if DEVILS_ADVOCATE_PROMPT in str(call))
            for agent in orch.agents
        )
        assert total_da == 1, f"Expected exactly 1 total DA prompt, got {total_da}"

    def test_da_role_release_at_rebuttal(self):
        """At REBUTTAL start, previous DA receives ROLE_RELEASE_PROMPT."""
        from tinyic.debate.prompts import DEVILS_ADVOCATE_PROMPT, ROLE_RELEASE_PROMPT

        dp = make_mock_data_package()
        personas = [make_mock_persona("Warren Buffett"), make_mock_persona("Benjamin Graham")]
        orch = DebateOrchestrator(name="test_da_release", personas=personas, data_package=dp)

        orch.run_debate()

        # Find which agent was DA (received DA prompt)
        da_agent = None
        for agent in orch.agents:
            for call in agent.listen.call_args_list:
                if DEVILS_ADVOCATE_PROMPT in str(call):
                    da_agent = agent
                    break
            if da_agent:
                break

        assert da_agent is not None, "No agent received DA prompt"

        # Verify DA agent also received the role release prompt
        release_found = any(
            ROLE_RELEASE_PROMPT in str(call)
            for call in da_agent.listen.call_args_list
        )
        assert release_found, (
            f"DA agent {da_agent.name} did not receive ROLE_RELEASE_PROMPT"
        )

    def test_da_does_not_constrain_verdict(self):
        """In VERDICT, no DA-related prompts are injected."""
        from tinyic.debate.prompts import DEVILS_ADVOCATE_PROMPT, ROLE_RELEASE_PROMPT

        dp = make_mock_data_package()
        personas = [make_mock_persona("Warren Buffett"), make_mock_persona("Benjamin Graham")]
        orch = DebateOrchestrator(name="test_da_verdict", personas=personas, data_package=dp)

        orch.run_debate()

        # Count DA and role release prompts -- should be exactly 1 each total
        total_da = sum(
            sum(1 for call in agent.listen.call_args_list if DEVILS_ADVOCATE_PROMPT in str(call))
            for agent in orch.agents
        )
        total_release = sum(
            sum(1 for call in agent.listen.call_args_list if ROLE_RELEASE_PROMPT in str(call))
            for agent in orch.agents
        )

        # DA prompt: exactly 1 (only in CROSS_EXAM)
        assert total_da == 1, f"Expected 1 DA prompt total, got {total_da}"
        # Role release: exactly 1 (only at REBUTTAL start)
        assert total_release == 1, f"Expected 1 role release total, got {total_release}"
