"""CLI and lifecycle wiring for the read-only web replay face."""

from __future__ import annotations

import io
import socket

import pytest

import tinyic.headless as headless
from tinyic.cli import build_parser, main
from tinyic.web import WebFace


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
    args = build_parser().parse_args(
        ["replay", "/tmp/run.jsonl", "--port", "8765", "--no-open", "--no-wait"]
    )
    assert args.command == "replay"
    assert args.path == "/tmp/run.jsonl"
    assert args.port == 8765
    assert args.no_open is True
    assert args.no_wait is True
    assert hasattr(args, "func")


def test_replay_dispatches_to_web_runner(monkeypatch):
    called = {}

    def fake_run_replay(path, **kwargs):
        called.update(path=path, **kwargs)
        return 0

    monkeypatch.setattr(headless, "run_replay_command", fake_run_replay)
    rc = main(
        ["replay", "/tmp/run.jsonl", "--port", "8765", "--no-open", "--no-wait"]
    )
    assert rc == 0
    assert called["path"] == "/tmp/run.jsonl"
    assert called["port"] == 8765
    assert called["no_open"] is True
    assert called["no_wait"] is True


def test_replay_runner_starts_read_only_face_and_honors_no_wait(
    tmp_path, monkeypatch
):
    log = tmp_path / "run.jsonl"
    log.write_text("", encoding="utf-8")
    called = {}

    class StubFace:
        base_url = "http://127.0.0.1:4567"

        def start(self):
            called["started"] = True

        def wait_for_sse_disconnect(self, timeout=None):
            called["disconnect_timeout"] = timeout
            return True

        def shutdown(self, *, timeout=None):
            called["shutdown_timeout"] = timeout

    def fake_replay(path, **kwargs):
        called.update(path=path, **kwargs)
        return StubFace()

    monkeypatch.setattr(WebFace, "replay", staticmethod(fake_replay))
    err = io.StringIO()
    opener = lambda _url: None
    assert (
        headless.run_replay_command(
            str(log),
            port=9001,
            no_open=True,
            no_wait=True,
            err=err,
            browser_opener=opener,
        )
        == 0
    )
    assert called["path"] == log
    assert called["port"] == 9001
    assert called["open_browser"] is False
    assert called["browser_opener"] is opener
    assert called["started"] is True
    assert called["disconnect_timeout"] is None
    assert called["shutdown_timeout"] == 1.0


def test_replay_auto_open_waits_for_attach_then_unbounded_drain(
    tmp_path, monkeypatch
):
    log = tmp_path / "run.jsonl"
    log.write_text("", encoding="utf-8")
    called = {}

    class StubFace:
        base_url = "http://127.0.0.1:4567"

        def start(self):
            called["started"] = True

        def wait_for_sse_cycle(self, **kwargs):
            called["cycle"] = kwargs
            return True

        def shutdown(self, *, timeout=None):
            called["shutdown_timeout"] = timeout

    monkeypatch.setattr(
        WebFace,
        "replay",
        staticmethod(lambda _path, **_kwargs: StubFace()),
    )
    assert (
        headless.run_replay_command(
            str(log), no_wait=True, err=io.StringIO()
        )
        == 0
    )
    assert called["cycle"] == {
        "connect_timeout": 5.0,
        "disconnect_timeout": None,
    }


def test_replay_runner_reports_occupied_port_without_traceback(tmp_path):
    log = tmp_path / "run.jsonl"
    log.write_text("", encoding="utf-8")
    err = io.StringIO()
    with socket.create_server(("127.0.0.1", 0)) as occupied:
        port = occupied.getsockname()[1]
        assert (
            headless.run_replay_command(
                str(log),
                port=port,
                no_open=True,
                no_wait=True,
                err=err,
            )
            == 3
        )
    diagnostic = err.getvalue()
    assert f"127.0.0.1:{port}" in diagnostic
    assert "could not bind" in diagnostic
    assert "Traceback" not in diagnostic


def test_replay_runner_reports_missing_run_without_starting_server(tmp_path):
    err = io.StringIO()
    missing = tmp_path / "missing.jsonl"
    assert headless.run_replay_command(str(missing), no_wait=True, err=err) == 3
    assert "no debate run found" in err.getvalue()


def test_replay_requires_a_path(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["replay"])
    assert exc.value.code == 2  # argparse usage error
    assert "path" in capsys.readouterr().err
