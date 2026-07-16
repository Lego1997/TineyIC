"""``ModelBinding`` value object and ``provider/model`` reference parsing.

FR-1.1: every LLM consumer (each persona, moderator, aggregator, extraction,
data-pipeline synthesis) resolves a
``ModelBinding = {model_ref, auth_profile, thinking_level, params}`` where
``model_ref = "provider/model"``.  Bindings come from config presets (FR-1.4)
with per-debate CLI overrides; this module owns the immutable value type
and the reference grammar.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .thinking import ThinkingLevel


class ModelRefError(ValueError):
    """Raised when a ``provider/model`` reference is malformed."""

    reason_code = "invalid_model_ref"


def parse_model_ref(model_ref: str) -> tuple[str, str]:
    """Split ``"provider/model"`` into ``(provider, model)``.

    The split is on the *first* ``/`` only, so model ids that themselves
    contain slashes (e.g. an OpenRouter ``"openrouter/vendor/model"``) keep
    their full identifier.  Both segments must be non-empty; surrounding
    whitespace is trimmed but case is preserved (provider lookup is
    case-insensitive at the registry).
    """
    if not isinstance(model_ref, str):
        raise ModelRefError(
            f"model_ref must be a string, got {type(model_ref).__name__}"
        )
    provider, sep, model = model_ref.strip().partition("/")
    if not sep:
        raise ModelRefError(
            f"model_ref {model_ref!r} must be 'provider/model' (missing '/')"
        )
    provider = provider.strip()
    model = model.strip()
    if not provider:
        raise ModelRefError(f"model_ref {model_ref!r} has an empty provider")
    if not model:
        raise ModelRefError(f"model_ref {model_ref!r} has an empty model")
    return provider, model


@dataclass(frozen=True)
class ModelBinding:
    """An immutable per-consumer model selection (FR-1.1).

    ``thinking_level`` defaults to :attr:`ThinkingLevel.MEDIUM` — a neutral rung
    every bundled reasoning model accepts — but presets and per-debate overrides
    normally set it explicitly.  ``params`` carries provider-agnostic extra
    request knobs (e.g. ``temperature``); it is copied on construction so the
    binding stays immutable.
    """

    model_ref: str
    auth_profile: str | None = None
    thinking_level: ThinkingLevel = ThinkingLevel.MEDIUM
    params: Mapping[str, Any] = field(default_factory=dict)
    provider: str = field(init=False)
    model: str = field(init=False)

    def __post_init__(self) -> None:
        provider, model = parse_model_ref(self.model_ref)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(
            self, "thinking_level", ThinkingLevel(self.thinking_level)
        )
        object.__setattr__(self, "params", dict(self.params))

    def with_thinking(self, level: ThinkingLevel | str) -> "ModelBinding":
        """Return a copy with a different thinking level (per-debate override)."""
        return replace(self, thinking_level=ThinkingLevel(level))

    def with_auth_profile(self, auth_profile: str | None) -> "ModelBinding":
        """Return a copy pinned to a different auth profile."""
        return replace(self, auth_profile=auth_profile)

    def with_params(self, **overrides: Any) -> "ModelBinding":
        """Return a copy with ``params`` shallow-merged with ``overrides``."""
        merged = {**self.params, **overrides}
        return replace(self, params=merged)


__all__ = ["ModelBinding", "ModelRefError", "parse_model_ref"]
