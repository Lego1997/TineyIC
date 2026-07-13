"""Anthropic subscription transport through official Claude plumbing only.

FR-2.3 permits TinyIC to reuse a user's own Claude Code login through the
official Claude Agent SDK, with ``claude -p`` as the fallback.  This module
does exactly those two things.  It contains no Claude.ai OAuth flow, browser
login, token exchange, private endpoint, or client-identity spoofing.

The SDK is preferred, but its Python implementation merges child environment
options over the SDK caller's ambient ``os.environ``.  TinyIC therefore invokes
the SDK in :mod:`_claude_sdk_worker`, a subprocess started with an auditable
allowlist.  The fallback CLI uses the same allowlist and a tool-free, ephemeral
configuration.  Prompt text travels over stdin, never argv.  All runtime errors
cross the adapter boundary as fixed, secret-free normalized exceptions.
"""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from tinyic.auth.usage_window import RollingUsageMeter

from ..binding import ModelBinding
from ..credentials import CredentialProvider
from ..thinking import ThinkingLevel
from ..types import (
    AuthError,
    ChatMessage,
    ChatRequest,
    ChatStreamEvent,
    FinalMessage,
    FinishReason,
    InvalidRequestError,
    ProviderError,
    ReasoningDelta,
    Role,
    TextDelta,
    TransientError,
    Usage,
    UsageLimitError,
)
from .anthropic_messages import ANTHROPIC_THINKING_BUDGETS, finish_from_anthropic


CLAUDE_CODE_OAUTH_TOKEN_REF = "CLAUDE_CODE_OAUTH_TOKEN"
SDK_WORKER_COMMAND = (
    sys.executable,
    str(Path(__file__).with_name("_claude_sdk_worker.py")),
)

_SYSTEM_PROMPT = (
    "You are a text-completion runtime embedded in TinyIC. Return only the "
    "requested answer. Never call tools, inspect files, browse, run commands, "
    "delegate, load skills, use MCP, or access the working directory."
)
_MCP_NONE = '{"mcpServers":{}}'
_SDK_OPTIONS = {
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
}

# Allowlist, not a provider-key blacklist: newly invented credentials remain
# excluded by default.  Claude's own token is added separately only after the
# selected AuthCandidate resolves it.
_CLAUDE_CHILD_ENV_NAMES = frozenset(
    {
        "HOME",
        "USER",
        "LOGNAME",
        "USERPROFILE",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "APPDATA",
        "LOCALAPPDATA",
    }
)


class ClaudeRuntimeReason(str, Enum):
    """Stable local readiness codes consumed by ``tinyic doctor``."""

    OK = "ok"
    MISSING_CREDENTIAL = "missing_credential"
    INVALID_CREDENTIAL = "invalid_credential"
    POLICY_DISABLED = "policy_disabled"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"


class ClaudePolicyError(AuthError):
    """The legal/policy kill switch disabled subscription use."""

    reason_code = "policy_disabled"

    def __init__(self) -> None:
        super().__init__(
            "The Anthropic subscription lane is disabled by policy_guard.",
            provider="anthropic",
        )


def _claude_child_env(
    source: Mapping[str, str] | None = None,
    *,
    oauth_token: str | None = None,
) -> dict[str, str]:
    """Build the only environment permitted into either official runtime."""

    environment = os.environ if source is None else source
    child = {
        name: value
        for name, value in environment.items()
        if name in _CLAUDE_CHILD_ENV_NAMES and isinstance(value, str)
    }
    # This is the documented Agent SDK application-identity mechanism.  It is
    # honest attribution, not a Claude Code client spoof.
    child["CLAUDE_AGENT_SDK_CLIENT_APP"] = "tinyic/2"
    if isinstance(oauth_token, str) and oauth_token.strip():
        child[CLAUDE_CODE_OAUTH_TOKEN_REF] = oauth_token.strip()
    return child


def _sdk_available() -> bool:
    try:
        return importlib.util.find_spec("claude_agent_sdk") is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _bundled_sdk_cli() -> str | None:
    """Locate the Agent SDK's official bundled CLI without importing the SDK."""

    try:
        spec = importlib.util.find_spec("claude_agent_sdk")
    except (ImportError, AttributeError, ValueError):
        return None
    locations = getattr(spec, "submodule_search_locations", None) if spec else None
    if not locations:
        return None
    name = "claude.exe" if os.name == "nt" else "claude"
    for location in locations:
        candidate = Path(location) / "_bundled" / name
        if candidate.is_file():
            return str(candidate)
    return None


def _prompt_text(messages: Sequence[ChatMessage]) -> str:
    """Encode normalized roles in stdin without placing request text in argv."""

    return "\n\n".join(
        f"<{message.role.value}>\n{message.content}\n</{message.role.value}>"
        for message in messages
    )


def _thinking_budget(binding: ModelBinding) -> int | None:
    # The catalog and HTTP adapter share this table.  Config validation already
    # rejects unsupported Anthropic levels; runtime remapping happens before a
    # binding reaches this transport.  OFF/unknown means omit the control.
    return ANTHROPIC_THINKING_BUDGETS.get(ThinkingLevel(binding.thinking_level))


@dataclass(frozen=True, repr=False)
class ClaudeInvocation:
    """Secret-redacting description passed to an injectable runtime runner."""

    backend: str
    command: tuple[str, ...]
    prompt: str
    system_prompt: str
    options: Mapping[str, Any]
    env: Mapping[str, str]
    cwd: Path
    timeout: float

    def __repr__(self) -> str:
        return (
            "ClaudeInvocation("
            f"backend={self.backend!r}, command={self.command!r}, "
            f"cwd={str(self.cwd)!r}, timeout={self.timeout!r}, "
            "prompt='<redacted>', env='<redacted>')"
        )


class RuntimeRunner(Protocol):
    def __call__(self, invocation: ClaudeInvocation) -> Iterable[Mapping[str, Any]]: ...


def _cli_command(path: str, binding: ModelBinding) -> tuple[str, ...]:
    command = [
        path,
        "-p",
        "--safe-mode",
        "--tools",
        "",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        _MCP_NONE,
        "--no-chrome",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--input-format",
        "text",
        "--verbose",
        "--max-turns",
        "1",
        "--model",
        binding.model,
        "--system-prompt",
        _SYSTEM_PROMPT,
    ]
    budget = _thinking_budget(binding)
    if budget is not None:
        command.extend(("--max-thinking-tokens", str(budget)))
    return tuple(command)


def _sdk_options(binding: ModelBinding) -> dict[str, Any]:
    options = dict(_SDK_OPTIONS)
    options["model"] = binding.model
    budget = _thinking_budget(binding)
    if budget is not None:
        options["max_thinking_tokens"] = budget
    return options


def _terminate_process(process: Any) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt" and getattr(process, "pid", None):
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=0.5)
    except Exception:
        try:
            if os.name != "nt" and getattr(process, "pid", None):
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except Exception:
            pass


def _run_jsonl_process(invocation: ClaudeInvocation) -> Iterator[Mapping[str, Any]]:
    """Stream JSONL from an official child with a whole-call deadline."""

    process = subprocess.Popen(
        invocation.command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        cwd=str(invocation.cwd),
        env=dict(invocation.env),
        shell=False,
        start_new_session=(os.name != "nt"),
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def read_stdout() -> None:
        try:
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()
    payload = (
        json.dumps(
            {
                "prompt": invocation.prompt,
                "system_prompt": invocation.system_prompt,
                "options": dict(invocation.options),
                "cwd": str(invocation.cwd),
            },
            separators=(",", ":"),
        )
        if invocation.backend == "sdk"
        else invocation.prompt
    )
    deadline = time.monotonic() + invocation.timeout
    saw_runtime_error = False
    try:
        process.stdin.write(payload)
        process.stdin.close()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Claude runtime deadline exceeded")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("Claude runtime deadline exceeded") from None
            if line is None:
                break
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, Mapping):
                if item.get("type") == "tinyic_runtime_error":
                    saw_runtime_error = True
                yield item
        remaining = max(0.0, deadline - time.monotonic())
        returncode = process.wait(timeout=remaining)
        if returncode != 0 and not saw_runtime_error:
            raise RuntimeError("Claude runtime exited unsuccessfully")
    finally:
        _terminate_process(process)
        reader.join(timeout=0.5)


@contextmanager
def _safe_cwd(cwd_factory: Callable[[], Path] | None) -> Iterator[Path]:
    if cwd_factory is not None:
        path = Path(cwd_factory()).expanduser().resolve()
        if not path.is_dir():
            raise InvalidRequestError(
                "Claude runtime cwd must be an existing directory",
                provider="anthropic",
            )
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="tinyic-claude-") as directory:
        yield Path(directory).resolve()


def _usage(value: object, *, auth_profile: str) -> Usage | None:
    if not isinstance(value, Mapping):
        return None

    def integer(*names: str) -> int:
        for name in names:
            raw = value.get(name)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return max(0, int(raw))
        return 0

    if not any(
        name in value
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "cached_tokens",
        )
    ):
        return None
    return Usage(
        input_tokens=integer("input_tokens"),
        output_tokens=integer("output_tokens"),
        cached_tokens=integer(
            "cached_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"
        ),
        auth_profile=auth_profile,
        lane="subscription",
    )


def _merge_usage(old: Usage | None, new: Usage | None) -> Usage | None:
    if new is None:
        return old
    if old is None:
        return new
    return Usage(
        input_tokens=max(old.input_tokens, new.input_tokens),
        output_tokens=max(old.output_tokens, new.output_tokens),
        cached_tokens=max(old.cached_tokens, new.cached_tokens),
        auth_profile=new.auth_profile,
        lane="subscription",
    )


def _raise_reason(
    reason: object,
    *,
    status: object = None,
    partial_output: bool = False,
) -> None:
    """Map only fixed labels/statuses; never reflect provider-controlled text."""

    if reason in {"usage_limited", "rate_limit", "billing_error"} or status == 429:
        raise UsageLimitError(
            "Claude subscription usage limit reached",
            partial_output=partial_output,
            provider="anthropic",
        )
    if reason in {
        "invalid_credential",
        "missing_credential",
        "authentication_failed",
    } or status in {401, 403}:
        raise AuthError(
            "Claude Code login is missing, invalid, or expired", provider="anthropic"
        )
    if reason == "runtime_unavailable":
        raise TransientError("Claude official runtime is unavailable", provider="anthropic")
    if reason == "invalid_request":
        raise InvalidRequestError(
            "Claude official runtime rejected the request", provider="anthropic"
        )
    raise TransientError("Claude subscription runtime failed", provider="anthropic")


def _content_blocks(value: object) -> tuple[str, str]:
    text: list[str] = []
    reasoning: list[str] = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return "", ""
    for block in value:
        if not isinstance(block, Mapping):
            raise InvalidRequestError(
                "Claude subscription binding attempted prohibited tool activity",
                provider="anthropic",
            )
        kind = block.get("type")
        if kind not in {"text", "thinking", "redacted_thinking"}:
            raise InvalidRequestError(
                "Claude subscription binding attempted prohibited tool activity",
                provider="anthropic",
            )
        if kind == "text" and isinstance(block.get("text"), str):
            text.append(block["text"])
        elif kind in {"thinking", "redacted_thinking"} and isinstance(
            block.get("thinking"), str
        ):
            reasoning.append(block["thinking"])
    return "".join(text), "".join(reasoning)


class ClaudeRuntimeTransport:
    """Normalized transport for Claude subscription-backed model calls."""

    def __init__(
        self,
        binding: ModelBinding,
        credentials: CredentialProvider | None,
        *,
        policy_guard: bool = True,
        sdk_available: Callable[[], bool] | None = None,
        cli_locator: Callable[[str], str | None] | None = None,
        sdk_runner: RuntimeRunner | None = None,
        cli_runner: RuntimeRunner | None = None,
        cwd_factory: Callable[[], Path] | None = None,
        environ: Mapping[str, str] | None = None,
        timeout: float = 300.0,
        usage_meter: RollingUsageMeter | None = None,
        window_estimate_msgs: int = 0,
    ) -> None:
        if binding.provider.casefold() != "anthropic":
            raise ValueError("Claude runtime requires an anthropic/* binding")
        if not isinstance(policy_guard, bool):
            raise TypeError("policy_guard must be a boolean")
        credential_guard = getattr(credentials, "anthropic_policy_guard", True)
        if not isinstance(credential_guard, bool):
            raise TypeError("credential policy guard must be a boolean")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if window_estimate_msgs < 0:
            raise ValueError("window_estimate_msgs cannot be negative")
        self.binding = binding
        self._credentials = credentials
        # A disabled manager guard cannot be re-enabled by constructing the
        # transport directly with its default arguments.
        self._policy_guard = policy_guard and credential_guard
        self._sdk_available = sdk_available or _sdk_available
        self._cli_locator = cli_locator or shutil.which
        self._sdk_runner = sdk_runner or _run_jsonl_process
        self._cli_runner = cli_runner or _run_jsonl_process
        self._cwd_factory = cwd_factory
        self._environ = os.environ if environ is None else environ
        self._timeout = timeout
        self._usage_meter = usage_meter or RollingUsageMeter()
        self._window_estimate_msgs = window_estimate_msgs
        self.auth_profile = (
            binding.auth_profile
            or getattr(credentials, "ref", None)
            or "anthropic:claude"
        )

    def generate(self, request: ChatRequest) -> Iterator[ChatStreamEvent]:
        # This ordering is a legal boundary: when disabled, do not read a token,
        # import/probe the SDK, locate Claude, or spawn anything.
        if not self._policy_guard:
            raise ClaudePolicyError()
        if request.binding.provider.casefold() != "anthropic":
            raise InvalidRequestError(
                "Claude runtime received a non-Anthropic binding",
                provider="anthropic",
            )
        if request.binding.model != self.binding.model:
            raise InvalidRequestError(
                "Claude runtime request model does not match its binding",
                provider="anthropic",
            )

        token = (
            self._credentials(CLAUDE_CODE_OAUTH_TOKEN_REF)
            if self._credentials is not None
            else None
        )
        child_env = _claude_child_env(self._environ, oauth_token=token)
        with _safe_cwd(self._cwd_factory) as cwd:
            if self._sdk_available():
                invocation = ClaudeInvocation(
                    backend="sdk",
                    command=SDK_WORKER_COMMAND,
                    prompt=_prompt_text(request.messages),
                    system_prompt=_SYSTEM_PROMPT,
                    options=_sdk_options(request.binding),
                    env=child_env,
                    cwd=cwd,
                    timeout=self._timeout,
                )
                runner = self._sdk_runner
            else:
                path = self._cli_locator("claude")
                if not path:
                    raise TransientError(
                        "Claude official runtime is unavailable", provider="anthropic"
                    )
                invocation = ClaudeInvocation(
                    backend="cli",
                    command=_cli_command(path, request.binding),
                    prompt=_prompt_text(request.messages),
                    system_prompt=_SYSTEM_PROMPT,
                    options={},
                    env=child_env,
                    cwd=cwd,
                    timeout=self._timeout,
                )
                runner = self._cli_runner
            try:
                yield from self._consume(runner(invocation), request.stream)
            except ProviderError:
                raise
            except TimeoutError:
                raise TransientError(
                    "Claude subscription runtime timed out", provider="anthropic"
                ) from None
            except (OSError, RuntimeError):
                raise TransientError(
                    "Claude subscription runtime became unavailable",
                    provider="anthropic",
                ) from None
            except Exception:
                raise TransientError(
                    "Claude subscription runtime failed", provider="anthropic"
                ) from None

    def _consume(
        self,
        records: Iterable[Mapping[str, Any]],
        streaming: bool,
    ) -> Iterator[ChatStreamEvent]:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        authoritative_text = ""
        authoritative_reasoning = ""
        usage: Usage | None = None
        finish = FinishReason.STOP
        terminal = False
        saw_provider_output = False

        for record in records:
            if not isinstance(record, Mapping):
                continue
            kind = record.get("type")
            if kind == "tinyic_runtime_error":
                _raise_reason(
                    record.get("reason"), partial_output=saw_provider_output
                )
            if kind == "rate_limit_event":
                info = record.get("rate_limit_info")
                if isinstance(info, Mapping) and info.get("status") == "rejected":
                    _raise_reason(
                        "usage_limited", partial_output=saw_provider_output
                    )
                continue
            if kind == "stream_event":
                event = record.get("event")
                if not isinstance(event, Mapping):
                    continue
                event_type = event.get("type")
                if event_type == "error":
                    error = event.get("error")
                    error_type = error.get("type") if isinstance(error, Mapping) else None
                    _raise_reason(
                        error_type, partial_output=saw_provider_output
                    )
                saw_provider_output = True
                if event_type == "message_start":
                    message = event.get("message")
                    if isinstance(message, Mapping):
                        usage = _merge_usage(
                            usage,
                            _usage(message.get("usage"), auth_profile=self.auth_profile),
                        )
                elif event_type == "message_delta":
                    delta = event.get("delta")
                    if isinstance(delta, Mapping):
                        finish = finish_from_anthropic(delta.get("stop_reason"))
                    usage = _merge_usage(
                        usage,
                        _usage(event.get("usage"), auth_profile=self.auth_profile),
                    )
                elif event_type == "content_block_start":
                    block = event.get("content_block")
                    block_type = block.get("type") if isinstance(block, Mapping) else None
                    if block_type not in {
                        "text",
                        "thinking",
                        "redacted_thinking",
                    }:
                        raise InvalidRequestError(
                            "Claude subscription binding attempted prohibited tool activity",
                            provider="anthropic",
                        )
                elif event_type == "content_block_delta":
                    delta = event.get("delta")
                    if not isinstance(delta, Mapping):
                        continue
                    delta_type = delta.get("type")
                    if isinstance(delta_type, str) and (
                        "tool" in delta_type or delta_type == "input_json_delta"
                    ):
                        raise InvalidRequestError(
                            "Claude subscription binding attempted prohibited tool activity",
                            provider="anthropic",
                        )
                    if delta_type == "text_delta" and isinstance(delta.get("text"), str):
                        value = delta["text"]
                        if value:
                            text_parts.append(value)
                            if streaming:
                                yield TextDelta(value)
                    elif delta_type in {"thinking_delta", "signature_delta"} and isinstance(
                        delta.get("thinking"), str
                    ):
                        value = delta["thinking"]
                        if value:
                            reasoning_parts.append(value)
                            if streaming:
                                yield ReasoningDelta(value)
                continue
            if kind == "assistant":
                if record.get("error") is not None:
                    _raise_reason(
                        record.get("error"), partial_output=saw_provider_output
                    )
                message = record.get("message")
                if not isinstance(message, Mapping):
                    continue
                saw_provider_output = True
                block_text, block_reasoning = _content_blocks(message.get("content"))
                authoritative_text = block_text or authoritative_text
                authoritative_reasoning = block_reasoning or authoritative_reasoning
                usage = _merge_usage(
                    usage,
                    _usage(message.get("usage"), auth_profile=self.auth_profile),
                )
                finish = finish_from_anthropic(message.get("stop_reason"))
                continue
            if kind == "result":
                if bool(record.get("is_error")):
                    _raise_reason(
                        record.get("subtype"),
                        status=record.get("api_error_status"),
                        partial_output=saw_provider_output,
                    )
                result = record.get("result")
                if isinstance(result, str) and result:
                    authoritative_text = result
                usage = _merge_usage(
                    usage,
                    _usage(record.get("usage"), auth_profile=self.auth_profile),
                )
                finish = finish_from_anthropic(record.get("stop_reason"))
                terminal = True
                break

        if not terminal:
            raise TransientError(
                "Claude runtime ended before completion", provider="anthropic"
            )
        text = authoritative_text or "".join(text_parts)
        reasoning = authoritative_reasoning or "".join(reasoning_parts)
        if streaming and not text_parts and text:
            yield TextDelta(text)
        if streaming and not reasoning_parts and reasoning:
            yield ReasoningDelta(reasoning)
        if usage is not None:
            yield usage
        yield self._usage_meter.record(
            self.auth_profile,
            estimate=self._window_estimate_msgs,
        )
        yield FinalMessage(
            text=text,
            reasoning=reasoning,
            usage=usage,
            finish_reason=finish,
        )


def probe_claude_runtime(
    *,
    credentials: CredentialProvider | None = None,
    policy_guard: bool = True,
    environ: Mapping[str, str] | None = None,
    sdk_available: Callable[[], bool] | None = None,
    cli_locator: Callable[[str], str | None] | None = None,
    status_runner: Callable[..., Any] | None = None,
    timeout: float = 10.0,
) -> ClaudeRuntimeReason:
    """Probe local runtime/login state without making an inference request.

    A selected named setup-token candidate may be passed as ``credentials``;
    otherwise a transient user-minted ``CLAUDE_CODE_OAUTH_TOKEN`` is read from
    ``environ``.  Runtime-owned profiles use the official non-mutating
    ``claude auth status --json`` command.  Account identity fields are ignored.
    """

    # Keep the policy switch ahead of token reads, package inspection, runtime
    # location, subprocess work, and keychain access.
    if not policy_guard:
        return ClaudeRuntimeReason.POLICY_DISABLED
    credential_guard = getattr(credentials, "anthropic_policy_guard", True)
    if credential_guard is not True:
        # The probe is also a public seam for onboarding.  A disabled manager
        # must remain authoritative even when a caller forgets to copy its
        # policy value into this keyword argument.
        return ClaudeRuntimeReason.POLICY_DISABLED
    source = os.environ if environ is None else environ
    token = (
        credentials(CLAUDE_CODE_OAUTH_TOKEN_REF)
        if credentials is not None
        else source.get(CLAUDE_CODE_OAUTH_TOKEN_REF)
    )
    available = sdk_available or _sdk_available
    locator = cli_locator or shutil.which
    sdk_ready = available()
    cli = locator("claude") or (_bundled_sdk_cli() if sdk_ready else None)
    if not sdk_ready and not cli:
        return ClaudeRuntimeReason.RUNTIME_UNAVAILABLE
    if isinstance(token, str) and token.strip():
        return ClaudeRuntimeReason.OK
    if not cli:
        # The SDK can execute, but has no public local auth-status API.  Refuse
        # to claim credential readiness without evidence; a live doctor probe
        # remains available behind the explicit live_api boundary.
        return ClaudeRuntimeReason.MISSING_CREDENTIAL
    run = status_runner or subprocess.run
    probe_home = source.get("HOME") or source.get("USERPROFILE")
    try:
        completed = run(
            (cli, "auth", "status", "--json"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=str(Path(probe_home).expanduser()) if probe_home else str(Path.home()),
            env=_claude_child_env(source),
            shell=False,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ClaudeRuntimeReason.RUNTIME_UNAVAILABLE
    if getattr(completed, "returncode", 1) != 0:
        return ClaudeRuntimeReason.MISSING_CREDENTIAL
    try:
        status = json.loads(getattr(completed, "stdout", ""))
    except (TypeError, json.JSONDecodeError):
        return ClaudeRuntimeReason.INVALID_CREDENTIAL
    if not isinstance(status, Mapping):
        return ClaudeRuntimeReason.INVALID_CREDENTIAL
    logged_in = status.get("loggedIn", status.get("logged_in"))
    return (
        ClaudeRuntimeReason.OK
        if logged_in is True
        else ClaudeRuntimeReason.MISSING_CREDENTIAL
    )


def make_factory(
    **transport_kwargs: Any,
) -> Callable[[ModelBinding, CredentialProvider], ClaudeRuntimeTransport]:
    """Build a registry-compatible official Claude runtime factory."""

    def factory(
        binding: ModelBinding, credentials: CredentialProvider
    ) -> ClaudeRuntimeTransport:
        return ClaudeRuntimeTransport(binding, credentials, **transport_kwargs)

    return factory


__all__ = [
    "ClaudeInvocation",
    "ClaudePolicyError",
    "ClaudeRuntimeReason",
    "ClaudeRuntimeTransport",
    "make_factory",
    "probe_claude_runtime",
]
