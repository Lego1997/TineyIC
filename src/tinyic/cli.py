"""TinyIC command-line entry point.

M0 established the console script and ``--help`` path; ``tinyic replay`` serves
a recorded event log in the read-only web viewer. M3 adds the headless
``doctor`` seam and its interactive twin ``tinyic onboard`` (the FR-2.4 wizard).
M6 adds the flagship ``tinyic debate`` (interactive Town Hall or headless/JSON
agent mode, FR-6.1/6.2) plus ``tinyic runs list`` and ``tinyic result`` for
consuming recorded debates, and ``tinyic export`` for rendering a recorded debate
as a self-contained HTML page or a Markdown bundle (FR-3.2). v2.1 adds ``tinyic
models`` — the per-provider model catalog (static, or ``--refresh`` live). The
parser keeps subcommands *optional* so a bare ``tinyic`` and ``tinyic --help``
both work.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json as _json
import sys
from collections.abc import Sequence

from tinyic import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser with optional subcommands."""
    parser = argparse.ArgumentParser(
        prog="tinyic",
        description="TinyIC — an AI investment committee simulator.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    _add_debate_parser(subparsers)
    _add_runs_parser(subparsers)
    _add_result_parser(subparsers)
    _add_export_parser(subparsers)
    _add_models_parser(subparsers)
    _add_persona_parser(subparsers)

    replay = subparsers.add_parser(
        "replay",
        help="Replay a recorded debate in the read-only web viewer (no LLM calls).",
        description=(
            "Replay a recorded TinyIC debate by serving its JSONL event log "
            "to the same localhost Town Hall used for live debates."
        ),
    )
    replay.add_argument(
        "path",
        help="Debate id or path to a recorded .jsonl event log.",
    )
    _add_web_flags(replay)
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
        help="Run explicit one-token checks for the preset's required bindings (may consume quota).",
    )
    doctor.add_argument(
        "--live-all",
        dest="live_all",
        action="store_true",
        help=(
            "Extend live one-token checks to every discovered lane, not just the "
            "preset's required bindings (implies --live; may consume more quota)."
        ),
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
            "Run a four-phase investment-committee debate. By default this "
            "opens the localhost Town Hall; with --headless/--json it runs as a "
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
        help="Do not start the web viewer (agent/CI mode).",
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
    _add_web_flags(debate)
    debate.set_defaults(func=_cmd_debate)


def _port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _add_web_flags(parser: argparse.ArgumentParser) -> None:
    """Add the shared localhost viewer lifecycle flags without importing it."""
    parser.add_argument(
        "--port",
        type=_port_number,
        default=0,
        metavar="N",
        help="Bind the localhost viewer to port N (default: an ephemeral port).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Print the viewer URL without opening a browser.",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Exit after the run and any active SSE viewer disconnect.",
    )


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


def _add_export_parser(subparsers) -> None:
    """Register ``tinyic export <id|path> --html|--md`` (FR-3.2 / FR-6.1)."""
    export = subparsers.add_parser(
        "export",
        help="Render a recorded debate as self-contained HTML or a Markdown bundle.",
        description=(
            "Render any recorded debate from its event log into a shareable "
            "artifact: a single self-contained HTML page (--html) with the "
            "transcript, expandable thinking, committee, scorecard, memo, "
            "disagreements and usage rollup, or a Markdown bundle (--md) of the "
            "scorecard, memo, transcript and disagreements. Writes to STDOUT "
            "unless -o is given. No LLM calls."
        ),
    )
    export.add_argument(
        "id", metavar="<id|path>", help="Debate id or path to a run's .jsonl log."
    )
    fmt = export.add_mutually_exclusive_group(required=True)
    fmt.add_argument(
        "--html",
        action="store_const",
        const="html",
        dest="fmt",
        help="Render a self-contained static HTML page.",
    )
    fmt.add_argument(
        "--md",
        "--markdown",
        action="store_const",
        const="md",
        dest="fmt",
        help="Render a Markdown bundle (scorecard/memo/transcript/disagreements).",
    )
    export.add_argument(
        "-o",
        "--out",
        metavar="FILE",
        help="Write the report to FILE (default: STDOUT).",
    )
    export.set_defaults(func=_cmd_export)


def _add_models_parser(subparsers) -> None:
    """Register ``tinyic models [provider] [--refresh] [--json]`` (v2.1)."""
    models = subparsers.add_parser(
        "models",
        help="List the per-provider model catalog (static, or --refresh live).",
        description=(
            "Show the model catalog per provider: the shipped static catalogs, "
            "or — with --refresh — each provider's live model listing merged "
            "over them. Refresh uses the configured credentials; a provider "
            "whose refresh fails degrades to its static catalog with a warning "
            "(the command still exits 0)."
        ),
    )
    models.add_argument(
        "provider",
        nargs="?",
        help="Limit the catalog to one provider (e.g. anthropic).",
    )
    models.add_argument(
        "--refresh",
        action="store_true",
        help="Query the providers' live model-listing endpoints (network).",
    )
    models.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help="Write one machine-readable models schema-v1 JSON document.",
    )
    models.add_argument("--config", help="Path to tinyic.toml.")
    models.set_defaults(func=_cmd_models)


def _max_searches(value: str) -> int:
    """Validate the persona factory's bounded provider-search budget."""
    try:
        searches = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max searches must be an integer") from exc
    if not 1 <= searches <= 16:
        raise argparse.ArgumentTypeError("max searches must be between 1 and 16")
    return searches


def _add_persona_parser(subparsers) -> None:
    """Register the import-light ``tinyic persona`` command family."""
    persona = subparsers.add_parser(
        "persona",
        help="Research, list, or inspect investor personas.",
        description=(
            "Create cited investor personas from public sources, or inspect "
            "the layered built-in and user persona registry."
        ),
    )
    persona_commands = persona.add_subparsers(
        dest="persona_command", metavar="<command>", required=True
    )

    research = persona_commands.add_parser(
        "research",
        help="Research an investor and create cited persona artifacts.",
        description=(
            "Research an investor through a configured search-capable API lane "
            "and write an agent JSON file plus a cited Markdown dossier."
        ),
    )
    research.add_argument("name", metavar="NAME", help="Public investor name.")
    research.add_argument(
        "--model",
        metavar="REF",
        help="Explicit search-capable provider/model binding.",
    )
    research.add_argument(
        "--slug",
        help="Artifact slug (default: snake-case investor name).",
    )
    research.add_argument(
        "--max-searches",
        type=_max_searches,
        default=None,
        metavar="N",
        help=(
            "Maximum billable search units/echo rounds, 1-16 "
            "(default: 12; Kimi: 16)."
        ),
    )
    research.add_argument(
        "--yes",
        action="store_true",
        help="Accept the displayed cost estimate without prompting.",
    )
    research.add_argument(
        "--force",
        action="store_true",
        help="Atomically replace an existing user persona's two artifacts.",
    )
    research.set_defaults(func=_cmd_persona_research)

    list_command = persona_commands.add_parser(
        "list",
        help="List built-in and user personas.",
    )
    list_command.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help="Write one stable schema-v1 JSON document.",
    )
    list_command.set_defaults(func=_cmd_persona_list)

    show = persona_commands.add_parser(
        "show",
        help="Show a persona summary and its artifact paths.",
    )
    show.add_argument("slug", metavar="SLUG", help="Persona registry slug.")
    show.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help="Write one stable schema-v1 JSON document.",
    )
    show.set_defaults(func=_cmd_persona_show)


def _cmd_models(args: argparse.Namespace) -> int:
    """List the merged model catalog (human table or ``--json``)."""
    # Imported lazily so ``--help`` and the other light commands never pull the
    # model registry (and, on --refresh, the auth manager / keyring). Importing
    # tinyic.models transitively triggers TinyTroupe's one-time disclaimer +
    # config dump; run under a redirect so ``--json`` stays one clean document.
    try:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            from .models import catalog as catalog_module

            service = catalog_module.CatalogService(config_path=args.config)
            catalogs = service.snapshot(args.provider, refresh=args.refresh)
    except KeyError as exc:
        print(f"tinyic: {exc.args[0]}", file=sys.stderr)
        return 2
    if args.json_mode:
        document = catalog_module.to_document(catalogs)
        print(_json.dumps(document, ensure_ascii=False, separators=(",", ":")))
    else:
        print(catalog_module.render_human(catalogs))
    # Per-provider refresh degradation is reported in the output, never via a
    # non-zero exit: the command itself succeeded.
    return 0


def _cmd_persona_research(args: argparse.Namespace) -> int:
    """Create one cited persona through the lazy command service."""
    from .persona_cli import research_persona

    return research_persona(
        args.name,
        model=args.model,
        slug=args.slug,
        max_searches=args.max_searches,
        yes=args.yes,
        force=args.force,
    )


def _cmd_persona_list(args: argparse.Namespace) -> int:
    """List the layered persona registry without loading persona agents."""
    from .persona_cli import list_personas_command

    return list_personas_command(json_mode=args.json_mode)


def _cmd_persona_show(args: argparse.Namespace) -> int:
    """Render one registry persona's metadata summary."""
    from .persona_cli import show_persona_command

    return show_persona_command(args.slug, json_mode=args.json_mode)


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
        port=args.port,
        no_open=args.no_open,
        no_wait=args.no_wait,
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


def _cmd_export(args: argparse.Namespace) -> int:
    """Render a recorded debate as an HTML page or Markdown bundle."""
    # Imported lazily so ``--help`` and the light commands never pull the report
    # renderer (which imports the debate models / view-state reducer).
    from .report import load_and_render

    # The Markdown path reuses the debate models' ``to_markdown``; importing that
    # package pulls TinyTroupe, whose one-time import prints an AI disclaimer +
    # config dump to STDOUT. The report *is* the STDOUT artifact, so render under
    # a redirect (as ``doctor``/headless do) — import chatter must never prefix
    # the exported document.
    try:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            text = load_and_render(args.id, args.fmt)
    except FileNotFoundError as exc:
        print(f"tinyic: {exc}", file=sys.stderr)
        return 3
    if args.out:
        from pathlib import Path

        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        # The written path is human progress, not the artifact: keep it off STDOUT.
        print(f"tinyic: wrote {args.fmt} report to {out_path}", file=sys.stderr)
    else:
        # The report is the artifact; write it verbatim (no extra newline) so a
        # redirected file is exactly what the renderer produced.
        sys.stdout.write(text)
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    """Serve ``args.path`` in the read-only localhost Town Hall."""
    from .headless import run_replay_command

    return run_replay_command(
        args.path,
        port=args.port,
        no_open=args.no_open,
        no_wait=args.no_wait,
    )


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
            live=args.live or args.live_all,
            live_optional=args.live_all,
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
