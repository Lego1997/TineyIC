"""Persistent localhost persona studio: library, editor, research, committee.

The studio reuses the debate viewer's capability-security stack
(:mod:`tinyic.web.security`) but owns a separate, studio-local JSON API. It
never touches the debate event log and adds no debate event types; research
progress is a studio-local SSE channel. All state-changing routes require
``application/json`` bodies up to 256 KB (dossiers exceed the debate viewer's
64 KB cap).
"""

from __future__ import annotations

import json
import re
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, TextIO, cast
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .security import (
    CACHE_CONTROL,
    CSP_POLICY,
    AuthStatus,
    SecurityPolicy,
    generate_capability_token,
    is_json_content_type,
)

__all__ = ["MAX_BODY_BYTES", "StudioServer"]

MAX_BODY_BYTES = 262_144
_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class _StudioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, owner) -> None:
        self.owner = owner
        super().__init__(address, handler)


class _StudioHandler(BaseHTTPRequestHandler):
    """One authenticated studio request; all state belongs to ``StudioServer``."""

    protocol_version = "HTTP/1.1"
    server_version = "TinyIC"
    sys_version = ""

    @property
    def studio(self) -> "StudioServer":
        return cast(_StudioHTTPServer, self.server).owner

    def log_message(self, _format: str, *args: object) -> None:
        # The first request target carries the capability token: never log it.
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", CACHE_CONTROL)
        self.send_header("Content-Security-Policy", CSP_POLICY)
        self.send_header("X-Frame-Options", "DENY")
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
            self._serve_asset("studio.html")
        elif path.startswith("/assets/"):
            self._serve_asset_path(path.removeprefix("/assets/"))
        elif path == "/api/personas":
            self._get_personas()
        elif path.startswith("/api/personas/"):
            self._get_persona(path.removeprefix("/api/personas/"))
        else:
            self._problem(HTTPStatus.NOT_FOUND, "not_found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        body = self._mutation_start()
        if body is None:
            return
        self._problem(HTTPStatus.NOT_FOUND, "not_found")

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler API
        body = self._mutation_start()
        if body is None:
            return
        self._problem(HTTPStatus.NOT_FOUND, "not_found")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._request_security_ok():
            return
        if not self._authenticated():
            return
        self._problem(HTTPStatus.NOT_FOUND, "not_found")

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._request_security_ok():
            return
        self._problem(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed")

    def _mutation_start(self) -> dict[str, Any] | None:
        """Shared security + JSON gate for POST/PUT; returns the parsed object."""
        if not self._request_security_ok():
            return None
        if not self._authenticated():
            return None
        content_types = self.headers.get_all("Content-Type") or []
        if len(content_types) != 1 or not is_json_content_type(content_types[0]):
            self._problem(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "json_required")
            return None
        return self._read_json_body()

    # -- security (mirrors tinyic.web.server) ---------------------------- #

    def _request_security_ok(self) -> bool:
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or not self.studio.security.valid_host(hosts[0]):
            self._problem(HTTPStatus.FORBIDDEN, "invalid_host")
            return False
        origins = self.headers.get_all("Origin") or []
        if len(origins) > 1 or (
            origins and not self.studio.security.valid_origin(origins[0])
        ):
            self._problem(HTTPStatus.FORBIDDEN, "invalid_origin")
            return False
        return True

    def _token_handoff(self, query: str) -> bool:
        parameters = parse_qs(query, keep_blank_values=True)
        if "token" not in parameters:
            return False
        supplied = parameters["token"]
        if len(supplied) != 1 or not self.studio.security.matches_token(supplied[0]):
            self._problem(HTTPStatus.FORBIDDEN, "invalid_token")
            return True
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{self.studio.security.cookie_name}={self.studio.token}; "
            "HttpOnly; SameSite=Strict; Path=/",
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
            status = self.studio.security.authenticate(
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

    # -- assets & body ---------------------------------------------------- #

    def _get_personas(self) -> None:
        from tinyic.personas import registry as registry_module
        from tinyic.personas.summary import registry_summaries

        try:
            summaries = registry_summaries(registry_module)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._problem(HTTPStatus.INTERNAL_SERVER_ERROR, "persona_read_error")
            self.studio.diagnostic(f"studio: persona list failed: {exc}")
            return
        self._send_json(
            HTTPStatus.OK, {"schema_version": 1, "personas": summaries}
        )

    def _get_persona(self, slug: str) -> None:
        if not SLUG_RE.fullmatch(slug):
            self._problem(HTTPStatus.NOT_FOUND, "unknown_persona")
            return
        from tinyic.personas import registry as registry_module

        snapshot = registry_module.registry_snapshot(warn=False)
        path = snapshot.get(slug)
        if path is None:
            self._problem(HTTPStatus.NOT_FOUND, "unknown_persona")
            return
        try:
            agent = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            self._problem(HTTPStatus.INTERNAL_SERVER_ERROR, "persona_read_error")
            return
        dossier_path = path.with_name(f"{slug}.dossier.md")
        dossier: str | None = None
        try:
            if dossier_path.is_file():
                dossier = dossier_path.read_text(encoding="utf-8")
        except OSError:
            dossier = None
        origin = "built_in" if slug in registry_module.BUILTIN_PERSONAS else "user"
        self._send_json(
            HTTPStatus.OK,
            {"slug": slug, "origin": origin, "agent": agent, "dossier": dossier},
        )

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
        loaded = self.studio.load_asset(name)
        if loaded is None:
            self._problem(HTTPStatus.NOT_FOUND, "not_found")
            return
        content, content_type = loaded
        self._send_bytes(HTTPStatus.OK, content, content_type=content_type)

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._problem(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_too_large")
            return None
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_json")
            return None
        if not isinstance(value, dict):
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_json")
            return None
        return value

    # -- response helpers (mirrors tinyic.web.server) --------------------- #

    def _problem(self, status, reason, *, headers=None) -> None:
        self._send_json(int(status), {"error": reason}, headers=headers)

    def _send_json(self, status, payload, *, headers=None) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(
            status, encoded,
            content_type="application/json; charset=utf-8", headers=headers,
        )

    def _send_bytes(self, status, content, *, content_type=None, headers=None) -> None:
        self.send_response(int(status))
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        body = b"" if int(status) == HTTPStatus.NO_CONTENT else content
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
            self.wfile.flush()


class StudioServer:
    """A background ``ThreadingHTTPServer`` for the persona studio."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
        browser_opener=None,
        open_browser: bool = True,
        asset_loader=None,
        stderr: TextIO | None = None,
        jobs=None,
        heartbeat_interval: float = 15.0,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("TinyIC studio must bind exactly to 127.0.0.1")
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer between 0 and 65535")
        self.host = host
        self.requested_port = port
        self._token = token or generate_capability_token()
        self._browser_opener = browser_opener
        self._open_browser = bool(open_browser)
        self._asset_loader = asset_loader
        self._stderr = stderr if stderr is not None else sys.stderr
        self.jobs = jobs  # research.ResearchJobs; defaults once research lands
        self.heartbeat_interval = max(0.001, float(heartbeat_interval))
        self._httpd: _StudioHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._security: SecurityPolicy | None = None
        self._closing = threading.Event()
        self._stopped = threading.Event()
        self._lifecycle_lock = threading.RLock()

    def start(self) -> "StudioServer":
        """Bind, start the background server, print/open the capability URL."""
        with self._lifecycle_lock:
            if self._closing.is_set():
                raise RuntimeError("studio server has already been shut down")
            if self._httpd is not None:
                return self
            self._httpd = _StudioHTTPServer(
                (self.host, self.requested_port), _StudioHandler, self
            )
            self._security = SecurityPolicy(port=self.port, token=self._token)
            self._thread = threading.Thread(
                target=self._serve, name="tinyic-studio-server", daemon=True
            )
            self._thread.start()
        self.diagnostic(f"TinyIC studio: {self.launch_url}")
        if self._open_browser:
            opener = self._browser_opener
            if opener is None:
                from tinyic.browser import open_browser

                opener = open_browser
            try:
                opener(self.launch_url)
            except Exception:
                pass  # The URL remains on STDERR; browser failure is never fatal.
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
        httpd, thread = self._httpd, self._thread
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for server shutdown; return whether it stopped in time."""
        return self._stopped.wait(timeout)

    @property
    def port(self) -> int:
        assert self._httpd is not None
        return int(self._httpd.server_address[1])

    @property
    def token(self) -> str:
        return self._token

    @property
    def security(self) -> SecurityPolicy:
        assert self._security is not None
        return self._security

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def launch_url(self) -> str:
        return f"{self.base_url}/?token={quote(self.token, safe='')}"

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

    def diagnostic(self, message: str) -> None:
        try:
            self._stderr.write(message.rstrip() + "\n")
            self._stderr.flush()
        except Exception:  # pragma: no cover - diagnostics are best effort
            pass
