"""Deterministic generator for a full mocked six-persona committee debate (M4).

This is the M4 counterpart to the M1 golden generator in
``tests/test_event_stream.py``: it drives the **real** debate engine end to end —
moderator, exchange caps, devil's-advocate rotation, structured opening theses
and final verdicts, and the aggregator's MoA memo / disagreement / collapse
synthesis — with every LLM call served offline. Nothing here touches the network.

Unlike the M1 golden (a two-persona *legacy* run that predates the structured
artifacts), this records the **full M4 emission set** — ``thesis_recorded``,
structured ``vote_recorded`` (``source == "structured"``), ``memo_section``,
``disagreement`` and ``collapse_metric`` — so it is the real recording the
interim synthetic log (``tests/support/synthetic_events.py``, which fabricates
the same event shapes *without* running the engine) was always a stand-in for.

The committed fixture ``tests/fixtures/m4_full_debate.jsonl`` is regenerated from
this module and guarded against drift by
``tests/test_m4_dod.py::test_generator_matches_committed_golden``. Regenerate it
whenever debate emission legitimately changes::

    uv run python scripts/regen_m4_golden.py

Determinism: a fixed clock (no wall time), a fixed ``debate_id``, phase-aware
persona prose keyed only on the persona and its turn index, and a single fake
transport that replays one recorded aggregator response. The devil's advocate is
deterministic **only** under a fresh ``TINYIC_STATE_DIR`` (rotation counter 0 →
the first committee member); the regen script and the pytest autouse sandbox
both provide one.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import tinyic.debate as debate_module
from tinyic.data.models import DataPackage
from tinyic.models import (
    BindingSpec,
    Preset,
    StaticCredentialProvider,
    build_committee,
)
from tinyic.models.adapters.openai_chat import OpenAIChatAdapter
from tinyic.personas.base import InvestorPersona
from tinytroupe.agent import TinyPerson

# --------------------------------------------------------------------------- #
# Deterministic constants
# --------------------------------------------------------------------------- #

DEBATE_ID = "aapl-20260713-m4dd"
TICKER = "AAPL"
COMPANY_NAME = "Apple Inc."
FIXED_NOW = datetime(2026, 7, 13, 1, 2, 3, tzinfo=timezone.utc)
AGGREGATOR_MODEL = "openai/gpt-5.2"

GOLDEN_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "m4_full_debate.jsonl"
)

# Stable placeholder for the per-run absolute path in ``debate_completed`` (the
# drift comparison masks this field; a fixed value keeps the committed fixture
# free of a machine-specific path).
RESULT_REF_PLACEHOLDER = "tests/fixtures/m4_full_debate.jsonl"

# Registry order == committee (agents) order == devil's-advocate rotation order.
# Warren Buffett is slot 0, so a fresh-counter debate announces him first and the
# next consecutive debate rotates to Charlie Munger.
PERSONAS: tuple[tuple[str, str], ...] = (
    ("warren_buffett", "Warren Buffett"),
    ("charlie_munger", "Charlie Munger"),
    ("benjamin_graham", "Benjamin Graham"),
    ("peter_lynch", "Peter Lynch"),
    ("howard_marks", "Howard Marks"),
    ("li_lu", "Li Lu"),
)
PERSONA_REGISTRY: list[str] = [name for name, _display in PERSONAS]
PERSONA_DISPLAY: list[str] = [display for _name, display in PERSONAS]

# The four phases run one round each (base protocol), so a persona's Nth ``act``
# call is the Nth phase. The phase-aware prose keys on this index.
PHASE_SEQUENCE: tuple[str, ...] = ("opening", "cross_exam", "rebuttal", "verdict")


# --------------------------------------------------------------------------- #
# Structured block builders (the mandated FR-4.4 trailing blocks)
# --------------------------------------------------------------------------- #

def _thesis_block(*, stance: str, confidence: str, claims: list[str]) -> str:
    lines = ["===THESIS===", f"STANCE: {stance}", f"CONFIDENCE: {confidence}", "CLAIMS:"]
    lines += [f"- {claim}" for claim in claims]
    lines.append("===END===")
    return "\n".join(lines)


def _verdict_block(
    *,
    vote: str,
    confidence: str,
    reasons: list[str],
    risks: list[str],
    changed_mind: str,
) -> str:
    lines = ["===VERDICT===", f"VOTE: {vote}", f"CONFIDENCE: {confidence}", "REASONS:"]
    lines += [f"- {reason}" for reason in reasons]
    lines.append("RISKS:")
    lines += [f"- {risk}" for risk in risks]
    lines.append(f"CHANGED_MIND: {changed_mind}")
    lines.append("===END===")
    return "\n".join(lines)


@dataclass(frozen=True)
class _Script:
    """One persona's phase-aware prose. Opening carries the thesis block, verdict
    the verdict block; cross-exam and rebuttal are free-form NL (FR-4.4)."""

    opening: str
    cross_exam: str
    rebuttal: str
    verdict: str

    def text_for(self, phase: str) -> str:
        return getattr(self, phase)


def _opening(intro: str, *, stance: str, confidence: str, claims: list[str]) -> str:
    return (
        f"{intro}\n"
        + _thesis_block(stance=stance, confidence=confidence, claims=claims)
    )


def _verdict(
    intro: str,
    *,
    vote: str,
    confidence: str,
    reasons: list[str],
    risks: list[str],
    changed_mind: str,
) -> str:
    return (
        f"{intro}\n"
        + _verdict_block(
            vote=vote,
            confidence=confidence,
            reasons=reasons,
            risks=risks,
            changed_mind=changed_mind,
        )
    )


# The scenario (documented so the collapse metric is legible):
#   * Buffett, Munger, Lynch, Li Lu — open bullish, vote BUY: held, not caved.
#   * Benjamin Graham — opens BEARISH (contrarian to the bullish plurality) then
#     votes BUY, and his verdict reason merely echoes his opening claim (no new
#     evidence): the constructed sycophantic cave (collapse_metric.caved == True).
#   * Howard Marks — opens bearish and holds SELL through the verdict: the
#     genuine standing dissenter (not caved), so the committee is never unanimous.
SCRIPTS: dict[str, _Script] = {
    "Warren Buffett": _Script(
        opening=_opening(
            "Apple is a wonderful business at a fair price.",
            stance="bullish",
            confidence="high",
            claims=[
                "Durable ecosystem moat compounds owner earnings",
                "Enormous buybacks shrink the share count",
            ],
        ),
        cross_exam=(
            "Ben, a rich multiple is not the same as a bad business. "
            "The switching costs here are real and widening."
        ),
        rebuttal=(
            "The bears price the moat as if it were rented. "
            "It is owned, and the installed base keeps paying rent to Apple."
        ),
        verdict=_verdict(
            "My judgment is unchanged after the debate.",
            vote="BUY",
            confidence="HIGH",
            reasons=[
                "Durable ecosystem moat compounds owner earnings",
                "Enormous buybacks shrink the share count",
            ],
            risks=["Regulatory pressure on the App Store take rate"],
            changed_mind="no",
        ),
    ),
    "Charlie Munger": _Script(
        opening=_opening(
            "It is a great business run by sensible people.",
            stance="bullish",
            confidence="high",
            claims=[
                "Rational capital allocation over decades",
                "A brand that prices with genuine pricing power",
            ],
        ),
        cross_exam=(
            "Howard, worrying about the macro is how you talk yourself out of "
            "every great compounder ever offered to you."
        ),
        rebuttal=(
            "Invert the bear case: what would have to go wrong is a collapse of "
            "the brand, and I see no evidence of that."
        ),
        verdict=_verdict(
            "I stay with quality.",
            vote="BUY",
            confidence="HIGH",
            reasons=[
                "Rational capital allocation over decades",
                "A brand that prices with genuine pricing power",
            ],
            risks=["Complacency at the top of a long run"],
            changed_mind="no",
        ),
    ),
    # The constructed cave: bearish opening -> BUY verdict, reason echoes the
    # opening claim so the keyword-novelty proxy finds no new evidence.
    "Benjamin Graham": _Script(
        opening=_opening(
            "The numbers demand caution, not enthusiasm.",
            stance="bearish",
            confidence="high",
            claims=[
                "Thirty times earnings leaves no margin of safety",
                "Hardware demand is cyclical and could roll over",
            ],
        ),
        cross_exam=(
            "Warren, I concede the franchise is extraordinary. "
            "My quarrel has always been the price paid, not the asset owned."
        ),
        rebuttal=(
            "Perhaps the committee is right that quality earns a premium. "
            "The weight of opinion here is hard to stand against."
        ),
        verdict=_verdict(
            "Against my instincts, I will move with the committee.",
            vote="BUY",
            confidence="MEDIUM",
            reasons=[
                "Thirty times earnings leaves no margin of safety",
            ],
            risks=["Multiple compression if growth slows"],
            changed_mind="yes",
        ),
    ),
    "Peter Lynch": _Script(
        opening=_opening(
            "Ordinary customers already told us this story at the mall.",
            stance="bullish",
            confidence="medium",
            claims=[
                "Consumers upgrade devices on a dependable cadence",
                "Services revenue keeps growing double digits",
            ],
        ),
        cross_exam=(
            "The pessimists keep waiting for a saturation that the upgrade "
            "cycle refuses to deliver."
        ),
        rebuttal=(
            "I'll buy what I can see working, and I can see the ecosystem "
            "pulling every new customer deeper in."
        ),
        verdict=_verdict(
            "The story is intact.",
            vote="BUY",
            confidence="MEDIUM",
            reasons=[
                "Consumers upgrade devices on a dependable cadence",
                "Services revenue keeps growing double digits",
            ],
            risks=["A stumble in the next hardware cycle"],
            changed_mind="no",
        ),
    ),
    # The standing dissenter: bearish opening, SELL verdict, holds firm.
    "Howard Marks": _Script(
        opening=_opening(
            "Where you buy in the cycle matters more than the asset's quality.",
            stance="bearish",
            confidence="high",
            claims=[
                "Sentiment is euphoric and priced for perfection",
                "The risk-reward is asymmetric against buyers today",
            ],
        ),
        cross_exam=(
            "Charlie, greatness at the wrong price is still a way to lose money. "
            "I am pricing the odds, not doubting the company."
        ),
        rebuttal=(
            "Nothing said today changes the asymmetry. "
            "I would rather be early and safe than late and sorry."
        ),
        verdict=_verdict(
            "The price does not compensate the risk.",
            vote="SELL",
            confidence="HIGH",
            reasons=[
                "Sentiment is euphoric and priced for perfection",
                "The risk-reward is asymmetric against buyers today",
            ],
            risks=["Being early looks wrong until it is suddenly right"],
            changed_mind="no",
        ),
    ),
    "Li Lu": _Script(
        opening=_opening(
            "A durable franchise compounding intrinsic value deserves patience.",
            stance="bullish",
            confidence="medium",
            claims=[
                "A widening competitive advantage over a long horizon",
                "Management that thinks like long-term owners",
            ],
        ),
        cross_exam=(
            "The bears measure a decade of compounding with a single-quarter "
            "yardstick."
        ),
        rebuttal=(
            "Held over the horizon that matters, today's multiple looks like a "
            "footnote, not the thesis."
        ),
        verdict=_verdict(
            "I remain a long-term owner.",
            vote="BUY",
            confidence="MEDIUM",
            reasons=[
                "A widening competitive advantage over a long horizon",
                "Management that thinks like long-term owners",
            ],
            risks=["Time horizon risk if the compounding slows"],
            changed_mind="no",
        ),
    ),
}


# --------------------------------------------------------------------------- #
# The aggregator's canned MoA output (memo + disagreements)
# --------------------------------------------------------------------------- #

def aggregator_payload() -> dict:
    """The combined memo + disagreement JSON the fake aggregator returns.

    Every memo section attributes real committee members and cites at least one
    supporting data point (FR-4.5 grounding); the disagreements carry verbatim
    evidence quotes. ``generate_memo`` reads the five section keys and
    ``extract_disagreements`` reads ``disagreements`` from this one payload.
    """
    def section(content: str, personas: list[str], data: list[str]) -> dict:
        return {
            "content": content,
            "contributing_personas": personas,
            "supporting_data": data,
        }

    return {
        "executive_summary": section(
            "The committee reached a BUY consensus (five to one) on Apple, with "
            "Howard Marks the lone SELL on valuation grounds.",
            ["Warren Buffett", "Howard Marks"],
            ["Vote split 5 BUY / 1 SELL", "P/E near 30x"],
        ),
        "investment_thesis": section(
            "Bulls led by Buffett and Munger argue a durable ecosystem moat "
            "compounds owner earnings and supports the premium multiple.",
            ["Warren Buffett", "Charlie Munger", "Li Lu"],
            ["Durable ecosystem moat", "Long-run compounding of intrinsic value"],
        ),
        "key_risks": section(
            "Graham and Marks flag the absence of a margin of safety at roughly "
            "thirty times earnings and an asymmetric risk-reward at today's price.",
            ["Benjamin Graham", "Howard Marks"],
            ["Thirty times earnings", "Asymmetric risk-reward"],
        ),
        "valuation_discussion": section(
            "The debate turned on price: bulls treat the multiple as justified by "
            "quality, while value voices call it unmargined.",
            ["Benjamin Graham", "Li Lu"],
            ["~30x P/E", "Premium multiple vs. quality"],
        ),
        "final_verdict": section(
            "Consensus BUY, but the dissent is substantive: Marks holds SELL and "
            "Graham conceded to the majority without fresh evidence.",
            ["Warren Buffett", "Peter Lynch", "Howard Marks"],
            ["Consensus BUY", "One standing SELL dissent"],
        ),
        "disagreements": [
            {
                "dimension": "Valuation",
                "description": "Whether ~30x earnings is justified by quality.",
                "sides": [
                    {
                        "persona": "Warren Buffett",
                        "position": "Quality justifies the premium multiple.",
                        "evidence_quote": "a wonderful business at a fair price",
                    },
                    {
                        "persona": "Benjamin Graham",
                        "position": "The multiple leaves no margin of safety.",
                        "evidence_quote": "leaves no margin of safety",
                    },
                ],
                "resolution": "Unresolved; Graham conceded the vote, not the point.",
            },
            {
                "dimension": "Cycle timing",
                "description": "Whether the entry price compensates the risk.",
                "sides": [
                    {
                        "persona": "Howard Marks",
                        "position": "Risk-reward is asymmetric against buyers.",
                        "evidence_quote": "priced for perfection",
                    },
                    {
                        "persona": "Charlie Munger",
                        "position": "Waiting for the macro forfeits great compounders.",
                        "evidence_quote": "talk yourself out of every great compounder",
                    },
                ],
                "resolution": "Held apart; Marks kept his SELL.",
            },
        ],
    }


def aggregator_sse_lines() -> list[str]:
    """One recorded OpenAI-chat streaming response carrying the canned payload."""
    content = json.dumps(aggregator_payload())
    delta = json.dumps(
        {
            "choices": [
                {"index": 0, "delta": {"content": content}, "finish_reason": "stop"}
            ]
        }
    )
    usage = json.dumps(
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 30,
                "total_tokens": 80,
            },
        }
    )
    return f"data: {delta}\n\ndata: {usage}\n\ndata: [DONE]\n".split("\n")


# --------------------------------------------------------------------------- #
# Offline transport + committee
# --------------------------------------------------------------------------- #

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

    def iter_lines(self) -> Iterator[str]:
        yield from self.lines

    def read_text(self) -> str:
        return "\n".join(self.lines)

    def close(self) -> None:
        pass


def fake_response(lines: list[str]) -> _FakeResponse:
    """A recorded HTTP response over ``lines`` (for fault-injecting transports)."""
    return _FakeResponse(200, lines=list(lines))


class _RepeatingTransport:
    """Replays one recorded SSE response on every send (each synthesis window)."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.sent = 0

    def send(self, request):
        self.sent += 1
        return fake_response(self._lines)

    def close(self) -> None:
        pass


def default_transport_factory(binding, credentials):
    """Map any binding to an OpenAI-chat adapter over the canned aggregator SSE.

    Only the aggregator actually sends (persona turns are mocked at ``act``), so
    a single repeating response is sufficient for the whole debate.
    """
    return OpenAIChatAdapter(
        binding,
        credentials,
        base_url="https://api.openai.com/v1",
        credential_ref=None,
        http=_RepeatingTransport(aggregator_sse_lines()),
        sleep=lambda _delay: None,
    )


def make_committee(*, transport_factory=default_transport_factory):
    """Build the six-persona committee on one model over an offline transport."""
    return build_committee(
        Preset(
            name="m4-dod",
            default=BindingSpec(model=AGGREGATOR_MODEL, thinking="high"),
        ),
        list(PERSONAS),
        credentials=StaticCredentialProvider({}),
        transport_factory=transport_factory,
    )


def data_package() -> DataPackage:
    return DataPackage(
        ticker=TICKER,
        company_name=COMPANY_NAME,
        description="Consumer technology company with a large services franchise.",
        fetched_at=FIXED_NOW,
    )


# --------------------------------------------------------------------------- #
# The phase-aware persona act
# --------------------------------------------------------------------------- #

def make_act(calls: dict[str, int] | None = None):
    """Return an ``InvestorPersona.act`` replacement driven by phase-aware prose.

    A persona's Nth call is its Nth phase (one round per phase), so the mock
    emits the opening thesis, then free-form cross-exam and rebuttal, then the
    final verdict — no network, no binding send. ``calls`` (per persona turn
    index) is created fresh per invocation unless supplied.
    """
    counts: dict[str, int] = {} if calls is None else calls

    def act(self, *, return_actions=False, **_kwargs):
        index = counts.get(self.name, 0)
        counts[self.name] = index + 1
        phase = PHASE_SEQUENCE[min(index, len(PHASE_SEQUENCE) - 1)]
        talk = SCRIPTS[self.name].text_for(phase)
        cognitive_state = {
            "goals": f"Reach a defensible verdict on {TICKER} as {self.name}",
            "attention": f"The {phase.replace('_', '-')} arguments on the table",
            "emotions": "Engaged and skeptical",
            "context": ["Investment committee debate", f"Phase: {phase}"],
        }
        actions = [
            {
                "type": "THINK",
                "content": f"{self.name} weighs the {phase.replace('_', '-')} case.",
                "target": "",
            },
            {"type": "TALK", "content": talk, "target": ""},
            {"type": "DONE", "content": "", "target": ""},
        ]
        self._actions_buffer.extend(actions)
        committed = [
            {"action": action, "cognitive_state": cognitive_state}
            for action in actions
        ]
        return committed if return_actions else self

    return act


@contextlib.contextmanager
def patched_personas() -> Iterator[None]:
    """Patch persona acting/consolidation/display for a deterministic offline run."""
    with (
        patch.object(InvestorPersona, "act", make_act()),
        patch.object(
            InvestorPersona, "consolidate_episode_memories", lambda _self: False
        ),
        patch.object(TinyPerson, "communication_display", False),
    ):
        yield


def record_debate(event_log, *, da: str | None = None, committee=None):
    """Run the full mocked six-persona debate into ``event_log``.

    Builds the offline committee (unless one is supplied — e.g. a fault-injecting
    variant), patches persona acting, and drives the real ``run_debate`` so the
    engine emits the complete M4 event set. Returns the ``DebateResult``.
    """
    resolved_committee = committee if committee is not None else make_committee()
    with patched_personas():
        return debate_module.run_debate(
            TICKER,
            list(PERSONA_REGISTRY),
            data_package=data_package(),
            event_log=event_log,
            committee=resolved_committee,
            da=da,
        )


__all__ = [
    "AGGREGATOR_MODEL",
    "COMPANY_NAME",
    "DEBATE_ID",
    "FIXED_NOW",
    "GOLDEN_PATH",
    "PERSONAS",
    "PERSONA_DISPLAY",
    "PERSONA_REGISTRY",
    "PHASE_SEQUENCE",
    "RESULT_REF_PLACEHOLDER",
    "SCRIPTS",
    "TICKER",
    "aggregator_payload",
    "aggregator_sse_lines",
    "data_package",
    "default_transport_factory",
    "fake_response",
    "make_act",
    "make_committee",
    "patched_personas",
    "record_debate",
]
