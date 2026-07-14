"""OpenAI and Grok persona-research backends over the Responses API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tinyic.personas.factory.types import SearchRequest, SearchResponse

from ._base import (
    BaseResearchBackend,
    Completion,
    EvidenceCollector,
    PURPOSE_SEARCH,
    annotation_excerpt,
    response_text_and_annotations,
    response_web_search_calls,
    search_prompt,
    usage_from_responses,
)


OPENAI_BASE_URL = "https://api.openai.com/v1"
GROK_BASE_URL = "https://api.x.ai/v1"


def _source_value(source: Any, field: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(field)
    return source if field == "url" else ""


def _add_response_annotations(
    collector: EvidenceCollector,
    annotations: tuple[tuple[str, Mapping[str, Any]], ...],
) -> set[str]:
    urls: set[str] = set()
    for block_text, annotation in annotations:
        if annotation.get("type") != "url_citation":
            continue
        url = annotation.get("url")
        if isinstance(url, str):
            urls.add(url.strip())
        collector.add(
            url,
            title=annotation.get("title"),
            excerpt=annotation_excerpt(block_text, annotation),
            fallback_text=block_text,
        )
    return urls


class _ResponsesResearchBackend(BaseResearchBackend):
    endpoint_suffix = "/responses"

    def _complete(self, prompt: str) -> Completion:
        body = self._body_params()
        body.update({"model": self.binding.model, "input": prompt})
        data = self._post_json(body)
        text, _annotations = response_text_and_annotations(data)
        return Completion(text, usage_from_responses(data.get("usage")))


class OpenAIResearchBackend(_ResponsesResearchBackend):
    """OpenAI Responses with forced web search and complete source inclusion."""

    provider = "openai"
    credential_refs = ("OPENAI_API_KEY",)

    def __init__(self, binding, credentials, *, base_url=OPENAI_BASE_URL, **kwargs):
        super().__init__(binding, credentials, base_url=base_url, **kwargs)

    def search(self, request: SearchRequest) -> SearchResponse:
        tool: dict[str, Any] = {"type": "web_search", "search_context_size": "high"}
        body = self._body_params()
        body.update(
            {
                "model": self.binding.model,
                "input": search_prompt(request),
                "tools": [tool],
                "tool_choice": "required",
                "include": ["web_search_call.action.sources"],
            }
        )
        if request.remaining_searches is not None:
            # Responses exposes a native aggregate built-in-tool ceiling. With
            # only web_search enabled this is the exact remaining run budget.
            body["max_tool_calls"] = request.remaining_searches
        data = self._post_json(body)
        text, annotations = response_text_and_annotations(data)
        calls = response_web_search_calls(data)
        collector = EvidenceCollector(request, self.provider)
        annotated_urls = _add_response_annotations(collector, annotations)
        for call in calls:
            action = call.get("action")
            if not isinstance(action, Mapping):
                continue
            for source in action.get("sources") or ():
                collector.add(
                    _source_value(source, "url"),
                    title=_source_value(source, "title"),
                    excerpt=_source_value(source, "snippet"),
                    fallback_text=(
                        ""
                        if str(_source_value(source, "url") or "").strip()
                        in annotated_urls
                        else text
                    ),
                )
        tool_calls = max(1, len(calls))  # tool_choice=required guarantees at least one
        usage = self._usage(
            usage_from_responses(data.get("usage")),
            purpose=PURPOSE_SEARCH,
            search_tool_calls=tool_calls,
            search_calls=tool_calls,
        )
        return SearchResponse(
            collector.evidence,
            usage,
            budget_exhausted=(
                request.remaining_searches is not None
                and tool_calls >= request.remaining_searches
            ),
        )


class GrokResearchBackend(_ResponsesResearchBackend):
    """xAI Responses web search with flat and inline citation normalization."""

    provider = "grok"
    credential_refs = ("XAI_API_KEY",)

    def __init__(self, binding, credentials, *, base_url=GROK_BASE_URL, **kwargs):
        super().__init__(binding, credentials, base_url=base_url, **kwargs)

    @staticmethod
    def _reported_tool_calls(data: Mapping[str, Any]) -> int:
        usage = data.get("usage")
        if isinstance(usage, Mapping):
            details = usage.get("server_side_tool_usage_details")
            if isinstance(details, Mapping):
                raw_count = details.get("web_search_calls")
                if isinstance(raw_count, int) and not isinstance(raw_count, bool):
                    # This is xAI's documented successful, billable web-search
                    # count.  Preserve an explicit zero rather than inferring a
                    # charge from attempted output rows or returned citations.
                    return max(0, raw_count)

        # Preserve compatibility with the pre-release response shape used by
        # older xAI clients, while preferring the documented usage nesting.
        raw = data.get("server_side_tool_usage")
        reported: list[int] = []
        if isinstance(raw, Mapping):
            for key, value in raw.items():
                if "web_search" in str(key).casefold():
                    if isinstance(value, Mapping):
                        raw_count = value.get("count")
                        if isinstance(raw_count, int) and not isinstance(
                            raw_count, bool
                        ):
                            reported.append(max(0, raw_count))
                    elif isinstance(value, int) and not isinstance(value, bool):
                        reported.append(max(0, value))
        # xAI bills successful server-side tool use. Its explicit usage count
        # is therefore authoritative over attempted call rows in ``output``;
        # responses without that metric fall back to successful output rows.
        if reported:
            return max(reported)
        return sum(
            1
            for call in response_web_search_calls(data)
            if call.get("status") is None
            or str(call.get("status")).casefold() == "completed"
        )

    def search(self, request: SearchRequest) -> SearchResponse:
        body = self._body_params()
        body.update(
            {
                "model": self.binding.model,
                "input": search_prompt(request),
                "tools": [{"type": "web_search"}],
                # Serialize server-side calls so max_turns is also an exact
                # ceiling on the only enabled tool's billable invocations.
                "parallel_tool_calls": False,
            }
        )
        if request.remaining_searches is not None:
            body["max_turns"] = request.remaining_searches
        data = self._post_json(body)
        text, annotations = response_text_and_annotations(data)
        collector = EvidenceCollector(request, self.provider)
        annotated_urls = _add_response_annotations(collector, annotations)
        for citation in data.get("citations") or ():
            collector.add(
                _source_value(citation, "url"),
                title=_source_value(citation, "title"),
                excerpt=_source_value(citation, "snippet"),
                fallback_text=(
                    ""
                    if str(_source_value(citation, "url") or "").strip()
                    in annotated_urls
                    else text
                ),
            )
        tool_calls = self._reported_tool_calls(data)
        usage = self._usage(
            usage_from_responses(data.get("usage")),
            purpose=PURPOSE_SEARCH,
            search_tool_calls=tool_calls,
            search_calls=tool_calls,
        )
        return SearchResponse(
            collector.evidence,
            usage,
            budget_exhausted=(
                request.remaining_searches is not None
                and tool_calls >= request.remaining_searches
            ),
        )


__all__ = [
    "GROK_BASE_URL",
    "OPENAI_BASE_URL",
    "GrokResearchBackend",
    "OpenAIResearchBackend",
]
