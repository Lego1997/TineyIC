"""Schema-conformance tests for the synthetic debate log against event-schema.md.

Every event produced by the deterministic generator is validated against the
frozen v1 contract: known type, all required payload fields present, closed
enums respected (including nested ``data_ready.sources[].status``), and a
well-formed envelope (v/seq/ts/debate_id). The set of event *types* is
cross-checked against the type tables in ``docs/event-schema.md`` so the code and
the doc cannot drift apart silently.

This is interim CONTRACT-TEST scaffolding: when M1 records a real golden log from
a mocked debate, the renderer/snapshot tests migrate to that recording and this
file narrows to a pure schema-conformance guard on the generator.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from tinyic.tui import events as E

from tests.support import synthetic_events as G

_SCHEMA_DOC = Path(__file__).resolve().parents[1] / "docs" / "event-schema.md"
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_DEBATE_ID_RE = re.compile(r"^[a-z0-9]+-\d{8}-[a-z0-9]+$")
_SOURCE_STATUSES = frozenset({"ok", "degraded", "unavailable", "disabled_no_credential"})
_PHASES = ("opening", "cross_exam", "rebuttal", "verdict")


def _doc_event_types() -> set[str]:
    """Extract the event-type names from the markdown type tables in the schema doc."""
    text = _SCHEMA_DOC.read_text(encoding="utf-8")
    # Type tables are rows whose first column is a backticked snake_case token.
    row = re.compile(r"^\|\s*`([a-z][a-z_]*)`\s*\|", re.MULTILINE)
    return set(row.findall(text))


def _doc_payload_tokens() -> dict[str, set[str]]:
    """Map each event type to the identifier tokens named in its payload cell.

    Used to prove ``REQUIRED_PAYLOAD_FIELDS`` is a faithful transcription — every
    field the code requires must actually be named by the schema doc for that type.
    """
    text = _SCHEMA_DOC.read_text(encoding="utf-8")
    row = re.compile(r"^\|\s*`([a-z][a-z_]*)`\s*\|(.*)\|\s*$", re.MULTILINE)
    ident = re.compile(r"[a-z][a-z_]+")
    return {etype: set(ident.findall(cell)) for etype, cell in row.findall(text)}


def _generated_events() -> list[E.Event]:
    return E.read_events(G.to_jsonl(G.build_events()).splitlines())


# --------------------------------------------------------------------------- #
# The code's type set matches the doc's type set
# --------------------------------------------------------------------------- #

def test_known_types_match_the_schema_doc_exactly():
    doc_types = _doc_event_types()
    assert doc_types, "failed to parse any event types out of event-schema.md"
    assert doc_types == set(E.KNOWN_EVENT_TYPES)


def test_required_fields_are_faithfully_transcribed_from_the_doc():
    doc_tokens = _doc_payload_tokens()
    problems: list[str] = []
    for etype, required in E.REQUIRED_PAYLOAD_FIELDS.items():
        named = doc_tokens.get(etype, set())
        for field in required:
            if field not in named:
                problems.append(f"{etype}: required field {field!r} not named in schema doc")
    assert not problems, "\n".join(problems)


# --------------------------------------------------------------------------- #
# Every generated event conforms to the schema
# --------------------------------------------------------------------------- #

def test_every_generated_event_has_required_fields_and_valid_enums():
    events = _generated_events()
    problems: list[str] = []
    for event in events:
        if not event.known:
            problems.append(f"seq {event.seq}: unknown type {event.type!r}")
            continue
        missing = E.missing_required_fields(event)
        if missing:
            problems.append(f"seq {event.seq} {event.type}: missing {missing}")
        invalid = E.invalid_enum_fields(event)
        if invalid:
            problems.append(f"seq {event.seq} {event.type}: bad enum {invalid}")
    assert not problems, "\n".join(problems)


def test_nested_data_ready_source_statuses_are_valid_enums():
    (data_ready,) = [e for e in _generated_events() if e.type == "data_ready"]
    sources = data_ready.payload["sources"]
    assert sources, "data_ready must list sources"
    for source in sources:
        assert "name" in source
        assert source["status"] in _SOURCE_STATUSES, source


def test_envelope_invariants():
    events = _generated_events()
    # v == 1 everywhere.
    assert all(e.v == E.SCHEMA_VERSION for e in events)
    # seq strictly increasing, contiguous, starting at 1 (no gaps).
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    # ts is UTC ISO-8601 with milliseconds, and monotonically non-decreasing.
    parsed_ts = []
    for e in events:
        assert _TS_RE.match(e.ts), f"seq {e.seq}: bad ts {e.ts!r}"
        parsed_ts.append(datetime.fromisoformat(e.ts.replace("Z", "+00:00")))
    assert parsed_ts == sorted(parsed_ts)
    # debate_id shape and consistency.
    assert all(e.debate_id == G.DEBATE_ID for e in events)
    assert _DEBATE_ID_RE.match(G.DEBATE_ID)


def test_log_is_replayable_and_bookended():
    events = _generated_events()
    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_completed"
    assert E.is_replayable(events) is True


# --------------------------------------------------------------------------- #
# The generator satisfies the M5 stage-1 content contract
# --------------------------------------------------------------------------- #

def test_debate_started_declares_six_fully_bound_personas():
    (started,) = [e for e in _generated_events() if e.type == "debate_started"]
    personas = started.payload["personas"]
    assert len(personas) == 6
    for persona in personas:
        for field in ("name", "model_ref", "auth_profile", "thinking_level", "temperament"):
            assert persona.get(field), f"persona missing {field}: {persona}"
    for key in ("moderator", "aggregator", "caps", "config_hash", "tinyic_version"):
        assert key in started.payload


def test_all_four_phases_are_opened_and_completed():
    events = _generated_events()
    started = [e.payload["phase"] for e in events if e.type == "phase_started"]
    completed = [e.payload["phase"] for e in events if e.type == "phase_completed"]
    assert started == list(_PHASES)
    assert completed == list(_PHASES)
    # cross_exam announces its devil's advocate.
    (cross,) = [e for e in events if e.type == "phase_started" and e.payload["phase"] == "cross_exam"]
    assert cross.payload.get("da_persona")


def test_six_votes_and_a_consistent_scorecard():
    events = _generated_events()
    votes = [e for e in events if e.type == "vote_recorded"]
    assert len(votes) == 6
    assert all(v.payload["source"] in {"structured", "extracted"} for v in votes)
    (scorecard,) = [e for e in events if e.type == "scorecard"]
    bull = sum(1 for v in votes if v.payload["vote"] == "BUY")
    bear = sum(1 for v in votes if v.payload["vote"] == "SELL")
    hold = sum(1 for v in votes if v.payload["vote"] == "HOLD")
    assert scorecard.payload["bull_count"] == bull
    assert scorecard.payload["bear_count"] == bear
    assert scorecard.payload["hold_count"] == hold
    assert bull + bear + hold == 6


def test_five_canonical_memo_sections():
    sections = [
        e.payload["section"] for e in _generated_events() if e.type == "memo_section"
    ]
    assert sections == [
        "executive_summary",
        "investment_thesis",
        "key_risks",
        "valuation_discussion",
        "final_verdict",
    ]


def test_steer_delivered_mid_cross_exam_and_queue_at_phase_boundary():
    events = _generated_events()
    by_seq = {e.seq: e for e in events}

    submits = [e for e in events if e.type == "steering_submitted"]
    delivers = {e.payload["msg_id"]: e for e in events if e.type == "steering_delivered"}
    modes = {e.payload["msg_id"]: e.payload["mode"] for e in submits}
    assert modes == {"m01": "steer", "m02": "queue"}

    # Helper: which phase is active at a given seq (between phase_started/completed).
    def phase_at(seq: int) -> str | None:
        active = None
        for s in range(1, seq):
            ev = by_seq.get(s)
            if ev is None:
                continue
            if ev.type == "phase_started":
                active = ev.payload["phase"]
            elif ev.type == "phase_completed":
                active = None
        return active

    # steer (m01): submitted AND delivered while cross_exam is the active phase.
    steer_submit = next(e for e in submits if e.payload["msg_id"] == "m01")
    steer_deliver = delivers["m01"]
    assert phase_at(steer_submit.seq) == "cross_exam"
    assert phase_at(steer_deliver.seq) == "cross_exam"
    # It is delivered *before* a real upcoming turn within the phase.
    target_turn = steer_deliver.payload["delivered_before_turn_id"]
    starts_after = [
        e for e in events
        if e.type == "turn_started" and e.seq > steer_deliver.seq
        and e.payload["turn_id"] == target_turn
    ]
    assert starts_after, "steer must be delivered before an actual upcoming turn"

    # queue (m02): submitted at the cross_exam->rebuttal boundary, delivered as
    # rebuttal opens (i.e. not inside the same phase it was submitted from).
    queue_deliver = delivers["m02"]
    assert phase_at(queue_deliver.seq) == "rebuttal"
    q_target = queue_deliver.payload["delivered_before_turn_id"]
    first_rebuttal_turn = next(
        e for e in events
        if e.type == "turn_started" and e.payload["phase"] == "rebuttal"
    )
    assert q_target == first_rebuttal_turn.payload["turn_id"]


# --------------------------------------------------------------------------- #
# The committed fixture is byte-identical to a fresh generation (no drift)
# --------------------------------------------------------------------------- #

def test_committed_fixture_matches_generator_output():
    assert G.FIXTURE_PATH.is_file(), (
        "fixture missing — run `uv run python -m tests.support.synthetic_events`"
    )
    on_disk = G.FIXTURE_PATH.read_text(encoding="utf-8")
    fresh = G.to_jsonl(G.build_events())
    assert on_disk == fresh, "fixture is stale; regenerate it"
