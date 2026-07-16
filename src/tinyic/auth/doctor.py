"""Reason-coded auth diagnostics consumed by ``tinyic doctor`` and onboarding.

Doctor result schema v1
=======================

``DoctorReport.to_dict()`` returns exactly this machine-readable shape::

    {
      "schema_version": 1,
      "ok": true,
      "generated_at": "2026-07-13T00:00:00.000Z",
      "preset": "default",
      "probes": [
        {
          "provider": "openai",
          "lane": "subscription",
          "auth_profile": "openai:chatgpt",
          "model_ref": "openai/gpt-5.6-sol",
          "required": true,
          "status": "ok",
          "reason_code": "ok",
          "message": "Codex subscription runtime is available."
        }
      ]
    }

The schema is deliberately narrow: there is no unrestricted details field and
no account identity, command output, exception text, token, or credential
field. ``ok`` describes whether every *required selected-preset binding* has a
viable route. Optional provider/lane probes may warn without making the report
unhealthy. Within schema v1, new reason codes are additive and consumers must
treat unknown codes as non-secret diagnostic labels.

Report-level failures that occur before any real provider lane can be probed
use a reserved pseudo-provider ``"tinyic"`` (never a model provider) with one
of two pseudo-lanes:

* ``"configuration"`` — the selected preset / ``tinyic.toml`` could not be
  loaded or validated (reason ``invalid_preset``), or preset probe collection
  itself failed (reason ``probe_failed``);
* ``"credential_store"`` — the auth-profile manager / credential store could
  not be constructed (reason ``probe_failed``).

Onboarding wizards must recognize these ``tinyic``/``configuration`` and
``tinyic``/``credential_store`` probes as whole-run diagnostics and render them
as such, rather than as a fixable provider/lane the user can add a credential
to.
"""

from __future__ import annotations

import json
import inspect
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypeAlias

from .live_probe import live_token_probe


DOCTOR_SCHEMA_VERSION = 1
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]*$")

# These are the provider/lane combinations shipped by the v1 registry.  Keeping
# the list explicit makes schema-v1 output predictable for the onboarding TUI;
# a selected custom provider is still added as an ``api_key`` lane below.
#
# The reserved pseudo-provider ``"tinyic"`` is intentionally absent here: it is
# emitted only for report-level failures (pseudo-lanes ``"configuration"`` and
# ``"credential_store"``; see the module docstring) that precede any real
# provider-lane probe, so it is never one of these enumerable registry lanes.
_BUILTIN_LANES = (
    ("openai", "api_key"),
    ("openai", "subscription"),
    ("anthropic", "api_key"),
    ("anthropic", "subscription"),
    ("google", "api_key"),
    ("grok", "api_key"),
    ("grok", "subscription"),
    ("kimi", "api_key"),
    ("ollama", "local"),
)

_KNOWN_REASONS = frozenset(
    {
        "ok",
        "missing_credential",
        "expired",
        "invalid_credential",
        "unsupported_thinking_level",
        "policy_disabled",
        "runtime_unavailable",
        "network_unreachable",
        "lane_incompatible",
        "subscription_required",
        "usage_limited",
        "subscription_inactive",
        "probe_failed",
        "unknown_provider",
        "invalid_preset",
        "unknown_persona",
    }
)

_FIXED_MESSAGES = {
    "missing_credential": "No usable credential is configured for this lane.",
    "expired": "Every configured credential for this lane is expired.",
    "invalid_credential": "The configured credential is not valid for this lane.",
    "unsupported_thinking_level": (
        "The selected thinking level is unsupported by this model."
    ),
    "policy_disabled": (
        "The Anthropic subscription lane is disabled by policy_guard."
    ),
    "runtime_unavailable": "The required local provider runtime is unavailable.",
    "lane_incompatible": "The selected auth profile belongs to another lane.",
    "subscription_required": "This model requires a subscription auth profile.",
    "usage_limited": "Every usable credential for this lane is usage-limited.",
    "subscription_inactive": (
        "The provider reports no active subscription for this account."
    ),
    "probe_failed": "The provider readiness check failed.",
    "unknown_provider": "The selected provider is not registered.",
    "invalid_preset": "The selected preset configuration is invalid.",
    "unknown_persona": "The configured committee names an unknown persona.",
}

ReasonProbe: TypeAlias = Callable[..., object]
RuntimeLocator: TypeAlias = Callable[[str], str | None]
LiveProbe: TypeAlias = Callable[[Any, Any | None], object]


class ProbeStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


def _iso_millis(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("doctor clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


@dataclass(frozen=True)
class ProbeResult:
    """One safe local/live readiness observation for a provider lane."""

    provider: str
    lane: str
    auth_profile: str | None
    model_ref: str | None
    required: bool
    status: ProbeStatus
    reason_code: str
    message: str

    def __post_init__(self) -> None:
        if not self.provider or not _SAFE_CODE.fullmatch(self.provider):
            raise ValueError("provider must be a lowercase reason-code identifier")
        if not self.lane or not _SAFE_CODE.fullmatch(self.lane):
            raise ValueError("lane must be a lowercase reason-code identifier")
        if not _SAFE_CODE.fullmatch(self.reason_code):
            raise ValueError("reason_code must be a lowercase identifier")
        if not isinstance(self.status, ProbeStatus):
            object.__setattr__(self, "status", ProbeStatus(self.status))
        if not self.message or "\n" in self.message:
            raise ValueError("doctor messages must be non-empty single lines")

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "lane": self.lane,
            "auth_profile": self.auth_profile,
            "model_ref": self.model_ref,
            "required": self.required,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class DoctorReport:
    """Stable v1 doctor document; safe for JSON stdout and the onboard wizard."""

    ok: bool
    generated_at: str
    preset: str
    probes: tuple[ProbeResult, ...]
    schema_version: int = DOCTOR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "probes", tuple(self.probes))
        if self.schema_version != DOCTOR_SCHEMA_VERSION:
            raise ValueError("unsupported doctor schema version")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "generated_at": self.generated_at,
            "preset": self.preset,
            "probes": [probe.to_dict() for probe in self.probes],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":")
        )


def build_report(
    probes: Iterable[ProbeResult],
    *,
    preset: str,
    clock: Callable[[], datetime] | None = None,
) -> DoctorReport:
    """Build a deterministic report and derive readiness from required probes."""
    ordered = tuple(
        sorted(
            probes,
            key=lambda probe: (
                probe.provider,
                probe.lane,
                probe.auth_profile or "",
                probe.model_ref or "",
                not probe.required,
            ),
        )
    )
    ready = all(
        probe.status is ProbeStatus.OK
        for probe in ordered
        if probe.required
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    return DoctorReport(
        ok=ready,
        generated_at=_iso_millis(now),
        preset=preset,
        probes=ordered,
    )


def run_doctor(
    *,
    preset: str | None = None,
    config_path: str | None = None,
    live: bool = False,
    live_optional: bool = False,
    manager=None,
    clock: Callable[[], datetime] | None = None,
    openai_probe: ReasonProbe | None = None,
    anthropic_probe: ReasonProbe | None = None,
    grok_probe: ReasonProbe | None = None,
    live_probe: LiveProbe | None = None,
    runtime_locator: RuntimeLocator | None = None,
    registry=None,
) -> DoctorReport:
    """Run local auth diagnostics; perform completions only with ``live=True``.

    The injected callables are the offline-test/onboarding seam.  Reason
    probes return a stable reason code (or an object whose ``reason`` has one);
    their message/exception text is never copied into the report.  ``live_probe``
    receives the selected binding and auth candidate and must perform at most a
    one-token verification.  The production hook asks for exactly ``OK`` and
    sets the normalized adapter token cap to one where the runtime supports it.
    """
    from tinyic.models.presets import load_config

    try:
        config = load_config(config_path)
        chosen = preset or config["default_preset"]
        selected = config["presets"][chosen]
    except Exception:
        chosen = preset or "default"
        return build_report(
            [
                _result(
                    provider="tinyic",
                    lane="configuration",
                    required=True,
                    reason="invalid_preset",
                )
            ],
            preset=chosen,
            clock=clock,
        )

    if manager is None:
        try:
            from .manager import AuthManager

            manager = AuthManager.from_config(config_path)
        except Exception:
            return build_report(
                [
                    _result(
                        provider="tinyic",
                        lane="credential_store",
                        required=True,
                        reason="probe_failed",
                    )
                ],
                preset=chosen,
                clock=clock,
            )

    if registry is None:
        from tinyic.models.registry import default_registry

        registry = default_registry()

    context = _ProbeContext(
        manager=manager,
        registry=registry,
        live=live,
        live_optional=live_optional,
        openai_probe=openai_probe or _default_openai_probe,
        anthropic_probe=anthropic_probe or _default_anthropic_probe,
        grok_probe=grok_probe or _default_grok_probe,
        live_probe=live_probe or live_token_probe,
        runtime_locator=runtime_locator or shutil.which,
    )
    try:
        probes = context.collect(selected)
        committee = config.get("committee") or []
        if committee:
            from tinyic.personas.registry import registry_snapshot

            available = registry_snapshot(warn=False)
            if any(name not in available for name in committee):
                probes = (
                    *probes,
                    _result(
                        provider="tinyic",
                        lane="committee",
                        required=False,
                        reason="unknown_persona",
                    ),
                )
    except Exception as exc:
        from tinyic.models.presets import PresetError

        reason = "invalid_preset" if isinstance(exc, PresetError) else "probe_failed"
        probes = (
            _result(
                provider="tinyic",
                lane="configuration",
                required=True,
                reason=reason,
            ),
        )
    return build_report(probes, preset=chosen, clock=clock)


def _reason_value(value: object) -> str:
    """Reduce a probe result to an allowlisted code, never its message."""
    reason = getattr(value, "reason", value)
    reason = getattr(reason, "value", reason)
    if not isinstance(reason, str) or reason not in _KNOWN_REASONS:
        return "probe_failed"
    return reason


def _provider_identifier(value: object) -> str:
    """Normalize a registry label into the schema's non-secret identifier form."""
    if not isinstance(value, str):
        return "unknown"
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")
    if not normalized:
        return "unknown"
    if not normalized[0].isalpha():
        normalized = f"provider_{normalized}"
    return normalized


def _message(reason: str, provider: str, lane: str, *, live: bool = False) -> str:
    if reason != "ok":
        if live and reason == "probe_failed":
            return "The live one-token check failed."
        if reason == "policy_disabled" and provider == "grok":
            return "The Grok subscription lane is disabled by policy_guard."
        return _FIXED_MESSAGES[reason]
    if live:
        return "The live one-token check passed."
    if lane == "api_key":
        return "API-key credential is available."
    if provider == "openai" and lane == "subscription":
        return "Codex ChatGPT subscription runtime is available."
    if provider == "anthropic" and lane == "subscription":
        return "Claude Code subscription runtime is available."
    if provider == "grok" and lane == "subscription":
        return "Grok subscription sign-in is available."
    return "Local runtime is available."


def _result(
    *,
    provider: str,
    lane: str,
    required: bool,
    reason: str,
    auth_profile: str | None = None,
    model_ref: str | None = None,
    live: bool = False,
) -> ProbeResult:
    safe_reason = reason if reason in _KNOWN_REASONS else "probe_failed"
    return ProbeResult(
        provider=provider,
        lane=lane,
        auth_profile=auth_profile,
        model_ref=model_ref,
        required=required,
        status=(
            ProbeStatus.OK
            if safe_reason == "ok"
            else ProbeStatus.ERROR
            if required
            else ProbeStatus.WARNING
        ),
        reason_code=safe_reason,
        message=_message(safe_reason, provider, lane, live=live),
    )


def _default_openai_probe() -> object:
    """Prove app-server + ChatGPT auth without rotating Codex credentials."""
    from .openai import probe_codex_auth

    # Forcing the runtime branch is deliberate.  A file-only read could prove a
    # credential exists but not that the sanctioned app-server transport can
    # run.  account/read itself uses refreshToken=false and honors Codex's own
    # cli_auth_credentials_store setting; TinyIC never writes that store.
    return probe_codex_auth(credential_store="keyring")


def _default_grok_probe() -> object:
    """Probe the official grok CLI's ~/.grok/auth.json without touching it."""
    from .grok import probe_grok_auth

    return probe_grok_auth()


def _default_anthropic_probe(candidate=None) -> object:
    """Use the Claude runtime's safe local probe when that adapter is installed."""
    try:
        from tinyic.models.adapters.claude_runtime import probe_claude_runtime
    except (ImportError, AttributeError):
        return "runtime_unavailable"
    try:
        return probe_claude_runtime(credentials=candidate)
    except Exception:
        return "runtime_unavailable"


def _call_candidate_probe(probe: ReasonProbe, candidate) -> object:
    """Call an injected probe with a candidate only when its signature accepts it.

    Existing onboarding/tests use zero-argument probes.  Candidate-aware Claude
    probes additionally need the selected named setup-token profile, because
    that secret is intentionally absent from the parent environment.  Signature
    binding avoids catching a ``TypeError`` raised *inside* the probe and then
    accidentally invoking it twice.
    """

    try:
        signature = inspect.signature(probe)
        signature.bind(candidate)
    except (TypeError, ValueError):
        return probe()
    return probe(candidate)


class _ProbeContext:
    """One doctor run with memoized local runtime observations."""

    def __init__(
        self,
        *,
        manager,
        registry,
        live: bool,
        live_optional: bool = False,
        openai_probe: ReasonProbe,
        anthropic_probe: ReasonProbe,
        grok_probe: ReasonProbe,
        live_probe: LiveProbe,
        runtime_locator: RuntimeLocator,
    ) -> None:
        self.manager = manager
        self.registry = registry
        self.live = live
        self.live_optional = live_optional
        self.openai_probe = openai_probe
        self.anthropic_probe = anthropic_probe
        self.grok_probe = grok_probe
        self.live_probe = live_probe
        self.runtime_locator = runtime_locator
        self._runtime_reasons: dict[str, str] = {}

    def collect(self, preset) -> tuple[ProbeResult, ...]:
        bindings = self._preset_bindings(preset)
        required = tuple(self._probe_binding(binding) for binding in bindings)
        covered = {(probe.provider, probe.lane) for probe in required}
        selected_providers = {
            _provider_identifier(binding.provider) for binding in bindings
        }
        lanes = list(_BUILTIN_LANES)
        for provider in sorted(selected_providers):
            if not any(name == provider for name, _lane in lanes):
                lanes.append((provider, "api_key"))
        optional = tuple(
            self._probe_optional_lane(provider, lane)
            for provider, lane in lanes
            if (provider, lane) not in covered
        )
        return (*required, *optional)

    @staticmethod
    def _preset_bindings(preset) -> tuple[Any, ...]:
        """Return each distinct concrete auth/thinking binding in a preset."""
        values: list[Any] = []
        if preset.default.model:
            values.append(
                preset.default.to_binding(where=f"preset {preset.name!r} default")
            )
        values.extend(
            preset.persona_binding(name) for name in sorted(preset.personas)
        )
        values.extend((preset.aggregator_binding(), preset.moderator_binding()))
        unique: dict[tuple[object, ...], Any] = {}
        for binding in values:
            key = (
                binding.model_ref,
                binding.auth_profile,
                binding.thinking_level.value,
                tuple(
                    sorted(
                        (str(key), repr(value))
                        for key, value in binding.params.items()
                    )
                ),
            )
            unique.setdefault(key, binding)
        return tuple(unique.values())

    def _probe_binding(self, binding) -> ProbeResult:
        from tinyic.auth.manager import AuthResolutionError
        from tinyic.models.thinking import UnsupportedThinkingLevelError

        provider_name = _provider_identifier(binding.provider)
        try:
            provider = self.registry.get(binding.provider)
        except Exception:
            return _result(
                provider=provider_name,
                lane="api_key",
                required=True,
                reason="unknown_provider",
                auth_profile=self._known_profile_ref(binding),
                model_ref=binding.model_ref,
            )
        lane = self._preferred_lane(binding, provider)
        try:
            provider.resolve_thinking(
                binding.model, binding.thinking_level, runtime=False
            )
        except UnsupportedThinkingLevelError:
            return _result(
                provider=provider_name,
                lane=lane,
                required=True,
                reason="unsupported_thinking_level",
                auth_profile=self._known_profile_ref(binding),
                model_ref=binding.model_ref,
            )
        except Exception:
            return _result(
                provider=provider_name,
                lane=lane,
                required=True,
                reason="probe_failed",
                auth_profile=self._known_profile_ref(binding),
                model_ref=binding.model_ref,
            )

        if provider_name == "ollama":
            reason = self._local_runtime_reason("ollama")
            if reason == "ok" and self.live:
                reason = self._run_live(binding, None)
            return _result(
                provider=provider_name,
                lane="local",
                required=True,
                reason=reason,
                model_ref=binding.model_ref,
                live=self.live,
            )

        try:
            candidates = self.manager.candidates(
                binding,
                subscription_only=provider.is_subscription_only(binding.model),
            )
        except AuthResolutionError as exc:
            return _result(
                provider=provider_name,
                lane=lane,
                required=True,
                reason=exc.reason_code,
                auth_profile=self._known_profile_ref(binding),
                model_ref=binding.model_ref,
            )
        except Exception:
            return _result(
                provider=provider_name,
                lane=lane,
                required=True,
                reason="probe_failed",
                auth_profile=self._known_profile_ref(binding),
                model_ref=binding.model_ref,
            )

        first_failure: tuple[str, Any] | None = None
        for candidate in candidates:
            reason = self._candidate_reason(candidate)
            if reason == "ok" and self.live:
                reason = self._run_live(binding, candidate)
            if reason == "ok":
                return _result(
                    provider=provider_name,
                    lane=candidate.lane.value,
                    required=True,
                    reason="ok",
                    auth_profile=candidate.ref,
                    model_ref=binding.model_ref,
                    live=self.live,
                )
            if first_failure is None:
                first_failure = (reason, candidate)
        reason, candidate = first_failure or ("missing_credential", None)
        return _result(
            provider=provider_name,
            lane=candidate.lane.value if candidate is not None else lane,
            required=True,
            reason=reason,
            auth_profile=(
                candidate.ref
                if candidate is not None
                else self._known_profile_ref(binding)
            ),
            model_ref=binding.model_ref,
            live=self.live,
        )

    def _probe_optional_lane(self, provider_name: str, lane: str) -> ProbeResult:
        if lane == "local":
            reason = self._local_runtime_reason(provider_name)
            return _result(
                provider=provider_name,
                lane=lane,
                required=False,
                reason=reason,
            )
        if (
            provider_name == "anthropic"
            and lane == "subscription"
            and not self.manager.anthropic_policy_guard
        ):
            return _result(
                provider=provider_name,
                lane=lane,
                required=False,
                reason="policy_disabled",
            )
        if (
            provider_name == "grok"
            and lane == "subscription"
            and not getattr(self.manager, "grok_policy_guard", True)
        ):
            return _result(
                provider=provider_name,
                lane=lane,
                required=False,
                reason="policy_disabled",
            )

        binding = self._inventory_binding(provider_name)
        if binding is None:
            return _result(
                provider=provider_name,
                lane=lane,
                required=False,
                reason="unknown_provider",
            )
        candidates: tuple[Any, ...] = ()
        resolution_reason = "missing_credential"
        try:
            candidates = tuple(
                candidate
                for candidate in self.manager.candidates(binding)
                if candidate.lane.value == lane
            )
        except Exception as exc:
            reason = getattr(exc, "reason_code", "probe_failed")
            resolution_reason = (
                reason if reason in _KNOWN_REASONS else "probe_failed"
            )

        # Optional lanes stay local-only under plain ``--live``; a live one-token
        # completion is escalated only when the caller opts in with
        # ``live_optional`` (the onboarding all-lanes detection path). Required
        # preset bindings keep live escalation unconditionally.
        optional_live = self.live and self.live_optional
        first_failure: tuple[str, Any] | None = None
        for candidate in candidates:
            reason = self._candidate_reason(candidate)
            if reason == "ok" and optional_live:
                reason = self._run_live(binding, candidate)
            if reason == "ok":
                return _result(
                    provider=provider_name,
                    lane=lane,
                    required=False,
                    reason="ok",
                    auth_profile=candidate.ref,
                    model_ref=binding.model_ref,
                    live=optional_live,
                )
            if first_failure is None:
                first_failure = (reason, candidate)
        if first_failure is not None:
            reason, candidate = first_failure
            return _result(
                provider=provider_name,
                lane=lane,
                required=False,
                reason=reason,
                auth_profile=candidate.ref,
                model_ref=binding.model_ref,
                live=optional_live,
            )

        # Subscription probes double as the onboard wizard's non-mutating
        # detection API, so an external official login can be reported even
        # before a TinyIC route-marker profile has been persisted.
        if lane == "subscription":
            reason = self._subscription_runtime_reason(provider_name)
            return _result(
                provider=provider_name,
                lane=lane,
                required=False,
                reason=reason,
            )
        return _result(
            provider=provider_name,
            lane=lane,
            required=False,
            reason=resolution_reason,
        )

    def _inventory_binding(self, provider_name: str):
        from tinyic.models import ModelBinding, ThinkingLevel

        try:
            provider = self.registry.get(provider_name)
        except Exception:
            return None
        catalog = provider.catalog()
        model = next(
            (
                value
                for value in catalog
                if not provider.is_subscription_only(value)
            ),
            catalog[0] if catalog else "doctor-probe",
        )
        profile = provider.thinking_profile(model)
        thinking = profile.supported[0] if profile.supported else ThinkingLevel.MEDIUM
        return ModelBinding(f"{provider_name}/{model}", thinking_level=thinking)

    def _preferred_lane(self, binding, provider) -> str:
        if provider.is_subscription_only(binding.model):
            return "subscription"
        if binding.auth_profile:
            try:
                profile = self.manager.store.get(binding.auth_profile)
            except Exception:
                profile = None
            if (
                profile is not None
                and profile.provider == binding.provider.casefold()
            ):
                return profile.lane.value
        return "api_key"

    def _known_profile_ref(self, binding) -> str | None:
        """Return only a validated stored ref, never arbitrary config text."""
        if not binding.auth_profile:
            return None
        try:
            profile = self.manager.store.get(binding.auth_profile)
        except Exception:
            return None
        if profile is None or profile.provider != binding.provider.casefold():
            return None
        return profile.ref

    def _candidate_reason(self, candidate) -> str:
        if candidate.lane.value == "api_key":
            return "ok"
        if (
            candidate.provider == "anthropic"
            and not self.manager.anthropic_policy_guard
        ):
            return "policy_disabled"
        if candidate.provider == "grok":
            if not getattr(self.manager, "grok_policy_guard", True):
                return "policy_disabled"
            from tinyic.auth.profiles import ProfileKind

            if getattr(candidate, "kind", None) is ProfileKind.GROK_OAUTH:
                # TinyIC-owned tokens: probe the stored document itself, not
                # the CLI's file — the two credentials are independent.
                from .grok import probe_grok_profile_secret

                try:
                    return _reason_value(
                        probe_grok_profile_secret(candidate(candidate.ref))
                    )
                except Exception:
                    return "probe_failed"
        return self._subscription_runtime_reason(
            candidate.provider, candidate=candidate
        )

    def _subscription_runtime_reason(self, provider: str, *, candidate=None) -> str:
        cache_key = (
            provider
            if candidate is None
            else f"{provider}:{getattr(candidate, 'ref', 'candidate')}"
        )
        if cache_key in self._runtime_reasons:
            return self._runtime_reasons[cache_key]
        if provider == "openai":
            if self.runtime_locator("codex") is None:
                reason = "runtime_unavailable"
            else:
                try:
                    reason = _reason_value(self.openai_probe())
                except Exception:
                    reason = "probe_failed"
        elif provider == "anthropic":
            if not self.manager.anthropic_policy_guard:
                reason = "policy_disabled"
            else:
                try:
                    reason = _reason_value(
                        _call_candidate_probe(self.anthropic_probe, candidate)
                    )
                except Exception:
                    reason = "probe_failed"
        elif provider == "grok":
            if not getattr(self.manager, "grok_policy_guard", True):
                reason = "policy_disabled"
            else:
                try:
                    reason = _reason_value(self.grok_probe())
                except Exception:
                    reason = "probe_failed"
        else:
            reason = "lane_incompatible"
        self._runtime_reasons[cache_key] = reason
        return reason

    def _local_runtime_reason(self, command: str) -> str:
        try:
            return (
                "ok"
                if self.runtime_locator(command) is not None
                else "runtime_unavailable"
            )
        except Exception:
            return "probe_failed"

    def _run_live(self, binding, candidate) -> str:
        try:
            return _reason_value(self.live_probe(binding, candidate))
        except Exception as exc:
            reason = getattr(exc, "reason_code", None)
            return reason if reason in _KNOWN_REASONS else "probe_failed"


def render_human(report: DoctorReport) -> str:
    """Render a compact secret-free human summary."""
    heading = "TinyIC doctor: ready" if report.ok else "TinyIC doctor: setup needed"
    lines = [heading, f"Preset: {report.preset}"]
    markers = {
        ProbeStatus.OK: "OK",
        ProbeStatus.WARNING: "WARN",
        ProbeStatus.ERROR: "ERROR",
    }
    for probe in report.probes:
        profile = f" ({probe.auth_profile})" if probe.auth_profile else ""
        lines.append(
            f"[{markers[probe.status]}] {probe.provider}/{probe.lane}{profile}: "
            f"{probe.reason_code} — {probe.message}"
        )
    return "\n".join(lines)


__all__ = [
    "DOCTOR_SCHEMA_VERSION",
    "DoctorReport",
    "ProbeResult",
    "ProbeStatus",
    "build_report",
    "render_human",
    "run_doctor",
]
