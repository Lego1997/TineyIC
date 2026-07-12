"""Normalized thinking/reasoning ladder and per-model capability profiles.

Implements the FR-1.3 "thinking ladder": one canonical enum
(``off | minimal | low | medium | high | xhigh | max``) that every provider
adapter capability-gates per model via its ``thinking_profile(model)``.

Resolution semantics (FR-1.3):

* config-time unsupported level -> raise, listing the valid set;
* runtime override unsupported  -> remap to the nearest supported level;
* model rejects the parameter    -> omit it entirely (empty ``supported``).

A :class:`ThinkingProfile` owns a model's supported levels plus a *mapping
strategy*: a callable turning a resolved level into the request parameters an
adapter merges onto the wire.  The concrete OpenAI/Anthropic/Gemini/… wire
shapes are supplied by the provider adapters (M2 stage 2); this module only
provides the ladder, the resolution rules, and reusable strategy builders.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from enum import Enum
from typing import Any


class ThinkingLevel(str, Enum):
    """The single normalized reasoning-effort ladder (FR-1.3)."""

    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"

    @classmethod
    def _missing_(cls, value: object):
        """Accept ladder levels by value or name, case-insensitively."""
        if isinstance(value, str):
            return cls.__members__.get(value.strip().upper())
        return None

    @property
    def rank(self) -> int:
        """Position on the ladder (``OFF`` = 0 … ``MAX`` = 6)."""
        return _LADDER_INDEX[self]


# Ascending capability order; the sole source of truth for "nearest" remapping.
THINKING_LADDER: tuple[ThinkingLevel, ...] = (
    ThinkingLevel.OFF,
    ThinkingLevel.MINIMAL,
    ThinkingLevel.LOW,
    ThinkingLevel.MEDIUM,
    ThinkingLevel.HIGH,
    ThinkingLevel.XHIGH,
    ThinkingLevel.MAX,
)
_LADDER_INDEX: dict[ThinkingLevel, int] = {
    level: index for index, level in enumerate(THINKING_LADDER)
}

# A mapping strategy renders a (supported) level into request parameters to
# merge onto the outgoing call.  Returning ``{}`` means "send nothing".
ThinkingStrategy = Callable[[ThinkingLevel], dict[str, Any]]


def _omit_strategy(level: ThinkingLevel) -> dict[str, Any]:
    return {}


def nearest_supported(
    level: ThinkingLevel, supported: Iterable[ThinkingLevel]
) -> ThinkingLevel:
    """Return the supported level closest to ``level`` on the ladder.

    Ties resolve to the *higher* (stronger) level: the thinking dial expresses
    desired reasoning effort, so an ambiguous remap honors that intent rather
    than silently reducing it.  ``supported`` must be non-empty.
    """
    candidates = tuple(supported)
    if not candidates:
        raise ValueError("nearest_supported requires a non-empty supported set")
    target = level.rank
    return min(candidates, key=lambda option: (abs(option.rank - target), -option.rank))


class UnsupportedThinkingLevelError(ValueError):
    """A configured thinking level is not accepted by the target model.

    Carries the requested level and the valid set so callers (and the
    ``tinyic doctor`` reason code ``unsupported_thinking_level``) can report a
    precise, actionable message.
    """

    reason_code = "unsupported_thinking_level"

    def __init__(
        self, requested: ThinkingLevel, supported: Iterable[ThinkingLevel]
    ) -> None:
        self.requested = requested
        self.supported: tuple[ThinkingLevel, ...] = tuple(supported)
        valid = ", ".join(level.value for level in self.supported) or "(none)"
        super().__init__(
            f"thinking level {requested.value!r} is not supported; "
            f"valid levels: {valid}"
        )


class ThinkingResolution:
    """The outcome of resolving a requested level against a profile."""

    __slots__ = ("requested", "effective", "params", "remapped")

    def __init__(
        self,
        requested: ThinkingLevel,
        effective: ThinkingLevel | None,
        params: dict[str, Any],
        *,
        remapped: bool = False,
    ) -> None:
        self.requested = requested
        #: ``None`` means the parameter is omitted entirely.
        self.effective = effective
        #: Request parameters to merge on the wire (``{}`` when omitted).
        self.params = params
        #: ``True`` when a runtime override was remapped to a nearer level.
        self.remapped = remapped

    @property
    def omitted(self) -> bool:
        """Whether the thinking parameter is dropped for this call."""
        return self.effective is None

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            "ThinkingResolution("
            f"requested={self.requested.value!r}, "
            f"effective={None if self.effective is None else self.effective.value!r}, "
            f"params={self.params!r}, remapped={self.remapped})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ThinkingResolution):
            return NotImplemented
        return (
            self.requested == other.requested
            and self.effective == other.effective
            and self.params == other.params
            and self.remapped == other.remapped
        )


class ThinkingProfile:
    """A model's thinking capability: supported levels + a mapping strategy.

    An empty ``supported`` set marks a model that rejects the thinking
    parameter altogether; :meth:`resolve` then always omits it.
    """

    __slots__ = ("supported", "_strategy")

    def __init__(
        self,
        supported: Iterable[ThinkingLevel] = (),
        strategy: ThinkingStrategy | None = None,
    ) -> None:
        present = set(supported)
        # Store in canonical ladder order, deduplicated.
        self.supported: tuple[ThinkingLevel, ...] = tuple(
            level for level in THINKING_LADDER if level in present
        )
        self._strategy: ThinkingStrategy = strategy or _omit_strategy

    # -- constructors for the common per-provider mapping shapes -----------

    @classmethod
    def omitted(cls) -> "ThinkingProfile":
        """A profile for models with no thinking knob (always omit)."""
        return cls(supported=(), strategy=_omit_strategy)

    @classmethod
    def effort(
        cls, param_key: str, mapping: Mapping[ThinkingLevel, str]
    ) -> "ThinkingProfile":
        """Flat effort string, e.g. ``{"reasoning_effort": "high"}``."""
        table = dict(mapping)

        def render(level: ThinkingLevel) -> dict[str, Any]:
            return {param_key: table[level]}

        return cls(supported=table.keys(), strategy=render)

    @classmethod
    def nested_effort(
        cls, path: tuple[str, ...], mapping: Mapping[ThinkingLevel, str]
    ) -> "ThinkingProfile":
        """Nested effort, e.g. Responses ``{"reasoning": {"effort": "high"}}``."""
        if not path:
            raise ValueError("nested_effort requires a non-empty path")
        table = dict(mapping)

        def render(level: ThinkingLevel) -> dict[str, Any]:
            root: dict[str, Any] = {}
            cursor = root
            for key in path[:-1]:
                nxt: dict[str, Any] = {}
                cursor[key] = nxt
                cursor = nxt
            cursor[path[-1]] = table[level]
            return root

        return cls(supported=table.keys(), strategy=render)

    @classmethod
    def budget(
        cls, param_key: str, mapping: Mapping[ThinkingLevel, int]
    ) -> "ThinkingProfile":
        """Token budget, e.g. Anthropic/Gemini ``{"budget_tokens": 8192}``."""
        table = dict(mapping)

        def render(level: ThinkingLevel) -> dict[str, Any]:
            return {param_key: table[level]}

        return cls(supported=table.keys(), strategy=render)

    @classmethod
    def flag(
        cls,
        param_key: str,
        *,
        supported: Iterable[ThinkingLevel] = THINKING_LADDER,
        off: ThinkingLevel = ThinkingLevel.OFF,
    ) -> "ThinkingProfile":
        """Boolean toggle, e.g. Ollama ``{"think": True}`` (``off`` -> False)."""

        def render(level: ThinkingLevel) -> dict[str, Any]:
            return {param_key: level != off}

        return cls(supported=supported, strategy=render)

    # -- query & resolution ------------------------------------------------

    @property
    def accepts_thinking(self) -> bool:
        """Whether the model has any thinking knob at all."""
        return bool(self.supported)

    def supports(self, level: ThinkingLevel) -> bool:
        return level in self.supported

    def resolve(
        self, level: ThinkingLevel, *, runtime: bool = False
    ) -> ThinkingResolution:
        """Resolve ``level`` against this profile per FR-1.3.

        ``runtime=True`` marks an ad-hoc per-debate override, which remaps an
        unsupported level to the nearest supported one instead of raising.
        """
        level = ThinkingLevel(level)
        if not self.supported:
            # Model rejects the parameter -> omit entirely, never an error.
            return ThinkingResolution(level, None, {})
        if level in self.supported:
            return ThinkingResolution(level, level, dict(self._strategy(level)))
        if runtime:
            effective = nearest_supported(level, self.supported)
            return ThinkingResolution(
                level, effective, dict(self._strategy(effective)), remapped=True
            )
        raise UnsupportedThinkingLevelError(level, self.supported)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        levels = ", ".join(level.value for level in self.supported) or "omit"
        return f"ThinkingProfile(supported=[{levels}])"


__all__ = [
    "THINKING_LADDER",
    "ThinkingLevel",
    "ThinkingProfile",
    "ThinkingResolution",
    "ThinkingStrategy",
    "UnsupportedThinkingLevelError",
    "nearest_supported",
]
