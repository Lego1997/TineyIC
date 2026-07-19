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
    model = "openai/gpt-5.6-sol"      # required "provider/model" ref
    thinking = "high"                  # off|minimal|low|medium|high|xhigh|max
    auth_profile = "openai:default"    # optional; resolved by M3 auth profiles

    [presets.default.params]           # optional provider-agnostic knobs
    temperature = 0.7

    # A heterogeneous committee: role tables override the committee default and
    # inherit any field they omit (here, thinking/params from the default).
    [presets.mixed]
    model = "openai/gpt-5.6-sol"
    thinking = "high"

    [presets.mixed.aggregator]
    model = "anthropic/claude-opus-4-8"

    [presets.mixed.moderator]
    model = "openai/gpt-5.6-luna"
    thinking = "minimal"           # config-time levels must be model-supported

    # Persona overrides are keyed by snake_case registry name; unlisted personas
    # fall back to the committee default binding.
    [presets.mixed.personas.benjamin_graham]
    model = "kimi/kimi-k2.6"

Resolution: a role binding inherits ``thinking``, ``auth_profile``, and
``params`` from the preset's committee default for any field it does not set;
``model`` is required somewhere on the inheritance chain (role or default).

Config precedence (v2.1)
------------------------

Two files feed ``load_config``:

1. the **base** config — the explicit ``path`` argument, else ``$TINYIC_CONFIG``,
   else ``./tinyic.toml``, else the built-in default preset.  The repo's
   ``tinyic.toml`` is the shipped baseline and is never written by TinyIC.
2. the **user overlay** — ``$TINYIC_USER_CONFIG`` else ``~/.tinyic/tinyic.toml``.
   When present it is deep-merged *over* the base (tables merge recursively,
   scalars/arrays in the overlay win), so a key set in the overlay always beats
   the same key in the base.  The onboarding wizard persists the user's chosen
   committee default binding here via :func:`set_user_default_binding` and
   writes **only** this file.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .binding import ModelBinding
from .thinking import ThinkingLevel, UnsupportedThinkingLevelError
from tinyic.constants import MAX_PERSONAS, MIN_PERSONAS

#: Built-in fallback when no ``tinyic.toml`` is present: one strong model
#: everywhere (FR-1.4's ``default`` preset), so a committee always resolves.
BUILTIN_DEFAULT_MODEL = "openai/gpt-5.6-sol"
BUILTIN_DEFAULT_THINKING = "high"
DEFAULT_PRESET_NAME = "default"
#: Environment override for the config file location (else ``./tinyic.toml``).
CONFIG_ENV_VAR = "TINYIC_CONFIG"
CONFIG_FILENAME = "tinyic.toml"
#: Environment override for the user overlay location (else ``~/.tinyic/tinyic.toml``).
USER_CONFIG_ENV_VAR = "TINYIC_USER_CONFIG"
#: Legal/policy kill switches, one per subscription-capable provider lane.
DEFAULT_AUTH_CONFIG = {
    "anthropic": {"policy_guard": True},
    "grok": {"policy_guard": True},
}


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
_ROLE_KEYS = frozenset({"aggregator", "moderator", "personas", "caps"})

# Canonical debate-phase keys accepted in a ``[presets.<name>.caps]`` table
# (FR-4.2). Bounds on the values are enforced by the moderator's ``resolve_caps``
# so the numeric policy lives in one place.
_CAP_PHASES = frozenset({"opening", "cross_exam", "rebuttal", "verdict"})


def _parse_caps(
    data: Any, *, where: str
) -> dict[str, int] | None:
    """Parse an optional ``[presets.<name>.caps]`` table into ``{phase: int}``.

    A typo'd phase key or a non-integer value raises at config load; the
    ``[MIN_EXCHANGES, MAX_EXCHANGES]`` range check is the moderator's, applied
    when the committee's caps are realized.
    """
    if data is None:
        return None
    if not isinstance(data, Mapping):
        raise PresetError(f"{where} must be a table")
    caps: dict[str, int] = {}
    for phase, value in data.items():
        if phase not in _CAP_PHASES:
            raise PresetError(
                f"{where}.{phase} is not a debate phase; valid phases: "
                f"{', '.join(sorted(_CAP_PHASES))}"
            )
        if not isinstance(value, int) or isinstance(value, bool):
            raise PresetError(f"{where}.{phase} must be an integer")
        caps[str(phase)] = value
    return caps


@dataclass(frozen=True)
class Preset:
    """A named committee configuration (FR-1.4)."""

    name: str
    default: BindingSpec
    personas: Mapping[str, BindingSpec] = field(default_factory=dict)
    aggregator: BindingSpec | None = None
    moderator: BindingSpec | None = None
    #: Optional per-phase exchange caps (FR-4.2); ``None`` means the moderator's
    #: built-in defaults. Values are range-checked when the committee is built.
    caps: Mapping[str, int] | None = None

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
            # A model/thinking override does not touch protocol caps.
            caps=self.caps,
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
        caps = _parse_caps(data.get("caps"), where=f"{where}.caps")
        return cls(
            name=name,
            default=default,
            personas=personas,
            aggregator=aggregator,
            moderator=moderator,
            caps=caps,
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


def user_config_path() -> Path:
    """The user overlay location: ``$TINYIC_USER_CONFIG`` else ``~/.tinyic/tinyic.toml``."""
    env_path = os.environ.get(USER_CONFIG_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".tinyic" / CONFIG_FILENAME


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``overlay`` over ``base``: tables recurse, everything else replaces."""
    merged: dict[str, Any] = {key: value for key, value in base.items()}
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _builtin_raw_config() -> dict[str, Any]:
    """The raw-mapping equivalent of :func:`builtin_default_preset`."""
    return {
        "default_preset": DEFAULT_PRESET_NAME,
        "presets": {
            DEFAULT_PRESET_NAME: {
                "model": BUILTIN_DEFAULT_MODEL,
                "thinking": BUILTIN_DEFAULT_THINKING,
            }
        },
    }


def _parse_committee(value: Any, *, where: str) -> list[str] | None:
    """Validate the optional top-level ``committee = [...]`` overlay key."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PresetError(f"{where} committee must be an array of persona slugs")
    names = [item.strip() for item in value]
    if any(not item for item in names):
        raise PresetError(f"{where} committee entries must be non-empty persona slugs")
    if len(names) != len(set(names)):
        raise PresetError(f"{where} committee cannot contain duplicate personas")
    if not MIN_PERSONAS <= len(names) <= MAX_PERSONAS:
        raise PresetError(
            f"{where} committee needs {MIN_PERSONAS}-{MAX_PERSONAS} members, got {len(names)}"
        )
    return names


def parse_committee(value: Any, *, where: str = "user config overlay") -> list[str] | None:
    """Validate a ``committee = [...]`` value (2-6 unique non-empty slugs)."""
    return _parse_committee(value, where=where)


def _load_toml(resolved: Path, *, what: str) -> dict[str, Any]:
    try:
        with resolved.open("rb") as stream:
            return tomllib.load(stream)
    except tomllib.TOMLDecodeError as exc:
        raise PresetError(f"{what} {resolved} is not valid TOML: {exc}") from exc


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Parse ``tinyic.toml`` into ``{presets, default_preset, auth}``.

    A missing base file yields the built-in ``default`` preset, so the model
    layer works before a user writes any config.  The user overlay (see the
    module docstring) is deep-merged over the base whenever it exists.
    """
    resolved = _config_path(path)
    if resolved is None:
        raw: dict[str, Any] = _builtin_raw_config()
    else:
        if not resolved.is_file():
            raise PresetError(f"config file not found: {resolved}")
        raw = _load_toml(resolved, what="config file")
    overlay_path = user_config_path()
    if overlay_path.is_file():
        raw = _deep_merge(raw, _load_toml(overlay_path, what="user config overlay"))
    where = str(resolved) if resolved is not None else "the TinyIC config"
    auth_table = raw.get("auth", {})
    if not isinstance(auth_table, Mapping):
        raise PresetError(f"{where} [auth] must be a table")
    auth: dict[str, dict[str, bool]] = {}
    for provider in DEFAULT_AUTH_CONFIG:
        provider_table = auth_table.get(provider, {})
        if not isinstance(provider_table, Mapping):
            raise PresetError(f"{where} [auth.{provider}] must be a table")
        policy_guard = provider_table.get("policy_guard", True)
        if not isinstance(policy_guard, bool):
            raise PresetError(
                f"{where} auth.{provider}.policy_guard must be a boolean"
            )
        auth[provider] = {"policy_guard": policy_guard}
    presets_table = raw.get("presets", {})
    if not isinstance(presets_table, Mapping) or not presets_table:
        raise PresetError(f"{where} defines no [presets.*] tables")
    presets = {
        str(name): Preset.from_mapping(str(name), spec)
        for name, spec in presets_table.items()
    }
    default_preset = raw.get("default_preset", DEFAULT_PRESET_NAME)
    if default_preset not in presets:
        raise PresetError(
            f"default_preset {default_preset!r} is not a defined preset in {where}"
        )
    committee = _parse_committee(raw.get("committee"), where=where)
    return {
        "presets": presets,
        "default_preset": str(default_preset),
        "auth": auth,
        "committee": committee,
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


# --------------------------------------------------------------------------- #
# The user overlay writer (the only TinyIC-written config file)
# --------------------------------------------------------------------------- #

_BARE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(key: str) -> str:
    return key if _BARE_TOML_KEY.fullmatch(key) else json.dumps(key)


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(item) for item in value) + "]"
    raise PresetError(
        f"cannot serialize {type(value).__name__} into the user config overlay"
    )


def _dump_toml(table: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> list[str]:
    """Serialize the narrow scalar/table shape the overlay uses (no writer dep)."""
    lines: list[str] = []
    subtables: list[tuple[str, Mapping[str, Any]]] = []
    for key, value in table.items():
        if isinstance(value, Mapping):
            subtables.append((str(key), value))
        else:
            lines.append(f"{_toml_key(str(key))} = {_toml_scalar(value)}")
    for key, value in subtables:
        path = (*prefix, key)
        if lines and lines[-1] != "":
            lines.append("")
        lines.append("[" + ".".join(_toml_key(part) for part in path) + "]")
        lines.extend(_dump_toml(value, path))
    return lines


_OVERLAY_HEADER = (
    "# TinyIC user overlay — written by `tinyic onboard`; deep-merged over\n"
    "# the shipped tinyic.toml (overlay wins). Safe to edit or delete.\n"
)


def _write_overlay(path: Path, raw: Mapping[str, Any]) -> Path:
    """Serialize *raw* to the user overlay via a staged tmp + atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(_dump_toml(raw)) + "\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_OVERLAY_HEADER + body, encoding="utf-8")
    os.replace(tmp, path)
    return path


def set_user_default_binding(
    model_ref: str, *, thinking: str | None = None
) -> Path:
    """Persist ``model_ref`` as the committee default in the *user overlay*.

    Writes ``[presets.default] model`` (and ``thinking`` when given) into the
    overlay at :func:`user_config_path`, preserving any other overlay content.
    The repo/base ``tinyic.toml`` is never touched — the overlay deep-merges
    over it at load (overlay wins; see the module docstring).  Returns the
    written path.
    """
    from .binding import parse_model_ref

    parse_model_ref(model_ref)  # validate "provider/model" before writing
    if thinking is not None:
        thinking = ThinkingLevel(thinking).value
    path = user_config_path()
    raw: dict[str, Any] = {}
    if path.is_file():
        raw = _load_toml(path, what="user config overlay")
    presets_table = raw.get("presets", {})
    if not isinstance(presets_table, Mapping):
        raise PresetError(f"user config overlay {path} [presets] must be a table")
    raw["presets"] = dict(presets_table)
    default_table = raw["presets"].get(DEFAULT_PRESET_NAME, {})
    if not isinstance(default_table, Mapping):
        raise PresetError(
            f"user config overlay {path} [presets.{DEFAULT_PRESET_NAME}] must be a table"
        )
    default_table = dict(default_table)
    default_table["model"] = model_ref
    if thinking is not None:
        default_table["thinking"] = thinking
    raw["presets"][DEFAULT_PRESET_NAME] = default_table
    return _write_overlay(path, raw)


def set_user_committee(names: list[str] | None) -> Path:
    """Persist or clear the top-level ``committee`` key in the user overlay.

    Shape-validated here (2-6 unique slugs); registry resolvability is the
    caller's job (``resolve_personas`` re-checks at debate time anyway).
    """
    validated = parse_committee(list(names)) if names is not None else None
    path = user_config_path()
    raw: dict[str, Any] = {}
    if path.is_file():
        raw = _load_toml(path, what="user config overlay")
    if validated is None:
        raw.pop("committee", None)
    else:
        raw["committee"] = list(validated)
    return _write_overlay(path, raw)


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
    "USER_CONFIG_ENV_VAR",
    "builtin_default_preset",
    "load_config",
    "load_preset",
    "parse_committee",
    "set_user_committee",
    "set_user_default_binding",
    "user_config_path",
    "validate_preset_thinking",
]
