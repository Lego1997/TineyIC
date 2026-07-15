"""M6 Stage 1 acceptance: the headless ``tinyic debate`` command (FR-6.1/6.2).

Everything here is offline: a debate is driven end to end through the same fake
wire transports ``test_m2_dod`` uses (recorded SSE fixtures, zero network),
injected via a pre-built ``committee`` exactly as that milestone does.

The headline assertion (FR-6.2): a ``--headless --json`` run's STDOUT parses
line-by-line as schema-valid events with **zero** non-JSON lines, human progress
goes to STDERR, and the streamed lines are byte-identical to the recorded log.
The rest cover the exit-code contract (0/2/3), engine-backed steering delivery,
the ``esc`` interrupt (discard-on-arrival + retake), the STDIN steering reader,
and the ``runs list`` / ``result`` document surface.
"""

from __future__ import annotations

import io
import json
import logging
import os
import socket
import threading

import pytest

import tinyic.debate as debate_module
import tinyic.headless as headless_module
from tinyic.debate.steering import SteeringInbox
from tinyic.events import EventEnvelope, EventLog, make_debate_id, read_event_log
from tinyic.headless import PersonaSelectionError, resolve_personas, run_debate_command
from tinyic.models import StaticCredentialProvider, build_committee
from tinyic.personas.base import InvestorPersona
from tinyic.result import assemble_result, list_runs, load_result
from tinytroupe.agent import TinyPerson

# Reuse the recorded-fixture transport machinery + deterministic mocks from the
# M2 DoD suite so the debate is fully offline and adapter-driven.
from tests.test_m2_dod import (
    _act_via_binding,
    _dod_transport_factory,
    _mock_data_package,
    _mock_disagreements,
    _mock_memo,
    _mock_votes,
    _preset_all,
    _PERSONAS_3,
)

_REGISTRY_3 = [name for name, _display in _PERSONAS_3]
_PERSONAS_CSV = ",".join(_REGISTRY_3)


@pytest.fixture
def binding_mocks(monkeypatch):
    """Route every persona turn through its fake binding client; stub aggregation."""
    monkeypatch.setattr(InvestorPersona, "act", _act_via_binding)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(TinyPerson, "communication_display", False)
    monkeypatch.setattr(debate_module, "extract_votes", _mock_votes)
    monkeypatch.setattr(debate_module, "generate_memo", _mock_memo)
    monkeypatch.setattr(debate_module, "extract_disagreements", _mock_disagreements)


def _fake_committee(model: str = "openai/gpt-5.2"):
    return build_committee(
        _preset_all(model),
        _PERSONAS_3,
        credentials=StaticCredentialProvider({}),
        transport_factory=_dod_transport_factory,
    )


def _jsonl_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


# ==========================================================================
# Persona resolution (FR-6.1 --personas)
# ==========================================================================


def test_resolve_personas_default_explicit_and_errors():
    assert len(resolve_personas(None)) == 6  # the default committee
    assert resolve_personas("warren_buffett,charlie_munger") == [
        "warren_buffett",
        "charlie_munger",
    ]
    with pytest.raises(PersonaSelectionError):
        resolve_personas("warren_buffett")  # below MIN_PERSONAS
    with pytest.raises(PersonaSelectionError):
        resolve_personas("warren_buffett,nobody_here")  # unknown name


# ==========================================================================
# (FR-6.2) --headless --json: STDOUT is pure schema-valid JSONL, exit 0
# ==========================================================================


def test_headless_json_stdout_is_pure_schema_valid_events(binding_mocks):
    out, err = io.StringIO(), io.StringIO()
    code = run_debate_command(
        "AAPL",
        personas=_PERSONAS_CSV,
        headless=True,
        json_mode=True,
        committee=_fake_committee(),
        data_package=_mock_data_package(),
        out=out,
        err=err,
    )

    assert code == 0
    lines = _jsonl_lines(out.getvalue())
    assert lines, "expected a streamed event log on stdout"

    # EVERY stdout line is a schema-valid v1 event — zero non-JSON lines.
    events = []
    for line in lines:
        obj = json.loads(line)  # raises on any non-JSON line
        events.append(EventEnvelope.model_validate(obj))  # raises on schema violation

    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_completed"
    types = {e.type for e in events}
    assert "debate_error" not in types
    # A real, streamed debate: 3 personas x 4 phases of turns, with deltas.
    assert sum(e.type == "turn_started" for e in events) == 12
    assert sum(e.type == "think_delta" for e in events) == 12
    assert sum(e.type == "talk_delta" for e in events) == 12

    # Human-readable progress landed on STDERR, never STDOUT.
    assert "debate complete" in err.getvalue()
    # Every stdout line is a JSON object — nothing else leaks onto the channel.
    for line in lines:
        assert line.lstrip().startswith("{")


def test_headless_json_stdout_is_verbatim_copy_of_the_recorded_log(binding_mocks):
    out, err = io.StringIO(), io.StringIO()
    run_debate_command(
        "AAPL",
        personas=_PERSONAS_CSV,
        headless=True,
        json_mode=True,
        committee=_fake_committee(),
        data_package=_mock_data_package(),
        out=out,
        err=err,
    )
    stdout_lines = _jsonl_lines(out.getvalue())

    # The stream is the file "as written": locate the run and compare verbatim.
    debate_id = json.loads(stdout_lines[0])["debate_id"]
    from tinyic.result import resolve_run_path

    log_lines = _jsonl_lines(resolve_run_path(debate_id).read_text(encoding="utf-8"))
    assert stdout_lines == log_lines
    # And the recorded log is itself a valid, contiguous v1 stream.
    events = read_event_log(resolve_run_path(debate_id))
    assert [e.seq for e in events] == list(range(1, len(events) + 1))


def test_headless_without_json_is_silent_on_stdout(binding_mocks):
    out, err = io.StringIO(), io.StringIO()
    code = run_debate_command(
        "AAPL",
        personas=_PERSONAS_CSV,
        headless=True,
        json_mode=False,
        committee=_fake_committee(),
        data_package=_mock_data_package(),
        out=out,
        err=err,
    )
    assert code == 0
    assert out.getvalue() == ""  # no JSON stream without --json
    assert "debate complete" in err.getvalue()  # progress still on stderr


# ==========================================================================
# (FR-6.2) Exit codes 0 / 2 / 3
# ==========================================================================


def test_exit_code_2_on_partial_debate_after_phases(monkeypatch, binding_mocks):
    # Every phase runs, then extraction fails -> debate_error after >=1 phase.
    def _boom(_orchestrator):
        raise RuntimeError("extraction blew up")

    monkeypatch.setattr(debate_module, "extract_votes", _boom)

    out, err = io.StringIO(), io.StringIO()
    code = run_debate_command(
        "AAPL",
        personas=_PERSONAS_CSV,
        headless=True,
        json_mode=True,
        committee=_fake_committee(),
        data_package=_mock_data_package(),
        out=out,
        err=err,
    )
    assert code == 2
    events = [EventEnvelope.model_validate(json.loads(l)) for l in _jsonl_lines(out.getvalue())]
    assert events[-1].type == "debate_error"
    assert sum(e.type == "phase_completed" for e in events) == 4  # all phases ran


def test_exit_code_3_on_setup_error_with_doctor_reason_on_stderr(tmp_path):
    # An unknown provider in the resolved preset fails committee build at setup
    # (0 phases) -> exit 3, and a doctor-style reason is written to STDERR only.
    config = tmp_path / "tinyic.toml"
    config.write_text(
        'default_preset = "default"\n[presets.default]\nmodel = "bogus/nope"\n',
        encoding="utf-8",
    )
    out, err = io.StringIO(), io.StringIO()
    code = run_debate_command(
        "AAPL",
        personas=_PERSONAS_CSV,
        headless=True,
        json_mode=True,
        data_package=_mock_data_package(),
        config_path=str(config),
        out=out,
        err=err,
    )
    assert code == 3
    # STDOUT stays a clean JSON channel even on failure.
    for line in _jsonl_lines(out.getvalue()):
        EventEnvelope.model_validate(json.loads(line))
    assert "setup failed" in err.getvalue().lower()


# ==========================================================================
# (FR-5.3) Engine-backed steering delivery — deterministic (pre-loaded inbox)
# ==========================================================================


def _run_logged(committee, inbox, tmp_path, debate_id="aapl-20260713-str1"):
    log_path = tmp_path / f"{debate_id}.jsonl"
    with EventLog(debate_id, path=log_path) as log:
        inbox.bind_event_log(log)
        debate_module.run_debate(
            "AAPL",
            _REGISTRY_3,
            data_package=_mock_data_package(),
            event_log=log,
            committee=committee,
            steering=inbox,
            da="warren_buffett",
        )
    return read_event_log(log_path)


def _assert_steering_lifecycle_closed(events) -> None:
    """Every steering_submitted msg_id has exactly one delivered/dropped ack.

    The frozen contract (docs/event-schema.md) forbids a silent loss: an
    acknowledged command must terminate as either delivered or dropped, never
    both and never neither.
    """
    submitted = [
        e.payload["msg_id"] for e in events if e.type == "steering_submitted"
    ]
    for msg_id in submitted:
        acks = [
            e
            for e in events
            if e.type in ("steering_delivered", "steering_dropped")
            and e.payload["msg_id"] == msg_id
        ]
        assert len(acks) == 1, f"{msg_id}: {len(acks)} acks, expected exactly 1"


def test_steer_and_queue_are_acknowledged_and_delivered(tmp_path, binding_mocks):
    inbox = SteeringInbox(id_prefix="m")
    # Pre-loaded (before debate_started): the ack is deferred until the command
    # is delivered, but every delivered command still has a preceding submit.
    steer_id = inbox.submit("steer", "Press on China supply-chain risk.", target="Warren Buffett")
    queue_id = inbox.submit("queue", "Everyone tie your view to a valuation multiple.")
    assert steer_id and queue_id

    events = _run_logged(_fake_committee(), inbox, tmp_path)
    by_seq = {e.seq: e for e in events}

    submits = {e.payload["msg_id"]: e for e in events if e.type == "steering_submitted"}
    delivers = {e.payload["msg_id"]: e for e in events if e.type == "steering_delivered"}
    assert {steer_id, queue_id} <= set(submits)
    assert submits[steer_id].payload["mode"] == "steer"
    assert submits[queue_id].payload["mode"] == "queue"
    assert submits[steer_id].payload["target_persona"] == "Warren Buffett"

    # Each is delivered exactly once, and the ack precedes the delivery in seq.
    assert {steer_id, queue_id} <= set(delivers)
    for msg_id in (steer_id, queue_id):
        assert submits[msg_id].seq < delivers[msg_id].seq
        # Delivered before a real turn (the opening phase boundary is both the
        # first speaker boundary and the first phase boundary).
        before = delivers[msg_id].payload["delivered_before_turn_id"]
        assert before == "turn-0001"

    # The moderator relay actually reached the committee (targeted framing to
    # Warren Buffett, plus an observation to the others).
    buffett = _display_agent(events)  # sanity: the debate really ran
    assert buffett
    _assert_steering_lifecycle_closed(events)


def test_queue_defers_to_next_phase_while_steer_lands_next_turn(tmp_path, binding_mocks, monkeypatch):
    # Submit both mid-opening (from the first speaker's turn) so the boundary
    # distinction is observable: the steer lands at the very next speaker turn,
    # the queue waits for the cross_exam phase boundary.
    inbox = SteeringInbox(id_prefix="b")
    submitted: dict[str, str] = {}

    def act_and_submit(self, *, return_actions=False, **kwargs):
        if self.name == "Warren Buffett" and "steer" not in submitted:
            submitted["steer"] = inbox.submit("steer", "sharpen the moat question")
            submitted["queue"] = inbox.submit("queue", "cite a valuation multiple")
        return _act_via_binding(self, return_actions=return_actions, **kwargs)

    monkeypatch.setattr(InvestorPersona, "act", act_and_submit)

    events = _run_logged(_fake_committee(), inbox, tmp_path, debate_id="aapl-20260713-bnd")
    delivers = {e.payload["msg_id"]: e for e in events if e.type == "steering_delivered"}
    turn_phase = {
        e.payload["turn_id"]: e.payload["phase"]
        for e in events
        if e.type == "turn_started"
    }
    steer_turn = delivers[submitted["steer"]].payload["delivered_before_turn_id"]
    queue_turn = delivers[submitted["queue"]].payload["delivered_before_turn_id"]
    # Steer lands still inside opening (next speaker); queue waits for cross_exam.
    assert turn_phase[steer_turn] == "opening"
    assert turn_phase[queue_turn] == "cross_exam"
    _assert_steering_lifecycle_closed(events)


def _display_agent(events) -> bool:
    return any(e.type == "turn_completed" for e in events)


def test_undelivered_steering_is_dropped_explicitly(tmp_path):
    # Unit-level: a command still pending when the inbox closes is dropped, never
    # silently — and the drop is a schema-valid steering_dropped event.
    debate_id = "aapl-20260713-drop"
    log_path = tmp_path / f"{debate_id}.jsonl"
    log = EventLog(debate_id, path=log_path)
    log.emit("debate_started", _minimal_started_payload())
    inbox = SteeringInbox(event_log=log, id_prefix="d")
    msg_id = inbox.submit("queue", "never delivered")
    inbox.close(reason="debate_ended")
    log.emit(
        "debate_completed",
        {"phases_completed": [], "duration_s": 0.0, "result_ref": str(log_path)},
    )
    log.close()

    events = read_event_log(log_path)
    dropped = [e for e in events if e.type == "steering_dropped"]
    assert len(dropped) == 1
    assert dropped[0].payload == {"msg_id": msg_id, "reason": "debate_ended"}
    # A closed inbox no-ops further submits (never crashes the producer).
    assert inbox.submit("steer", "too late") is None
    _assert_steering_lifecycle_closed(events)


def test_queue_drained_at_crashing_phase_boundary_is_dropped_not_lost(
    tmp_path, binding_mocks, monkeypatch
):
    # TIC-003 regression: a command drained at a boundary but not yet
    # acknowledged delivered when the relay crashes must still be dropped
    # explicitly at the terminal close — never lost in the drained window.
    from tinyic.debate.moderator import Moderator

    inbox = SteeringInbox(id_prefix="c")
    submitted: dict[str, str] = {}

    def act_and_submit(self, *, return_actions=False, **kwargs):
        # Submitted mid-opening so it is drained at the cross_exam phase boundary.
        if self.name == "Warren Buffett" and "queue" not in submitted:
            submitted["queue"] = inbox.submit("queue", "phase-two instruction")
        return _act_via_binding(self, return_actions=return_actions, **kwargs)

    monkeypatch.setattr(InvestorPersona, "act", act_and_submit)

    real_relay = Moderator.relay_message

    def relay_or_boom(self, text, target, **kwargs):
        if text == "phase-two instruction":
            raise RuntimeError("relay blew up at the phase boundary")
        return real_relay(self, text, target, **kwargs)

    monkeypatch.setattr(Moderator, "relay_message", relay_or_boom)

    debate_id = "aapl-20260713-crashdrop"
    log_path = tmp_path / f"{debate_id}.jsonl"
    with pytest.raises(RuntimeError):
        with EventLog(debate_id, path=log_path) as log:
            inbox.bind_event_log(log)
            debate_module.run_debate(
                "AAPL",
                _REGISTRY_3,
                data_package=_mock_data_package(),
                event_log=log,
                committee=_fake_committee(),
                steering=inbox,
                da="warren_buffett",
            )

    events = read_event_log(log_path)
    msg_id = submitted["queue"]
    dropped = [
        e
        for e in events
        if e.type == "steering_dropped" and e.payload["msg_id"] == msg_id
    ]
    delivered = [
        e
        for e in events
        if e.type == "steering_delivered" and e.payload["msg_id"] == msg_id
    ]
    assert len(dropped) == 1
    assert delivered == []
    assert events[-1].type == "debate_error"
    cross_exam = next(
        e
        for e in events
        if e.type == "phase_started" and e.payload["phase"] == "cross_exam"
    )
    # Dropped during the run — after the crashing boundary, before the terminal.
    assert cross_exam.seq < dropped[0].seq < events[-1].seq
    _assert_steering_lifecycle_closed(events)


def test_targeted_steer_waits_for_that_personas_next_turn(
    tmp_path, binding_mocks, monkeypatch
):
    # TIC-015 regression: a steer addressed to Warren Buffett, submitted during
    # his own opening turn, must be delivered before HIS next turn (cross_exam) —
    # not the immediately-next speaker (Benjamin Graham at turn-0002).
    inbox = SteeringInbox(id_prefix="t")
    submitted: dict[str, str] = {}

    def act_and_submit(self, *, return_actions=False, **kwargs):
        if self.name == "Warren Buffett" and "steer" not in submitted:
            submitted["steer"] = inbox.submit(
                "steer", "revisit your moat claim", target="Warren Buffett"
            )
        return _act_via_binding(self, return_actions=return_actions, **kwargs)

    monkeypatch.setattr(InvestorPersona, "act", act_and_submit)

    events = _run_logged(
        _fake_committee(), inbox, tmp_path, debate_id="aapl-20260713-target"
    )
    delivers = {
        e.payload["msg_id"]: e for e in events if e.type == "steering_delivered"
    }
    turn_started = {
        e.payload["turn_id"]: e.payload
        for e in events
        if e.type == "turn_started"
    }
    before = delivers[submitted["steer"]].payload["delivered_before_turn_id"]
    assert before != "turn-0002"
    assert turn_started[before]["persona"] == "Warren Buffett"
    assert turn_started[before]["phase"] == "cross_exam"
    _assert_steering_lifecycle_closed(events)


def test_targeted_steer_with_no_future_turn_is_dropped(
    tmp_path, binding_mocks, monkeypatch
):
    # TIC-015 regression: a steer addressed to the first persona but submitted
    # during the last persona's verdict turn has no future boundary to land on;
    # it is dropped at close, never delivered to the wrong speaker.
    inbox = SteeringInbox(id_prefix="v")
    submitted: dict[str, str] = {}
    munger_acts = {"count": 0}

    def act_and_submit(self, *, return_actions=False, **kwargs):
        if self.name == "Charlie Munger":
            munger_acts["count"] += 1
            # Munger's 4th act is his verdict turn (last speaker, last phase).
            if munger_acts["count"] == 4 and "late" not in submitted:
                submitted["late"] = inbox.submit(
                    "steer", "too late to matter", target="Warren Buffett"
                )
        return _act_via_binding(self, return_actions=return_actions, **kwargs)

    monkeypatch.setattr(InvestorPersona, "act", act_and_submit)

    events = _run_logged(
        _fake_committee(), inbox, tmp_path, debate_id="aapl-20260713-nofuture"
    )
    msg_id = submitted["late"]
    dropped = [
        e
        for e in events
        if e.type == "steering_dropped" and e.payload["msg_id"] == msg_id
    ]
    delivered = [
        e
        for e in events
        if e.type == "steering_delivered" and e.payload["msg_id"] == msg_id
    ]
    assert len(dropped) == 1
    assert dropped[0].payload["reason"]  # dropped with an explicit reason
    assert delivered == []
    assert events[-1].type == "debate_completed"
    _assert_steering_lifecycle_closed(events)


def _consolidation_boom():
    # A named helper so the enriched debate_error can name the innermost failing
    # frame (module.function), the way the real consolidation crash would.
    raise AttributeError("'NoneType' object has no attribute 'get'")


def test_debate_error_names_failing_component_and_logs_traceback(
    tmp_path, binding_mocks, monkeypatch, caplog
):
    # TIC-001 regression: the terminal debate_error message names the failing
    # component (innermost module.function), never a bare exception class, and
    # the full traceback lands in the application log — while the public event
    # stays free of file paths and exception-args text.
    def boom(_self):
        _consolidation_boom()

    monkeypatch.setattr(debate_module.DebateOrchestrator, "run_debate", boom)

    debate_id = "aapl-20260713-diag"
    log_path = tmp_path / f"{debate_id}.jsonl"
    with caplog.at_level(logging.ERROR, logger="tinyic.debate"):
        with pytest.raises(AttributeError):
            with EventLog(debate_id, path=log_path) as log:
                debate_module.run_debate(
                    "AAPL",
                    _REGISTRY_3,
                    data_package=_mock_data_package(),
                    event_log=log,
                    committee=_fake_committee(),
                )

    events = read_event_log(log_path)
    error = events[-1]
    assert error.type == "debate_error"
    # Schema v1: exactly stage/message/recoverable — no new payload fields.
    assert set(error.payload) == {"stage", "message", "recoverable"}
    assert error.payload["stage"] == "debate"
    assert error.payload["recoverable"] is False
    message = error.payload["message"]
    assert "AttributeError" in message
    assert "_consolidation_boom" in message  # the innermost failing frame
    # Secret-free: no file paths and no exception-args text in the public event.
    assert os.sep not in message
    assert "NoneType" not in message
    # The crash is logged, not silently swallowed. The security log filter
    # (tinytroupe/utils/config.py) scrubs the raw traceback but keeps the stage
    # and exception class, which is enough to locate the failure.
    logged = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR
        and "debate failed at stage debate" in record.getMessage()
        and "AttributeError" in record.getMessage()
    ]
    assert logged, "the crash must be logged on the crash path, never swallowed"


# ==========================================================================
# (FR-5.3) esc interrupt: discard-on-arrival + retake with the message in context
# ==========================================================================


def test_interrupt_discards_turn_and_speaker_retakes(tmp_path, binding_mocks):
    inbox = SteeringInbox(id_prefix="i")
    # An interrupt pending before the debate starts lands on the very first turn:
    # that turn's content arrives, then is discarded, and the speaker retakes.
    inbox.request_interrupt(text="Reconsider the downside first.", source="stdin")

    events = _run_logged(_fake_committee(), inbox, tmp_path, debate_id="aapl-20260713-int1")

    interrupted = [e for e in events if e.type == "turn_interrupted"]
    assert len(interrupted) == 1
    payload = interrupted[0].payload
    assert payload == {
        "turn_id": "turn-0001",  # the first turn was discarded
        "persona": "Warren Buffett",
        "by": "user",
        "disposition": "discarded_on_arrival",
    }

    # The discarded turn has no turn_completed; the retake is a fresh turn that
    # does complete, so the first speaker still produces a committed turn.
    completed_ids = {
        e.payload["turn_id"] for e in events if e.type == "turn_completed"
    }
    assert "turn-0001" not in completed_ids
    assert "turn-0002" in completed_ids
    retake = next(e for e in events if e.type == "turn_started" and e.payload["turn_id"] == "turn-0002")
    # Same speaker retakes in the same (opening) phase.
    first = next(e for e in events if e.type == "turn_started" and e.payload["turn_id"] == "turn-0001")
    assert retake.payload["persona"] == first.payload["persona"]
    assert retake.payload["phase"] == first.payload["phase"] == "opening"
    # The whole stream remains schema-valid and complete.
    assert events[-1].type == "debate_completed"
    assert not inbox.has_interrupt()


def test_targeted_interrupt_matches_normalized_speaker_and_lands_for_retake(
    tmp_path, binding_mocks, monkeypatch
):
    inbox = SteeringInbox(id_prefix="it")
    message = "Rebuild the downside case before answering."
    contexts: list[str] = []
    submitted = False

    def act_and_capture_context(self, *, return_actions=False, **kwargs):
        nonlocal submitted
        if self.name == "Warren Buffett":
            contexts.append(repr(self.episodic_memory.retrieve_all()))
            if not submitted:
                submitted = inbox.request_interrupt(
                    text=message,
                    target="  warren buffett ",
                    source="stdin",
                )
        return _act_via_binding(self, return_actions=return_actions, **kwargs)

    monkeypatch.setattr(InvestorPersona, "act", act_and_capture_context)

    events = _run_logged(
        _fake_committee(),
        inbox,
        tmp_path,
        debate_id="aapl-20260715-targeted-int",
    )

    assert submitted is True
    interrupted = [e for e in events if e.type == "turn_interrupted"]
    assert len(interrupted) == 1
    assert interrupted[0].payload == {
        "turn_id": "turn-0001",
        "persona": "Warren Buffett",
        "by": "user",
        "disposition": "discarded_on_arrival",
    }
    turns = [e.payload for e in events if e.type == "turn_started"]
    assert turns[0]["persona"] == turns[1]["persona"] == "Warren Buffett"
    assert turns[0]["phase"] == turns[1]["phase"] == "opening"
    assert message not in contexts[0]
    assert message in contexts[1]
    assert not inbox.has_interrupt()


def test_interrupt_arriving_during_retake_waits_for_next_speaker_check(
    tmp_path, binding_mocks, monkeypatch
):
    inbox = SteeringInbox(id_prefix="ir")
    warren_attempts = 0

    def act_and_interrupt_each_warren_attempt(
        self, *, return_actions=False, **kwargs
    ):
        nonlocal warren_attempts
        if self.name == "Warren Buffett":
            warren_attempts += 1
            if warren_attempts == 1:
                inbox.request_interrupt(text="Force Warren's retake.")
            elif warren_attempts == 2:
                # The attempt==0 gate must leave this armed during Warren's
                # retake. Benjamin is the next speaker, so it fires only after
                # Benjamin's own model call returns.
                inbox.request_interrupt(
                    text="Force Benjamin's retake.",
                    target="Benjamin Graham",
                )
        return _act_via_binding(self, return_actions=return_actions, **kwargs)

    monkeypatch.setattr(
        InvestorPersona,
        "act",
        act_and_interrupt_each_warren_attempt,
    )

    events = _run_logged(
        _fake_committee(),
        inbox,
        tmp_path,
        debate_id="aapl-20260715-retake-interrupt",
    )

    interrupted = [e.payload for e in events if e.type == "turn_interrupted"]
    assert [payload["persona"] for payload in interrupted] == [
        "Warren Buffett",
        "Benjamin Graham",
    ]
    assert [payload["turn_id"] for payload in interrupted] == [
        "turn-0001",
        "turn-0003",
    ]
    completed_ids = {
        e.payload["turn_id"] for e in events if e.type == "turn_completed"
    }
    assert {"turn-0001", "turn-0003"}.isdisjoint(completed_ids)
    assert {"turn-0002", "turn-0004"} <= completed_ids
    assert not inbox.has_interrupt()


def test_targeted_interrupt_mismatch_expires_before_targets_later_turn(
    tmp_path, binding_mocks, monkeypatch, caplog
):
    inbox = SteeringInbox(id_prefix="im")
    submitted = False

    def act_and_interrupt_other_speaker(self, *, return_actions=False, **kwargs):
        nonlocal submitted
        if self.name == "Benjamin Graham" and not submitted:
            submitted = inbox.request_interrupt(
                text="This must not fire later.",
                target="Charlie Munger",
                source="stdin",
            )
        return _act_via_binding(self, return_actions=return_actions, **kwargs)

    monkeypatch.setattr(InvestorPersona, "act", act_and_interrupt_other_speaker)
    with caplog.at_level(logging.WARNING, logger="tinyic.debate.steering"):
        events = _run_logged(
            _fake_committee(),
            inbox,
            tmp_path,
            debate_id="aapl-20260715-mismatched-int",
        )

    assert submitted is True
    assert [e for e in events if e.type == "turn_interrupted"] == []
    started_ids = {
        e.payload["turn_id"] for e in events if e.type == "turn_started"
    }
    completed_ids = {
        e.payload["turn_id"] for e in events if e.type == "turn_completed"
    }
    assert len(started_ids) == 12
    assert completed_ids == started_ids
    assert not inbox.has_interrupt()
    warnings = [
        record
        for record in caplog.records
        if record.name == "tinyic.debate.steering"
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "not in flight" in warnings[0].getMessage()
    assert "Charlie Munger" in warnings[0].getMessage()
    assert "Benjamin Graham" in warnings[0].getMessage()


@pytest.mark.parametrize("target", ["Warren Buffet", "   "])
def test_unknown_target_interrupt_warns_once_and_emits_no_event(
    tmp_path, binding_mocks, caplog, target
):
    inbox = SteeringInbox(id_prefix="iu")
    inbox.request_interrupt(
        text="A typo must be harmless.",
        target=target,
        source="stdin",
    )

    with caplog.at_level(logging.WARNING, logger="tinyic.debate.steering"):
        events = _run_logged(
            _fake_committee(),
            inbox,
            tmp_path,
            debate_id="aapl-20260715-unknown-int",
        )

    assert [e for e in events if e.type == "turn_interrupted"] == []
    assert sum(e.type == "turn_started" for e in events) == 12
    assert sum(e.type == "turn_completed" for e in events) == 12
    assert not inbox.has_interrupt()
    warnings = [
        record
        for record in caplog.records
        if record.name == "tinyic.debate.steering"
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "unknown target" in warnings[0].getMessage()
    assert repr(target) in warnings[0].getMessage()


def test_interrupt_slot_latest_request_wins_across_targeting_modes():
    known = {"Warren Buffett", "Benjamin Graham", "Charlie Munger"}

    targeted_wins = SteeringInbox(id_prefix="ilt")
    targeted_wins.request_interrupt(text="old untargeted")
    targeted_wins.request_interrupt(
        text="new targeted",
        target="Warren Buffett",
        source="api",
    )
    targeted = targeted_wins.take_interrupt(
        in_flight="Warren Buffett",
        known_targets=known,
    )
    assert targeted is not None
    assert targeted.text == "new targeted"
    assert targeted.target == "Warren Buffett"
    assert targeted.source == "api"
    assert targeted_wins.take_interrupt(
        in_flight="Warren Buffett",
        known_targets=known,
    ) is None

    untargeted_wins = SteeringInbox(id_prefix="ilu")
    untargeted_wins.request_interrupt(
        text="old targeted",
        target="Charlie Munger",
    )
    untargeted_wins.request_interrupt(text="new untargeted", source="api")
    untargeted = untargeted_wins.take_interrupt(
        in_flight="Benjamin Graham",
        known_targets=known,
    )
    assert untargeted is not None
    assert untargeted.text == "new untargeted"
    assert untargeted.target is None
    assert untargeted.source == "api"
    assert untargeted_wins.take_interrupt(
        in_flight="Benjamin Graham",
        known_targets=known,
    ) is None


# ==========================================================================
# (FR-6.2) STDIN steering reader
# ==========================================================================


def test_stdin_reader_parses_and_feeds_the_inbox(tmp_path):
    from tinyic.headless import _steer_stdin_reader

    debate_id = "aapl-20260713-stdin"
    log = EventLog(debate_id, path=tmp_path / f"{debate_id}.jsonl")
    log.emit("debate_started", _minimal_started_payload())  # so the reader proceeds
    inbox = SteeringInbox(event_log=log, id_prefix="s")

    stdin = io.StringIO(
        '{"type": "steer", "target": "Warren Buffett", "text": "focus on moat"}\n'
        '{"type": "queue", "text": "cite a multiple"}\n'
        "not-json-should-be-ignored\n"
        '{"type": "bogus", "text": "ignored"}\n'
        '{"type": "interrupt", "text": "stop and rethink"}\n'
    )
    err = io.StringIO()
    # Runs synchronously to EOF (StringIO), so delivery order is deterministic.
    _steer_stdin_reader(stdin, inbox, log, err, threading.Event())

    events = read_event_log(tmp_path / f"{debate_id}.jsonl")
    submits = [e for e in events if e.type == "steering_submitted"]
    assert [e.payload["mode"] for e in submits] == ["steer", "queue"]
    assert submits[0].payload["target_persona"] == "Warren Buffett"
    assert inbox.has_interrupt()  # the interrupt line armed an interrupt
    assert "non-JSON" in err.getvalue() or "unknown type" in err.getvalue()


@pytest.mark.parametrize("target", [0, 42])
def test_stdin_reader_malformed_interrupt_target_is_unknown_noop(
    tmp_path, caplog, target
):
    from tinyic.headless import _steer_stdin_reader

    debate_id = f"aapl-20260715-stdin-target-{target}"
    log_path = tmp_path / f"{debate_id}.jsonl"
    log = EventLog(debate_id, path=log_path)
    log.emit("debate_started", _minimal_started_payload())
    inbox = SteeringInbox(event_log=log, id_prefix="x")
    stdin = io.StringIO(
        json.dumps(
            {
                "type": "interrupt",
                "target": target,
                "text": "must remain non-destructive",
            }
        )
        + "\n"
    )

    _steer_stdin_reader(
        stdin,
        inbox,
        log,
        io.StringIO(),
        threading.Event(),
    )

    assert inbox.has_interrupt()
    with caplog.at_level(logging.WARNING, logger="tinyic.debate.steering"):
        assert inbox.take_interrupt(
            in_flight="Warren Buffett",
            known_targets={"Warren Buffett"},
        ) is None
    assert not inbox.has_interrupt()
    warnings = [
        record
        for record in caplog.records
        if record.name == "tinyic.debate.steering"
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "unknown target" in warnings[0].getMessage()
    assert repr(target) in warnings[0].getMessage()

    inbox.close()
    log.emit(
        "debate_completed",
        {"phases_completed": [], "duration_s": 0.0, "result_ref": str(log_path)},
    )
    log.close()
    assert [e for e in read_event_log(log_path) if e.type == "turn_interrupted"] == []


def test_steer_stdin_end_to_end_runs_and_stays_clean(tmp_path, binding_mocks):
    # steer_stdin=True must wire the reader thread without crashing; delivery is
    # made deterministic via a pre-loaded inbox, and stdout stays pure JSONL.
    inbox = SteeringInbox(id_prefix="e")
    inbox.submit("steer", "an early steer")
    out, err = io.StringIO(), io.StringIO()
    stdin = io.StringIO('{"type": "queue", "text": "from stdin"}\n')
    code = run_debate_command(
        "AAPL",
        personas=_PERSONAS_CSV,
        headless=True,
        json_mode=True,
        steer_stdin=True,
        committee=_fake_committee(),
        data_package=_mock_data_package(),
        steering=inbox,
        out=out,
        err=err,
        stdin=stdin,
    )
    assert code == 0
    events = [EventEnvelope.model_validate(json.loads(l)) for l in _jsonl_lines(out.getvalue())]
    # The pre-loaded steer was delivered; the run is complete and clean.
    assert any(e.type == "steering_submitted" for e in events)
    assert events[-1].type == "debate_completed"


# ==========================================================================
# (FR-6.1) runs list + result document
# ==========================================================================


def test_runs_list_and_result_document_from_a_recorded_debate(tmp_path, binding_mocks):
    out, err = io.StringIO(), io.StringIO()
    run_debate_command(
        "AAPL",
        personas=_PERSONAS_CSV,
        headless=True,
        json_mode=True,
        committee=_fake_committee(),
        data_package=_mock_data_package(),
        out=out,
        err=err,
    )
    debate_id = json.loads(_jsonl_lines(out.getvalue())[0])["debate_id"]

    # runs list finds the recorded debate.
    runs = list_runs()
    assert any(r["debate_id"] == debate_id and r["status"] == "complete" for r in runs)

    # result assembles a single document from the log (schema promise #3).
    document = load_result(debate_id)
    assert document["debate_id"] == debate_id
    assert document["status"] == "complete"
    assert document["ticker"] == "AAPL"
    assert document["scorecard"]["consensus"] in {"BUY", "HOLD", "SELL", None}
    assert len(document["votes"]) == 3
    assert list(document["memo"]) == [
        "executive_summary",
        "investment_thesis",
        "key_risks",
        "valuation_discussion",
        "final_verdict",
    ]
    # Usage rollup is model-attributed across the debate turns.
    assert document["usage"]["total"]["input_tokens"] > 0
    assert "openai/gpt-5.2" in document["usage"]["by_model"]


def test_cli_debate_dispatches_all_flags_to_the_runner(monkeypatch):
    import tinyic.headless as headless_module
    from tinyic.cli import main

    captured: dict = {}

    def fake_runner(query, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(headless_module, "run_debate_command", fake_runner)
    code = main(
        [
            "debate",
            "AAPL",
            "--headless",
            "--json",
            "--steer-stdin",
            "--phase-step",
            "--no-research",
            "--yes",
            "--personas",
            "warren_buffett,charlie_munger",
            "--preset",
            "fast",
            "--model",
            "openai/gpt-5.2",
            "--thinking",
            "high",
            "--da",
            "charlie_munger",
            "--port",
            "8765",
            "--no-open",
            "--no-wait",
        ]
    )
    assert code == 0
    assert captured["query"] == "AAPL"
    assert captured["headless"] and captured["json_mode"]
    assert captured["steer_stdin"] and captured["phase_step"]
    assert captured["no_research"] and captured["yes"]
    assert captured["personas"] == "warren_buffett,charlie_munger"
    assert captured["preset"] == "fast"
    assert captured["model"] == "openai/gpt-5.2"
    assert captured["thinking"] == "high"
    assert captured["da"] == "charlie_munger"
    assert captured["port"] == 8765
    assert captured["no_open"] and captured["no_wait"]


def test_cli_runs_list_and_result_json(binding_mocks, capsys):
    run_debate_command(
        "AAPL",
        personas=_PERSONAS_CSV,
        headless=True,
        json_mode=False,
        committee=_fake_committee(),
        data_package=_mock_data_package(),
        out=io.StringIO(),
        err=io.StringIO(),
    )
    from tinyic.cli import main

    capsys.readouterr()  # clear anything accumulated during the run
    assert main(["runs", "list", "--json"]) == 0
    runs_payload = json.loads(capsys.readouterr().out)
    assert runs_payload["runs"], "runs list should find the recorded debate"
    debate_id = runs_payload["runs"][0]["debate_id"]

    assert main(["result", debate_id, "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["debate_id"] == debate_id
    assert document["status"] == "complete"

    # An unknown id is a clean setup-style error (exit 3), never a crash.
    assert main(["result", "does-not-exist", "--json"]) == 3


def test_fresh_process_headless_json_keeps_stdout_clean(tmp_path):
    """A *fresh* process imports TinyTroupe (which prints a disclaimer + config
    dump) and logs — none of it may reach STDOUT under ``--headless --json``."""
    import os
    import subprocess
    import sys

    config = tmp_path / "tinyic.toml"
    config.write_text(
        'default_preset = "default"\n[presets.default]\nmodel = "bogus/nope"\n',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["TINYIC_RUNS_DIR"] = str(tmp_path / "runs")
    env["TINYIC_STATE_DIR"] = str(tmp_path / "state")
    program = (
        "import sys; from tinyic.cli import main; "
        "sys.exit(main(['debate', 'AAPL', '--headless', '--json', "
        f"'--config', {str(config)!r}]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", program],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    # The one-time import banner + config dump must be OFF stdout entirely.
    assert "DISCLAIMER" not in proc.stdout
    assert "TinyTroupe configuration" not in proc.stdout
    # Every non-empty stdout line is a schema-valid event — zero non-JSON lines.
    for line in proc.stdout.splitlines():
        if line.strip():
            EventEnvelope.model_validate(json.loads(line))
    # A bogus provider fails at setup (no phase completed) -> exit 3.
    assert proc.returncode == 3
    # The reason (and the banner) went to stderr, where humans read it.
    assert "setup failed" in proc.stderr.lower()


def test_stream_log_stops_when_worker_finishes_without_a_terminal_event(tmp_path):
    # A crashed debate may leave a log with no terminal event; the tailer must
    # still stop once the worker signals done, rather than tailing forever.
    from tinyic.headless import _stream_log

    debate_id = "aapl-20260713-notrm"
    log = EventLog(debate_id, path=tmp_path / f"{debate_id}.jsonl")
    log.emit("debate_started", _minimal_started_payload())
    log.emit("phase_started", {"phase": "opening", "index": 0})
    log.close()  # no debate_completed / debate_error

    out, err = io.StringIO(), io.StringIO()
    done = threading.Event()
    done.set()  # worker already finished
    _stream_log(log.path, out, err, done)  # must return promptly, not hang
    lines = _jsonl_lines(out.getvalue())
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "debate_started"


def test_worker_forwards_no_research_to_debate_pipeline(tmp_path, monkeypatch):
    from tinyic.headless import _run_worker

    captured = {}

    def fake_run_debate(_ticker, _personas, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(debate_module, "run_debate", fake_run_debate)
    log = EventLog("aapl-20260714-nors", path=tmp_path / "run.jsonl")
    done = threading.Event()
    _run_worker(
        ticker="AAPL",
        persona_names=["a", "b"],
        log=log,
        inbox=object(),
        preset=None,
        model=None,
        thinking=None,
        da=None,
        no_research=True,
        committee=None,
        data_package=None,
        transport_factory=None,
        credentials=None,
        config_path=None,
        phase_gate=None,
        result_holder={},
        done=done,
    )
    assert done.is_set()
    assert captured["deep_research"] is False


def test_web_path_wires_engine_controls_and_finalizes(binding_mocks, monkeypatch):
    # Exercise the default web branch with the server lifecycle stubbed. The
    # debate worker remains real and consumes the same JSONL log as production.
    from tinyic.debate.control import RunControl
    from tinyic.debate.steering import SteeringInbox
    from tinyic.web import WebFace

    captured: dict = {}

    class _StubFace:
        base_url = "http://127.0.0.1:4567"

        def start(self):
            captured["started"] = True

        def mark_run_finished(self):
            captured["finished"] = True

        def wait_for_sse_disconnect(self, timeout=None):
            captured["disconnect_timeout"] = timeout
            return True

        def shutdown(self, *, timeout=None):
            captured["shutdown_timeout"] = timeout

    def fake_live(log, *, inbox=None, control=None, **_kwargs):
        captured["log"] = log
        captured["inbox"] = inbox
        captured["control"] = control
        return _StubFace()

    monkeypatch.setattr(WebFace, "live", staticmethod(fake_live))

    code = run_debate_command(
        "AAPL",
        personas=_PERSONAS_CSV,
        committee=_fake_committee(),
        data_package=_mock_data_package(),
        interactive=True,
        no_open=True,
        no_wait=True,
        out=io.StringIO(),
        err=io.StringIO(),
    )
    assert code == 0
    assert captured.get("started") is True
    assert captured.get("finished") is True
    assert isinstance(captured["inbox"], SteeringInbox)
    assert isinstance(captured["control"], RunControl)
    assert captured["disconnect_timeout"] is None
    assert captured["shutdown_timeout"] == 1.0


def test_web_path_keeps_stdout_quiet_with_communication_display_on(
    binding_mocks, monkeypatch, capsys
):
    # TIC-010 regression: the vendored TinyTroupe communication display is ON by
    # default, and the moderator/reinforcement ``listen`` briefs render through the
    # real ``_observe`` path even while ``InvestorPersona.act`` is stubbed. The web
    # branch must hold ``redirect_stdout`` across the whole worker run so those
    # renders land on STDERR, never the real STDOUT machine channel.
    from tinyic.web import WebFace

    # Re-enable the vendored default the fixture masks: without the display on,
    # the briefs never render and the leak cannot reproduce.
    monkeypatch.setattr(TinyPerson, "communication_display", True)

    class _StubFace:
        base_url = "http://127.0.0.1:4567"

        def start(self):
            pass

        def mark_run_finished(self):
            pass

        def wait_for_sse_disconnect(self, timeout=None):
            return True

        def shutdown(self, *, timeout=None):
            pass

    monkeypatch.setattr(
        WebFace, "live", staticmethod(lambda _log, **_kwargs: _StubFace())
    )

    committee = _fake_committee()
    data_package = _mock_data_package()
    err = io.StringIO()
    capsys.readouterr()  # discard any construction-time banner before the run
    code = run_debate_command(
        "AAPL",
        personas=_PERSONAS_CSV,
        committee=committee,
        data_package=data_package,
        interactive=True,
        no_open=True,
        no_wait=True,
        out=io.StringIO(),
        err=err,
    )

    assert code == 0
    # The real process STDOUT (what a pipe/agent reads) stays empty ...
    assert capsys.readouterr().out == ""
    # ... while the per-turn renders were produced and routed to the human channel.
    assert "-->" in err.getvalue()


def test_web_path_reports_occupied_port_before_starting_worker():
    err = io.StringIO()
    with socket.create_server(("127.0.0.1", 0)) as occupied:
        port = occupied.getsockname()[1]
        code = run_debate_command(
            "AAPL",
            committee=object(),
            interactive=True,
            port=port,
            no_open=True,
            no_wait=True,
            out=io.StringIO(),
            err=err,
        )
    assert code == 3
    diagnostic = err.getvalue()
    assert f"127.0.0.1:{port}" in diagnostic
    assert "could not bind" in diagnostic
    assert "Traceback" not in diagnostic


def test_web_sigint_does_not_enter_no_wait_attach_or_drain(monkeypatch):
    from tinyic.web import WebFace

    calls: list[str] = []

    class StubFace:
        def start(self):
            calls.append("start")

        def mark_run_finished(self):
            calls.append("finished")

        def wait_for_sse_disconnect(self, timeout=None):
            calls.append("disconnect")

        def wait_for_sse_cycle(self, **_kwargs):
            calls.append("cycle")

        def shutdown(self, *, timeout=None):
            calls.append("shutdown")

    monkeypatch.setattr(
        WebFace,
        "live",
        staticmethod(lambda _log, **_kwargs: StubFace()),
    )

    def finish_worker(**kwargs):
        kwargs["done"].set()

    def interrupt_stream(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(headless_module, "_run_worker", finish_worker)
    monkeypatch.setattr(headless_module, "_stream_log", interrupt_stream)

    assert (
        run_debate_command(
            "AAPL",
            committee=object(),
            interactive=True,
            no_wait=True,
            no_open=False,
            out=io.StringIO(),
            err=io.StringIO(),
        )
        == 3
    )
    assert calls == ["start", "finished", "shutdown"]


def test_assemble_result_reports_truncated_log_as_incomplete(tmp_path):
    debate_id = "aapl-20260713-trunc"
    log = EventLog(debate_id, path=tmp_path / f"{debate_id}.jsonl")
    log.emit("debate_started", _minimal_started_payload())
    log.emit("phase_started", {"phase": "opening", "index": 0})
    log.close()  # crash before a terminal event
    document = assemble_result(read_event_log(tmp_path / f"{debate_id}.jsonl"))
    assert document["status"] == "incomplete"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _minimal_started_payload() -> dict:
    return {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "preset": "default",
        "personas": [
            {
                "name": "Warren Buffett",
                "model_ref": "openai/gpt-5.2",
                "auth_profile": "openai:default",
                "thinking_level": "high",
                "temperament": "balanced",
            }
        ],
        "moderator": "rules",
        "aggregator": "openai/gpt-5.2",
        "caps": {"opening": 1, "cross_exam": 2, "rebuttal": 1, "verdict": 1},
        "config_hash": "sha256:0",
        "tinyic_version": "0.1.0",
    }
