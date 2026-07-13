"""TinyIC command-line entry point.

M0 established the console script and ``--help`` path; M5 adds ``tinyic replay``
to render a recorded event log in the Textual TUI. M3 adds the headless
``doctor`` seam and its interactive twin ``tinyic onboard`` (the FR-2.4 wizard).
M6 adds the flagship ``tinyic debate`` (interactive Town Hall or headless/JSON
agent mode, FR-6.1/6.2) plus ``tinyic runs list`` and ``tinyic result`` for
consuming recorded debates. The parser keeps subcommands *optional* so a bare
``tinyic`` and ``tinyic --help`` both work.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json as _json
import sys
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser with optional subcommands."""
    parser = argparse.ArgumentParser(
        prog="tinyic",
        description="TinyIC — an AI investment committee simulator.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    _add_debate_parser(subparsers)
    _add_runs_parser(subparsers)
    _add_result_parser(subparsers)

    replay = subparsers.add_parser(
        "replay",
        help="Replay a recorded debate event log in the TUI (no LLM calls).",
        description=(
            "Replay a recorded TinyIC debate by re-feeding its JSONL event log "
            "to the Town Hall TUI. Accepts a path to a .jsonl log."
        ),
    )
    replay.add_argument(
        "path",
        help="Path to a recorded debate event log (.jsonl).",
    )
    replay.set_defaults(func=_cmd_replay)

    doctor = subparsers.add_parser(
        "doctor",
        help="Check auth profiles and provider lanes without launching a UI.",
        description=(
            "Probe TinyIC auth profiles and provider runtimes. Probes are local "
            "and non-networked unless --live is supplied."
        ),
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Write one machine-readable doctor schema-v1 JSON document.",
    )
    doctor.add_argument("--preset", help="Preset whose required bindings to check.")
    doctor.add_argument("--config", help="Path to tinyic.toml.")
    doctor.add_argument(
        "--live",
        action="store_true",
        help="Run explicit one-token provider checks (may consume quota).",
    )
    doctor.set_defaults(func=_cmd_doctor)

    onboard = subparsers.add_parser(
        "onboard",
        help="Interactive setup wizard: detect, choose a lane, verify, persist.",
        description=(
            "Walk auth setup in a full-screen TUI (FR-2.4): detect existing "
            "access, choose a subscription or API-key lane per provider, verify "
            "with a live one-token check, and persist only verified routes. "
            "Re-running is an idempotent verify-and-repair pass."
        ),
    )
    onboard.add_argument("--config", help="Path to tinyic.toml.")
    onboard.set_defaults(func=_cmd_onboard)

    return parser


def _add_debate_parser(subparsers) -> None:
    """Register ``tinyic debate`` (FR-6.1): the flagship committee command."""
    debate = subparsers.add_parser(
        "debate",
        help="Convene the investment committee on a ticker or company.",
        description=(
            "Run a four-phase investment-committee debate. In a terminal this "
            "opens the live Town Hall TUI; with --headless/--json it runs as a "
            "headless agent, streaming the event log to STDOUT."
        ),
    )
    debate.add_argument(
        "query", metavar="<ticker|company>", help="Ticker symbol or company name."
    )
    debate.add_argument("--preset", help="Named committee preset from tinyic.toml.")
    debate.add_argument(
        "--personas",
        help="Comma-separated persona registry names (default: the six-member committee).",
    )
    debate.add_argument(
        "--model", help="Per-debate provider/model override for every role."
    )
    debate.add_argument(
        "--thinking",
        help="Per-debate thinking level (off|minimal|low|medium|high|xhigh|max).",
    )
    debate.add_argument("--da", help="Pin the cross-exam devil's advocate to a persona.")
    debate.add_argument(
        "--no-research",
        action="store_true",
        help="Skip the web-research data source.",
    )
    debate.add_argument(
        "--headless",
        action="store_true",
        help="Never launch the TUI (agent/CI mode).",
    )
    debate.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help="Stream the event log as JSONL to STDOUT (implies headless).",
    )
    debate.add_argument(
        "--phase-step",
        dest="phase_step",
        action="store_true",
        help="Pause at each phase boundary (interactive).",
    )
    debate.add_argument(
        "--steer-stdin",
        dest="steer_stdin",
        action="store_true",
        help='Read JSON steering lines from STDIN ({"type":"steer|queue|interrupt",...}).',
    )
    debate.add_argument(
        "--yes",
        action="store_true",
        help="Assume yes / never prompt (non-interactive runs).",
    )
    debate.add_argument("--config", help="Path to tinyic.toml.")
    debate.set_defaults(func=_cmd_debate)


def _add_runs_parser(subparsers) -> None:
    """Register ``tinyic runs list`` (FR-6.1)."""
    runs = subparsers.add_parser(
        "runs",
        help="List recorded debate runs.",
        description="Inspect the recorded debates under ~/.tinyic/runs.",
    )
    runs.add_argument(
        "subcommand",
        nargs="?",
        default="list",
        choices=["list"],
        help="Runs sub-command (only 'list' is defined).",
    )
    runs.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help="Emit the run list as one JSON document.",
    )
    runs.set_defaults(func=_cmd_runs)


def _add_result_parser(subparsers) -> None:
    """Register ``tinyic result <id>`` (schema compatibility promise #3)."""
    result = subparsers.add_parser(
        "result",
        help="Print the assembled result document for a recorded debate.",
        description=(
            "Assemble a single result document (scorecard, memo, disagreements, "
            "usage rollup) from a recorded debate's event log."
        ),
    )
    result.add_argument(
        "id", metavar="<id|path>", help="Debate id or path to a run's .jsonl log."
    )
    result.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help="Emit the result as one JSON document (default is a human summary).",
    )
    result.set_defaults(func=_cmd_result)


def _cmd_debate(args: argparse.Namespace) -> int:
    """Dispatch to the headless/interactive debate runner (FR-6.1/6.2)."""
    # Imported lazily so the (heavy) engine + TinyTroupe import stays out of
    # ``--help`` and the other light commands.
    from .headless import run_debate_command

    return run_debate_command(
        args.query,
        personas=args.personas,
        preset=args.preset,
        model=args.model,
        thinking=args.thinking,
        da=args.da,
        no_research=args.no_research,
        headless=args.headless,
        json_mode=args.json_mode,
        phase_step=args.phase_step,
        steer_stdin=args.steer_stdin,
        yes=args.yes,
        config_path=args.config,
    )


def _cmd_runs(args: argparse.Namespace) -> int:
    """List recorded debate runs (human table or ``--json``)."""
    from .result import list_runs

    runs = list_runs()
    if args.json_mode:
        print(_json.dumps({"runs": runs}, ensure_ascii=False))
        return 0
    if not runs:
        print("No recorded debates found under ~/.tinyic/runs.")
        return 0
    print(f"{'DEBATE ID':<28} {'TICKER':<8} {'STATUS':<10} {'CONSENSUS':<9} STARTED")
    for run in runs:
        print(
            f"{str(run.get('debate_id') or ''):<28} "
            f"{str(run.get('ticker') or '?'):<8} "
            f"{str(run.get('status') or '?'):<10} "
            f"{str(run.get('consensus') or '-'):<9} "
            f"{run.get('started_at') or ''}"
        )
    return 0


def _cmd_result(args: argparse.Namespace) -> int:
    """Assemble and print a recorded debate's result document."""
    from .result import exit_code_for_status, load_result

    try:
        document = load_result(args.id)
    except FileNotFoundError as exc:
        print(f"tinyic: {exc}", file=sys.stderr)
        return 3
    if args.json_mode:
        print(_json.dumps(document, ensure_ascii=False))
    else:
        print(_render_result_human(document))
    # A result that never completed is surfaced with the same code family a live
    # run would exit with, so a scripted caller sees a consistent signal.
    return exit_code_for_status(document.get("status", "incomplete"))


def _render_result_human(document: dict) -> str:
    """Render a compact human summary of a result document."""
    lines = [
        f"Debate {document.get('debate_id', '?')} — "
        f"{document.get('ticker', '?')} ({document.get('company_name', '?')})",
        f"Status: {document.get('status', '?')} · preset "
        f"{document.get('preset', '?')}",
    ]
    scorecard = document.get("scorecard")
    if isinstance(scorecard, dict):
        lines.append(
            f"Scorecard: consensus={scorecard.get('consensus', 'none')} "
            f"(bull {scorecard.get('bull_count', 0)} / "
            f"bear {scorecard.get('bear_count', 0)} / "
            f"hold {scorecard.get('hold_count', 0)})"
        )
    for vote in document.get("votes", []) or []:
        lines.append(
            f"  {vote.get('persona', '?'):<20} {vote.get('vote', '?'):<5} "
            f"{vote.get('confidence', '?')}"
        )
    usage = (document.get("usage") or {}).get("total") or {}
    if usage:
        cost = usage.get("cost_usd")
        cost_text = f"${cost:.4f}" if isinstance(cost, (int, float)) else "n/a"
        lines.append(
            f"Usage: in {usage.get('input_tokens', 0)} / "
            f"out {usage.get('output_tokens', 0)} tokens · cost {cost_text}"
        )
    return "\n".join(lines)


def _cmd_replay(args: argparse.Namespace) -> int:
    """Launch the Town Hall TUI to replay ``args.path``."""
    # Import lazily so the (heavy) Textual stack is only loaded when replaying,
    # keeping ``tinyic --help`` and other commands fast and import-light.
    from .tui.app import run_replay

    run_replay(args.path)
    return 0


def _cmd_onboard(args: argparse.Namespace) -> int:
    """Launch the interactive onboarding wizard (FR-2.4)."""
    # Import lazily so ``--help``/``doctor``/``replay`` stay import-light and do
    # not pull in the Textual stack until an interactive wizard is requested.
    from .tui.onboard import run_onboard

    run_onboard(config_path=args.config)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Run the headless reason-coded auth diagnostics."""
    # Import lazily so help/replay do not load keyring or provider runtimes.
    # TinyTroupe's package import prints a legacy disclaimer/config dump; the
    # doctor seam must never let dependency chatter corrupt its one-document
    # machine output (and should stay quiet in human mode too).
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        from .auth import doctor as doctor_module

        report = doctor_module.run_doctor(
            preset=args.preset,
            config_path=args.config,
            live=args.live,
        )
    if args.json:
        print(report.to_json())
    else:
        print(doctor_module.render_human(report))
    return 0 if report.ok else 3


def main(argv: Sequence[str] | None = None) -> int:
    """Run the TinyIC command-line entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "func", None)
    if handler is None:
        # Bare ``tinyic`` with no subcommand: show help, exit cleanly.
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
