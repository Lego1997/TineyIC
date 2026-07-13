"""TinyIC command-line entry point.

M0 established the console script and ``--help`` path; M5 adds ``tinyic replay``
to render a recorded event log in the Textual TUI. Additional product commands
(``debate``, ``onboard``, ``doctor``, ``export``, ``result`` …) land in their own
milestones. The parser keeps subcommands *optional* so a bare ``tinyic`` and
``tinyic --help`` both stay working.
"""

from __future__ import annotations

import argparse
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

    return parser


def _cmd_replay(args: argparse.Namespace) -> int:
    """Launch the Town Hall TUI to replay ``args.path``."""
    # Import lazily so the (heavy) Textual stack is only loaded when replaying,
    # keeping ``tinyic --help`` and other commands fast and import-light.
    from .tui.app import run_replay

    run_replay(args.path)
    return 0


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
