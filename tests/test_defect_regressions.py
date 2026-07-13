"""Red acceptance tests for review defect classes B1-B5.

These tests intentionally encode the failure scenarios verified in
``docs/code-review-2026-07-12.md``.  Product fixes land only after the
corresponding test has been observed failing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tinyic.data.models import DataPackage
from tinyic.debate import run_debate
from tinyic.debate.extraction import extract_votes
from tinyic.debate.memo import extract_disagreements, generate_memo
from tinyic.debate.models import (
    Confidence,
    DebateResult,
    DisagreementAnalysis,
    InvestmentMemo,
    Scorecard,
    Vote,
    VoteChoice,
)
from tinytroupe import config_manager
from tinytroupe.agent import TinyPerson
from tinytroupe.extraction import ResultsExtractor
from tinytroupe.session import Session
from tinytroupe.utils.behavior import _compute_single_action_jaccard_similarity
from tinytroupe.utils.config import read_config_file


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "recorded_debate_apple.json"


def _recorded_speech(persona_name: str) -> str:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return next(
        persona["reasoning_text"]
        for persona in fixture["personas"]
        if persona["name"] == persona_name
    )


def _data_package() -> DataPackage:
    return DataPackage(
        ticker="AAPL",
        company_name="Apple Inc.",
        fetched_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )


def _scorecard() -> Scorecard:
    return Scorecard(
        ticker="AAPL",
        company_name="Apple Inc.",
        votes=[
            Vote(
                investor="Warren Buffett",
                vote=VoteChoice.BUY,
                confidence=Confidence.HIGH,
            )
        ],
        consensus=VoteChoice.BUY,
        bull_count=1,
    )


def _memo_response(content: str = "RUNNING_SYNTHESIS_SENTINEL") -> dict:
    def section() -> dict:
        return {
            "content": content,
            "contributing_personas": ["Warren Buffett"],
            "supporting_data": ["P/E 28x"],
        }

    return {
        "executive_summary": section(),
        "investment_thesis": section(),
        "key_risks": section(),
        "valuation_discussion": section(),
        "final_verdict": section(),
    }


def _disagreement_response() -> dict:
    return {
        "disagreements": [
            {
                "dimension": "Valuation",
                "description": "The committee disagreed on valuation.",
                "sides": [
                    {
                        "persona": "Warren Buffett",
                        "position": "Quality merits a premium.",
                        "evidence_quote": "Quality at a fair price.",
                    },
                    {
                        "persona": "Benjamin Graham",
                        "position": "There is no margin of safety.",
                        "evidence_quote": "No margin of safety.",
                    },
                ],
                "resolution": "Unresolved",
            }
        ]
    }


def _long_transcript() -> str:
    return "\n".join(
        [
            "OPENING_SENTINEL " + "opening thesis and business quality " * 260,
            "MIDDLE_SENTINEL " + "cross examination and valuation challenge " * 260,
            "VERDICT_SENTINEL " + "final stance confidence and risks " * 260,
        ]
    )


def _long_debate_result() -> DebateResult:
    return DebateResult(
        ticker="AAPL",
        company_name="Apple Inc.",
        scorecard=_scorecard(),
        phases_completed=[
            "opening_statements",
            "cross_examination",
            "rebuttal",
            "final_verdict",
        ],
        transcript=_long_transcript(),
    )


# ---------------------------------------------------------------------------
# B1: vote polarity integrity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_vote", "expected"),
    [
        ("SELL (I would not buy here)", VoteChoice.SELL),
        ("Do not buy", VoteChoice.HOLD),
    ],
)
def test_b1_adversarial_vote_language_cannot_be_recorded_as_buy(
    raw_vote: str, expected: VoteChoice
) -> None:
    vote = Vote(investor="Adversarial Analyst", vote=raw_vote, confidence="HIGH")
    assert vote.vote is expected


# ---------------------------------------------------------------------------
# B2: confidence and null-vote normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_confidence", "expected"),
    [
        ("High", Confidence.HIGH),
        ("medium", Confidence.MEDIUM),
        ("lOw", Confidence.LOW),
    ],
)
def test_b2_confidence_enum_is_case_insensitive(
    raw_confidence: str, expected: Confidence
) -> None:
    assert Confidence(raw_confidence) is expected


def test_b2_null_vote_does_not_discard_valid_confidence_or_reasoning() -> None:
    agent = SimpleNamespace(name="Null Vote Analyst")
    orchestrator = SimpleNamespace(
        agents=[agent],
        data_package=SimpleNamespace(company_name="Apple Inc.", ticker="AAPL"),
    )
    raw_result = {
        "vote": None,
        "confidence": "High",
        "reasoning": ["PRESERVED_REASONING_SENTINEL"],
    }

    with patch("tinyic.debate.extraction.ResultsExtractor") as extractor_cls:
        extractor_cls.return_value.extract_results_from_agents.return_value = [
            raw_result
        ]
        votes = extract_votes(orchestrator)

    assert len(votes) == 1
    assert votes[0].vote is VoteChoice.HOLD
    assert votes[0].confidence is Confidence.HIGH
    assert votes[0].reasoning == ["PRESERVED_REASONING_SENTINEL"]


# ---------------------------------------------------------------------------
# B3: per-agent extraction isolation
# ---------------------------------------------------------------------------


def test_b3_bulk_extraction_continues_after_agent_four_failure(monkeypatch) -> None:
    agents = [SimpleNamespace(name=f"Agent {index}") for index in range(1, 7)]
    extractor = ResultsExtractor()
    visited: list[str] = []

    def extract_one(agent, *_args, **_kwargs):
        visited.append(agent.name)
        if agent.name == "Agent 4":
            raise RuntimeError("transient API error on agent 4")
        return {"vote": "BUY" if agent.name == "Agent 1" else "SELL"}

    monkeypatch.setattr(extractor, "extract_results_from_agent", extract_one)

    results = extractor.extract_results_from_agents(agents, verbose=False)

    assert visited == [agent.name for agent in agents]
    assert len(results) == 6
    assert results[0] == {"vote": "BUY"}
    assert results[3] is None
    assert results[4] == {"vote": "SELL"}
    assert results[5] == {"vote": "SELL"}


def test_b3_only_the_failed_agent_receives_an_extraction_fallback() -> None:
    agents = [SimpleNamespace(name=f"Agent {index}") for index in range(1, 7)]
    orchestrator = SimpleNamespace(
        agents=agents,
        data_package=SimpleNamespace(company_name="Apple Inc.", ticker="AAPL"),
    )
    raw_results = [
        {"vote": "BUY", "confidence": "HIGH", "reasoning": ["agent 1"]},
        {"vote": "SELL", "confidence": "MEDIUM", "reasoning": ["agent 2"]},
        {"vote": "BUY", "confidence": "LOW", "reasoning": ["agent 3"]},
        None,
        {"vote": "SELL", "confidence": "HIGH", "reasoning": ["agent 5"]},
        {"vote": "SELL", "confidence": "MEDIUM", "reasoning": ["agent 6"]},
    ]

    with patch("tinyic.debate.extraction.ResultsExtractor") as extractor_cls:
        extractor_cls.return_value.extract_results_from_agents.return_value = raw_results
        votes = extract_votes(orchestrator)

    assert [vote.vote for vote in votes] == [
        VoteChoice.BUY,
        VoteChoice.SELL,
        VoteChoice.BUY,
        VoteChoice.HOLD,
        VoteChoice.SELL,
        VoteChoice.SELL,
    ]
    assert votes[3].confidence is Confidence.LOW
    assert votes[3].reasoning == ["Extraction failed"]
    assert votes[4].reasoning == ["agent 5"]
    assert votes[5].reasoning == ["agent 6"]


def test_b3_short_bulk_result_still_yields_one_vote_per_agent() -> None:
    agents = [SimpleNamespace(name=f"Agent {index}") for index in range(1, 4)]
    orchestrator = SimpleNamespace(
        agents=agents,
        data_package=SimpleNamespace(company_name="Apple Inc.", ticker="AAPL"),
    )

    with patch("tinyic.debate.extraction.ResultsExtractor") as extractor_cls:
        extractor_cls.return_value.extract_results_from_agents.return_value = [
            {"vote": "BUY", "confidence": "HIGH", "reasoning": ["agent 1"]}
        ]
        votes = extract_votes(orchestrator)

    assert [vote.investor for vote in votes] == [
        "Agent 1",
        "Agent 2",
        "Agent 3",
    ]
    assert [vote.vote for vote in votes] == [
        VoteChoice.BUY,
        VoteChoice.HOLD,
        VoteChoice.HOLD,
    ]


# ---------------------------------------------------------------------------
# B4: full-history rendering and windowed synthesis
# ---------------------------------------------------------------------------


def test_b4_config_default_preserves_explicit_keyword_none() -> None:
    @config_manager.config_defaults(value="max_content_display_length")
    def configured(value=None):
        return value

    assert configured() == config_manager.get("max_content_display_length")
    assert configured(value=None) is None


def test_b4_config_default_preserves_explicit_positional_none() -> None:
    @config_manager.config_defaults(value="max_content_display_length")
    def configured(value=None):
        return value

    assert configured() == config_manager.get("max_content_display_length")
    assert configured(None) is None


def test_b4_explicit_none_still_inherits_non_display_config_defaults() -> None:
    """Unlimited rendering must not change every configured API's semantics."""
    @config_manager.config_defaults(value="model")
    def configured(value=None):
        return value

    assert configured(None) == config_manager.get("model")


def test_b4_vote_extraction_prompt_contains_speech_tail_beyond_1024_chars() -> None:
    speech = (
        "OPENING_LONG_SPEECH "
        + "durable moat owner earnings capital allocation valuation risk " * 80
        + " EXTRACTION_TAIL_SENTINEL"
    )

    with Session() as session:
        agent = TinyPerson("Long Speaker", session=session)
        agent.store_in_memory(
            {
                "role": "assistant",
                "content": {
                    "action": {"type": "TALK", "content": speech, "target": ""}
                },
                "type": "action",
                "simulation_timestamp": None,
            }
        )

        with patch("tinytroupe.extraction.results_extractor.client") as mock_client:
            mock_client.return_value.send_message.return_value = {
                "role": "assistant",
                "content": '{"vote": "BUY"}',
            }
            ResultsExtractor(fields=["vote"]).extract_results_from_agent(agent)

    messages = mock_client.return_value.send_message.call_args.args[0]
    extraction_prompt = next(
        message["content"] for message in messages if message["role"] == "user"
    )
    assert "EXTRACTION_TAIL_SENTINEL" in extraction_prompt


def test_b4_top_level_debate_requests_an_unlimited_transcript() -> None:
    personas = [SimpleNamespace(name="A"), SimpleNamespace(name="B")]
    orchestrator = MagicMock()
    orchestrator._phase_history = ["final_verdict"]
    orchestrator.get_cost_stats.return_value = {}
    orchestrator.pretty_current_interactions.return_value = "full transcript"

    with (
        patch(
            "tinyic.personas.registry.load_persona", side_effect=personas
        ),
        patch("tinyic.debate.DebateOrchestrator", return_value=orchestrator),
        patch(
            "tinyic.debate.extract_votes",
            return_value=[
                Vote(investor="A", vote="BUY", confidence="HIGH"),
                Vote(investor="B", vote="SELL", confidence="HIGH"),
            ],
        ),
        patch("tinyic.debate.build_scorecard", return_value=_scorecard()),
    ):
        run_debate(
            "AAPL",
            ["warren_buffett", "benjamin_graham"],
            data_package=_data_package(),
        )

    orchestrator.pretty_current_interactions.assert_called_once_with(
        max_content_length=None
    )


def test_b4_long_memo_uses_all_windows_and_carries_running_synthesis() -> None:
    response = {
        "role": "assistant",
        "content": json.dumps(_memo_response()),
    }

    with patch("tinyic.debate.memo.client") as mock_client:
        mock_client.return_value.send_message.return_value = response
        memo = generate_memo(_long_debate_result(), _data_package())

    assert isinstance(memo, InvestmentMemo)
    assert mock_client.return_value.send_message.call_count > 1
    prompts = [
        next(
            message["content"]
            for message in call.args[0]
            if message["role"] == "user"
        )
        for call in mock_client.return_value.send_message.call_args_list
    ]
    combined_prompts = "\n".join(prompts)
    assert "OPENING_SENTINEL" in combined_prompts
    assert "MIDDLE_SENTINEL" in combined_prompts
    assert "VERDICT_SENTINEL" in combined_prompts
    assert any(
        "RUNNING_SYNTHESIS_SENTINEL" in prompt for prompt in prompts[1:]
    )


def test_b4_long_disagreement_analysis_uses_all_transcript_windows() -> None:
    response = {
        "role": "assistant",
        "content": json.dumps(_disagreement_response()),
    }

    with patch("tinyic.debate.memo.client") as mock_client:
        mock_client.return_value.send_message.return_value = response
        analysis = extract_disagreements(_long_debate_result())

    assert isinstance(analysis, DisagreementAnalysis)
    assert mock_client.return_value.send_message.call_count > 1
    prompts = [
        next(
            message["content"]
            for message in call.args[0]
            if message["role"] == "user"
        )
        for call in mock_client.return_value.send_message.call_args_list
    ]
    combined_prompts = "\n".join(prompts)
    assert "OPENING_SENTINEL" in combined_prompts
    assert "MIDDLE_SENTINEL" in combined_prompts
    assert "VERDICT_SENTINEL" in combined_prompts


def test_b4_running_synthesis_has_a_deterministic_prompt_budget() -> None:
    """An expanding draft cannot crowd later transcript windows from context."""
    response = {
        "role": "assistant",
        "content": json.dumps(_memo_response("EXPANDING_DRAFT " * 10_000)),
    }

    with patch("tinyic.debate.memo.client") as mock_client:
        mock_client.return_value.send_message.return_value = response
        generate_memo(_long_debate_result(), _data_package())

    prompt_lengths = [
        sum(len(message["content"]) for message in call.args[0])
        for call in mock_client.return_value.send_message.call_args_list
    ]
    assert len(prompt_lengths) > 1
    assert max(prompt_lengths) <= 24_000


# ---------------------------------------------------------------------------
# B5: token-level repetition guard
# ---------------------------------------------------------------------------


def test_b5_distinct_recorded_speeches_are_below_repetition_threshold() -> None:
    graham = _recorded_speech("Benjamin Graham")
    marks = _recorded_speech("Howard Marks")
    assert len(graham) > 500
    assert len(marks) > 500

    similarity = _compute_single_action_jaccard_similarity(
        {"type": "TALK", "content": graham, "target": ""},
        {"type": "TALK", "content": marks, "target": ""},
    )

    assert similarity < TinyPerson.MAX_ACTION_SIMILARITY


def test_b5_guard_does_not_replace_distinct_long_speech_with_done(
    monkeypatch,
) -> None:
    graham = _recorded_speech("Benjamin Graham")
    marks = _recorded_speech("Howard Marks")
    action_generator = MagicMock()
    action_generator.generate_next_actions.return_value = (
        {"type": "TALK", "content": marks, "target": ""},
        "assistant",
        {},
        [],
    )
    monkeypatch.setattr(TinyPerson, "communication_display", False)

    with Session() as session:
        agent = TinyPerson(
            "Similarity Analyst",
            action_generator=action_generator,
            session=session,
        )
        agent.store_in_memory(
            {
                "role": "assistant",
                "content": {
                    "action": {"type": "TALK", "content": graham, "target": ""}
                },
                "type": "action",
                "simulation_timestamp": None,
            }
        )
        monkeypatch.setattr(agent, "consolidate_episode_memories", lambda: False)

        agent.act(return_actions=True)
        latest_actions = agent.pop_latest_actions()

    assert latest_actions == [{"type": "TALK", "content": marks, "target": ""}]


def test_b5_exact_repetition_still_exceeds_the_guard_threshold() -> None:
    speech = _recorded_speech("Benjamin Graham")
    similarity = _compute_single_action_jaccard_similarity(
        {"type": "TALK", "content": speech, "target": ""},
        {"type": "TALK", "content": speech, "target": ""},
    )
    assert similarity > TinyPerson.MAX_ACTION_SIMILARITY


def test_b5_near_duplicate_repeated_tokens_still_trip_the_guard() -> None:
    first = " ".join(["buy"] * 1000)
    second = " ".join(["buy"] * 999 + ["sell"])

    similarity = _compute_single_action_jaccard_similarity(
        {"type": "TALK", "content": first, "target": ""},
        {"type": "TALK", "content": second, "target": ""},
    )

    assert similarity > TinyPerson.MAX_ACTION_SIMILARITY


def test_b5_reordered_opposite_claims_are_not_treated_as_repetition() -> None:
    first = " ".join(["risk exceeds reward"] * 100)
    second = " ".join(["reward exceeds risk"] * 100)

    similarity = _compute_single_action_jaccard_similarity(
        {"type": "TALK", "content": first, "target": ""},
        {"type": "TALK", "content": second, "target": ""},
    )

    assert similarity < TinyPerson.MAX_ACTION_SIMILARITY


# ---------------------------------------------------------------------------
# D2: ordinary runs must not persist full prompts at DEBUG level
# ---------------------------------------------------------------------------


def test_d2_file_logging_defaults_to_info_and_root_level_is_initialized() -> None:
    import importlib
    import logging

    logging_config = importlib.import_module("tinytroupe.utils.config")
    config = read_config_file()

    assert config["Logging"].get("LOGLEVEL_FILE") == "INFO"
    assert logging_config._root_level == logging.ERROR


@pytest.mark.parametrize(
    "environment_name",
    ["OPENAI_API_KEY", "AZURE_OPENAI_KEY", "ANTHROPIC_AUTH_TOKEN"],
)
def test_d2_logging_filter_redacts_configured_credentials(
    monkeypatch, environment_name
) -> None:
    import importlib
    import logging

    secret = "opaque-environment-credential-123456789"
    monkeypatch.setenv(environment_name, secret)
    logging_config = importlib.import_module("tinytroupe.utils.config")
    record = logging.LogRecord(
        name="tinyic.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=f"provider rejected {secret}",
        args=(),
        exc_info=None,
    )

    logging_config.CredentialRedactionFilter().filter(record)

    assert secret not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()
