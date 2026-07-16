"""Shared machinery for the four wire-format adapters (FR-1.2).

:class:`BaseHttpAdapter` implements everything the four adapters share so each
concrete adapter is just *wire shaping*: how to place messages and the thinking
parameter into the request body, and how to fold the provider's SSE/JSON frames
back into the normalized :class:`~tinyic.models.types` stream events.  The base
owns credential resolution (the M3-replaceable seam), the thinking-parameter
resolve/include/omit decision, bounded retry/backoff on transient and
rate-limit failures only, HTTP-status → error-taxonomy classification, and the
uniform terminal ``Usage`` + ``FinalMessage`` emission contract.

Normalized stream contract every adapter honors:

* streaming: zero or more ``ReasoningDelta``/``TextDelta`` in wire order, then —
  only if the provider reported usage — exactly one ``Usage``, then exactly one
  terminal ``FinalMessage`` (carrying the assembled text/reasoning/usage/finish
  reason);
* non-streaming: the same terminal pair (``Usage?`` then ``FinalMessage``) with
  no deltas.

One bad SSE frame never aborts a stream: JSON decoding is best-effort and
undecodable frames are skipped.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any, ClassVar

from ..binding import ModelBinding
from ..credentials import CredentialProvider
from ..thinking import ThinkingProfile
from ..types import (
    AuthError,
    ChatRequest,
    ChatStreamEvent,
    ErrorKind,
    FinalMessage,
    FinishReason,
    InvalidRequestError,
    ProviderError,
    RateLimitError,
    TransientError,
    Usage,
    UsageLimitError,
    WireFormat,
)
from ._http import (
    HttpConnectionError,
    HttpRequest,
    HttpResponse,
    HttpTransport,
    HttpxTransport,
)
from ._retry import (
    RetryPolicy,
    compute_backoff,
    error_kind_for_status,
    parse_retry_after,
)
from ._sse import SseEvent, iter_sse_events

_KIND_TO_EXCEPTION: dict[ErrorKind, type[ProviderError]] = {
    ErrorKind.AUTH: AuthError,
    ErrorKind.RATE_LIMIT: RateLimitError,
    ErrorKind.TRANSIENT: TransientError,
    ErrorKind.INVALID_REQUEST: InvalidRequestError,
}

_USAGE_LIMIT_CODES = frozenset(
    {
        "insufficient_quota",
        "billing_hard_limit_reached",
        "usage_limit_reached",
        "quota_exceeded",
        "credit_balance_too_low",
    }
)


class BaseHttpAdapter:
    """Base class implementing the :class:`~tinyic.models.types.Transport` seam."""

    #: The wire family this adapter speaks (set by each subclass).
    wire_format: ClassVar[WireFormat]
    #: Path appended to ``base_url`` to form the endpoint (set by each subclass).
    endpoint_suffix: ClassVar[str] = ""

    def __init__(
        self,
        binding: ModelBinding,
        credentials: CredentialProvider,
        *,
        base_url: str,
        credential_ref: str | None,
        thinking: ThinkingProfile | None = None,
        provider_name: str | None = None,
        http: HttpTransport | None = None,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] | None = None,
        thinking_runtime: bool = True,
        timeout: float = 60.0,
    ) -> None:
        self._binding = binding
        self._credentials = credentials
        self._base_url = base_url.rstrip("/")
        self._credential_ref = credential_ref
        self._thinking_profile = thinking
        self._thinking_runtime = thinking_runtime
        self._provider_name = provider_name or binding.provider
        self._http: HttpTransport = http if http is not None else HttpxTransport(
            timeout=timeout
        )
        self._retry = retry if retry is not None else RetryPolicy()
        self._sleep = sleep if sleep is not None else time.sleep
        self._timeout = timeout

    # -- the Transport seam -------------------------------------------------

    def generate(self, request: ChatRequest) -> Iterator[ChatStreamEvent]:
        """Execute ``request`` and yield the normalized stream (FR-1.2)."""
        body = self._build_body(request, include_thinking=True)
        try:
            response = self._send_with_retry(self._http_request(request, body))
        except InvalidRequestError as error:
            degraded = self._maybe_degrade(request, body, error)
            if degraded is None:
                raise
            response = self._send_with_retry(self._http_request(request, degraded))

        if request.stream:
            yield from self._parse_stream(response, request)
        else:
            yield from self._parse_response(response, request)

    # -- request assembly (overridable pieces) ------------------------------

    def _endpoint(self) -> str:
        return f"{self._base_url}{self.endpoint_suffix}"

    def _http_request(
        self, request: ChatRequest, body: Mapping[str, Any]
    ) -> HttpRequest:
        return HttpRequest(
            method="POST",
            url=self._endpoint(),
            headers=self._headers(request),
            body=body,
            stream=request.stream,
            timeout=self._timeout,
        )

    def _headers(self, request: ChatRequest) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "accept": "text/event-stream" if request.stream else "application/json",
        }
        headers.update(self._default_headers(self._resolve_key()))
        return headers

    def _default_headers(self, key: str | None) -> dict[str, str]:
        return dict(self._auth_headers(key)) if key is not None else {}

    def _auth_headers(self, key: str) -> dict[str, str]:
        return {"authorization": f"Bearer {key}"}

    def _resolve_key(self) -> str | None:
        """Resolve the provider credential, or raise a normalized AuthError.

        ``credential_ref is None`` marks an unauthenticated lane (local Ollama);
        otherwise a missing secret is a non-retryable ``AuthError`` whose message
        names the environment variable — never the (absent) secret itself.
        """
        if not self._credential_ref:
            return None
        key = self._credentials(self._credential_ref)
        if key is None:
            raise AuthError(
                f"missing credential: set the {self._credential_ref} "
                "environment variable",
                provider=self._provider_name,
            )
        return key

    # -- thinking parameter (include / omit / remap) ------------------------

    def _thinking_params(self, request: ChatRequest) -> dict[str, Any]:
        """The resolved thinking params for this request (``{}`` when omitted)."""
        if self._thinking_profile is None:
            return {}
        resolution = self._thinking_profile.resolve(
            request.binding.thinking_level, runtime=self._thinking_runtime
        )
        return dict(resolution.params)

    def _request_params(self, request: ChatRequest) -> dict[str, Any]:
        """Provider-agnostic extra knobs from the binding (temperature, …)."""
        return dict(request.binding.params)

    # -- overridable body / parsing seams -----------------------------------

    def _build_body(
        self, request: ChatRequest, *, include_thinking: bool
    ) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _parse_stream(
        self, response: HttpResponse, request: ChatRequest
    ) -> Iterator[ChatStreamEvent]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _parse_response(
        self, response: HttpResponse, request: ChatRequest
    ) -> Iterator[ChatStreamEvent]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _maybe_degrade(
        self, request: ChatRequest, body: dict[str, Any], error: InvalidRequestError
    ) -> dict[str, Any] | None:
        """Hook: return a repaired body to retry once, or ``None`` to re-raise.

        The base never degrades; :class:`OpenAICompatibleAdapter` overrides it to
        drop thinking params a permissive server rejected.
        """
        return None

    # -- retry / classification ---------------------------------------------

    def _send_with_retry(self, http_request: HttpRequest) -> HttpResponse:
        attempt = 0
        while True:
            error: ProviderError
            try:
                response = self._http.send(http_request)
            except HttpConnectionError as exc:
                error = TransientError(
                    f"connection error: {exc}", provider=self._provider_name
                )
            else:
                if response.status_code < 400:
                    return response
                error = self._classify_http_error(response)

            # An account/profile quota cannot recover by retrying the same
            # credential. Surface it immediately so the M3 auth-order wrapper
            # can select the next profile before any output is produced.
            if isinstance(error, UsageLimitError):
                raise error
            if error.retryable and attempt < self._retry.max_retries:
                self._sleep(
                    compute_backoff(attempt, self._retry, error.retry_after)
                )
                attempt += 1
                continue
            raise error

    def _classify_http_error(self, response: HttpResponse) -> ProviderError:
        status = response.status_code
        kind = error_kind_for_status(status)
        retry_after = (
            parse_retry_after(response.header("Retry-After"))
            if kind is ErrorKind.RATE_LIMIT
            else None
        )
        try:
            body_text = response.read_text()
        except Exception:  # pragma: no cover - defensive
            body_text = ""
        finally:
            response.close()
        exception_type: type[ProviderError]
        if self._is_usage_limit_response(body_text):
            exception_type = UsageLimitError
        else:
            exception_type = _KIND_TO_EXCEPTION[kind]
        error = exception_type(
            self._error_message(status, body_text),
            retry_after=retry_after,
            provider=self._provider_name,
        )
        # Attach the raw body so a graceful-degradation hook can inspect it.
        error.response_body = body_text  # type: ignore[attr-defined]
        return error

    @staticmethod
    def _is_usage_limit_response(body_text: str) -> bool:
        """Recognize structured account-quota codes, never free-form prose."""

        try:
            data = json.loads(body_text)
        except (TypeError, ValueError):
            return False
        if not isinstance(data, Mapping):
            return False
        error = data.get("error", data)
        if not isinstance(error, Mapping):
            return False
        for field in ("code", "type", "reason"):
            value = error.get(field)
            if not isinstance(value, str):
                continue
            normalized = value.strip().lower().replace("-", "_")
            if normalized in _USAGE_LIMIT_CODES:
                return True
        return False

    def _error_message(self, status: int, body_text: str) -> str:
        detail = self._extract_error_detail(body_text)
        base = f"{self._provider_name} request failed with HTTP {status}"
        return f"{base}: {detail}" if detail else base

    @staticmethod
    def _extract_error_detail(body_text: str) -> str:
        body_text = (body_text or "").strip()
        if not body_text:
            return ""
        try:
            data = json.loads(body_text)
        except (ValueError, TypeError):
            return body_text[:500]
        if isinstance(data, Mapping):
            err = data.get("error", data)
            if isinstance(err, Mapping):
                return str(err.get("message") or err.get("type") or err)[:500]
            return str(err)[:500]
        return str(data)[:500]

    # -- shared parsing helpers ---------------------------------------------

    def _sse_events(self, response: HttpResponse) -> Iterator[SseEvent]:
        try:
            yield from iter_sse_events(response.iter_lines())
        finally:
            response.close()

    @staticmethod
    def _load_json(data: str) -> Any | None:
        """Best-effort JSON decode; ``None`` on a malformed frame (skip it)."""
        try:
            return json.loads(data)
        except (ValueError, TypeError):
            return None

    def _emit_final(
        self,
        *,
        text: str,
        reasoning: str,
        usage: Usage | None,
        finish_reason: FinishReason,
    ) -> Iterator[ChatStreamEvent]:
        if usage is not None:
            yield usage
        yield FinalMessage(
            text=text, reasoning=reasoning, usage=usage, finish_reason=finish_reason
        )


__all__ = ["BaseHttpAdapter"]
