"""M4 Definition-of-Done acceptance suite (PRD §12 M4 row).

This is the milestone gate for debate hardening. It proves, entirely offline (no
network, no LLM — persona turns are mocked and the aggregator is served a
recorded response), the properties the M4 row demands of a *recorded full
debate*: "meets protocol acceptance checks; memo grounded on full transcript".

The subject under test is a full mocked **six-persona committee debate** driven
through the real engine — moderator, exchange caps, devil's-advocate rotation,
structured opening theses / final verdicts, and the aggregator's MoA memo /
disagreement / collapse synthesis. The generator (persona prose, scenario,
committee wiring) is single-sourced from :mod:`tests.support.m4_debate`, the same
module the committed golden fixture (``tests/fixtures/m4_full_debate.jsonl``) is
regenerated from, so the acceptance run and the golden can never drift.

Coverage maps to the DoD sub-checks:

* (a) **Protocol acceptance** — per-phase/per-persona caps are respected, the
  devil's advocate is announced and *rotates* across two consecutive debates on
  the persisted counter, the moderator speaks only procedure (never a debating
  turn or a vote), and the committee carries a hard dissenter plus a standing
  dissent.
* (b) **Structured artifacts** — ``thesis_recorded`` for every persona in the
  opening; well-formed verdicts yield ``vote_recorded.source == "structured"``.
* (c) **Grounded memo** — every ``memo_section`` attributes real personas and
  cites supporting data, and the draft survives an injected mid-window failure.
* (d) **Collapse metric** — a ``collapse_metric`` is emitted for a constructed
  sycophantic-cave scenario.
* (e) **Golden fixture** — the committed log is schema-valid, replayable, and
  renderer-ready, and the generator reproduces it byte-for-byte.

The live-debate half of the M4 DoD (a recorded *live* run meeting the same
protocol checks) is executed by the lead outside this offline workflow.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

from tinyic.debate.moderator import DEFAULT_EXCHANGE_CAPS
from tinyic.events import EventLog, read_event_log
from tinyic.models.adapters.openai_chat import OpenAIChatAdapter
from tinyic.tui.events import (
    invalid_enum_fields,
    is_replayable,
    missing_required_fields,
    read_events,
)

from tests.support import m4_debate as gen

COMMITTEE = set(gen.PERSONA_DISPLAY)
CANONICAL_MEMO_SECTIONS = {
    "executive_summary",
    "investment_thesis",
    "key_risks",
    "valuation_discussion",
    "final_verdict",
}
PHASES_IN_ORDER = ["opening", "cross_exam", "rebuttal", "verdict"]


def _of_type(events, event_type: str):
    return [event for event in events if event.type == event_type]


def _one(events, event_type: str):
    matches = _of_type(events, event_type)
    assert len(matches) == 1, f"expected exactly one {event_type}, got {len(matches)}"
    return matches[0]


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    """Record the full mocked six-persona debate once for the read-only checks.

    Runs under its own isolated per-install state so the devil's-advocate
    rotation counter starts at 0 (deterministic DA = the first committee member),
    exactly as the golden regeneration does; the env is restored afterwards so the
    per-test conftest sandbox is unaffected.
    """
    state = tmp_path_factory.mktemp("m4dod-state")
    runs = tmp_path_factory.mktemp("m4dod-runs")
    log_path = tmp_path_factory.mktemp("m4dod-log") / f"{gen.DEBATE_ID}.jsonl"
    previous = {
        key: os.environ.get(key)
        for key in ("TINYIC_STATE_DIR", "TINYIC_RUNS_DIR")
    }
    os.environ["TINYIC_STATE_DIR"] = str(state)
    os.environ["TINYIC_RUNS_DIR"] = str(runs)
    try:
        with EventLog(
            gen.DEBATE_ID, path=log_path, clock=lambda: gen.FIXED_NOW
        ) as event_log:
            result = gen.record_debate(event_log)
        events = read_event_log(log_path)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return events, result


# ==========================================================================
# (a) Protocol acceptance checks
# ==========================================================================


def test_a_exchange_caps_are_respected_per_phase_and_persona(recorded):
    events, _result = recorded
    turns = _of_type(events, "turn_started")

    # Every debating turn belongs to a committee persona in a canonical phase.
    assert {turn.payload["persona"] for turn in turns} == COMMITTEE
    assert {turn.payload["phase"] for turn in turns} == set(PHASES_IN_ORDER)

    # No persona exceeds the declared per-phase exchange ceiling (FR-4.2). The
    # base protocol runs one round per phase, so each persona speaks once per
    # phase — well within every cap (cross-exam's ceiling is 2).
    per_phase_persona = Counter(
        (turn.payload["phase"], turn.payload["persona"]) for turn in turns
    )
    for (phase, _persona), count in per_phase_persona.items():
        assert count <= DEFAULT_EXCHANGE_CAPS[phase], (
            f"{phase} ran {count} exchanges, above cap {DEFAULT_EXCHANGE_CAPS[phase]}"
        )
        assert count == 1

    # phase_completed.turn_count agrees: six personas per phase, 24 turns total.
    for completed in _of_type(events, "phase_completed"):
        assert completed.payload["turn_count"] == len(COMMITTEE)
    assert len(turns) == len(COMMITTEE) * len(PHASES_IN_ORDER)


def test_a_devils_advocate_is_announced_and_rotates_across_two_debates(
    tmp_path,
):
    """Two back-to-back debates rotate the DA on the persisted counter (B8 fix).

    The autouse conftest sandbox gives this test a fresh ``TINYIC_STATE_DIR`` (a
    rotation counter starting at 0), so the first debate announces the first
    committee member and the second — reading the counter the first advanced —
    announces the next. A fresh orchestrator no longer resets the rotation.
    """
    from tinyic import state

    assert state.read_counter("da_rotation") == 0

    das: list[str] = []
    for index in range(2):
        log_path = tmp_path / f"debate-{index}.jsonl"
        with EventLog(
            f"aapl-2026071{index}-rot{index}",
            path=log_path,
            clock=lambda: gen.FIXED_NOW,
        ) as event_log:
            gen.record_debate(event_log)
        events = read_event_log(log_path)
        cross_exam = _one(
            [e for e in _of_type(events, "phase_started")
             if e.payload["phase"] == "cross_exam"],
            "phase_started",
        )
        # Announced as procedure on the cross-exam banner, never as a turn.
        da = cross_exam.payload["da_persona"]
        assert da in COMMITTEE
        das.append(da)

    # It actually rotated: two consecutive debates picked two different members,
    # in registry (committee) order, and the counter advanced twice.
    assert das[0] == gen.PERSONA_DISPLAY[0]  # slot 0
    assert das[1] == gen.PERSONA_DISPLAY[1]  # slot 1
    assert das[0] != das[1]
    assert state.read_counter("da_rotation") == 2


def test_a_moderator_speaks_only_procedure_and_never_votes(recorded):
    events, _result = recorded

    started = _one(events, "debate_started").payload
    # The moderator is a rules-only system component, not a seventh voice.
    assert started["moderator"] == "rules"
    assert {persona["name"] for persona in started["personas"]} == COMMITTEE
    assert len(started["personas"]) == len(COMMITTEE)

    # It never takes a debating turn and never casts a vote: every turn and every
    # recorded vote belongs to a committee persona.
    assert {t.payload["persona"] for t in _of_type(events, "turn_started")} <= COMMITTEE
    assert {
        v.payload["persona"] for v in _of_type(events, "vote_recorded")
    } == COMMITTEE

    # Its only "speech" is procedure carried by schema events (the phase banners
    # and the cross-exam DA announcement) — no persona turn is attributed to it.
    cross_exam = next(
        e for e in _of_type(events, "phase_started")
        if e.payload["phase"] == "cross_exam"
    )
    assert cross_exam.payload["da_persona"] in COMMITTEE


def test_a_committee_carries_a_hard_dissenter_and_a_standing_dissent(recorded):
    events, _result = recorded
    started = _one(events, "debate_started").payload

    # FR-4.3 composition: at least one hard-dissenter temperament is present.
    temperaments = {p["name"]: p["temperament"] for p in started["personas"]}
    assert any(t == "contrarian" for t in temperaments.values()), temperaments

    # And the debate ends with a genuine standing dissent (not merely unanimous):
    # at least one SELL that did not cave under pressure.
    scorecard = _one(events, "scorecard").payload
    assert scorecard["bear_count"] >= 1

    collapse = {c.payload["persona"]: c.payload for c in _of_type(events, "collapse_metric")}
    sell_voters = [
        v.payload["persona"]
        for v in _of_type(events, "vote_recorded")
        if v.payload["vote"] == "SELL"
    ]
    assert sell_voters, "expected at least one SELL dissent"
    assert any(collapse[persona]["caved"] is False for persona in sell_voters)


# ==========================================================================
# (b) Structured artifacts
# ==========================================================================


def test_b_thesis_recorded_for_every_persona_in_the_opening(recorded):
    events, _result = recorded
    theses = _of_type(events, "thesis_recorded")

    assert {t.payload["persona"] for t in theses} == COMMITTEE
    assert all(t.payload["phase"] == "opening" for t in theses)
    assert all(t.payload["stance"] in {"bullish", "bearish", "neutral"} for t in theses)
    assert all(t.payload["claims"] for t in theses)

    # Every thesis is recorded before the first cross-examination turn (the
    # opening's structured records are complete before free-form NL begins).
    types = [e.type for e in events]
    first_cross_turn = next(
        i for i, e in enumerate(events)
        if e.type == "turn_started" and e.payload["phase"] == "cross_exam"
    )
    last_thesis = max(i for i, t in enumerate(types) if t == "thesis_recorded")
    assert last_thesis < first_cross_turn


def test_b_well_formed_verdicts_yield_structured_votes(recorded):
    events, _result = recorded
    votes = _of_type(events, "vote_recorded")

    assert {v.payload["persona"] for v in votes} == COMMITTEE
    # Every persona emitted a mandated verdict block, so extraction consumed the
    # structured record and made no LLM-extraction fallback.
    assert all(v.payload["source"] == "structured" for v in votes)
    assert all(v.payload["vote"] in {"BUY", "HOLD", "SELL"} for v in votes)


# ==========================================================================
# (c) Grounded memo (+ window-failure resilience)
# ==========================================================================


def test_c_every_memo_section_is_grounded_on_structured_records(recorded):
    events, result = recorded
    sections = _of_type(events, "memo_section")

    assert {s.payload["section"] for s in sections} == CANONICAL_MEMO_SECTIONS
    for section in sections:
        personas = section.payload["contributing_personas"]
        supporting = section.payload["supporting_data"]
        # Every attributed persona exists in the committee (no invented names)...
        assert personas, f"{section.payload['section']} has no contributing personas"
        assert set(personas) <= COMMITTEE, personas
        # ...and the section cites concrete supporting data (FR-4.5 grounding).
        assert supporting, f"{section.payload['section']} cites no supporting data"

    # The synthesis really ran through the aggregator binding (a memo usage event)
    # and is attached to the result document.
    assert any(
        e.type == "usage" and e.payload["purpose"] == "memo" for e in events
    )
    assert result.memo is not None


def test_c_memo_draft_survives_an_injected_window_failure(tmp_path):
    """The memo is grounded on the *full* transcript despite a window failure.

    The transcript spans many windows; an aggregator call raises on one of them.
    The windowed synthesis must retain the accumulated draft and continue, so the
    emitted memo sections carry real grounded content — never the failure
    fallback.
    """

    class _FaultyOnce:
        """Raises on its second send (a mid-stream window), then behaves."""

        def __init__(self, lines: list[str]) -> None:
            self._lines = lines
            self._sends = 0

        def send(self, request):
            self._sends += 1
            if self._sends == 2:
                raise RuntimeError("injected window failure")
            return gen.fake_response(self._lines)

        def close(self) -> None:
            pass

    def faulty_factory(binding, credentials):
        return OpenAIChatAdapter(
            binding,
            credentials,
            base_url="https://api.openai.com/v1",
            credential_ref=None,
            http=_FaultyOnce(gen.aggregator_sse_lines()),
            sleep=lambda _delay: None,
        )

    committee = gen.make_committee(transport_factory=faulty_factory)
    log_path = tmp_path / "window-failure.jsonl"
    with EventLog(
        "aapl-20260713-wf01", path=log_path, clock=lambda: gen.FIXED_NOW
    ) as event_log:
        gen.record_debate(event_log, committee=committee)

    events = read_event_log(log_path)
    sections = _of_type(events, "memo_section")
    assert {s.payload["section"] for s in sections} == CANONICAL_MEMO_SECTIONS
    # The draft survived: real grounded content, not the "Memo generation failed"
    # fallback, and every section still cites supporting data.
    assert all("failed" not in s.payload["content"].lower() for s in sections)
    assert all(s.payload["supporting_data"] for s in sections)
    assert all(set(s.payload["contributing_personas"]) <= COMMITTEE for s in sections)
    # And the debate as a whole completed cleanly.
    assert events[-1].type == "debate_completed"
    assert "debate_error" not in {e.type for e in events}


# ==========================================================================
# (d) Collapse metric for a constructed cave
# ==========================================================================


def test_d_collapse_metric_is_emitted_for_the_constructed_cave(recorded):
    events, _result = recorded
    collapse = _of_type(events, "collapse_metric")

    # One trajectory per persona (each has an opening thesis and a final verdict).
    assert {c.payload["persona"] for c in collapse} == COMMITTEE
    by_persona = {c.payload["persona"]: c.payload for c in collapse}

    caved = [persona for persona, payload in by_persona.items() if payload["caved"]]
    assert len(caved) == 1, f"expected exactly one caved persona, got {caved}"
    cave = by_persona[caved[0]]

    # The constructed cave: a contrarian bearish opening abandoned for the bullish
    # majority at the verdict, realized in the verdict phase.
    assert cave["stance_before"] == "bearish"
    assert cave["stance_after"] == "bullish"
    assert cave["phase"] == "verdict"
    assert cave["note"]

    # The standing dissenter (a SELL that held) is *not* flagged as caved, so the
    # metric distinguishes a genuine hold from a sycophantic collapse.
    sell_voters = {
        v.payload["persona"]
        for v in _of_type(events, "vote_recorded")
        if v.payload["vote"] == "SELL"
    }
    assert sell_voters
    assert all(by_persona[persona]["caved"] is False for persona in sell_voters)
    assert caved[0] not in sell_voters


# ==========================================================================
# (e) Golden fixture: schema-valid, replayable, renderer-ready, drift-free
# ==========================================================================


def test_e_golden_is_schema_valid_replayable_and_renderer_ready():
    path = gen.GOLDEN_PATH
    assert path.exists(), "M4 golden fixture is missing; run scripts/regen_m4_golden.py"

    # The engine reader validates schema + seq contiguity + terminal bookends.
    events = read_event_log(path)
    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_completed"
    assert {event.debate_id for event in events} == {gen.DEBATE_ID}
    assert {event.ts for event in events} == {gen.FIXED_NOW}

    # It genuinely captures the *new* M4 emission set (not just the M1 events).
    kinds = {event.type for event in events}
    assert {
        "thesis_recorded",
        "vote_recorded",
        "memo_section",
        "disagreement",
        "collapse_metric",
    } <= kinds

    # The TUI reader tolerates every event: all types known, no missing required
    # or invalid-enum fields, and the log is replayable (additive-safe contract).
    tui_events = read_events(path)
    assert is_replayable(tui_events)
    problems: list[str] = []
    for event in tui_events:
        assert event.known, f"seq {event.seq}: unknown type {event.type!r}"
        problems += missing_required_fields(event)
        problems += invalid_enum_fields(event)
    assert not problems, problems


def test_e_generator_reproduces_the_committed_golden(tmp_path):
    """The committed golden is generated behavior, not a hand-stale sample.

    Regenerating from the single-sourced generator under the same isolated state
    (the conftest sandbox gives a fresh DA rotation counter) must reproduce the
    committed fixture exactly, modulo the per-run ``result_ref`` path.
    """
    log_path = tmp_path / f"{gen.DEBATE_ID}.jsonl"
    with EventLog(
        gen.DEBATE_ID, path=log_path, clock=lambda: gen.FIXED_NOW
    ) as event_log:
        gen.record_debate(event_log)

    def normalized(path: Path) -> list[dict]:
        documents = [event.model_dump(mode="json") for event in read_event_log(path)]
        for document in documents:
            if document["type"] == "debate_completed":
                document["payload"]["result_ref"] = "<RESULT_REF>"
        return documents

    assert normalized(log_path) == normalized(gen.GOLDEN_PATH)
