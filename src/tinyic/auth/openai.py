"""OpenAI ChatGPT-subscription authentication via the official Codex runtime.

TinyIC does not own Codex credentials.  This module can *inspect* Codex's
configured credential source and ask ``codex app-server`` for account status,
but it never copies, refreshes, rotates, or writes ``~/.codex/auth.json``.  In
particular, every ``account/read`` probe sets ``refreshToken`` to ``False``.

Browser and device-code login are also delegated to app-server's
``account/login/start`` API, so TinyIC contains no OAuth client id or token
exchange.  The browser paste fallback only validates and forwards a loopback
redirect to the callback listener that app-server already owns.

Reason codes are deliberately small and stable because :mod:`tinyic.auth.doctor`
and the later onboarding wizard consume this module as a probe seam:
``ok``, ``missing_credential``, ``expired``, ``runtime_unavailable``, and
``invalid_credential``.  Results contain provenance, never credential values,
account ids, email addresses, callback codes, or raw runtime error text.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import threading
import tomllib
import urllib.request
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit


CODEX_APP_SERVER_COMMAND: tuple[str, ...] = (
    "codex",
    "app-server",
    "-c",
    "shell_environment_policy.inherit=none",
    "--disable",
    "shell_tool",
    "--disable",
    "unified_exec",
    "--disable",
    "code_mode",
    "--disable",
    "standalone_web_search",
    "--disable",
    "browser_use",
    "--disable",
    "computer_use",
    "--disable",
    "apps",
    "--disable",
    "plugins",
    "--disable",
    "multi_agent",
    "--disable",
    "multi_agent_v2",
    "--disable",
    "image_generation",
    "--disable",
    "hooks",
    "--listen",
    "stdio://",
)

_CLIENT_INFO: dict[str, str] = {
    "name": "tinyic",
    "title": "TinyIC",
    "version": "2",
}
_CREDENTIAL_STORES = frozenset({"file", "keyring", "auto"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_STDIO_EOF = object()

# The official runtime needs only its own home/config location plus ordinary
# process/network plumbing.  Passing the full TinyIC environment would hand an
# inference subprocess unrelated provider keys and cloud credentials.  Keep
# this allowlist deliberately narrow and auditable; proxy variables remain so
# users whose Codex login requires an enterprise/system proxy are not broken.
_CODEX_CHILD_ENV_NAMES = frozenset(
    {
        "HOME",
        "USERPROFILE",
        "CODEX_HOME",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "APPDATA",
        "LOCALAPPDATA",
    }
)


def _codex_child_env(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the minimal environment allowed into official app-server.

    This is an allowlist rather than a credential-name blacklist: newly added
    provider secrets stay out by default.  Values are passed through only to
    the child process and are never logged or included in diagnostics.
    """

    environment = os.environ if source is None else source
    return {
        name: value
        for name, value in environment.items()
        if name in _CODEX_CHILD_ENV_NAMES and isinstance(value, str)
    }


class OpenAIAuthReason(str, Enum):
    """Stable reason codes returned by the OpenAI subscription probe."""

    OK = "ok"
    MISSING_CREDENTIAL = "missing_credential"
    EXPIRED = "expired"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    INVALID_CREDENTIAL = "invalid_credential"


@dataclass(frozen=True)
class OpenAIAuthProbe:
    """Secret-free result of probing the Codex-backed subscription lane."""

    reason: OpenAIAuthReason
    credential_store: str
    source: str
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason is OpenAIAuthReason.OK


class LoginMode(str, Enum):
    """Official app-server login modes exposed by TinyIC."""

    BROWSER = "browser"
    DEVICE_CODE = "device_code"


@dataclass(frozen=True, repr=False)
class LoginChallenge:
    """Non-secret instructions returned by ``account/login/start``."""

    mode: LoginMode
    login_id: str
    auth_url: str | None = None
    verification_url: str | None = None
    user_code: str | None = None

    def __repr__(self) -> str:
        return (
            "LoginChallenge("
            f"mode={self.mode.value!r}, login_id='<redacted>', "
            f"has_auth_url={self.auth_url is not None}, "
            f"has_verification_url={self.verification_url is not None}, "
            f"has_user_code={self.user_code is not None}"
            ")"
        )


class CodexRPCError(RuntimeError):
    """A sanitized app-server JSON-RPC failure."""

    def __init__(self, method: str, code: int | str | None = None) -> None:
        super().__init__(f"Codex app-server request {method!r} failed")
        self.method = method
        # JSON-RPC error codes are integers. Discard any runtime-controlled
        # string instead of retaining it on a structured exception object.
        self.code = (
            code
            if isinstance(code, int) and not isinstance(code, bool)
            else None
        )


@runtime_checkable
class CodexRPC(Protocol):
    """Injectable app-server seam used by all offline tests."""

    def request(
        self, method: str, params: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def notify(
        self, method: str, params: Mapping[str, Any] | None = None
    ) -> None: ...

    def notifications(self) -> Iterator[Mapping[str, Any]]: ...

    def close(self) -> None: ...


RPCFactory = Callable[[], CodexRPC]


class StdioCodexRPC:
    """Newline-delimited JSON-RPC over ``codex app-server --listen stdio://``.

    Stderr is intentionally discarded: a runtime or upstream error can contain
    bearer material, redirect codes, or prompt fragments and must not cross
    into TinyIC logs/errors.  Server-to-client requests are rejected rather
    than approved; the subscription completion binding does not expose tools.
    """

    def __init__(
        self,
        *,
        command: tuple[str, ...] = CODEX_APP_SERVER_COMMAND,
        process_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        request_timeout: float = 30.0,
        notification_timeout: float = 300.0,
    ) -> None:
        if request_timeout <= 0 or notification_timeout <= 0:
            raise ValueError("Codex RPC timeouts must be positive")
        self.command = tuple(command)
        self._request_timeout = float(request_timeout)
        self._notification_timeout = float(notification_timeout)
        try:
            self._process = process_factory(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_codex_child_env(),
                shell=False,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except (FileNotFoundError, OSError):
            raise RuntimeError("Codex app-server is unavailable") from None
        if self._process.stdin is None or self._process.stdout is None:
            self._process.kill()
            raise RuntimeError("Codex app-server stdio is unavailable")
        self._stdin = self._process.stdin
        self._stdout = self._process.stdout
        self._next_id = 1
        self._pending: deque[Mapping[str, Any]] = deque()
        self._write_lock = threading.Lock()
        self._closed = False
        self._read_queue: queue.Queue[str | object] = queue.Queue()
        self._reader = threading.Thread(
            target=self._pump_stdout,
            name="tinyic-codex-stdio-reader",
            daemon=True,
        )
        self._reader.start()

    def request(
        self, method: str, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"id": request_id, "method": method, "params": dict(params)})
        while True:
            message = self._read(timeout=self._request_timeout)
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    error = message.get("error")
                    code = error.get("code") if isinstance(error, Mapping) else None
                    raise CodexRPCError(method, code)
                result = message.get("result")
                return dict(result) if isinstance(result, Mapping) else {}
            if "id" in message and "method" in message:
                self._reject_server_request(message)
            elif "method" in message:
                self._pending.append(message)

    def notify(
        self, method: str, params: Mapping[str, Any] | None = None
    ) -> None:
        message: dict[str, Any] = {"method": method}
        if params:
            message["params"] = dict(params)
        self._send(message)

    def notifications(self) -> Iterator[Mapping[str, Any]]:
        while not self._closed:
            if self._pending:
                yield self._pending.popleft()
                continue
            message = self._read(timeout=self._notification_timeout)
            if "id" in message and "method" in message:
                self._reject_server_request(message)
            elif "method" in message:
                yield message

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stdin.close()
        except OSError:
            pass
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)

    def __enter__(self) -> "StdioCodexRPC":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _send(self, message: Mapping[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("Codex app-server connection is closed")
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        try:
            with self._write_lock:
                self._stdin.write(encoded + "\n")
                self._stdin.flush()
        except (BrokenPipeError, OSError):
            raise RuntimeError("Codex app-server became unavailable") from None

    def _pump_stdout(self) -> None:
        """Read potentially blocking child stdout outside caller threads."""

        try:
            while True:
                line = self._stdout.readline()
                if not line:
                    break
                self._read_queue.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._read_queue.put(_STDIO_EOF)

    def _read(self, *, timeout: float) -> Mapping[str, Any]:
        try:
            item = self._read_queue.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise RuntimeError("Codex app-server timed out") from None
        if item is _STDIO_EOF or not isinstance(item, str):
            raise RuntimeError("Codex app-server became unavailable")
        try:
            message = json.loads(item)
        except (json.JSONDecodeError, UnicodeError):
            raise RuntimeError("Codex app-server returned invalid JSON") from None
        if not isinstance(message, Mapping):
            raise RuntimeError("Codex app-server returned an invalid message")
        return message

    def _reject_server_request(self, message: Mapping[str, Any]) -> None:
        # No user/runtime text is reflected in the error response.
        self._send(
            {
                "id": message.get("id"),
                "error": {
                    "code": -32601,
                    "message": "TinyIC subscription binding exposes no tools",
                },
            }
        )


def default_rpc_factory() -> CodexRPC:
    """Open one production app-server connection."""

    return StdioCodexRPC()


def initialize_rpc(rpc: CodexRPC) -> None:
    """Perform the mandatory app-server initialize handshake."""

    rpc.request(
        "initialize",
        {
            "clientInfo": dict(_CLIENT_INFO),
            # TinyIC depends on the official empty-environment controls to
            # remove shell/file capabilities from inference-only threads.
            "capabilities": {"experimentalApi": True},
        },
    )
    rpc.notify("initialized", {})


@contextmanager
def _managed_rpc(factory: RPCFactory) -> Iterator[CodexRPC]:
    rpc = factory()
    try:
        yield rpc
    finally:
        rpc.close()


def _codex_home(
    *, home: Path | None, env: Mapping[str, str] | None
) -> Path:
    environment = os.environ if env is None else env
    explicit = environment.get("CODEX_HOME")
    if explicit:
        return Path(explicit).expanduser()
    base = Path.home() if home is None else Path(home)
    return base.expanduser() / ".codex"


def _read_credential_store(codex_home: Path) -> tuple[str | None, bool]:
    config_path = codex_home / "config.toml"
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except FileNotFoundError:
        return "file", True
    except (OSError, tomllib.TOMLDecodeError):
        return None, False
    value = config.get("cli_auth_credentials_store", "file")
    if not isinstance(value, str) or value not in _CREDENTIAL_STORES:
        return None, False
    return value, True


def _coerce_expiry(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        # Some tools serialize epoch milliseconds.
        return number / 1000 if number > 10_000_000_000 else number
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return _coerce_expiry(float(stripped))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _jwt_expiry(token: str) -> tuple[float | None, bool]:
    parts = token.split(".")
    if len(parts) != 3:
        return None, True  # opaque access tokens are valid and have no local exp
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None, False
    if not isinstance(decoded, Mapping):
        return None, False
    expiry = _coerce_expiry(decoded.get("exp"))
    return expiry, expiry is not None


def _probe_auth_file(
    path: Path, *, now: datetime | None = None, credential_store: str = "file"
) -> OpenAIAuthProbe:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return OpenAIAuthProbe(
            OpenAIAuthReason.MISSING_CREDENTIAL,
            credential_store,
            "codex_file",
            "Codex ChatGPT sign-in was not found",
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return OpenAIAuthProbe(
            OpenAIAuthReason.INVALID_CREDENTIAL,
            credential_store,
            "codex_file",
            "Codex credential file is unreadable or invalid",
        )
    if not isinstance(raw, Mapping):
        return OpenAIAuthProbe(
            OpenAIAuthReason.INVALID_CREDENTIAL,
            credential_store,
            "codex_file",
            "Codex credential file has an invalid shape",
        )
    tokens = raw.get("tokens")
    token_map = tokens if isinstance(tokens, Mapping) else raw
    access = token_map.get("access_token") or token_map.get("accessToken")
    if not isinstance(access, str) or not access:
        reason = (
            OpenAIAuthReason.INVALID_CREDENTIAL
            if raw.get("OPENAI_API_KEY") or raw
            else OpenAIAuthReason.MISSING_CREDENTIAL
        )
        return OpenAIAuthProbe(
            reason,
            credential_store,
            "codex_file",
            "Codex credential is not a ChatGPT subscription login",
        )

    expiry: float | None = None
    for key in ("expires_at", "expiresAt", "expires"):
        expiry = _coerce_expiry(token_map.get(key))
        if expiry is not None:
            break
    if expiry is None:
        expiry, structurally_valid = _jwt_expiry(access)
        if not structurally_valid:
            return OpenAIAuthProbe(
                OpenAIAuthReason.INVALID_CREDENTIAL,
                credential_store,
                "codex_file",
                "Codex access credential is invalid",
            )
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    if expiry is not None and instant.timestamp() >= expiry:
        return OpenAIAuthProbe(
            OpenAIAuthReason.EXPIRED,
            credential_store,
            "codex_file",
            "Codex ChatGPT sign-in has expired",
        )
    return OpenAIAuthProbe(
        OpenAIAuthReason.OK,
        credential_store,
        "codex_file",
        "Codex ChatGPT sign-in is available",
    )


def _probe_runtime_account(
    rpc_factory: RPCFactory, *, credential_store: str
) -> OpenAIAuthProbe:
    try:
        with _managed_rpc(rpc_factory) as rpc:
            initialize_rpc(rpc)
            # The false value is the legal/ownership boundary: the status probe
            # must not rotate or rewrite credentials owned by Codex.
            result = rpc.request("account/read", {"refreshToken": False})
    except (FileNotFoundError, OSError, RuntimeError):
        return OpenAIAuthProbe(
            OpenAIAuthReason.RUNTIME_UNAVAILABLE,
            credential_store,
            "codex_runtime",
            "Codex app-server is unavailable",
        )
    account = result.get("account")
    if not isinstance(account, Mapping):
        return OpenAIAuthProbe(
            OpenAIAuthReason.MISSING_CREDENTIAL,
            credential_store,
            "codex_runtime",
            "Codex ChatGPT sign-in was not found",
        )
    if account.get("type") != "chatgpt":
        return OpenAIAuthProbe(
            OpenAIAuthReason.INVALID_CREDENTIAL,
            credential_store,
            "codex_runtime",
            "Codex is authenticated with a non-subscription credential",
        )
    return OpenAIAuthProbe(
        OpenAIAuthReason.OK,
        credential_store,
        "codex_runtime",
        "Codex ChatGPT sign-in is available",
    )


def _coerce_runtime_probe(
    value: OpenAIAuthProbe | OpenAIAuthReason | str,
    *,
    credential_store: str,
) -> OpenAIAuthProbe:
    if isinstance(value, OpenAIAuthProbe):
        return value
    try:
        reason = OpenAIAuthReason(value)
    except (TypeError, ValueError):
        reason = OpenAIAuthReason.INVALID_CREDENTIAL
    return OpenAIAuthProbe(reason, credential_store, "codex_runtime")


def probe_codex_auth(
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    credential_store: str | None = None,
    runtime_probe: Callable[
        [], OpenAIAuthProbe | OpenAIAuthReason | str
    ]
    | None = None,
    rpc_factory: RPCFactory | None = None,
    now: datetime | None = None,
) -> OpenAIAuthProbe:
    """Probe the configured Codex ChatGPT credential without taking ownership.

    ``file`` reads ``auth.json`` only.  ``keyring`` delegates entirely to the
    official runtime because TinyIC deliberately does not know Codex's keyring
    service/schema.  ``auto`` asks the runtime first and falls back to the file
    only when the runtime reports no credential or is unavailable.
    """

    codex_home = _codex_home(home=home, env=env)
    if credential_store is None:
        store, valid_config = _read_credential_store(codex_home)
        if not valid_config or store is None:
            return OpenAIAuthProbe(
                OpenAIAuthReason.INVALID_CREDENTIAL,
                "unknown",
                "codex_config",
                "Codex credential-store configuration is invalid",
            )
    else:
        store = credential_store
        if store not in _CREDENTIAL_STORES:
            return OpenAIAuthProbe(
                OpenAIAuthReason.INVALID_CREDENTIAL,
                str(store),
                "codex_config",
                "Codex credential-store configuration is invalid",
            )

    file_probe = lambda: _probe_auth_file(
        codex_home / "auth.json", now=now, credential_store=store
    )
    if store == "file":
        return file_probe()

    try:
        if runtime_probe is not None:
            runtime = _coerce_runtime_probe(
                runtime_probe(), credential_store=store
            )
        else:
            runtime = _probe_runtime_account(
                rpc_factory or default_rpc_factory, credential_store=store
            )
    except (FileNotFoundError, OSError, RuntimeError):
        runtime = OpenAIAuthProbe(
            OpenAIAuthReason.RUNTIME_UNAVAILABLE,
            store,
            "codex_runtime",
            "Codex app-server is unavailable",
        )

    if store == "keyring" or runtime.reason not in {
        OpenAIAuthReason.MISSING_CREDENTIAL,
        OpenAIAuthReason.RUNTIME_UNAVAILABLE,
    }:
        return runtime
    fallback = file_probe()
    if fallback.reason is not OpenAIAuthReason.MISSING_CREDENTIAL:
        return fallback
    return (
        runtime
        if runtime.reason is OpenAIAuthReason.RUNTIME_UNAVAILABLE
        else fallback
    )


class CodexLoginSession:
    """One official app-server login session kept alive through completion."""

    def __init__(self, *, rpc_factory: RPCFactory | None = None) -> None:
        self._rpc_factory = rpc_factory or default_rpc_factory
        self._rpc: CodexRPC | None = None

    def __enter__(self) -> "CodexLoginSession":
        self._rpc = self._rpc_factory()
        try:
            initialize_rpc(self._rpc)
        except Exception:
            self._rpc.close()
            self._rpc = None
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        if self._rpc is not None:
            self._rpc.close()
            self._rpc = None

    def start(self, mode: LoginMode | str) -> LoginChallenge:
        """Start browser or device login through ``account/login/start``."""

        if self._rpc is None:
            raise RuntimeError("Codex login session is not open")
        chosen = LoginMode(mode)
        params: dict[str, Any]
        if chosen is LoginMode.BROWSER:
            # ``appBrand`` is optional.  Omitting it keeps identity ownership
            # crisp: TinyIC identifies itself in ``initialize.clientInfo`` and
            # delegates OAuth mechanics to the official Codex runtime without
            # asking that runtime to brand TinyIC as Codex or ChatGPT.
            params = {"type": "chatgpt"}
        else:
            params = {"type": "chatgptDeviceCode"}
        response = self._rpc.request("account/login/start", params)
        login_id = response.get("loginId")
        if not isinstance(login_id, str) or not login_id:
            raise RuntimeError("Codex app-server returned an invalid login challenge")
        if chosen is LoginMode.BROWSER:
            auth_url = response.get("authUrl")
            if not isinstance(auth_url, str) or not auth_url:
                raise RuntimeError(
                    "Codex app-server returned an invalid browser challenge"
                )
            return LoginChallenge(chosen, login_id, auth_url=auth_url)
        verification_url = response.get("verificationUrl")
        user_code = response.get("userCode")
        if not isinstance(verification_url, str) or not isinstance(user_code, str):
            raise RuntimeError("Codex app-server returned an invalid device challenge")
        return LoginChallenge(
            chosen,
            login_id,
            verification_url=verification_url,
            user_code=user_code,
        )

    def wait(self, challenge: LoginChallenge) -> OpenAIAuthProbe:
        """Wait for app-server's matching login-completed notification."""

        if self._rpc is None:
            raise RuntimeError("Codex login session is not open")
        try:
            for notification in self._rpc.notifications():
                if notification.get("method") != "account/login/completed":
                    continue
                params = notification.get("params")
                if not isinstance(params, Mapping):
                    continue
                if params.get("loginId") not in (None, challenge.login_id):
                    continue
                if params.get("success") is True:
                    return OpenAIAuthProbe(
                        OpenAIAuthReason.OK,
                        "runtime",
                        "codex_runtime",
                        "ChatGPT sign-in completed",
                    )
                return OpenAIAuthProbe(
                    OpenAIAuthReason.INVALID_CREDENTIAL,
                    "runtime",
                    "codex_runtime",
                    "ChatGPT sign-in failed",
                )
        except (OSError, RuntimeError):
            pass
        return OpenAIAuthProbe(
            OpenAIAuthReason.RUNTIME_UNAVAILABLE,
            "runtime",
            "codex_runtime",
            "Codex app-server ended before login completed",
        )


def _expected_loopback(auth_url: str) -> tuple[str | None, str | None]:
    query = parse_qs(urlsplit(auth_url).query, keep_blank_values=True)
    state_values = query.get("state", [])
    redirect_values = query.get("redirect_uri", [])
    state = state_values[0] if len(state_values) == 1 else None
    redirect = redirect_values[0] if len(redirect_values) == 1 else None
    return state, redirect


def _deliver_loopback(url: str) -> None:
    # Proxy bypass is explicit: a validated loopback callback must never be
    # forwarded to a configured corporate/system HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=10) as response:
            response.read(4096)
    except Exception:
        # Suppress the urllib exception as well as its traceback cause: HTTP
        # errors commonly include the complete callback URL (and OAuth code).
        raise RuntimeError("Could not deliver redirect to Codex app-server") from None


def deliver_pasted_redirect(
    auth_url: str,
    pasted_redirect: str,
    *,
    deliver: Callable[[str], None] | None = None,
) -> None:
    """Validate and deliver a pasted OAuth redirect to app-server's listener.

    The callback must be plain HTTP on a loopback host, contain exactly one
    non-empty authorization code, and carry the exact state from app-server's
    authorization URL.  When app-server supplied ``redirect_uri`` its port and
    path must match as well.  Validation errors never reflect the pasted URL.
    """

    try:
        expected_state, expected_redirect = _expected_loopback(auth_url)
        parsed = urlsplit(pasted_redirect)
        query = parse_qs(parsed.query, keep_blank_values=True)
        states = query.get("state", [])
        codes = query.get("code", [])
        valid = (
            expected_state is not None
            and parsed.scheme == "http"
            and parsed.hostname in _LOOPBACK_HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port is not None
            and len(states) == 1
            and states[0] == expected_state
            and len(codes) == 1
            and bool(codes[0])
        )
        if valid and expected_redirect:
            expected = urlsplit(expected_redirect)
            valid = (
                expected.scheme == "http"
                and expected.hostname in _LOOPBACK_HOSTS
                and parsed.hostname == expected.hostname
                and parsed.port == expected.port
                and parsed.path == expected.path
            )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Pasted redirect is not the expected local OAuth callback")
    try:
        (deliver or _deliver_loopback)(pasted_redirect)
    except RuntimeError:
        raise
    except Exception:
        # An injected UI/local delivery implementation may include the callback
        # URL in its exception.  Do not let that OAuth code escape.
        raise RuntimeError("Could not deliver redirect to Codex app-server") from None


__all__ = [
    "CODEX_APP_SERVER_COMMAND",
    "CodexLoginSession",
    "CodexRPC",
    "CodexRPCError",
    "LoginChallenge",
    "LoginMode",
    "OpenAIAuthProbe",
    "OpenAIAuthReason",
    "RPCFactory",
    "StdioCodexRPC",
    "default_rpc_factory",
    "deliver_pasted_redirect",
    "initialize_rpc",
    "probe_codex_auth",
]
