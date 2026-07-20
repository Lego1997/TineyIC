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


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    overlay_path = tmp_path / "overlay" / "tinyic.toml"
    monkeypatch.setenv("TINYIC_USER_CONFIG", str(overlay_path))
    return overlay_path


def test_committee_get_default_then_overlay(studios, user_persona, overlay):
    server = studios()
    status, payload = _json(_request(server, "GET", "/api/committee", headers=_auth(server)))
    assert status == 200 and payload["source"] == "default"
    assert "warren_buffett" in payload["committee"]

    status, payload = _json(
        _post_json(
            server, "/api/committee",
            {"committee": ["warren_buffett", "test_investor"]}, method="PUT",
        )
    )
    assert status == 200 and payload["source"] == "overlay"
    assert payload["committee"] == ["warren_buffett", "test_investor"]
    assert "committee" in overlay.read_text(encoding="utf-8")

    status, payload = _json(
        _post_json(server, "/api/committee", {"committee": None}, method="PUT")
    )
    assert status == 200 and payload["source"] == "default"
    assert "committee" not in overlay.read_text(encoding="utf-8")


def test_committee_validation(studios, user_persona, overlay):
    server = studios()
    status, payload = _json(
        _post_json(server, "/api/committee", {"committee": ["warren_buffett"]}, method="PUT")
    )
    assert status == 400 and payload["error"] == "invalid_committee"
    status, payload = _json(
        _post_json(
            server, "/api/committee",
            {"committee": ["warren_buffett", "ghost"]}, method="PUT",
        )
    )
    assert status == 400 and payload["error"] == "unknown_persona"
    assert payload["unknown"] == ["ghost"]


from tinyic.web.research import ResearchSeams


class _FakeBinding:
    def __init__(self, model_ref, auth_profile=None):
        self.model_ref = model_ref
        self.provider = model_ref.split("/")[0]
        self.auth_profile = auth_profile


class _FakePreset:
    name = "fake"

    class default:
        model = "openai/gpt-5.6-sol"

        @staticmethod
        def to_binding(where=""):
            return _FakeBinding("openai/gpt-5.6-sol")

    personas: dict = {}

    @staticmethod
    def persona_binding(_name):
        return _FakeBinding("openai/gpt-5.6-sol")

    @staticmethod
    def aggregator_binding():
        return _FakeBinding("openai/gpt-5.6-sol")

    @staticmethod
    def moderator_binding():
        return _FakeBinding("openai/gpt-5.6-sol")


def _seams(backend=None, selector_error=None):
    def selector(bindings, credentials, **kwargs):
        if selector_error is not None:
            raise selector_error
        return backend
    return ResearchSeams(
        credentials_factory=lambda: object(),
        backend_selector=selector,
        preset_loader=lambda: _FakePreset(),
    )


def test_estimate_returns_cli_numbers(studios, user_persona):
    backend = type("B", (), {"provider": "openai", "model_ref": "openai/gpt-5.6-sol"})()
    server = studios(research_seams=_seams(backend))
    status, payload = _json(
        _post_json(server, "/api/research/estimate", {"investor_name": "New Investor"})
    )
    assert status == 200
    assert payload["slug"] == "new_investor"
    assert payload["provider"] == "openai"
    assert payload["effective_max_searches"] == 12
    assert payload["estimate"]["planned_searches"] == 12
    assert payload["estimate"]["planned_calls"] == 19


def test_estimate_collision_and_lane_failures(studios, user_persona):
    backend = type("B", (), {"provider": "openai", "model_ref": "openai/gpt-5.6-sol"})()
    server = studios(research_seams=_seams(backend))
    status, payload = _json(
        _post_json(server, "/api/research/estimate", {"investor_name": "Warren Buffett"})
    )
    assert status == 409 and payload["error"] == "builtin_persona_collision"
    status, payload = _json(
        _post_json(server, "/api/research/estimate", {"investor_name": "Test Investor"})
    )
    assert status == 409 and payload["error"] == "persona_exists"
    status, payload = _json(
        _post_json(
            server, "/api/research/estimate",
            {"investor_name": "Test Investor", "force": True},
        )
    )
    assert status == 200

    class _NoLane(Exception):
        reason_code = "no_search_capable_lane"

    server = studios(research_seams=_seams(selector_error=_NoLane("none")))
    status, payload = _json(
        _post_json(server, "/api/research/estimate", {"investor_name": "New Investor"})
    )
    assert status == 403 and payload["error"] == "no_search_capable_lane"


def test_estimate_rejects_bad_requests(studios, user_persona):
    server = studios(research_seams=_seams(None))
    for body in (
        {},
        {"investor_name": "  "},
        {"investor_name": "X", "max_searches": 0},
        {"investor_name": "X", "max_searches": 17},
        {"investor_name": "X", "max_searches": "four"},
    ):
        status, payload = _json(_post_json(server, "/api/research/estimate", body))
        assert status == 400 and payload["error"] == "invalid_research_request"


class _FakeUsage:
    calls = 3
    search_calls = 2
    cost_usd = 0.01


def _fake_result(slug, output_dir):
    from pathlib import Path as _P

    return type(
        "R", (),
        {
            "slug": slug, "investor_name": "New Investor",
            "agent_path": _P(output_dir) / f"{slug}.agent.json",
            "dossier_path": _P(output_dir) / f"{slug}.dossier.md",
            "source_count": 5, "domain_count": 4, "quality": "normal",
            "usage": _FakeUsage(),
        },
    )()


class _FakeFactory:
    gate = None  # set to a threading.Event to block mid-run
    raises = None  # set to an exception to fail the run

    def __init__(self, backend, progress=None):
        self._progress = progress or (lambda _stage: None)

    def run(self, request):
        self._progress("planning")
        self._progress("search:philosophy")
        if _FakeFactory.gate is not None:
            _FakeFactory.gate.wait(timeout=5)
        if _FakeFactory.raises is not None:
            raise _FakeFactory.raises
        self._progress("writing")
        return _fake_result(request.slug, request.output_dir)


def _job_seams():
    backend = type("B", (), {"provider": "openai", "model_ref": "openai/gpt-5.6-sol"})()
    seams = _seams(backend)
    return ResearchSeams(
        credentials_factory=seams.credentials_factory,
        backend_selector=seams.backend_selector,
        preset_loader=seams.preset_loader,
        factory_class=_FakeFactory,
    )


def _sse_frames(body: bytes) -> list[dict]:
    frames = []
    for block in body.decode("utf-8").split("\n\n"):
        data = [line[5:] for line in block.splitlines() if line.startswith("data:")]
        if data:
            frames.append(json.loads("".join(data)))
    return frames


def test_research_job_lifecycle_over_sse(studios, user_persona):
    _FakeFactory.gate = None
    _FakeFactory.raises = None
    server = studios(research_seams=_job_seams())
    status, payload = _json(
        _post_json(server, "/api/research", {"investor_name": "New Investor", "confirmed": True})
    )
    assert status == 202 and payload["job_id"]
    status, _h, body = _request(
        server, "GET", f"/api/research/{payload['job_id']}/events", headers=_auth(server)
    )
    assert status == 200
    frames = _sse_frames(body)
    assert [frame["type"] for frame in frames] == [
        "job_started", "stage", "stage", "stage", "job_completed",
    ]
    assert frames[1]["payload"] == {"stage": "planning"}
    assert frames[2]["payload"] == {"stage": "search", "angle": "philosophy"}
    done = frames[-1]["payload"]
    assert done["slug"] == "new_investor"
    assert done["quality"] == "normal"
    assert done["usage"]["search_calls"] == 2
    # The job_started event carries the confirmed estimate.
    assert frames[0]["payload"]["estimate"]["planned_searches"] == 12


def test_research_requires_confirmation_and_single_active(studios, user_persona):
    import threading

    _FakeFactory.gate = threading.Event()
    _FakeFactory.raises = None
    try:
        server = studios(research_seams=_job_seams())
        status, payload = _json(
            _post_json(server, "/api/research", {"investor_name": "New Investor"})
        )
        assert status == 400 and payload["error"] == "confirmation_required"
        status, payload = _json(
            _post_json(
                server, "/api/research",
                {"investor_name": "New Investor", "confirmed": True},
            )
        )
        assert status == 202
        status, payload = _json(
            _post_json(
                server, "/api/research",
                {"investor_name": "Another Investor", "confirmed": True},
            )
        )
        assert status == 409 and payload["error"] == "research_job_active"
    finally:
        _FakeFactory.gate.set()
        _FakeFactory.gate = None


def test_research_job_error_reason_codes(studios, user_persona):
    class _ThinSources(Exception):
        reason_code = "insufficient_sources"

    _FakeFactory.gate = None
    _FakeFactory.raises = _ThinSources("only one domain")
    try:
        server = studios(research_seams=_job_seams())
        status, payload = _json(
            _post_json(
                server, "/api/research",
                {"investor_name": "New Investor", "confirmed": True},
            )
        )
        assert status == 202
        status, _h, body = _request(
            server, "GET", f"/api/research/{payload['job_id']}/events", headers=_auth(server)
        )
        frames = _sse_frames(body)
        assert frames[-1]["type"] == "job_error"
        assert frames[-1]["payload"]["reason"] == "insufficient_sources"
    finally:
        _FakeFactory.raises = None


def test_unknown_job_events_404(studios, user_persona):
    server = studios(research_seams=_job_seams())
    status, payload = _json(
        _request(server, "GET", "/api/research/deadbeef/events", headers=_auth(server))
    )
    assert status == 404 and payload["error"] == "not_found"


# -- v2.3.1 regression coverage ------------------------------------------- #


def test_duplicate_builtin_persona_and_edit_copy(studios, user_persona):
    # The documented flagship flow: built-ins are read-only, duplicate to tune.
    server = studios()
    status, payload = _json(
        _post_json(
            server, "/api/personas/warren_buffett/duplicate", {"new_slug": "my_buffett"}
        )
    )
    assert status == 201 and payload == {"slug": "my_buffett"}
    agent_path = user_persona / "my_buffett.agent.json"
    assert agent_path.is_file()
    copy = json.loads(agent_path.read_text(encoding="utf-8"))

    from tinyic.personas.factory.schema import validate_user_agent_spec

    validate_user_agent_spec(copy)  # legacy dict sources must stay editable
    status, payload = _json(
        _post_json(server, "/api/personas/my_buffett", {"agent": copy}, method="PUT")
    )
    assert status == 200 and payload["saved"] is True


def test_duplicate_target_still_protects_builtins(studios, user_persona):
    server = studios()
    status, payload = _json(
        _post_json(
            server, "/api/personas/warren_buffett/duplicate", {"new_slug": "li_lu"}
        )
    )
    assert status == 409 and payload["error"] == "builtin_persona_collision"


def test_delete_heals_overlay_committee(studios, user_persona, overlay):
    server = studios()
    status, _payload = _json(
        _post_json(
            server,
            "/api/committee",
            {"committee": ["warren_buffett", "charlie_munger", "test_investor"]},
            method="PUT",
        )
    )
    assert status == 200
    status, payload = _json(
        _request(server, "DELETE", "/api/personas/test_investor", headers=_auth(server))
    )
    assert status == 200
    assert payload["deleted"] == "test_investor"
    assert payload["committee_updated"] is True
    assert payload["committee"] == ["warren_buffett", "charlie_munger"]
    status, payload = _json(
        _request(server, "GET", "/api/committee", headers=_auth(server))
    )
    assert payload == {
        "committee": ["warren_buffett", "charlie_munger"], "source": "overlay",
    }


def test_delete_clears_undersized_committee(studios, user_persona, overlay):
    server = studios()
    _post_json(
        server,
        "/api/committee",
        {"committee": ["warren_buffett", "test_investor"]},
        method="PUT",
    )
    status, payload = _json(
        _request(server, "DELETE", "/api/personas/test_investor", headers=_auth(server))
    )
    assert status == 200 and payload["committee_cleared"] is True
    status, payload = _json(
        _request(server, "GET", "/api/committee", headers=_auth(server))
    )
    assert payload["source"] == "default"


def test_research_status_endpoint(studios, user_persona):
    import threading

    server = studios(research_seams=_job_seams())
    status, payload = _json(
        _request(server, "GET", "/api/research", headers=_auth(server))
    )
    assert status == 200 and payload == {"job_id": None, "done": True}
    _FakeFactory.gate = threading.Event()
    _FakeFactory.raises = None
    try:
        status, started = _json(
            _post_json(
                server, "/api/research",
                {"investor_name": "New Investor", "confirmed": True},
            )
        )
        assert status == 202
        status, payload = _json(
            _request(server, "GET", "/api/research", headers=_auth(server))
        )
        assert status == 200
        assert payload["job_id"] == started["job_id"] and payload["done"] is False
    finally:
        _FakeFactory.gate.set()
        _FakeFactory.gate = None


def test_sse_resume_honors_last_event_id(studios, user_persona):
    _FakeFactory.gate = None
    _FakeFactory.raises = None
    server = studios(research_seams=_job_seams())
    _status, started = _json(
        _post_json(
            server, "/api/research",
            {"investor_name": "New Investor", "confirmed": True},
        )
    )
    target = f"/api/research/{started['job_id']}/events"
    _status, _headers, full = _request(server, "GET", target, headers=_auth(server))
    assert [frame["seq"] for frame in _sse_frames(full)] == [1, 2, 3, 4, 5]
    _status, _headers, resumed = _request(
        server, "GET", target, headers={**_auth(server), "Last-Event-ID": "3"}
    )
    assert [frame["seq"] for frame in _sse_frames(resumed)] == [4, 5]


def test_terminal_event_survives_buffer_cap(studios, user_persona, monkeypatch):
    from tinyic.web import research as research_module

    monkeypatch.setattr(research_module, "_JOB_BUFFER_LIMIT", 1)
    _FakeFactory.gate = None
    _FakeFactory.raises = None
    server = studios(research_seams=_job_seams())
    _status, started = _json(
        _post_json(
            server, "/api/research",
            {"investor_name": "New Investor", "confirmed": True},
        )
    )
    _status, _headers, body = _request(
        server, "GET", f"/api/research/{started['job_id']}/events", headers=_auth(server)
    )
    frames = _sse_frames(body)
    assert frames[0]["type"] == "job_started"
    assert frames[-1]["type"] == "job_completed"  # exempt from the cap


def test_packaged_assets_served_without_loader():
    import re
    from pathlib import Path as _P

    server = StudioServer(token="t", open_browser=False, stderr=io.StringIO()).start()
    try:
        status, _headers, body = _request(server, "GET", "/", headers=_auth(server))
        assert status == 200 and b"studio.js" in body
        references = sorted(set(re.findall(rb'(?:href|src)="(/assets/[^"]+)"', body)))
        assert references, "studio.html should reference packaged assets"
        for reference in references:
            target = reference.decode("ascii")
            status, headers, asset = _request(
                server, "GET", target, headers=_auth(server)
            )
            assert status == 200 and asset, target
            expected = "text/css" if target.endswith(".css") else "text/javascript"
            assert headers.get("Content-Type", "").startswith(expected), target
    finally:
        server.shutdown()
    assert server.wait(1.0)


def test_builtin_six_pass_relaxed_contract():
    from pathlib import Path as _P

    from tinyic.personas.factory.schema import validate_user_agent_spec
    from tinyic.personas.registry import BUILTIN_PERSONAS

    config_dir = _P(__file__).parents[1] / "src" / "tinyic" / "personas" / "configs"
    for slug in BUILTIN_PERSONAS:
        specification = json.loads(
            (config_dir / f"{slug}.agent.json").read_text(encoding="utf-8")
        )
        validate_user_agent_spec(specification)
