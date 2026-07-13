"""Offline contract tests for the headless ``tinyic doctor`` seam."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tinyic.auth.doctor import (
    DOCTOR_SCHEMA_VERSION,
    DoctorReport,
    ProbeResult,
    ProbeStatus,
    build_report,
    run_doctor,
)
from tinyic.auth.manager import AuthManager
from tinyic.auth.profiles import AuthLane, AuthProfile, ProfileKind, ProfileStore
from tinyic.cli import build_parser, main


ROOT = Path(__file__).resolve().parents[1]


class MemoryKeyring:
    def __init__(self) -> None:
        self.value: str | None = None

    def get_password(self, _service: str, _username: str) -> str | None:
        return self.value

    def set_password(self, _service: str, _username: str, value: str) -> None:
        self.value = value


def _manager(
    tmp_path: Path,
    *profiles: AuthProfile,
    environ: dict[str, str] | None = None,
    policy_guard: bool = True,
) -> AuthManager:
    store = ProfileStore(
        keyring_backend=MemoryKeyring(), path=tmp_path / "credentials.json"
    )
    for profile in profiles:
        store.put(profile)
    for provider in {profile.provider for profile in profiles}:
        store.set_auth_order(
            provider,
            [profile.ref for profile in profiles if profile.provider == provider],
        )
    return AuthManager(
        store,
        environ={} if environ is None else environ,
        anthropic_policy_guard=policy_guard,
    )


def _config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tinyic.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _probe(
    *,
    provider: str = "openai",
    lane: str = "api_key",
    profile: str | None = "openai:default",
    required: bool = True,
    status: ProbeStatus = ProbeStatus.OK,
    reason: str = "ok",
    message: str = "Credential is available.",
) -> ProbeResult:
    return ProbeResult(
        provider=provider,
        lane=lane,
        auth_profile=profile,
        model_ref="openai/gpt-5.2" if provider == "openai" else None,
        required=required,
        status=status,
        reason_code=reason,
        message=message,
    )


def test_doctor_schema_is_versioned_deliberate_and_secret_free():
    report = build_report(
        [_probe()],
        preset="default",
        clock=lambda: datetime(2026, 7, 13, tzinfo=timezone.utc),
    )

    document = report.to_dict()
    assert document == {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "ok": True,
        "generated_at": "2026-07-13T00:00:00.000Z",
        "preset": "default",
        "probes": [
            {
                "provider": "openai",
                "lane": "api_key",
                "auth_profile": "openai:default",
                "model_ref": "openai/gpt-5.2",
                "required": True,
                "status": "ok",
                "reason_code": "ok",
                "message": "Credential is available.",
            }
        ],
    }
    assert "secret" not in json.dumps(document).casefold()


def test_optional_missing_lanes_do_not_make_a_healthy_preset_fail():
    report = build_report(
        [
            _probe(),
            _probe(
                provider="anthropic",
                lane="subscription",
                profile=None,
                required=False,
                status=ProbeStatus.WARNING,
                reason="missing_credential",
                message="No Claude runtime profile is configured.",
            ),
        ],
        preset="default",
    )
    assert report.ok is True


def test_required_failed_probe_makes_report_unhealthy():
    report = build_report(
        [
            _probe(
                profile=None,
                status=ProbeStatus.ERROR,
                reason="missing_credential",
                message="No usable OpenAI profile is configured.",
            )
        ],
        preset="default",
    )
    assert report.ok is False


@pytest.mark.parametrize(
    "reason",
    [
        "missing_credential",
        "expired",
        "unsupported_thinking_level",
        "policy_disabled",
        "runtime_unavailable",
        "lane_incompatible",
        "usage_limited",
        "probe_failed",
    ],
)
def test_reason_codes_are_stable_nonsecret_strings(reason):
    probe = _probe(
        status=ProbeStatus.ERROR,
        reason=reason,
        message="Profile cannot be used.",
    )
    assert probe.to_dict()["reason_code"] == reason


def test_doctor_cli_parser_is_headless_and_onboard_is_now_wired():
    # ``doctor`` stays a headless, non-UI seam. Its interactive twin
    # ``onboard`` (FR-2.4) is now a first-class subcommand of its own.
    parser = build_parser()
    args = parser.parse_args(["doctor", "--json", "--preset", "default"])
    assert args.command == "doctor"
    assert args.json is True
    assert args.preset == "default"
    assert not hasattr(args, "func") or args.func.__name__ == "_cmd_doctor"
    onboard_args = parser.parse_args(["onboard"])
    assert onboard_args.command == "onboard"
    assert "onboard" in parser.format_help()


def test_doctor_json_stdout_is_one_machine_document(monkeypatch, capsys):
    report = DoctorReport(
        ok=True,
        generated_at="2026-07-13T00:00:00.000Z",
        preset="default",
        probes=(_probe(),),
    )
    monkeypatch.setattr("tinyic.auth.doctor.run_doctor", lambda **_kwargs: report)

    assert main(["doctor", "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == report.to_dict()
    assert captured.out.count("\n") == 1


def test_doctor_json_returns_setup_status_for_unhealthy_required_binding(
    monkeypatch, capsys
):
    report = DoctorReport(
        ok=False,
        generated_at="2026-07-13T00:00:00.000Z",
        preset="default",
        probes=(
            _probe(
                profile=None,
                status=ProbeStatus.ERROR,
                reason="missing_credential",
                message="No usable profile is configured.",
            ),
        ),
    )
    monkeypatch.setattr("tinyic.auth.doctor.run_doctor", lambda **_kwargs: report)

    assert main(["doctor", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_doctor_json_is_one_clean_document_in_a_fresh_process(tmp_path):
    # Keep this subprocess wholly offline even though the installed Agent SDK
    # bundles an official Claude CLI.  Policy-off is already unit-tested to
    # precede runtime discovery/spawn; provider runtime behavior is exercised
    # through injected fakes elsewhere in this module.
    config = _config(
        tmp_path,
        "[auth.anthropic]\n"
        "policy_guard = false\n\n"
        "[presets.default]\n"
        'model = "openai/gpt-5.2"\n'
        'thinking = "high"\n',
    )
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        # The offline subprocess must not discover or launch a real local AI CLI.
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
        [
            sys.executable,
            "-m",
            "tinyic.cli",
            "doctor",
            "--json",
            "--config",
            str(config),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 3
    document = json.loads(completed.stdout)
    assert document["schema_version"] == 1
    assert document["ok"] is False
    assert completed.stdout.count("\n") == 1
    assert "DISCLAIMER" not in completed.stdout


def test_doctor_probes_every_builtin_provider_lane_and_requires_selected_preset(
    tmp_path,
):
    report = run_doctor(
        manager=_manager(tmp_path),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        grok_probe=lambda: "missing_credential",
        runtime_locator=lambda _command: None,
    )

    lanes = {(probe.provider, probe.lane) for probe in report.probes}
    assert lanes == {
        ("openai", "api_key"),
        ("openai", "subscription"),
        ("anthropic", "api_key"),
        ("anthropic", "subscription"),
        ("google", "api_key"),
        ("grok", "api_key"),
        ("grok", "subscription"),
        ("kimi", "api_key"),
        ("ollama", "local"),
    }
    required = [probe for probe in report.probes if probe.required]
    assert [(probe.provider, probe.reason_code) for probe in required] == [
        ("openai", "missing_credential")
    ]
    assert report.ok is False
    assert all(
        probe.status is ProbeStatus.WARNING
        for probe in report.probes
        if not probe.required and probe.status is not ProbeStatus.OK
    )


def test_configured_key_makes_selected_preset_ready_without_live_network(
    tmp_path,
):
    manager = _manager(tmp_path, environ={"OPENAI_API_KEY": "opaque-key"})
    live_calls: list[str] = []

    report = run_doctor(
        manager=manager,
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        live_probe=lambda binding, candidate: live_calls.append(candidate.ref) or "ok",
        runtime_locator=lambda _command: None,
    )

    required = [probe for probe in report.probes if probe.required]
    assert len(required) == 1
    assert required[0].to_dict() == {
        "provider": "openai",
        "lane": "api_key",
        "auth_profile": "openai:env-fallback",
        "model_ref": "openai/gpt-5.6-sol",
        "required": True,
        "status": "ok",
        "reason_code": "ok",
        "message": "API-key credential is available.",
    }
    assert report.ok is True
    assert live_calls == []


def test_live_probe_is_explicit_one_token_seam_and_failure_text_is_sanitized(
    tmp_path,
):
    manager = _manager(tmp_path, environ={"OPENAI_API_KEY": "opaque-secret"})
    calls: list[tuple[str, str]] = []

    def failing_live(binding, candidate):
        calls.append((binding.model_ref, candidate.ref))
        raise RuntimeError("provider echoed opaque-secret")

    report = run_doctor(
        manager=manager,
        live=True,
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        live_probe=failing_live,
        runtime_locator=lambda _command: None,
    )

    required = [probe for probe in report.probes if probe.required]
    assert calls == [("openai/gpt-5.6-sol", "openai:env-fallback")]
    assert required[0].reason_code == "probe_failed"
    assert required[0].message == "The live one-token check failed."
    assert "opaque-secret" not in report.to_json()


def test_subscription_only_openai_requires_configured_profile_and_viable_codex(
    tmp_path,
):
    profile = AuthProfile(
        "openai:chatgpt",
        ProfileKind.CODEX_READTHROUGH,
        AuthLane.SUBSCRIPTION,
    )
    config = _config(
        tmp_path,
        "[presets.default]\n"
        'model = "openai/gpt-5.6-sol"\n'
        'thinking = "high"\n'
        'auth_profile = "openai:chatgpt"\n',
    )
    calls: list[bool] = []

    report = run_doctor(
        config_path=str(config),
        manager=_manager(tmp_path, profile),
        openai_probe=lambda: calls.append(True) or "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        runtime_locator=lambda command: "/fake/codex" if command == "codex" else None,
    )

    required = [probe for probe in report.probes if probe.required]
    assert calls == [True]
    assert len(required) == 1
    assert required[0].lane == "subscription"
    assert required[0].reason_code == "runtime_unavailable"
    assert required[0].status is ProbeStatus.ERROR
    assert report.ok is False


def test_anthropic_policy_disabled_precedes_token_and_runtime_inspection(tmp_path):
    secret = "must-never-be-read-or-rendered"
    config = _config(
        tmp_path,
        "[auth.anthropic]\n"
        "policy_guard = false\n\n"
        "[presets.default]\n"
        'model = "anthropic/claude-opus-4-8"\n'
        'thinking = "high"\n'
        'auth_profile = "anthropic:claude"\n',
    )

    def forbidden_probe():
        raise AssertionError("policy-disabled doctor touched the Claude runtime")

    report = run_doctor(
        config_path=str(config),
        manager=_manager(
            tmp_path,
            AuthProfile(
                "anthropic:claude",
                ProfileKind.CLAUDE_RUNTIME,
                AuthLane.SUBSCRIPTION,
            ),
            environ={"CLAUDE_CODE_OAUTH_TOKEN": secret},
            policy_guard=False,
        ),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=forbidden_probe,
        runtime_locator=lambda _command: None,
    )

    required = [probe for probe in report.probes if probe.required]
    assert len(required) == 1
    assert required[0].reason_code == "policy_disabled"
    assert required[0].message == (
        "The Anthropic subscription lane is disabled by policy_guard."
    )
    assert secret not in report.to_json()


def test_named_claude_setup_token_is_probed_as_the_selected_candidate(tmp_path):
    token = "stored-user-minted-setup-token"
    profile = AuthProfile(
        "anthropic:headless",
        ProfileKind.CLAUDE_OAUTH_TOKEN,
        AuthLane.SUBSCRIPTION,
        token,
    )
    config = _config(
        tmp_path,
        "[presets.default]\n"
        'model = "anthropic/claude-opus-4-8"\n'
        'thinking = "high"\n'
        'auth_profile = "anthropic:headless"\n',
    )
    seen: list[object] = []

    def candidate_probe(candidate):
        seen.append(candidate)
        assert candidate("CLAUDE_CODE_OAUTH_TOKEN") == token
        return "ok"

    report = run_doctor(
        config_path=str(config),
        manager=_manager(tmp_path, profile),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=candidate_probe,
        runtime_locator=lambda _command: None,
    )

    required = [probe for probe in report.probes if probe.required]
    assert report.ok is True
    assert required[0].reason_code == "ok"
    assert required[0].auth_profile == profile.ref
    assert len(seen) == 1
    assert token not in report.to_json()


def test_doctor_reports_unsupported_thinking_level_instead_of_raising(tmp_path):
    config = _config(
        tmp_path,
        "[presets.default]\n"
        'model = "openai/gpt-5.2"\n'
        'thinking = "max"\n',
    )
    report = run_doctor(
        config_path=str(config),
        manager=_manager(tmp_path, environ={"OPENAI_API_KEY": "opaque"}),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        runtime_locator=lambda _command: None,
    )

    required = [probe for probe in report.probes if probe.required]
    assert len(required) == 1
    assert required[0].reason_code == "unsupported_thinking_level"
    assert required[0].message == (
        "The selected thinking level is unsupported by this model."
    )
    assert report.ok is False


def test_expired_profile_has_reason_code_without_exposing_secret(tmp_path):
    expired = AuthProfile(
        "openai:expired",
        ProfileKind.API_KEY,
        AuthLane.API_KEY,
        "expired-opaque-secret",
        datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    config = _config(
        tmp_path,
        "[presets.default]\n"
        'model = "openai/gpt-5.2"\n'
        'auth_profile = "openai:expired"\n',
    )
    report = run_doctor(
        config_path=str(config),
        manager=_manager(tmp_path, expired),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        runtime_locator=lambda _command: None,
    )

    required = [probe for probe in report.probes if probe.required]
    assert required[0].reason_code == "expired"
    assert "expired-opaque-secret" not in report.to_json()


def test_malformed_auth_profile_config_is_never_echoed(tmp_path):
    mistaken_secret = "sk-proj-opaque-secret-in-profile-field"
    config = _config(
        tmp_path,
        "[presets.default]\n"
        'model = "openai/gpt-5.2"\n'
        f'auth_profile = "{mistaken_secret}"\n',
    )

    report = run_doctor(
        config_path=str(config),
        manager=_manager(tmp_path),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        runtime_locator=lambda _command: None,
    )

    required = [probe for probe in report.probes if probe.required]
    assert required[0].auth_profile is None
    assert mistaken_secret not in report.to_json()


def test_invalid_preset_shape_is_reason_coded_instead_of_raising(tmp_path):
    config = _config(
        tmp_path,
        "[presets.default]\n"
        'thinking = "high"\n',
    )

    report = run_doctor(
        config_path=str(config),
        manager=_manager(tmp_path),
        openai_probe=lambda: "runtime_unavailable",
        anthropic_probe=lambda: "runtime_unavailable",
        runtime_locator=lambda _command: None,
    )

    assert report.ok is False
    assert len(report.probes) == 1
    assert report.probes[0].provider == "tinyic"
    assert report.probes[0].reason_code == "invalid_preset"
