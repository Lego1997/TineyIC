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


import queue
import threading


class TestMessageQueue:
    """Test message queue injection into debate."""

    def test_broadcast_message_delivered(self):
        """Messages with no target are broadcast to all agents."""
        personas = [make_mock_persona("Alice"), make_mock_persona("Bob")]
        dp = make_mock_data_package()
        orch = DebateOrchestrator(name="test", personas=personas, data_package=dp)

        q = queue.Queue()
        orch.message_queue = q
        q.put(("What about the moat?", None))

        orch.run_debate()

        # broadcast() calls listen() on all agents -- check that listen was called
        # with a message containing "Moderator" and "moat"
        all_listen_calls = []
        for p in personas:
            for call in p.listen.call_args_list:
                all_listen_calls.append(str(call))
        moderator_calls = [c for c in all_listen_calls if "Moderator" in c and "moat" in c]
        assert len(moderator_calls) > 0

    def test_targeted_message_delivered(self):
        """Messages with @mention target deliver directly to that agent."""
        personas = [make_mock_persona("Alice"), make_mock_persona("Bob")]
        dp = make_mock_data_package()
        orch = DebateOrchestrator(name="test", personas=personas, data_package=dp)

        q = queue.Queue()
        orch.message_queue = q
        q.put(("What about the moat?", "Alice"))

        orch.run_debate()

        # Alice should get "[Moderator to Alice]" message
        alice = personas[0]
        alice_listen_calls = [str(c) for c in alice.listen.call_args_list]
        targeted = [c for c in alice_listen_calls if "Moderator to Alice" in c]
        assert len(targeted) > 0

        # Bob should get "[Moderator asked Alice]" observation
        bob = personas[1]
        bob_listen_calls = [str(c) for c in bob.listen.call_args_list]
        observation = [c for c in bob_listen_calls if "Moderator asked Alice" in c]
        assert len(observation) > 0

    def test_no_queue_no_error(self):
        """Debate runs normally when no message queue is set."""
        personas = [make_mock_persona("A"), make_mock_persona("B")]
        dp = make_mock_data_package()
        orch = DebateOrchestrator(name="test", personas=personas, data_package=dp)

        orch.run_debate()  # Should not raise
        assert orch.is_complete


class TestPhaseGate:
    """Test inter-phase pausing via threading.Event."""

    def test_phase_gate_blocks_until_set(self):
        """Debate waits at phase_gate.wait() until event is set."""
        personas = [make_mock_persona("A"), make_mock_persona("B")]
        dp = make_mock_data_package()
        orch = DebateOrchestrator(name="test", personas=personas, data_package=dp)

        gate = threading.Event()
        orch.phase_gate = gate

        completed = {"value": False}

        def run_in_thread():
            orch.run_debate()
            completed["value"] = True

        t = threading.Thread(target=run_in_thread, daemon=True)
        t.start()

        # Debate should be blocked at first phase gate
        # (run_debate sets gate for first phase, so first phase runs)
        # Wait briefly, then signal gates for remaining phases
        import time
        time.sleep(0.5)

        # Signal remaining 3 phases
        for _ in range(3):
            gate.set()
            time.sleep(0.2)

        t.join(timeout=5)
        assert completed["value"], "Debate should have completed"
        assert orch.is_complete

    def test_no_gate_no_blocking(self):
        """Debate runs straight through when no phase_gate is set."""
        personas = [make_mock_persona("A"), make_mock_persona("B")]
        dp = make_mock_data_package()
        orch = DebateOrchestrator(name="test", personas=personas, data_package=dp)

        orch.run_debate()
        assert orch.is_complete


class TestParseUserMessage:
    """Test @mention parsing from user input."""

    def test_no_mention(self):
        from tinyic.ui.app import parse_user_message
        text, target = parse_user_message("What about the balance sheet?")
        assert text == "What about the balance sheet?"
        assert target is None

    def test_mention_buffett(self):
        from tinyic.ui.app import parse_user_message
        text, target = parse_user_message("@Buffett what about the moat?")
        assert text == "what about the moat?"
        assert target == "Warren Buffett"

    def test_mention_graham(self):
        from tinyic.ui.app import parse_user_message
        text, target = parse_user_message("@graham is it trading below book value?")
        assert text == "is it trading below book value?"
        assert target == "Benjamin Graham"

    def test_mention_case_insensitive(self):
        from tinyic.ui.app import parse_user_message
        text, target = parse_user_message("@MUNGER thoughts on management?")
        assert target == "Charlie Munger"

    def test_mention_li_lu(self):
        from tinyic.ui.app import parse_user_message
        _, target = parse_user_message("@Li what about China exposure?")
        assert target == "Li Lu"

    def test_unknown_mention(self):
        from tinyic.ui.app import parse_user_message
        text, target = parse_user_message("@Soros what do you think?")
        assert text == "what do you think?"
        assert target is None

    def test_mention_by_first_name(self):
        from tinyic.ui.app import parse_user_message
        _, target = parse_user_message("@Warren how long would you hold?")
        assert target == "Warren Buffett"
        _, target = parse_user_message("@Howard where are we in the cycle?")
        assert target == "Howard Marks"
