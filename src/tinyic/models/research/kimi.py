"""Kimi ``$web_search`` echo-loop backend (degraded prose-URL citations)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from tinyic.personas.factory.types import SearchRequest, SearchResponse

from ..adapters.kimi_chat import (
    MAX_WEB_SEARCH_ROUNDS,
    WEB_SEARCH_TOOL_NAME,
)
from ..types import TransientError
from ._base import (
    BaseResearchBackend,
    Completion,
    EvidenceCollector,
    PURPOSE_SEARCH,
    UsageNumbers,
    search_prompt,
    usage_from_chat,
)


KIMI_BASE_URL = "https://api.moonshot.ai/v1"
KIMI_BASE_URL_ENV_VAR = "MOONSHOT_BASE_URL"
MAX_RESEARCH_SEARCH_ROUNDS = 16

_MARKDOWN_URL_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", re.I)
_BARE_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def _web_search_tool() -> dict[str, Any]:
    return {
        "type": "builtin_function",
        "function": {"name": WEB_SEARCH_TOOL_NAME},
    }


def _tool_calls(message: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    calls: list[dict[str, str]] = []
    for index, raw in enumerate(message.get("tool_calls") or ()):
        if not isinstance(raw, Mapping):
            continue
        function = raw.get("function")
        if not isinstance(function, Mapping):
            function = {}
        calls.append(
            {
                "id": str(raw.get("id") or f"call-{index}"),
                "type": str(raw.get("type") or "builtin_function"),
                "name": str(function.get("name") or ""),
                # Echo exactly what the server returned; do not decode/re-encode.
                "arguments": str(function.get("arguments") or ""),
            }
        )
    return tuple(calls)


def _assistant_echo(text: str, calls: tuple[dict[str, str], ...]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {
                "id": call["id"],
                "type": call["type"],
                "function": {
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            }
            for call in calls
        ],
    }


def _tool_echo(call: Mapping[str, str]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call["id"],
        "name": call["name"],
        "content": call["arguments"],
    }


def _usage_total(records: list[UsageNumbers]) -> UsageNumbers:
    if not records:
        return UsageNumbers()
    return UsageNumbers(
        sum(item.input_tokens for item in records),
        sum(item.output_tokens for item in records),
        sum(item.cached_tokens for item in records),
        reported=all(item.reported for item in records),
    )


def _paragraph_at(text: str, offset: int) -> str:
    start = text.rfind("\n\n", 0, offset)
    end = text.find("\n\n", offset)
    return text[(start + 2 if start >= 0 else 0) : (end if end >= 0 else len(text))]


class KimiResearchBackend(BaseResearchBackend):
    """Moonshot search with client echo and independently visible prose URLs.

    Kimi has no structured citation field. ``degraded`` is intentionally public
    so the factory/CLI can require the stricter verification lane and disclose
    the limitation.
    """

    provider = "kimi"
    credential_refs = ("MOONSHOT_API_KEY", "KIMI_API_KEY")
    endpoint_suffix = "/chat/completions"
    citation_quality = "prose_urls"
    degraded = True

    def __init__(
        self,
        binding,
        credentials,
        *,
        base_url=None,
        max_search_rounds: int = MAX_RESEARCH_SEARCH_ROUNDS,
        **kwargs,
    ):
        if (
            not isinstance(max_search_rounds, int)
            or isinstance(max_search_rounds, bool)
            or not 1 <= max_search_rounds <= MAX_RESEARCH_SEARCH_ROUNDS
        ):
            raise ValueError(
                "max_search_rounds must be an integer between 1 and "
                f"{MAX_RESEARCH_SEARCH_ROUNDS}"
            )
        # Moonshot requires thinking disabled whenever $web_search is present.
        binding = binding.with_thinking("off")
        base_url = base_url or os.environ.get(KIMI_BASE_URL_ENV_VAR) or KIMI_BASE_URL
        super().__init__(binding, credentials, base_url=base_url, **kwargs)
        self._remaining_search_rounds = max_search_rounds

    @property
    def remaining_search_rounds(self) -> int:
        """Unspent HTTP rounds in the run-wide ``$web_search`` budget."""
        return self._remaining_search_rounds

    def _chat_body(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body = self._body_params()
        body.update(
            {
                "model": self.binding.model,
                "messages": messages,
                "stream": False,
                "thinking": {"type": "disabled"},
            }
        )
        return body

    def _one_chat(
        self, body: Mapping[str, Any]
    ) -> tuple[str, str, tuple[dict[str, str], ...], UsageNumbers]:
        data = self._post_json(body)
        choices = data.get("choices") or ()
        choice = choices[0] if choices and isinstance(choices[0], Mapping) else {}
        message = choice.get("message") if isinstance(choice, Mapping) else {}
        if not isinstance(message, Mapping):
            message = {}
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        finish = str(choice.get("finish_reason") or "stop")
        return text, finish, _tool_calls(message), usage_from_chat(data.get("usage"))

    def _complete(self, prompt: str) -> Completion:
        text, _finish, _calls, usage = self._one_chat(
            self._chat_body([{"role": "user", "content": prompt}])
        )
        return Completion(text, usage)

    def search(self, request: SearchRequest) -> SearchResponse:
        conversation: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": search_prompt(request, inline_urls=True),
            }
        ]
        text_parts: list[str] = []
        usage_records: list[UsageNumbers] = []
        search_tool_calls = 0

        requested_remaining = (
            request.remaining_searches
            if request.remaining_searches is not None
            else self._remaining_search_rounds
        )
        query_round_limit = min(
            MAX_WEB_SEARCH_ROUNDS,
            self._remaining_search_rounds,
            requested_remaining,
        )
        for round_index in range(query_round_limit):
            # Charge before sending so exceptions cannot accidentally make a
            # failed network round free.  The same counter survives subsequent
            # logical search queries in this factory run.
            self._remaining_search_rounds -= 1
            body = self._chat_body(list(conversation))
            body["tools"] = [_web_search_tool()]
            text, finish, calls, usage = self._one_chat(body)
            usage_records.append(usage)
            if text:
                text_parts.append(text)
            if finish == "tool_calls" and calls:
                search_tool_calls += sum(
                    1 for call in calls if call["name"] == WEB_SEARCH_TOOL_NAME
                )
                conversation.append(_assistant_echo(text, calls))
                conversation.extend(_tool_echo(call) for call in calls)
                continue

            final_text = "".join(text_parts)
            collector = EvidenceCollector(request, self.provider)
            for match in _MARKDOWN_URL_RE.finditer(final_text):
                collector.add(
                    match.group(2),
                    title=match.group(1),
                    excerpt=_paragraph_at(final_text, match.start()),
                    fallback_text=final_text,
                )
            for match in _BARE_URL_RE.finditer(final_text):
                collector.add(
                    match.group(0),
                    excerpt=_paragraph_at(final_text, match.start()),
                    fallback_text=final_text,
                )
            numbers = _usage_total(usage_records)
            usage_record = self._usage(
                numbers,
                purpose=PURPOSE_SEARCH,
                calls=round_index + 1,
                search_tool_calls=search_tool_calls,
                search_calls=round_index + 1,
            )
            return SearchResponse(
                collector.evidence,
                usage_record,
                budget_exhausted=(
                    self._remaining_search_rounds == 0
                    or round_index + 1 >= requested_remaining
                ),
            )

        request_budget_exhausted = query_round_limit >= requested_remaining
        if self._remaining_search_rounds == 0 or request_budget_exhausted:
            # Exhausting the user-selected run-wide budget is a normal bounded
            # completion condition, not a provider failure.  Preserve usage
            # from an unfinished final echo loop so the factory can account for
            # every paid round, then let it continue with evidence gathered by
            # earlier logical queries and apply its ordinary source gate.
            usage_record = None
            if usage_records:
                usage_record = self._usage(
                    _usage_total(usage_records),
                    purpose=PURPOSE_SEARCH,
                    calls=len(usage_records),
                    search_tool_calls=search_tool_calls,
                    search_calls=len(usage_records),
                )
            return SearchResponse(
                (),
                usage_record,
                budget_exhausted=True,
            )
        raise TransientError(
            f"kimi {WEB_SEARCH_TOOL_NAME} did not converge within "
            f"{MAX_WEB_SEARCH_ROUNDS} rounds for one query",
            provider=self.provider,
        )


__all__ = [
    "KIMI_BASE_URL",
    "KIMI_BASE_URL_ENV_VAR",
    "MAX_RESEARCH_SEARCH_ROUNDS",
    "KimiResearchBackend",
]
