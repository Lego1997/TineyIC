"""M0 acceptance tests for debate-scoped TinyTroupe registries."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import tinyic.debate as debate_module
from tinyic.data.models import DataPackage
from tinyic.debate.models import Confidence, Vote, VoteChoice
from tinyic.debate.orchestrator import DebateOrchestrator
from tinyic.personas.base import InvestorPersona
from tinytroupe.agent import TinyPerson
from tinytroupe.environment.tiny_world import TinyWorld


@pytest.fixture(autouse=True)
def clean_legacy_registries():
    """Keep the pre-M0 global registry defect isolated to each test."""
    TinyPerson.all_agents.clear()
    TinyWorld.all_environments.clear()
    yield
    TinyPerson.all_agents.clear()
    TinyWorld.all_environments.clear()


def _data_package() -> DataPackage:
    return DataPackage(
        ticker="AAPL",
        company_name="Apple Inc.",
        fetched_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )


def _mock_persona_act(self, *, return_actions=False, **_kwargs):
    """Commit one deterministic TALK action without making an LLM call."""
    action = {
        "type": "TALK",
        "content": f"{self.name} gives a mocked investment view.",
        "target": "",
    }
    self._actions_buffer.append(action)
    return [action] if return_actions else self


def _mock_votes(_orchestrator: DebateOrchestrator) -> list[Vote]:
    return [
        Vote(
            investor="Warren Buffett",
            vote=VoteChoice.BUY,
            confidence=Confidence.HIGH,
        ),
        Vote(
            investor="Benjamin Graham",
            vote=VoteChoice.HOLD,
            confidence=Confidence.MEDIUM,
        ),
    ]


def test_two_consecutive_mocked_debates_reuse_names_in_one_process(monkeypatch):
    """A1: identical full debates must not collide in process-global registries."""
    registry_snapshots = []

    def capture_votes(orchestrator):
        registry_snapshots.append(
            (
                orchestrator.session,
                tuple(orchestrator.session.agents),
                tuple(orchestrator.session.environments),
            )
        )
        return _mock_votes(orchestrator)

    monkeypatch.setattr(InvestorPersona, "act", _mock_persona_act)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(debate_module, "extract_votes", capture_votes)
    monkeypatch.setattr(DebateOrchestrator, "get_cost_stats", lambda _self: {})
    monkeypatch.setattr(TinyPerson, "communication_display", False)

    results = [
        debate_module.run_debate(
            "AAPL",
            ["warren_buffett", "benjamin_graham"],
            data_package=_data_package(),
        )
        for _ in range(2)
    ]

    expected_phases = [phase.value for phase in DebateOrchestrator.PHASE_ORDER]
    assert [result.phases_completed for result in results] == [
        expected_phases,
        expected_phases,
    ]
    assert [[vote.vote for vote in result.scorecard.votes] for result in results] == [
        [VoteChoice.BUY, VoteChoice.HOLD],
        [VoteChoice.BUY, VoteChoice.HOLD],
    ]
    assert registry_snapshots[0][0] is not registry_snapshots[1][0]
    assert [snapshot[1] for snapshot in registry_snapshots] == [
        ("Warren Buffett", "Benjamin Graham"),
        ("Warren Buffett", "Benjamin Graham"),
    ]
    assert [snapshot[2] for snapshot in registry_snapshots] == [
        ("IC-AAPL",),
        ("IC-AAPL",),
    ]
    assert TinyPerson.all_agents == {}
    assert TinyWorld.all_environments == {}


def test_same_names_are_isolated_between_sessions():
    """Names are unique inside a Session, not across independent Sessions."""
    from tinytroupe.session import Session

    first_session = Session()
    second_session = Session()

    first_agent = TinyPerson("Analyst", session=first_session)
    second_agent = TinyPerson("Analyst", session=second_session)
    first_world = TinyWorld("Committee", session=first_session)
    second_world = TinyWorld("Committee", session=second_session)

    assert TinyPerson.get_agent_by_name("Analyst", session=first_session) is first_agent
    assert TinyPerson.get_agent_by_name("Analyst", session=second_session) is second_agent
    assert (
        TinyWorld.get_environment_by_name("Committee", session=first_session)
        is first_world
    )
    assert (
        TinyWorld.get_environment_by_name("Committee", session=second_session)
        is second_world
    )

    with pytest.raises(ValueError, match="already in use"):
        TinyPerson("Analyst", session=first_session)
    with pytest.raises(ValueError, match="must be unique"):
        TinyWorld("Committee", session=first_session)

    assert first_session.agents["Analyst"] is first_agent
    assert first_session.environments["Committee"] is first_world


def test_session_close_is_idempotent_and_rejects_new_objects():
    """Closing a Session releases its registries and ends its lifecycle."""
    from tinytroupe.session import Session

    session = Session()
    TinyPerson("Analyst", session=session)
    TinyWorld("Committee", session=session)

    session.close()
    session.close()

    assert session.agents == {}
    assert session.environments == {}
    with pytest.raises(RuntimeError, match="closed"):
        TinyPerson("Late Analyst", session=session)


def test_legacy_registry_aliases_use_the_default_session():
    """Unscoped upstream callers retain the legacy global helper behavior."""
    from tinytroupe.session import default_session

    agent = TinyPerson("Legacy Analyst")
    world = TinyWorld("Legacy Committee")

    assert TinyPerson.all_agents is default_session().agents
    assert TinyWorld.all_environments is default_session().environments
    assert TinyPerson.get_agent_by_name("Legacy Analyst") is agent
    assert TinyWorld.get_environment_by_name("Legacy Committee") is world


def test_failed_persona_initialization_releases_its_name(tmp_path):
    """A1: a bad persona config must not leave a half-built registration."""
    from tinytroupe.session import Session

    session = Session()
    invalid_config = tmp_path / "invalid.agent.json"
    invalid_config.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError):
        InvestorPersona(
            name="Recoverable Analyst",
            philosophy_config_path=str(invalid_config),
            session=session,
        )

    assert session.agents == {}
    recovered = InvestorPersona(name="Recoverable Analyst", session=session)
    assert session.agents == {"Recoverable Analyst": recovered}


def test_tinyperson_post_init_failure_rolls_back_registration(monkeypatch):
    """Base initialization remains atomic when work after registration fails."""
    from tinytroupe.session import Session

    session = Session()

    def fail_reset(_self):
        raise RuntimeError("reset failed")

    monkeypatch.setattr(TinyPerson, "reset_prompt", fail_reset)
    with pytest.raises(RuntimeError, match="reset failed"):
        TinyPerson("Recoverable Analyst", session=session)

    assert session.agents == {}


def test_tinyworld_constructor_failure_rolls_back_registry_and_agents():
    """A partially added agent cannot retain a failed environment."""
    from tinytroupe.session import Session

    session = Session()
    first = SimpleNamespace(name="Duplicate", environment=None)
    second = SimpleNamespace(name="Duplicate", environment=None)

    with pytest.raises(ValueError, match="must be unique"):
        TinyWorld("Broken Committee", agents=[first, second], session=session)

    assert session.environments == {}
    assert first.environment is None
    assert second.environment is None


def test_world_rejects_agent_owned_by_another_session():
    """A world cannot silently cross the registry boundary of a real agent."""
    from tinytroupe.session import Session

    agent_session = Session()
    world_session = Session()
    agent = TinyPerson("Scoped Analyst", session=agent_session)

    with pytest.raises(ValueError, match="different Session"):
        TinyWorld("Mismatched Committee", agents=[agent], session=world_session)

    assert world_session.environments == {}
    assert agent.environment is None


def test_unchanged_streamlit_engine_path_can_run_twice(monkeypatch):
    """A1: the pre-M6 worker call path must also avoid default-registry leaks."""
    from tinyic.personas.registry import load_persona

    monkeypatch.setattr(InvestorPersona, "act", _mock_persona_act)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(TinyPerson, "communication_display", False)

    orchestrators = []
    for _ in range(2):
        personas = [
            load_persona("warren_buffett"),
            load_persona("benjamin_graham"),
        ]
        orchestrator = DebateOrchestrator(
            name="IC-AAPL",
            personas=personas,
            data_package=_data_package(),
        )
        orchestrator.run_debate()
        orchestrators.append(orchestrator)

    expected_phases = [phase.value for phase in DebateOrchestrator.PHASE_ORDER]
    assert [orchestrator._phase_history for orchestrator in orchestrators] == [
        expected_phases,
        expected_phases,
    ]
    assert orchestrators[0].session is not orchestrators[1].session
    assert TinyPerson.all_agents == {}
    assert TinyWorld.all_environments == {}

    for orchestrator in orchestrators:
        orchestrator.session.close()


def test_agent_attached_to_world_cannot_move_sessions():
    """Moving a live world member cannot corrupt scoped state lookup."""
    from tinytroupe.session import Session

    current_session = Session()
    other_session = Session()
    agent = TinyPerson("World Member", session=current_session)
    world = TinyWorld("Current Committee", agents=[agent], session=current_session)

    with pytest.raises(ValueError, match="attached to environment"):
        agent.move_to_session(other_session)

    assert agent.session is current_session
    assert current_session.agents == {"World Member": agent}
    assert other_session.agents == {}

    state = world.encode_complete_state()
    world.decode_complete_state(state)
    assert world.get_agent_by_name("World Member") is agent


def test_failed_world_construction_restores_prior_environment():
    """World rollback restores the exact environment an added object had before."""
    from tinytroupe.session import Session

    previous_environment = object()
    first = SimpleNamespace(name="Duplicate", environment=previous_environment)
    second = SimpleNamespace(name="Duplicate", environment=None)
    session = Session()

    with pytest.raises(ValueError, match="must be unique"):
        TinyWorld("Broken Committee", agents=[first, second], session=session)

    assert session.environments == {}
    assert first.environment is previous_environment
    assert second.environment is None


def test_caller_owned_session_is_reusable_across_full_debates(monkeypatch):
    """An injected Session is cleaned transactionally after every invocation."""
    from tinytroupe.session import Session

    monkeypatch.setattr(InvestorPersona, "act", _mock_persona_act)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(debate_module, "extract_votes", _mock_votes)
    monkeypatch.setattr(DebateOrchestrator, "get_cost_stats", lambda _self: {})
    monkeypatch.setattr(TinyPerson, "communication_display", False)

    session = Session()
    results = []
    for _ in range(2):
        results.append(
            debate_module.run_debate(
                "AAPL",
                ["warren_buffett", "benjamin_graham"],
                data_package=_data_package(),
                session=session,
            )
        )
        assert session.agents == {}
        assert session.environments == {}
        assert session.closed is False

    assert [len(result.phases_completed) for result in results] == [4, 4]


def test_caller_owned_session_is_clean_after_debate_failure(monkeypatch):
    """A failed invocation cannot reserve names in an injected Session."""
    from tinyic.personas.registry import load_persona
    from tinytroupe.session import Session

    session = Session()

    def fail_debate(_self):
        raise RuntimeError("mocked debate failure")

    monkeypatch.setattr(DebateOrchestrator, "run_debate", fail_debate)
    with pytest.raises(RuntimeError, match="mocked debate failure"):
        debate_module.run_debate(
            "AAPL",
            ["warren_buffett", "benjamin_graham"],
            data_package=_data_package(),
            session=session,
        )

    assert session.agents == {}
    assert session.environments == {}
    recovered = load_persona("warren_buffett", session=session)
    assert session.agents == {"Warren Buffett": recovered}


def test_orchestrator_post_init_failure_disposes_world(monkeypatch):
    """Subclass setup failures cannot leave a registered half-built committee."""
    from tinytroupe.session import Session

    first_session = Session()
    second_session = Session()
    personas = [
        TinyPerson("First", session=first_session),
        TinyPerson("Second", session=second_session),
    ]

    def fail_accessibility(_self):
        raise RuntimeError("accessibility failed")

    monkeypatch.setattr(
        DebateOrchestrator, "make_everyone_accessible", fail_accessibility
    )
    with pytest.raises(RuntimeError, match="accessibility failed"):
        DebateOrchestrator(
            name="Broken IC",
            personas=personas,
            data_package=_data_package(),
        )

    assert first_session.environments == {}
    assert all(persona.environment is None for persona in personas)
    assert personas[0].session is first_session
    assert personas[1].session is second_session


def test_orchestrator_adoption_failure_preserves_original_sessions():
    """Persona adoption validates the whole batch before moving any object."""
    from tinytroupe.session import Session

    first_session = Session()
    second_session = Session()
    third_session = Session()
    first = TinyPerson("First", session=first_session)
    second = TinyPerson("Duplicate", session=second_session)
    third = TinyPerson("Duplicate", session=third_session)

    with pytest.raises(ValueError, match="already in use"):
        DebateOrchestrator(
            name="Broken Adoption",
            personas=[first, second, third],
            data_package=_data_package(),
        )

    assert first.session is first_session
    assert second.session is second_session
    assert third.session is third_session
    assert first_session.agents == {"First": first}
    assert second_session.agents == {"Duplicate": second}
    assert third_session.agents == {"Duplicate": third}
    assert first_session.environments == {}


def test_world_registration_failure_rolls_back_adopted_personas():
    """A base-world failure restores every persona moved during adoption."""
    from tinytroupe.session import Session

    first_session = Session()
    second_session = Session()
    first = TinyPerson("First", session=first_session)
    second = TinyPerson("Second", session=second_session)
    existing_world = TinyWorld("Existing IC", session=first_session)

    with pytest.raises(ValueError, match="must be unique"):
        DebateOrchestrator(
            name="Existing IC",
            personas=[first, second],
            data_package=_data_package(),
        )

    assert first.session is first_session
    assert second.session is second_session
    assert first_session.agents == {"First": first}
    assert second_session.agents == {"Second": second}
    assert first_session.environments == {"Existing IC": existing_world}
