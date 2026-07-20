"""Drive a ``tinyic debate`` run — web Town Hall or headless agent mode.

This is the M6 seam between the CLI verb and the engine.  It runs the debate in a
worker thread that appends to the JSONL event log, and then *watches that file*
exactly as any other process would (the follower/reader from
``tinyic.live`` / ``tinyic.tui.events``): the renderer and the headless
JSON stream both consume the file, never engine internals.

Two faces:

* **Web (the default):** serve the localhost Town Hall, wiring JSON POSTs to the
  engine-backed steering/run-control seams and SSE to the log.
* **Headless (**``--headless``\\ ** or** ``--json``\\ **):**
  with ``--json``, stream each event-log line to STDOUT verbatim *as it is
  written*; without it, run quietly.  Either way human-readable progress goes to
  STDERR only, and **STDOUT carries nothing but event JSONL** — the agent
  contract.  ``--steer-stdin`` reads JSON steering lines from STDIN and feeds the
  same inbox.

STDOUT hygiene: the vendored TinyTroupe import prints an AI disclaimer and a
config dump, its console logger is a second potential polluter, and its
communication display (per-turn act/listen renders, on by default) is a third.
M6 moved the console log handler to STDERR (``tinytroupe/utils/config.py``); both
faces additionally hold ``redirect_stdout`` across the *entire* worker run —
thread start through join — so any stray ``print`` or render lands on STDERR,
leaving STDOUT a clean machine channel.  Under ``--json`` the JSONL writer holds
the *real* stdout captured before the redirect.

Exit codes (FR-6.2): ``0`` complete · ``2`` partial (``debate_error`` after ≥1
phase completed) · ``3`` setup/auth error (nothing completed) — with a
doctor-style reason on STDERR and never a prompt.
"""

from __future__ import annotations

import codecs
import contextlib
import io
import json
import os
import select
import sys
import threading
import time
from pathlib import Path
from typing import Callable, TextIO

from .constants import MAX_PERSONAS, MIN_PERSONAS
from .events import EventLog, make_debate_id
from .result import debate_status, exit_code_for_status
from .tui.events import Event, parse_event, read_events

__all__ = [
    "DEFAULT_PERSONAS",
    "PersonaSelectionError",
    "resolve_personas",
    "run_debate_command",
    "run_replay_command",
]

#: The default six-member committee (PRD §1), in the canonical speaking order.
DEFAULT_PERSONAS = [
    "warren_buffett",
    "charlie_munger",
    "benjamin_graham",
    "peter_lynch",
    "howard_marks",
    "li_lu",
]

# Give an auto-opened browser a bounded chance to attach. Once a real client is
# present, ``--no-wait`` drains it without a second deadline.
_BROWSER_ATTACH_TIMEOUT_S = 5.0


class PersonaSelectionError(ValueError):
    """A ``--personas`` value naming an unknown or too-small committee."""


def resolve_personas(
    spec: str | None, *, config_path: str | Path | None = None
) -> list[str]:
    """Resolve a ``--personas a,b,c`` value (or ``None``) to registry names.

    ``None`` selects the user-overlay committee when present, otherwise the
    built-in six. A comma list always takes precedence.
    """
    from .personas.registry import list_personas, registry_snapshot

    if spec is None:
        try:
            from .models.presets import load_config

            configured = load_config(config_path).get("committee")
        except Exception as exc:
            raise PersonaSelectionError(f"invalid committee overlay: {exc}") from exc
        names = list(configured or DEFAULT_PERSONAS)
    else:
        names = [token.strip() for token in spec.split(",") if token.strip()]
    registry = registry_snapshot()
    unknown = [name for name in names if name not in registry]
    if unknown:
        raise PersonaSelectionError(
            f"unknown persona(s): {', '.join(unknown)}; "
            f"available: {', '.join(list_personas())}"
        )
    if len(names) < MIN_PERSONAS:
        raise PersonaSelectionError(
            f"a committee needs at least {MIN_PERSONAS} personas, got {len(names)}"
        )
    if len(names) > MAX_PERSONAS:
        raise PersonaSelectionError(
            f"a committee supports at most {MAX_PERSONAS} personas, got {len(names)}"
        )
    if len(set(names)) != len(names):
        raise PersonaSelectionError("a committee cannot contain duplicate personas")
    return names


# --------------------------------------------------------------------------- #
# Human-readable progress (STDERR)
# --------------------------------------------------------------------------- #

def _progress(err: TextIO, message: str) -> None:
    try:
        err.write(message + "\n")
        err.flush()
    except Exception:  # pragma: no cover - a broken stderr must not crash a debate
        pass


def _progress_for_event(event: Event) -> str | None:
    """A concise one-line human progress note for a lifecycle event, or ``None``."""
    payload = event.payload
    etype = event.type
    if etype == "debate_started":
        return (
            f"▶ debate {event.debate_id} — {payload.get('ticker', '?')} "
            f"({payload.get('company_name', '?')}), preset "
            f"{payload.get('preset', '?')}"
        )
    if etype == "data_ready":
        sources = payload.get("sources") or []
        ok = sum(1 for s in sources if isinstance(s, dict) and s.get("status") == "ok")
        return f"  data ready — {ok}/{len(sources)} sources ok"
    if etype == "phase_started":
        return f"  phase {payload.get('phase', '?')} started"
    if etype == "phase_completed":
        return (
            f"  phase {payload.get('phase', '?')} complete "
            f"({payload.get('turn_count', '?')} turns)"
        )
    if etype == "scorecard":
        return (
            f"  scorecard — consensus={payload.get('consensus', 'none')} "
            f"(bull {payload.get('bull_count', 0)} / "
            f"bear {payload.get('bear_count', 0)} / "
            f"hold {payload.get('hold_count', 0)})"
        )
    if etype == "debate_completed":
        return (
            f"✓ debate complete — {payload.get('duration_s', '?')}s, "
            f"phases {payload.get('phases_completed', [])}"
        )
    if etype == "debate_error":
        return (
            f"✗ debate error at stage '{payload.get('stage', '?')}': "
            f"{payload.get('message', '')}"
        )
    return None


# --------------------------------------------------------------------------- #
# Live log tailing → STDOUT (verbatim) + progress → STDERR
# --------------------------------------------------------------------------- #

def _stream_log(
    path: Path,
    out: TextIO | None,
    err: TextIO,
    worker_done: threading.Event,
    *,
    poll_interval: float = 0.02,
    on_interrupt: Callable[[], None] | None = None,
) -> None:
    """Tail the actively-written log; echo each complete line verbatim to ``out``.

    Mirrors :class:`~tinyic.live.EventLogFollower`'s tailing discipline (a
    binary read fed through an incremental UTF-8 decoder so a poll landing
    mid-character or mid-line waits for the rest), then writes each *complete*
    newline-terminated line to ``out`` exactly as written — no re-serialization —
    and a human progress note to ``err``. Stops after a terminal event or once the
    worker has finished and every flushed line has been echoed. A truncated
    trailing line (a crash between bytes) is left unwritten. When
    ``on_interrupt`` is supplied, Ctrl-C invokes it and tailing resumes from the
    same byte offset so graceful-stop usage and terminal events still reach the
    JSONL stream without duplicates.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    position = 0
    while True:
        try:
            if not path.exists():
                if worker_done.is_set():
                    return  # the debate ended before ever opening a log
                worker_done.wait(poll_interval)
                continue
            with path.open("rb") as handle:
                handle.seek(position)
                chunk = handle.read()
                position = handle.tell()
            if chunk:
                buffer += decoder.decode(chunk)
            # Process previously buffered complete lines even when this poll
            # read no new bytes. A caught Ctrl-C can occur after one line was
            # removed/written while later lines from the same chunk remain;
            # they must drain before worker_done permits return.
            while "\n" in buffer:
                raw, buffer = buffer.split("\n", 1)
                if not raw.strip():
                    continue
                if out is not None:
                    out.write(raw + "\n")
                    out.flush()
                event = parse_event(raw)
                if event is not None:
                    note = _progress_for_event(event)
                    if note is not None:
                        _progress(err, note)
                    if event.is_terminal:
                        return
            if chunk:
                continue
            # No new bytes right now.
            if worker_done.is_set():
                return  # worker flushed + closed the log before signalling done
            worker_done.wait(poll_interval)
        except KeyboardInterrupt:
            if on_interrupt is None:
                raise
            on_interrupt()


# --------------------------------------------------------------------------- #
# STDIN steering reader
# --------------------------------------------------------------------------- #

def _steer_stdin_reader(
    stdin: TextIO,
    inbox,
    log: EventLog,
    err: TextIO,
    stop_event: threading.Event,
    ready_event: threading.Event | None = None,
) -> None:
    """Read JSON steering lines from ``stdin`` and feed the engine inbox.

    Each line is ``{"type": "steer"|"queue"|"interrupt", "target"?, "text"?}``
    (``docs/event-schema.md``). ``steer``/``queue`` submit to the moderator's
    delivery queue (acknowledged by ``steering_submitted``); ``interrupt`` arms a
    hard interrupt. Pre-start submits remain safely queued: the inbox emits
    their deferred ``steering_submitted`` acknowledgement before delivery or a
    terminal drop. ``ready_event`` lets the command runner drain input already
    available at startup before launching a worker that could fail immediately.
    """
    def lines_until_stopped():
        """Yield lines without leaving a blocking stdin read behind at shutdown."""
        try:
            fd = stdin.fileno()
        except (AttributeError, OSError, ValueError):
            # In-memory streams used by programmatic callers are finite and do
            # not block; retain their normal iterator semantics.
            if not isinstance(stdin, io.StringIO):
                _progress(
                    err,
                    "  steer-stdin: input stream is not pollable; reader disabled",
                )
                return
            for buffered_line in stdin:
                if stop_event.is_set():
                    return
                yield buffered_line
            if ready_event is not None:
                ready_event.set()
            return

        encoding = getattr(stdin, "encoding", None) or "utf-8"
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        buffer = ""
        startup_drain = True
        while not stop_event.is_set():
            try:
                ready, _, _ = select.select(
                    [fd], [], [], 0.0 if startup_drain else 0.1
                )
            except (OSError, ValueError):
                return
            if not ready:
                if startup_drain:
                    startup_drain = False
                    if ready_event is not None:
                        ready_event.set()
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                return
            if not chunk:
                buffer += decoder.decode(b"", final=True)
                if buffer:
                    yield buffer
                return
            buffer += decoder.decode(chunk)
            while "\n" in buffer:
                buffered_line, buffer = buffer.split("\n", 1)
                yield buffered_line

    try:
        for line in lines_until_stopped():
            if stop_event.is_set():
                return
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                _progress(err, "  steer-stdin: ignoring non-JSON line")
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            target = obj.get("target")
            body = obj.get("text")
            if kind in ("steer", "queue"):
                if (
                    not isinstance(body, str)
                    or not body.strip()
                    or (
                        target is not None
                        and (not isinstance(target, str) or not target.strip())
                    )
                ):
                    _progress(
                        err,
                        f"  steer-stdin: ignoring malformed {kind!r} command",
                    )
                    continue
                inbox.submit(kind, body, target=target, source="stdin")
            elif kind == "interrupt":
                if (
                    (body is not None and not isinstance(body, str))
                    or (target is not None and not isinstance(target, str))
                ):
                    _progress(
                        err,
                        "  steer-stdin: ignoring malformed 'interrupt' command",
                    )
                    continue
                inbox.request_interrupt(text=body, target=target, source="stdin")
            else:
                _progress(err, f"  steer-stdin: ignoring unknown type {kind!r}")
    finally:
        if ready_event is not None:
            ready_event.set()


# --------------------------------------------------------------------------- #
# Debate worker
# --------------------------------------------------------------------------- #

def _run_worker(
    *,
    ticker: str,
    persona_names: list[str],
    log: EventLog,
    inbox,
    preset: str | None,
    model: str | None,
    thinking: str | None,
    da: str | None,
    no_research: bool,
    committee,
    data_package,
    transport_factory,
    credentials,
    config_path,
    phase_gate,
    result_holder: dict,
    done: threading.Event,
) -> None:
    """Run one debate to ``log``; capture any exception and signal completion.

    ``done`` is set in the ``finally`` **after** the log is flushed and closed, so
    a watcher (``_stream_log`` / the follower) can stop even when the debate never
    wrote a terminal event (a hard crash), instead of tailing forever.
    """
    from tinyic.debate import run_debate

    try:
        run_debate(
            ticker,
            persona_names,
            data_package=data_package,
            event_log=log,
            preset=preset,
            model=model,
            thinking=thinking,
            da=da,
            deep_research=not no_research,
            committee=committee,
            transport_factory=transport_factory,
            credentials=credentials,
            config_path=config_path,
            steering=inbox,
            phase_gate=phase_gate,
        )
    except BaseException as exc:  # noqa: BLE001 - surfaced via result_holder + exit code
        result_holder["error"] = exc
    finally:
        log.close()
        done.set()


def _finalize(
    log: EventLog,
    err: TextIO,
    result_holder: dict,
    *,
    run_doctor_reason: bool,
    preset: str | None,
    config_path,
) -> int:
    """Read the finished log, print a doctor-style reason on failure, return code."""
    try:
        events = read_events(log.path)
    except Exception:  # pragma: no cover - unreadable log => treat as failure
        events = []
    status = debate_status(events)
    code = exit_code_for_status(status)
    if code == 3:
        _emit_setup_reason(
            events,
            err,
            result_holder.get("error"),
            run_doctor_reason=run_doctor_reason,
            preset=preset,
            config_path=config_path,
        )
    return code


def _emit_setup_reason(
    events: list[Event],
    err: TextIO,
    worker_error: BaseException | None,
    *,
    run_doctor_reason: bool,
    preset: str | None,
    config_path,
) -> None:
    """Write a doctor-style reason for an exit-3 (setup/auth) failure to STDERR."""
    error_event = next((e for e in events if e.type == "debate_error"), None)
    if error_event is not None:
        _progress(
            err,
            f"tinyic: setup failed at stage "
            f"'{error_event.payload.get('stage', '?')}': "
            f"{error_event.payload.get('message', '')}",
        )
    elif worker_error is not None:
        _progress(err, f"tinyic: setup failed: {type(worker_error).__name__}")
    else:
        _progress(err, "tinyic: the debate did not complete")
    if not run_doctor_reason:
        return
    # Enrich with the reason-coded doctor summary so an auth gap is actionable.
    # The doctor probe pulls auth/provider modules that may print on import; run
    # it with STDOUT redirected to STDERR so nothing pollutes the machine channel.
    try:
        with contextlib.redirect_stdout(err):
            from tinyic.auth import doctor as doctor_module

            report = doctor_module.run_doctor(preset=preset, config_path=config_path)
        if not report.ok:
            _progress(err, doctor_module.render_human(report))
    except Exception:  # pragma: no cover - doctor must never mask the real error
        pass


# --------------------------------------------------------------------------- #
# Headless + web entry points
# --------------------------------------------------------------------------- #

def _should_use_web(
    interactive: bool | None, headless: bool, json_mode: bool, out: TextIO
) -> bool:
    if interactive is not None:
        return interactive
    return not (headless or json_mode)


def run_debate_command(
    ticker: str,
    *,
    personas: str | None = None,
    preset: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    da: str | None = None,
    no_research: bool = False,
    headless: bool = False,
    json_mode: bool = False,
    phase_step: bool = False,
    steer_stdin: bool = False,
    yes: bool = False,
    port: int = 0,
    no_open: bool = False,
    no_wait: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
    stdin: TextIO | None = None,
    # Test/advanced injection seams (never set by the CLI):
    committee=None,
    data_package=None,
    transport_factory=None,
    credentials=None,
    config_path=None,
    steering=None,
    browser_opener=None,
    interactive: bool | None = None,
) -> int:
    """Run ``tinyic debate`` and return its process exit code.

    ``out``/``err``/``stdin`` default to the process streams; the offline tests
    pass their own (and a pre-built ``committee``/``data_package``/``steering``)
    to drive a fully mocked debate. ``--yes``/``--headless`` guarantee no prompt.
    """
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    stdin = stdin if stdin is not None else sys.stdin

    try:
        # Resolving a ``--personas`` spec imports the persona registry, which
        # pulls TinyTroupe and prints its one-time import banner + config dump.
        # That happens *before* the engine run's own STDOUT->STDERR redirect, so
        # without this guard ``--headless --json --personas ...`` would leak the
        # banner onto STDOUT and break the machine channel (FR-6.2). Redirect the
        # resolution's STDOUT to STDERR too; the default committee needs no import
        # and is unaffected. STDERR stays the human channel for any banner.
        with contextlib.redirect_stdout(err):
            persona_names = (
                list(DEFAULT_PERSONAS)
                if personas is None and committee is not None
                else resolve_personas(personas, config_path=config_path)
            )
    except PersonaSelectionError as exc:
        _progress(err, f"tinyic: {exc}")
        return 3

    # M6: ``tinyic debate`` runs through the model layer by default. With no
    # preset/model/thinking (and no injected committee) resolve the ``default``
    # preset so every persona routes through its own ModelBinding rather than the
    # legacy process-global client. An injected committee takes precedence.
    if committee is None and preset is None and model is None and thinking is None:
        preset = "default"

    deep_research = not no_research
    use_web = _should_use_web(interactive, headless, json_mode, out)
    # The doctor-style enrichment on failure is only meaningful for a real,
    # config-resolved run — never when a test injects a committee/transports.
    run_doctor_reason = committee is None and transport_factory is None

    if use_web:
        return _run_web(
            ticker=ticker,
            persona_names=persona_names,
            preset=preset,
            model=model,
            thinking=thinking,
            da=da,
            deep_research=deep_research,
            phase_step=phase_step,
            err=err,
            committee=committee,
            data_package=data_package,
            transport_factory=transport_factory,
            credentials=credentials,
            config_path=config_path,
            steering=steering,
            run_doctor_reason=run_doctor_reason,
            port=port,
            no_open=no_open,
            no_wait=no_wait,
            browser_opener=browser_opener,
        )
    return _run_headless(
        ticker=ticker,
        persona_names=persona_names,
        preset=preset,
        model=model,
        thinking=thinking,
        da=da,
        deep_research=deep_research,
        json_mode=json_mode,
        steer_stdin=steer_stdin,
        out=out,
        err=err,
        stdin=stdin,
        committee=committee,
        data_package=data_package,
        transport_factory=transport_factory,
        credentials=credentials,
        config_path=config_path,
        steering=steering,
        run_doctor_reason=run_doctor_reason,
    )


def _run_headless(
    *,
    ticker: str,
    persona_names: list[str],
    preset: str | None,
    model: str | None,
    thinking: str | None,
    da: str | None,
    deep_research: bool,
    json_mode: bool,
    steer_stdin: bool,
    out: TextIO,
    err: TextIO,
    stdin: TextIO,
    committee,
    data_package,
    transport_factory,
    credentials,
    config_path,
    steering,
    run_doctor_reason: bool,
) -> int:
    """Run without the web viewer, streaming JSONL to STDOUT under ``--json``."""
    # Import the inbox lazily (its package pulls TinyTroupe, whose import prints);
    # do it under the redirect below so nothing lands on the real STDOUT.
    real_out = out  # the machine channel; captured before we redirect sys.stdout
    result_holder: dict = {}
    worker_done = threading.Event()

    with contextlib.redirect_stdout(err):
        from tinyic.debate.control import RunControl
        from tinyic.debate.steering import SteeringInbox

        log = EventLog(make_debate_id(ticker))
        inbox = steering if steering is not None else SteeringInbox()
        inbox.bind_event_log(log)
        control = RunControl()

        worker = threading.Thread(
            name="tinyic-debate-worker",
            target=_run_worker,
            kwargs=dict(
                ticker=ticker,
                persona_names=persona_names,
                log=log,
                inbox=inbox,
                preset=preset,
                model=model,
                thinking=thinking,
                da=da,
                no_research=not deep_research,
                committee=committee,
                data_package=data_package,
                transport_factory=transport_factory,
                credentials=credentials,
                config_path=config_path,
                phase_gate=control,
                result_holder=result_holder,
                done=worker_done,
            ),
            daemon=False,
        )
        stop_stdin = threading.Event()
        reader = None
        reader_ready = None
        if steer_stdin:
            reader_ready = threading.Event()
            reader = threading.Thread(
                name="tinyic-steer-stdin",
                target=_steer_stdin_reader,
                args=(stdin, inbox, log, err, stop_stdin, reader_ready),
                daemon=False,
            )

        # Tail the log: echo verbatim to the real STDOUT under --json, and always
        # surface human progress on STDERR. The worker sets ``worker_done`` from
        # its finally, so this returns even if no terminal event was ever written.
        def request_worker_stop() -> None:
            control.stop()
            inbox.request_interrupt(source="stdin")

        worker_started = False
        try:
            if reader is not None:
                reader.start()
                assert reader_ready is not None
                while not reader_ready.wait(0.1):
                    pass
            worker.start()
            worker_started = True
            _stream_log(
                log.path,
                real_out if json_mode else None,
                err,
                worker_done,
                on_interrupt=request_worker_stop,
            )
            while worker_started and worker.is_alive():
                try:
                    worker.join(timeout=0.1)
                except KeyboardInterrupt:
                    request_worker_stop()
        finally:
            # Any renderer/output failure must not orphan the now non-daemon
            # debate worker. Ask it to stop, retain the original exception, and
            # keep the log/redirect alive until its final flush completes.
            if worker_started and worker.is_alive():
                request_worker_stop()
                while worker.is_alive():
                    try:
                        worker.join(timeout=0.1)
                    except KeyboardInterrupt:
                        request_worker_stop()
            stop_stdin.set()
            if reader is not None:
                reader.join()

    return _finalize(
        log,
        err,
        result_holder,
        run_doctor_reason=run_doctor_reason,
        preset=preset,
        config_path=config_path,
    )

def _run_web(
    *,
    ticker: str,
    persona_names: list[str],
    preset: str | None,
    model: str | None,
    thinking: str | None,
    da: str | None,
    deep_research: bool,
    phase_step: bool,
    err: TextIO,
    committee,
    data_package,
    transport_factory,
    credentials,
    config_path,
    steering,
    run_doctor_reason: bool,
    port: int,
    no_open: bool,
    no_wait: bool,
    browser_opener,
) -> int:
    """Run a debate worker behind the secured localhost web face."""
    with contextlib.redirect_stdout(err):
        from tinyic.debate.control import RunControl
        from tinyic.debate.steering import SteeringInbox
        from tinyic.web import WebFace

        log = EventLog(make_debate_id(ticker))
        inbox = steering if steering is not None else SteeringInbox()
        inbox.bind_event_log(log)
        control = RunControl(paused=phase_step)
        if phase_step:
            # Opening runs immediately; subsequent phases park until next/resume.
            control.next_phase()

        result_holder: dict = {}
        worker_done = threading.Event()
        worker = threading.Thread(
            name="tinyic-debate-worker",
            target=_run_worker,
            kwargs=dict(
                ticker=ticker,
                persona_names=persona_names,
                log=log,
                inbox=inbox,
                preset=preset,
                model=model,
                thinking=thinking,
                da=da,
                no_research=not deep_research,
                committee=committee,
                data_package=data_package,
                transport_factory=transport_factory,
                credentials=credentials,
                config_path=config_path,
                phase_gate=control,
                result_holder=result_holder,
                done=worker_done,
            ),
            daemon=False,
        )
        face = WebFace.live(
            log,
            inbox=inbox,
            control=control,
            port=port,
            browser_opener=browser_opener,
            open_browser=not no_open,
            stderr=err,
            run_done=worker_done,
        )
        interrupted = False
        try:
            try:
                face.start()  # bind before the worker can emit debate_started
            except OSError as exc:
                _report_web_bind_error(err, port, exc)
                return 3
            worker.start()
            try:
                _stream_log(log.path, None, err, worker_done, poll_interval=0.1)
                worker.join()
            except KeyboardInterrupt:
                interrupted = True
                control.stop()
                inbox.request_interrupt(source="api")
                # Provider calls are not safely pre-emptible. Keep the face,
                # stdout redirect, and event log alive until the worker reaches
                # its next checkpoint and writes the terminal event. Repeated
                # Ctrl-C presses remain harmless while we wait.
                while worker.is_alive():
                    try:
                        worker.join(timeout=0.1)
                    except KeyboardInterrupt:
                        control.stop()
            finally:
                if worker.is_alive():
                    control.stop()
                    inbox.request_interrupt(source="api")
                    while worker.is_alive():
                        try:
                            worker.join(timeout=0.1)
                        except KeyboardInterrupt:
                            control.stop()
                face.mark_run_finished()

            if not no_wait and not interrupted:
                _progress(
                    err,
                    f"debate complete — viewer still at {face.base_url}, Ctrl-C to exit",
                )
                try:
                    while not face.wait(0.5):
                        pass
                except KeyboardInterrupt:
                    pass
            elif no_wait and not interrupted:
                _wait_for_no_wait_viewer(face, no_open=no_open)
        finally:
            face.shutdown(timeout=1.0)

    return _finalize(
        log,
        err,
        result_holder,
        run_doctor_reason=run_doctor_reason,
        preset=preset,
        config_path=config_path,
    )


def run_replay_command(
    id_or_path: str,
    *,
    port: int = 0,
    no_open: bool = False,
    no_wait: bool = False,
    err: TextIO | None = None,
    browser_opener=None,
) -> int:
    """Serve a recorded log through the same web face, read-only and offline."""
    err = err if err is not None else sys.stderr
    from .result import resolve_run_path

    path = resolve_run_path(id_or_path)
    if not path.is_file():
        _progress(
            err,
            f"tinyic: no debate run found for {id_or_path!r} (looked at {path})",
        )
        return 3
    with contextlib.redirect_stdout(err):
        from tinyic.web import WebFace

    face = WebFace.replay(
        path,
        port=port,
        browser_opener=browser_opener,
        open_browser=not no_open,
        stderr=err,
    )
    try:
        try:
            face.start()
        except OSError as exc:
            _report_web_bind_error(err, port, exc)
            return 3
        if no_wait:
            _wait_for_no_wait_viewer(face, no_open=no_open)
        else:
            _progress(err, f"replay viewer at {face.base_url}, Ctrl-C to exit")
            try:
                while not face.wait(0.5):
                    pass
            except KeyboardInterrupt:
                pass
    finally:
        face.shutdown(timeout=1.0)
    return 0


def run_studio_command(
    *,
    port: int = 0,
    no_open: bool = False,
    err: TextIO | None = None,
    browser_opener=None,
) -> int:
    """Serve the persistent persona studio until interrupted."""
    err = err if err is not None else sys.stderr
    with contextlib.redirect_stdout(err):
        from .web.studio import StudioServer

    studio = StudioServer(
        port=port,
        browser_opener=browser_opener,
        open_browser=not no_open,
        stderr=err,
    )
    try:
        try:
            studio.start()
        except OSError as exc:
            _report_web_bind_error(err, port, exc)
            return 3
        _progress(err, f"studio at {studio.base_url}, Ctrl-C to exit")
        try:
            while not studio.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
    finally:
        studio.shutdown(timeout=1.0)
    return 0


def _report_web_bind_error(err: TextIO, port: int, exc: OSError) -> None:
    """Render a bind failure as a concise setup error, without a traceback."""
    requested = str(port) if port else "auto"
    reason = exc.strerror or str(exc) or type(exc).__name__
    _progress(
        err,
        f"tinyic: web viewer could not bind 127.0.0.1:{requested}: {reason}",
    )


def _wait_for_no_wait_viewer(face, *, no_open: bool) -> None:
    """Drain SSE clients under the bounded attach semantics of ``--no-wait``."""
    try:
        if no_open:
            # Automation explicitly disabled browser launch. Drain only a
            # client that is active right now; zero clients returns at once.
            face.wait_for_sse_disconnect(timeout=None)
        else:
            # Browser launch is asynchronous: allow it time to attach, then do
            # not cut off a genuine client merely because it drains slowly.
            face.wait_for_sse_cycle(
                connect_timeout=_BROWSER_ATTACH_TIMEOUT_S,
                disconnect_timeout=None,
            )
    except KeyboardInterrupt:
        pass
