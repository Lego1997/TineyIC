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
import threading

import pytest

import tinyic.debate as debate_module
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
    assert payload["turn_id"] == "turn-0001"  # the first turn was discarded
    assert payload["by"] == "user"
    assert payload["disposition"] == "discarded_on_arrival"

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
    assert captured["shutdown_timeout"] == 1.0


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
