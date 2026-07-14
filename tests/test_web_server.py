"""Focused real-socket tests for TinyIC's localhost web face."""

from __future__ import annotations

import http.client
import http.cookiejar
import io
import json
import threading
import time
from pathlib import Path
from urllib.request import HTTPCookieProcessor, build_opener

import pytest

from tinyic.debate.control import RunControl
from tinyic.report import render_html, render_markdown
from tinyic.tui.events import read_events
from tinyic.web.security import CSP_POLICY, SecurityPolicy, is_json_content_type
from tinyic.web.server import WebFace, WebServer


def _event(seq: int, event_type: str, **payload: object) -> dict[str, object]:
    return {
        "v": 1,
        "seq": seq,
        "ts": f"2026-07-14T12:00:0{seq}.000Z",
        "debate_id": "aapl-20260714-web1",
        "type": event_type,
        "payload": payload,
    }


def _write_log(path: Path, events: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )


def _assets(name: str):
    values = {
        "index.html": (b"<!doctype html><title>TinyIC</title>", "text/html; charset=utf-8"),
        "app.js": (b"export {};", "text/javascript; charset=utf-8"),
        "style.css": (b"body{}", "text/css; charset=utf-8"),
    }
    return values.get(name)


@pytest.fixture
def servers():
    running: list[WebServer] = []

    def make(path: Path, **kwargs) -> WebServer:
        token = kwargs.pop("token", "test-token")
        server = WebServer(
            path,
            token=token,
            open_browser=False,
            asset_loader=_assets,
            stderr=io.StringIO(),
            poll_interval=0.005,
            heartbeat_interval=0.03,
            **kwargs,
        ).start()
        running.append(server)
        return server

    yield make
    for server in running:
        server.shutdown()


def _request(
    server: WebServer,
    method: str,
    target: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    host: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
    connection.putrequest(method, target, skip_host=True)
    connection.putheader("Host", host or f"127.0.0.1:{server.port}")
    for name, value in (headers or {}).items():
        connection.putheader(name, value)
    if body is not None and not any(
        name.casefold() == "content-length" for name in (headers or {})
    ):
        connection.putheader("Content-Length", str(len(body)))
    connection.endheaders(body)
    response = connection.getresponse()
    response_body = response.read()
    result = response.status, {name: value for name, value in response.getheaders()}, response_body
    connection.close()
    return result


def _auth(server: WebServer) -> dict[str, str]:
    return {"Authorization": f"Bearer {server.token}"}


def _post_json(server: WebServer, path: str, value: object):
    body = json.dumps(value).encode("utf-8")
    return _request(
        server,
        "POST",
        path,
        headers={**_auth(server), "Content-Type": "application/json; charset=utf-8"},
        body=body,
    )


def test_security_policy_exact_allowlists_and_json_type():
    policy = SecurityPolicy(port=8123, token="safe-token")
    assert policy.valid_host("127.0.0.1:8123")
    assert policy.valid_host("localhost:8123")
    assert policy.valid_host("[::1]:8123")
    assert not policy.valid_host("127.0.0.1:8124")
    assert not policy.valid_host("attacker.test")
    assert policy.valid_origin(None)
    assert policy.valid_origin("http://localhost:8123")
    assert not policy.valid_origin("https://localhost:8123")
    assert is_json_content_type("application/json; charset=utf-8")
    assert not is_json_content_type("application/x-www-form-urlencoded")


def test_token_cookie_handoff_and_security_headers(tmp_path, servers):
    path = tmp_path / "run.jsonl"
    _write_log(path, [])
    server = servers(path, replay=True)

    status, headers, _ = _request(server, "GET", "/")
    assert status == 401
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Security-Policy"] == CSP_POLICY

    assert _request(server, "GET", "/?token=wrong")[0] == 403
    status, headers, _ = _request(server, "GET", "/?token=test-token")
    assert status == 302
    assert headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    assert f"{server.security.cookie_name}=test-token" in cookie
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie

    status, _, body = _request(
        server, "GET", "/", headers={"Cookie": cookie.split(";", 1)[0]}
    )
    assert status == 200
    assert b"TinyIC" in body


def test_two_servers_keep_independent_capability_cookies(tmp_path, servers):
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    _write_log(first_path, [])
    _write_log(second_path, [])
    first = servers(first_path, replay=True, token="first-token")
    second = servers(second_path, replay=True, token="second-token")

    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    with opener.open(first.launch_url, timeout=3) as response:
        assert response.status == 200
    with opener.open(second.launch_url, timeout=3) as response:
        assert response.status == 200

    assert first.security.cookie_name != second.security.cookie_name
    assert {cookie.name for cookie in cookie_jar} == {
        first.security.cookie_name,
        second.security.cookie_name,
    }
    with opener.open(f"{first.base_url}/api/meta", timeout=3) as response:
        assert response.status == 200
    with opener.open(f"{second.base_url}/api/meta", timeout=3) as response:
        assert response.status == 200


def test_host_origin_and_json_only_rejections(tmp_path, servers):
    path = tmp_path / "run.jsonl"
    _write_log(path, [])
    server = servers(path, inbox=FakeInbox())

    assert _request(server, "GET", "/api/meta", headers=_auth(server), host="attacker.test")[0] == 403
    assert _request(
        server,
        "POST",
        "/api/steering",
        headers={
            **_auth(server),
            "Origin": "http://attacker.test",
            "Content-Type": "application/json",
        },
        body=b' {"type":"steer","text":"x"}',
    )[0] == 403
    assert _request(
        server,
        "POST",
        "/api/steering",
        headers={
            **_auth(server),
            "Origin": f"http://127.0.0.1:{server.port}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body=b"type=steer&text=x",
    )[0] == 415


def test_sse_full_replay_resume_and_post_final_204(tmp_path, servers):
    path = tmp_path / "run.jsonl"
    events = [
        _event(1, "debate_started", ticker="AAPL", company_name="Apple Inc."),
        _event(2, "talk_delta", turn_id="t1", text="hello"),
        _event(3, "debate_completed", phases_completed=[], duration_s=1, result_ref="x"),
    ]
    _write_log(path, events)
    server = servers(path, replay=True)

    status, headers, body = _request(
        server, "GET", "/events?from_seq=0", headers=_auth(server)
    )
    text = body.decode("utf-8")
    assert status == 200
    assert headers["Content-Type"].startswith("text/event-stream")
    assert text.startswith("retry: 1500\n\n")
    assert [line for line in text.splitlines() if line.startswith("id:")] == [
        "id:1",
        "id:2",
        "id:3",
    ]
    envelopes = [
        json.loads(line.removeprefix("data:"))
        for line in text.splitlines()
        if line.startswith("data:")
    ]
    assert envelopes == events

    status, _, body = _request(
        server,
        "GET",
        "/events?from_seq=0",
        headers={**_auth(server), "Last-Event-ID": "1"},
    )
    assert status == 200
    assert b"id:1\n" not in body
    assert b"id:2\n" in body and b"id:3\n" in body
    assert _request(server, "GET", "/events?from_seq=3", headers=_auth(server))[0] == 204


def test_sse_heartbeat_and_live_run_finish_seam(tmp_path, servers):
    path = tmp_path / "run.jsonl"
    _write_log(path, [_event(1, "debate_started", ticker="AAPL")])
    server = servers(path)
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
    connection.request("GET", "/events?from_seq=0", headers=_auth(server))
    response = connection.getresponse()
    assert response.status == 200
    seen = []
    while ": ping\n" not in seen:
        line = response.readline().decode("utf-8")
        assert line
        seen.append(line)
    assert server.active_sse == 1
    server.mark_run_finished()
    response.read()
    connection.close()
    assert server.wait_for_sse_disconnect(timeout=2)
    status, _, body = _request(
        server, "GET", "/api/meta", headers=_auth(server)
    )
    assert status == 200
    assert json.loads(body)["status"] == "error"


def test_sse_drains_terminal_appended_as_run_becomes_done(tmp_path, servers):
    path = tmp_path / "run.jsonl"
    _write_log(path, [_event(1, "debate_started", ticker="AAPL")])
    terminal = _event(
        2,
        "debate_completed",
        phases_completed=[],
        duration_s=1,
    )

    class SealBetweenTailReadAndCheck:
        def __init__(self) -> None:
            self.checks = 0

        def is_set(self) -> bool:
            self.checks += 1
            if self.checks == 2:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(terminal, separators=(",", ":")) + "\n")
                    handle.flush()
                return True
            return self.checks > 2

    server = servers(path, run_done=SealBetweenTailReadAndCheck())
    status, _, body = _request(
        server, "GET", "/events?from_seq=0", headers=_auth(server)
    )

    assert status == 200
    assert b"id:1\n" in body
    assert b"id:2\n" in body
    payloads = [
        json.loads(line.removeprefix(b"data:"))
        for line in body.splitlines()
        if line.startswith(b"data:")
    ]
    assert payloads[-1] == terminal


def test_sse_incremental_tail_preserves_partial_utf8_and_resume(
    tmp_path, servers
):
    path = tmp_path / "run.jsonl"
    first = json.dumps(
        _event(1, "debate_started", ticker="AAPL"),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    second = json.dumps(
        _event(2, "talk_delta", turn_id="t1", text="margin…safety"),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    terminal = json.dumps(
        _event(3, "debate_completed", phases_completed=[], duration_s=1),
        separators=(",", ":"),
    ).encode("utf-8")
    ellipsis = "…".encode("utf-8")
    cut = second.index(ellipsis) + 2  # stop inside the three-byte character
    path.write_bytes(first + b"\n" + second[:cut])
    server = servers(path)

    # Streaming must use its per-connection follower, not the whole-file
    # snapshot used by /api/meta and exports.
    server.snapshot = lambda: (_ for _ in ()).throw(
        AssertionError("SSE unexpectedly read a whole-file snapshot")
    )
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
    connection.request("GET", "/events?from_seq=0", headers=_auth(server))
    response = connection.getresponse()
    assert response.status == 200
    first_frame = b"".join(response.readline() for _ in range(5)).decode("utf-8")
    assert "retry: 1500" in first_frame
    assert "id:1\n" in first_frame
    assert "id:2\n" not in first_frame

    with path.open("ab") as handle:
        handle.write(second[cut:] + b"\n" + terminal + b"\n")
        handle.flush()
    remainder = response.read().decode("utf-8")
    connection.close()
    assert "id:2\n" in remainder and "id:3\n" in remainder
    payloads = [
        json.loads(line.removeprefix("data:"))
        for line in remainder.splitlines()
        if line.startswith("data:")
    ]
    assert payloads[0]["payload"]["text"] == "margin…safety"

    # The completed stream remains resumable by sequence without re-sending 1.
    status, _, body = _request(
        server,
        "GET",
        "/events?from_seq=0",
        headers={**_auth(server), "Last-Event-ID": "1"},
    )
    assert status == 200
    assert b"id:1\n" not in body
    assert b"id:2\n" in body and b"id:3\n" in body


def test_wait_for_sse_cycle_is_bounded_and_remembers_fast_replay(
    tmp_path, servers
):
    path = tmp_path / "run.jsonl"
    _write_log(
        path,
        [
            _event(1, "debate_started", ticker="AAPL"),
            _event(2, "debate_completed", phases_completed=[], duration_s=1),
        ],
    )
    server = servers(path, replay=True)

    started = time.monotonic()
    assert server.wait_for_sse_cycle(connect_timeout=0.03) is False
    assert time.monotonic() - started < 0.5

    assert _request(
        server, "GET", "/events?from_seq=0", headers=_auth(server)
    )[0] == 200
    # The request has already connected and disconnected. The monotonic
    # connection counter keeps that fast cycle observable to the CLI.
    assert server.wait_for_sse_cycle(connect_timeout=0.03) is True


class FakeInbox:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def submit(self, mode, text, *, target=None, source=None):
        self.calls.append(("submit", mode, text, target, source))
        return "api-0001"

    def request_interrupt(self, *, text=None, target=None, source=None):
        self.calls.append(("interrupt", text, target, source))
        return True


class FakeControl:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")

    def next_phase(self):
        self.calls.append("next_phase")

    def stop(self):
        self.calls.append("stop")


def test_steering_and_control_posts_preserve_protocol(tmp_path, servers):
    path = tmp_path / "run.jsonl"
    _write_log(path, [])
    inbox = FakeInbox()
    control = FakeControl()
    server = servers(path, inbox=inbox, control=control)

    status, _, body = _post_json(
        server,
        "/api/steering",
        {"type": "steer", "target": "Warren Buffett", "text": "Press valuation."},
    )
    assert status == 202
    assert json.loads(body)["msg_id"] == "api-0001"
    assert inbox.calls == [
        ("submit", "steer", "Press valuation.", "Warren Buffett", "api")
    ]
    assert _post_json(server, "/api/steering", {"type": "interrupt"})[0] == 202
    assert inbox.calls[-1] == ("interrupt", None, None, "api")

    for action in ("pause", "resume", "next_phase", "stop"):
        assert _post_json(server, "/api/control", {"type": action})[0] == 202
    assert control.calls == ["pause", "resume", "next_phase", "stop"]


def test_replay_rejects_state_changes(tmp_path, servers):
    path = tmp_path / "run.jsonl"
    _write_log(path, [])
    server = servers(path, replay=True)
    status, _, body = _post_json(
        server, "/api/steering", {"type": "steer", "text": "x"}
    )
    assert status == 409
    assert json.loads(body) == {"error": "replay_read_only"}
    status, _, body = _post_json(server, "/api/control", {"type": "pause"})
    assert status == 409
    assert json.loads(body) == {"error": "replay_read_only"}


@pytest.mark.parametrize("finish_mode", ["terminal", "run_done"])
def test_finished_live_run_rejects_steering_and_control(
    tmp_path, servers, finish_mode
):
    path = tmp_path / "run.jsonl"
    events = [_event(1, "debate_started", ticker="AAPL")]
    if finish_mode == "terminal":
        events.append(
            _event(2, "debate_completed", phases_completed=[], duration_s=1)
        )
    _write_log(path, events)
    inbox = FakeInbox()
    control = FakeControl()
    server = servers(path, inbox=inbox, control=control)
    if finish_mode == "run_done":
        server.mark_run_finished()

    for endpoint, payload in (
        ("/api/steering", {"type": "steer", "text": "too late"}),
        ("/api/control", {"type": "pause"}),
    ):
        status, _, body = _post_json(server, endpoint, payload)
        assert status == 409
        assert json.loads(body) == {"error": "run_finished"}
    assert inbox.calls == []
    assert control.calls == []


def test_meta_and_exports_use_current_log_verbatim(tmp_path, servers):
    path = tmp_path / "run.jsonl"
    events = [
        _event(1, "debate_started", ticker="AAPL", company_name="Apple Inc.", preset="default", personas=[], moderator={}, aggregator={}, caps={}, config_hash="x", tinyic_version="x"),
        _event(2, "debate_completed", phases_completed=[], duration_s=1, result_ref="x"),
    ]
    _write_log(path, events)
    server = servers(path, replay=True)

    status, _, body = _request(server, "GET", "/api/meta", headers=_auth(server))
    assert status == 200
    assert json.loads(body) == {
        "run_id": "aapl-20260714-web1",
        "ticker": "AAPL",
        "status": "replay",
        "seq_high": 2,
        "replay": True,
    }

    parsed = read_events(path)
    status, headers, body = _request(
        server, "GET", "/api/result?fmt=html", headers=_auth(server)
    )
    assert status == 200
    assert body.decode("utf-8") == render_html(parsed)
    assert headers["Content-Disposition"].endswith('.html"')
    status, _, body = _request(
        server, "GET", "/api/result?fmt=md", headers=_auth(server)
    )
    assert status == 200
    assert body.decode("utf-8") == render_markdown(parsed)


def test_live_meta_reports_initial_phase_pause(tmp_path, servers):
    path = tmp_path / "run.jsonl"
    _write_log(path, [])
    server = servers(path, control=RunControl(paused=True))
    status, _, body = _request(server, "GET", "/api/meta", headers=_auth(server))
    assert status == 200
    assert json.loads(body)["paused"] is True


def test_browser_open_is_injected_and_server_is_loopback_only(tmp_path):
    path = tmp_path / "run.jsonl"
    _write_log(path, [])
    opened: list[str] = []
    stderr = io.StringIO()
    server = WebFace.replay(
        path,
        token="browser-token",
        browser_opener=lambda url: opened.append(url),
        asset_loader=_assets,
        stderr=stderr,
    ).start()
    try:
        assert opened == [server.launch_url]
        assert server.launch_url.endswith("/?token=browser-token")
        assert server.launch_url in stderr.getvalue()
        assert server.host == "127.0.0.1"
    finally:
        server.shutdown()
    with pytest.raises(ValueError, match="127.0.0.1"):
        WebServer(path, host="0.0.0.0")


def test_snapshot_ignores_partial_utf8_tail(tmp_path):
    path = tmp_path / "run.jsonl"
    first = json.dumps(_event(1, "debate_started", ticker="AAPL"), ensure_ascii=False)
    second = json.dumps(_event(2, "talk_delta", turn_id="t", text="paused…"), ensure_ascii=False).encode("utf-8")
    ellipsis = "…".encode("utf-8")
    cut = second.rfind(ellipsis) + 2
    path.write_bytes(first.encode("utf-8") + b"\n" + second[:cut])
    server = WebServer(path, token="test-token", open_browser=False)
    snapshot = server.snapshot()
    assert [record.seq for record in snapshot.records] == [1]
