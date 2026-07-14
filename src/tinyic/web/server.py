"""Stdlib localhost HTTP/SSE server for TinyIC's web debate face.

The server is deliberately an adapter over an append-only JSONL path.  It has
no engine imports: a live caller injects the engine's steering inbox and control
object, while replay injects neither.  Every SSE connection independently reads
the log, so reconnect/resume and recorded replay share exactly one source of
truth.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, TextIO, cast
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .security import (
    CACHE_CONTROL,
    CAPABILITY_COOKIE,
    CSP_POLICY,
    AuthStatus,
    SecurityPolicy,
    generate_capability_token,
    is_json_content_type,
)

__all__ = ["WebFace", "WebServer"]

_TERMINAL_TYPES = frozenset({"debate_completed", "debate_error"})
_CONTROL_TYPES = frozenset({"pause", "resume", "next_phase", "stop"})
_MAX_JSON_BODY = 64 * 1024
_MIME_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}

AssetLoader = Callable[[str], tuple[bytes, str] | bytes | None]
Renderer = Callable[[list[Any]], str]


@dataclass(frozen=True)
class _LogRecord:
    seq: int
    type: str
    raw: str
    envelope: Mapping[str, Any]


@dataclass(frozen=True)
class _LogSnapshot:
    records: tuple[_LogRecord, ...]
    terminal_type: str | None

    @property
    def seq_high(self) -> int:
        return max((record.seq for record in self.records), default=0)


class _TinyICHTTPServer(ThreadingHTTPServer):
    """Thread-per-request server carrying a reference to its owning face."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, owner: "WebServer") -> None:
        self.owner = owner
        super().__init__(address, handler)


class _RequestHandler(BaseHTTPRequestHandler):
    """One authenticated HTTP request; all state belongs to ``WebServer``."""

    protocol_version = "HTTP/1.1"
    server_version = "TinyIC"
    sys_version = ""

    @property
    def web(self) -> "WebServer":
        return cast(_TinyICHTTPServer, self.server).owner

    def log_message(self, _format: str, *args: object) -> None:
        # BaseHTTPRequestHandler logs the full request target.  The first target
        # contains the capability token, so never hand it to the default logger.
        return

    def end_headers(self) -> None:
        # Centralizing these here covers redirects, errors, 204s, and even the
        # BaseHTTPRequestHandler fallback responses for unsupported methods.
        self.send_header("Cache-Control", CACHE_CONTROL)
        self.send_header("Content-Security-Policy", CSP_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    # -- method dispatch ------------------------------------------------- #

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._request_security_ok():
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)

        if path == "/" and self._token_handoff(parsed.query):
            return
        if not self._authenticated():
            return

        if path == "/":
            self._serve_asset("index.html")
        elif path.startswith("/assets/"):
            self._serve_asset_path(path.removeprefix("/assets/"))
        elif path == "/events":
            self._serve_events(parsed.query)
        elif path == "/api/meta":
            self._send_json(HTTPStatus.OK, self.web.meta())
        elif path == "/api/result":
            self._serve_result(parsed.query)
        else:
            self._problem(HTTPStatus.NOT_FOUND, "not_found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._request_security_ok():
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if not self._authenticated():
            return
        if path not in {"/api/steering", "/api/control"}:
            self._problem(HTTPStatus.NOT_FOUND, "not_found")
            return

        content_types = self.headers.get_all("Content-Type") or []
        if len(content_types) != 1 or not is_json_content_type(content_types[0]):
            self._problem(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "json_required")
            return
        body = self._read_json_body()
        if body is None:
            return
        if self.web.is_replay:
            self._problem(HTTPStatus.CONFLICT, "replay_read_only")
            return
        if path == "/api/steering":
            self._post_steering(body)
        else:
            self._post_control(body)

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
        # TinyIC is same-origin only and emits no CORS permission.  Still apply
        # Host/Origin validation so a hostile preflight receives a clean 403.
        if not self._request_security_ok():
            return
        self._problem(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed")

    # -- security -------------------------------------------------------- #

    def _request_security_ok(self) -> bool:
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or not self.web.security.valid_host(hosts[0]):
            self._problem(HTTPStatus.FORBIDDEN, "invalid_host")
            return False
        origins = self.headers.get_all("Origin") or []
        if len(origins) > 1 or (
            origins and not self.web.security.valid_origin(origins[0])
        ):
            self._problem(HTTPStatus.FORBIDDEN, "invalid_origin")
            return False
        return True

    def _token_handoff(self, query: str) -> bool:
        parameters = parse_qs(query, keep_blank_values=True)
        if "token" not in parameters:
            return False
        supplied = parameters["token"]
        if len(supplied) != 1 or not self.web.security.matches_token(supplied[0]):
            self._problem(HTTPStatus.FORBIDDEN, "invalid_token")
            return True
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{CAPABILITY_COOKIE}={self.web.token}; HttpOnly; SameSite=Strict; Path=/",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _authenticated(self) -> bool:
        cookie_headers = self.headers.get_all("Cookie") or []
        auth_headers = self.headers.get_all("Authorization") or []
        if len(auth_headers) > 1:
            status = AuthStatus.INVALID
        else:
            status = self.web.security.authenticate(
                cookie_header="; ".join(cookie_headers) if cookie_headers else None,
                authorization=auth_headers[0] if auth_headers else None,
            )
        if status is AuthStatus.OK:
            return True
        code = (
            HTTPStatus.UNAUTHORIZED
            if status is AuthStatus.MISSING
            else HTTPStatus.FORBIDDEN
        )
        headers = {"WWW-Authenticate": 'Bearer realm="tinyic"'} if code == 401 else None
        self._problem(code, "authentication_required", headers=headers)
        return False

    # -- GET endpoints --------------------------------------------------- #

    def _serve_asset_path(self, name: str) -> None:
        normalized = PurePosixPath(name)
        parts = normalized.parts
        if (
            not name
            or name.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or "\\" in name
            or any(ord(character) < 32 for character in name)
        ):
            self._problem(HTTPStatus.NOT_FOUND, "not_found")
            return
        self._serve_asset("/".join(parts))

    def _serve_asset(self, name: str) -> None:
        loaded = self.web.load_asset(name)
        if loaded is None:
            self._problem(HTTPStatus.NOT_FOUND, "not_found")
            return
        content, content_type = loaded
        self._send_bytes(HTTPStatus.OK, content, content_type=content_type)

    def _resume_seq(self, query: str) -> int | None:
        last_ids = self.headers.get_all("Last-Event-ID") or []
        raw: str | None
        if last_ids:
            if len(last_ids) != 1:
                self._problem(HTTPStatus.BAD_REQUEST, "invalid_resume_sequence")
                return None
            raw = last_ids[0].strip()
        else:
            parameters = parse_qs(query, keep_blank_values=True)
            values = parameters.get("from_seq", ["0"])
            if len(values) != 1:
                self._problem(HTTPStatus.BAD_REQUEST, "invalid_resume_sequence")
                return None
            raw = values[0].strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_resume_sequence")
            return None
        if value < 0:
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_resume_sequence")
            return None
        return value

    def _serve_events(self, query: str) -> None:
        resume_seq = self._resume_seq(query)
        if resume_seq is None:
            return
        initial = self.web.snapshot()
        pending = [record for record in initial.records if record.seq > resume_seq]
        if self.web.snapshot_is_sealed(initial) and not pending:
            self._send_bytes(HTTPStatus.NO_CONTENT, b"", content_type=None)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.web._sse_connected()
        cursor = resume_seq
        last_write = time.monotonic()
        try:
            if not self._write_sse(b"retry: 1500\n\n"):
                return
            last_write = time.monotonic()
            while not self.web.closing:
                snapshot = self.web.snapshot()
                for record in snapshot.records:
                    if record.seq <= cursor:
                        continue
                    frame = f"id:{record.seq}\ndata:{record.raw}\n\n".encode("utf-8")
                    if not self._write_sse(frame):
                        return
                    cursor = record.seq
                    last_write = time.monotonic()
                    if record.type in _TERMINAL_TYPES:
                        return

                if self.web.snapshot_is_sealed(snapshot) and cursor >= snapshot.seq_high:
                    return

                now = time.monotonic()
                if now - last_write >= self.web.heartbeat_interval:
                    if not self._write_sse(b": ping\n\n"):
                        return
                    last_write = time.monotonic()
                wait_for = min(
                    self.web.poll_interval,
                    max(0.001, self.web.heartbeat_interval - (time.monotonic() - last_write)),
                )
                self.web.wait_for_change(wait_for)
        finally:
            self.web._sse_disconnected()

    def _write_sse(self, data: bytes) -> bool:
        try:
            self.wfile.write(data)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _serve_result(self, query: str) -> None:
        parameters = parse_qs(query, keep_blank_values=True)
        values = parameters.get("fmt", ["html"])
        if len(values) != 1 or values[0] not in {"html", "md"}:
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_result_format")
            return
        fmt = values[0]
        try:
            rendered = self.web.render_result(fmt)
        except Exception:
            self.web.diagnostic("could not render the current result")
            self._problem(HTTPStatus.INTERNAL_SERVER_ERROR, "render_failed")
            return
        suffix = "html" if fmt == "html" else "md"
        content_type = (
            "text/html; charset=utf-8"
            if fmt == "html"
            else "text/markdown; charset=utf-8"
        )
        filename = self.web.download_stem + "." + suffix
        self._send_bytes(
            HTTPStatus.OK,
            rendered.encode("utf-8"),
            content_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # -- POST endpoints -------------------------------------------------- #

    def _read_json_body(self) -> dict[str, Any] | None:
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1:
            self._problem(HTTPStatus.LENGTH_REQUIRED, "content_length_required")
            return None
        try:
            length = int(lengths[0])
        except (TypeError, ValueError):
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_content_length")
            return None
        if length < 0:
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_content_length")
            return None
        if length > _MAX_JSON_BODY:
            self._problem(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large")
            return None
        raw = self.rfile.read(length)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_json")
            return None
        if not isinstance(decoded, dict):
            self._problem(HTTPStatus.BAD_REQUEST, "json_object_required")
            return None
        return decoded

    def _post_steering(self, body: dict[str, Any]) -> None:
        mode = body.get("type")
        if mode not in {"steer", "queue", "interrupt"}:
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_steering_type")
            return
        target = body.get("target")
        text = body.get("text")
        if target is not None and (not isinstance(target, str) or not target.strip()):
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_target")
            return
        if text is not None and not isinstance(text, str):
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_text")
            return
        if mode in {"steer", "queue"} and (
            not isinstance(text, str) or not text.strip()
        ):
            self._problem(HTTPStatus.BAD_REQUEST, "text_required")
            return
        inbox = self.web.inbox
        if inbox is None:
            self._problem(HTTPStatus.SERVICE_UNAVAILABLE, "steering_unavailable")
            return
        try:
            if mode == "interrupt":
                accepted = inbox.request_interrupt(
                    text=(text or None), target=(target or None), source="api"
                )
                response: dict[str, Any] = {"accepted": bool(accepted), "type": mode}
            else:
                msg_id = inbox.submit(
                    mode,
                    cast(str, text),
                    target=(target or None),
                    source="api",
                )
                accepted = msg_id is not None
                response = {"accepted": accepted, "type": mode, "msg_id": msg_id}
        except Exception:
            self.web.diagnostic("steering endpoint could not reach the inbox")
            self._problem(HTTPStatus.SERVICE_UNAVAILABLE, "steering_unavailable")
            return
        if not accepted:
            self._problem(HTTPStatus.CONFLICT, "steering_closed")
            return
        self._send_json(HTTPStatus.ACCEPTED, response)

    def _post_control(self, body: dict[str, Any]) -> None:
        action = body.get("type")
        if action not in _CONTROL_TYPES:
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_control_type")
            return
        control = self.web.control
        callback = getattr(control, cast(str, action), None) if control is not None else None
        if not callable(callback):
            self._problem(HTTPStatus.SERVICE_UNAVAILABLE, "control_unavailable")
            return
        try:
            outcome = callback()
        except Exception:
            self.web.diagnostic(f"control endpoint could not apply {action}")
            self._problem(HTTPStatus.SERVICE_UNAVAILABLE, "control_unavailable")
            return
        if outcome is False:
            self._problem(HTTPStatus.CONFLICT, "control_rejected")
            return
        self._send_json(HTTPStatus.ACCEPTED, {"accepted": True, "type": action})

    # -- response helpers ------------------------------------------------ #

    def _problem(
        self,
        status: HTTPStatus | int,
        reason: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._send_json(int(status), {"error": reason}, headers=headers)

    def _send_json(
        self,
        status: HTTPStatus | int,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self._send_bytes(
            status,
            encoded,
            content_type="application/json; charset=utf-8",
            headers=headers,
        )

    def _send_bytes(
        self,
        status: HTTPStatus | int,
        content: bytes,
        *,
        content_type: str | None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(int(status))
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        # 204 responses cannot carry a body even if a caller supplied one.
        body = b"" if int(status) == HTTPStatus.NO_CONTENT else content
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
            self.wfile.flush()


class WebServer:
    """A background ``ThreadingHTTPServer`` bound exclusively to 127.0.0.1.

    ``log_path`` may be a path or a log-like object exposing ``.path``.  All
    mutable dependencies are injected: ``inbox`` receives steer/queue/interrupt,
    ``control`` receives lifecycle commands, and ``browser_opener`` is the only
    desktop side effect.  Call :meth:`mark_run_finished` if a live worker exits
    without a terminal event, allowing SSE clients to stop cleanly.
    """

    def __init__(
        self,
        log_path: str | Path | object,
        *,
        inbox: object | None = None,
        control: object | None = None,
        replay: bool = False,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
        ticker: str | None = None,
        browser_opener: Callable[[str], object] | None = None,
        open_browser: bool = True,
        asset_loader: AssetLoader | None = None,
        html_renderer: Renderer | None = None,
        markdown_renderer: Renderer | None = None,
        stderr: TextIO | None = None,
        poll_interval: float = 0.1,
        heartbeat_interval: float = 15.0,
        run_done: threading.Event | None = None,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("TinyIC web server must bind exactly to 127.0.0.1")
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer between 0 and 65535")
        candidate = getattr(log_path, "path", log_path)
        self.log_path = Path(cast(str | Path, candidate)).expanduser()
        self.inbox = inbox
        self.control = control
        self.is_replay = bool(replay)
        self.host = host
        self.requested_port = port
        self._token = token or generate_capability_token()
        self._ticker_hint = ticker or ""
        self._run_id_hint = str(getattr(log_path, "debate_id", "") or self.log_path.stem)
        self._browser_opener = browser_opener
        self._open_browser = bool(open_browser)
        self._asset_loader = asset_loader
        self._html_renderer = html_renderer
        self._markdown_renderer = markdown_renderer
        self._stderr = stderr if stderr is not None else sys.stderr
        self.poll_interval = max(0.001, float(poll_interval))
        self.heartbeat_interval = max(0.001, float(heartbeat_interval))
        self._run_done = run_done or threading.Event()
        self._closing = threading.Event()
        self._stopped = threading.Event()
        self._httpd: _TinyICHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._security: SecurityPolicy | None = None
        self._lifecycle_lock = threading.RLock()
        self._sse_condition = threading.Condition()
        self._active_sse = 0

    # -- public lifecycle ------------------------------------------------ #

    def start(self) -> "WebServer":
        """Bind, start the background server, print/open the capability URL."""
        with self._lifecycle_lock:
            if self._closing.is_set():
                raise RuntimeError("web server has already been shut down")
            if self._httpd is not None:
                return self
            httpd = _TinyICHTTPServer(
                (self.host, self.requested_port), _RequestHandler, self
            )
            self._httpd = httpd
            self._security = SecurityPolicy(port=self.port, token=self._token)
            self._thread = threading.Thread(
                target=self._serve,
                name="tinyic-web-server",
                daemon=True,
            )
            self._thread.start()
        self.diagnostic(f"TinyIC viewer: {self.launch_url}")
        if self._open_browser:
            opener = self._browser_opener
            if opener is None:
                from tinyic.browser import open_browser

                opener = open_browser
            try:
                opener(self.launch_url)
            except Exception:
                # The URL remains on STDERR; browser failure is never fatal.
                pass
        return self

    def _serve(self) -> None:
        assert self._httpd is not None
        try:
            self._httpd.serve_forever(poll_interval=0.05)
        finally:
            self._stopped.set()

    def shutdown(self, *, timeout: float = 1.0) -> None:
        """Stop accepting requests and give active handlers a short drain."""
        self._closing.set()
        with self._sse_condition:
            self._sse_condition.notify_all()
        httpd = self._httpd
        thread = self._thread
        if httpd is None:
            self._stopped.set()
            return
        httpd.shutdown()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        self.wait_for_sse_disconnect(timeout=max(0.0, timeout))
        httpd.server_close()
        self._stopped.set()

    close = shutdown

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for server shutdown; return whether it stopped in time."""
        return self._stopped.wait(timeout)

    def mark_run_finished(self) -> None:
        """Seal a live log when its worker is done, even if it was truncated."""
        self._run_done.set()
        with self._sse_condition:
            self._sse_condition.notify_all()

    def wait_for_sse_disconnect(self, timeout: float | None = None) -> bool:
        """Wait until every current SSE client has disconnected."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._sse_condition:
            while self._active_sse:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._sse_condition.wait(remaining)
            return True

    def __enter__(self) -> "WebServer":
        return self.start()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.shutdown()

    # -- public state ---------------------------------------------------- #

    @property
    def port(self) -> int:
        if self._httpd is None:
            return self.requested_port
        return int(self._httpd.server_address[1])

    @property
    def token(self) -> str:
        return self._token

    @property
    def security(self) -> SecurityPolicy:
        if self._security is None:
            raise RuntimeError("web server has not been started")
        return self._security

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("web server has not been started")
        return f"http://127.0.0.1:{self.port}"

    @property
    def address(self) -> tuple[str, int]:
        """The concrete loopback address after binding (including port 0)."""
        if self._httpd is None:
            raise RuntimeError("web server has not been started")
        return self.host, self.port

    @property
    def launch_url(self) -> str:
        return f"{self.base_url}/?token={quote(self.token, safe='')}"

    @property
    def url(self) -> str:
        """Compatibility shorthand for the token-bearing browser launch URL."""
        return self.launch_url

    @property
    def closing(self) -> bool:
        return self._closing.is_set()

    @property
    def active_sse(self) -> int:
        with self._sse_condition:
            return self._active_sse

    @property
    def download_stem(self) -> str:
        value = str(self.meta().get("run_id") or "tinyic-debate")
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
        return (cleaned or "tinyic-debate")[:100]

    # -- request-facing seams ------------------------------------------- #

    def snapshot(self) -> _LogSnapshot:
        """Read only complete UTF-8 JSONL lines from the current log snapshot."""
        try:
            data = self.log_path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, OSError):
            return _LogSnapshot((), None)
        pieces = data.split(b"\n")
        if data and not data.endswith(b"\n"):
            pieces.pop()  # concurrent writer's partial trailing line
        records: list[_LogRecord] = []
        for raw_bytes in pieces:
            if not raw_bytes.strip():
                continue
            try:
                raw = raw_bytes.decode("utf-8")
                envelope = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                continue
            if not isinstance(envelope, dict):
                continue
            seq = envelope.get("seq")
            event_type = envelope.get("type")
            if (
                not isinstance(seq, int)
                or isinstance(seq, bool)
                or not isinstance(event_type, str)
                or not event_type
            ):
                continue
            records.append(_LogRecord(seq, event_type, raw, envelope))
        terminal = records[-1].type if records and records[-1].type in _TERMINAL_TYPES else None
        return _LogSnapshot(tuple(records), terminal)

    def snapshot_is_sealed(self, snapshot: _LogSnapshot) -> bool:
        return bool(snapshot.terminal_type) or self.is_replay or self._run_done.is_set()

    def meta(self) -> dict[str, object]:
        snapshot = self.snapshot()
        run_id = self._run_id_hint
        ticker = self._ticker_hint
        for record in snapshot.records:
            candidate_id = record.envelope.get("debate_id")
            if isinstance(candidate_id, str) and candidate_id:
                run_id = candidate_id
            if record.type == "debate_started":
                payload = record.envelope.get("payload")
                if isinstance(payload, dict):
                    candidate_ticker = payload.get("ticker")
                    if isinstance(candidate_ticker, str):
                        ticker = candidate_ticker
                break
        if self.is_replay:
            status = "replay"
        elif snapshot.terminal_type == "debate_completed":
            status = "completed"
        elif snapshot.terminal_type == "debate_error" or self._run_done.is_set():
            status = "error"
        else:
            status = "live"
        return {
            "run_id": run_id,
            "ticker": ticker,
            "status": status,
            "seq_high": snapshot.seq_high,
            "replay": self.is_replay,
        }

    def load_asset(self, name: str) -> tuple[bytes, str] | None:
        loader = self._asset_loader
        try:
            loaded = loader(name) if loader is not None else self._load_packaged_asset(name)
        except (FileNotFoundError, IsADirectoryError, OSError):
            return None
        if loaded is None:
            return None
        if isinstance(loaded, tuple):
            content, content_type = loaded
        else:
            content = loaded
            content_type = _MIME_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")
        return bytes(content), content_type

    @staticmethod
    def _load_packaged_asset(name: str) -> bytes | None:
        target = resources.files("tinyic.web").joinpath("assets", *name.split("/"))
        return target.read_bytes() if target.is_file() else None

    def render_result(self, fmt: str) -> str:
        snapshot = self.snapshot()
        from tinyic.tui.events import read_events

        events = read_events(record.raw for record in snapshot.records)
        if fmt == "html":
            renderer = self._html_renderer
            if renderer is None:
                from tinyic.report import render_html

                renderer = render_html
        else:
            renderer = self._markdown_renderer
            if renderer is None:
                from tinyic.report import render_markdown

                renderer = render_markdown
        return renderer(events)

    def wait_for_change(self, timeout: float) -> None:
        with self._sse_condition:
            self._sse_condition.wait(timeout)

    def _sse_connected(self) -> None:
        with self._sse_condition:
            self._active_sse += 1
            self._sse_condition.notify_all()

    def _sse_disconnected(self) -> None:
        with self._sse_condition:
            self._active_sse = max(0, self._active_sse - 1)
            self._sse_condition.notify_all()

    def diagnostic(self, message: str) -> None:
        try:
            self._stderr.write(message.rstrip() + "\n")
            self._stderr.flush()
        except Exception:  # pragma: no cover - diagnostics are best effort
            pass


class WebFace(WebServer):
    """Naming adapter used by debate/replay entrypoints."""

    @classmethod
    def live(
        cls,
        log_path: str | Path | object,
        *,
        inbox: object,
        control: object | None = None,
        **kwargs: object,
    ) -> "WebFace":
        return cls(log_path, inbox=inbox, control=control, replay=False, **kwargs)

    @classmethod
    def replay(
        cls,
        log_path: str | Path | object,
        **kwargs: object,
    ) -> "WebFace":
        return cls(log_path, replay=True, **kwargs)
