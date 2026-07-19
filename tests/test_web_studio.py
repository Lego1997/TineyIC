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


@pytest.fixture
def user_persona(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYIC_PERSONAS_DIR", str(tmp_path))
    agent = {
        "type": "TinyPerson",
        "persona": {"name": "Test Investor", "occupation": {"description": "Test"}},
        "tinyic": {
            "schema_version": 1,
            "epithet": "Test epithet",
            "philosophy_hook": "Test hook.",
            "temperament": "balanced",
            "sources": [{"title": "T", "url": "https://example.com", "type": "primary", "accessed": "2026-07-18"}],
        },
    }
    (tmp_path / "test_investor.agent.json").write_text(json.dumps(agent), encoding="utf-8")
    (tmp_path / "test_investor.dossier.md").write_text("# Test Investor\n", encoding="utf-8")
    return tmp_path


def test_personas_list_includes_origins(studios, user_persona):
    server = studios()
    status, payload = _json(_request(server, "GET", "/api/personas", headers=_auth(server)))
    assert status == 200 and payload["schema_version"] == 1
    by_slug = {item["slug"]: item for item in payload["personas"]}
    assert by_slug["warren_buffett"]["origin"] == "built_in"
    assert by_slug["test_investor"]["origin"] == "user"
    assert by_slug["test_investor"]["epithet"] == "Test epithet"
    assert by_slug["test_investor"]["source_count"] == 1


def test_persona_get_returns_agent_and_dossier(studios, user_persona):
    server = studios()
    status, payload = _json(
        _request(server, "GET", "/api/personas/test_investor", headers=_auth(server))
    )
    assert status == 200
    assert payload["origin"] == "user"
    assert payload["agent"]["persona"]["name"] == "Test Investor"
    assert payload["dossier"] == "# Test Investor\n"


def test_persona_get_builtin_and_unknown(studios, user_persona):
    server = studios()
    status, payload = _json(
        _request(server, "GET", "/api/personas/li_lu", headers=_auth(server))
    )
    assert status == 200 and payload["origin"] == "built_in"
    assert payload["agent"]["persona"]["name"]
    status, payload = _json(
        _request(server, "GET", "/api/personas/nobody", headers=_auth(server))
    )
    assert status == 404 and payload["error"] == "unknown_persona"
    status, payload = _json(
        _request(server, "GET", "/api/personas/Bad-Slug", headers=_auth(server))
    )
    assert status == 404 and payload["error"] == "unknown_persona"


def _valid_agent(name="Test Investor"):
    return {
        "type": "TinyPerson",
        "persona": {
            "name": name,
            "style": "Measured.",
            "occupation": {"description": "Epithet"},
            "beliefs": ["Margin of safety."],
            "skills": ["Analysis."],
            "other_facts": [],
            "personality": {"traits": ["patient"]},
            "behaviors": {"general": ["Asks for the downside first."]},
            "preferences": {"interests": [], "likes": [], "dislikes": []},
        },
        "tinyic": {
            "schema_version": 1,
            "epithet": "Epithet",
            "philosophy_hook": "Hook.",
            "temperament": "balanced",
            "decision_checklist": ["a", "b", "c"],
            "signal_rules": [],
            "red_flags": [],
            "famous_quotes": [],
            "sources": [{"title": "T", "url": "https://example.com", "type": "primary", "accessed": "2026-07-18"}],
            "generation": {
                "generated_by": "tinyic persona research",
                "model_ref": "openai/gpt-5.6-sol",
                "date": "2026-07-18",
                "search_calls": 4,
                "quality": "normal",
                "disclaimer": "Educational simulation.",
            },
        },
    }


def test_put_persona_validates_and_saves_atomically(studios, user_persona):
    server = studios()
    agent_path = user_persona / "test_investor.agent.json"
    before = agent_path.read_bytes()
    status, payload = _json(
        _post_json(server, "/api/personas/test_investor", {"agent": {"bad": True}}, method="PUT")
    )
    assert status == 400 and payload["error"] == "validation_failed"
    assert payload["issues"]  # redacted schema diagnostics
    assert agent_path.read_bytes() == before  # nothing written on rejection

    agent = _valid_agent("Renamed Investor")
    status, payload = _json(
        _post_json(
            server, "/api/personas/test_investor",
            {"agent": agent, "dossier": "# Updated\n"}, method="PUT",
        )
    )
    assert status == 200 and payload["saved"] is True
    saved = json.loads(agent_path.read_text(encoding="utf-8"))
    assert saved["persona"]["name"] == "Renamed Investor"
    assert (user_persona / "test_investor.dossier.md").read_text(encoding="utf-8") == "# Updated\n"


def test_put_builtin_refused(studios, user_persona):
    server = studios()
    status, payload = _json(
        _post_json(server, "/api/personas/li_lu", {"agent": _valid_agent()}, method="PUT")
    )
    assert status == 403 and payload["error"] == "builtin_persona_protected"


def test_duplicate_persona(studios, user_persona):
    server = studios()
    status, payload = _json(
        _post_json(server, "/api/personas/test_investor/duplicate", {"new_slug": "copy_one"})
    )
    assert status == 201 and payload["slug"] == "copy_one"
    assert (user_persona / "copy_one.agent.json").is_file()
    assert (user_persona / "copy_one.dossier.md").is_file()
    status, payload = _json(
        _post_json(server, "/api/personas/test_investor/duplicate", {"new_slug": "copy_one"})
    )
    assert status == 409 and payload["error"] == "persona_exists"
    status, payload = _json(
        _post_json(server, "/api/personas/test_investor/duplicate", {"new_slug": "li_lu"})
    )
    assert status == 409 and payload["error"] == "builtin_persona_collision"
    status, payload = _json(
        _post_json(server, "/api/personas/test_investor/duplicate", {"new_slug": "!!"})
    )
    assert status == 400 and payload["error"] == "invalid_persona_slug"


def test_put_relaxed_validation_for_ungenerated_persona(studios, user_persona):
    """Hand-maintained files (no generation block) use the relaxed contract."""
    server = studios()
    body = {
        "agent": {
            "type": "TinyPerson",
            "persona": {"name": "Hand Tuned"},
            "tinyic": {"schema_version": 1, "epithet": "Hand-edited", "temperament": "contrarian"},
        }
    }
    status, payload = _json(
        _post_json(server, "/api/personas/test_investor", body, method="PUT")
    )
    assert status == 200 and payload["saved"] is True

    body["agent"]["tinyic"]["temperament"] = "wild"
    status, payload = _json(
        _post_json(server, "/api/personas/test_investor", body, method="PUT")
    )
    assert status == 400 and payload["error"] == "validation_failed"
    assert any("temperament" in issue for issue in payload["issues"])


def test_delete_persona(studios, user_persona):
    server = studios()
    status, payload = _json(
        _request(server, "DELETE", "/api/personas/li_lu", headers=_auth(server))
    )
    assert status == 403 and payload["error"] == "builtin_persona_protected"
    status, payload = _json(
        _request(server, "DELETE", "/api/personas/test_investor", headers=_auth(server))
    )
    assert status == 200 and payload["deleted"] == "test_investor"
    assert not (user_persona / "test_investor.agent.json").exists()
    assert not (user_persona / "test_investor.dossier.md").exists()
    status, payload = _json(
        _request(server, "DELETE", "/api/personas/test_investor", headers=_auth(server))
    )
    assert status == 404 and payload["error"] == "unknown_persona"
