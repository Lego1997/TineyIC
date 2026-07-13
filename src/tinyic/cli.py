"""TinyIC command-line entry point.

M0 established the console script and ``--help`` path; M5 adds ``tinyic replay``
to render a recorded event log in the Textual TUI. M3 adds the headless
``doctor`` seam; its TUI ``onboard`` consumer is intentionally separate.
Additional product commands land in their own milestones. The parser keeps
subcommands *optional* so a bare ``tinyic`` and ``tinyic --help`` both work.
"""

from __future__ import annotations

import argparse
import contextlib
import io
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser with optional subcommands."""
    parser = argparse.ArgumentParser(
        prog="tinyic",
        description="TinyIC — an AI investment committee simulator.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

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

    return parser


def _cmd_replay(args: argparse.Namespace) -> int:
    """Launch the Town Hall TUI to replay ``args.path``."""
    # Import lazily so the (heavy) Textual stack is only loaded when replaying,
    # keeping ``tinyic --help`` and other commands fast and import-light.
    from .tui.app import run_replay

    run_replay(args.path)
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
