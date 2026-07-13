"""M3 integration tests across config, registry, committee, and events."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tinyic.auth.manager import AuthManager, AuthResolutionError
from tinyic.auth.profiles import AuthLane, AuthProfile, ProfileKind, ProfileStore
from tinyic.debate import _debate_started_payload
from tinyic.events import EventLog
from tinyic.models import (
    BindingSpec,
    ModelBinding,
    Preset,
    StaticCredentialProvider,
    build_committee,
    build_transport,
    load_config,
)
from tinyic.models.adapters.codex_runtime import CodexRuntimeTransport
from tinyic.models.adapters.rotating import RotatingTransport
from tinyic.models.presets import PresetError


class MemoryKeyring:
    def __init__(self) -> None:
        self.value: str | None = None

    def get_password(self, _service: str, _username: str) -> str | None:
        return self.value

    def set_password(self, _service: str, _username: str, value: str) -> None:
        self.value = value


def _store(tmp_path: Path, *profiles: AuthProfile) -> ProfileStore:
    store = ProfileStore(
        keyring_backend=MemoryKeyring(), path=tmp_path / "credentials.json"
    )
    for profile in profiles:
        store.put(profile)
    return store


def _config(path: Path, policy: str | None) -> Path:
    auth = (
        ""
        if policy is None
        else f"[auth.anthropic]\npolicy_guard = {policy}\n\n"
    )
    path.write_text(
        auth
        + "[presets.default]\n"
        + 'model = "openai/gpt-5.2"\n'
        + 'thinking = "high"\n',
        encoding="utf-8",
    )
    return path


def test_auth_policy_config_defaults_true_parses_false_and_rejects_non_bool(
    tmp_path: Path,
) -> None:
    absent = load_config(_config(tmp_path / "absent.toml", None))
    disabled = load_config(_config(tmp_path / "disabled.toml", "false"))

    assert absent["auth"] == {"anthropic": {"policy_guard": True}}
    assert disabled["auth"] == {"anthropic": {"policy_guard": False}}
    configured = AuthManager.from_config(
        tmp_path / "disabled.toml",
        store=_store(tmp_path / "configured"),
        environ={},
    )
    assert configured.anthropic_policy_guard is False

    with pytest.raises(PresetError, match="policy_guard must be a boolean"):
        load_config(_config(tmp_path / "invalid.toml", '"false"'))


def test_run_debate_passes_configured_policy_manager_to_committee(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tinyic.models as models_module
    from tinyic.debate import run_debate

    config_path = _config(tmp_path / "tinyic.toml", "false")
    sentinel_manager = object()
    seen: dict[str, object] = {}

    class ExpectedStop(RuntimeError):
        pass

    class FakeSession:
        def unregister_agent(self, _persona) -> None:
            return None

    class LegacyClient:
        def get_cost_stats(self):
            return {}

    personas = iter(
        [
            type("Persona", (), {"name": "Warren Buffett"})(),
            type("Persona", (), {"name": "Benjamin Graham"})(),
        ]
    )
    monkeypatch.setattr(
        "tinyic.personas.registry.load_persona",
        lambda _name, session=None: next(personas),
    )
    monkeypatch.setattr("tinytroupe.clients.client", lambda: LegacyClient())
    def manager_from_config(_cls, path=None, **_kwargs):
        seen["config_path"] = path
        return sentinel_manager

    monkeypatch.setattr(
        AuthManager, "from_config", classmethod(manager_from_config)
    )

    def stop_build(_preset, _personas, **kwargs):
        seen["credentials"] = kwargs["credentials"]
        raise ExpectedStop

    monkeypatch.setattr(models_module, "build_committee", stop_build)
    log_path = tmp_path / "policy.jsonl"
    with EventLog("aapl-20260713-pol1", path=log_path) as event_log:
        with pytest.raises(ExpectedStop):
            run_debate(
                "AAPL",
                ["warren_buffett", "benjamin_graham"],
                data_package=type(
                    "DataPackage",
                    (),
                    {"ticker": "AAPL", "company_name": "Apple"},
                )(),
                session=FakeSession(),
                event_log=event_log,
                preset="default",
                config_path=config_path,
            )

    assert seen == {
        "config_path": config_path,
        "credentials": sentinel_manager,
    }


def test_subscription_only_model_rejects_plain_api_key_before_child_transport() -> None:
    with pytest.raises(AuthResolutionError) as caught:
        build_transport(
            ModelBinding("openai/gpt-5.6-sol"),
            StaticCredentialProvider({"OPENAI_API_KEY": "must-not-be-used"}),
        )

    assert caught.value.reason_code == "subscription_required"


def test_auth_manager_does_not_require_a_credential_for_local_ollama(
    tmp_path: Path,
) -> None:
    manager = AuthManager(_store(tmp_path), environ={})

    transport = build_transport(ModelBinding("ollama/qwen3:32b"), manager)

    assert type(transport).__name__ == "OpenAICompatibleAdapter"


def test_registry_dispatches_codex_profile_to_official_subscription_runtime(
    tmp_path: Path,
) -> None:
    profile = AuthProfile(
        "openai:chatgpt",
        ProfileKind.CODEX_READTHROUGH,
        AuthLane.SUBSCRIPTION,
    )
    store = _store(tmp_path, profile)
    store.set_auth_order("openai", [profile.ref])
    manager = AuthManager(store, environ={})

    transport = build_transport(
        ModelBinding("openai/gpt-5.6-sol"), manager
    )

    assert isinstance(transport, RotatingTransport)
    child = transport._transport_for(0, transport._candidates[0])
    assert isinstance(child, CodexRuntimeTransport)
    assert child.binding.model_ref == "openai/gpt-5.6-sol"


def test_registry_dispatches_claude_profile_to_official_subscription_runtime(
    tmp_path: Path,
) -> None:
    from tinyic.models.adapters.claude_runtime import ClaudeRuntimeTransport

    profile = AuthProfile(
        "anthropic:claude-code",
        ProfileKind.CLAUDE_RUNTIME,
        AuthLane.SUBSCRIPTION,
    )
    store = _store(tmp_path, profile)
    store.set_auth_order("anthropic", [profile.ref])
    manager = AuthManager(store, environ={})

    transport = build_transport(
        ModelBinding("anthropic/claude-opus-4-8"), manager
    )

    assert isinstance(transport, RotatingTransport)
    child = transport._transport_for(0, transport._candidates[0])
    assert isinstance(child, ClaudeRuntimeTransport)
    assert child.binding.model_ref == "anthropic/claude-opus-4-8"


def test_call_seam_suppresses_stored_anthropic_subscription_when_guard_off(
    tmp_path: Path,
) -> None:
    """The direct credential-provider seam honors the Anthropic policy guard.

    A stored Anthropic subscription profile resolved by ref through
    ``AuthManager.__call__`` must return ``None`` when ``policy_guard`` is off
    (a legal boundary), mirroring ``candidates()`` suppression, and must return
    the secret when the guard is on. Regression: previously only the literal
    ``CLAUDE_CODE_OAUTH_TOKEN`` env ref was guarded in ``__call__``, so a stored
    subscription profile ref leaked its secret through this seam.
    """
    profile = AuthProfile(
        "anthropic:oauth",
        ProfileKind.CLAUDE_OAUTH_TOKEN,
        AuthLane.SUBSCRIPTION,
        "user-minted-oauth-token",
    )
    store = _store(tmp_path, profile)

    guarded = AuthManager(store, environ={}, anthropic_policy_guard=True)
    assert guarded(profile.ref) == "user-minted-oauth-token"

    unguarded = AuthManager(store, environ={}, anthropic_policy_guard=False)
    assert unguarded(profile.ref) is None


def test_committee_default_is_one_shared_auth_manager() -> None:
    seen: list[object] = []

    class OfflineTransport:
        def generate(self, _request) -> Iterator[object]:
            return iter(())

    preset = Preset(
        name="default",
        default=BindingSpec(model="openai/gpt-5.2", thinking="high"),
    )
    build_committee(
        preset,
        [("warren_buffett", "Warren Buffett")],
        transport_factory=lambda _binding, credentials: (
            seen.append(credentials) or OfflineTransport()
        ),
    )

    assert len(seen) == 3
    assert isinstance(seen[0], AuthManager)
    assert all(credentials is seen[0] for credentials in seen)


def test_committee_default_honors_the_configured_anthropic_policy_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _config(tmp_path / "tinyic.toml", "false")
    monkeypatch.setenv("TINYIC_CONFIG", str(config_path))
    seen: list[AuthManager] = []

    class OfflineTransport:
        def generate(self, _request) -> Iterator[object]:
            return iter(())

    preset = Preset(
        name="default",
        default=BindingSpec(model="openai/gpt-5.2", thinking="high"),
    )
    build_committee(
        preset,
        [("warren_buffett", "Warren Buffett")],
        transport_factory=lambda _binding, credentials: (
            seen.append(credentials) or OfflineTransport()
        ),
    )

    assert seen
    assert all(manager.anthropic_policy_guard is False for manager in seen)


def test_debate_started_reports_resolved_fallback_not_fictitious_default(
    tmp_path: Path,
) -> None:
    profile = AuthProfile(
        "openai:chatgpt",
        ProfileKind.CODEX_READTHROUGH,
        AuthLane.SUBSCRIPTION,
    )
    store = _store(tmp_path, profile)
    store.set_auth_order("openai", [profile.ref])
    manager = AuthManager(store, environ={})
    preset = Preset(
        name="fallback",
        default=BindingSpec(
            model="openai/gpt-5.2",
            thinking="high",
            auth_profile="openai:missing",
        ),
    )
    committee = build_committee(
        preset,
        [("warren_buffett", "Warren Buffett")],
        credentials=manager,
    )
    persona = type("Persona", (), {"name": "Warren Buffett"})()

    payload = _debate_started_payload("AAPL", "Apple", [persona], committee)

    assert payload["personas"][0]["auth_profile"] == profile.ref
