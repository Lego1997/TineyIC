"""Offline tests for the FR-2.4 onboarding wizard (``tinyic onboard``).

The wizard's every side effect — the doctor report (DETECT), the device-code
login (CONNECT), and the one-token verification (VERIFY) — is an injected seam,
so this whole suite runs offline with fakes. The real network calls stay behind
the ``live_api`` marker and a human running ``tinyic onboard``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyic.auth.doctor import ProbeResult, ProbeStatus, build_report
from tinyic.auth.manager import AuthManager
from tinyic.auth.profiles import AuthLane, AuthProfile, ProfileKind, ProfileStore
from tinyic.cli import build_parser, main
from tinyic.tui.onboard import (
    ANTHROPIC_POLICY_NOTE,
    NEXT_STEP_HINT,
    LaneStatus,
    OnboardApp,
    OnboardController,
    OnboardScreen,
    build_controller,
)


# --------------------------------------------------------------------------- #
# Fakes / builders
# --------------------------------------------------------------------------- #

class MemoryKeyring:
    """A usable in-memory keyring backend (priority absent -> treated usable)."""

    def __init__(self) -> None:
        self.value: str | None = None

    def get_password(self, _service: str, _username: str) -> str | None:
        return self.value

    def set_password(self, _service: str, _username: str, value: str) -> None:
        self.value = value


def _store(tmp_path: Path, *profiles: AuthProfile, keyring=True) -> ProfileStore:
    store = ProfileStore(
        keyring_backend=MemoryKeyring() if keyring else None,
        path=tmp_path / "credentials.json",
    )
    by_provider: dict[str, list[str]] = {}
    for profile in profiles:
        store.put(profile)
        by_provider.setdefault(profile.provider, []).append(profile.ref)
    for provider, refs in by_provider.items():
        store.set_auth_order(provider, refs)
    return store


def _manager(
    store: ProfileStore, *, policy_guard: bool = True, environ: dict | None = None
) -> AuthManager:
    return AuthManager(
        store,
        environ={} if environ is None else environ,
        anthropic_policy_guard=policy_guard,
    )


def _probe(
    provider: str,
    lane: str,
    *,
    status: str = "warning",
    reason: str = "missing_credential",
    required: bool = False,
    profile: str | None = None,
    model: str | None = None,
) -> ProbeResult:
    marker = {
        "ok": ProbeStatus.OK,
        "warning": ProbeStatus.WARNING,
        "error": ProbeStatus.ERROR,
    }[status]
    return ProbeResult(
        provider=provider,
        lane=lane,
        auth_profile=profile,
        model_ref=model,
        required=required,
        status=marker,
        reason_code=reason,
        message="probe detail",
    )


def _default_report(*, openai_key_ok: bool = False, extra=()):
    probes = [
        _probe(
            "openai",
            "api_key",
            status="ok" if openai_key_ok else "error",
            reason="ok" if openai_key_ok else "missing_credential",
            required=True,
            profile="openai:key" if openai_key_ok else None,
            model="openai/gpt-5.2",
        ),
        _probe("openai", "subscription", reason="runtime_unavailable"),
        _probe("anthropic", "api_key"),
        _probe("anthropic", "subscription", reason="runtime_unavailable"),
        _probe("google", "api_key"),
        _probe("ollama", "local", reason="runtime_unavailable"),
        *extra,
    ]
    return build_report(probes, preset="default")


def _controller(
    tmp_path,
    *,
    report=None,
    verify=None,
    manager=None,
    login_factory=None,
    policy_guard=True,
    config_path=None,
) -> OnboardController:
    manager = manager or _manager(_store(tmp_path), policy_guard=policy_guard)
    report = report if report is not None else _default_report()
    controller = OnboardController(
        manager=manager,
        report_factory=lambda: report,
        config_path=config_path,
        verify_probe=verify or (lambda _b, _c: "ok"),
        openai_login_factory=login_factory,
    )
    controller.start()
    return controller


def _ids(controller: OnboardController) -> list[str]:
    return [choice.id for choice in controller.choices()]


def _select(controller: OnboardController, choice_id: str) -> None:
    controller.select_index(_ids(controller).index(choice_id))


class FakeLoginSession:
    """A context-managed stand-in for ``CodexLoginSession`` (start/wait)."""

    def __init__(
        self, *, user_code="ABCD-EFGH", url="https://auth.example/device",
        success=True, raise_on_wait=False,
    ) -> None:
        self.user_code = user_code
        self.url = url
        self.success = success
        self.raise_on_wait = raise_on_wait
        self.opened = False
        self.closed = False
        self.started = None
        self.waited = False

    def __enter__(self) -> "FakeLoginSession":
        self.opened = True
        return self

    def __exit__(self, *_args) -> bool:
        self.closed = True
        return False

    def start(self, mode):
        from tinyic.auth.openai import LoginChallenge, LoginMode

        self.started = mode
        return LoginChallenge(
            LoginMode.DEVICE_CODE,
            "login-fake",
            verification_url=self.url,
            user_code=self.user_code,
        )

    def wait(self, _challenge):
        from tinyic.auth.openai import OpenAIAuthProbe, OpenAIAuthReason

        self.waited = True
        if self.raise_on_wait:
            raise RuntimeError("app-server crashed")
        reason = OpenAIAuthReason.OK if self.success else OpenAIAuthReason.INVALID_CREDENTIAL
        return OpenAIAuthProbe(reason, "runtime", "codex_runtime")


# --------------------------------------------------------------------------- #
# DETECT
# --------------------------------------------------------------------------- #

def test_start_builds_ordered_plans_and_extracts_diagnostics(tmp_path):
    report = _default_report(
        extra=[_probe("tinyic", "credential_store", reason="probe_failed", required=True)]
    )
    controller = _controller(tmp_path, report=report)
    assert controller.screen is OnboardScreen.DETECT
    assert [plan.provider for plan in controller.plans] == [
        "openai",
        "anthropic",
        "google",
        "ollama",
    ]
    # The reserved pseudo-provider is a whole-run diagnostic, never a fixable lane.
    assert [d.reason_code for d in controller.diagnostics] == ["probe_failed"]
    assert all(plan.provider != "tinyic" for plan in controller.plans)


def test_detection_lanes_carry_status(tmp_path):
    controller = _controller(tmp_path, report=_default_report(openai_key_ok=True))
    openai = controller.plans[0]
    assert openai.lanes["api_key"].status == "ok"
    assert openai.lanes["api_key"].auth_profile == "openai:key"
    assert openai.already_ok is True


# --------------------------------------------------------------------------- #
# CHOOSE — two-branch chooser + copy + policy
# --------------------------------------------------------------------------- #

def test_choose_offers_two_branches_with_best_for_copy(tmp_path):
    controller = _controller(tmp_path)
    controller.activate()  # begin -> openai CHOOSE
    assert controller.screen is OnboardScreen.CHOOSE
    assert _ids(controller) == ["subscription", "api_key", "skip"]
    sub = controller.choices()[0]
    key = controller.choices()[1]
    assert sub.label == "Use your subscription"
    assert key.label == "Enter an API key"
    assert "Best for:" in sub.detail and "5-hour" in sub.detail
    assert "Best for:" in key.detail


def test_anthropic_subscription_shows_policy_note(tmp_path):
    controller = _controller(tmp_path)
    controller.activate()  # openai
    _select(controller, "skip")
    controller.activate()  # -> anthropic CHOOSE
    assert controller.current_plan().provider == "anthropic"
    sub = controller.choices()[0]
    assert sub.id == "subscription" and sub.enabled is True
    assert sub.note == ANTHROPIC_POLICY_NOTE
    assert "Claude.ai login" in sub.note and "Pro/Max" in sub.note


def test_policy_guard_off_disables_anthropic_subscription(tmp_path):
    controller = _controller(tmp_path, policy_guard=False)
    controller.activate()  # openai
    _select(controller, "skip")
    controller.activate()  # anthropic
    sub = controller.choices()[0]
    assert sub.id == "subscription"
    assert sub.enabled is False
    assert "policy_guard" in (sub.disabled_reason or "")
    # Activating the disabled branch does not advance and stores nothing.
    _select(controller, "subscription")
    controller.activate()
    assert controller.screen is OnboardScreen.CHOOSE
    assert controller.current_plan().error == sub.disabled_reason


def test_skip_is_first_class(tmp_path):
    controller = _controller(tmp_path)
    controller.activate()  # openai
    _select(controller, "skip")
    controller.activate()
    assert controller.plans[0].outcome == "skipped"
    assert controller.current_plan().provider == "anthropic"


# --------------------------------------------------------------------------- #
# CONNECT + VERIFY — API key
# --------------------------------------------------------------------------- #

def test_api_key_success_verifies_persists_and_advances(tmp_path):
    seen = []

    def verify(binding, candidate):
        seen.append((binding.provider, candidate.ref, candidate.lane.value))
        return "ok"

    store = _store(tmp_path)
    controller = _controller(tmp_path, manager=_manager(store), verify=verify)
    controller.activate()  # openai CHOOSE
    _select(controller, "api_key")
    controller.activate()  # -> CONNECT_KEY
    assert controller.screen is OnboardScreen.CONNECT_KEY

    assert controller.submit_key("sk-live-secret") is True
    # verify saw the ephemeral api-key candidate for this lane
    assert seen == [("openai", "openai:key", "api_key")]
    # persisted through the store, first in auth_order, advanced to next provider
    profile = store.get("openai:key")
    assert profile is not None and profile.lane is AuthLane.API_KEY
    assert store.get_auth_order("openai") == ("openai:key",)
    assert controller.plans[0].outcome == "verified"
    assert controller.plans[0].persisted_lane == "api_key"
    assert controller.current_plan().provider == "anthropic"


def test_api_key_empty_is_rejected_without_verify(tmp_path):
    calls = []
    controller = _controller(tmp_path, verify=lambda b, c: calls.append(1) or "ok")
    controller.activate()
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key("   ") is False
    assert calls == []  # never even probed
    assert controller.current_plan().error == "missing_credential"
    assert controller.screen is OnboardScreen.CONNECT_KEY


def test_failed_verify_never_persists_and_never_overwrites(tmp_path):
    # A working openai:key already exists.
    existing = AuthProfile("openai:key", ProfileKind.API_KEY, AuthLane.API_KEY, "OLD-GOOD")
    store = _store(tmp_path, existing)
    controller = _controller(
        tmp_path,
        manager=_manager(store),
        report=_default_report(openai_key_ok=True),
        verify=lambda b, c: "invalid_credential",
    )
    controller.activate()  # openai CHOOSE
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key("BAD-NEW-KEY") is False
    # The existing working credential is untouched; nothing was overwritten.
    assert store.get("openai:key").secret == "OLD-GOOD"
    assert controller.plans[0].outcome is None
    assert controller.plans[0].error == "invalid_credential"
    assert controller.screen is OnboardScreen.CONNECT_KEY


# --------------------------------------------------------------------------- #
# CONNECT + VERIFY — subscription route markers
# --------------------------------------------------------------------------- #

def test_readthrough_persists_secretless_marker(tmp_path):
    store = _store(tmp_path)
    controller = _controller(
        tmp_path, manager=_manager(store), report=_default_report()
    )
    # Pretend Codex is already signed in so "reuse" is offered.
    controller.plans[0].lanes["subscription"] = LaneStatus(
        lane="subscription", status="ok", reason_code="ok", message="ok",
        auth_profile=None, model_ref=None, required=False,
    )
    controller.activate()  # openai CHOOSE
    _select(controller, "subscription")
    controller.activate()  # -> CONNECT_SUB menu
    assert "reuse" in _ids(controller)
    _select(controller, "reuse")
    kind, payload = controller.activate()
    assert kind == "verify"
    assert payload() is True
    marker = store.get("openai:codex")
    assert marker is not None
    assert marker.kind is ProfileKind.CODEX_READTHROUGH
    assert marker.secret is None and marker.lane is AuthLane.SUBSCRIPTION


def test_anthropic_runtime_persists_secretless_marker(tmp_path):
    store = _store(tmp_path)
    controller = _controller(tmp_path, manager=_manager(store))
    controller.activate()  # openai
    _select(controller, "skip")
    controller.activate()  # anthropic CHOOSE
    _select(controller, "subscription")
    controller.activate()  # CONNECT_SUB
    assert _ids(controller) == ["runtime", "back"]
    assert controller.confirm_runtime() is True
    marker = store.get("anthropic:claude-code")
    assert marker.kind is ProfileKind.CLAUDE_RUNTIME
    assert marker.secret is None and marker.lane is AuthLane.SUBSCRIPTION


def test_ollama_verify_persists_nothing(tmp_path):
    store = _store(tmp_path)
    calls = []
    controller = _controller(
        tmp_path, manager=_manager(store), verify=lambda b, c: calls.append(c) or "ok"
    )
    # advance to ollama by skipping openai, anthropic, google
    controller.activate()  # begin -> openai CHOOSE
    for provider in ("openai", "anthropic", "google"):
        assert controller.current_plan().provider == provider
        _select(controller, "skip")
        controller.activate()
    plan = controller.current_plan()
    assert plan.provider == "ollama"
    _select(controller, "local")
    controller.activate()  # CONNECT_SUB
    assert _ids(controller) == ["ollama", "back"]
    assert controller.confirm_ollama() is True
    # verify ran with no candidate; nothing persisted for ollama
    assert calls == [None]
    assert store.list(provider="ollama") == ()
    assert plan.outcome == "verified"
    assert controller.screen is OnboardScreen.SUMMARY


# --------------------------------------------------------------------------- #
# CONNECT + VERIFY — OpenAI device-code login (invokes the auth entrypoint)
# --------------------------------------------------------------------------- #

def test_device_code_login_renders_code_and_persists_oauth_on_success(tmp_path):
    session = FakeLoginSession(user_code="WXYZ-1234", url="https://auth.example/dev")
    store = _store(tmp_path)
    controller = _controller(
        tmp_path, manager=_manager(store), login_factory=lambda: session
    )
    controller.activate()  # openai CHOOSE
    _select(controller, "subscription")
    controller.activate()  # CONNECT_SUB menu (device only — not detected)
    assert _ids(controller) == ["device", "back"]
    _select(controller, "device")
    kind, _ = controller.activate()
    assert kind == "device"
    assert controller.sub_stage == "device_wait"
    # the code + URL are rendered for the user to authorize
    body = OnboardApp(controller, threaded_verify=False).render_body().plain
    assert "WXYZ-1234" in body and "auth.example/dev" in body

    assert controller.finish_subscription_login() is True
    assert session.waited and session.closed
    marker = store.get("openai:chatgpt")
    assert marker.kind is ProfileKind.OPENAI_OAUTH
    assert marker.secret is None and marker.lane is AuthLane.SUBSCRIPTION
    assert controller.current_plan().provider == "anthropic"


def test_device_code_login_failure_sets_error_and_persists_nothing(tmp_path):
    session = FakeLoginSession(success=False)
    store = _store(tmp_path)
    controller = _controller(
        tmp_path, manager=_manager(store), login_factory=lambda: session
    )
    controller.activate()
    _select(controller, "subscription")
    controller.activate()
    _select(controller, "device")
    controller.activate()  # start login
    assert controller.finish_subscription_login() is False
    assert store.get("openai:chatgpt") is None
    assert controller.current_plan().provider == "openai"  # did not advance
    assert controller.current_plan().error == "invalid_credential"
    assert controller.sub_stage == "menu" and session.closed


def test_device_login_wait_crash_is_contained(tmp_path):
    session = FakeLoginSession(raise_on_wait=True)
    controller = _controller(tmp_path, login_factory=lambda: session)
    controller.activate()
    _select(controller, "subscription")
    controller.activate()
    _select(controller, "device")
    controller.activate()
    assert controller.finish_subscription_login() is False
    assert controller.current_plan().error == "runtime_unavailable"
    assert session.closed


# --------------------------------------------------------------------------- #
# Navigation — escape backs out safely at every step
# --------------------------------------------------------------------------- #

def test_escape_backs_out_at_every_step_and_exits_at_detect(tmp_path):
    controller = _controller(tmp_path)
    # DETECT: escape asks the app to exit
    assert controller.back() is False

    controller.activate()  # -> CHOOSE openai
    _select(controller, "api_key")
    controller.activate()  # -> CONNECT_KEY
    assert controller.back() is True and controller.screen is OnboardScreen.CHOOSE

    _select(controller, "subscription")
    controller.activate()  # -> CONNECT_SUB
    assert controller.back() is True and controller.screen is OnboardScreen.CHOOSE

    # From the first provider's CHOOSE, back returns to DETECT
    assert controller.back() is True and controller.screen is OnboardScreen.DETECT


def test_escape_cancels_device_wait_and_closes_session(tmp_path):
    session = FakeLoginSession()
    controller = _controller(tmp_path, login_factory=lambda: session)
    controller.activate()
    _select(controller, "subscription")
    controller.activate()
    _select(controller, "device")
    controller.activate()
    assert controller.sub_stage == "device_wait"
    assert controller.back() is True
    assert controller.sub_stage == "menu" and session.closed


# --------------------------------------------------------------------------- #
# Idempotency / verify-and-repair
# --------------------------------------------------------------------------- #

def test_idempotent_rerun_defaults_to_keep_current(tmp_path):
    controller = _controller(tmp_path, report=_default_report(openai_key_ok=True))
    controller.activate()  # -> openai CHOOSE
    # already working -> highlight lands on "keep current", not a reconfigure
    choices = controller.choices()
    assert choices[controller.highlight].id == "skip"
    assert choices[controller.highlight].label == "Keep current setup"
    controller.activate()  # keep
    assert controller.plans[0].outcome == "kept"


# --------------------------------------------------------------------------- #
# SUMMARY
# --------------------------------------------------------------------------- #

def test_summary_resolves_persona_bindings_backend_and_hint(tmp_path):
    # A verified api key + a bespoke default preset so resolution is deterministic.
    config = tmp_path / "tinyic.toml"
    config.write_text(
        '[presets.default]\nmodel = "openai/gpt-5.2"\nthinking = "high"\n',
        encoding="utf-8",
    )
    key = AuthProfile("openai:key", ProfileKind.API_KEY, AuthLane.API_KEY, "sk-x")
    store = _store(tmp_path, key)
    controller = _controller(
        tmp_path,
        manager=_manager(store),
        config_path=str(config),
        report=_default_report(openai_key_ok=True),
    )
    # skip all four providers -> SUMMARY
    controller.activate()  # begin
    for _ in range(4):
        _select(controller, "skip")
        controller.activate()
    assert controller.screen is OnboardScreen.SUMMARY
    summary = controller.summary
    assert summary is not None
    assert summary.backend == "keyring"
    assert len(summary.rows) == 6
    row = summary.rows[0]
    assert row.model_ref == "openai/gpt-5.2"
    assert row.thinking == "high"
    assert row.lane == "api_key"
    assert row.auth_ref == "openai:key"
    body = OnboardApp(controller, threaded_verify=False).render_body().plain
    assert NEXT_STEP_HINT in body
    assert "keyring" in body


def test_summary_marks_unresolved_when_no_credential(tmp_path):
    config = tmp_path / "tinyic.toml"
    config.write_text(
        '[presets.default]\nmodel = "openai/gpt-5.2"\nthinking = "high"\n',
        encoding="utf-8",
    )
    controller = _controller(
        tmp_path,
        manager=_manager(_store(tmp_path)),
        config_path=str(config),
    )
    controller.activate()  # begin
    for _ in range(4):
        _select(controller, "skip")
        controller.activate()
    row = controller.summary.rows[0]
    assert row.lane is None
    assert row.reason == "missing_credential"


# --------------------------------------------------------------------------- #
# Secret hygiene
# --------------------------------------------------------------------------- #

def test_secret_never_appears_in_rendered_text(tmp_path):
    secret = "sk-SUPER-SECRET-VALUE-9999"
    store = _store(tmp_path)
    controller = _controller(tmp_path, manager=_manager(store))
    controller.activate()  # openai
    _select(controller, "api_key")
    controller.activate()
    assert controller.submit_key(secret) is True
    app = OnboardApp(controller, threaded_verify=False)
    # Not in the body of any screen, not in the summary, not in the persisted repr
    controller.screen = OnboardScreen.SUMMARY
    controller._build_summary()
    assert secret not in app.render_body().plain
    assert secret not in repr(store.get("openai:key"))


def test_masked_input_is_a_password_field(tmp_path):
    controller = _controller(tmp_path)
    app = OnboardApp(controller)
    assert app._key_input.password is True


# --------------------------------------------------------------------------- #
# Textual pilots
# --------------------------------------------------------------------------- #

async def test_app_mounts_and_renders_detection(tmp_path):
    controller = _controller(tmp_path)
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.render_body().plain
        assert "Detected access" in body
        assert "OpenAI" in body
        # enter begins setup -> CHOOSE
        await pilot.press("enter")
        await pilot.pause()
        assert controller.screen is OnboardScreen.CHOOSE
        assert "Use your subscription" in app.render_body().plain


async def test_app_api_key_flow_persists_via_pilot(tmp_path):
    store = _store(tmp_path)
    controller = _controller(tmp_path, manager=_manager(store))
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # begin -> openai CHOOSE
        await pilot.pause()
        # move to "api_key" (index 1) and select
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert controller.screen is OnboardScreen.CONNECT_KEY
        app.query_one("#key-input").value = "sk-pilot-secret"
        await pilot.press("enter")
        await pilot.pause()
        assert store.get("openai:key") is not None
        assert controller.current_plan().provider == "anthropic"


async def test_app_escape_exits_from_detect(tmp_path):
    controller = _controller(tmp_path)
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.return_code == 0


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #

def test_cli_parser_registers_onboard():
    parser = build_parser()
    args = parser.parse_args(["onboard", "--config", "x.toml"])
    assert args.command == "onboard"
    assert args.config == "x.toml"
    assert callable(args.func)


def test_cli_onboard_invokes_run_onboard(monkeypatch):
    called = {}

    def fake_run_onboard(*, config_path=None):
        called["config_path"] = config_path

    import tinyic.tui.onboard as onboard_module

    monkeypatch.setattr(onboard_module, "run_onboard", fake_run_onboard)
    assert main(["onboard", "--config", "cfg.toml"]) == 0
    assert called == {"config_path": "cfg.toml"}


def test_build_controller_defaults_wire_the_doctor_seam(tmp_path):
    # No manager/report injected: build_controller must construct both without
    # touching the network (doctor is local unless live=True).
    config = tmp_path / "tinyic.toml"
    config.write_text(
        "[auth.anthropic]\npolicy_guard = false\n"
        '[presets.default]\nmodel = "openai/gpt-5.2"\nthinking = "high"\n',
        encoding="utf-8",
    )
    controller = build_controller(config_path=str(config))
    controller.start()
    assert controller.manager.anthropic_policy_guard is False
    assert [p.provider for p in controller.plans]  # detection produced plans


# --------------------------------------------------------------------------- #
# ProfileStore.backend accessor (summary "keyring vs file")
# --------------------------------------------------------------------------- #

def test_profile_store_backend_reports_keyring_and_file(tmp_path):
    keyring_store = ProfileStore(
        keyring_backend=MemoryKeyring(), path=tmp_path / "a.json"
    )
    assert keyring_store.backend == "keyring"

    class Unusable:
        def get_password(self, *_):
            raise RuntimeError("nope")

        def set_password(self, *_):
            raise RuntimeError("nope")

    file_store = ProfileStore(keyring_backend=Unusable(), path=tmp_path / "b.json")
    assert file_store.backend == "file"
