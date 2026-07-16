"""Deterministic generator for a schema-valid TinyIC debate event log.

INTERIM CONTRACT SCAFFOLDING. This module fabricates a full, schema-valid v1
event log (per ``docs/event-schema.md``) *without running the engine*, so that
the TUI, headless renderer, and HTML exporter can be built and tested against
the frozen contract before M1 exists. It is explicitly a stand-in: **once M1
records a real golden log from a mocked debate, renderer tests should migrate to
that recording** and this generator's role narrows to a schema-conformance
fixture / fuzz seed.

Everything here is deterministic — fixed personas, fixed fake timestamps
(no wall clock), monotonic ``seq`` with no gaps, no randomness — so the committed
fixture ``tests/fixtures/synthetic_debate.jsonl`` reproduces byte-for-byte. Run
this module as a script to (re)write that fixture::

    uv run python -m tests.support.synthetic_events
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Deterministic constants (no wall clock, no randomness)
# --------------------------------------------------------------------------- #

DEBATE_ID = "aapl-20260712-t35t"
TICKER = "AAPL"
COMPANY_NAME = "Apple Inc."
PRESET = "default"
TINYIC_VERSION = "2.0.0.dev0"
CONFIG_HASH = "cfg-" + hashlib.sha256(b"tinyic-synthetic-preset-v1").hexdigest()[:12]

_BASE_TS = datetime(2026, 7, 12, 20, 30, 0, tzinfo=timezone.utc)
_TS_STEP = timedelta(milliseconds=500)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "synthetic_debate.jsonl"
)

# Six personas, each with a distinct model_ref / auth_profile / thinking_level /
# temperament — exercising the heterogeneous-committee shape of debate_started.
Persona = dict[str, str]
PERSONAS: tuple[Persona, ...] = (
    {
        "name": "Warren Buffett",
        "model_ref": "anthropic/claude-opus-4-8",
        "auth_profile": "anthropic:claude-max",
        "thinking_level": "high",
        "temperament": "balanced",
    },
    {
        "name": "Charlie Munger",
        "model_ref": "openai/gpt-5.6-sol",
        "auth_profile": "openai:key",
        "thinking_level": "high",
        "temperament": "contrarian",
    },
    {
        "name": "Benjamin Graham",
        "model_ref": "openai/gpt-5.6-terra",
        "auth_profile": "openai:key",
        "thinking_level": "medium",
        "temperament": "conciliatory",
    },
    {
        "name": "Peter Lynch",
        "model_ref": "google/gemini-3.5-flash",
        "auth_profile": "google:key",
        "thinking_level": "medium",
        "temperament": "balanced",
    },
    {
        "name": "Howard Marks",
        "model_ref": "anthropic/claude-opus-4-8",
        "auth_profile": "anthropic:claude-max",
        "thinking_level": "high",
        "temperament": "contrarian",
    },
    {
        "name": "Li Lu",
        "model_ref": "kimi/kimi-k2.6",
        "auth_profile": "kimi:key",
        "thinking_level": "high",
        "temperament": "balanced",
    },
)

_MODEL_BY_PERSONA = {p["name"]: p["model_ref"] for p in PERSONAS}

MODERATOR = {
    "name": "Moderator",
    "model_ref": "openai/gpt-5.6-luna",
    "auth_profile": "openai:key",
    "thinking_level": "low",
}
AGGREGATOR = {
    "name": "Aggregator",
    "model_ref": "anthropic/claude-opus-4-8",
    "auth_profile": "anthropic:claude-max",
    "thinking_level": "high",
}
CAPS = {"opening": 1, "cross_exam_exchanges": 2, "rebuttal": 1, "verdict": 1}
DA_PERSONA = "Howard Marks"

# Short, on-topic, deterministic speech snippets keyed by persona.
_ANGLE = {
    "Warren Buffett": "the durable ecosystem moat and Services recurring revenue",
    "Charlie Munger": "whether the price already discounts the franchise quality",
    "Benjamin Graham": "the thin margin of safety at ~30x trailing earnings",
    "Peter Lynch": "the upgrade cycle and what the products tell ordinary buyers",
    "Howard Marks": "where we sit in the sentiment cycle for mega-cap tech",
    "Li Lu": "the long-run owner economics against the China supply exposure",
}

# Deterministic per-persona verdict votes -> BUY:3, SELL:1, HOLD:2.
_VOTES = {
    "Warren Buffett": ("BUY", "HIGH", False),
    "Charlie Munger": ("BUY", "MEDIUM", False),
    "Benjamin Graham": ("SELL", "HIGH", False),
    "Peter Lynch": ("HOLD", "MEDIUM", True),
    "Howard Marks": ("HOLD", "HIGH", False),
    "Li Lu": ("BUY", "MEDIUM", False),
}

_STANCE = {
    "Warren Buffett": "bullish",
    "Charlie Munger": "bullish",
    "Benjamin Graham": "bearish",
    "Peter Lynch": "neutral",
    "Howard Marks": "neutral",
    "Li Lu": "bullish",
}

_MEMO_SECTIONS = (
    (
        "executive_summary",
        "The committee splits three ways on Apple: three BUY, two HOLD, one SELL, "
        "with no unanimous consensus but a BUY plurality driven by ecosystem economics.",
    ),
    (
        "investment_thesis",
        "Bulls anchor on switching costs, buyback-driven per-share growth, and an "
        "$85B high-margin Services annuity that reframes Apple as a recurring-revenue business.",
    ),
    (
        "key_risks",
        "Valuation offers little margin of safety at ~30x earnings; China supply "
        "concentration and App Store antitrust are the most-cited asymmetric downside catalysts.",
    ),
    (
        "valuation_discussion",
        "At ~30x trailing and ~45x book the price embeds optimistic growth; the "
        "bear case needs only multiple compression, not a business break, to hurt.",
    ),
    (
        "final_verdict",
        "Net: a wonderful business at a full price. The committee's BUY plurality is "
        "conviction-weighted but the dissent on price is substantive, not sycophantic.",
    ),
)


# Deterministic character-slice width for the synthetic ``*_delta`` streams. The
# only property that matters is that the fragments concatenate back to exactly
# the ``*_completed`` full_text (see ``_chunks``), so a delta log and its
# completed-only twin fold to identical final render state.
DELTA_CHUNK_CHARS = 24


def _fmt_ts(moment: datetime) -> str:
    """UTC ISO-8601 with millisecond precision and a trailing ``Z``."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _chunks(text: str, size: int = DELTA_CHUNK_CHARS) -> list[str]:
    """Slice ``text`` into fixed-width fragments that join back to exactly ``text``.

    Deterministic and reversible: ``"".join(_chunks(s)) == s`` for any ``s``, and
    empty text yields no fragments. Used to fabricate ``think_delta`` /
    ``talk_delta`` streams whose pieces concatenate to the ``*_completed``
    full_text — the invariant the delta/completed-consistency pilots rely on.
    """
    return [text[i : i + size] for i in range(0, len(text), size)]


class _Log:
    """Accumulates envelope dicts with monotonic seq and clock-free timestamps."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._turn = 0

    def emit(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        event = {
            "v": 1,
            "seq": self._seq,
            "ts": _fmt_ts(_BASE_TS + (self._seq - 1) * _TS_STEP),
            "debate_id": DEBATE_ID,
            "type": event_type,
            "payload": payload,
        }
        self.events.append(event)
        return event

    def next_turn_id(self) -> str:
        self._turn += 1
        return f"t{self._turn:03d}"

    def peek_next_turn_id(self) -> str:
        """The id the *next* :meth:`next_turn_id` call will return (no advance)."""
        return f"t{self._turn + 1:03d}"


def _usage(log: _Log, *, turn_id: str | None, persona: str | None, purpose: str,
           model_ref: str, cin: int, cout: int, cached: int, cost: float | None) -> None:
    payload: dict[str, Any] = {
        "purpose": purpose,
        "model_ref": model_ref,
        "input_tokens": cin,
        "output_tokens": cout,
        "cached_tokens": cached,
        "cost_usd": cost,
    }
    if turn_id is not None:
        payload["turn_id"] = turn_id
    if persona is not None:
        payload["persona"] = persona
    log.emit("usage", payload)


def _turn(
    log: _Log,
    *,
    persona: str,
    phase: str,
    role: str,
    talk: str,
    think: str,
    target_persona: str | None = None,
    goals: list[str],
    attention: str,
    emotions: str,
    stream: bool = False,
) -> str:
    """Emit one committed turn: started -> think -> talk -> cognitive_state ->
    completed -> usage. Returns the turn_id (used for steering delivery refs).

    With ``stream=True`` the turn additionally emits ``think_delta`` fragments
    before its ``think_completed`` and ``talk_delta`` fragments before its
    ``talk_completed`` (turn-scoped, ordered exactly as the M2 engine streams
    them). The fragments concatenate to the matching ``full_text`` — the
    delta/completed-consistency contract the live-think pilots exercise — so a
    streamed turn folds to the identical final state as its completed-only twin.
    """
    turn_id = log.next_turn_id()
    started: dict[str, Any] = {
        "turn_id": turn_id,
        "persona": persona,
        "phase": phase,
        "role": role,
    }
    if target_persona is not None:
        started["target_persona"] = target_persona
    log.emit("turn_started", started)
    if stream:
        for fragment in _chunks(think):
            log.emit("think_delta", {"turn_id": turn_id, "text": fragment})
    log.emit("think_completed", {"turn_id": turn_id, "full_text": think})
    if stream:
        for fragment in _chunks(talk):
            log.emit("talk_delta", {"turn_id": turn_id, "text": fragment})
    log.emit("talk_completed", {"turn_id": turn_id, "full_text": talk})
    log.emit(
        "cognitive_state",
        {
            "turn_id": turn_id,
            "persona": persona,
            "goals": goals,
            "attention": attention,
            "emotions": emotions,
            "context": f"{phase} phase",
        },
    )
    log.emit(
        "turn_completed",
        {
            "turn_id": turn_id,
            "persona": persona,
            "phase": phase,
            "interrupted": False,
            "usage_ref": turn_id,
        },
    )
    model_ref = _MODEL_BY_PERSONA[persona]
    _usage(
        log,
        turn_id=turn_id,
        persona=persona,
        purpose="turn",
        model_ref=model_ref,
        cin=1200,
        cout=320,
        cached=256,
        cost=None if model_ref.startswith("anthropic/") else 0.0125,
    )
    return turn_id


def build_events(*, stream: bool = False) -> list[dict[str, Any]]:
    """Return the full ordered list of envelope dicts for the synthetic debate.

    ``stream=False`` (the default) is the byte-stable, completed-only log the
    committed fixture and every existing renderer test consume. ``stream=True``
    interleaves ``think_delta`` / ``talk_delta`` fragments into each turn (see
    :func:`_turn`) to exercise the live-think path; it folds to the identical
    final render state as the default (:func:`build_delta_events`).
    """
    log = _Log()

    # --- Lifecycle: debate_started -------------------------------------- #
    log.emit(
        "debate_started",
        {
            "ticker": TICKER,
            "company_name": COMPANY_NAME,
            "preset": PRESET,
            "personas": [dict(p) for p in PERSONAS],
            "moderator": dict(MODERATOR),
            "aggregator": dict(AGGREGATOR),
            "caps": dict(CAPS),
            "config_hash": CONFIG_HASH,
            "tinyic_version": TINYIC_VERSION,
        },
    )

    # --- data_ready (per-source availability badges) -------------------- #
    log.emit(
        "data_ready",
        {
            "sources": [
                {"name": "yfinance_fundamentals", "status": "ok"},
                {"name": "sec_edgar", "status": "ok"},
                {
                    "name": "yfinance_news",
                    "status": "degraded",
                    "warning": "only 4 of 12 headlines resolved",
                },
                {
                    "name": "xai_social",
                    "status": "disabled_no_credential",
                    "warning": "XAI_API_KEY not set — social sentiment skipped",
                },
                {
                    "name": "deep_research",
                    "status": "unavailable",
                    "warning": "web search timed out after 30s",
                },
            ],
            "financials_summary": (
                "AAPL — P/E 30.1x, FCF $99B TTM, Services revenue $85B (+11% YoY), "
                "net cash $55B, buyback reduced shares ~3%/yr."
            ),
            "description": (
                "Apple Inc. designs and sells consumer electronics (iPhone, Mac, "
                "iPad, Watch), wearables, and a fast-growing Services segment."
            ),
            "fetched_at": _fmt_ts(_BASE_TS),
        },
    )
    _usage(
        log,
        turn_id=None,
        persona=None,
        purpose="research",
        model_ref="openai/gpt-5.6",
        cin=4200,
        cout=680,
        cached=0,
        cost=0.031,
    )

    # --- Subscription usage window snapshot ----------------------------- #
    log.emit(
        "usage_window",
        {
            "auth_profile": "anthropic:claude-max",
            "lane": "subscription",
            "window_used_msgs": 18,
            "window_estimate_msgs": 110,
            "resets_at": _fmt_ts(_BASE_TS + timedelta(hours=5)),
        },
    )

    # --- Phase: OPENING ------------------------------------------------- #
    log.emit("phase_started", {"phase": "opening", "index": 0})
    for persona in (p["name"] for p in PERSONAS):
        angle = _ANGLE[persona]
        _turn(
            log,
            persona=persona,
            phase="opening",
            role="statement",
            talk=f"My opening read on Apple centers on {angle}. On balance that "
            f"shapes where I think the risk and reward actually sit.",
            think=f"({persona} privately weighs {angle} before committing to a stance.)",
            goals=[f"State an evidence-based opening view on {TICKER}"],
            attention=angle,
            emotions="Composed and deliberate, laying out first principles.",
            stream=stream,
        )
        stance = _STANCE[persona]
        log.emit(
            "thesis_recorded",
            {
                "persona": persona,
                "phase": "opening",
                "stance": stance,
                "claims": [
                    f"{persona}'s core claim keys on {angle}.",
                    f"Stance is {stance} pending cross-examination.",
                ],
                "confidence": "medium",
            },
        )
    log.emit("phase_completed", {"phase": "opening", "index": 0, "turn_count": 6})

    # --- Phase: CROSS_EXAM (with mid-phase steer) ----------------------- #
    log.emit(
        "phase_started",
        {"phase": "cross_exam", "index": 1, "da_persona": DA_PERSONA},
    )
    # Exchange 1: Howard Marks (DA) challenges Warren Buffett; Buffett responds.
    _turn(
        log,
        persona="Howard Marks",
        phase="cross_exam",
        role="challenge",
        target_persona="Warren Buffett",
        talk="Warren, isn't your moat thesis already the consensus, and therefore "
        "already in the price? Where is the margin of error if sentiment turns?",
        think="(Marks presses on crowded positioning and asymmetric downside.)",
        goals=["Expose unpriced downside in the bull case"],
        attention="crowded positioning risk",
        emotions="Skeptical and probing, hunting for complacency.",
        stream=stream,
    )
    _turn(
        log,
        persona="Warren Buffett",
        phase="cross_exam",
        role="response",
        target_persona="Howard Marks",
        talk="Consensus or not, owner earnings keep compounding and the buyback "
        "shrinks the share count. I am buying the business, not the mood.",
        think="(Buffett reframes from sentiment back to per-share economics.)",
        goals=["Defend the durable-earnings thesis"],
        attention="owner earnings and buybacks",
        emotions="Calm and unbothered by the crowd framing.",
        stream=stream,
    )

    # Mid-phase steer: delivered at the next speaker-turn boundary (steer mode).
    log.emit(
        "steering_submitted",
        {
            "msg_id": "m01",
            "mode": "steer",
            "target_persona": "Warren Buffett",
            "text": "Press Buffett specifically on China supply-chain concentration.",
            "source": "tui",
        },
    )
    log.emit(
        "steering_delivered",
        {"msg_id": "m01", "delivered_before_turn_id": log.peek_next_turn_id()},
    )
    # Exchange 2: Charlie Munger challenges Warren Buffett (carrying the steer).
    _turn(
        log,
        persona="Charlie Munger",
        phase="cross_exam",
        role="challenge",
        target_persona="Warren Buffett",
        talk="Following the moderator's steer: Warren, how much of this franchise "
        "rests on Chinese assembly you don't control? Concentrate that risk for us.",
        think="(Munger sharpens the China supply-chain concentration point.)",
        goals=["Force a concrete answer on supply concentration"],
        attention="China assembly dependency",
        emotions="Blunt and impatient with hand-waving.",
        stream=stream,
    )
    _turn(
        log,
        persona="Warren Buffett",
        phase="cross_exam",
        role="response",
        target_persona="Charlie Munger",
        talk="It's a real concentration and I won't pretend otherwise; management is "
        "diversifying assembly, but the timeline is the honest risk to underwrite.",
        think="(Buffett concedes the concentration while bounding it.)",
        goals=["Acknowledge the risk without abandoning the thesis"],
        attention="assembly diversification timeline",
        emotions="Candid, giving ground where the fact demands it.",
        stream=stream,
    )
    log.emit("phase_completed", {"phase": "cross_exam", "index": 1, "turn_count": 4})

    # --- Queue-mode steer submitted at the phase boundary --------------- #
    log.emit(
        "steering_submitted",
        {
            "msg_id": "m02",
            "mode": "queue",
            "text": "In rebuttals, everyone tie your view to a concrete valuation multiple.",
            "source": "stdin",
        },
    )

    # --- Phase: REBUTTAL (queue steer delivered at this phase boundary) - #
    log.emit("phase_started", {"phase": "rebuttal", "index": 2})
    log.emit(
        "steering_delivered",
        {"msg_id": "m02", "delivered_before_turn_id": log.peek_next_turn_id()},
    )
    for persona in (p["name"] for p in PERSONAS):
        angle = _ANGLE[persona]
        _turn(
            log,
            persona=persona,
            phase="rebuttal",
            role="rebuttal",
            talk=f"On rebuttal, and pinning it to a multiple: {angle} is why I hold my "
            f"line on where Apple's price-to-earnings should sit.",
            think=f"({persona} answers the queued instruction to cite a multiple.)",
            goals=["Rebut with an explicit valuation anchor"],
            attention="valuation multiple vs. history",
            emotions="Firm, sharpening rather than softening the view.",
            stream=stream,
        )
    # Collapse metrics: one persona caves under the cross-exam pressure, one holds.
    log.emit(
        "collapse_metric",
        {
            "persona": "Peter Lynch",
            "phase": "rebuttal",
            "stance_before": "neutral",
            "stance_after": "cautious-hold",
            "caved": True,
            "note": "Softened toward the moderated consensus after the China exchange.",
        },
    )
    log.emit(
        "collapse_metric",
        {
            "persona": "Howard Marks",
            "phase": "rebuttal",
            "stance_before": "neutral",
            "stance_after": "neutral",
            "caved": False,
            "note": "Held the skeptical line; did not converge under pressure.",
        },
    )
    log.emit("phase_completed", {"phase": "rebuttal", "index": 2, "turn_count": 6})

    # --- Phase: VERDICT (verdict turn -> vote_recorded per persona) ----- #
    log.emit("phase_started", {"phase": "verdict", "index": 3})
    for persona in (p["name"] for p in PERSONAS):
        vote, confidence, changed = _VOTES[persona]
        _turn(
            log,
            persona=persona,
            phase="verdict",
            role="verdict",
            talk=f"My final call on Apple is {vote} at {confidence} confidence, "
            f"grounded in {_ANGLE[persona]}.",
            think=f"({persona} finalizes a {vote} with {confidence} confidence.)",
            goals=["Cast a final, reasoned vote"],
            attention="final decision",
            emotions="Resolved, ready to commit to a verdict.",
            stream=stream,
        )
        log.emit(
            "vote_recorded",
            {
                "persona": persona,
                "vote": vote,
                "confidence": confidence,
                "reasoning": [
                    f"Anchored on {_ANGLE[persona]}.",
                    "Weighed the China supply-chain risk raised in cross-exam.",
                ],
                "key_risks": [
                    "Valuation leaves little margin of safety.",
                    "China assembly concentration.",
                ],
                "changed_mind": changed,
                "source": "structured",
            },
        )
    log.emit("phase_completed", {"phase": "verdict", "index": 3, "turn_count": 6})

    # --- Scorecard ------------------------------------------------------ #
    votes_summary = [
        {"persona": name, "vote": v[0], "confidence": v[1]}
        for name, v in _VOTES.items()
    ]
    bull = sum(1 for v in _VOTES.values() if v[0] == "BUY")
    bear = sum(1 for v in _VOTES.values() if v[0] == "SELL")
    hold = sum(1 for v in _VOTES.values() if v[0] == "HOLD")
    log.emit(
        "scorecard",
        {
            "votes": votes_summary,
            "consensus": "BUY",
            "bull_count": bull,
            "bear_count": bear,
            "hold_count": hold,
        },
    )
    _usage(
        log,
        turn_id=None,
        persona=None,
        purpose="extraction",
        model_ref="openai/gpt-5.6-mini",
        cin=5600,
        cout=900,
        cached=1024,
        cost=0.018,
    )

    # --- Disagreement analysis ------------------------------------------ #
    log.emit(
        "disagreement",
        {
            "dimension": "Valuation vs. quality",
            "description": "Whether Apple's business quality justifies paying ~30x earnings.",
            "sides": [
                {
                    "persona": "Warren Buffett",
                    "position": "Quality justifies the price for a permanent holder.",
                    "evidence_quote": "I am buying the business, not the mood.",
                },
                {
                    "persona": "Benjamin Graham",
                    "position": "No margin of safety at this multiple.",
                    "evidence_quote": "the thin margin of safety at ~30x trailing earnings",
                },
            ],
            "resolution": "Unresolved — a genuine, evidence-backed split, not convergence.",
        },
    )

    # --- Memo sections (MoA aggregation) -------------------------------- #
    for section, content in _MEMO_SECTIONS:
        log.emit(
            "memo_section",
            {
                "section": section,
                "content": content,
                "contributing_personas": [p["name"] for p in PERSONAS],
                "supporting_data": ["yfinance_fundamentals", "sec_edgar"],
            },
        )
    _usage(
        log,
        turn_id=None,
        persona=None,
        purpose="memo",
        model_ref="anthropic/claude-opus-4-8",
        cin=8800,
        cout=1500,
        cached=2048,
        cost=None,
    )

    # --- debate_completed ----------------------------------------------- #
    log.emit(
        "debate_completed",
        {
            "phases_completed": ["opening", "cross_exam", "rebuttal", "verdict"],
            "duration_s": 372.5,
            "result_ref": f"~/.tinyic/runs/{DEBATE_ID}.result.json",
        },
    )

    return log.events


def build_delta_events() -> list[dict[str, Any]]:
    """The synthetic debate with per-turn ``think_delta`` / ``talk_delta`` streams.

    The *delta twin* of :func:`build_events`: same personas, phases, votes, and
    artifacts, but every turn additionally streams its private reasoning and
    speech in fragments before the matching ``*_completed`` event. Because the
    fragments concatenate to the same ``full_text`` and ``*_completed`` overwrites
    the accumulated deltas, this folds to the identical final render state as the
    completed-only :func:`build_events` — the property the live-think pilots
    assert (delta/completed consistency), while additionally driving the FR-5.1
    live-think highlight block that a completed-only log never triggers.
    """
    return build_events(stream=True)


def to_jsonl(events: list[dict[str, Any]]) -> str:
    """Serialize events to newline-terminated JSONL (deterministic key order)."""
    return "".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        for event in events
    )


def write_fixture(path: Path | str = FIXTURE_PATH) -> Path:
    """(Re)write the committed synthetic fixture and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_jsonl(build_events()), encoding="utf-8")
    return target


if __name__ == "__main__":  # pragma: no cover - script entry
    written = write_fixture()
    print(f"wrote {written} ({len(build_events())} events)")
