"""Tests for InvestorPersona base class and TinyTroupe integration."""

import os
from pathlib import Path

import pytest
from tinyic.personas.base import InvestorPersona
from tinytroupe.agent import TinyPerson


# Path to the test persona config
CONFIGS_DIR = Path(__file__).parent.parent / "src" / "tinyic" / "personas" / "configs"
TEST_CONFIG_PATH = CONFIGS_DIR / "test_investor.agent.json"


@pytest.fixture(autouse=True)
def clear_agent_registry():
    """Clear TinyPerson's global agent registry between tests."""
    yield
    TinyPerson.all_agents.clear()


class TestInvestorPersonaUnit:
    """Unit tests that do NOT require API access."""

    def test_import_investor_persona(self):
        """FOUND-01: InvestorPersona is importable."""
        assert InvestorPersona is not None

    def test_import_tinyperson(self):
        """FOUND-02: TinyPerson is importable from forked tinytroupe."""
        assert TinyPerson is not None

    def test_investor_persona_is_tinyperson_subclass(self):
        """InvestorPersona extends TinyPerson."""
        assert issubclass(InvestorPersona, TinyPerson)

    def test_analyze_company_is_implemented(self):
        """analyze_company() is no longer a stub."""
        import inspect
        src = inspect.getsource(InvestorPersona.analyze_company)
        assert "NotImplementedError" not in src

    def test_format_vote_is_implemented(self):
        """format_vote() is no longer a stub."""
        import inspect
        src = inspect.getsource(InvestorPersona.format_vote)
        assert "NotImplementedError" not in src

    def test_load_philosophy_config(self):
        """InvestorPersona can load a .agent.json config."""
        persona = InvestorPersona(
            name="ConfigLoadTest",
            philosophy_config_path=str(TEST_CONFIG_PATH),
        )
        # After loading, persona should have beliefs from config
        assert persona is not None
        assert persona.get("beliefs") is not None

    def test_persona_attributes_from_config(self):
        """InvestorPersona loads attributes from .agent.json correctly."""
        persona = InvestorPersona(
            name="AttrTest",
            philosophy_config_path=str(TEST_CONFIG_PATH),
        )
        # Check that persona data from JSON was merged
        beliefs = persona.get("beliefs")
        assert isinstance(beliefs, list)
        assert any("intrinsic value" in b for b in beliefs)

    def test_create_minimal_persona(self):
        """InvestorPersona can be created with just a name."""
        persona = InvestorPersona(name="Minimal Investor")
        assert persona.name == "Minimal Investor"

    def test_semantic_consolidation_off_skips_store_and_consolidator(
        self, monkeypatch
    ):
        """TIC-007: a disabled persona commits the episode without any LLM/embedding.

        With ``semantic_consolidation=False`` a > ``MIN_EPISODE_LENGTH`` episode
        must not touch the episodic consolidator or ``SemanticMemory.store_all``
        (which would embed each engram through the credential-less global client)
        even when a binding is active, while still committing the episode.
        """
        from tinyic.models import routing
        from tinytroupe.agent import memory as tt_memory
        from tinytroupe.session import Session

        store_calls: list = []
        process_calls: list = []
        monkeypatch.setattr(
            tt_memory.SemanticMemory,
            "store_all",
            lambda self, values: store_calls.append(values),
        )
        monkeypatch.setattr(
            tt_memory.EpisodicConsolidator,
            "process",
            lambda self, *a, **k: process_calls.append(1),
        )

        with Session() as session:
            persona = InvestorPersona(
                name="Frugal Graham",
                session=session,
                semantic_consolidation=False,
            )
            for index in range(persona.MIN_EPISODE_LENGTH + 2):
                persona.store_in_memory(
                    {
                        "role": "user",
                        "content": {
                            "stimuli": [
                                {
                                    "type": "CONVERSATION",
                                    "content": f"event {index}",
                                    "source": "",
                                }
                            ]
                        },
                        "type": "stimulus",
                        "simulation_timestamp": None,
                    }
                )
            with routing.activate(object()):
                persona.consolidate_episode_memories()

        assert store_calls == []
        assert process_calls == []
        assert persona._current_episode_event_count == 0
        assert persona.episodic_memory.episodic_buffer == []


class TestInvestorPersonaLiveAPI:
    """Integration tests that require a live OpenAI API key."""

    @pytest.mark.live_api
    @pytest.mark.timeout(120)
    def test_listen_act_default_model(self, has_api_key):
        """FOUND-03: InvestorPersona can listen() and act() using the default model.

        Validates the full chain:
        InvestorPersona -> TinyPerson -> openai_client -> the configured default model with reasoning_effort=xhigh
        """
        persona = InvestorPersona(name="Default Model Test Investor")
        persona["nationality"] = "American"
        persona["occupation"] = {
            "title": "Value Investor",
            "organization": "Test Fund",
        }
        persona["personality"] = {
            "traits": ["Analytical", "Patient", "Value-oriented"],
        }
        persona["beliefs"] = [
            "Buy businesses below intrinsic value",
            "Margin of safety is essential",
        ]

        persona.listen(
            "What do you think about Apple (AAPL) as an investment?"
        )
        actions = persona.act()

        assert actions is not None
        assert len(actions) > 0
        # The action should contain some response content
        first_action = actions[0]
        assert "type" in first_action or "content" in first_action or "action" in first_action

    @pytest.mark.live_api
    @pytest.mark.timeout(120)
    def test_sdk_v2_compatibility(self, has_api_key):
        """FOUND-04: OpenAI SDK v2.x works with TinyTroupe fork without errors.

        If this test passes, the TinyTroupe fork is compatible with
        openai SDK v2.x for chat completions with reasoning_effort.
        """
        import openai
        # Verify we are running SDK v2.x
        sdk_version = openai.__version__
        assert sdk_version.startswith("2."), (
            f"Expected openai SDK v2.x, got {sdk_version}"
        )

        # The listen/act test above already validates SDK compatibility.
        # This test additionally confirms the SDK version is 2.x.
        persona = InvestorPersona(name="SDK Compat Test")
        persona["nationality"] = "American"
        persona["occupation"] = {"title": "Analyst", "organization": "Test"}

        persona.listen("Say hello in one sentence.")
        actions = persona.act()

        assert actions is not None
        assert len(actions) > 0
