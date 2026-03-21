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


class TestInvestorPersonaLiveAPI:
    """Integration tests that require a live OpenAI API key."""

    @pytest.mark.live_api
    def test_listen_act_gpt52(self, has_api_key):
        """FOUND-03: InvestorPersona can listen() and act() using GPT-5.2.

        Validates the full chain:
        InvestorPersona -> TinyPerson -> openai_client -> GPT-5.2 with reasoning_effort=xhigh
        """
        persona = InvestorPersona(name="GPT52 Test Investor")
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
