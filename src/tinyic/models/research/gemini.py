"""Gemini Interactions ``google_search`` research backend."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tinyic.personas.factory.types import SearchRequest, SearchResponse

from ._base import (
    BaseResearchBackend,
    Completion,
    EvidenceCollector,
    PURPOSE_SEARCH,
    annotation_excerpt,
    search_prompt,
    usage_from_gemini,
)


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _usage_object(data: Mapping[str, Any]) -> Any:
    return data.get("usage") or data.get("total_usage") or data.get("usageMetadata")


def _interaction_text_and_annotations(
    data: Mapping[str, Any],
) -> tuple[str, tuple[tuple[str, Mapping[str, Any]], ...]]:
    texts: list[str] = []
    annotations: list[tuple[str, Mapping[str, Any]]] = []
    for step in data.get("steps") or ():
        if not isinstance(step, Mapping) or step.get("type") != "model_output":
            continue
        for block in step.get("content") or ():
            if not isinstance(block, Mapping) or block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            texts.append(text)
            for annotation in block.get("annotations") or ():
                if isinstance(annotation, Mapping):
                    annotations.append((text, annotation))

    # Legacy generateContent responses remain accepted so recorded research
    # logs survive a provider migration, but new requests use Interactions.
    for candidate in data.get("candidates") or ():
        if not isinstance(candidate, Mapping):
            continue
        content = candidate.get("content")
        if not isinstance(content, Mapping):
            continue
        for part in content.get("parts") or ():
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                texts.append(str(part["text"]))
    if not texts and isinstance(data.get("output_text"), str):
        texts.append(str(data["output_text"]))
    return "\n".join(texts).strip(), tuple(annotations)


def _grounding_metadata(candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    raw = candidate.get("groundingMetadata", candidate.get("grounding_metadata"))
    return raw if isinstance(raw, Mapping) else None


def _chunk_web(chunk: Any) -> Mapping[str, Any] | None:
    if not isinstance(chunk, Mapping):
        return None
    raw = chunk.get("web", chunk)
    return raw if isinstance(raw, Mapping) else None


def _segment_excerpt(text: str, raw: Any) -> str:
    if not isinstance(raw, Mapping):
        return text
    start = raw.get("startIndex", raw.get("start_index"))
    end = raw.get("endIndex", raw.get("end_index"))
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
        return text[start:end]
    return text


def _grounding_tool_count(data: Mapping[str, Any]) -> int:
    count = 0
    for step in data.get("steps") or ():
        if not isinstance(step, Mapping) or step.get("type") != "google_search_call":
            continue
        arguments = step.get("arguments")
        queries = arguments.get("queries") if isinstance(arguments, Mapping) else None
        if isinstance(queries, Sequence) and not isinstance(queries, (str, bytes)):
            count += sum(1 for query in queries if str(query).strip()) or 1
        else:
            count += 1
    usage = _usage_object(data)
    if isinstance(usage, Mapping):
        raw_counts = usage.get("grounding_tool_count")
        if isinstance(raw_counts, Mapping):
            if raw_counts.get("type") in (None, "google_search"):
                raw = raw_counts.get("count")
                if isinstance(raw, int) and not isinstance(raw, bool):
                    count = max(count, raw)
        elif isinstance(raw_counts, Sequence) and not isinstance(
            raw_counts, (str, bytes)
        ):
            for row in raw_counts:
                if isinstance(row, Mapping) and row.get("type") == "google_search":
                    raw = row.get("count")
                    if isinstance(raw, int) and not isinstance(raw, bool):
                        count = max(count, raw)
    return count


class GeminiResearchBackend(BaseResearchBackend):
    """Gemini Interactions with structured ``url_citation`` grounding."""

    provider = "google"
    credential_refs = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    endpoint_suffix = "/interactions"

    def __init__(self, binding, credentials, *, base_url=GEMINI_BASE_URL, **kwargs):
        super().__init__(binding, credentials, base_url=base_url, **kwargs)

    def _auth_headers(self, key: str) -> dict[str, str]:
        # Header auth keeps the secret out of request URLs and test snapshots.
        return {"x-goog-api-key": key}

    def _complete(self, prompt: str) -> Completion:
        body = self._body_params()
        body.update({"model": self.binding.model, "input": prompt})
        data = self._post_json(body)
        text, _annotations = _interaction_text_and_annotations(data)
        return Completion(text, usage_from_gemini(_usage_object(data)))

    def search(self, request: SearchRequest) -> SearchResponse:
        body = self._body_params()
        body.update(
            {
                "model": self.binding.model,
                "input": search_prompt(request),
                "tools": [{"type": "google_search"}],
            }
        )
        data = self._post_json(body)
        text, annotations = _interaction_text_and_annotations(data)
        collector = EvidenceCollector(request, self.provider)

        # Current Interactions responses expose the actual search-result rows.
        for step in data.get("steps") or ():
            if not isinstance(step, Mapping) or step.get("type") != "google_search_result":
                continue
            result = step.get("result")
            if isinstance(result, Mapping):
                result = result.get("results", result.get("search_results"))
            rows = result if isinstance(result, Sequence) and not isinstance(
                result, (str, bytes)
            ) else ()
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                collector.add(
                    row.get("url", row.get("uri")),
                    title=row.get("title"),
                    excerpt=row.get("snippet"),
                    fallback_text=text,
                )

        for block_text, annotation in annotations:
            if annotation.get("type") != "url_citation":
                continue
            collector.add(
                annotation.get("url"),
                title=annotation.get("title"),
                excerpt=annotation_excerpt(block_text, annotation),
                fallback_text=block_text,
            )

        # Legacy groundingMetadata: supports identify which chunk grounds which
        # span. This is parse-only compatibility; requests never use the legacy
        # generateContent endpoint.
        for candidate in data.get("candidates") or ():
            if not isinstance(candidate, Mapping):
                continue
            candidate_text, _unused = _interaction_text_and_annotations(
                {"candidates": [candidate]}
            )
            metadata = _grounding_metadata(candidate)
            if metadata is None:
                continue
            chunks = tuple(metadata.get("groundingChunks") or ())
            supported_indexes: set[int] = set()
            for support in metadata.get("groundingSupports") or ():
                if not isinstance(support, Mapping):
                    continue
                excerpt = _segment_excerpt(candidate_text, support.get("segment"))
                for index in support.get("groundingChunkIndices") or ():
                    if not isinstance(index, int) or isinstance(index, bool):
                        continue
                    if not 0 <= index < len(chunks):
                        continue
                    supported_indexes.add(index)
                    web = _chunk_web(chunks[index])
                    if web is not None:
                        collector.add(
                            web.get("uri", web.get("url")),
                            title=web.get("title"),
                            excerpt=excerpt,
                            fallback_text=candidate_text,
                        )
            for index, chunk in enumerate(chunks):
                if index in supported_indexes:
                    continue
                web = _chunk_web(chunk)
                if web is not None:
                    collector.add(
                        web.get("uri", web.get("url")),
                        title=web.get("title"),
                        fallback_text=candidate_text,
                    )

        tool_calls = _grounding_tool_count(data)
        if tool_calls == 0 and collector.evidence:
            tool_calls = 1
        usage = self._usage(
            usage_from_gemini(_usage_object(data)),
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


__all__ = ["GEMINI_BASE_URL", "GeminiResearchBackend"]
