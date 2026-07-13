"""The HTTP wire seam every provider adapter is built on (FR-1.2).

Adapters never import a provider SDK; they build a normalized JSON request and
hand it to an injected :class:`HttpTransport`.  In production that is
:class:`HttpxTransport` (a thin ``httpx`` wrapper, imported lazily so the model
layer has no hard httpx-at-import dependency).  In tests it is a fake that
replays recorded wire chunks — which is why the entire adapter suite runs with
**zero network**.

The response contract is deliberately tiny: a status code, case-insensitive
header access, a lazy line iterator for streaming bodies, and a full-body text
read for error/non-streaming bodies.  Transport-level faults (DNS, connect,
read timeout, reset) surface as :class:`HttpConnectionError`, which the retry
layer classifies as transient.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class HttpConnectionError(Exception):
    """A transport-level failure (connect/read/timeout/reset), pre-response.

    Adapters treat this as a transient, retryable fault; it never carries
    credential material.
    """


@dataclass(frozen=True)
class HttpRequest:
    """A normalized outbound HTTP request an adapter hands to a transport."""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Mapping[str, Any] | None = None
    stream: bool = False
    timeout: float = 60.0


@runtime_checkable
class HttpResponse(Protocol):
    """The minimal response surface adapters consume."""

    @property
    def status_code(self) -> int: ...

    def header(self, name: str) -> str | None:
        """Case-insensitive single-header lookup (e.g. ``Retry-After``)."""
        ...

    def iter_lines(self) -> Iterator[str]:
        """Yield decoded body lines (for streaming SSE bodies)."""
        ...

    def read_text(self) -> str:
        """Read and return the full body as text (error / non-streaming)."""
        ...

    def close(self) -> None:
        """Release the underlying connection/stream."""
        ...


@runtime_checkable
class HttpTransport(Protocol):
    """The injected wire transport an adapter sends requests through."""

    def send(self, request: HttpRequest) -> HttpResponse:
        """Send ``request``; raise :class:`HttpConnectionError` on wire faults."""
        ...

    def close(self) -> None: ...


class HttpxTransport:
    """Production :class:`HttpTransport` backed by ``httpx`` (lazy import).

    Never exercised by the offline suite (it would touch the network); adapters
    are unit-tested against fakes.  A pre-built ``client`` may be injected.
    """

    def __init__(self, *, timeout: float = 60.0, client: Any | None = None) -> None:
        self._timeout = timeout
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def send(self, request: HttpRequest) -> HttpResponse:
        import httpx

        client = self._get_client()
        try:
            if request.stream:
                manager = client.stream(
                    request.method,
                    request.url,
                    headers=dict(request.headers),
                    json=request.body,
                    timeout=request.timeout,
                )
                response = manager.__enter__()
                return _HttpxResponse(response, manager)
            response = client.request(
                request.method,
                request.url,
                headers=dict(request.headers),
                json=request.body,
                timeout=request.timeout,
            )
            return _HttpxResponse(response, None)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise HttpConnectionError(str(exc)) from exc

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


class _HttpxResponse:
    """Adapter-facing wrapper over an ``httpx.Response`` (± stream manager)."""

    def __init__(self, response: Any, manager: Any | None) -> None:
        self._response = response
        self._manager = manager

    @property
    def status_code(self) -> int:
        return int(self._response.status_code)

    def header(self, name: str) -> str | None:
        return self._response.headers.get(name)

    def iter_lines(self) -> Iterator[str]:
        try:
            yield from self._response.iter_lines()
        finally:
            self.close()

    def read_text(self) -> str:
        try:
            if self._manager is not None:
                self._response.read()
            return self._response.text
        finally:
            self.close()

    def close(self) -> None:
        if self._manager is not None:
            try:
                self._manager.__exit__(None, None, None)
            finally:
                self._manager = None


__all__ = [
    "HttpConnectionError",
    "HttpRequest",
    "HttpResponse",
    "HttpTransport",
    "HttpxTransport",
]
