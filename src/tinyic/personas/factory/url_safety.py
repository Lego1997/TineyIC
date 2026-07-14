"""Shared secret-safe URL checks for generated persona evidence."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import unicodedata
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit


_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "apikey",
        "auth",
        "authorization",
        "authorizationcode",
        "bearer",
        "code",
        "credential",
        "credentials",
        "csrf",
        "cookie",
        "cfauthorization",
        "key",
        "jwt",
        "loginticket",
        "nonce",
        "oauthstate",
        "otp",
        "otpcode",
        "passwd",
        "password",
        "privatekey",
        "pwd",
        "relaystate",
        "samlart",
        "samlresponse",
        "secret",
        "session",
        "sid",
        "sig",
        "signature",
        "state",
        "ticket",
        "token",
        "xsrf",
    }
)
_SENSITIVE_QUERY_FRAGMENTS = (
    "accesskey",
    "accesstoken",
    "apikey",
    "authnonce",
    "authtoken",
    "authkey",
    "authcode",
    "assertion",
    "clientsecret",
    "cookievalue",
    "codeverifier",
    "credential",
    "csrf",
    "encryptionkey",
    "idtoken",
    "loginticket",
    "oauthcode",
    "oauthnonce",
    "oauthstate",
    "onetimecode",
    "otpcode",
    "passcode",
    "passwordhash",
    "privatekey",
    "refreshtoken",
    "secretkey",
    "securitytoken",
    "sessid",
    "sessionid",
    "sessionkey",
    "sessiontoken",
    "setcookie",
    "signature",
    "signingkey",
    "subscriptionkey",
    "samlrequest",
    "verifier",
    "xsrf",
)
_SENSITIVE_QUERY_SUFFIXES = (
    "assertion",
    "credential",
    "password",
    "passwd",
    "secret",
    "signature",
    "token",
    "verifier",
)
_SENSITIVE_QUERY_COMPONENTS = frozenset(
    {
        "assertion",
        "credential",
        "key",
        "nonce",
        "passwd",
        "password",
        "secret",
        "session",
        "sid",
        "signature",
        "token",
        "verifier",
    }
)
_MAX_DECODE_PASSES = 4
_MAX_JSON_DEPTH = 8
_MAX_JSON_NODES = 256

_NON_ASCII_DOTS = frozenset({"\u3002", "\uff0e", "\uff61"})
_HOST_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
_UNSAFE_TEXT_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_ASCII_ESCAPE_RE = re.compile(
    r"(?:\\u|%u)([0-9a-f]{4})|\\x([0-9a-f]{2})", re.IGNORECASE
)
_ESCAPE_INTRO_RE = re.compile(r"\\[ux]|%u", re.IGNORECASE)
_STRUCTURED_KEY_RE = re.compile(
    r"(?:^|[,{;&?\s])(?:[\"']\s*)?([A-Za-z0-9_.-]+)"
    r"(?:\s*[\"'])?\s*[:=]"
)
_EMBEDDED_URL_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*:)?//[^\s\"'<>\\]+"
)
_MARKDOWN_BREAKOUT_RE = re.compile(r"\]\s*\(|\)\s*\[")


def _decode_ascii_escape(match: re.Match[str]) -> str:
    raw = match.group(1) or match.group(2)
    codepoint = int(raw, 16)
    return chr(codepoint) if codepoint <= 0x7F else match.group(0)


def _decode_once(value: str) -> str:
    decoded = unquote(value)
    decoded = _ASCII_ESCAPE_RE.sub(_decode_ascii_escape, decoded)
    return unicodedata.normalize("NFKC", decoded)


def _escape_syntax_is_unsafe(value: str) -> bool:
    """Reject malformed or non-ASCII legacy/JavaScript escape syntax."""

    for intro in _ESCAPE_INTRO_RE.finditer(value):
        match = _ASCII_ESCAPE_RE.match(value, intro.start())
        if match is None:
            return True
        raw = match.group(1) or match.group(2)
        if int(raw, 16) > 0x7F:
            return True
    return False


def _decoded_variants(value: str) -> Iterator[str]:
    current = value
    yield current
    for _ in range(_MAX_DECODE_PASSES):
        decoded = _decode_once(current)
        if decoded == current:
            break
        yield decoded
        current = decoded


def _decodes_further(value: str) -> bool:
    return _decode_once(value) != value


def _address_is_public(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Apply a fail-closed public-network policy to a parsed IP literal."""

    return bool(
        address.is_global
        and not address.is_private
        and not address.is_reserved
        and not address.is_link_local
        and not address.is_loopback
        and not address.is_multicast
        and not address.is_unspecified
        and not getattr(address, "is_site_local", False)
    )


def _normalized_dns_host(host: str) -> str | None:
    """Normalize a DNS hostname while rejecting ambiguous authority syntax."""

    if not host or "%" in host or any(dot in host for dot in _NON_ASCII_DOTS):
        return None
    normalized = unicodedata.normalize("NFKC", host).lower()
    if not normalized or any(
        character.isspace()
        or unicodedata.category(character) in _HOST_CONTROL_CATEGORIES
        for character in normalized
    ):
        return None
    # A single terminal root dot is conventional. Any other empty label is
    # malformed, including hosts made entirely from dots.
    if normalized.endswith("."):
        normalized = normalized[:-1]
    if not normalized or ".." in normalized or normalized.startswith("."):
        return None
    labels = normalized.split(".")
    # Single-label names are normally resolved through a private search domain,
    # not the public DNS, so they do not meet this public-source contract.
    if len(labels) < 2:
        return None
    ascii_labels: list[str] = []
    for label in labels:
        try:
            ascii_label = label.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None
        if any(ord(character) > 127 for character in label):
            # Python's stdlib codec implements the older IDNA 2003 mapping.
            # Browsers use newer UTS #46/IDNA rules, under which characters
            # such as German sharp-s and Greek final sigma remain distinct.
            # Reject rather than collapse those domains into another origin.
            try:
                round_trip = ascii_label.encode("ascii").decode("idna")
            except UnicodeError:
                return None
            round_trip = unicodedata.normalize("NFKC", round_trip).lower()
            if round_trip != label:
                return None
        if _DNS_LABEL_RE.fullmatch(ascii_label) is None:
            return None
        ascii_labels.append(ascii_label)
    ascii_host = ".".join(ascii_labels)
    if len(ascii_host) > 253:
        return None
    return ascii_host


def normalize_public_host(host: str) -> str | None:
    """Return a canonical public host, or ``None`` for an unsafe spelling.

    Hostnames are normalized with NFKC and IDNA. Percent-encoded and
    non-standard dot spellings are rejected instead of being interpreted
    differently by a browser, proxy, or downstream HTTP client.
    """

    if not isinstance(host, str) or not host:
        return None
    variants = tuple(_decoded_variants(host))
    if variants and _decodes_further(variants[-1]):
        return None
    raw = variants[0]
    # Encoding in an authority is unnecessary and has historically produced
    # parser disagreements. Decode above only to keep the check bounded, then
    # reject the ambiguous spelling regardless of its destination.
    if (
        "%" in raw
        or "\\" in raw
        or any(dot in raw for dot in _NON_ASCII_DOTS)
    ):
        return None
    normalized = unicodedata.normalize("NFKC", raw).lower()
    if not normalized or any(
        character.isspace()
        or unicodedata.category(character) in _HOST_CONTROL_CATEGORIES
        for character in normalized
    ):
        return None
    if normalized.endswith("."):
        normalized = normalized[:-1]
    if not normalized:
        return None

    # Strict IPv4/IPv6 literals are accepted only when globally routable.
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        address = None
    if address is not None:
        return str(address) if _address_is_public(address) else None

    # inet_aton deliberately recognizes historical one-part, shortened,
    # octal, and hexadecimal IPv4 spellings. Reject them wholesale so no
    # component interprets (for example) 2130706433 or 127.1 as localhost.
    try:
        socket.inet_aton(normalized)
    except OSError:
        pass
    else:
        return None

    dns_host = _normalized_dns_host(raw)
    if (
        not dns_host
        or dns_host == "localhost"
        or dns_host.endswith(".localhost")
    ):
        return None
    return dns_host


def public_host_is_allowed(host: str) -> bool:
    """Return whether *host* unambiguously names a public Internet host."""

    return normalize_public_host(host) is not None


def _normalized_query_key(key: str) -> str:
    normalized = unicodedata.normalize("NFKC", key).casefold()
    return re.sub(r"[^a-z0-9]+", "", normalized)


def query_key_is_sensitive(key: str) -> bool:
    """Return whether a URL parameter name conventionally carries a secret."""

    if any(ord(character) > 127 for character in key):
        # Provider-returned parameter names have no reason to use Unicode for
        # credential conventions. Rejecting them avoids homoglyph bypasses.
        return True
    folded = unicodedata.normalize("NFKC", key).casefold()
    normalized = _normalized_query_key(folded)
    components = frozenset(re.findall(r"[a-z0-9]+", folded))
    return (
        normalized in _SENSITIVE_QUERY_KEYS
        or bool(components.intersection(_SENSITIVE_QUERY_COMPONENTS))
        or any(fragment in normalized for fragment in _SENSITIVE_QUERY_FRAGMENTS)
        or any(normalized.endswith(suffix) for suffix in _SENSITIVE_QUERY_SUFFIXES)
    )


def _structured_text_contains_sensitive_key(value: str) -> bool:
    return any(
        query_key_is_sensitive(match.group(1))
        for match in _STRUCTURED_KEY_RE.finditer(value)
    )


def _nested_url_is_unsafe(value: str) -> bool:
    """Inspect a decoded parameter value for nested URL authorities."""

    variants = tuple(_decoded_variants(value))
    if variants and _decodes_further(variants[-1]):
        return True
    for variant in variants:
        if _escape_syntax_is_unsafe(variant):
            return True
        # WHATWG-style URL consumers commonly treat backslashes as slashes.
        # Normalize them only for detection so ``\\127.0.0.1\private`` cannot
        # hide a scheme-relative private authority from urllib's RFC parser.
        readable = variant.replace(r"\/", "/").replace("\\", "/")
        candidates = [readable]
        candidates.extend(
            match.group(0) for match in _EMBEDDED_URL_RE.finditer(readable)
        )
        for candidate in candidates:
            try:
                nested = urlsplit(candidate)
                nested.port
            except ValueError:
                return True
            if nested.username or nested.password:
                return True
            has_authority = bool(nested.netloc) or nested.scheme.casefold() in {
                "http",
                "https",
            }
            if nested.hostname and not public_host_is_allowed(nested.hostname):
                return True
            if has_authority and not nested.hostname:
                return True
    return False


def _json_key_is_sensitive(key: str) -> bool:
    variants = tuple(_decoded_variants(key))
    if variants and _decodes_further(variants[-1]):
        return True
    return any(
        _escape_syntax_is_unsafe(variant)
        or query_key_is_sensitive(variant)
        for variant in variants
    )


def _json_node_is_unsafe(
    value: Any,
    *,
    depth: int,
    nodes: list[int],
) -> bool:
    nodes[0] += 1
    if nodes[0] > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
        return True
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or _json_key_is_sensitive(key):
                return True
            if _json_node_is_unsafe(nested, depth=depth + 1, nodes=nodes):
                return True
        return False
    if isinstance(value, list):
        return any(
            _json_node_is_unsafe(item, depth=depth + 1, nodes=nodes)
            for item in value
        )
    if isinstance(value, str):
        variants = tuple(_decoded_variants(value))
        if variants and _decodes_further(variants[-1]):
            return True
        for variant in variants:
            if (
                _escape_syntax_is_unsafe(variant)
                or _structured_text_contains_sensitive_key(variant)
                or _nested_url_is_unsafe(variant)
            ):
                return True
        return _json_text_is_unsafe(value, depth=depth + 1, nodes=nodes)
    return False


def _json_text_is_unsafe(
    value: str,
    *,
    depth: int = 0,
    nodes: list[int] | None = None,
) -> bool:
    if depth > _MAX_JSON_DEPTH:
        return True
    nodes = nodes if nodes is not None else [0]
    variants = tuple(_decoded_variants(value))
    if variants and _decodes_further(variants[-1]):
        return True
    for variant in variants:
        if _escape_syntax_is_unsafe(variant):
            return True
        stripped = variant.strip()
        if not stripped or stripped[0] not in "{[\"":
            continue
        try:
            parsed = json.loads(stripped)
        except (TypeError, ValueError):
            continue
        if _json_node_is_unsafe(parsed, depth=depth + 1, nodes=nodes):
            return True
    return False


def _path_is_unsafe(path: str) -> bool:
    variants = tuple(_decoded_variants(path))
    if variants and _decodes_further(variants[-1]):
        return True
    for variant in variants:
        if _escape_syntax_is_unsafe(variant):
            return True
        if any(
            unicodedata.category(character) in _UNSAFE_TEXT_CATEGORIES
            for character in variant
        ):
            return True
        if "\\" in variant or "<" in variant or ">" in variant:
            return True
        if _MARKDOWN_BREAKOUT_RE.search(variant):
            return True
        if (
            _structured_text_contains_sensitive_key(variant)
            or _json_text_is_unsafe(variant)
            or _nested_url_is_unsafe(variant)
        ):
            return True
    return False


def _field_contains_sensitive_parameter(field: str) -> bool:
    variants = tuple(_decoded_variants(field))
    if variants and _decodes_further(variants[-1]):
        # Excessive nested encoding is unnecessary for a public citation URL
        # and must not provide an unbounded hiding place for credential keys.
        return True
    for decoded in variants:
        if _escape_syntax_is_unsafe(decoded):
            return True
        if _structured_text_contains_sensitive_key(decoded):
            return True
        if _json_text_is_unsafe(decoded):
            return True
        for key, nested_value in parse_qsl(
            re.sub(r"[;?#]", "&", decoded), keep_blank_values=True
        ):
            if query_key_is_sensitive(key):
                return True
            if _json_text_is_unsafe(nested_value):
                return True
            if _nested_url_is_unsafe(nested_value):
                return True
        if _nested_url_is_unsafe(decoded):
            return True
    return False


def url_contains_sensitive_material(value: Any) -> bool:
    """Detect secret parameters and unsafe nested URLs after bounded decoding.

    Values are deliberately not pattern-matched: real credentials can be short,
    opaque, or otherwise indistinguishable from ordinary public identifiers.
    Rejecting by the parameter name prevents the value from reaching prompts,
    artifacts, logs, or exports.
    """

    if not isinstance(value, str):
        return False
    candidates = tuple(_decoded_variants(value))
    if candidates and _decodes_further(candidates[-1]):
        return True
    for candidate in candidates:
        if _escape_syntax_is_unsafe(candidate):
            return True
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return True
        if parsed.username or parsed.password:
            return True
        if _field_contains_sensitive_parameter(parsed.query):
            return True
        if _field_contains_sensitive_parameter(parsed.fragment):
            return True
        if _path_is_unsafe(parsed.path):
            return True
        # RFC 3986 path segments can carry matrix parameters (notably
        # ``;jsessionid=...``). Scan only the semicolon suffixes so ordinary
        # human-readable path components remain untouched.
        matrix = "&".join(
            segment.partition(";")[2]
            for segment in parsed.path.split("/")
            if ";" in segment
        )
        if matrix and _field_contains_sensitive_parameter(matrix):
            return True
    return False
