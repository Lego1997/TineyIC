"""Tests for Phase 5 UI components: orchestrator callbacks and helpers."""

import pytest
from unittest.mock import MagicMock

from tinytroupe.environment.tiny_world import TinyWorld

from tinyic.debate.orchestrator import DebateOrchestrator
from tinyic.debate.models import DebatePhase


def make_mock_persona(name):
    """Create a mock persona that works with TinyWorld.add_agent."""
    m = MagicMock()
    m.name = name
    m.environment = None
    m.act.return_value = [{"type": "TALK", "content": f"{name} says something"}]
    m.pop_latest_actions.return_value = [
        {"type": "TALK", "content": f"{name} says something", "target": ""}
    ]
    return m


def make_mock_data_package():
    m = MagicMock()
    m.ticker = "AAPL"
    m.company_name = "Apple Inc."
    m.to_context_string.return_value = '{"ticker": "AAPL"}'
    return m


@pytest.fixture(autouse=True)
def _clear_environments():
    """Clean up TinyWorld global state between tests."""
    TinyWorld.all_environments = {}
    yield
    TinyWorld.all_environments = {}


class TestOrchestratorCallbacks:
    """Test that streaming callbacks fire correctly."""

    def test_callbacks_called_in_order(self):
        """Verify on_phase_start, on_agent_start, on_agent_done fire in correct order."""
        personas = [make_mock_persona("Alice"), make_mock_persona("Bob")]
        dp = make_mock_data_package()
        orch = DebateOrchestrator(name="test", personas=personas, data_package=dp)

        call_log = []
        orch.on_phase_start = lambda phase: call_log.append(("phase_start", phase))
        orch.on_agent_start = lambda name, phase: call_log.append(("agent_start", name, phase))
        orch.on_agent_done = lambda name, phase, actions: call_log.append(("agent_done", name, phase))

        orch.run_debate()

        # 4 phases x (1 phase_start + 2 agents x (agent_start + agent_done)) = 4 x 5 = 20 events
        assert len(call_log) == 20

        # First event is phase_start for opening_statements
        assert call_log[0] == ("phase_start", "opening_statements")

        # Within each phase: phase_start, then agent_start/done for each agent
        phase_events = call_log[:5]  # First phase
        assert phase_events[0] == ("phase_start", "opening_statements")
        assert phase_events[1] == ("agent_start", "Alice", "opening_statements")
        assert phase_events[2] == ("agent_done", "Alice", "opening_statements")
        assert phase_events[3] == ("agent_start", "Bob", "opening_statements")
        assert phase_events[4] == ("agent_done", "Bob", "opening_statements")

    def test_no_callbacks_no_error(self):
        """Verify debate runs normally when no callbacks are set."""
        personas = [make_mock_persona("Alice"), make_mock_persona("Bob")]
        dp = make_mock_data_package()
        orch = DebateOrchestrator(name="test", personas=personas, data_package=dp)

        # No callbacks set (all None)
        orch.run_debate()  # Should not raise

        assert orch.is_complete
        assert len(orch._phase_history) == 4

    def test_on_agent_done_receives_actions(self):
        """Verify on_agent_done callback receives the agent's actions list."""
        personas = [make_mock_persona("Alice")]
        personas.append(make_mock_persona("Bob"))
        dp = make_mock_data_package()
        orch = DebateOrchestrator(name="test", personas=personas, data_package=dp)

        received_actions = []
        orch.on_agent_done = lambda name, phase, actions: received_actions.append(
            (name, actions)
        )
        orch.run_debate()

        # 2 agents x 4 phases = 8 callbacks
        assert len(received_actions) == 8
        # Each action list should contain the mock's pop_latest_actions return
        for name, actions in received_actions:
            assert isinstance(actions, list)
            assert len(actions) == 1
            assert actions[0]["type"] == "TALK"

    def test_callbacks_default_none(self):
        """Verify callback attributes are None by default."""
        personas = [make_mock_persona("A"), make_mock_persona("B")]
        dp = make_mock_data_package()
        orch = DebateOrchestrator(name="test", personas=personas, data_package=dp)

        assert orch.on_phase_start is None
        assert orch.on_agent_start is None
        assert orch.on_agent_done is None


class TestExtractTalkContent:
    """Test the helper function that extracts TALK content from action lists."""

    def test_extract_single_talk(self):
        from tinyic.ui.app import extract_talk_content

        actions = [{"type": "TALK", "content": "Hello world", "target": ""}]
        assert extract_talk_content(actions) == "Hello world"

    def test_extract_multiple_talks(self):
        from tinyic.ui.app import extract_talk_content

        actions = [
            {"type": "TALK", "content": "First point.", "target": ""},
            {"type": "TALK", "content": "Second point.", "target": ""},
        ]
        result = extract_talk_content(actions)
        assert "First point." in result
        assert "Second point." in result

    def test_extract_no_talk(self):
        from tinyic.ui.app import extract_talk_content

        actions = [{"type": "REACH_OUT", "content": "hi", "target": "Bob"}]
        assert extract_talk_content(actions) == "(No response)"

    def test_extract_empty_actions(self):
        from tinyic.ui.app import extract_talk_content

        assert extract_talk_content([]) == "(No response)"
        assert extract_talk_content(None) == "(No response)"
