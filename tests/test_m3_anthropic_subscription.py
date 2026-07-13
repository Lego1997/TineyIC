"""Offline contracts for Anthropic subscription use through official plumbing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from claude_agent_sdk import RateLimitEvent, RateLimitInfo

from tinyic.models.adapters._claude_sdk_worker import _message as sdk_worker_message
from tinyic.models.adapters.claude_runtime import (
    ClaudePolicyError,
    ClaudeRuntimeReason,
    ClaudeRuntimeTransport,
    _claude_child_env,
    probe_claude_runtime,
)
from tinyic.models.binding import ModelBinding
from tinyic.models.credentials import StaticCredentialProvider
from tinyic.models.types import (
    ChatMessage,
    ChatRequest,
    FinalMessage,
    InvalidRequestError,
    ReasoningDelta,
    Role,
    TextDelta,
    TransientError,
    Usage,
    UsageLimitError,
    UsageWindow,
)


def _request(*, stream: bool = True, text: str = "Assess ACME") -> ChatRequest:
    binding = ModelBinding("anthropic/claude-opus-4-8")
    return ChatRequest(
        (
            ChatMessage(Role.SYSTEM, "Act as an investment committee member."),
            ChatMessage(Role.USER, text),
        ),
        binding,
        stream=stream,
    )


def test_policy_guard_stops_before_credentials_sdk_or_cli_are_touched():
    calls: list[str] = []

    def credential(_ref: str) -> str | None:
        calls.append("credential")
        return "must-not-be-read"

    transport = ClaudeRuntimeTransport(
        _request().binding,
        credential,
        policy_guard=False,
        sdk_available=lambda: calls.append("sdk") or True,
        cli_locator=lambda _name: calls.append("cli") or "/bin/claude",
    )

    with pytest.raises(ClaudePolicyError) as caught:
        list(transport.generate(_request()))

    assert caught.value.reason_code == "policy_disabled"
    assert calls == []
    assert "must-not-be-read" not in str(caught.value)


def test_transport_inherits_a_disabled_guard_from_an_auth_manager_like_provider():
    calls: list[str] = []

    class DisabledCredentials:
        anthropic_policy_guard = False

        def __call__(self, _ref: str) -> str | None:
            calls.append("credential")
            raise AssertionError("disabled manager credential was read")

    transport = ClaudeRuntimeTransport(
        _request().binding,
        DisabledCredentials(),
        sdk_available=lambda: calls.append("sdk") or True,
        cli_locator=lambda _name: calls.append("cli") or "/bin/claude",
    )

    with pytest.raises(ClaudePolicyError):
        list(transport.generate(_request()))
    assert calls == []


def test_sdk_worker_is_preferred_hardened_and_normalizes_stream(tmp_path: Path):
    sentinel = "oauth-token-that-must-stay-private"
    captured = []

    def sdk_runner(invocation):
        captured.append(invocation)
        return iter(
            [
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": "weigh "},
                    },
                },
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "BUY"},
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "weigh risks"},
                            {"type": "text", "text": "BUY"},
                        ],
                        "usage": {"input_tokens": 11, "output_tokens": 3},
                        "stop_reason": "end_turn",
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "BUY",
                    "usage": {"input_tokens": 11, "output_tokens": 3},
                },
            ]
        )

    transport = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({"CLAUDE_CODE_OAUTH_TOKEN": sentinel}),
        sdk_available=lambda: True,
        sdk_runner=sdk_runner,
        cli_locator=lambda _name: pytest.fail("CLI fallback must not be consulted"),
        cwd_factory=lambda: tmp_path,
        environ={
            "HOME": "/safe/home",
            "PATH": "/safe/bin",
            "LANG": "en_US.UTF-8",
            "OPENAI_API_KEY": "unrelated-secret",
            "AWS_SECRET_ACCESS_KEY": "unrelated-secret",
            "CLAUDE_CODE_OAUTH_TOKEN": "ambient-token-must-not-win",
        },
        window_estimate_msgs=45,
    )

    events = list(transport.generate(_request()))

    assert [type(event) for event in events] == [
        ReasoningDelta,
        TextDelta,
        Usage,
        UsageWindow,
        FinalMessage,
    ]
    assert events[0].text == "weigh "
    assert events[1].text == "BUY"
    assert events[2] == Usage(
        input_tokens=11,
        output_tokens=3,
        auth_profile="anthropic:claude",
        lane="subscription",
    )
    assert events[3].window_used_msgs == 1
    assert events[3].window_estimate_msgs == 45
    assert events[4].text == "BUY"
    assert events[4].reasoning == "weigh risks"
    assert events[4].usage == events[2]

    invocation = captured[0]
    assert invocation.backend == "sdk"
    assert invocation.cwd == tmp_path.resolve()
    assert invocation.env["CLAUDE_CODE_OAUTH_TOKEN"] == sentinel
    assert "OPENAI_API_KEY" not in invocation.env
    assert "AWS_SECRET_ACCESS_KEY" not in invocation.env
    assert invocation.options == {
        "tools": [],
        "allowed_tools": [],
        "disallowed_tools": [],
        "mcp_servers": {},
        "strict_mcp_config": True,
        "permission_mode": "dontAsk",
        "max_turns": 1,
        "setting_sources": [],
        "skills": [],
        "plugins": [],
        "include_partial_messages": True,
        "model": "claude-opus-4-8",
        "max_thinking_tokens": 4096,
    }
    assert sentinel not in repr(invocation)
    assert "Assess ACME" not in repr(invocation)


def test_secretless_profile_does_not_forward_an_ambient_oauth_token(tmp_path: Path):
    captured = []

    def sdk_runner(invocation):
        captured.append(invocation)
        return iter(
            [
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "HOLD",
                }
            ]
        )

    transport = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({}),
        sdk_available=lambda: True,
        sdk_runner=sdk_runner,
        cwd_factory=lambda: tmp_path,
        environ={
            "HOME": "/safe/home",
            "PATH": "/safe/bin",
            "CLAUDE_CODE_OAUTH_TOKEN": "ambient-not-selected",
        },
    )

    list(transport.generate(_request()))

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in captured[0].env


def test_cli_fallback_is_official_tool_free_and_keeps_prompt_out_of_argv(
    tmp_path: Path,
):
    sentinel = "prompt-private-value"
    captured = []

    def cli_runner(invocation):
        captured.append(invocation)
        return iter(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "SELL"}],
                        "usage": {"input_tokens": 5, "output_tokens": 1},
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "SELL",
                },
            ]
        )

    transport = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({}),
        sdk_available=lambda: False,
        cli_locator=lambda _name: "/official/bin/claude",
        cli_runner=cli_runner,
        cwd_factory=lambda: tmp_path,
    )

    events = list(transport.generate(_request(text=sentinel)))

    assert events[-1].text == "SELL"
    invocation = captured[0]
    command = invocation.command
    assert command[0] == "/official/bin/claude"
    assert command[1] == "-p"
    for required in (
        "--safe-mode",
        "--tools",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        "--no-chrome",
        "--no-session-persistence",
        "--permission-mode",
        "--output-format",
        "--include-partial-messages",
        "--model",
    ):
        assert required in command
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert "--bare" not in command
    joined = " ".join(command)
    assert sentinel not in joined
    assert "claude.ai" not in joined
    assert "oauth" not in joined.casefold()


def test_non_streaming_request_yields_only_usage_window_and_final(tmp_path: Path):
    transport = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({}),
        sdk_available=lambda: True,
        sdk_runner=lambda _invocation: iter(
            [
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "HOLD"},
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "HOLD",
                },
            ]
        ),
        cwd_factory=lambda: tmp_path,
    )

    events = list(transport.generate(_request(stream=False)))

    assert [type(event) for event in events] == [UsageWindow, FinalMessage]
    assert events[-1].text == "HOLD"


def test_usage_limit_and_tool_attempts_are_normalized_without_runtime_text(
    tmp_path: Path,
):
    limited = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({}),
        sdk_available=lambda: True,
        sdk_runner=lambda _invocation: iter(
            [
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "api_error_status": 429,
                    "errors": ["secret provider response"],
                }
            ]
        ),
        cwd_factory=lambda: tmp_path,
    )
    with pytest.raises(UsageLimitError) as caught:
        list(limited.generate(_request()))
    assert "secret provider response" not in str(caught.value)

    tool = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({}),
        sdk_available=lambda: True,
        sdk_runner=lambda _invocation: iter(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Bash", "input": {}}
                        ]
                    },
                }
            ]
        ),
        cwd_factory=lambda: tmp_path,
    )
    with pytest.raises(Exception, match="prohibited tool activity"):
        list(tool.generate(_request()))


def test_sdk_worker_preserves_rate_limit_and_server_tool_security_signals(
    tmp_path: Path,
):
    from tinyic.models.adapters import _claude_sdk_worker as worker

    class RateLimitInfo:
        status = "rejected"
        resets_at = 1_800_000_000
        rate_limit_type = "five_hour"

    class RateLimitEvent:
        rate_limit_info = RateLimitInfo()

    assert worker._message(RateLimitEvent()) == {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "rejected",
            "resets_at": 1_800_000_000,
            "rate_limit_type": "five_hour",
        },
    }

    class ServerToolUseBlock:
        name = "web_search"

    assert worker._block(ServerToolUseBlock()) == {
        "type": "server_tool_use",
        "name": "web_search",
    }

    class AssistantMessage:
        content = []
        usage = None
        stop_reason = None
        error = "rate_limit"

    assert worker._message(AssistantMessage())["error"] == "rate_limit"

    transport = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({}),
        sdk_available=lambda: True,
        sdk_runner=lambda _invocation: iter(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "server_tool_use",
                                "name": "web_search",
                            }
                        ]
                    },
                }
            ]
        ),
        cwd_factory=lambda: tmp_path,
    )

    with pytest.raises(InvalidRequestError, match="prohibited tool activity"):
        list(transport.generate(_request()))

    stream_transport = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({}),
        sdk_available=lambda: True,
        sdk_runner=lambda _invocation: iter(
            [
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_start",
                        "content_block": {"type": "future_unknown_block"},
                    },
                },
                {"type": "result", "result": "must not be accepted"},
            ]
        ),
        cwd_factory=lambda: tmp_path,
    )
    with pytest.raises(InvalidRequestError, match="prohibited tool activity"):
        list(stream_transport.generate(_request()))


def test_unknown_sdk_content_blocks_fail_closed_as_possible_tool_activity(
    tmp_path: Path,
):
    from tinyic.models.adapters import _claude_sdk_worker as worker

    class FutureToolInvocationBlock:
        name = "new_tool_shape"

    encoded = worker._block(FutureToolInvocationBlock())
    assert encoded == {"type": "unknown"}
    transport = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({}),
        sdk_available=lambda: True,
        sdk_runner=lambda _invocation: iter(
            [
                {
                    "type": "assistant",
                    "message": {"content": [encoded]},
                },
                {"type": "result", "result": "must not be accepted"},
            ]
        ),
        cwd_factory=lambda: tmp_path,
    )

    with pytest.raises(InvalidRequestError, match="prohibited tool activity"):
        list(transport.generate(_request()))


def test_sdk_assistant_error_is_normalized_before_provider_text_can_escape(
    tmp_path: Path,
):
    secret = "provider-error-secret"
    transport = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({}),
        sdk_available=lambda: True,
        sdk_runner=lambda _invocation: iter(
            [
                {
                    "type": "assistant",
                    "error": "rate_limit",
                    "message": {
                        "content": [{"type": "text", "text": secret}],
                    },
                }
            ]
        ),
        cwd_factory=lambda: tmp_path,
    )

    with pytest.raises(UsageLimitError) as caught:
        list(transport.generate(_request()))
    assert secret not in str(caught.value)


def test_sdk_worker_preserves_only_safe_rate_limit_fields():
    encoded = sdk_worker_message(
        RateLimitEvent(
            rate_limit_info=RateLimitInfo(
                status="rejected",
                resets_at=1_800_000_000,
                rate_limit_type="five_hour",
                raw={"provider_message": "must-not-cross-worker"},
            ),
            uuid="runtime-id",
            session_id="private-session-id",
        )
    )

    assert encoded == {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "rejected",
            "resets_at": 1_800_000_000,
            "rate_limit_type": "five_hour",
        },
    }
    assert "must-not-cross-worker" not in repr(encoded)
    assert "private-session-id" not in repr(encoded)


def test_runner_timeout_is_bounded_and_secret_safe(tmp_path: Path):
    sentinel = "timeout-secret"

    def timeout(_invocation):
        raise TimeoutError(sentinel)

    transport = ClaudeRuntimeTransport(
        _request().binding,
        StaticCredentialProvider({}),
        sdk_available=lambda: True,
        sdk_runner=timeout,
        cwd_factory=lambda: tmp_path,
        timeout=0.01,
    )

    with pytest.raises(TransientError) as caught:
        list(transport.generate(_request()))
    assert sentinel not in str(caught.value)


def test_child_env_is_an_allowlist_and_token_is_added_only_explicitly():
    source = {
        "HOME": "/safe/home",
        "PATH": "/safe/bin",
        "TMPDIR": "/safe/tmp",
        "HTTPS_PROXY": "http://proxy.local",
        "OPENAI_API_KEY": "provider-secret",
        "ANTHROPIC_API_KEY": "platform-secret",
        "CLAUDE_CODE_OAUTH_TOKEN": "ambient-token",
        "AWS_SECRET_ACCESS_KEY": "cloud-secret",
    }

    assert _claude_child_env(source) == {
        "HOME": "/safe/home",
        "PATH": "/safe/bin",
        "TMPDIR": "/safe/tmp",
        "HTTPS_PROXY": "http://proxy.local",
        "CLAUDE_AGENT_SDK_CLIENT_APP": "tinyic/2",
    }
    with_token = _claude_child_env(source, oauth_token="selected-token")
    assert with_token["CLAUDE_CODE_OAUTH_TOKEN"] == "selected-token"
    assert "ANTHROPIC_API_KEY" not in with_token


def test_probe_is_reason_coded_non_completing_and_policy_first():
    calls: list[str] = []
    disabled = probe_claude_runtime(
        policy_guard=False,
        credentials=lambda _ref: calls.append("credential") or None,
        sdk_available=lambda: calls.append("sdk") or True,
        cli_locator=lambda _name: calls.append("cli") or "/bin/claude",
    )
    assert disabled is ClaudeRuntimeReason.POLICY_DISABLED
    assert calls == []

    unavailable = probe_claude_runtime(
        environ={},
        sdk_available=lambda: False,
        cli_locator=lambda _name: None,
    )
    assert unavailable is ClaudeRuntimeReason.RUNTIME_UNAVAILABLE

    token_ready = probe_claude_runtime(
        environ={"CLAUDE_CODE_OAUTH_TOKEN": "user-minted-token"},
        sdk_available=lambda: True,
        cli_locator=lambda _name: None,
        status_runner=lambda *_args, **_kwargs: pytest.fail(
            "token readiness must not perform a completion or auth command"
        ),
    )
    assert token_ready is ClaudeRuntimeReason.OK


def test_probe_inherits_a_disabled_guard_from_credentials():
    calls: list[str] = []

    class DisabledCredentials:
        anthropic_policy_guard = False

        def __call__(self, _ref: str) -> str | None:
            calls.append("credential")
            raise AssertionError("policy-disabled probe read a credential")

    result = probe_claude_runtime(
        credentials=DisabledCredentials(),
        sdk_available=lambda: calls.append("sdk") or True,
        cli_locator=lambda _name: calls.append("cli") or "/bin/claude",
    )

    assert result is ClaudeRuntimeReason.POLICY_DISABLED
    assert calls == []


def test_probe_uses_official_auth_status_and_discards_identity_output():
    captured = {}

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "email": "private@example.com",
            }
        )

    def status_runner(command, **kwargs):
        captured["command"] = tuple(command)
        captured.update(kwargs)
        return Completed()

    result = probe_claude_runtime(
        environ={
            "HOME": "/safe/home",
            "PATH": "/safe/bin",
            "OPENAI_API_KEY": "unrelated-secret",
        },
        sdk_available=lambda: False,
        cli_locator=lambda _name: "/official/bin/claude",
        status_runner=status_runner,
    )

    assert result is ClaudeRuntimeReason.OK
    assert captured["command"] == (
        "/official/bin/claude",
        "auth",
        "status",
        "--json",
    )
    assert captured["shell"] is False
    assert "OPENAI_API_KEY" not in captured["env"]
