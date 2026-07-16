"""Offline tests for the v2.1 model selector: catalog service + CLI + wizard.

Every network call is a fake behind the catalog service's injected HTTP
transport (the adapters' own ``HttpTransport`` seam); the static snapshot is
proven to never touch it at all. The presets user-overlay precedence
(repo ``tinyic.toml`` baseline, ``~/.tinyic/tinyic.toml`` wins) is exercised
through the ``TINYIC_USER_CONFIG`` sandbox path the suite-wide conftest fixture
pins for hermeticity.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tinyic.auth.manager import AuthManager
from tinyic.auth.profiles import AuthLane, AuthProfile, ProfileKind, ProfileStore
from tinyic.cli import build_parser, main
from tinyic.models.adapters._http import HttpConnectionError
from tinyic.models.catalog import (
    CATALOG_SCHEMA_VERSION,
    PROVIDER_ORDER,
    CatalogService,
    ModelListing,
    ProviderCatalog,
    render_human,
    to_document,
)
from tinyic.models.credentials import StaticCredentialProvider
from tinyic.models.presets import (
    PresetError,
    load_config,
    load_preset,
    set_user_default_binding,
    user_config_path,
)
from tinyic.tui.onboard import (
    BEST_FOR,
    PROVIDER_LABELS,
    OnboardController,
    OnboardScreen,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeResponse:
    def __init__(self, status: int, payload) -> None:
        self._status = status
        self._payload = payload
        self.closed = False

    @property
    def status_code(self) -> int:
        return self._status

    def header(self, _name: str) -> str | None:
        return None

    def iter_lines(self):
        return iter(())

    def read_text(self) -> str:
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload)

    def close(self) -> None:
        self.closed = True


class FakeHttp:
    """Route URL substrings to canned (status, payload) results or exceptions."""

    def __init__(self, routes=()) -> None:
        self.routes = list(routes)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        for fragment, result in self.routes:
            if fragment in request.url:
                if isinstance(result, Exception):
                    raise result
                status, payload = result
                return FakeResponse(status, payload)
        raise AssertionError(f"unexpected catalog URL: {request.url}")

    def close(self) -> None:
        pass


class ExplodingHttp:
    def send(self, request):  # pragma: no cover - the assertion is the point
        raise AssertionError(f"static snapshot must not touch the network: {request.url}")

    def close(self) -> None:
        pass


_KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-CATALOG-SECRET",
    "GEMINI_API_KEY": "goog-CATALOG-SECRET",
    "XAI_API_KEY": "xai-CATALOG-SECRET",
    "MOONSHOT_API_KEY": "moon-CATALOG-SECRET",
    "OPENAI_API_KEY": "sk-openai-CATALOG-SECRET",
}


def _service(routes=(), *, credentials=None, environ=None) -> CatalogService:
    return CatalogService(
        credentials=(
            StaticCredentialProvider(_KEYS) if credentials is None else credentials
        ),
        http=FakeHttp(routes),
        environ={} if environ is None else environ,
    )


def _catalog_for(catalogs, provider: str) -> ProviderCatalog:
    return next(catalog for catalog in catalogs if catalog.provider == provider)


# --------------------------------------------------------------------------- #
# Static snapshot
# --------------------------------------------------------------------------- #

def test_static_snapshot_orders_providers_and_pins_metadata():
    service = CatalogService(credentials=lambda ref: None, http=ExplodingHttp())
    catalogs = service.snapshot()
    assert [catalog.provider for catalog in catalogs] == list(PROVIDER_ORDER)
    openai = _catalog_for(catalogs, "openai")
    assert openai.refreshed is False and openai.warning is None
    sol = openai.listings[0]
    assert sol.model_id == "gpt-5.6-sol"
    assert sol.context_window == 1_050_000
    assert sol.max_output == 128_000
    assert sol.thinking_capable is True
    assert sol.source == "static"
    # thinking capability mirrors the registry profile, e.g. llama has none
    ollama = _catalog_for(catalogs, "ollama")
    by_id = {listing.model_id: listing for listing in ollama.listings}
    assert by_id["qwen3:32b"].thinking_capable is True
    assert by_id["llama3.1:8b"].thinking_capable is False


def test_static_snapshot_never_touches_network_even_with_keys():
    service = CatalogService(
        credentials=StaticCredentialProvider(_KEYS), http=ExplodingHttp()
    )
    catalogs = service.snapshot(refresh=False)
    assert all(not catalog.refreshed for catalog in catalogs)


def test_single_provider_snapshot_and_unknown_provider():
    service = _service()
    (anthropic,) = service.snapshot("anthropic")
    assert anthropic.provider == "anthropic"
    with pytest.raises(KeyError):
        service.snapshot("deepseek")


# --------------------------------------------------------------------------- #
# Live refresh + merge
# --------------------------------------------------------------------------- #

def test_anthropic_refresh_merges_metadata_and_appends_live_models():
    payload = {
        "data": [
            {
                "id": "claude-fable-5",
                "max_input_tokens": 1_000_000,
                "max_tokens": 128_000,
                "capabilities": {"thinking": True},
            },
            {
                "id": "claude-nova-6",
                "max_input_tokens": 2_000_000,
                "capabilities": {"thinking": True},
            },
        ]
    }
    service = _service([("api.anthropic.com/v1/models", (200, payload))])
    (catalog,) = service.snapshot("anthropic", refresh=True)
    assert catalog.refreshed is True and catalog.warning is None
    by_id = {listing.model_id: listing for listing in catalog.listings}
    assert by_id["claude-fable-5"].source == "live"
    assert by_id["claude-fable-5"].context_window == 1_000_000
    # A live-only model is appended after the static catalog order.
    assert catalog.listings[-1].model_id == "claude-nova-6"
    assert by_id["claude-nova-6"].source == "live"
    assert by_id["claude-nova-6"].context_window == 2_000_000
    # Static models the listing omitted are kept, still marked static.
    assert by_id["claude-haiku-4-5"].source == "static"
    assert by_id["claude-haiku-4-5"].context_window == 200_000
    # Auth rode the documented headers.
    request = service._http.requests[0]  # type: ignore[attr-defined]
    assert request.headers["x-api-key"] == _KEYS["ANTHROPIC_API_KEY"]
    assert "anthropic-version" in request.headers


def test_google_refresh_uses_header_not_url_and_strips_prefix():
    payload = {
        "models": [
            {
                "name": "models/gemini-3.5-flash",
                "inputTokenLimit": 1_048_576,
                "outputTokenLimit": 65_536,
                "thinking": True,
            }
        ]
    }
    service = _service([("generativelanguage.googleapis.com", (200, payload))])
    (catalog,) = service.snapshot("google", refresh=True)
    by_id = {listing.model_id: listing for listing in catalog.listings}
    assert by_id["gemini-3.5-flash"].source == "live"
    assert by_id["gemini-3.5-flash"].context_window == 1_048_576
    request = service._http.requests[0]  # type: ignore[attr-defined]
    # The key never appears in the URL — only in the x-goog-api-key header.
    assert _KEYS["GEMINI_API_KEY"] not in request.url
    assert "key=" not in request.url
    assert request.headers["x-goog-api-key"] == _KEYS["GEMINI_API_KEY"]


def test_grok_refresh_parses_language_models_metadata():
    payload = {
        "models": [
            {"id": "grok-4.5", "context_length": 500_000},
            {"id": "grok-4.20-0309-reasoning", "context_length": 1_000_000},
        ]
    }
    service = _service([("api.x.ai/v1/language-models", (200, payload))])
    (catalog,) = service.snapshot("grok", refresh=True)
    by_id = {listing.model_id: listing for listing in catalog.listings}
    assert by_id["grok-4.5"].context_window == 500_000
    # Live metadata never clobbers the registry's thinking knowledge.
    assert by_id["grok-4.5"].thinking_capable is True
    assert by_id["grok-4.20-0309-reasoning"].source == "live"
    request = service._http.requests[0]  # type: ignore[attr-defined]
    assert request.headers["Authorization"] == f"Bearer {_KEYS['XAI_API_KEY']}"


def test_kimi_refresh_honors_moonshot_base_url_env():
    payload = {
        "data": [
            {"id": "kimi-k2.6", "context_length": 262_144, "supports_reasoning": True}
        ]
    }
    service = CatalogService(
        credentials=StaticCredentialProvider(_KEYS),
        http=FakeHttp([("api.moonshot.cn/v1/models", (200, payload))]),
        environ={"MOONSHOT_BASE_URL": "https://api.moonshot.cn/v1"},
    )
    (catalog,) = service.snapshot("kimi", refresh=True)
    assert catalog.refreshed is True
    by_id = {listing.model_id: listing for listing in catalog.listings}
    assert by_id["kimi-k2.6"].context_window == 262_144
    assert by_id["kimi-k2.6"].thinking_capable is True


def test_ollama_refresh_lists_installed_models_without_credentials():
    payload = {"models": [{"name": "qwen3:32b"}, {"name": "phi5:latest"}]}
    service = CatalogService(
        credentials=lambda ref: None,
        http=FakeHttp([("localhost:11434/api/tags", (200, payload))]),
        environ={},
    )
    (catalog,) = service.snapshot("ollama", refresh=True)
    assert catalog.refreshed is True
    by_id = {listing.model_id: listing for listing in catalog.listings}
    assert by_id["qwen3:32b"].source == "live"
    assert by_id["phi5:latest"].source == "live"
    # Static seeds not installed locally stay visible as suggestions.
    assert by_id["llama3.1:8b"].source == "static"


def test_openai_refresh_is_static_only():
    service = _service()
    (catalog,) = service.snapshot("openai", refresh=True)
    assert catalog.refreshed is False
    assert catalog.warning == "refresh_unsupported"
    assert all(listing.source == "static" for listing in catalog.listings)


# --------------------------------------------------------------------------- #
# Per-provider degrade
# --------------------------------------------------------------------------- #

def test_missing_credential_degrades_to_static_with_warning():
    service = CatalogService(
        credentials=lambda ref: None, http=ExplodingHttp(), environ={}
    )
    (catalog,) = service.snapshot("anthropic", refresh=True)
    assert catalog.refreshed is False
    assert catalog.warning == "missing_credential"
    assert all(listing.source == "static" for listing in catalog.listings)


@pytest.mark.parametrize(
    "result",
    [
        (500, {"error": "server"}),
        (200, "this is not json"),
        HttpConnectionError("connect timeout"),
    ],
)
def test_refresh_faults_degrade_to_static(result):
    service = _service([("api.anthropic.com", result)])
    (catalog,) = service.snapshot("anthropic", refresh=True)
    assert catalog.refreshed is False
    assert catalog.warning == "refresh_failed"
    assert all(listing.source == "static" for listing in catalog.listings)


def test_provider_failures_degrade_independently():
    kimi_payload = {"data": [{"id": "kimi-k2.6", "context_length": 262_144}]}
    service = _service(
        [
            ("api.x.ai", HttpConnectionError("down")),
            ("api.moonshot.ai/v1/models", (200, kimi_payload)),
            ("api.anthropic.com", (500, {})),
            ("generativelanguage", (200, {"models": []})),
            ("localhost:11434", (200, {"models": []})),
        ]
    )
    catalogs = service.snapshot(refresh=True)
    assert _catalog_for(catalogs, "grok").warning == "refresh_failed"
    assert _catalog_for(catalogs, "anthropic").warning == "refresh_failed"
    kimi = _catalog_for(catalogs, "kimi")
    assert kimi.refreshed is True and kimi.warning is None
    assert _catalog_for(catalogs, "google").refreshed is True
    assert _catalog_for(catalogs, "openai").warning == "refresh_unsupported"


def test_auth_manager_stored_profile_feeds_can_refresh(tmp_path):
    class MemoryKeyring:
        def __init__(self) -> None:
            self.value = None

        def get_password(self, _s, _u):
            return self.value

        def set_password(self, _s, _u, value):
            self.value = value

    store = ProfileStore(
        keyring_backend=MemoryKeyring(), path=tmp_path / "credentials.json"
    )
    profile = AuthProfile(
        "anthropic:key", ProfileKind.API_KEY, AuthLane.API_KEY, "sk-ant-stored"
    )
    store.put(profile)
    store.set_auth_order("anthropic", [profile.ref])
    manager = AuthManager(store, environ={})
    service = CatalogService(credentials=manager, http=ExplodingHttp())
    assert service.can_refresh("anthropic") is True
    assert service.can_refresh("grok") is False
    assert service.can_refresh("openai") is False  # bare-id listing: never
    assert service.can_refresh("ollama") is True  # local enumeration: always


# --------------------------------------------------------------------------- #
# Documents + rendering (secret-free, schema-versioned)
# --------------------------------------------------------------------------- #

def test_document_is_schema_versioned_and_secret_free():
    service = _service([("api.anthropic.com", (500, {}))])
    catalogs = service.snapshot(refresh=True)
    document = to_document(catalogs)
    assert document["schema_version"] == CATALOG_SCHEMA_VERSION
    assert document["generated_at"].endswith("Z")
    providers = {entry["provider"] for entry in document["providers"]}
    assert providers == set(PROVIDER_ORDER)
    blob = json.dumps(document)
    for secret in _KEYS.values():
        assert secret not in blob
    text = render_human(catalogs)
    for secret in _KEYS.values():
        assert secret not in text


def test_render_human_table_and_warnings():
    catalogs = (
        ProviderCatalog(
            "openai",
            (
                ModelListing(
                    "gpt-5.6-sol", "openai", 1_050_000, 128_000, True, "static"
                ),
            ),
        ),
        ProviderCatalog(
            "grok",
            (ModelListing("grok-4.5", "grok", 500_000, None, True, "static"),),
            warning="missing_credential",
        ),
    )
    text = render_human(catalogs)
    lines = text.splitlines()
    assert lines[0] == "TinyIC models"
    assert "PROVIDER" in lines[1] and "MODEL" in lines[1]
    assert any("gpt-5.6-sol" in line and "1,050,000" in line for line in lines)
    assert any(
        line.startswith("[WARN] grok:") and "missing_credential" in line
        for line in lines
    )


# --------------------------------------------------------------------------- #
# CLI: tinyic models
# --------------------------------------------------------------------------- #

def test_cli_parser_registers_models():
    parser = build_parser()
    args = parser.parse_args(["models", "anthropic", "--refresh", "--json"])
    assert args.command == "models"
    assert args.provider == "anthropic"
    assert args.refresh is True
    assert args.json_mode is True
    assert callable(args.func)


class _FakeCliService:
    catalogs = (
        ProviderCatalog(
            "openai",
            (ModelListing("gpt-5.6-sol", "openai", 1_050_000, 128_000, True),),
        ),
        ProviderCatalog(
            "grok",
            (ModelListing("grok-4.5", "grok", 500_000, None, True),),
            warning="refresh_failed",
        ),
    )

    def __init__(self, **_kwargs) -> None:
        pass

    def snapshot(self, provider=None, *, refresh=False):
        if provider is not None and provider not in {"openai", "grok"}:
            raise KeyError(f"unknown provider {provider!r}")
        return self.catalogs


def test_cli_models_json_document(monkeypatch, capsys):
    import tinyic.models.catalog as catalog_module

    monkeypatch.setattr(catalog_module, "CatalogService", _FakeCliService)
    assert main(["models", "--refresh", "--json"]) == 0
    out = capsys.readouterr().out
    document = json.loads(out)
    assert document["schema_version"] == CATALOG_SCHEMA_VERSION
    assert [entry["provider"] for entry in document["providers"]] == [
        "openai",
        "grok",
    ]
    assert document["providers"][1]["warning"] == "refresh_failed"
    model = document["providers"][0]["models"][0]
    assert model == {
        "model_id": "gpt-5.6-sol",
        "provider": "openai",
        "context_window": 1_050_000,
        "max_output": 128_000,
        "thinking_capable": True,
        "source": "static",
    }


def test_cli_models_human_degrade_still_exits_zero(monkeypatch, capsys):
    import tinyic.models.catalog as catalog_module

    monkeypatch.setattr(catalog_module, "CatalogService", _FakeCliService)
    assert main(["models", "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "TinyIC models" in out
    assert "[WARN] grok: live refresh degraded (refresh_failed)" in out


def test_cli_models_unknown_provider_exits_two(monkeypatch, capsys):
    import tinyic.models.catalog as catalog_module

    monkeypatch.setattr(catalog_module, "CatalogService", _FakeCliService)
    assert main(["models", "deepseek"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unknown provider" in captured.err


def test_models_json_is_one_clean_document_in_a_fresh_process(tmp_path):
    """STDOUT is a machine channel: importing tinyic.models pulls TinyTroupe's
    stdout disclaimer + config dump, and only a fresh interpreter can prove the
    redirect keeps them off the ``--json`` document (the in-process tests above
    import everything long before ``main`` runs).  Mirrors the doctor's
    subprocess regression test."""

    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        # The offline subprocess must not discover local runtimes or keyrings.
        "PATH": str(tmp_path),
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
    }
    for secret_name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "XAI_API_KEY",
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
    ):
        env.pop(secret_name, None)
    completed = subprocess.run(
        [sys.executable, "-m", "tinyic.cli", "models", "--json"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    document = json.loads(completed.stdout)
    assert document["schema_version"] == CATALOG_SCHEMA_VERSION
    assert completed.stdout.count("\n") == 1
    assert "DISCLAIMER" not in completed.stdout


# --------------------------------------------------------------------------- #
# Presets: the user overlay (~/.tinyic/tinyic.toml)
# --------------------------------------------------------------------------- #

def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_user_overlay_wins_over_base_config(tmp_path, monkeypatch):
    base = _write(
        tmp_path / "tinyic.toml",
        '[presets.default]\nmodel = "openai/gpt-5.2"\nthinking = "high"\n',
    )
    overlay = tmp_path / "user" / "tinyic.toml"
    monkeypatch.setenv("TINYIC_USER_CONFIG", str(overlay))
    _write(overlay, '[presets.default]\nmodel = "anthropic/claude-fable-5"\n')
    preset = load_preset(None, str(base))
    # Overlay wins on model; thinking deep-merges through from the base.
    assert preset.default.model == "anthropic/claude-fable-5"
    assert preset.default.thinking == "high"


def test_user_overlay_merges_over_builtin_when_no_base(tmp_path, monkeypatch):
    overlay = tmp_path / "user" / "tinyic.toml"
    monkeypatch.setenv("TINYIC_USER_CONFIG", str(overlay))
    monkeypatch.delenv("TINYIC_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./tinyic.toml -> built-in base
    _write(overlay, '[presets.default]\nmodel = "kimi/kimi-k2.6"\n')
    config = load_config(None)
    preset = config["presets"]["default"]
    assert preset.default.model == "kimi/kimi-k2.6"
    assert preset.default.thinking == "high"  # built-in default retained
    # Policy switches keep their defaults when the overlay does not set them.
    assert config["auth"]["grok"]["policy_guard"] is True


def test_missing_overlay_leaves_config_untouched(tmp_path, monkeypatch):
    base = _write(
        tmp_path / "tinyic.toml",
        '[presets.default]\nmodel = "openai/gpt-5.2"\nthinking = "high"\n',
    )
    monkeypatch.setenv("TINYIC_USER_CONFIG", str(tmp_path / "nope" / "tinyic.toml"))
    assert load_preset(None, str(base)).default.model == "openai/gpt-5.2"


def test_set_user_default_binding_writes_and_preserves_overlay(tmp_path, monkeypatch):
    overlay = tmp_path / "user" / "tinyic.toml"
    monkeypatch.setenv("TINYIC_USER_CONFIG", str(overlay))
    _write(overlay, "[auth.grok]\npolicy_guard = false\n")
    written = set_user_default_binding("anthropic/claude-fable-5")
    assert written == overlay == user_config_path()
    raw = tomllib.loads(overlay.read_text(encoding="utf-8"))
    assert raw["presets"]["default"]["model"] == "anthropic/claude-fable-5"
    # Pre-existing overlay content survives the write.
    assert raw["auth"]["grok"]["policy_guard"] is False
    # And the merged load honors both overlay keys over a base config.
    base = _write(
        tmp_path / "tinyic.toml", '[presets.default]\nmodel = "openai/gpt-5.2"\n'
    )
    config = load_config(str(base))
    assert config["presets"]["default"].default.model == "anthropic/claude-fable-5"
    assert config["auth"]["grok"]["policy_guard"] is False


def test_set_user_default_binding_writes_thinking_when_given(tmp_path, monkeypatch):
    overlay = tmp_path / "user" / "tinyic.toml"
    monkeypatch.setenv("TINYIC_USER_CONFIG", str(overlay))
    set_user_default_binding("grok/grok-4.5", thinking="high")
    raw = tomllib.loads(overlay.read_text(encoding="utf-8"))
    assert raw["presets"]["default"] == {
        "model": "grok/grok-4.5",
        "thinking": "high",
    }


def test_set_user_default_binding_validates_inputs(tmp_path, monkeypatch):
    overlay = tmp_path / "user" / "tinyic.toml"
    monkeypatch.setenv("TINYIC_USER_CONFIG", str(overlay))
    with pytest.raises(ValueError):
        set_user_default_binding("no-slash-model")
    with pytest.raises(ValueError):
        set_user_default_binding("openai/gpt-5.6-sol", thinking="warp")
    assert not overlay.exists()  # nothing written on validation failure


def test_invalid_overlay_toml_is_a_preset_error(tmp_path, monkeypatch):
    overlay = tmp_path / "user" / "tinyic.toml"
    monkeypatch.setenv("TINYIC_USER_CONFIG", str(overlay))
    _write(overlay, "this = is not toml [")
    with pytest.raises(PresetError):
        load_config(None)


# --------------------------------------------------------------------------- #
# Wizard: provider rows + the MODEL step
# --------------------------------------------------------------------------- #

from tinyic.auth.doctor import ProbeResult, ProbeStatus, build_report  # noqa: E402


class MemoryKeyring:
    def __init__(self) -> None:
        self.value = None

    def get_password(self, _s, _u):
        return self.value

    def set_password(self, _s, _u, value):
        self.value = value


def _probe(provider, lane, *, reason="missing_credential", required=False):
    return ProbeResult(
        provider=provider,
        lane=lane,
        auth_profile=None,
        model_ref=None,
        required=required,
        status=ProbeStatus.OK if reason == "ok" else ProbeStatus.WARNING,
        reason_code=reason,
        message="probe detail",
    )


def _six_provider_report():
    probes = [
        _probe("openai", "api_key"),
        _probe("anthropic", "api_key"),
        _probe("grok", "api_key"),
        _probe("grok", "subscription"),
        _probe("google", "api_key"),
        _probe("kimi", "api_key"),
        _probe("ollama", "local"),
    ]
    return build_report(probes, preset="default")


class FakeCatalog:
    """A controllable stand-in for the wizard's catalog seam."""

    def __init__(self, *, refresh_warning=None, refresh_allowed=True) -> None:
        self.refresh_warning = refresh_warning
        self.refresh_allowed = refresh_allowed
        self.refresh_calls: list[str] = []

    def snapshot(self, provider=None, *, refresh=False):
        static = (
            ModelListing("gpt-5.6-sol", provider, 1_050_000, 128_000, True),
            ModelListing("gpt-5.2", provider, 400_000, None, True),
        )
        if not refresh:
            return (ProviderCatalog(provider, static),)
        self.refresh_calls.append(provider)
        if self.refresh_warning:
            return (
                ProviderCatalog(provider, static, warning=self.refresh_warning),
            )
        live = (*static, ModelListing("gpt-6-preview", provider, source="live"))
        return (ProviderCatalog(provider, live, refreshed=True),)

    def can_refresh(self, provider):
        return self.refresh_allowed


def _controller(tmp_path, *, catalog=None, report=None, config_path=None):
    store = ProfileStore(
        keyring_backend=MemoryKeyring(), path=tmp_path / "credentials.json"
    )
    manager = AuthManager(store, environ={})
    controller = OnboardController(
        manager=manager,
        report_factory=lambda: report or _six_provider_report(),
        config_path=config_path,
        verify_probe=lambda _b, _c: "ok",
        catalog_factory=(lambda: catalog) if catalog is not None else None,
    )
    controller.start()
    return controller


def _ids(controller):
    return [choice.id for choice in controller.choices()]


def _select(controller, choice_id):
    controller.select_index(_ids(controller).index(choice_id))


def test_provider_rows_order_labels_and_copy(tmp_path):
    controller = _controller(tmp_path, catalog=FakeCatalog())
    assert [plan.provider for plan in controller.plans] == [
        "openai",
        "anthropic",
        "grok",
        "google",
        "kimi",
        "ollama",
    ]
    assert PROVIDER_LABELS["grok"] == "Grok"
    assert PROVIDER_LABELS["kimi"] == "Kimi"
    assert "deepseek" not in {plan.provider for plan in controller.plans}
    assert "SuperGrok / X Premium+" in BEST_FOR[("grok", "subscription")]
    assert "official grok CLI login" in BEST_FOR[("grok", "subscription")]
    assert "XAI_API_KEY" in BEST_FOR[("grok", "api_key")]
    kimi_copy = BEST_FOR[("kimi", "api_key")]
    assert "api.moonshot.ai" in kimi_copy and "api.moonshot.cn" in kimi_copy
    google_copy = BEST_FOR[("google", "api_key")]
    assert "API key only" in google_copy
    assert "Antigravity" in google_copy


def test_verified_lane_enters_model_step_with_catalog_rows(tmp_path):
    controller = _controller(tmp_path, catalog=FakeCatalog())
    controller.activate()  # begin -> openai CHOOSE
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key("sk-model-step") is True
    assert controller.screen is OnboardScreen.MODEL
    ids = _ids(controller)
    assert ids == ["model:gpt-5.6-sol", "model:gpt-5.2", "refresh", "keep_default"]
    detail = controller.choices()[0].detail
    assert "context 1,050,000" in detail and "thinking" in detail


def test_choose_model_persists_overlay_and_returns_to_hub(tmp_path):
    config = _write(
        tmp_path / "tinyic.toml",
        '[presets.default]\nmodel = "openai/gpt-5.2"\nthinking = "high"\n',
    )
    controller = _controller(
        tmp_path, catalog=FakeCatalog(), config_path=str(config)
    )
    controller.activate()
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key("sk-model-step") is True
    _select(controller, "model:gpt-5.6-sol")
    controller.activate()
    assert controller.plans[0].chosen_model == "openai/gpt-5.6-sol"
    assert controller.screen is OnboardScreen.DETECT
    overlay = user_config_path()
    raw = tomllib.loads(overlay.read_text(encoding="utf-8"))
    assert raw["presets"]["default"]["model"] == "openai/gpt-5.6-sol"
    # "high" suits gpt-5.6-sol, so the overlay stays minimal (no thinking key).
    assert "thinking" not in raw["presets"]["default"]
    # The merged preset now resolves to the chosen committee default.
    assert load_preset(None, str(config)).default.model == "openai/gpt-5.6-sol"


def test_escape_on_model_step_keeps_default_and_returns_to_hub(tmp_path):
    controller = _controller(tmp_path, catalog=FakeCatalog())
    controller.activate()
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key("sk-model-step") is True
    assert controller.screen is OnboardScreen.MODEL
    assert controller.back() is True
    assert controller.screen is OnboardScreen.DETECT
    assert controller.plans[0].chosen_model is None
    assert not user_config_path().exists()


def test_refresh_action_hidden_when_credentials_do_not_allow(tmp_path):
    controller = _controller(
        tmp_path, catalog=FakeCatalog(refresh_allowed=False)
    )
    controller.activate()
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key("sk-model-step") is True
    assert "refresh" not in _ids(controller)


def test_refresh_merges_live_rows_into_the_menu(tmp_path):
    catalog = FakeCatalog()
    controller = _controller(tmp_path, catalog=catalog)
    controller.activate()
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key("sk-model-step") is True
    _select(controller, "refresh")
    kind, payload = controller.activate()
    assert kind == "verify" and callable(payload)
    assert payload() is True
    assert catalog.refresh_calls == ["openai"]
    assert "model:gpt-6-preview" in _ids(controller)
    assert controller.model_warning is None


def test_refresh_degrade_shows_warning_and_keeps_static_rows(tmp_path):
    catalog = FakeCatalog(refresh_warning="missing_credential")
    controller = _controller(tmp_path, catalog=catalog)
    controller.activate()
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key("sk-model-step") is True
    _select(controller, "refresh")
    _kind, payload = controller.activate()
    assert payload() is False
    assert controller.model_warning == "missing_credential"
    assert controller.screen is OnboardScreen.MODEL
    assert "model:gpt-5.6-sol" in _ids(controller)


def test_thinking_rides_along_when_default_level_unsupported(tmp_path):
    config = _write(
        tmp_path / "tinyic.toml",
        '[presets.default]\nmodel = "openai/gpt-5.6-sol"\nthinking = "xhigh"\n',
    )
    report = build_report(
        [_probe("grok", "api_key"), _probe("grok", "subscription")],
        preset="default",
    )
    controller = _controller(
        tmp_path, catalog=None, report=report, config_path=str(config)
    )
    controller.activate()  # begin -> grok CHOOSE (real static catalog seam)
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key("xai-key") is True
    assert controller.screen is OnboardScreen.MODEL
    _select(controller, "model:grok-4.5")
    controller.activate()
    raw = tomllib.loads(user_config_path().read_text(encoding="utf-8"))
    # grok-4.5 has no xhigh -> the nearest supported level rides along, so the
    # merged config keeps passing strict config-time validation.
    assert raw["presets"]["default"] == {
        "model": "grok/grok-4.5",
        "thinking": "high",
    }
    assert load_preset(None, str(config)).default.model == "grok/grok-4.5"


def test_summary_shows_default_model_and_chosen_model(tmp_path):
    config = _write(
        tmp_path / "tinyic.toml",
        '[presets.default]\nmodel = "openai/gpt-5.2"\nthinking = "high"\n',
    )
    controller = _controller(
        tmp_path, catalog=FakeCatalog(), config_path=str(config)
    )
    controller.activate()
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key("sk-model-step") is True
    _select(controller, "model:gpt-5.6-sol")
    controller.activate()
    assert controller.screen is OnboardScreen.DETECT
    _select(controller, "finish")
    controller.activate()
    assert controller.screen is OnboardScreen.SUMMARY
    assert controller.summary is not None
    assert controller.summary.default_model == "openai/gpt-5.6-sol"
    assert controller.plans[0].chosen_model == "openai/gpt-5.6-sol"

    from tinyic.tui.onboard import OnboardApp

    body = OnboardApp(controller, threaded_verify=False).render_body().plain
    assert "Committee default model: openai/gpt-5.6-sol" in body
    assert "model openai/gpt-5.6-sol" in body
