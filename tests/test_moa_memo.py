"""M4 Stage-3 tests: MoA memo/disagreement writer + collapse analytics (FR-4.5).

Covers:
* the Together-MoA skeptical framing + structured-records **primary grounding**
  in the memo/disagreement prompts;
* **window-failure resilience** — a failed transcript window retains the last
  good accumulated draft and finishes with it (the carried M1 review item);
* the **collapse analytics** — per-persona stance trajectory (thesis -> verdict),
  the "caved under pressure" heuristic, and the DCR summary; and
* the end-to-end wiring: a committee ``run_debate`` emits ``collapse_metric`` /
  ``disagreement`` / ``memo_section`` events, all schema-valid, with the memo
  routed through the aggregator binding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import tinyic.debate as debate_module
from tinyic.data.models import DataPackage
from tinyic.debate.analytics import (
    analyze_collapse,
    render_structured_grounding,
    stance_for_vote,
)
from tinyic.debate.memo import (
    DISAGREEMENT_SYSTEM_PROMPT,
    MEMO_SYSTEM_PROMPT,
    TRANSCRIPT_WINDOW_LENGTH,
    extract_disagreements,
    generate_memo,
)
from tinyic.debate.models import (
    DebateResult,
    InvestmentMemo,
    Scorecard,
    Vote,
    VoteChoice,
)
from tinyic.debate.structured import StructuredThesis
from tinyic.events import EventLog, read_event_log
from tinyic.models import BindingSpec, Preset, StaticCredentialProvider, build_committee
from tinyic.models.adapters.openai_chat import OpenAIChatAdapter
from tinyic.personas.base import InvestorPersona
from tinytroupe.agent import TinyPerson
from tinytroupe.environment.tiny_world import TinyWorld


FIXED_NOW = datetime(2026, 7, 13, 1, 2, 3, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scorecard(votes: list[Vote]) -> Scorecard:
    return Scorecard(
        ticker="AAPL",
        company_name="Apple Inc.",
        votes=votes,
        bull_count=sum(v.vote == VoteChoice.BUY for v in votes),
        bear_count=sum(v.vote == VoteChoice.SELL for v in votes),
        hold_count=sum(v.vote == VoteChoice.HOLD for v in votes),
    )


def _debate_result(transcript: str, votes: list[Vote]) -> DebateResult:
    return DebateResult(
        ticker="AAPL",
        company_name="Apple Inc.",
        scorecard=_scorecard(votes),
        phases_completed=["opening_statements", "final_verdict"],
        transcript=transcript,
    )


def _data_package() -> MagicMock:
    package = MagicMock()
    package.ticker = "AAPL"
    package.company_name = "Apple Inc."
    package.to_context_string.return_value = json.dumps({"ticker": "AAPL", "pe_ratio": 30.0})
    return package


def _memo_json(marker: str) -> dict:
    def section(content: str) -> dict:
        return {
            "content": content,
            "contributing_personas": ["Warren Buffett"],
            "supporting_data": ["pe_ratio"],
        }

    return {
        "executive_summary": section(marker),
        "investment_thesis": section("thesis"),
        "key_risks": section("risks"),
        "valuation_discussion": section("valuation"),
        "final_verdict": section("verdict"),
    }


def _memo_response(marker: str) -> dict:
    return {"role": "assistant", "content": json.dumps(_memo_json(marker))}


def _disagreement_json() -> dict:
    return {
        "disagreements": [
            {
                "dimension": "Valuation",
                "description": "Whether 30x is justified.",
                "sides": [
                    {
                        "persona": "Warren Buffett",
                        "position": "Quality justifies the price.",
                        "evidence_quote": "buying the business, not the mood",
                    },
                    {
                        "persona": "Benjamin Graham",
                        "position": "No margin of safety.",
                        "evidence_quote": "no margin of safety at thirty times",
                    },
                ],
                "resolution": "Unresolved.",
            }
        ]
    }


def _disagreement_response(marker: str) -> dict:
    payload = _disagreement_json()
    payload["disagreements"][0]["description"] = marker
    return {"role": "assistant", "content": json.dumps(payload)}


def _multi_window_transcript() -> str:
    # Three windows: each slot is exactly one TRANSCRIPT_WINDOW_LENGTH chunk.
    return (
        "W1 " + "a" * (TRANSCRIPT_WINDOW_LENGTH - 3)
        + "W2 " + "b" * (TRANSCRIPT_WINDOW_LENGTH - 3)
        + "W3 " + "c" * 100
    )


# ===========================================================================
# (1) MoA skeptical framing + structured-records primary grounding
# ===========================================================================


class TestMoAFramingAndGrounding:
    def test_system_prompts_carry_together_moa_skeptical_framing(self):
        for prompt in (MEMO_SYSTEM_PROMPT, DISAGREEMENT_SYSTEM_PROMPT):
            assert "critically evaluate" in prompt.lower()
            assert "may be biased or incorrect" in prompt.lower()
            assert "PRIMARY GROUNDING" in prompt

    @patch("tinyic.debate.memo.client")
    def test_memo_prompt_grounds_on_structured_records(self, mock_client):
        mock_client.return_value.send_message.return_value = _memo_response("ok")
        votes = [
            Vote(investor="Warren Buffett", vote="BUY", confidence="HIGH", reasoning=["Durable moat"]),
        ]
        result = _debate_result("Short transcript.", votes)
        theses = {
            "Warren Buffett": StructuredThesis(
                stance="bullish", claims=["Durable ecosystem moat"], confidence="high"
            )
        }

        generate_memo(result, _data_package(), theses=theses)

        user_prompt = _user_prompt(mock_client)
        assert "PRIMARY GROUNDING" in user_prompt
        assert "bullish" in user_prompt  # opening thesis stance
        assert "Durable ecosystem moat" in user_prompt  # thesis claim
        assert "BUY" in user_prompt  # final verdict vote

    @patch("tinyic.debate.memo.client")
    def test_memo_without_theses_omits_grounding_slot(self, mock_client):
        mock_client.return_value.send_message.return_value = _memo_response("ok")
        result = _debate_result("Short transcript.", [])
        generate_memo(result, _data_package())
        assert "PRIMARY GROUNDING" not in _user_prompt(mock_client)

    @patch("tinyic.debate.memo.client")
    def test_disagreement_prompt_grounds_on_structured_records(self, mock_client):
        mock_client.return_value.send_message.return_value = _disagreement_response("d")
        votes = [Vote(investor="Benjamin Graham", vote="SELL", confidence="HIGH", reasoning=["Expensive"])]
        result = _debate_result("Short transcript.", votes)
        theses = {
            "Benjamin Graham": StructuredThesis(stance="bearish", claims=["No margin of safety"])
        }
        extract_disagreements(result, theses=theses)
        user_prompt = _user_prompt(mock_client)
        assert "PRIMARY GROUNDING" in user_prompt
        assert "bearish" in user_prompt
        assert "SELL" in user_prompt

    def test_render_structured_grounding_is_attributable(self):
        theses = {"Warren Buffett": StructuredThesis(stance="bullish", claims=["Moat"])}
        votes = [Vote(investor="Warren Buffett", vote="BUY", confidence="HIGH", reasoning=["Moat"])]
        block = render_structured_grounding(theses, votes)
        assert "Warren Buffett: bullish" in block
        assert "Warren Buffett: BUY" in block


def _user_prompt(mock_client) -> str:
    messages = mock_client.return_value.send_message.call_args[0][0]
    return next(m["content"] for m in messages if m["role"] == "user")


# ===========================================================================
# (2) Window-failure resilience (carried M1 review item)
# ===========================================================================


class TestWindowFailureResilience:
    @patch("tinyic.debate.memo.client")
    def test_memo_mid_window_failure_retains_and_continues(self, mock_client):
        # Window 2 raises; windows 1 and 3 succeed. The final memo reflects
        # window 3 built atop the retained window-1 draft (not a fallback).
        mock_client.return_value.send_message.side_effect = [
            _memo_response("DRAFT_V1"),
            RuntimeError("provider blipped"),
            _memo_response("DRAFT_V3"),
        ]
        result = _debate_result(_multi_window_transcript(), [])

        memo = generate_memo(result, _data_package())

        assert mock_client.return_value.send_message.call_count == 3
        assert isinstance(memo, InvestmentMemo)
        assert memo.executive_summary.content == "DRAFT_V3"
        assert "failed" not in memo.executive_summary.content.lower()
        # Window 3's running draft is the retained window-1 draft (window 2 never
        # committed), proving the accumulation survived the failure.
        window3_prompt = mock_client.return_value.send_message.call_args_list[2][0][0]
        window3_user = next(m["content"] for m in window3_prompt if m["role"] == "user")
        assert "DRAFT_V1" in window3_user

    @patch("tinyic.debate.memo.client")
    def test_memo_last_window_failure_finishes_with_prior_draft(self, mock_client):
        mock_client.return_value.send_message.side_effect = [
            _memo_response("DRAFT_V1"),
            _memo_response("DRAFT_V2"),
            RuntimeError("provider blipped"),
        ]
        result = _debate_result(_multi_window_transcript(), [])
        memo = generate_memo(result, _data_package())
        # Finishes with the last good draft (window 2), not a fallback.
        assert memo.executive_summary.content == "DRAFT_V2"

    @patch("tinyic.debate.memo.client")
    def test_memo_every_window_failing_falls_back(self, mock_client):
        mock_client.return_value.send_message.side_effect = RuntimeError("down")
        result = _debate_result(_multi_window_transcript(), [])
        memo = generate_memo(result, _data_package())
        assert "failed" in memo.executive_summary.content.lower()

    @patch("tinyic.debate.memo.client")
    def test_memo_bad_json_window_does_not_discard_prior_draft(self, mock_client):
        mock_client.return_value.send_message.side_effect = [
            _memo_response("DRAFT_V1"),
            {"role": "assistant", "content": "not json at all"},
            _memo_response("DRAFT_V3"),
        ]
        result = _debate_result(_multi_window_transcript(), [])
        memo = generate_memo(result, _data_package())
        assert memo.executive_summary.content == "DRAFT_V3"

    @patch("tinyic.debate.memo.client")
    def test_disagreement_mid_window_failure_retains_and_continues(self, mock_client):
        mock_client.return_value.send_message.side_effect = [
            _disagreement_response("D1"),
            RuntimeError("provider blipped"),
            _disagreement_response("D3"),
        ]
        result = _debate_result(_multi_window_transcript(), [])
        analysis = extract_disagreements(result)
        assert mock_client.return_value.send_message.call_count == 3
        # The retained draft carried through; a real disagreement is returned.
        assert len(analysis.disagreements) == 1
        assert analysis.disagreements[0].description == "D3"


# ===========================================================================
# (3) Collapse analytics: trajectories, "caved" heuristic, DCR
# ===========================================================================


class TestCollapseAnalytics:
    def test_stance_for_vote_mapping(self):
        assert stance_for_vote(VoteChoice.BUY) == "bullish"
        assert stance_for_vote(VoteChoice.SELL) == "bearish"
        assert stance_for_vote(VoteChoice.HOLD) == "neutral"
        assert stance_for_vote("buy") == "bullish"

    def test_caved_contrarian_without_new_evidence(self):
        theses = {
            "Warren Buffett": StructuredThesis(stance="bullish", claims=["Durable moat"]),
            "Benjamin Graham": StructuredThesis(
                stance="bearish", claims=["No margin of safety at thirty times"]
            ),
            "Peter Lynch": StructuredThesis(stance="neutral", claims=["Growth uncertain"]),
            "Howard Marks": StructuredThesis(stance="bearish", claims=["Late cycle risk"]),
        }
        votes = [
            Vote(investor="Warren Buffett", vote="BUY", confidence="HIGH", reasoning=["Durable moat"]),
            # Contrarian bearish -> BUY majority, reasoning echoes the thesis: CAVED.
            Vote(
                investor="Benjamin Graham",
                vote="BUY",
                confidence="MEDIUM",
                reasoning=["No margin of safety at thirty times"],
            ),
            # Contrarian neutral -> BUY majority, but cites new evidence: NOT caved.
            Vote(
                investor="Peter Lynch",
                vote="BUY",
                confidence="MEDIUM",
                reasoning=["Services annuity reframes the recurring revenue story"],
            ),
            # Held the contrarian bearish line to a SELL: NOT caved.
            Vote(investor="Howard Marks", vote="SELL", confidence="HIGH", reasoning=["Late cycle risk"]),
        ]

        summary = analyze_collapse(theses, votes)

        assert summary.majority_stance == "bullish"
        assert summary.assessed_count == 4
        assert summary.caved_count == 1
        assert summary.caved_personas == ["Benjamin Graham"]
        assert summary.disagreement_collapse_rate == 0.25
        caved = {s.persona: s.caved for s in summary.shifts}
        assert caved == {
            "Warren Buffett": False,
            "Benjamin Graham": True,
            "Peter Lynch": False,
            "Howard Marks": False,
        }
        graham = next(s for s in summary.shifts if s.persona == "Benjamin Graham")
        assert graham.stance_before == "bearish"
        assert graham.stance_after == "bullish"

    def test_no_clear_majority_flags_nobody(self):
        # A 2-2 split is not "majority pressure": no caves even though A flipped.
        theses = {
            "A": StructuredThesis(stance="bearish", claims=["cheap-only-mindset"]),
            "B": StructuredThesis(stance="bearish", claims=["risk"]),
            "C": StructuredThesis(stance="bullish", claims=["moat"]),
            "D": StructuredThesis(stance="bullish", claims=["growth"]),
        }
        votes = [
            Vote(investor="A", vote="BUY", confidence="LOW", reasoning=["cheap-only-mindset"]),
            Vote(investor="B", vote="SELL", confidence="HIGH", reasoning=["risk"]),
            Vote(investor="C", vote="BUY", confidence="HIGH", reasoning=["moat"]),
            Vote(investor="D", vote="SELL", confidence="LOW", reasoning=["growth"]),
        ]
        summary = analyze_collapse(theses, votes)
        assert summary.majority_stance is None
        assert summary.caved_count == 0
        assert summary.disagreement_collapse_rate == 0.0

    def test_no_opening_claims_is_conservatively_not_caved(self):
        # Without a claims baseline we cannot prove the shift lacked new evidence.
        theses = {"A": StructuredThesis(stance="bearish", claims=[])}
        votes = [
            Vote(investor="A", vote="BUY", confidence="LOW", reasoning=["aligning"]),
            Vote(investor="B", vote="BUY", confidence="HIGH", reasoning=["moat"]),
            Vote(investor="C", vote="BUY", confidence="HIGH", reasoning=["moat"]),
        ]
        summary = analyze_collapse(theses, votes)
        assert summary.majority_stance == "bullish"
        assert summary.caved_count == 0

    def test_persona_without_a_thesis_has_no_trajectory(self):
        theses = {"A": StructuredThesis(stance="bullish", claims=["moat"])}
        votes = [
            Vote(investor="A", vote="BUY", confidence="HIGH", reasoning=["moat"]),
            Vote(investor="B", vote="BUY", confidence="HIGH", reasoning=["moat"]),
        ]
        summary = analyze_collapse(theses, votes)
        assert summary.assessed_count == 1
        assert [s.persona for s in summary.shifts] == ["A"]

    def test_empty_records_yield_empty_summary(self):
        summary = analyze_collapse({}, [])
        assert summary.assessed_count == 0
        assert summary.disagreement_collapse_rate == 0.0
        assert summary.shifts == []


# ===========================================================================
# (4) DCR summary folded into the disagreement-analysis output
# ===========================================================================


class TestDcrSummaryInOutput:
    @patch("tinyic.debate.memo.client")
    def test_collapse_summary_attached_when_theses_supplied(self, mock_client):
        mock_client.return_value.send_message.return_value = _disagreement_response("d")
        votes = [
            Vote(investor="Warren Buffett", vote="BUY", confidence="HIGH", reasoning=["Moat"]),
            Vote(investor="Benjamin Graham", vote="BUY", confidence="LOW", reasoning=["No margin of safety"]),
        ]
        result = _debate_result("Short transcript.", votes)
        theses = {
            "Warren Buffett": StructuredThesis(stance="bullish", claims=["Moat"]),
            "Benjamin Graham": StructuredThesis(stance="bearish", claims=["No margin of safety"]),
        }
        analysis = extract_disagreements(result, theses=theses)
        assert analysis.collapse_summary is not None
        assert analysis.collapse_summary.caved_personas == ["Benjamin Graham"]
        assert analysis.collapse_summary.disagreement_collapse_rate == 0.5
        # The DCR block renders into the analysis markdown output.
        assert "Disagreement Collapse (DCR)" in analysis.to_markdown()

    @patch("tinyic.debate.memo.client")
    def test_collapse_summary_survives_total_extraction_failure(self, mock_client):
        # Even if every disagreement window fails, the LLM-independent collapse
        # summary is still computed and attached.
        mock_client.return_value.send_message.side_effect = RuntimeError("down")
        votes = [
            Vote(investor="A", vote="BUY", confidence="HIGH", reasoning=["moat"]),
            Vote(investor="B", vote="BUY", confidence="LOW", reasoning=["no margin"]),
        ]
        result = _debate_result("Short.", votes)
        theses = {
            "A": StructuredThesis(stance="bullish", claims=["moat"]),
            "B": StructuredThesis(stance="bearish", claims=["no margin"]),
        }
        analysis = extract_disagreements(result, theses=theses)
        assert analysis.disagreements == []
        assert analysis.collapse_summary is not None
        assert analysis.collapse_summary.caved_personas == ["B"]

    @patch("tinyic.debate.memo.client")
    def test_no_theses_leaves_collapse_summary_none(self, mock_client):
        mock_client.return_value.send_message.return_value = _disagreement_response("d")
        analysis = extract_disagreements(_debate_result("Short.", []))
        assert analysis.collapse_summary is None


# ===========================================================================
# (5) End-to-end: committee run_debate emits the synthesis artifacts
# ===========================================================================


@dataclass
class _FakeResponse:
    status_code: int = 200
    lines: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)

    def header(self, name: str):
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def iter_lines(self):
        yield from self.lines

    def read_text(self) -> str:
        return "\n".join(self.lines)

    def close(self) -> None:
        pass


class _RepeatingTransport:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def send(self, request):
        return _FakeResponse(200, lines=list(self._lines))

    def close(self) -> None:
        pass


def _aggregator_sse(payload: dict) -> list[str]:
    content = json.dumps(payload)
    delta = json.dumps(
        {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "stop"}]}
    )
    usage = json.dumps(
        {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80}}
    )
    return f"data: {delta}\n\ndata: {usage}\n\ndata: [DONE]\n".split("\n")


def _blocks(*, stance: str, claim: str, vote: str, reason: str, changed: str) -> str:
    return (
        "My opening view on Apple.\n"
        "===THESIS===\n"
        f"STANCE: {stance}\n"
        "CONFIDENCE: high\n"
        "CLAIMS:\n"
        f"- {claim}\n"
        "===END===\n\n"
        "My final position after the debate.\n"
        "===VERDICT===\n"
        f"VOTE: {vote}\n"
        "CONFIDENCE: HIGH\n"
        "REASONS:\n"
        f"- {reason}\n"
        "RISKS:\n"
        "- Valuation is rich\n"
        f"CHANGED_MIND: {changed}\n"
        "===END==="
    )


_PERSONA_BLOCKS = {
    # Bullish -> BUY: held; not caved.
    "Warren Buffett": _blocks(
        stance="bullish", claim="Durable ecosystem moat", vote="BUY",
        reason="Durable ecosystem moat", changed="no",
    ),
    # Contrarian bearish -> BUY majority, reason echoes the thesis: CAVED.
    "Benjamin Graham": _blocks(
        stance="bearish", claim="No margin of safety at thirty times earnings", vote="BUY",
        reason="No margin of safety at thirty times earnings", changed="yes",
    ),
    # Bullish -> BUY: held; not caved.
    "Charlie Munger": _blocks(
        stance="bullish", claim="A wonderful business", vote="BUY",
        reason="A wonderful business", changed="no",
    ),
}


@pytest.fixture
def _clear_registries():
    TinyWorld.all_environments.clear()
    yield
    TinyWorld.all_environments.clear()


def test_committee_run_debate_emits_memo_disagreement_and_collapse(
    tmp_path, monkeypatch, _clear_registries
):
    combined = {**_memo_json("Committee splits on valuation."), **_disagreement_json()}
    sse = _aggregator_sse(combined)

    def _transport_factory(binding, credentials):
        return OpenAIChatAdapter(
            binding,
            credentials,
            base_url="https://api.openai.com/v1",
            credential_ref=None,
            http=_RepeatingTransport(sse),
            sleep=lambda _d: None,
        )

    committee = build_committee(
        Preset(name="moa-e2e", default=BindingSpec(model="openai/gpt-5.2", thinking="high")),
        [
            ("warren_buffett", "Warren Buffett"),
            ("benjamin_graham", "Benjamin Graham"),
            ("charlie_munger", "Charlie Munger"),
        ],
        credentials=StaticCredentialProvider({}),
        transport_factory=_transport_factory,
    )

    def _act_blocks(self, *, return_actions=False, **_kwargs):
        talk = _PERSONA_BLOCKS[self.name]
        cognitive_state = {
            "goals": f"Evaluate AAPL as {self.name}",
            "attention": "Valuation and downside risk",
            "emotions": "Skeptical but engaged",
            "context": ["Investment committee debate"],
        }
        actions = [
            {"type": "THINK", "content": f"{self.name} weighs it.", "target": ""},
            {"type": "TALK", "content": talk, "target": ""},
            {"type": "DONE", "content": "", "target": ""},
        ]
        self._actions_buffer.extend(actions)
        committed = [{"action": a, "cognitive_state": cognitive_state} for a in actions]
        return committed if return_actions else self

    monkeypatch.setattr(InvestorPersona, "act", _act_blocks)
    monkeypatch.setattr(InvestorPersona, "consolidate_episode_memories", lambda _self: False)
    monkeypatch.setattr(TinyPerson, "communication_display", False)

    data_package = DataPackage(
        ticker="AAPL",
        company_name="Apple Inc.",
        description="Consumer technology company.",
        fetched_at=FIXED_NOW,
    )
    log_path = tmp_path / "moa.jsonl"
    with EventLog("aapl-20260713-moa1", path=log_path, clock=lambda: FIXED_NOW) as log:
        result = debate_module.run_debate(
            "AAPL",
            ["warren_buffett", "benjamin_graham", "charlie_munger"],
            data_package=data_package,
            event_log=log,
            committee=committee,
        )

    # read_event_log validates the entire stream (schema + contiguity + terminal).
    events = read_event_log(log_path)
    kinds = [e.type for e in events]
    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_completed"
    assert "debate_error" not in kinds

    # Memo: exactly the five canonical sections, grounded and non-empty.
    memo_sections = [e for e in events if e.type == "memo_section"]
    assert {e.payload["section"] for e in memo_sections} == {
        "executive_summary",
        "investment_thesis",
        "key_risks",
        "valuation_discussion",
        "final_verdict",
    }
    exec_summary = next(
        e for e in memo_sections if e.payload["section"] == "executive_summary"
    )
    assert exec_summary.payload["content"] == "Committee splits on valuation."

    # Disagreement: evidence quotes carried in each side.
    disagreements = [e for e in events if e.type == "disagreement"]
    assert len(disagreements) == 1
    assert all(s["evidence_quote"] for s in disagreements[0].payload["sides"])

    # Collapse metrics: one per assessed persona; only the contrarian caved.
    collapse = [e for e in events if e.type == "collapse_metric"]
    caved = {e.payload["persona"]: e.payload["caved"] for e in collapse}
    assert caved == {
        "Warren Buffett": False,
        "Benjamin Graham": True,
        "Charlie Munger": False,
    }
    graham = next(e for e in collapse if e.payload["persona"] == "Benjamin Graham")
    assert graham.payload["stance_before"] == "bearish"
    assert graham.payload["stance_after"] == "bullish"
    assert graham.payload["phase"] == "verdict"

    # The synthesis routed through the aggregator binding -> a memo usage event.
    assert any(
        e.type == "usage" and e.payload["purpose"] == "memo" for e in events
    )

    # The result object carries the memo + DCR analysis.
    assert result.memo is not None
    assert result.memo.executive_summary.content == "Committee splits on valuation."
    assert result.disagreement_analysis is not None
    summary = result.disagreement_analysis.collapse_summary
    assert summary is not None
    assert summary.caved_personas == ["Benjamin Graham"]
    assert summary.disagreement_collapse_rate == round(1 / 3, 4)
