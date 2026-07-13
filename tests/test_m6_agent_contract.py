"""M6 Definition-of-Done: the headless **agent contract**, proven end to end as a
real subprocess (PRD §12 M6 row; success criterion 3; FR-6.2; ``docs/event-schema.md``).

Every other M6 test drives the engine in-process. This one is the gate the PRD
actually promises an *external agent*: ``tinyic`` is launched **as a fresh child
process** with real STDOUT / STDERR / STDIN pipes, real worker + tailer +
stdin-reader threads, a real event-log file, and a real process exit code — the
exact surface Claude Code / Codex sees. Nothing here touches the network: the
child (``tests/support/agent_contract_driver``) installs the same recorded-SSE
wire transports + deterministic extraction mocks the M2/M6 suites use (a subprocess
cannot receive a pytest ``monkeypatch``), the offline stand-in for a live provider.

The headline (``test_agent_drives_a_full_debate_via_json_and_steer_stdin``) mirrors
what an agent script would do: spawn ``--headless --json --steer-stdin``, feed a
steer line and an interrupt over STDIN mid-run, and assert

* every STDOUT line is schema-valid v1 JSONL — zero non-JSON lines (the import
  banner + config dump the vendored engine prints go to STDERR only);
* the steer is acknowledged then delivered (``steering_submitted`` →
  ``steering_delivered``, ack before delivery), sourced ``stdin``;
* the interrupt yields exactly one ``turn_interrupted`` with **engine-authoritative**
  provenance (``by: user``, ``disposition: discarded_on_arrival``) and the speaker
  retakes;
* the process exits ``0``; and
* a *separate* ``tinyic result <id> --json`` subprocess returns the assembled
  document (schema promise #3).

Plus the two failure exit codes an agent must be able to branch on: ``3`` when auth
is missing (clean, secret-free ``doctor``-style reason on STDERR; pristine STDOUT)
and ``2`` when the debate fails after ≥1 phase.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tinyic.events import EventEnvelope

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER_MODULE = "tests.support.agent_contract_driver"

# The three-persona committee the offline driver convenes (display names), in
# speaking order — used to assert the interrupt's retake is the same speaker.
_SPEAKING_ORDER = ["Warren Buffett", "Benjamin Graham", "Charlie Munger"]

_STEER_LINE = json.dumps(
    {"type": "steer", "target": "Warren Buffett", "text": "Press on the balance sheet."}
)
_INTERRUPT_LINE = json.dumps(
    {"type": "interrupt", "text": "Reconsider the downside first."}
)


# --------------------------------------------------------------------------- #
# Subprocess harness
# --------------------------------------------------------------------------- #

def _child_env(tmp_path: Path, *, drop_provider_keys: bool = False) -> dict:
    """A hermetic child environment: isolated run/state dirs, no keyring, no keys.

    ``TINYIC_RUNS_DIR`` is shared between the debate run and the later
    ``tinyic result`` invocation. The null keyring backend makes credential
    resolution deterministic regardless of the developer's stored profiles, and
    ``drop_provider_keys`` additionally clears ambient provider API keys so the
    missing-auth path is reproducible on a machine that has them exported.
    """
    import os

    env = dict(os.environ)
    env["TINYIC_RUNS_DIR"] = str(tmp_path / "runs")
    env["TINYIC_STATE_DIR"] = str(tmp_path / "state")
    # Neutralize the OS keyring so no real stored profile can be discovered.
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    if drop_provider_keys:
        for key in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "XAI_API_KEY",
            "GOOGLE_API_KEY",
            "DEEPSEEK_API_KEY",
        ):
            env.pop(key, None)
    return env


def _run_driver(
    tmp_path: Path, *, stdin_text: str = "", extra_args: tuple[str, ...] = ()
) -> subprocess.CompletedProcess:
    """Run the offline debate driver as a real child process.

    A run that feeds steering over stdin also asks the driver to *await* it
    (``--await-steering``): that installs the one-shot first-turn barrier so the
    steer/interrupt land deterministically. A run with empty stdin omits the flag
    so the driver never parks turn-0001 waiting on commands that never arrive.
    The timeout sits under the 120s per-test pytest ceiling so a genuine hang
    surfaces as a ``TimeoutExpired`` (with captured output) rather than a bare
    pytest-timeout kill.
    """
    args = list(extra_args)
    if stdin_text.strip():
        args.append("--await-steering")
    return subprocess.run(
        [sys.executable, "-m", DRIVER_MODULE, *args],
        cwd=REPO_ROOT,
        env=_child_env(tmp_path),
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=90,
    )


def _run_cli(
    argv: list[str], env: dict, *, stdin_text: str = ""
) -> subprocess.CompletedProcess:
    """Run the real ``tinyic`` CLI (``cli.main``) as a fresh child process."""
    program = (
        "import sys; from tinyic.cli import main; sys.exit(main(sys.argv[1:]))"
    )
    return subprocess.run(
        [sys.executable, "-c", program, *argv],
        cwd=REPO_ROOT,
        env=env,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=90,
    )


def _parse_jsonl_events(stdout: str) -> list[EventEnvelope]:
    """Validate every non-empty STDOUT line as a schema-v1 event; return them.

    Raises on the *first* non-JSON or schema-invalid line, which is exactly the
    contract: STDOUT under ``--json`` carries nothing but event JSONL.
    """
    events: list[EventEnvelope] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)  # raises on any non-JSON line
        events.append(EventEnvelope.model_validate(obj))  # raises on schema break
    return events


# ==========================================================================
# The DoD centerpiece
# ==========================================================================


def test_agent_drives_a_full_debate_via_json_and_steer_stdin(tmp_path):
    proc = _run_driver(
        tmp_path, stdin_text=_STEER_LINE + "\n" + _INTERRUPT_LINE + "\n"
    )

    # (1) Exit 0 — the whole debate completed.
    assert proc.returncode == 0, proc.stderr

    # (2) STDOUT is pure, schema-valid v1 JSONL — every line, zero exceptions.
    events = _parse_jsonl_events(proc.stdout)
    assert events, "expected a streamed event log on stdout"
    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_completed"
    types = {e.type for e in events}
    assert "debate_error" not in types
    # A real four-phase debate actually ran (not a stub): all phases completed.
    assert sum(e.type == "phase_completed" for e in events) == 4

    # The vendored import banner + config dump never reached the machine channel.
    assert "DISCLAIMER" not in proc.stdout
    assert "TinyTroupe configuration" not in proc.stdout
    # ...they went to STDERR, where a human reads progress.
    assert "debate complete" in proc.stderr

    # (3) The steer: acknowledged, then delivered — ack strictly precedes delivery.
    submits = [e for e in events if e.type == "steering_submitted"]
    delivers = [e for e in events if e.type == "steering_delivered"]
    assert len(submits) == 1 and len(delivers) == 1
    submit, deliver = submits[0], delivers[0]
    assert submit.payload["mode"] == "steer"
    assert submit.payload["source"] == "stdin"  # it came from the STDIN reader
    assert submit.payload["target_persona"] == "Warren Buffett"
    assert submit.payload["msg_id"] == deliver.payload["msg_id"]
    assert submit.seq < deliver.seq
    # Delivered at a real speaker-turn boundary (references an actual turn).
    started_turn_ids = {
        e.payload["turn_id"] for e in events if e.type == "turn_started"
    }
    assert deliver.payload["delivered_before_turn_id"] in started_turn_ids

    # (4) The interrupt: exactly one turn_interrupted, engine-authoritative.
    interrupts = [e for e in events if e.type == "turn_interrupted"]
    assert len(interrupts) == 1
    ipayload = interrupts[0].payload
    assert ipayload["by"] == "user"  # the engine, not the renderer, sets this
    assert ipayload["disposition"] == "discarded_on_arrival"
    interrupted_turn = ipayload["turn_id"]

    # The discarded turn never committed; the same speaker retakes and commits.
    completed = {
        e.payload["turn_id"]: e.payload["persona"]
        for e in events
        if e.type == "turn_completed"
    }
    assert interrupted_turn not in completed
    started = {
        e.payload["turn_id"]: e.payload["persona"]
        for e in events
        if e.type == "turn_started"
    }
    interrupted_speaker = started[interrupted_turn]
    assert interrupted_speaker == _SPEAKING_ORDER[0]  # first opening speaker
    # The retake is the next turn by the same speaker, and it does commit.
    retake_turn = f"turn-{int(interrupted_turn.split('-')[1]) + 1:04d}"
    assert completed.get(retake_turn) == interrupted_speaker

    # (5) `tinyic result <id> --json` — a *separate* process — returns the document.
    debate_id = events[0].debate_id
    result = _run_cli(
        ["result", debate_id, "--json"], _child_env(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    document = _one_json_document(result.stdout)
    assert document["debate_id"] == debate_id
    assert document["status"] == "complete"
    assert document["ticker"] == "AAPL"
    assert len(document["votes"]) == 3
    assert document["scorecard"]["consensus"] in {"BUY", "HOLD", "SELL"}
    assert list(document["memo"]) == [
        "executive_summary",
        "investment_thesis",
        "key_risks",
        "valuation_discussion",
        "final_verdict",
    ]
    # The usage rollup is real, per-model-attributed numbers (success criterion 4).
    assert document["usage"]["total"]["input_tokens"] > 0
    assert "openai/gpt-5.2" in document["usage"]["by_model"]


# ==========================================================================
# Exit-code contract an agent branches on: 3 (missing auth) and 2 (partial)
# ==========================================================================


def test_missing_auth_exits_3_with_clean_reason_on_stderr(tmp_path):
    # A real provider preset with no resolvable credential (keyring neutralized,
    # ambient keys cleared): setup fails before any phase -> exit 3. This drives
    # the *actual* `tinyic` CLI, with --personas, so it also guards the STDOUT
    # banner-leak regression on the persona-resolution path.
    config = tmp_path / "tinyic.toml"
    config.write_text(
        '[presets.default]\nmodel = "openai/gpt-5.2"\nthinking = "low"\n',
        encoding="utf-8",
    )
    proc = _run_cli(
        [
            "debate",
            "AAPL",
            "--headless",
            "--json",
            "--no-research",
            "--personas",
            "warren_buffett,charlie_munger,benjamin_graham,peter_lynch,howard_marks,li_lu",
            "--config",
            str(config),
        ],
        _child_env(tmp_path, drop_provider_keys=True),
    )

    assert proc.returncode == 3, proc.stderr
    # STDOUT stays a clean machine channel even on a setup failure — and the
    # banner never leaks past the --personas import (the regression guard).
    events = _parse_jsonl_events(proc.stdout)
    assert "DISCLAIMER" not in proc.stdout
    assert "TinyTroupe configuration" not in proc.stdout
    # The failed run is still replayable: it opens and closes the stream.
    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_error"

    # A doctor-style, secret-free reason lands on STDERR (never STDOUT).
    stderr = proc.stderr
    assert "setup failed" in stderr.lower()
    assert "missing_credential" in stderr
    # No secret material anywhere on either channel.
    assert "OPENAI_API_KEY" not in proc.stdout


def test_injected_mid_debate_failure_exits_2(tmp_path):
    # Every phase runs, then vote extraction is forced to fail -> a debate_error
    # after >=1 completed phase, which is the FR-6.2 "partial" exit code.
    proc = _run_driver(tmp_path, extra_args=("--fail-after-phases",))

    assert proc.returncode == 2, proc.stderr
    events = _parse_jsonl_events(proc.stdout)
    assert sum(e.type == "phase_completed" for e in events) == 4  # all phases ran
    assert events[-1].type == "debate_error"
    assert events[-1].payload["stage"] == "extraction"
    # The error message is a class name, never raw provider/credential detail.
    assert "extraction" in events[-1].payload["message"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _one_json_document(stdout: str) -> dict:
    """Parse a single-JSON-document STDOUT (``tinyic result --json``).

    Tolerates a possible trailing newline but asserts there is exactly one
    non-empty line and that it is the JSON object — proving the command's STDOUT
    is the document and nothing else.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got {len(lines)}"
    return json.loads(lines[0])
