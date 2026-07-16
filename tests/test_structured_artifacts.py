"""M4 Stage-2 tests: structured opening theses and final verdicts (FR-4.4).

Opening theses and final verdicts are lifted from a mandated fenced trailing
block in the persona's prose and recorded as structured artifacts alongside the
free-form transcript. These tests cover the deterministic parser
(:mod:`tinyic.debate.structured`), the ``thesis_recorded`` emission in the
orchestrator, and the structured-first / LLM-fallback vote extraction that drives
``vote_recorded.source``.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import tinyic.debate as debate_module
import tinytroupe.clients as clients_module
from tinyic.data.models import DataPackage
from tinyic.debate.extraction import extract_votes
from tinyic.debate.models import Confidence, DebatePhase, VoteChoice
from tinyic.debate.orchestrator import DebateOrchestrator
from tinyic.debate.prompts import PHASE_PROMPTS
from tinyic.debate.structured import (
    StructuredThesis,
    StructuredVerdict,
    parse_thesis,
    parse_verdict,
)
from tinyic.events import EventEnvelope, EventLog, read_event_log
from tinyic.personas.base import InvestorPersona
from tinytroupe.agent import TinyPerson
from tinytroupe.environment.tiny_world import TinyWorld


FIXED_NOW = datetime(2026, 7, 13, 1, 2, 3, tzinfo=timezone.utc)

THESIS_TALK = (
    "Apple's ecosystem moat is durable and Services revenue compounds.\n"
    "===THESIS===\n"
    "STANCE: bullish\n"
    "CONFIDENCE: high\n"
    "CLAIMS:\n"
    "- Durable ecosystem moat\n"
    "- Services recurring revenue\n"
    "- Strong balance sheet\n"
    "===END==="
)

VERDICT_TALK = (
    "After the debate I remain constructive on the franchise.\n"
    "===VERDICT===\n"
    "VOTE: BUY\n"
    "CONFIDENCE: HIGH\n"
    "REASONS:\n"
    "- Durable moat\n"
    "- Cash generation\n"
    "RISKS:\n"
    "- Rich valuation\n"
    "CHANGED_MIND: no\n"
    "===END==="
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class RecordingLog:
    """A minimal event sink recording ``(type, payload)`` without schema gating."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self._seq = 0

    def emit(self, event_type: str, payload: dict):
        self._seq += 1
        self.events.append((event_type, dict(payload)))
        return SimpleNamespace(seq=self._seq)


def _mock_persona(name: str, talk: str) -> MagicMock:
    persona = MagicMock()
    persona.name = name
    persona.environment = None
    persona.act.return_value = [{"type": "TALK", "content": talk}]
    persona.pop_latest_actions.return_value = [
        {"type": "TALK", "content": talk, "target": ""}
    ]
    return persona


def _mock_data_package() -> MagicMock:
    package = MagicMock(spec=DataPackage)
    package.ticker = "AAPL"
    package.company_name = "Apple Inc."
    package.to_context_string.return_value = '{"ticker": "AAPL"}'
    return package


def _typed(events: list[tuple[str, dict]], event_type: str) -> list[dict]:
    return [payload for etype, payload in events if etype == event_type]


@pytest.fixture(autouse=True)
def _clear_environments():
    TinyWorld.all_environments.clear()
    yield
    TinyWorld.all_environments.clear()


# ===========================================================================
# Parser: theses
# ===========================================================================


class TestParseThesis:
    def test_wellformed_block(self):
        assert parse_thesis(THESIS_TALK) == StructuredThesis(
            stance="bullish",
            claims=[
                "Durable ecosystem moat",
                "Services recurring revenue",
                "Strong balance sheet",
            ],
            confidence="high",
        )

    def test_absent_block_returns_none(self):
        assert parse_thesis("Just prose, no structured block here.") is None

    def test_empty_or_none_returns_none(self):
        assert parse_thesis("") is None
        assert parse_thesis(None) is None

    def test_missing_stance_returns_none(self):
        # A block with no recognizable stance is malformed -> no record.
        text = "===THESIS===\nCONFIDENCE: high\nCLAIMS:\n- a claim\n===END==="
        assert parse_thesis(text) is None

    def test_unrecognized_stance_returns_none(self):
        text = "===THESIS===\nSTANCE: undecided\n===END==="
        assert parse_thesis(text) is None

    def test_case_separator_and_bullet_tolerance(self):
        text = (
            "==thesis==\n"
            "stance = Bearish.\n"
            "confidence = LOW\n"
            "claims:\n"
            "* only one claim\n"
            "==end=="
        )
        assert parse_thesis(text) == StructuredThesis(
            stance="bearish", claims=["only one claim"], confidence="low"
        )

    def test_missing_end_marker_parses_to_end_of_text(self):
        text = (
            "prose\n"
            "===THESIS===\n"
            "STANCE: neutral\n"
            "CONFIDENCE: medium\n"
            "CLAIMS:\n"
            "- alpha\n"
            "- beta"
        )
        thesis = parse_thesis(text)
        assert thesis.stance == "neutral"
        assert thesis.claims == ["alpha", "beta"]

    def test_stance_only_thesis_defaults_confidence_and_empty_claims(self):
        thesis = parse_thesis("===THESIS===\nSTANCE: bullish\n===END===")
        assert thesis.stance == "bullish"
        assert thesis.claims == []
        assert thesis.confidence == "medium"

    def test_last_block_wins_over_an_earlier_draft(self):
        text = (
            "===THESIS===\nSTANCE: bullish\n===END===\n"
            "On reflection I revise my view:\n"
            "===THESIS===\nSTANCE: bearish\nCONFIDENCE: high\n===END==="
        )
        assert parse_thesis(text).stance == "bearish"

    def test_numbered_claims_are_parsed(self):
        text = (
            "===THESIS===\n"
            "STANCE: bullish\n"
            "CLAIMS:\n"
            "1. first\n"
            "2. second\n"
            "===END==="
        )
        assert parse_thesis(text).claims == ["first", "second"]


# ===========================================================================
# Parser: verdicts
# ===========================================================================


class TestParseVerdict:
    def test_wellformed_block(self):
        assert parse_verdict(VERDICT_TALK) == StructuredVerdict(
            vote="BUY",
            confidence="HIGH",
            reasons=["Durable moat", "Cash generation"],
            risks=["Rich valuation"],
            changed_mind=False,
        )

    def test_absent_block_returns_none(self):
        assert parse_verdict("No verdict block in this prose.") is None

    def test_empty_or_none_returns_none(self):
        assert parse_verdict("") is None
        assert parse_verdict(None) is None

    def test_missing_vote_returns_none(self):
        # No parseable vote -> defer to LLM extraction fallback.
        assert parse_verdict("===VERDICT===\nCONFIDENCE: HIGH\n===END===") is None

    def test_fuzzy_vote_and_default_lists(self):
        verdict = parse_verdict(
            "===VERDICT===\nVOTE: STRONG SELL\nCONFIDENCE: medium\n===END==="
        )
        assert verdict.vote == "SELL"
        assert verdict.confidence == "MEDIUM"
        assert verdict.reasons == []
        assert verdict.risks == []
        assert verdict.changed_mind is False

    def test_changed_mind_affirmative(self):
        verdict = parse_verdict(
            "===VERDICT===\nVOTE: HOLD\nCHANGED_MIND: yes\n===END==="
        )
        assert verdict.changed_mind is True

    def test_changed_mind_negative(self):
        verdict = parse_verdict(
            "===VERDICT===\nVOTE: HOLD\nCHANGED_MIND: no\n===END==="
        )
        assert verdict.changed_mind is False

    def test_thesis_block_alone_is_not_a_verdict(self):
        # A persona who emits only a thesis block yields no structured verdict.
        assert parse_verdict(THESIS_TALK) is None

    def test_both_blocks_are_parsed_independently(self):
        combined = THESIS_TALK + "\n\n" + VERDICT_TALK
        assert parse_thesis(combined).stance == "bullish"
        assert parse_verdict(combined).vote == "BUY"


# ===========================================================================
# Prompt contract
# ===========================================================================


class TestPromptContract:
    def test_blocks_mandated_only_in_opening_and_verdict(self):
        assert "===THESIS===" in PHASE_PROMPTS[DebatePhase.OPENING]
        assert "===VERDICT===" in PHASE_PROMPTS[DebatePhase.VERDICT]
        # Free-form NL is reserved for cross-exam and rebuttal (no block).
        for phase in (DebatePhase.CROSS_EXAM, DebatePhase.REBUTTAL):
            assert "===THESIS===" not in PHASE_PROMPTS[phase]
            assert "===VERDICT===" not in PHASE_PROMPTS[phase]

    def test_prompts_format_with_company_and_leave_no_braces(self):
        for phase in (DebatePhase.OPENING, DebatePhase.VERDICT):
            rendered = PHASE_PROMPTS[phase].format(company="Apple Inc.")
            assert "Apple Inc." in rendered
            # Angle-bracket placeholders, never ``{...}`` -> format() is a no-op
            # on the block and cannot raise a KeyError.
            assert "{" not in rendered and "}" not in rendered


# ===========================================================================
# Orchestrator: thesis_recorded emission
# ===========================================================================


class TestThesisEmission:
    def test_opening_thesis_emitted_once_per_persona(self):
        personas = [
            _mock_persona("Warren Buffett", THESIS_TALK),
            _mock_persona("Benjamin Graham", THESIS_TALK),
        ]
        log = RecordingLog()
        orch = DebateOrchestrator(
            name="thesis_emit",
            personas=personas,
            data_package=_mock_data_package(),
            event_log=log,
        )
        orch.run_debate()

        theses = _typed(log.events, "thesis_recorded")
        # Exactly one per persona, and only in the opening (the thesis block is
        # present in every mocked turn, but only the opening phase records it).
        assert len(theses) == 2
        assert {t["persona"] for t in theses} == {
            "Warren Buffett",
            "Benjamin Graham",
        }
        for thesis in theses:
            assert thesis["phase"] == "opening"
            assert thesis["stance"] == "bullish"
            assert thesis["confidence"] == "high"
            assert thesis["claims"] == [
                "Durable ecosystem moat",
                "Services recurring revenue",
                "Strong balance sheet",
            ]

    def test_thesis_recorded_precedes_the_first_cross_exam_turn(self):
        personas = [
            _mock_persona("A", THESIS_TALK),
            _mock_persona("B", THESIS_TALK),
        ]
        log = RecordingLog()
        orch = DebateOrchestrator(
            name="thesis_order",
            personas=personas,
            data_package=_mock_data_package(),
            event_log=log,
        )
        orch.run_debate()

        types = [etype for etype, _ in log.events]
        # A recorded thesis is emitted after its opening turn completes and
        # before cross-exam begins.
        first_thesis = types.index("thesis_recorded")
        first_cross = next(
            i
            for i, (etype, payload) in enumerate(log.events)
            if etype == "phase_started" and payload.get("phase") == "cross_exam"
        )
        assert first_thesis < first_cross

    def test_emitted_thesis_payload_passes_the_schema_validator(self):
        personas = [
            _mock_persona("Warren Buffett", THESIS_TALK),
            _mock_persona("Benjamin Graham", THESIS_TALK),
        ]
        log = RecordingLog()
        orch = DebateOrchestrator(
            name="thesis_schema",
            personas=personas,
            data_package=_mock_data_package(),
            event_log=log,
        )
        orch.run_debate()

        payload = _typed(log.events, "thesis_recorded")[0]
        envelope = EventEnvelope.model_validate(
            {
                "v": 1,
                "seq": 1,
                "ts": "2026-07-13T01:02:03.000Z",
                "debate_id": "aapl-20260713-a3f2",
                "type": "thesis_recorded",
                "payload": payload,
            }
        )
        assert envelope.payload["stance"] == "bullish"

    def test_malformed_opening_block_emits_no_thesis(self):
        personas = [
            _mock_persona("A", "Only prose, no structured block."),
            _mock_persona("B", "===THESIS===\nCONFIDENCE: high\n===END==="),
        ]
        log = RecordingLog()
        orch = DebateOrchestrator(
            name="thesis_malformed",
            personas=personas,
            data_package=_mock_data_package(),
            event_log=log,
        )
        orch.run_debate()

        assert _typed(log.events, "thesis_recorded") == []

    def test_verdict_block_is_recorded_on_the_moderator(self):
        personas = [
            _mock_persona("A", VERDICT_TALK),
            _mock_persona("B", VERDICT_TALK),
        ]
        orch = DebateOrchestrator(
            name="verdict_record",
            personas=personas,
            data_package=_mock_data_package(),
        )
        orch.run_debate()

        recorded = orch.moderator.recorded_verdicts
        assert set(recorded) == {"A", "B"}
        assert recorded["A"].vote == "BUY"
        # A verdict block never masquerades as an opening thesis.
        assert orch.moderator.recorded_theses == {}


# ===========================================================================
# Vote extraction: structured-first, LLM fallback, source field
# ===========================================================================


class TestVoteExtractionSource:
    def test_structured_verdicts_yield_structured_source_without_llm(self):
        personas = [_mock_persona("A", ""), _mock_persona("B", "")]
        orch = DebateOrchestrator(
            name="ev_structured",
            personas=personas,
            data_package=_mock_data_package(),
        )
        orch.moderator.recorded_verdicts = {
            "A": StructuredVerdict(
                vote="BUY",
                confidence="HIGH",
                reasons=["r1", "r2"],
                risks=["k1"],
                changed_mind=True,
            ),
            "B": StructuredVerdict(vote="SELL", confidence="LOW"),
        }

        with patch("tinyic.debate.extraction.ResultsExtractor") as mock_extractor:
            votes = extract_votes(orch)
            # Every persona had a structured verdict, so no LLM extractor is built.
            mock_extractor.assert_not_called()

        assert [v.source for v in votes] == ["structured", "structured"]
        assert votes[0].vote == VoteChoice.BUY
        assert votes[0].confidence == Confidence.HIGH
        assert votes[0].reasoning == ["r1", "r2"]
        assert votes[0].key_risks == ["k1"]
        assert votes[0].changed_mind is True
        assert votes[1].vote == VoteChoice.SELL
        assert votes[1].confidence == Confidence.LOW

    @patch("tinyic.debate.extraction.ResultsExtractor")
    def test_absent_verdicts_fall_back_to_llm_extraction(self, MockExtractor):
        MockExtractor.return_value.extract_results_from_agent.side_effect = [
            {"vote": "BUY", "confidence": "HIGH"},
            {"vote": "HOLD", "confidence": "MEDIUM"},
        ]
        personas = [_mock_persona("A", ""), _mock_persona("B", "")]
        orch = DebateOrchestrator(
            name="ev_extracted",
            personas=personas,
            data_package=_mock_data_package(),
        )
        # No structured verdicts recorded -> every persona goes through the LLM.
        votes = extract_votes(orch)

        assert [v.source for v in votes] == ["extracted", "extracted"]
        assert votes[0].vote == VoteChoice.BUY
        assert votes[1].vote == VoteChoice.HOLD

    @patch("tinyic.debate.extraction.ResultsExtractor")
    def test_mixed_sources_only_extract_the_unstructured_subset(
        self, MockExtractor
    ):
        mock_instance = MockExtractor.return_value
        # Only one persona (B) lacks a structured verdict, so the extractor is
        # asked for exactly one result.
        mock_instance.extract_results_from_agent.return_value = {
            "vote": "SELL",
            "confidence": "LOW",
        }
        personas = [_mock_persona("A", ""), _mock_persona("B", "")]
        orch = DebateOrchestrator(
            name="ev_mixed",
            personas=personas,
            data_package=_mock_data_package(),
        )
        orch.moderator.recorded_verdicts = {
            "A": StructuredVerdict(vote="BUY", confidence="HIGH")
        }

        votes = extract_votes(orch)

        assert votes[0].source == "structured"
        assert votes[0].vote == VoteChoice.BUY
        assert votes[1].source == "extracted"
        assert votes[1].vote == VoteChoice.SELL

        # The LLM extractor saw only the persona lacking a structured verdict.
        call = mock_instance.extract_results_from_agent.call_args
        assert call.args[0].name == "B"

    @patch("tinyic.debate.extraction.ResultsExtractor")
    def test_llm_failure_still_reports_extracted_source(self, MockExtractor):
        MockExtractor.return_value.extract_results_from_agent.side_effect = (
            RuntimeError("provider down")
        )
        personas = [_mock_persona("A", ""), _mock_persona("B", "")]
        orch = DebateOrchestrator(
            name="ev_fail",
            personas=personas,
            data_package=_mock_data_package(),
        )
        votes = extract_votes(orch)

        assert [v.source for v in votes] == ["extracted", "extracted"]
        assert all(v.vote == VoteChoice.HOLD for v in votes)
        assert all(v.confidence == Confidence.LOW for v in votes)


# ===========================================================================
# End-to-end: run_debate emits structured artifacts and structured votes
# ===========================================================================


def test_run_debate_emits_structured_thesis_and_structured_votes(
    tmp_path, monkeypatch
):
    """A debate whose personas emit both blocks records theses and structured votes."""
    both_blocks = THESIS_TALK + "\n\n" + VERDICT_TALK

    class _Counter:
        def __init__(self) -> None:
            self.stats = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "model_calls": 0,
                "cached_calls": 0,
            }

        def record_turn(self) -> None:
            self.stats["input_tokens"] += 10
            self.stats["output_tokens"] += 2
            self.stats["total_tokens"] += 12
            self.stats["model_calls"] += 1

        def get_cost_stats(self) -> dict:
            return dict(self.stats)

    counter = _Counter()

    def act_with_blocks(self, *, return_actions=False, **_kwargs):
        counter.record_turn()
        cognitive_state = {
            "goals": f"Evaluate AAPL as {self.name}",
            "attention": "Valuation and downside risk",
            "emotions": "Skeptical but engaged",
            "context": ["Investment committee debate"],
        }
        actions = [
            {"type": "THINK", "content": f"{self.name} weighs it.", "target": ""},
            {"type": "TALK", "content": both_blocks, "target": ""},
            {"type": "DONE", "content": "", "target": ""},
        ]
        self._actions_buffer.extend(actions)
        committed = [
            {"action": action, "cognitive_state": cognitive_state}
            for action in actions
        ]
        return committed if return_actions else self

    monkeypatch.setattr(clients_module, "client", lambda: counter)
    monkeypatch.setattr(InvestorPersona, "act", act_with_blocks)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(DebateOrchestrator, "get_cost_stats", lambda _self: {})
    monkeypatch.setattr(TinyPerson, "communication_display", False)

    data_package = DataPackage(
        ticker="AAPL",
        company_name="Apple Inc.",
        description="Consumer technology company.",
        fetched_at=FIXED_NOW,
    )
    log_path = tmp_path / "structured.jsonl"
    with EventLog(
        "aapl-20260713-strc", path=log_path, clock=lambda: FIXED_NOW
    ) as event_log:
        debate_module.run_debate(
            "AAPL",
            ["warren_buffett", "benjamin_graham"],
            data_package=data_package,
            event_log=event_log,
        )

    events = read_event_log(log_path)
    theses = [e for e in events if e.type == "thesis_recorded"]
    votes = [e for e in events if e.type == "vote_recorded"]

    # Two opening theses, each schema-valid (read_event_log validates the stream).
    assert len(theses) == 2
    assert all(e.payload["phase"] == "opening" for e in theses)
    assert all(e.payload["stance"] == "bullish" for e in theses)
    assert all(e.payload["claims"] for e in theses)
    assert all(e.payload["confidence"] == "high" for e in theses)

    # Votes come from the structured verdict blocks, not LLM extraction.
    assert len(votes) == 2
    assert all(e.payload["source"] == "structured" for e in votes)
    assert all(e.payload["vote"] == "BUY" for e in votes)

    # The scorecard reflects the structured votes (both BUY -> consensus BUY).
    scorecard = [e for e in events if e.type == "scorecard"][0]
    assert scorecard.payload["consensus"] == "BUY"
    assert scorecard.payload["bull_count"] == 2
