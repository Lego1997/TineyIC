"""Isolated Claude Agent SDK bridge used by :mod:`claude_runtime`.

The Agent SDK merges ``ClaudeAgentOptions.env`` on top of the Python process'
ambient environment.  TinyIC therefore runs the SDK in this small subprocess,
whose parent supplies an allowlisted environment.  Prompts and options arrive
over stdin; only structural SDK messages leave over JSONL stdout.  The worker
never prints exception text or credential material.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Mapping


def _block(value: object) -> dict[str, Any]:
    name = type(value).__name__
    if name == "TextBlock":
        return {"type": "text", "text": getattr(value, "text", "")}
    if name == "ThinkingBlock":
        return {"type": "thinking", "thinking": getattr(value, "thinking", "")}
    if name in {"ToolUseBlock", "ServerToolUseBlock"}:
        return {
            "type": "server_tool_use" if name.startswith("Server") else "tool_use",
            "name": getattr(value, "name", "unknown"),
        }
    if "Tool" in name and "ResultBlock" in name:
        return {
            "type": "server_tool_result" if name.startswith("Server") else "tool_result"
        }
    return {"type": "unknown"}


def _safe_mapping(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        item = value.get(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            result[key] = int(item)
    return result


def _message(value: object) -> dict[str, Any] | None:
    name = type(value).__name__
    if name == "StreamEvent":
        event = getattr(value, "event", None)
        return {"type": "stream_event", "event": event if isinstance(event, dict) else {}}
    if name == "AssistantMessage":
        error = getattr(value, "error", None)
        safe_error = (
            error
            if error
            in {
                "authentication_failed",
                "billing_error",
                "rate_limit",
                "invalid_request",
                "server_error",
                "unknown",
            }
            else "unknown" if error else None
        )
        return {
            "type": "assistant",
            "error": safe_error,
            "message": {
                "content": [_block(block) for block in getattr(value, "content", [])],
                "usage": _safe_mapping(getattr(value, "usage", None)),
                "stop_reason": getattr(value, "stop_reason", None),
            },
        }
    if name == "ResultMessage":
        return {
            "type": "result",
            "subtype": getattr(value, "subtype", "unknown"),
            "is_error": bool(getattr(value, "is_error", False)),
            "result": getattr(value, "result", None),
            "usage": _safe_mapping(getattr(value, "usage", None)),
            "stop_reason": getattr(value, "stop_reason", None),
            "api_error_status": getattr(value, "api_error_status", None),
        }
    if name == "RateLimitEvent":
        info = getattr(value, "rate_limit_info", None)
        return {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": getattr(info, "status", None),
                "resets_at": getattr(info, "resets_at", None),
                "rate_limit_type": getattr(info, "rate_limit_type", None),
            },
        }
    # System/user/control messages contain no model output needed by TinyIC.
    return None


def _error_reason(error: BaseException) -> str:
    """Classify internally, returning only a fixed reason label."""
    value = f"{type(error).__name__} {error}".casefold()
    if "rate" in value or "usage limit" in value or "429" in value:
        return "usage_limited"
    if any(token in value for token in ("auth", "login", "credential", "401", "403")):
        return "invalid_credential"
    if "notfound" in value or "not found" in value:
        return "runtime_unavailable"
    return "runtime_failed"


async def _run(payload: Mapping[str, Any]) -> None:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = payload.get("options")
    if not isinstance(options, Mapping):
        raise ValueError("invalid options")
    kwargs = dict(options)
    # Current official SDK fields.  These flags make its bundled Claude Code
    # child match the hardened ``claude -p`` fallback while preserving the
    # SDK's identity-honest entrypoint and normal keychain/OAuth ownership.
    kwargs.update(
        {
            "cwd": payload.get("cwd"),
            "system_prompt": payload.get("system_prompt"),
            "env": {"CLAUDE_AGENT_SDK_CLIENT_APP": "tinyic/2"},
            "extra_args": {
                "safe-mode": None,
                "no-chrome": None,
                "no-session-persistence": None,
                "disable-slash-commands": None,
            },
        }
    )
    sdk_options = ClaudeAgentOptions(**kwargs)
    async for item in query(prompt=str(payload.get("prompt") or ""), options=sdk_options):
        encoded = _message(item)
        if encoded is not None:
            print(json.dumps(encoded, separators=(",", ":")), flush=True)


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, Mapping):
            raise ValueError("invalid payload")
        asyncio.run(_run(payload))
    except BaseException as error:  # worker boundary must never reflect SDK text
        print(
            json.dumps(
                {"type": "tinyic_runtime_error", "reason": _error_reason(error)},
                separators=(",", ":"),
            ),
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(main())
