"""Shared HTTP, evidence, and usage machinery for persona research backends.

Research calls deliberately reuse the model layer's two injectable boundaries:
an opaque :class:`~tinyic.models.credentials.CredentialProvider` and an
SDK-free :class:`~tinyic.models.adapters._http.HttpTransport`.  Provider
modules only shape JSON and normalize citations; no backend fetches a cited
URL locally.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urlsplit

from tinyic.personas.factory.types import (
    CallUsage,
    DossierSynthesisRequest,
    DossierSynthesisResponse,
    Evidence,
    PersonaSynthesisRequest,
    PersonaSynthesisResponse,
    SearchRequest,
    VerificationRequest,
    VerificationResponse,
)

from ..adapters._base import BaseHttpAdapter
from ..adapters._http import HttpRequest, HttpTransport
from ..adapters._retry import RetryPolicy
from ..binding import ModelBinding
from ..credentials import CredentialProvider
from ..types import InvalidRequestError, WireFormat


PURPOSE_SEARCH = "search"
PURPOSE_SYNTHESIS = "synthesis"
PURPOSE_VERIFICATION = "verification"

# Frozen provider tool prices from plan.md §2.2, USD per invocation.
SEARCH_TOOL_FEES_USD: Mapping[str, float] = {
    "openai": 0.010,
    "grok": 0.005,
    "google": 0.014,
    "kimi": 0.005,
}

_RESERVED_BODY_PARAMS = frozenset(
    {
        "include",
        "input",
        "messages",
        "model",
        "stream",
        "tool_choice",
        "tools",
        # TinyIC-only switch understood by the ordinary Kimi transport.
        "web_search",
    }
)
_MARKDOWN_CITATION_RE = re.compile(r"^\s*\[\[?\d+\]?\]\(https?://", re.I)


class ResearchResponseError(InvalidRequestError):
    """A provider returned a successful HTTP response with unusable JSON."""

    reason_code = "invalid_research_response"


@dataclass(frozen=True)
class UsageNumbers:
    """Normalized token counters before they become a factory ``CallUsage``."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reported: bool = False


@dataclass(frozen=True)
class Completion:
    """One logical backend operation, possibly spanning several HTTP rounds."""

    text: str
    usage: UsageNumbers
    calls: int = 1
    search_tool_calls: int = 0


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def usage_from_responses(raw: Any) -> UsageNumbers:
    """Normalize OpenAI/xAI Responses token usage."""

    if not isinstance(raw, Mapping):
        return UsageNumbers()
    details = raw.get("input_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, Mapping) else 0
    return UsageNumbers(
        _non_negative_int(raw.get("input_tokens")),
        _non_negative_int(raw.get("output_tokens")),
        _non_negative_int(cached),
        reported=True,
    )


def usage_from_chat(raw: Any) -> UsageNumbers:
    """Normalize OpenAI-compatible Chat Completions token usage."""

    if not isinstance(raw, Mapping):
        return UsageNumbers()
    details = raw.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, Mapping) else 0
    return UsageNumbers(
        _non_negative_int(raw.get("prompt_tokens")),
        _non_negative_int(raw.get("completion_tokens")),
        _non_negative_int(cached),
        reported=True,
    )


def usage_from_gemini(raw: Any) -> UsageNumbers:
    """Normalize Gemini Interactions usage (including legacy aliases)."""

    if not isinstance(raw, Mapping):
        return UsageNumbers()
    return UsageNumbers(
        _non_negative_int(
            raw.get("total_input_tokens", raw.get("promptTokenCount"))
        ),
        _non_negative_int(
            raw.get("total_output_tokens", raw.get("candidatesTokenCount"))
        ),
        _non_negative_int(
            raw.get("total_cached_tokens", raw.get("cachedContentTokenCount"))
        ),
        reported=True,
    )


def _clean_text(value: Any, *, limit: int = 4_000) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].strip()


def _safe_public_url(value: Any) -> str | None:
    """Return a safe public HTTP URL, or ``None`` for unusable citations."""

    if not isinstance(value, str):
        return None
    candidate = value.strip().rstrip(".,;:!?)]}")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port  # force validation of malformed ports
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    host = parsed.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return None
    del port
    return candidate


def _host_label(url: str) -> str:
    host = (urlsplit(url).hostname or "source").casefold().removeprefix("www.")
    return host or "source"


def _title(value: Any, url: str) -> str:
    title = _clean_text(value, limit=300)
    # xAI uses the visible citation number as annotation.title; it is not a
    # document title, so prefer the hostname in that case.
    return title if title and not title.isdigit() else _host_label(url)


def annotation_excerpt(text: str, annotation: Mapping[str, Any]) -> str:
    """Extract the cited span, falling back to the preceding prose sentence.

    Gemini annotations normally span the supported prose. OpenAI-family
    Responses may instead span the rendered citation marker itself; in that
    case the nearest preceding sentence is the useful evidence excerpt.
    """

    for field in ("snippet", "excerpt", "cited_text"):
        explicit = _clean_text(annotation.get(field))
        if explicit:
            return explicit
    start = annotation.get("start_index", annotation.get("startIndex"))
    end = annotation.get("end_index", annotation.get("endIndex"))
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
        segment = _clean_text(text[start:end])
        if segment and not segment.startswith(("http://", "https://")) and not (
            _MARKDOWN_CITATION_RE.match(segment)
        ):
            return segment
        prefix = text[:start].rstrip()
        boundary = max(
            prefix.rfind("\n"),
            prefix.rfind(". "),
            prefix.rfind("! "),
            prefix.rfind("? "),
        )
        contextual = _clean_text(prefix[boundary + 1 :])
        if contextual:
            return contextual
    return _clean_text(text)


class EvidenceCollector:
    """Insertion-ordered URL dedupe with provider-neutral evidence records."""

    def __init__(self, request: SearchRequest, provider: str) -> None:
        self._request = request
        self._provider = provider
        self._items: list[Evidence] = []
        self._by_url: dict[str, int] = {}
        self._seed_hosts = {
            (urlsplit(url).hostname or "").casefold().removeprefix("www.")
            for url in request.query.seed_urls
        }

    def add(
        self,
        url: Any,
        *,
        title: Any = "",
        excerpt: Any = "",
        fallback_text: str = "",
    ) -> None:
        clean_url = _safe_public_url(url)
        if clean_url is None:
            return
        clean_excerpt = _clean_text(excerpt) or _clean_text(fallback_text)
        if not clean_excerpt:
            return
        host = _host_label(clean_url)
        source_type = "primary" if host in self._seed_hosts else "secondary"
        item = Evidence(
            url=clean_url,
            title=_title(title, clean_url),
            excerpt=clean_excerpt,
            query=self._request.query.text,
            provider=self._provider,
            source_type=source_type,
        )
        index = self._by_url.get(clean_url)
        if index is None:
            self._by_url[clean_url] = len(self._items)
            self._items.append(item)
            return
        old = self._items[index]
        richer = item if len(item.excerpt) > len(old.excerpt) else old
        if old.title == _host_label(clean_url) and item.title != old.title:
            richer = Evidence(
                url=richer.url,
                title=item.title,
                excerpt=richer.excerpt,
                query=richer.query,
                provider=richer.provider,
                source_type=richer.source_type,
                accessed=richer.accessed,
            )
        self._items[index] = richer

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(self._items)


def search_prompt(request: SearchRequest, *, inline_urls: bool = False) -> str:
    """Build one provider-side search prompt; seed URLs are hints, never fetched."""

    lines = [
        "Research this investor using public-record sources only.",
        f"Investor: {request.investor_name}",
        f"Research query: {request.query.text}",
        "Return a concise evidence digest with source-specific factual support.",
    ]
    if request.query.seed_urls:
        lines.append("Prioritize these canonical source hints when relevant:")
        lines.extend(f"- {url}" for url in request.query.seed_urls)
    if inline_urls:
        lines.append(
            "Citations are not structured on this lane. Include the complete "
            "http(s) source URL in the final prose for every factual passage; "
            "an answer without inline URLs is unusable."
        )
    return "\n".join(lines)


def response_text_and_annotations(
    data: Mapping[str, Any],
) -> tuple[str, tuple[tuple[str, Mapping[str, Any]], ...]]:
    """Read Responses ``output_text`` blocks and retain block-local annotations."""

    text_parts: list[str] = []
    annotations: list[tuple[str, Mapping[str, Any]]] = []
    for item in data.get("output") or ():
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        for block in item.get("content") or ():
            if not isinstance(block, Mapping) or block.get("type") != "output_text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            text_parts.append(text)
            for annotation in block.get("annotations") or ():
                if isinstance(annotation, Mapping):
                    annotations.append((text, annotation))
    if not text_parts and isinstance(data.get("output_text"), str):
        text_parts.append(str(data["output_text"]))
    return "\n".join(text_parts).strip(), tuple(annotations)


def response_web_search_calls(data: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        item
        for item in (data.get("output") or ())
        if isinstance(item, Mapping) and item.get("type") == "web_search_call"
    )


def _json_object(text: str, *, provider: str) -> dict[str, Any]:
    """Decode one JSON object, accepting a model's harmless Markdown fence."""

    candidate = str(text or "").strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    start = candidate.find("{")
    if start >= 0:
        candidate = candidate[start:]
    try:
        decoded, _end = json.JSONDecoder().raw_decode(candidate)
    except (TypeError, ValueError) as exc:
        raise ResearchResponseError(
            f"{provider} returned non-JSON research output", provider=provider
        ) from exc
    if not isinstance(decoded, dict):
        raise ResearchResponseError(
            f"{provider} returned a non-object research result", provider=provider
        )
    return decoded


class BaseResearchBackend(BaseHttpAdapter):
    """Common implementation of the non-search ``ResearchBackend`` methods."""

    wire_format: ClassVar[WireFormat] = WireFormat.OPENAI_RESPONSES
    provider: ClassVar[str]
    credential_refs: ClassVar[tuple[str, ...]]
    endpoint_suffix: ClassVar[str]
    citation_quality: ClassVar[str] = "structured"
    degraded: ClassVar[bool] = False

    def __init__(
        self,
        binding: ModelBinding,
        credentials: CredentialProvider,
        *,
        base_url: str,
        http: HttpTransport | None = None,
        retry: RetryPolicy | None = None,
        sleep=None,
        timeout: float = 60.0,
    ) -> None:
        credential_ref = self._first_resolvable_ref(credentials) or self.credential_refs[0]
        super().__init__(
            binding,
            credentials,
            base_url=base_url,
            credential_ref=credential_ref,
            provider_name=self.provider,
            http=http,
            retry=retry,
            sleep=sleep,
            timeout=timeout,
        )
        self.binding = binding
        self.model_ref = binding.model_ref

    @classmethod
    def _first_resolvable_ref(
        cls, credentials: CredentialProvider
    ) -> str | None:
        for ref in cls.credential_refs:
            try:
                value = credentials(ref)
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                return ref
        return None

    def _body_params(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.binding.params.items()
            if key not in _RESERVED_BODY_PARAMS
        }

    def _post_json(self, body: Mapping[str, Any]) -> dict[str, Any]:
        request = HttpRequest(
            method="POST",
            url=self._endpoint(),
            headers=self._json_headers(),
            body=dict(body),
            stream=False,
            timeout=self._timeout,
        )
        response = self._send_with_retry(request)
        try:
            raw = response.read_text()
        finally:
            response.close()
        data = self._load_json(raw)
        if not isinstance(data, dict):
            raise ResearchResponseError(
                f"{self.provider} returned an invalid JSON response",
                provider=self.provider,
            )
        return data

    def _json_headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json", "accept": "application/json"}
        key = self._resolve_key()
        if key is not None:
            headers.update(self._auth_headers(key))
        return headers

    def _usage(
        self,
        numbers: UsageNumbers,
        *,
        purpose: str,
        calls: int = 1,
        search_tool_calls: int = 0,
    ) -> CallUsage:
        cost: float | None = None
        if numbers.reported:
            # Import lazily so research adapters do not make the ordinary model
            # package pay for pricing data until an operation completes.
            from tinyic.usage import (
                MODEL_PRICES_USD_PER_MILLION,
                estimate_model_keyed_cost,
            )

            cost = estimate_model_keyed_cost(
                {
                    self.model_ref: {
                        "input_tokens": numbers.input_tokens,
                        "output_tokens": numbers.output_tokens,
                    }
                },
                MODEL_PRICES_USD_PER_MILLION,
            )
            if cost is not None and search_tool_calls:
                cost += SEARCH_TOOL_FEES_USD[self.provider] * search_tool_calls
                cost = round(cost, 8)
        return CallUsage(
            purpose=purpose,
            model_ref=self.model_ref,
            input_tokens=numbers.input_tokens,
            output_tokens=numbers.output_tokens,
            cached_tokens=numbers.cached_tokens,
            cost_usd=cost,
            calls=calls,
        )

    # Provider modules implement this one method for their ordinary text lane.
    def _complete(self, prompt: str) -> Completion:  # pragma: no cover - abstract
        raise NotImplementedError

    def synthesize_dossier(
        self, request: DossierSynthesisRequest
    ) -> DossierSynthesisResponse:
        completion = self._complete(request.prompt)
        return DossierSynthesisResponse(
            completion.text,
            self._usage(
                completion.usage,
                purpose=PURPOSE_SYNTHESIS,
                calls=completion.calls,
            ),
        )

    def synthesize_persona(
        self, request: PersonaSynthesisRequest
    ) -> PersonaSynthesisResponse:
        source_records = json.dumps(
            list(request.source_records), ensure_ascii=False, separators=(",", ":")
        )
        generation = json.dumps(
            dict(request.generation), ensure_ascii=False, separators=(",", ":")
        )
        prompt = (
            request.prompt
            + "\n\nReturn JSON only (no Markdown fence). The top-level keys must "
            "be type, persona, and tinyic; type must be TinyPerson. Include all "
            "required persona fields and the complete tinyic schema. Copy these "
            f"factory-owned sources exactly: {source_records}. Copy this "
            f"generation object exactly: {generation}."
        )
        completion = self._complete(prompt)
        specification = _json_object(completion.text, provider=self.provider)
        return PersonaSynthesisResponse(
            specification,
            self._usage(
                completion.usage,
                purpose=PURPOSE_SYNTHESIS,
                calls=completion.calls,
            ),
        )

    def verify(self, request: VerificationRequest) -> VerificationResponse:
        prompt = (
            request.prompt
            + "\n\nReturn JSON only: an object whose keys are supported claim ids "
            "and whose values are arrays of 1-based source numbers. Do not "
            "include unsupported ids or any prose."
        )
        completion = self._complete(prompt)
        decoded = _json_object(completion.text, provider=self.provider)
        raw_supported = decoded.get("supported", decoded)
        supported: dict[str, tuple[int, ...]] = {}
        if isinstance(raw_supported, Mapping):
            for claim_id, raw_numbers in raw_supported.items():
                if not isinstance(raw_numbers, Sequence) or isinstance(
                    raw_numbers, (str, bytes, bytearray)
                ):
                    continue
                numbers = tuple(
                    dict.fromkeys(
                        number
                        for number in raw_numbers
                        if isinstance(number, int)
                        and not isinstance(number, bool)
                        and number > 0
                    )
                )
                if numbers:
                    supported[str(claim_id)] = numbers
        return VerificationResponse(
            supported,
            self._usage(
                completion.usage,
                purpose=PURPOSE_VERIFICATION,
                calls=completion.calls,
            ),
        )

    def close(self) -> None:
        self._http.close()

    # BaseHttpAdapter's Transport hooks are irrelevant on this sibling seam.
    def _build_body(self, request, *, include_thinking: bool):  # pragma: no cover
        raise NotImplementedError

    def _parse_stream(self, response, request):  # pragma: no cover
        raise NotImplementedError

    def _parse_response(self, response, request):  # pragma: no cover
        raise NotImplementedError


__all__ = [
    "BaseResearchBackend",
    "Completion",
    "EvidenceCollector",
    "PURPOSE_SEARCH",
    "PURPOSE_SYNTHESIS",
    "PURPOSE_VERIFICATION",
    "ResearchResponseError",
    "SEARCH_TOOL_FEES_USD",
    "UsageNumbers",
    "annotation_excerpt",
    "response_text_and_annotations",
    "response_web_search_calls",
    "search_prompt",
    "usage_from_chat",
    "usage_from_gemini",
    "usage_from_responses",
]
