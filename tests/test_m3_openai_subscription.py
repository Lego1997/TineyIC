"""Offline contract tests for the sanctioned OpenAI subscription lane (FR-2.2)."""

from __future__ import annotations

import base64
import io
import json
import os
import threading
import stat
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from tinyic.auth.openai import (
    CODEX_APP_SERVER_COMMAND,
    CodexLoginSession,
    CodexRPCError,
    LoginMode,
    OpenAIAuthReason,
    StdioCodexRPC,
    _codex_child_env,
    deliver_pasted_redirect,
    probe_codex_auth,
)
from tinyic.models.adapters.codex_runtime import CodexRuntimeTransport
from tinyic.models.binding import ModelBinding
from tinyic.models.thinking import ThinkingLevel
from tinyic.models.types import (
    AuthError,
    ChatMessage,
    ChatRequest,
    FinalMessage,
    InvalidRequestError,
    ReasoningDelta,
    Role,
    TextDelta,
    TransientError,
    Usage,
    UsageWindow,
)


def _jwt(*, exp: int) -> str:
    def enc(value: Mapping[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{enc({'alg': 'none'})}.{enc({'exp': exp})}.signature"


def _write_codex_auth(codex_home: Path, *, token: str) -> Path:
    codex_home.mkdir(parents=True, exist_ok=True)
    path = codex_home / "auth.json"
    path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": token,
                    "refresh_token": "refresh-super-secret",
                    "account_id": "acct-secret",
                }
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


class FakeRPC:
    """Scripted app-server RPC with a record of every exact request."""

    def __init__(
        self,
        responses: Mapping[str, list[Mapping[str, Any]] | Mapping[str, Any]],
        notifications: list[Mapping[str, Any]] | None = None,
    ) -> None:
        self.responses = {
            key: list(value) if isinstance(value, list) else [value]
            for key, value in responses.items()
        }
        self._notifications = list(notifications or [])
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.notifies: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        self.requests.append((method, dict(params)))
        scripted = self.responses.get(method)
        if not scripted:
            raise AssertionError(f"unexpected app-server request: {method}")
        return scripted.pop(0)

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self.notifies.append((method, dict(params or {})))

    def notifications(self) -> Iterator[Mapping[str, Any]]:
        yield from self._notifications

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeRPC":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _initialized_requests(rpc: FakeRPC) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "initialize",
            {
                "clientInfo": {
                    "name": "tinyic",
                    "title": "TinyIC",
                    "version": "2",
                },
                # Empty environments/workspace roots are experimental fields
                # in app-server and are what remove shell/file capabilities.
                "capabilities": {"experimentalApi": True},
            },
        )
    ]


def test_production_runtime_command_is_official_app_server_stdio_only():
    assert CODEX_APP_SERVER_COMMAND[:2] == ("codex", "app-server")
    assert CODEX_APP_SERVER_COMMAND[-2:] == ("--listen", "stdio://")
    disabled = {
        CODEX_APP_SERVER_COMMAND[index + 1]
        for index, value in enumerate(CODEX_APP_SERVER_COMMAND[:-1])
        if value == "--disable"
    }
    assert {
        "shell_tool",
        "unified_exec",
        "code_mode",
        "standalone_web_search",
        "browser_use",
        "computer_use",
        "apps",
        "plugins",
        "multi_agent",
        "multi_agent_v2",
        "image_generation",
        "hooks",
    } <= disabled
    assert "shell_environment_policy.inherit=none" in CODEX_APP_SERVER_COMMAND
    joined = " ".join(CODEX_APP_SERVER_COMMAND)
    assert "chatgpt.com" not in joined
    assert "api.openai.com" not in joined
    assert "backend-api" not in joined


def test_codex_child_process_receives_only_scrubbed_provider_environment():
    sentinel = "opaque-provider-secret"
    source = {
        "HOME": "/safe/home",
        "CODEX_HOME": "/safe/codex",
        "PATH": "/safe/bin",
        "TMPDIR": "/safe/tmp",
        "LANG": "en_US.UTF-8",
        "HTTPS_PROXY": "http://proxy.local:8080",
        "OPENAI_API_KEY": sentinel,
        "OPENAI_BASE_URL": f"https://{sentinel}.example",
        "ANTHROPIC_API_KEY": sentinel,
        "CLAUDE_CODE_OAUTH_TOKEN": sentinel,
        "XAI_API_KEY": sentinel,
        "MOONSHOT_API_KEY": sentinel,
        "KIMI_API_KEY": sentinel,
        "GEMINI_API_KEY": sentinel,
        "GOOGLE_APPLICATION_CREDENTIALS": f"/{sentinel}.json",
        "AWS_SECRET_ACCESS_KEY": sentinel,
        "AZURE_OPENAI_API_KEY": sentinel,
    }

    child = _codex_child_env(source)

    assert child == {
        "HOME": "/safe/home",
        "CODEX_HOME": "/safe/codex",
        "PATH": "/safe/bin",
        "TMPDIR": "/safe/tmp",
        "LANG": "en_US.UTF-8",
        "HTTPS_PROXY": "http://proxy.local:8080",
    }
    assert sentinel not in repr(child)


def test_stdio_runtime_uses_shell_false_and_the_scrubbed_environment(monkeypatch):
    captured: dict[str, object] = {}

    class Process:
        stdin = io.StringIO()
        stdout = io.StringIO()

        def poll(self):
            return 0

        def kill(self):
            return None

    def process_factory(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return Process()

    monkeypatch.setenv("OPENAI_API_KEY", "opaque-provider-secret")
    rpc = StdioCodexRPC(process_factory=process_factory)
    rpc.close()

    assert captured["command"] == CODEX_APP_SERVER_COMMAND
    assert captured["shell"] is False
    assert "OPENAI_API_KEY" not in captured["env"]


def test_stdio_runtime_times_out_and_terminates_a_hung_child():
    released = threading.Event()

    class BlockingStdout:
        def readline(self):
            released.wait(1)
            return ""

    class Process:
        stdin = io.StringIO()
        stdout = BlockingStdout()
        terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True
            released.set()

        def kill(self):
            self.terminated = True
            released.set()

        def wait(self, timeout=None):
            return 0

    process = Process()
    rpc = StdioCodexRPC(
        process_factory=lambda *_args, **_kwargs: process,
        request_timeout=0.01,
    )

    with pytest.raises(RuntimeError, match="timed out"):
        rpc.request("account/read", {"refreshToken": False})

    assert process.terminated is True


@pytest.mark.parametrize(
    ("store", "runtime_reason", "expected", "runtime_calls"),
    [
        ("file", OpenAIAuthReason.MISSING_CREDENTIAL, OpenAIAuthReason.OK, 0),
        ("keyring", OpenAIAuthReason.OK, OpenAIAuthReason.OK, 1),
        ("auto", OpenAIAuthReason.OK, OpenAIAuthReason.OK, 1),
        ("auto", OpenAIAuthReason.MISSING_CREDENTIAL, OpenAIAuthReason.OK, 1),
    ],
)
def test_file_keyring_auto_precedence_and_read_through_is_non_mutating(
    tmp_path: Path,
    store: str,
    runtime_reason: OpenAIAuthReason,
    expected: OpenAIAuthReason,
    runtime_calls: int,
):
    codex_home = tmp_path / "codex-home"
    auth_path = _write_codex_auth(codex_home, token=_jwt(exp=2_000_000_000))
    (codex_home / "config.toml").write_text(
        f'cli_auth_credentials_store = "{store}"\n', encoding="utf-8"
    )
    before = (
        auth_path.read_bytes(),
        auth_path.stat().st_mtime_ns,
        stat.S_IMODE(auth_path.stat().st_mode),
    )
    calls = 0

    def runtime_probe():
        nonlocal calls
        calls += 1
        return runtime_reason

    result = probe_codex_auth(
        env={"CODEX_HOME": str(codex_home)},
        runtime_probe=runtime_probe,
        now=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )

    assert result.reason is expected
    assert result.credential_store == store
    assert calls == runtime_calls
    assert before == (
        auth_path.read_bytes(),
        auth_path.stat().st_mtime_ns,
        stat.S_IMODE(auth_path.stat().st_mode),
    )


def test_codex_home_env_wins_and_file_expiry_and_invalid_json_are_reason_coded(
    tmp_path: Path,
):
    home = tmp_path / "home"
    env_codex = tmp_path / "env-codex"
    _write_codex_auth(home / ".codex", token=_jwt(exp=2_000_000_000))
    _write_codex_auth(env_codex, token=_jwt(exp=1))

    expired = probe_codex_auth(
        home=home,
        env={"CODEX_HOME": str(env_codex)},
        now=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    assert expired.reason is OpenAIAuthReason.EXPIRED
    assert expired.source == "codex_file"

    (env_codex / "auth.json").write_text("{broken", encoding="utf-8")
    invalid = probe_codex_auth(
        home=home,
        env={"CODEX_HOME": str(env_codex)},
    )
    assert invalid.reason is OpenAIAuthReason.INVALID_CREDENTIAL
    assert "broken" not in (invalid.message or "")


def test_keyring_probe_delegates_to_account_read_without_refreshing():
    rpc = FakeRPC(
        {
            "initialize": {"userAgent": "codex"},
            "account/read": {
                "account": {"type": "chatgpt", "email": None, "planType": "plus"},
                "requiresOpenaiAuth": True,
            },
        }
    )
    result = probe_codex_auth(
        credential_store="keyring",
        rpc_factory=lambda: rpc,
    )

    assert result.reason is OpenAIAuthReason.OK
    assert rpc.requests == _initialized_requests(rpc) + [
        ("account/read", {"refreshToken": False})
    ]
    assert rpc.notifies == [("initialized", {})]
    assert rpc.closed


def test_runtime_unavailable_and_wrong_account_type_are_reason_coded():
    unavailable = probe_codex_auth(
        credential_store="keyring",
        rpc_factory=lambda: (_ for _ in ()).throw(FileNotFoundError("codex")),
    )
    assert unavailable.reason is OpenAIAuthReason.RUNTIME_UNAVAILABLE

    rpc = FakeRPC(
        {
            "initialize": {},
            "account/read": {
                "account": {"type": "apiKey"},
                "requiresOpenaiAuth": True,
            },
        }
    )
    wrong_lane = probe_codex_auth(
        credential_store="keyring", rpc_factory=lambda: rpc
    )
    assert wrong_lane.reason is OpenAIAuthReason.INVALID_CREDENTIAL


def test_browser_and_device_login_are_delegated_to_official_runtime():
    browser_rpc = FakeRPC(
        {
            "initialize": {},
            "account/login/start": {
                "type": "chatgpt",
                "loginId": "login-browser",
                "authUrl": "https://auth.example/authorize?state=state-123",
            },
        },
        [
            {
                "method": "account/login/completed",
                "params": {"loginId": "login-browser", "success": True},
            }
        ],
    )
    with CodexLoginSession(rpc_factory=lambda: browser_rpc) as session:
        challenge = session.start(LoginMode.BROWSER)
        completed = session.wait(challenge)
    assert challenge.login_id == "login-browser"
    assert completed.reason is OpenAIAuthReason.OK
    assert "state-123" not in repr(challenge)
    assert browser_rpc.requests == _initialized_requests(browser_rpc) + [
        (
            "account/login/start",
            {"type": "chatgpt"},
        )
    ]
    assert browser_rpc.requests[0][1]["clientInfo"]["name"] == "tinyic"

    device_rpc = FakeRPC(
        {
            "initialize": {},
            "account/login/start": {
                "type": "chatgptDeviceCode",
                "loginId": "login-device",
                "verificationUrl": "https://auth.example/device",
                "userCode": "ABCD-EFGH",
            },
        }
    )
    with CodexLoginSession(rpc_factory=lambda: device_rpc) as session:
        device = session.start(LoginMode.DEVICE_CODE)
    assert device.user_code == "ABCD-EFGH"
    assert "ABCD-EFGH" not in repr(device)
    assert device_rpc.requests[-1] == (
        "account/login/start",
        {"type": "chatgptDeviceCode"},
    )


def test_paste_redirect_requires_loopback_and_matching_state_before_delivery():
    challenge_url = "https://auth.example/authorize?state=expected-state"
    delivered: list[str] = []
    valid = "http://127.0.0.1:1455/auth/callback?code=secret-code&state=expected-state"

    deliver_pasted_redirect(challenge_url, valid, deliver=delivered.append)
    assert delivered == [valid]

    for invalid in (
        "https://127.0.0.1:1455/auth/callback?code=x&state=expected-state",
        "http://evil.example/auth/callback?code=x&state=expected-state",
        "http://localhost:1455/auth/callback?code=x&state=wrong-state",
        "http://localhost:1455/auth/callback?state=expected-state",
    ):
        with pytest.raises(ValueError) as exc:
            deliver_pasted_redirect(challenge_url, invalid, deliver=delivered.append)
        assert "secret-code" not in str(exc.value)
    assert delivered == [valid]


def _transport_rpc(*, terminal_status: str = "completed") -> FakeRPC:
    notifications: list[Mapping[str, Any]] = [
        {
            "method": "item/reasoning/summaryTextDelta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "reason-1",
                "summaryIndex": 0,
                "delta": "Check valuation. ",
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "message-1",
                "delta": "Buy only with margin ",
            },
        },
        {
            "method": "item/reasoning/textDelta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "reason-1",
                "contentIndex": 0,
                "delta": "of safety.",
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "message-1",
                "delta": "of safety.",
            },
        },
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "tokenUsage": {
                    "last": {
                        "inputTokens": 90,
                        "cachedInputTokens": 10,
                        "outputTokens": 12,
                        "reasoningOutputTokens": 4,
                        "totalTokens": 102,
                    },
                    "total": {
                        "inputTokens": 90,
                        "cachedInputTokens": 10,
                        "outputTokens": 12,
                        "reasoningOutputTokens": 4,
                        "totalTokens": 102,
                    },
                },
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {
                    "id": "turn-1",
                    "status": terminal_status,
                    "items": [],
                },
            },
        },
    ]
    return FakeRPC(
        {
            "initialize": {},
            "account/read": {
                "account": {"type": "chatgpt", "planType": "plus"},
                "requiresOpenaiAuth": True,
            },
            "thread/start": {
                "thread": {"id": "thread-1"},
                "model": "gpt-5.6-sol",
                "modelProvider": "openai",
                "cwd": "/empty",
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "sandbox": {"type": "readOnly"},
            },
            "turn/start": {
                "turn": {"id": "turn-1", "status": "inProgress", "items": []}
            },
        },
        notifications,
    )


def test_codex_runtime_uses_ephemeral_safe_thread_and_normalizes_stream(tmp_path: Path):
    rpc = _transport_rpc()
    binding = ModelBinding(
        "openai/gpt-5.6-sol",
        auth_profile="openai:chatgpt",
        thinking_level=ThinkingLevel.HIGH,
    )
    transport = CodexRuntimeTransport(
        binding,
        credentials=None,
        rpc_factory=lambda: rpc,
        cwd_factory=lambda: tmp_path,
        window_estimate_msgs=100,
    )
    request = ChatRequest(
        [
            ChatMessage(Role.SYSTEM, "Return a structured investment action."),
            ChatMessage(Role.USER, "Analyze AAPL."),
        ],
        binding,
        stream=True,
    )

    events = list(transport.generate(request))

    assert events[:4] == [
        ReasoningDelta("Check valuation. "),
        TextDelta("Buy only with margin "),
        ReasoningDelta("of safety."),
        TextDelta("of safety."),
    ]
    usage = next(event for event in events if isinstance(event, Usage))
    assert usage.input_tokens == 90
    assert usage.cached_tokens == 10
    assert usage.output_tokens == 12
    assert usage.auth_profile == "openai:chatgpt"
    assert usage.lane == "subscription"
    window = next(event for event in events if isinstance(event, UsageWindow))
    assert window.window_used_msgs == 1
    assert window.window_estimate_msgs == 100
    assert events[-1] == FinalMessage(
        "Buy only with margin of safety.",
        reasoning="Check valuation. of safety.",
        usage=usage,
    )

    assert rpc.requests[0] == _initialized_requests(rpc)[0]
    assert rpc.requests[1] == ("account/read", {"refreshToken": False})
    method, thread = rpc.requests[2]
    assert method == "thread/start"
    assert thread["model"] == "gpt-5.6-sol"
    assert thread["cwd"] == str(tmp_path.resolve())
    assert thread["ephemeral"] is True
    assert thread["approvalPolicy"] == "never"
    assert thread["sandbox"] == "read-only"
    assert thread["dynamicTools"] == []
    assert thread["environments"] == []
    assert thread["runtimeWorkspaceRoots"] == []
    assert thread["selectedCapabilityRoots"] == []
    assert thread["config"]["mcp_servers"] == {}
    assert thread["config"]["web_search"] == "disabled"
    turn_method, turn = rpc.requests[3]
    assert turn_method == "turn/start"
    assert turn["threadId"] == "thread-1"
    # PRD FR-1.3 says the subscription lane maps the dial to the selected
    # model tier (here ``gpt-5.6-sol``), not a numeric/effort request knob.
    assert "effort" not in turn
    assert turn["approvalPolicy"] == "never"
    assert turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert turn["environments"] == []
    assert turn["runtimeWorkspaceRoots"] == []
    assert turn["input"] == [{"type": "text", "text": "Analyze AAPL."}]
    assert "Return a structured investment action." in thread["developerInstructions"]
    wire = json.dumps(rpc.requests)
    assert "api.openai.com" not in wire
    assert "chatgpt.com" not in wire
    assert "backend-api" not in wire


def test_codex_runtime_rejects_a_request_model_that_differs_from_its_binding(
    tmp_path: Path,
):
    calls: list[str] = []
    bound = ModelBinding(
        "openai/gpt-5.6-sol", auth_profile="openai:chatgpt"
    )
    request_binding = ModelBinding(
        "openai/gpt-5.2", auth_profile="openai:chatgpt"
    )
    transport = CodexRuntimeTransport(
        bound,
        None,
        rpc_factory=lambda: calls.append("rpc") or pytest.fail(
            "mismatched binding must fail before runtime construction"
        ),
        cwd_factory=lambda: tmp_path,
    )

    with pytest.raises(InvalidRequestError):
        list(
            transport.generate(
                ChatRequest(
                    [ChatMessage(Role.USER, "Analyze AAPL")],
                    request_binding,
                    stream=True,
                )
            )
        )
    assert calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("model", "gpt-5.2"), ("modelProvider", "unexpected-provider")],
)
def test_codex_runtime_rejects_an_unexpected_returned_route(
    tmp_path: Path, field: str, value: str
):
    rpc = _transport_rpc()
    rpc.responses["thread/start"][0] = {
        "thread": {"id": "thread-1"},
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        field: value,
    }
    binding = ModelBinding(
        "openai/gpt-5.6-sol", auth_profile="openai:chatgpt"
    )
    transport = CodexRuntimeTransport(
        binding,
        None,
        rpc_factory=lambda: rpc,
        cwd_factory=lambda: tmp_path,
    )

    with pytest.raises(InvalidRequestError):
        list(
            transport.generate(
                ChatRequest(
                    [ChatMessage(Role.USER, "Analyze AAPL")], binding, stream=True
                )
            )
        )
    assert [method for method, _params in rpc.requests] == [
        "initialize",
        "account/read",
        "thread/start",
    ]


def test_codex_runtime_never_leaks_runtime_or_auth_secrets_in_errors(tmp_path: Path):
    secret = "sk-super-secret-value"
    rpc = FakeRPC(
        {
            "initialize": {},
            "account/read": {
                "account": {"type": "chatgpt", "planType": "plus"},
                "requiresOpenaiAuth": True,
            },
            "thread/start": {
                "thread": {"id": "thread-1"},
                "model": "gpt-5.6-sol",
                "modelProvider": "openai",
                "cwd": "/empty",
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "sandbox": {"type": "readOnly"},
            },
            "turn/start": {
                "turn": {"id": "turn-1", "status": "inProgress", "items": []}
            },
        },
        [
            {
                "method": "error",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "willRetry": False,
                    "error": {
                        "message": f"unauthorized bearer {secret}",
                        "additionalDetails": f"token={secret}",
                        "codexErrorInfo": "unauthorized",
                    },
                },
            }
        ],
    )
    binding = ModelBinding(
        "openai/gpt-5.6-sol", auth_profile="openai:chatgpt"
    )
    transport = CodexRuntimeTransport(
        binding,
        None,
        rpc_factory=lambda: rpc,
        cwd_factory=lambda: tmp_path,
    )

    with pytest.raises(AuthError) as exc:
        list(
            transport.generate(
                ChatRequest([ChatMessage(Role.USER, secret)], binding, stream=True)
            )
        )
    assert secret not in str(exc.value)
    assert secret not in repr(exc.value)


def test_codex_rpc_errors_discard_provider_controlled_string_codes():
    secret = "provider-controlled-secret-code"
    error = CodexRPCError("thread/start", secret)

    assert error.code is None
    assert secret not in str(error)
    assert secret not in repr(error)


def test_codex_runtime_discards_secret_bearing_runtime_causes(tmp_path: Path):
    secret = "nested-runtime-secret"
    binding = ModelBinding(
        "openai/gpt-5.6-sol", auth_profile="openai:chatgpt"
    )
    transport = CodexRuntimeTransport(
        binding,
        None,
        rpc_factory=lambda: (_ for _ in ()).throw(RuntimeError(secret)),
        cwd_factory=lambda: tmp_path,
    )

    with pytest.raises(TransientError) as caught:
        list(
            transport.generate(
                ChatRequest(
                    [ChatMessage(Role.USER, "Analyze AAPL")], binding, stream=True
                )
            )
        )

    assert caught.value.__cause__ is None
    assert secret not in str(caught.value)


def test_codex_runtime_refuses_api_key_account_before_starting_a_thread(
    tmp_path: Path,
):
    rpc = FakeRPC(
        {
            "initialize": {},
            "account/read": {
                "account": {"type": "apiKey"},
                "requiresOpenaiAuth": True,
            },
        }
    )
    binding = ModelBinding(
        "openai/gpt-5.6-sol", auth_profile="openai:chatgpt"
    )
    transport = CodexRuntimeTransport(
        binding,
        None,
        rpc_factory=lambda: rpc,
        cwd_factory=lambda: tmp_path,
    )

    with pytest.raises(AuthError, match="ChatGPT subscription"):
        list(
            transport.generate(
                ChatRequest(
                    [ChatMessage(Role.USER, "Analyze AAPL")],
                    binding,
                    stream=True,
                )
            )
        )

    assert [method for method, _params in rpc.requests] == [
        "initialize",
        "account/read",
    ]


def test_login_failure_does_not_echo_runtime_secret():
    secret = "oauth-secret-code"
    rpc = FakeRPC(
        {
            "initialize": {},
            "account/login/start": {
                "type": "chatgpt",
                "loginId": "login-browser",
                "authUrl": "https://auth.example/authorize?state=state-123",
            },
        },
        [
            {
                "method": "account/login/completed",
                "params": {
                    "loginId": "login-browser",
                    "success": False,
                    "error": f"bad code {secret}",
                },
            }
        ],
    )
    with CodexLoginSession(rpc_factory=lambda: rpc) as session:
        challenge = session.start(LoginMode.BROWSER)
        result = session.wait(challenge)
    assert result.reason is OpenAIAuthReason.INVALID_CREDENTIAL
    assert secret not in (result.message or "")
