"""Tests for the debate engine: models, prompts, and orchestrator."""

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
