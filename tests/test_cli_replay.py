"""CLI wiring tests for ``tinyic replay`` and the preserved bare/help paths.

The M0 packaging test already pins the ``--help`` contract; these guard that
adding subcommands did not break it and that ``replay`` dispatches to the TUI
launcher without actually starting Textual.
"""

from __future__ import annotations

import pytest

import tinyic.tui.app as tui_app
from tinyic.cli import build_parser, main


def test_help_still_advertises_tinyic_and_lists_replay(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "TinyIC" in out
    assert "replay" in out


def test_bare_invocation_returns_zero_and_prints_help(capsys):
    assert main([]) == 0
    assert "usage:" in capsys.readouterr().out


def test_parser_wires_replay_subcommand():
    args = build_parser().parse_args(["replay", "/tmp/run.jsonl"])
    assert args.command == "replay"
    assert args.path == "/tmp/run.jsonl"
    assert hasattr(args, "func")


def test_replay_dispatches_to_run_replay(monkeypatch):
    called = {}

    def fake_run_replay(path):
        called["path"] = path

    # _cmd_replay imports run_replay from tinyic.tui.app at call time.
    monkeypatch.setattr(tui_app, "run_replay", fake_run_replay)
    rc = main(["replay", "/tmp/run.jsonl"])
    assert rc == 0
    assert called["path"] == "/tmp/run.jsonl"


def test_replay_requires_a_path(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["replay"])
    assert exc.value.code == 2  # argparse usage error
    assert "path" in capsys.readouterr().err
