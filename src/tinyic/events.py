"""Versioned, append-only event persistence for TinyIC debates.

The JSONL stream defined in ``docs/event-schema.md`` is the public boundary
between the debate engine and every renderer.  This module deliberately has
no dependencies on debate or UI internals so logs can be consumed without
constructing a simulation.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from numbers import Real
from pathlib import Path
from typing import Any, Literal, TextIO

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


SCHEMA_VERSION = 1
TERMINAL_EVENT_TYPES = frozenset({"debate_completed", "debate_error"})


# Fields without a trailing question mark in docs/event-schema.md are required.
# Unknown event types and unknown payload fields remain valid additive v1
# extensions, as required by the compatibility contract.
_REQUIRED_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "debate_started": frozenset(
        {
            "ticker",
            "company_name",
            "preset",
            "personas",
            "moderator",
            "aggregator",
            "caps",
            "config_hash",
            "tinyic_version",
        }
    ),
    "data_ready": frozenset(
        {"sources", "financials_summary", "description", "fetched_at"}
    ),
    "phase_started": frozenset({"phase", "index"}),
    "phase_completed": frozenset({"phase", "index", "turn_count"}),
    "debate_completed": frozenset(
        {"phases_completed", "duration_s", "result_ref"}
    ),
    "debate_error": frozenset({"stage", "message", "recoverable"}),
    "turn_started": frozenset({"turn_id", "persona", "phase", "role"}),
    "think_delta": frozenset({"turn_id", "text"}),
    "think_completed": frozenset({"turn_id", "full_text"}),
    "talk_delta": frozenset({"turn_id", "text"}),
    "talk_completed": frozenset({"turn_id", "full_text"}),
    "cognitive_state": frozenset(
        {"turn_id", "persona", "goals", "attention", "emotions"}
    ),
    "turn_completed": frozenset(
        {"turn_id", "persona", "phase", "interrupted", "usage_ref"}
    ),
    "turn_interrupted": frozenset(
        {"turn_id", "persona", "by", "disposition"}
    ),
    "steering_submitted": frozenset({"msg_id", "mode", "text", "source"}),
    "steering_delivered": frozenset({"msg_id", "delivered_before_turn_id"}),
    "steering_dropped": frozenset({"msg_id", "reason"}),
    "thesis_recorded": frozenset(
        {"persona", "phase", "stance", "claims", "confidence"}
    ),
    "vote_recorded": frozenset(
        {
            "persona",
            "vote",
            "confidence",
            "reasoning",
            "key_risks",
            "changed_mind",
            "source",
        }
    ),
    "scorecard": frozenset(
        {"votes", "bull_count", "bear_count", "hold_count"}
    ),
    "memo_section": frozenset(
        {
            "section",
            "content",
            "contributing_personas",
            "supporting_data",
        }
    ),
    "disagreement": frozenset(
        {"dimension", "description", "sides", "resolution"}
    ),
    "collapse_metric": frozenset(
        {
            "persona",
            "phase",
            "stance_before",
            "stance_after",
            "caved",
            "note",
        }
    ),
    "usage": frozenset(
        {
            "purpose",
            "model_ref",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
        }
    ),
    "usage_window": frozenset(
        {"auth_profile", "lane", "window_used_msgs", "window_estimate_msgs"}
    ),
}

_OPTIONAL_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "data_ready": frozenset(),
    "phase_started": frozenset({"da_persona"}),
    "turn_started": frozenset({"target_persona"}),
    "cognitive_state": frozenset({"context"}),
    "steering_submitted": frozenset({"target_persona"}),
    "scorecard": frozenset({"consensus"}),
    "usage": frozenset({"turn_id", "persona", "cost_usd"}),
    "usage_window": frozenset({"resets_at"}),
}

# ``usage_ref`` is present on every completed turn but may be null when the
# legacy M1 client exposes no authoritative usage record. M2 adapters fill it
# whenever they emit a matching usage event. Other required v1 fields must
# carry a value, not merely a key.
_NULLABLE_REQUIRED_FIELDS = frozenset({("turn_completed", "usage_ref")})

_ENUM_PAYLOAD_FIELDS: dict[str, dict[str, frozenset[Any]]] = {
    "phase_started": {
        "phase": frozenset({"opening", "cross_exam", "rebuttal", "verdict"})
    },
    "phase_completed": {
        "phase": frozenset({"opening", "cross_exam", "rebuttal", "verdict"})
    },
    "turn_started": {
        "phase": frozenset({"opening", "cross_exam", "rebuttal", "verdict"}),
        "role": frozenset(
            {"statement", "challenge", "response", "rebuttal", "verdict"}
        ),
    },
    "turn_completed": {
        "phase": frozenset({"opening", "cross_exam", "rebuttal", "verdict"})
    },
    "turn_interrupted": {
        "by": frozenset({"user", "system"}),
        "disposition": frozenset({"cancelled", "discarded_on_arrival"}),
    },
    "steering_submitted": {
        "mode": frozenset({"steer", "queue"}),
        "source": frozenset({"tui", "stdin", "api"}),
    },
    "thesis_recorded": {
        "phase": frozenset({"opening"}),
        "stance": frozenset({"bullish", "bearish", "neutral"}),
    },
    "vote_recorded": {
        "vote": frozenset({"BUY", "HOLD", "SELL"}),
        "confidence": frozenset({"HIGH", "MEDIUM", "LOW"}),
        "source": frozenset({"structured", "extracted"}),
    },
    "scorecard": {
        "consensus": frozenset({"BUY", "HOLD", "SELL"}),
    },
    "memo_section": {
        "section": frozenset(
            {
                "executive_summary",
                "investment_thesis",
                "key_risks",
                "valuation_discussion",
                "final_verdict",
            }
        )
    },
    "usage": {
        "purpose": frozenset({"turn", "extraction", "memo", "research"})
    },
    "usage_window": {"lane": frozenset({"subscription"})},
}

_STRING_FIELDS = frozenset(
    {
        "ticker",
        "company_name",
        "preset",
        "moderator",
        "aggregator",
        "config_hash",
        "tinyic_version",
        "description",
        "fetched_at",
        "phase",
        "result_ref",
        "stage",
        "message",
        "turn_id",
        "persona",
        "role",
        "target_persona",
        "text",
        "full_text",
        "goals",
        "attention",
        "emotions",
        "da_persona",
        "by",
        "disposition",
        "msg_id",
        "mode",
        "source",
        "delivered_before_turn_id",
        "reason",
        "stance",
        "vote",
        "confidence",
        "section",
        "content",
        "dimension",
        "resolution",
        "stance_before",
        "stance_after",
        "note",
        "purpose",
        "model_ref",
        "auth_profile",
        "consensus",
        "lane",
        "resets_at",
    }
)
_LIST_FIELDS = frozenset(
    {
        "personas",
        "sources",
        "phases_completed",
        "context",
        "claims",
        "reasoning",
        "key_risks",
        "votes",
        "contributing_personas",
        "supporting_data",
        "sides",
    }
)
_DICT_FIELDS = frozenset({"caps", "financials_summary"})
_BOOL_FIELDS = frozenset({"recoverable", "interrupted", "changed_mind", "caved"})
_INT_FIELDS = frozenset(
    {
        "index",
        "turn_count",
        "bull_count",
        "bear_count",
        "hold_count",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "window_used_msgs",
        "window_estimate_msgs",
        "usage_ref",
    }
)
_NUMBER_FIELDS = frozenset({"duration_s", "cost_usd"})

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "oauth_token",
        "client_secret",
        "password",
        "passwd",
        "authorization",
        "cookie",
        "set_cookie",
        "credentials",
        "credential",
    }
)
_FORBIDDEN_CONTENT_KEYS = frozenset(
    {
        "full_prompt",
        "raw_prompt",
        "system_prompt",
        "provider_request",
        "provider_response",
        "provider_exception",
        "raw_request",
        "raw_response",
        "exception",
        "traceback",
        "stack_trace",
        "messages",
    }
)
_CREDENTIAL_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "XAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "MOONSHOT_API_KEY",
    "KIMI_API_KEY",
    # DeepSeek support is removed, but a stale key may still be exported in a
    # user's environment; redacting it costs nothing (matches the vendored
    # backstop in tinytroupe/utils/config.py).
    "DEEPSEEK_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)(\bauthorization\s*:\s*(?:bearer|basic)|\bbearer)"
    r"\s+[^\s,;\"']+"
)
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"token|secret|key)=)[^&\s\"']+"
)
_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"(?:sk|xai|ghp|gho|github_pat)-[A-Za-z0-9_-]{8,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[0-9A-Za-z_-]{8,}\.[0-9A-Za-z_-]{8,}\.[0-9A-Za-z_-]{8,}"
    r")"
)


def _canonicalize_payload_key(key: object) -> str:
    """Normalize snake/kebab/spaced and camel-case payload field names."""
    # Split only lower/digit -> upper boundaries.  This handles ``accessToken``
    # and ``OAuthToken`` without turning initialisms such as ``APIKey`` into
    # surprising one-letter components; ``apikey`` is an explicit alias below.
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
    return re.sub(r"[^a-z0-9]+", "_", separated.lower()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _canonicalize_payload_key(key)
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_api_key")
        or normalized.endswith("_access_token")
        or normalized.endswith("_refresh_token")
        or normalized.endswith("_client_secret")
    )


def _is_forbidden_content_key(key: object) -> bool:
    normalized = _canonicalize_payload_key(key)
    return (
        normalized in _FORBIDDEN_CONTENT_KEYS
        or normalized.endswith("_full_prompt")
        or normalized.endswith("_provider_exception")
        or normalized.endswith("_traceback")
    )


def _must_redact_key(key: object) -> bool:
    return _is_sensitive_key(key) or _is_forbidden_content_key(key)


def _redact_string(value: str) -> str:
    value = _AUTHORIZATION_PATTERN.sub(r"\1 [REDACTED]", value)
    value = _QUERY_SECRET_PATTERN.sub(r"\1[REDACTED]", value)
    return _TOKEN_PATTERN.sub("[REDACTED]", value)


def _collect_sensitive_values(
    value: Any, *, sensitive_context: bool = False
) -> set[str]:
    """Collect opaque string values nested beneath credential-shaped keys."""
    collected: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            collected.update(
                _collect_sensitive_values(
                    item,
                    sensitive_context=(
                        sensitive_context or _must_redact_key(key)
                    ),
                )
            )
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            collected.update(
                _collect_sensitive_values(
                    item, sensitive_context=sensitive_context
                )
            )
    elif sensitive_context and isinstance(value, str) and value:
        collected.add(value)
    return collected


def _redact(value: Any, sensitive_values: set[str] | None = None) -> Any:
    """Return a JSON-compatible copy with credential material removed.

    Redaction is deliberately two-pass: an opaque value found under a
    credential-shaped key is also removed if the same value is aliased under
    an otherwise innocuous key elsewhere in the payload.
    """
    if sensitive_values is None:
        sensitive_values = _collect_sensitive_values(value)
        sensitive_values.update(
            credential
            for name in _CREDENTIAL_ENV_VARS
            if (credential := os.getenv(name))
        )
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _must_redact_key(key)
                else _redact(item, sensitive_values)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, sensitive_values) for item in value]
    if isinstance(value, (set, frozenset)):
        # Sets are not part of the JSON contract and have no stable order or
        # field context. Drop their opaque contents instead of guessing which
        # members might be credentials.
        return ["[REDACTED]"] if value else []
    if isinstance(value, str):
        redacted = _redact_string(value)
        for secret_value in sorted(sensitive_values, key=len, reverse=True):
            redacted = redacted.replace(secret_value, "[REDACTED]")
        return redacted
    return value


def make_debate_id(
    ticker: str,
    *,
    now: datetime | None = None,
    suffix: str | None = None,
) -> str:
    """Build ``<ticker>-<yyyymmdd>-<short random>`` for a run filename."""
    normalized_ticker = re.sub(
        r"[^a-z0-9.-]+", "-", str(ticker).strip().lower()
    ).strip("-.")
    if not normalized_ticker:
        normalized_ticker = "debate"
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("debate-id timestamp must be timezone-aware")
    short_suffix = suffix or secrets.token_hex(2)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{4,}", short_suffix):
        raise ValueError("debate-id suffix must be at least four safe characters")
    return (
        f"{normalized_ticker}-"
        f"{timestamp.astimezone(timezone.utc):%Y%m%d}-"
        f"{short_suffix.lower()}"
    )


def _validate_payload(event_type: str, payload: dict[str, Any]) -> None:
    def validate_finite_numbers(value: Any, path: str) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite numbers")
        if isinstance(value, Mapping):
            for key, item in value.items():
                validate_finite_numbers(item, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                validate_finite_numbers(item, f"{path}[{index}]")

    validate_finite_numbers(payload, event_type)
    required = _REQUIRED_PAYLOAD_FIELDS.get(event_type)
    if required is None:
        return

    missing = sorted(required.difference(payload))
    if missing:
        raise ValueError(
            f"{event_type} payload is missing required fields: {', '.join(missing)}"
        )

    for field, allowed in _ENUM_PAYLOAD_FIELDS.get(event_type, {}).items():
        if (
            field in payload
            and payload[field] is not None
            and payload[field] not in allowed
        ):
            choices = ", ".join(sorted(str(choice) for choice in allowed))
            raise ValueError(
                f"{event_type}.{field} must be one of: {choices}"
            )

    known_fields = required.union(_OPTIONAL_PAYLOAD_FIELDS.get(event_type, ()))
    for field, value in payload.items():
        # Additive v1 payload fields are accepted without imposing semantics
        # from an unrelated event that happens to use the same field name.
        if field not in known_fields:
            continue
        if value is None:
            if (
                field in required
                and (event_type, field) not in _NULLABLE_REQUIRED_FIELDS
            ):
                raise ValueError(f"{event_type}.{field} must not be null")
            # Optional fields and the M1 usage_ref placeholder may be null.
            continue
        if (
            field in _STRING_FIELDS
            and not (event_type == "thesis_recorded" and field == "confidence")
            and not isinstance(value, str)
        ):
            raise ValueError(f"{event_type}.{field} must be a string")
        if field in _LIST_FIELDS and not isinstance(value, list):
            raise ValueError(f"{event_type}.{field} must be a list")
        if field in _DICT_FIELDS and not isinstance(value, dict):
            raise ValueError(f"{event_type}.{field} must be an object")
        if field in _BOOL_FIELDS and not isinstance(value, bool):
            raise ValueError(f"{event_type}.{field} must be a boolean")
        if field in _INT_FIELDS and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError(
                f"{event_type}.{field} must be a non-negative integer"
            )
        if field in _NUMBER_FIELDS and (
            not isinstance(value, Real)
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise ValueError(
                f"{event_type}.{field} must be a finite non-negative number"
            )

    if event_type == "turn_completed" and payload["usage_ref"] == 0:
        raise ValueError("turn_completed.usage_ref must reference a positive seq")

    if event_type == "debate_started":
        for index, persona in enumerate(payload["personas"]):
            if not isinstance(persona, dict):
                raise ValueError(f"debate_started.personas[{index}] must be an object")
            persona_fields = {
                "name",
                "model_ref",
                "auth_profile",
                "thinking_level",
                "temperament",
            }
            missing_persona = sorted(persona_fields.difference(persona))
            if missing_persona:
                raise ValueError(
                    "debate_started.personas"
                    f"[{index}] is missing: {', '.join(missing_persona)}"
                )
            invalid_persona = sorted(
                field
                for field in persona_fields
                if not isinstance(persona[field], str)
                or not persona[field].strip()
            )
            if invalid_persona:
                raise ValueError(
                    "debate_started.personas"
                    f"[{index}] fields must be non-empty strings: "
                    f"{', '.join(invalid_persona)}"
                )

    if event_type == "data_ready":
        allowed_statuses = {
            "ok",
            "degraded",
            "unavailable",
            "disabled_no_credential",
        }
        for index, source in enumerate(payload["sources"]):
            if not isinstance(source, dict):
                raise ValueError(f"data_ready.sources[{index}] must be an object")
            if "name" not in source or "status" not in source:
                raise ValueError(
                    f"data_ready.sources[{index}] requires name and status"
                )
            if (
                not isinstance(source["name"], str)
                or not source["name"].strip()
            ):
                raise ValueError(
                    f"data_ready.sources[{index}].name must be a non-empty string"
                )
            if source["status"] not in allowed_statuses:
                raise ValueError(
                    f"data_ready.sources[{index}].status is invalid"
                )
            if "warning" in source and not isinstance(source["warning"], str):
                raise ValueError(
                    f"data_ready.sources[{index}].warning must be a string"
                )
        try:
            fetched_at = datetime.fromisoformat(
                payload["fetched_at"].replace("Z", "+00:00")
            )
        except (AttributeError, ValueError) as exc:
            raise ValueError(
                "data_ready.fetched_at must be an ISO-8601 timestamp"
            ) from exc
        if (
            fetched_at.tzinfo is None
            or fetched_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("data_ready.fetched_at must be UTC")

    if event_type == "phase_started":
        has_da = "da_persona" in payload
        if payload["phase"] != "cross_exam" and has_da:
            raise ValueError("da_persona is valid only for cross_exam")


class EventEnvelope(BaseModel):
    """Validated schema-v1 event envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal[1]
    seq: int = Field(ge=1)
    ts: datetime
    debate_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    payload: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def redact_payload(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            copied = dict(value)
            if "payload" in copied:
                copied["payload"] = _redact(copied["payload"])
            return copied
        return value

    @field_validator("seq", mode="before")
    @classmethod
    def validate_sequence_type(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("seq must be an integer")
        return value

    @field_validator("ts")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ts must be timezone-aware")
        if value.utcoffset() != timedelta(0):
            raise ValueError("ts must be UTC")
        return value.astimezone(timezone.utc)

    @field_validator("debate_id", "type")
    @classmethod
    def validate_nonblank_string(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_known_payload(self) -> "EventEnvelope":
        _validate_payload(self.type, self.payload)
        return self

    @field_serializer("ts")
    def serialize_timestamp(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")


class EventLog:
    """Append and immediately flush validated events for one debate."""

    def __init__(
        self,
        debate_id: str,
        path: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(debate_id, str) or not debate_id.strip():
            raise ValueError("debate_id must be a non-empty string")
        self.debate_id = debate_id
        self.path = (
            Path(path).expanduser()
            if path is not None
            else Path(
                os.environ.get(
                    "TINYIC_RUNS_DIR",
                    str(Path.home() / ".tinyic" / "runs"),
                )
            ).expanduser()
            / f"{debate_id}.jsonl"
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stream: TextIO | None = None
        self._next_seq = 1
        self._started = False
        self._terminal = False
        self._existing_state_loaded = False
        self._lock = threading.RLock()

    def __enter__(self) -> "EventLog":
        self._ensure_open()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    @property
    def started(self) -> bool:
        return self._started

    @property
    def terminal(self) -> bool:
        return self._terminal

    def now(self) -> datetime:
        """Return the injectable run clock used for timestamps and durations."""
        return self._clock()

    def _ensure_open(self) -> None:
        with self._lock:
            if self._stream is not None and not self._stream.closed:
                return

            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self._existing_state_loaded:
                self._load_existing_state()
                self._existing_state_loaded = True
            self._stream = self.path.open("a", encoding="utf-8")

    def _load_existing_state(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        with self.path.open("rb") as stream:
            stream.seek(-1, 2)
            if stream.read(1) != b"\n":
                raise ValueError(
                    "cannot append to a crash-truncated event log; "
                    "start a new debate id"
                )
        events = read_event_log(self.path)
        if not events:
            return
        if events[0].debate_id != self.debate_id:
            raise ValueError(
                f"event log belongs to debate {events[0].debate_id}, "
                f"not {self.debate_id}"
            )
        self._started = True
        self._next_seq = events[-1].seq + 1
        self._terminal = events[-1].type in TERMINAL_EVENT_TYPES

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> EventEnvelope:
        """Validate, append, and flush one event, returning its envelope."""
        with self._lock:
            self._ensure_open()
            if self._terminal:
                raise RuntimeError("cannot emit after a terminal event")
            if not self._started and event_type != "debate_started":
                raise ValueError("the first event must be debate_started")
            if self._started and event_type == "debate_started":
                raise ValueError("debate_started has already been emitted")

            now = self._clock()
            event = EventEnvelope.model_validate(
                {
                    "v": SCHEMA_VERSION,
                    "seq": self._next_seq,
                    "ts": now,
                    "debate_id": self.debate_id,
                    "type": event_type,
                    "payload": dict(payload),
                }
            )
            line = json.dumps(
                event.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            assert self._stream is not None
            self._stream.write(line + "\n")
            self._stream.flush()

            self._next_seq += 1
            self._started = True
            if event_type in TERMINAL_EVENT_TYPES:
                self._terminal = True
            return event

    def close(self) -> None:
        with self._lock:
            if self._stream is not None and not self._stream.closed:
                self._stream.close()
            self._stream = None


def read_event_log(path: str | Path) -> list[EventEnvelope]:
    """Read a JSONL log in append order and validate its stream invariants."""
    log_path = Path(path).expanduser()
    events: list[EventEnvelope] = []
    lines = log_path.read_bytes().splitlines(keepends=True)
    for line_number, encoded_line in enumerate(lines, start=1):
        is_unterminated_tail = (
            line_number == len(lines)
            and not encoded_line.endswith((b"\n", b"\r"))
        )
        try:
            line = encoded_line.decode("utf-8")
        except UnicodeDecodeError as exc:
            if is_unterminated_tail:
                # A process can stop halfway through a multibyte codepoint.
                break
            raise ValueError(
                f"invalid UTF-8 at {log_path}:{line_number}: {exc}"
            ) from exc
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            event = EventEnvelope.model_validate(raw)
        except json.JSONDecodeError as exc:
            if is_unterminated_tail:
                # A crash can interrupt the final append between bytes.
                # Prior flushed events remain a valid replay prefix.
                break
            raise ValueError(
                f"invalid event at {log_path}:{line_number}: {exc}"
            ) from exc
        except ValueError as exc:
            raise ValueError(
                f"invalid event at {log_path}:{line_number}: {exc}"
            ) from exc
        events.append(event)

    if not events:
        return []
    if events[0].type != "debate_started":
        raise ValueError("event log must begin with debate_started")

    debate_id = events[0].debate_id
    previous_seq = events[0].seq
    for index, event in enumerate(events[1:], start=1):
        if event.debate_id != debate_id:
            raise ValueError("all events in a log must share one debate_id")
        if event.seq != previous_seq + 1:
            raise ValueError("event sequence numbers must be contiguous")
        if events[index - 1].type in TERMINAL_EVENT_TYPES:
            raise ValueError("terminal event must be the final log entry")
        previous_seq = event.seq

    return events


__all__ = [
    "EventEnvelope",
    "EventLog",
    "SCHEMA_VERSION",
    "TERMINAL_EVENT_TYPES",
    "make_debate_id",
    "read_event_log",
]
