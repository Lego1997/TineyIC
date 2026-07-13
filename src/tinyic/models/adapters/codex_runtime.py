"""ChatGPT-subscription transport backed only by Codex app-server stdio.

This adapter intentionally is *not* an OpenAI HTTP adapter.  It starts the
official ``codex app-server --listen stdio://`` runtime, creates one ephemeral
thread per normalized model call, and consumes app-server JSON-RPC
notifications.  No Platform or private subscription endpoint exists here.

The thread runs in a fresh empty directory with approval disabled, a read-only
sandbox, no environment/workspace roots, no dynamic tools, no MCP servers, and
web search disabled.  The input surface is text-only.  App-server remains the
owner of ChatGPT credentials and their refresh rotation.

Thinking follows the approved FR-1.3 subscription contract: the selected model
id (for example ``gpt-5.6-sol``) carries the tier, so TinyIC intentionally omits
``turn/start.effort``.  Codex app-server 0.144.1 added an optional effort field
and advertised per-model effort capabilities after the PRD evidence was
recorded.  That code-reality/PRD conflict is left explicit for owner resolution;
M3 does not silently switch from tier selection to the newer knob.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tinyic.auth.openai import (
    RPCFactory,
    default_rpc_factory,
    initialize_rpc,
)
from tinyic.auth.usage_window import RollingUsageMeter

from ..binding import ModelBinding
from ..credentials import CredentialProvider
from ..types import (
    AuthError,
    ChatMessage,
    ChatRequest,
    ChatStreamEvent,
    FinalMessage,
    FinishReason,
    InvalidRequestError,
    ReasoningDelta,
    RateLimitError,
    Role,
    TextDelta,
    TransientError,
    Usage,
    UsageLimitError,
)


_TEXT_ONLY_INSTRUCTIONS = (
    "You are a text-completion runtime embedded in TinyIC. Respond to the "
    "provided text using model reasoning and return only the requested answer. "
    "Do not call shell, file, web, MCP, app, collaboration, or other tools. "
    "Do not inspect the working directory or external environment."
)

_ALLOWED_ITEM_TYPES = frozenset({"agentMessage", "reasoning", "userMessage"})
_ERROR_USAGE_LIMIT = frozenset(
    {
        "usageLimitExceeded",
        "sessionBudgetExceeded",
        "workspace_owner_usage_limit_reached",
        "workspace_member_usage_limit_reached",
    }
)
_ERROR_AUTH = frozenset({"unauthorized"})
_ERROR_INVALID = frozenset({"badRequest", "cyberPolicy"})


def _system_instructions(messages: Sequence[ChatMessage]) -> str:
    system = [message.content for message in messages if message.role is Role.SYSTEM]
    if not system:
        return _TEXT_ONLY_INSTRUCTIONS
    return _TEXT_ONLY_INSTRUCTIONS + "\n\nRequest instructions:\n" + "\n\n".join(system)


def _turn_text(messages: Sequence[ChatMessage]) -> str:
    conversational = [
        message for message in messages if message.role is not Role.SYSTEM
    ]
    if len(conversational) == 1 and conversational[0].role is Role.USER:
        return conversational[0].content
    if not conversational:
        return ""
    return "\n\n".join(
        f"<{message.role.value}>\n{message.content}" for message in conversational
    )


def _thread_id(response: Mapping[str, Any]) -> str:
    thread = response.get("thread")
    value = thread.get("id") if isinstance(thread, Mapping) else None
    if not isinstance(value, str) or not value:
        raise TransientError(
            "Codex runtime returned an invalid thread response", provider="openai"
        )
    return value


def _turn_id(response: Mapping[str, Any]) -> str:
    turn = response.get("turn")
    value = turn.get("id") if isinstance(turn, Mapping) else None
    if not isinstance(value, str) or not value:
        raise TransientError(
            "Codex runtime returned an invalid turn response", provider="openai"
        )
    return value


def _codex_error_info(raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Mapping) and len(raw) == 1:
        key = next(iter(raw))
        return key if isinstance(key, str) else None
    return None


def _raise_runtime_error(error: Any) -> None:
    """Raise a normalized fixed-text error without reflecting runtime data."""

    payload = error if isinstance(error, Mapping) else {}
    info = _codex_error_info(payload.get("codexErrorInfo"))
    if info in _ERROR_USAGE_LIMIT:
        raise UsageLimitError(
            "ChatGPT subscription usage limit reached", provider="openai"
        )
    if info in _ERROR_AUTH:
        raise AuthError(
            "ChatGPT subscription credential is invalid or expired",
            provider="openai",
        )
    if info in _ERROR_INVALID:
        raise InvalidRequestError(
            "Codex runtime rejected the subscription request", provider="openai"
        )
    raise TransientError(
        "Codex subscription runtime failed", provider="openai"
    )


def _usage_from_notification(
    params: Mapping[str, Any], *, auth_profile: str
) -> Usage | None:
    token_usage = params.get("tokenUsage")
    if not isinstance(token_usage, Mapping):
        return None
    last = token_usage.get("last")
    if not isinstance(last, Mapping):
        return None
    try:
        return Usage(
            input_tokens=max(0, int(last.get("inputTokens") or 0)),
            output_tokens=max(0, int(last.get("outputTokens") or 0)),
            cached_tokens=max(0, int(last.get("cachedInputTokens") or 0)),
            auth_profile=auth_profile,
            lane="subscription",
        )
    except (TypeError, ValueError):
        return None


def _completed_items(turn: Mapping[str, Any]) -> tuple[str | None, str | None]:
    text: str | None = None
    reasoning: str | None = None
    items = turn.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return text, reasoning
    for item in items:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("type")
        if kind == "agentMessage" and isinstance(item.get("text"), str):
            text = item["text"]
        elif kind == "reasoning":
            summary = item.get("summary")
            content = item.get("content")
            values = summary if isinstance(summary, list) and summary else content
            if isinstance(values, list) and all(isinstance(v, str) for v in values):
                reasoning = "".join(values)
    return text, reasoning


@contextmanager
def _safe_cwd(
    cwd_factory: Callable[[], Path] | None,
) -> Iterator[Path]:
    if cwd_factory is not None:
        path = Path(cwd_factory()).expanduser().resolve()
        if not path.is_dir():
            raise InvalidRequestError("Codex runtime cwd must be an existing directory")
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="tinyic-codex-") as directory:
        yield Path(directory).resolve()


class CodexRuntimeTransport:
    """Normalized ``Transport`` for a ChatGPT-subscription model binding.

    ``credentials`` is accepted to preserve the model registry's existing
    ``(binding, credentials) -> Transport`` factory signature, but it is never
    read: app-server owns subscription authentication.  ``rpc_factory`` and
    ``cwd_factory`` are injectable so offline tests spawn neither a process nor
    network traffic.
    """

    def __init__(
        self,
        binding: ModelBinding,
        credentials: CredentialProvider | None,
        *,
        rpc_factory: RPCFactory | None = None,
        cwd_factory: Callable[[], Path] | None = None,
        usage_meter: RollingUsageMeter | None = None,
        window_estimate_msgs: int = 0,
    ) -> None:
        if binding.provider.lower() != "openai":
            raise ValueError("Codex runtime requires an openai/* binding")
        if window_estimate_msgs < 0:
            raise ValueError("window_estimate_msgs cannot be negative")
        self.binding = binding
        self._rpc_factory = rpc_factory or default_rpc_factory
        self._cwd_factory = cwd_factory
        self._usage_meter = usage_meter or RollingUsageMeter()
        self._window_estimate_msgs = window_estimate_msgs
        self.auth_profile = binding.auth_profile or "openai:chatgpt"

    def generate(self, request: ChatRequest) -> Iterator[ChatStreamEvent]:
        """Run one ephemeral text-only Codex thread and normalize its stream."""

        if request.binding.provider.lower() != "openai":
            raise InvalidRequestError(
                "Codex runtime received a non-OpenAI binding", provider="openai"
            )
        if request.binding.model != self.binding.model:
            raise InvalidRequestError(
                "Codex runtime request model does not match its binding",
                provider="openai",
            )
        with _safe_cwd(self._cwd_factory) as cwd:
            try:
                rpc = self._rpc_factory()
            except (FileNotFoundError, OSError, RuntimeError):
                raise TransientError(
                    "Codex app-server is unavailable", provider="openai"
                ) from None
            try:
                initialize_rpc(rpc)
                account = rpc.request(
                    "account/read", {"refreshToken": False}
                ).get("account")
                if not isinstance(account, Mapping) or account.get("type") != "chatgpt":
                    # A Codex runtime may itself be configured with a Platform
                    # API key.  Refuse that account before thread creation so a
                    # subscription profile can never be mislabeled or billed.
                    raise AuthError(
                        "ChatGPT subscription sign-in is required for this lane",
                        provider="openai",
                    )
                thread_response = rpc.request(
                    "thread/start", self._thread_params(request, cwd)
                )
                returned_provider = thread_response.get("modelProvider")
                if (
                    thread_response.get("model") != request.binding.model
                    or not isinstance(returned_provider, str)
                    or returned_provider.casefold() != "openai"
                ):
                    raise InvalidRequestError(
                        "Codex runtime returned an unexpected model route",
                        provider="openai",
                    )
                thread_id = _thread_id(thread_response)
                turn_response = rpc.request(
                    "turn/start", self._turn_params(request, thread_id)
                )
                turn_id = _turn_id(turn_response)
                yield from self._consume_turn(rpc.notifications(), thread_id, turn_id)
            except (AuthError, RateLimitError, InvalidRequestError, TransientError):
                raise
            except (OSError, RuntimeError):
                raise TransientError(
                    "Codex subscription runtime became unavailable",
                    provider="openai",
                ) from None
            finally:
                rpc.close()

    def _thread_params(self, request: ChatRequest, cwd: Path) -> dict[str, Any]:
        return {
            "model": request.binding.model,
            "cwd": str(cwd),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": True,
            "baseInstructions": _TEXT_ONLY_INSTRUCTIONS,
            "developerInstructions": _system_instructions(request.messages),
            "dynamicTools": [],
            "environments": [],
            "runtimeWorkspaceRoots": [],
            "selectedCapabilityRoots": [],
            "config": {
                "mcp_servers": {},
                "web_search": "disabled",
            },
        }

    def _turn_params(self, request: ChatRequest, thread_id: str) -> dict[str, Any]:
        # Deliberately no ``effort``: FR-1.3 assigns subscription thinking to
        # model tier, encoded in thread/start.model.  Current app-server now
        # accepts an optional effort field, but adopting it requires the owner
        # decision recorded as an M3 PR risk rather than an adapter assumption.
        return {
            "threadId": thread_id,
            "input": [{"type": "text", "text": _turn_text(request.messages)}],
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            "environments": [],
            "runtimeWorkspaceRoots": [],
        }

    def _consume_turn(
        self,
        notifications: Iterator[Mapping[str, Any]],
        thread_id: str,
        turn_id: str,
    ) -> Iterator[ChatStreamEvent]:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        authoritative_text: str | None = None
        authoritative_reasoning: str | None = None
        usage: Usage | None = None
        terminal: Mapping[str, Any] | None = None

        for notification in notifications:
            method = notification.get("method")
            params = notification.get("params")
            if not isinstance(params, Mapping):
                continue
            notified_thread = params.get("threadId")
            notified_turn = params.get("turnId")
            if notified_thread not in (None, thread_id):
                continue
            if notified_turn not in (None, turn_id):
                continue

            if method == "error":
                _raise_runtime_error(params.get("error"))
            elif method in {
                "item/reasoning/summaryTextDelta",
                "item/reasoning/textDelta",
            }:
                delta = params.get("delta")
                if isinstance(delta, str) and delta:
                    reasoning_parts.append(delta)
                    yield ReasoningDelta(delta)
            elif method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str) and delta:
                    text_parts.append(delta)
                    yield TextDelta(delta)
            elif method == "thread/tokenUsage/updated":
                reported = _usage_from_notification(
                    params, auth_profile=self.auth_profile
                )
                if reported is not None:
                    usage = reported
            elif method in {"item/started", "item/completed"}:
                item = params.get("item")
                if isinstance(item, Mapping):
                    kind = item.get("type")
                    if kind not in _ALLOWED_ITEM_TYPES:
                        raise InvalidRequestError(
                            "Codex subscription binding attempted prohibited "
                            "tool activity",
                            provider="openai",
                        )
                    if method == "item/completed":
                        if kind == "agentMessage" and isinstance(item.get("text"), str):
                            authoritative_text = item["text"]
                        elif kind == "reasoning":
                            values = item.get("summary") or item.get("content")
                            if isinstance(values, list) and all(
                                isinstance(value, str) for value in values
                            ):
                                authoritative_reasoning = "".join(values)
            elif method == "turn/completed":
                turn = params.get("turn")
                if isinstance(turn, Mapping) and turn.get("id") == turn_id:
                    terminal = turn
                    item_text, item_reasoning = _completed_items(turn)
                    authoritative_text = item_text or authoritative_text
                    authoritative_reasoning = item_reasoning or authoritative_reasoning
                    break

        if terminal is None:
            raise TransientError(
                "Codex runtime ended before the turn completed", provider="openai"
            )
        status = terminal.get("status")
        if status == "failed":
            _raise_runtime_error(terminal.get("error"))
        finish = (
            FinishReason.CANCELLED if status == "interrupted" else FinishReason.STOP
        )
        text = authoritative_text or "".join(text_parts)
        reasoning = authoritative_reasoning or "".join(reasoning_parts)
        if not text_parts and text:
            yield TextDelta(text)
        if not reasoning_parts and reasoning:
            yield ReasoningDelta(reasoning)
        if usage is not None:
            yield usage
        if status == "completed":
            yield self._usage_meter.record(
                self.auth_profile, estimate=self._window_estimate_msgs
            )
        yield FinalMessage(
            text=text,
            reasoning=reasoning,
            usage=usage,
            finish_reason=finish,
        )


def make_factory(
    **transport_kwargs: Any,
) -> Callable[[ModelBinding, CredentialProvider], CodexRuntimeTransport]:
    """Build a registry-compatible Codex subscription transport factory."""

    def factory(
        binding: ModelBinding, credentials: CredentialProvider
    ) -> CodexRuntimeTransport:
        return CodexRuntimeTransport(
            binding, credentials, **transport_kwargs
        )

    return factory


__all__ = ["CodexRuntimeTransport", "make_factory"]
