"""M4 Stage-1 protocol tests: moderator caps, DA rotation, temperament (FR-4.1/4.2/4.3).

These exercise the moderator as a standalone component and through the
orchestrator: exchange-cap resolution/enforcement, the persisted devil's-advocate
rotation that fixes review defect B8, and temperament-aware reinforcement. The
autouse ``TINYIC_STATE_DIR`` sandbox (conftest) gives every test a fresh rotation
counter and keeps the real ``~/.tinyic`` untouched.
"""

from types import SimpleNamespace

import pytest

from tinyic import state
from tinyic.data.models import DataPackage
from tinyic.debate import _debate_started_payload, _persona_temperament
from tinyic.debate.moderator import (
    DEFAULT_EXCHANGE_CAPS,
    MAX_EXCHANGES,
    Moderator,
    ModeratorError,
    resolve_caps,
)
from tinyic.debate.models import DebatePhase
from tinyic.debate.orchestrator import DebateOrchestrator
from tinyic.debate.prompts import (
    DEVILS_ADVOCATE_PROMPT,
    TEMPERAMENT_REINFORCEMENT,
    temperament_clause,
)
from tinyic.models.presets import Preset, PresetError
from tinytroupe.agent import TinyPerson
from tinytroupe.environment.tiny_world import TinyWorld


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agents(*names):
    """Lightweight stand-ins carrying only the ``.name`` the moderator reads."""
    return [SimpleNamespace(name=name) for name in names]


def _mock_persona(name: str):
    from unittest.mock import MagicMock

    persona = MagicMock()
    persona.name = name
    persona.environment = None
    persona.act.return_value = [{"type": "TALK", "content": f"{name} speaks"}]
    persona.pop_latest_actions.return_value = [
        {"type": "TALK", "content": f"{name} speaks", "target": ""}
    ]
    return persona


def _mock_data_package():
    from unittest.mock import MagicMock

    package = MagicMock(spec=DataPackage)
    package.ticker = "AAPL"
    package.company_name = "Apple Inc."
    package.to_context_string.return_value = '{"ticker": "AAPL"}'
    return package


@pytest.fixture(autouse=True)
def _clear_registries():
    TinyWorld.all_environments.clear()
    TinyPerson.all_agents.clear()
    yield
    TinyWorld.all_environments.clear()
    TinyPerson.all_agents.clear()


# ===========================================================================
# Exchange caps (FR-4.2)
# ===========================================================================


class TestExchangeCaps:
    def test_default_caps_match_fr_4_2(self):
        assert Moderator().exchange_caps == {
            "opening": 1,
            "cross_exam": 2,
            "rebuttal": 1,
            "verdict": 1,
        }

    def test_preset_caps_merge_over_defaults(self):
        moderator = Moderator(caps={"cross_exam": 3})
        assert moderator.exchange_caps["cross_exam"] == 3
        # Unspecified phases keep their defaults.
        assert moderator.exchange_caps["opening"] == 1
        assert moderator.exchange_caps["rebuttal"] == 1

    @pytest.mark.parametrize("bad", [0, MAX_EXCHANGES + 1, -1, 99])
    def test_out_of_bounds_cap_rejected(self, bad):
        with pytest.raises(ModeratorError):
            Moderator(caps={"cross_exam": bad})

    def test_non_integer_cap_rejected(self):
        with pytest.raises(ModeratorError):
            Moderator(caps={"cross_exam": 2.0})
        # A bool is not an accepted integer cap.
        with pytest.raises(ModeratorError):
            Moderator(caps={"cross_exam": True})

    def test_unknown_phase_key_is_ignored(self):
        moderator = Moderator(caps={"cross_examination": 2})
        assert moderator.exchange_caps == DEFAULT_EXCHANGE_CAPS

    def test_resolve_caps_returns_defaults_for_none(self):
        assert resolve_caps(None) == DEFAULT_EXCHANGE_CAPS

    def test_rounds_for_clamps_request_to_cap(self):
        moderator = Moderator(
            caps={"cross_exam": 2}, requested_exchanges={"cross_exam": 5}
        )
        assert moderator.rounds_for("cross_exam") == 2

    def test_rounds_for_honors_request_below_cap(self):
        moderator = Moderator(
            caps={"cross_exam": 3}, requested_exchanges={"cross_exam": 1}
        )
        assert moderator.rounds_for("cross_exam") == 1

    def test_rounds_for_defaults_to_one_per_phase(self):
        moderator = Moderator()
        assert [moderator.rounds_for(p) for p in DEFAULT_EXCHANGE_CAPS] == [
            1,
            1,
            1,
            1,
        ]

    def test_orchestrator_never_exceeds_cap_in_cross_exam(self):
        """A request above the cap runs only ``cap`` rounds of turns."""
        personas = [_mock_persona("A"), _mock_persona("B")]
        moderator = Moderator(
            caps={"cross_exam": 2}, requested_exchanges={"cross_exam": 3}
        )
        orch = DebateOrchestrator(
            name="cap_enforce",
            personas=personas,
            data_package=_mock_data_package(),
            moderator=moderator,
        )
        per_phase: list[str] = []
        orch.on_agent_done = lambda name, phase, actions: per_phase.append(phase)
        orch.run_debate()

        cross = sum(1 for p in per_phase if p == DebatePhase.CROSS_EXAM.value)
        opening = sum(1 for p in per_phase if p == DebatePhase.OPENING.value)
        # 2 personas x min(3 requested, 2 cap) = 4 cross-exam turns; other
        # phases keep the one-round baseline (2 turns each).
        assert cross == 4
        assert opening == 2

    def test_preset_caps_flow_into_debate_started_payload(self):
        payload = _debate_started_payload(
            "AAPL",
            "Apple Inc.",
            _agents("Warren Buffett", "Benjamin Graham"),
            caps={"opening": 1, "cross_exam": 3, "rebuttal": 1, "verdict": 1},
            moderator_ref="rules",
        )
        assert payload["caps"]["cross_exam"] == 3
        assert payload["moderator"] == "rules"


class TestPresetCaps:
    def test_preset_parses_valid_caps(self):
        preset = Preset.from_mapping(
            "x", {"model": "openai/gpt-5.2", "caps": {"cross_exam": 3}}
        )
        assert preset.caps == {"cross_exam": 3}

    def test_preset_rejects_unknown_phase_key(self):
        with pytest.raises(PresetError):
            Preset.from_mapping(
                "x",
                {"model": "openai/gpt-5.2", "caps": {"cross_examination": 2}},
            )

    def test_preset_rejects_non_integer_cap(self):
        with pytest.raises(PresetError):
            Preset.from_mapping(
                "x", {"model": "openai/gpt-5.2", "caps": {"cross_exam": "two"}}
            )

    def test_preset_caps_realize_through_moderator(self):
        preset = Preset.from_mapping(
            "x", {"model": "openai/gpt-5.2", "caps": {"cross_exam": 3}}
        )
        assert Moderator(caps=preset.caps).exchange_caps["cross_exam"] == 3

    def test_out_of_bounds_preset_cap_fails_when_realized(self):
        # Range-checking is the moderator's job, so parsing accepts it but
        # building the committee's moderator rejects it before the debate runs.
        preset = Preset.from_mapping(
            "x", {"model": "openai/gpt-5.2", "caps": {"cross_exam": 9}}
        )
        with pytest.raises(ModeratorError):
            Moderator(caps=preset.caps)


# ===========================================================================
# Devil's-advocate rotation (FR-4.3, fixes B8)
# ===========================================================================


class TestDevilsAdvocateRotation:
    def test_rotation_advances_across_separate_debates(self):
        agents = _agents("A", "B", "C")
        # A fresh moderator per debate (as run_debate builds one each time)
        # rotates because the counter is persisted per install.
        picks = [Moderator().select_devils_advocate(agents).name for _ in range(4)]
        assert picks == ["A", "B", "C", "A"]

    def test_selection_persists_the_counter(self):
        assert state.read_counter("da_rotation") == 0
        Moderator().select_devils_advocate(_agents("A", "B"))
        assert state.read_counter("da_rotation") == 1

    def test_single_debate_advances_counter_once(self):
        moderator = Moderator()
        moderator.select_devils_advocate(_agents("A", "B"))
        assert state.read_counter("da_rotation") == 1

    def test_override_pins_da_and_does_not_rotate(self):
        agents = _agents("A", "B", "C")
        moderator = Moderator(da_override="B")
        assert moderator.select_devils_advocate(agents).name == "B"
        # An explicit override must not consume a rotation slot.
        assert state.read_counter("da_rotation") == 0

    def test_override_matches_snake_case_registry_name(self):
        agents = _agents("Warren Buffett", "Benjamin Graham")
        moderator = Moderator(da_override="benjamin_graham")
        assert moderator.select_devils_advocate(agents).name == "Benjamin Graham"

    def test_override_matches_display_name_case_insensitively(self):
        agents = _agents("Warren Buffett", "Benjamin Graham")
        moderator = Moderator(da_override="warren buffett")
        assert moderator.select_devils_advocate(agents).name == "Warren Buffett"

    def test_invalid_override_raises_with_available_names(self):
        with pytest.raises(ModeratorError, match="Warren Buffett"):
            Moderator(da_override="Nobody").validate_override(
                _agents("Warren Buffett", "Benjamin Graham")
            )

    def test_orchestrator_rotates_da_across_two_debates(self):
        """Two back-to-back debates in one process pick different devils."""
        dp = _mock_data_package()
        first = DebateOrchestrator(
            name="rot_one",
            personas=[_mock_persona("A"), _mock_persona("B"), _mock_persona("C")],
            data_package=dp,
        )
        first.run_debate()
        second = DebateOrchestrator(
            name="rot_two",
            personas=[_mock_persona("A"), _mock_persona("B"), _mock_persona("C")],
            data_package=_mock_data_package(),
        )
        second.run_debate()

        def da_recipient(orch):
            for agent in orch.agents:
                if any(
                    DEVILS_ADVOCATE_PROMPT in str(call)
                    for call in agent.listen.call_args_list
                ):
                    return agent.name
            return None

        assert da_recipient(first) == "A"
        assert da_recipient(second) == "B"

    def test_orchestrator_da_override_fails_fast_on_bad_name(self):
        with pytest.raises(ModeratorError):
            DebateOrchestrator(
                name="bad_da",
                personas=[_mock_persona("A"), _mock_persona("B")],
                data_package=_mock_data_package(),
                moderator=Moderator(da_override="Nobody"),
            )

    def test_orchestrator_da_override_pins_selection(self):
        orch = DebateOrchestrator(
            name="pin_da",
            personas=[_mock_persona("A"), _mock_persona("B"), _mock_persona("C")],
            data_package=_mock_data_package(),
            moderator=Moderator(da_override="C"),
        )
        orch.run_debate()
        recipients = [
            agent.name
            for agent in orch.agents
            if any(
                DEVILS_ADVOCATE_PROMPT in str(call)
                for call in agent.listen.call_args_list
            )
        ]
        assert recipients == ["C"]


class TestStateModule:
    def test_read_counter_defaults_to_zero(self):
        assert state.read_counter("missing") == 0

    def test_advance_returns_preincrement_and_persists(self):
        assert state.advance_counter("k") == 0
        assert state.advance_counter("k") == 1
        assert state.read_counter("k") == 2

    def test_corrupt_state_file_reads_as_zero(self):
        path = state.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not valid json", encoding="utf-8")
        assert state.read_counter("da_rotation") == 0

    def test_advance_preserves_unrelated_keys(self):
        state.advance_counter("a")
        state.advance_counter("b")
        state.advance_counter("a")
        assert state.read_counter("a") == 2
        assert state.read_counter("b") == 1


# ===========================================================================
# Temperament injection (FR-4.3)
# ===========================================================================


class TestTemperamentInjection:
    def test_temperament_clause_maps_each_value(self):
        for temperament in ("conciliatory", "balanced", "contrarian"):
            assert (
                temperament_clause(temperament)
                == TEMPERAMENT_REINFORCEMENT[temperament]
            )

    def test_temperament_clause_falls_back_to_balanced(self):
        for value in (None, "unknown", 123, ""):
            assert (
                temperament_clause(value)
                == TEMPERAMENT_REINFORCEMENT["balanced"]
            )

    def test_contrarian_clause_prioritizes_accuracy_over_agreement(self):
        assert (
            "accuracy over agreement"
            in TEMPERAMENT_REINFORCEMENT["contrarian"].lower()
        )

    def test_reinforcement_folds_in_temperament_and_keeps_prefix(self):
        orch = DebateOrchestrator(
            name="temp_inject",
            personas=[_mock_persona("A"), _mock_persona("B")],
            data_package=_mock_data_package(),
        )
        contrarian = SimpleNamespace(
            name="Charlie Munger", temperament="contrarian"
        )
        prompt = orch._get_reinforcement_prompt(contrarian)
        assert "IMPORTANT REMINDER" in prompt
        assert TEMPERAMENT_REINFORCEMENT["contrarian"] in prompt
        # It stays a single line (tightened per FR-4.3).
        assert "\n" not in prompt

    def test_reinforcement_defaults_to_balanced_without_temperament(self):
        orch = DebateOrchestrator(
            name="temp_default",
            personas=[_mock_persona("A"), _mock_persona("B")],
            data_package=_mock_data_package(),
        )
        no_temperament = SimpleNamespace(name="Unknown Investor")
        prompt = orch._get_reinforcement_prompt(no_temperament)
        assert TEMPERAMENT_REINFORCEMENT["balanced"] in prompt
        # Unknown personas still get the generic philosophy fallback.
        assert "Stay true to your unique perspective." in prompt

    def test_persona_configs_carry_temperament(self):
        from tinyic.personas.registry import load_persona

        assert load_persona("charlie_munger").temperament == "contrarian"
        assert load_persona("benjamin_graham").temperament == "contrarian"
        assert load_persona("warren_buffett").temperament == "balanced"
        assert load_persona("li_lu").temperament == "conciliatory"

    def test_default_committee_has_a_hard_dissenter(self):
        """The bundled six-persona committee includes >=1 contrarian (FR-4.3)."""
        from tinyic.personas.registry import list_personas, load_persona

        temperaments = {
            name: load_persona(name).temperament for name in list_personas()
        }
        assert "contrarian" in temperaments.values()
        # ...and the mix is not uniform.
        assert len(set(temperaments.values())) >= 2

    def test_persona_temperament_helper_reads_and_defaults(self):
        assert (
            _persona_temperament(SimpleNamespace(name="X", temperament="Contrarian"))
            == "contrarian"
        )
        assert _persona_temperament(SimpleNamespace(name="X")) == "balanced"

    def test_debate_started_payload_reflects_persona_temperament(self):
        personas = [
            SimpleNamespace(name="Warren Buffett", temperament="balanced"),
            SimpleNamespace(name="Charlie Munger", temperament="contrarian"),
        ]
        payload = _debate_started_payload("AAPL", "Apple Inc.", personas)
        by_name = {p["name"]: p["temperament"] for p in payload["personas"]}
        assert by_name == {
            "Warren Buffett": "balanced",
            "Charlie Munger": "contrarian",
        }
