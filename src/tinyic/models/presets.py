"""Committee presets loaded from ``tinyic.toml`` (FR-1.4).

A *preset* is a named committee configuration: a binding for every persona, a
binding for the aggregator (memo/extraction synthesis), and a binding for the
moderator.  The bundled ``default`` preset points one strong model at every
role; other presets express per-persona heterogeneity (a cost/character knob —
FR-1.4) by overriding individual roles on top of a committee-wide default.

``tinyic.toml`` schema
----------------------

::

    # Optional: which preset to use when the caller names none.
    default_preset = "default"

    # Each [presets.<name>] table is one committee configuration. Its top-level
    # keys are the committee-wide default binding, inherited by every role that
    # does not override them.
    [presets.default]
    model = "openai/gpt-5.2"          # required "provider/model" ref
    thinking = "high"                  # off|minimal|low|medium|high|xhigh|max
    auth_profile = "openai:default"    # optional; resolved by M3 auth profiles

    [presets.default.params]           # optional provider-agnostic knobs
    temperature = 0.7

    # A heterogeneous committee: role tables override the committee default and
    # inherit any field they omit (here, thinking/params from the default).
    [presets.mixed]
    model = "openai/gpt-5.2"
    thinking = "high"

    [presets.mixed.aggregator]
    model = "anthropic/claude-opus-4-8"

    [presets.mixed.moderator]
    model = "openai/gpt-5.2"
    thinking = "minimal"           # config-time levels must be model-supported

    # Persona overrides are keyed by snake_case registry name; unlisted personas
    # fall back to the committee default binding.
    [presets.mixed.personas.benjamin_graham]
    model = "deepseek/deepseek-reasoner"

Resolution: a role binding inherits ``thinking``, ``auth_profile``, and
``params`` from the preset's committee default for any field it does not set;
``model`` is required somewhere on the inheritance chain (role or default).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .binding import ModelBinding
from .thinking import ThinkingLevel, UnsupportedThinkingLevelError

#: Built-in fallback when no ``tinyic.toml`` is present: one strong model
#: everywhere (FR-1.4's ``default`` preset), so a committee always resolves.
BUILTIN_DEFAULT_MODEL = "openai/gpt-5.2"
BUILTIN_DEFAULT_THINKING = "high"
DEFAULT_PRESET_NAME = "default"
#: Environment override for the config file location (else ``./tinyic.toml``).
CONFIG_ENV_VAR = "TINYIC_CONFIG"
CONFIG_FILENAME = "tinyic.toml"
DEFAULT_AUTH_CONFIG = {"anthropic": {"policy_guard": True}}


class PresetError(ValueError):
    """Raised when a preset config is malformed or a preset is missing."""

    reason_code = "invalid_preset"


@dataclass(frozen=True)
class BindingSpec:
    """A raw, partial binding: fields left ``None`` inherit the preset default."""

    model: str | None = None
    thinking: str | None = None
    auth_profile: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, where: str) -> "BindingSpec":
        if not isinstance(data, Mapping):
            raise PresetError(f"{where} must be a table, got {type(data).__name__}")
        model = data.get("model")
        if model is not None and not isinstance(model, str):
            raise PresetError(f"{where}.model must be a 'provider/model' string")
        thinking = data.get("thinking")
        if thinking is not None:
            # Validate eagerly so a bad level fails at load, not mid-debate.
            try:
                thinking = ThinkingLevel(thinking).value
            except ValueError as exc:
                raise PresetError(f"{where}.thinking is invalid: {exc}") from exc
        auth_profile = data.get("auth_profile")
        if auth_profile is not None and not isinstance(auth_profile, str):
            raise PresetError(f"{where}.auth_profile must be a string")
        params = data.get("params", {})
        if not isinstance(params, Mapping):
            raise PresetError(f"{where}.params must be a table")
        return cls(
            model=model,
            thinking=thinking,
            auth_profile=auth_profile,
            params=dict(params),
        )

    def merged_over(self, base: "BindingSpec") -> "BindingSpec":
        """Return this spec with ``base``'s values filling any unset field."""
        return BindingSpec(
            model=self.model if self.model is not None else base.model,
            thinking=self.thinking if self.thinking is not None else base.thinking,
            auth_profile=(
                self.auth_profile
                if self.auth_profile is not None
                else base.auth_profile
            ),
            params={**dict(base.params), **dict(self.params)},
        )

    def to_binding(self, *, where: str) -> ModelBinding:
        if not self.model:
            raise PresetError(f"{where} has no model (set it on the role or the preset default)")
        return ModelBinding(
            model_ref=self.model,
            auth_profile=self.auth_profile,
            thinking_level=ThinkingLevel(self.thinking or BUILTIN_DEFAULT_THINKING),
            params=self.params,
        )


# Role table keys reserved for structural sub-tables, not committee defaults.
_ROLE_KEYS = frozenset({"aggregator", "moderator", "personas"})


@dataclass(frozen=True)
class Preset:
    """A named committee configuration (FR-1.4)."""

    name: str
    default: BindingSpec
    personas: Mapping[str, BindingSpec] = field(default_factory=dict)
    aggregator: BindingSpec | None = None
    moderator: BindingSpec | None = None

    def persona_binding(self, registry_name: str) -> ModelBinding:
        """The binding for ``registry_name`` (snake_case), default if unlisted."""
        spec = self.personas.get(registry_name)
        resolved = spec.merged_over(self.default) if spec is not None else self.default
        return resolved.to_binding(where=f"preset {self.name!r} persona {registry_name!r}")

    def aggregator_binding(self) -> ModelBinding:
        spec = self.aggregator.merged_over(self.default) if self.aggregator else self.default
        return spec.to_binding(where=f"preset {self.name!r} aggregator")

    def moderator_binding(self) -> ModelBinding:
        spec = self.moderator.merged_over(self.default) if self.moderator else self.default
        return spec.to_binding(where=f"preset {self.name!r} moderator")

    def with_overrides(
        self, *, model: str | None = None, thinking: str | None = None
    ) -> "Preset":
        """Return a copy with a blunt per-debate model/thinking override.

        A ``--model``/``--thinking`` override (FR-6.1) forces that choice across
        every role, so it is applied to the committee default and to each
        role/persona spec that pins its own value; ``None`` leaves a dimension
        untouched.  Auth profiles and params are preserved.
        """
        if model is None and thinking is None:
            return self
        if thinking is not None:
            thinking = ThinkingLevel(thinking).value

        def override(spec: BindingSpec | None) -> BindingSpec | None:
            if spec is None:
                return None
            return BindingSpec(
                model=model if model is not None else spec.model,
                thinking=thinking if thinking is not None else spec.thinking,
                auth_profile=spec.auth_profile,
                params=spec.params,
            )

        return Preset(
            name=self.name,
            default=override(self.default),  # type: ignore[arg-type]
            personas={
                key: override(spec) for key, spec in self.personas.items()
            },
            aggregator=override(self.aggregator),
            moderator=override(self.moderator),
        )

    @classmethod
    def from_mapping(cls, name: str, data: Mapping[str, Any]) -> "Preset":
        if not isinstance(data, Mapping):
            raise PresetError(f"preset {name!r} must be a table")
        where = f"preset {name!r}"
        default_fields = {k: v for k, v in data.items() if k not in _ROLE_KEYS}
        default = BindingSpec.from_mapping(default_fields, where=where)
        aggregator = (
            BindingSpec.from_mapping(data["aggregator"], where=f"{where}.aggregator")
            if "aggregator" in data
            else None
        )
        moderator = (
            BindingSpec.from_mapping(data["moderator"], where=f"{where}.moderator")
            if "moderator" in data
            else None
        )
        personas_data = data.get("personas", {})
        if not isinstance(personas_data, Mapping):
            raise PresetError(f"{where}.personas must be a table")
        personas = {
            str(persona): BindingSpec.from_mapping(
                spec, where=f"{where}.personas.{persona}"
            )
            for persona, spec in personas_data.items()
        }
        return cls(
            name=name,
            default=default,
            personas=personas,
            aggregator=aggregator,
            moderator=moderator,
        )


def builtin_default_preset() -> Preset:
    """The always-available ``default`` preset (one strong model everywhere)."""
    return Preset(
        name=DEFAULT_PRESET_NAME,
        default=BindingSpec(
            model=BUILTIN_DEFAULT_MODEL, thinking=BUILTIN_DEFAULT_THINKING
        ),
    )


def _preset_role_bindings(preset: Preset) -> list[tuple[str, ModelBinding]]:
    """Every concrete role binding a preset can produce, with a role label.

    Covers the committee default (used by unlisted personas), each explicitly
    listed persona, the aggregator, and the moderator — i.e. every binding whose
    ``thinking`` level a config could get wrong.
    """
    roles: list[tuple[str, ModelBinding]] = []
    if preset.default.model:
        roles.append(
            (
                "committee default",
                preset.default.to_binding(where=f"preset {preset.name!r} default"),
            )
        )
    for persona_name in preset.personas:
        roles.append(
            (f"persona {persona_name!r}", preset.persona_binding(persona_name))
        )
    roles.append(("aggregator", preset.aggregator_binding()))
    roles.append(("moderator", preset.moderator_binding()))
    return roles


def validate_preset_thinking(preset: Preset, *, registry: Any | None = None) -> None:
    """Strict, config-time thinking validation for ``preset`` (FR-1.3).

    Resolves every role/persona binding's thinking level against its model's
    capability profile at *config time* (``runtime=False`` — no nearest-level
    remap): an unsupported level raises :class:`PresetError` naming the preset,
    role, model, and level plus the valid set, so a bad ``tinyic.toml`` fails
    fast at load or committee build instead of being silently remapped mid-debate
    by the per-call adapter. Runtime ``--model``/``--thinking`` overrides keep the
    remap semantics and must therefore bypass this (see ``build_committee``).

    Unknown providers/models are skipped: their capability is unknown here, and
    an unusable provider surfaces at transport construction / ``tinyic doctor``.
    """
    if registry is None:
        from .registry import default_registry

        registry = default_registry()

    for role_label, binding in _preset_role_bindings(preset):
        try:
            registry.resolve_thinking(binding, runtime=False)
        except KeyError:
            # Unknown provider: capability profile is not knowable at config time.
            continue
        except UnsupportedThinkingLevelError as exc:
            valid = ", ".join(level.value for level in exc.supported) or "(none)"
            raise PresetError(
                f"preset {preset.name!r} {role_label} pins model "
                f"{binding.model_ref!r} at thinking "
                f"{binding.thinking_level.value!r}, which that model does not "
                f"support; valid levels: {valid}"
            ) from exc


def _config_path(path: str | Path | None) -> Path | None:
    if path is not None:
        return Path(path).expanduser()
    env_path = os.environ.get(CONFIG_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser()
    candidate = Path.cwd() / CONFIG_FILENAME
    return candidate if candidate.is_file() else None


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Parse ``tinyic.toml`` into ``{presets, default_preset, auth}``.

    A missing file yields the built-in ``default`` preset only, so the model
    layer works before a user writes any config.
    """
    resolved = _config_path(path)
    if resolved is None:
        return {
            "presets": {DEFAULT_PRESET_NAME: builtin_default_preset()},
            "default_preset": DEFAULT_PRESET_NAME,
            "auth": {
                provider: dict(settings)
                for provider, settings in DEFAULT_AUTH_CONFIG.items()
            },
        }
    if not resolved.is_file():
        raise PresetError(f"config file not found: {resolved}")
    with resolved.open("rb") as stream:
        raw = tomllib.load(stream)
    auth_table = raw.get("auth", {})
    if not isinstance(auth_table, Mapping):
        raise PresetError(f"{resolved} [auth] must be a table")
    anthropic_table = auth_table.get("anthropic", {})
    if not isinstance(anthropic_table, Mapping):
        raise PresetError(f"{resolved} [auth.anthropic] must be a table")
    policy_guard = anthropic_table.get("policy_guard", True)
    if not isinstance(policy_guard, bool):
        raise PresetError(
            f"{resolved} auth.anthropic.policy_guard must be a boolean"
        )
    auth = {"anthropic": {"policy_guard": policy_guard}}
    presets_table = raw.get("presets", {})
    if not isinstance(presets_table, Mapping) or not presets_table:
        raise PresetError(f"{resolved} defines no [presets.*] tables")
    presets = {
        str(name): Preset.from_mapping(str(name), spec)
        for name, spec in presets_table.items()
    }
    default_preset = raw.get("default_preset", DEFAULT_PRESET_NAME)
    if default_preset not in presets:
        raise PresetError(
            f"default_preset {default_preset!r} is not a defined preset in {resolved}"
        )
    return {
        "presets": presets,
        "default_preset": str(default_preset),
        "auth": auth,
    }


def load_preset(name: str | None = None, path: str | Path | None = None) -> Preset:
    """Load one preset by ``name`` (or the config's default) from ``tinyic.toml``.

    With no config file and no name, returns the built-in ``default`` preset.
    """
    config = load_config(path)
    presets: dict[str, Preset] = config["presets"]
    chosen = name or config["default_preset"]
    try:
        preset = presets[chosen]
    except KeyError:
        known = ", ".join(sorted(presets)) or "(none)"
        raise PresetError(
            f"unknown preset {chosen!r}; defined presets: {known}"
        ) from None
    # Fail fast on a config-time thinking mismatch (FR-1.3): a bad tinyic.toml
    # raises at load rather than being silently remapped mid-debate.
    validate_preset_thinking(preset)
    return preset


__all__ = [
    "BUILTIN_DEFAULT_MODEL",
    "BUILTIN_DEFAULT_THINKING",
    "BindingSpec",
    "CONFIG_ENV_VAR",
    "CONFIG_FILENAME",
    "DEFAULT_AUTH_CONFIG",
    "DEFAULT_PRESET_NAME",
    "Preset",
    "PresetError",
    "builtin_default_preset",
    "load_config",
    "load_preset",
    "validate_preset_thinking",
]
