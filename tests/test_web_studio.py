"""Real-socket tests for the persona studio server."""

from __future__ import annotations

import http.client
import io
import json

import pytest

from tinyic.web.studio import StudioServer


def _assets(name: str):
    values = {
        "studio.html": (b"<!doctype html><title>TinyIC Studio</title>", "text/html; charset=utf-8"),
        "studio.js": (b"", "text/javascript; charset=utf-8"),
        "studio.css": (b"", "text/css; charset=utf-8"),
        "base.css": (b"", "text/css; charset=utf-8"),
        "md.js": (b"", "text/javascript; charset=utf-8"),
    }
    return values.get(name)


@pytest.fixture
def studios():
    running: list[StudioServer] = []

    def make(**kwargs) -> StudioServer:
        server = StudioServer(
            token="test-token",
            open_browser=False,
            asset_loader=_assets,
            stderr=io.StringIO(),
            heartbeat_interval=0.03,
            **kwargs,
        ).start()
        running.append(server)
        return server

    yield make
    for server in running:
        server.shutdown()


def _request(server, method, target, *, headers=None, body=None, host=None):
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
    payload = response.read()
    result = response.status, dict(response.getheaders()), payload
    connection.close()
    return result


def _auth(server):
    return {"Authorization": f"Bearer {server.token}"}


def _post_json(server, path, value, *, method="POST"):
    body = json.dumps(value).encode("utf-8")
    return _request(
        server,
        method,
        path,
        headers={**_auth(server), "Content-Type": "application/json; charset=utf-8"},
        body=body,
    )


def _json(response):
    status, _headers, body = response
    return status, json.loads(body.decode("utf-8"))


def test_token_cookie_handoff_and_security_headers(studios):
    server = studios()
    status, _h, _b = _request(server, "GET", "/")
    assert status == 401
    status, headers, _b = _request(server, "GET", "/?token=wrong")
    assert status == 403
    status, headers, _b = _request(server, "GET", "/?token=test-token")
    assert status == 302
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    status, headers, body = _request(server, "GET", "/", headers={"Cookie": cookie})
    assert status == 200
    assert headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert headers["Cache-Control"] == "no-store"
    assert b"TinyIC Studio" in body


def test_host_and_origin_allowlists(studios):
    server = studios()
    status, _h, _b = _request(server, "GET", "/", host="attacker.test")
    assert status == 403
    status, _h, _b = _request(
        server, "GET", "/", headers={**_auth(server), "Origin": "http://evil.test"}
    )
    assert status == 403


def test_state_changing_requires_json(studios):
    server = studios()
    status, _h, _b = _request(
        server,
        "POST",
        "/api/committee",
        headers={**_auth(server), "Content-Type": "application/x-www-form-urlencoded"},
        body=b"a=b",
    )
    assert status == 415
    status, payload = _json(
        _request(server, "OPTIONS", "/", headers=_auth(server))
    )
    assert status == 405 and payload["error"] == "method_not_allowed"


def test_unknown_routes_404_and_assets_served(studios):
    server = studios()
    status, payload = _json(_request(server, "GET", "/api/nope", headers=_auth(server)))
    assert status == 404 and payload["error"] == "not_found"
    status, headers, body = _request(server, "GET", "/assets/base.css", headers=_auth(server))
    assert status == 200 and headers["Content-Type"].startswith("text/css")
    status, _h, _b = _request(
        server, "GET", "/assets/../studio.py", headers=_auth(server)
    )
    assert status == 404


def test_bearer_auth_accepted(studios):
    server = studios()
    status, _h, body = _request(server, "GET", "/", headers=_auth(server))
    assert status == 200 and b"TinyIC Studio" in body
