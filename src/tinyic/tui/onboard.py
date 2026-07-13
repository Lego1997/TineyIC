"""``tinyic onboard`` — the OpenClaw-pattern onboarding wizard (FR-2.4).

A full-screen Textual flow that walks a fresh install from *no configured auth*
to *verified, persisted routes* without ever implementing an OAuth flow of its
own, echoing a secret to the screen, or overwriting a working config. It is the
interactive twin of the headless ``tinyic doctor`` seam and consumes the very
same reason-coded probe API (:mod:`tinyic.auth.doctor`) as its data source.

Design — a pure controller under a thin renderer
------------------------------------------------
All wizard logic lives in :class:`OnboardController`, a plain object with no
Textual dependency: it holds the five-step state machine (DETECT → CHOOSE →
CONNECT → VERIFY → SUMMARY), talks to the injectable seams, and owns every
persistence and verify-gating decision. :class:`OnboardApp` is a pure view over
it — it draws the controller's state across a scrollable body and a status line
and routes keys through a single ``on_key`` (matching the Town Hall house
style), but it never decides *what* a keystroke means beyond "move / activate /
back". This split keeps the whole flow unit-testable offline while the real
network calls (device-code login, the live 1-token verify) stay behind injected
fakes in tests and only fire for real when a human runs ``tinyic onboard`` or
under the ``live_api`` marker.

The five steps (FR-2.4)
-----------------------
1. **DETECT** — run the doctor report (injectable) and show, per provider × lane,
   what already works: env keys, a ``~/.codex/auth.json`` sign-in, a Claude Code
   login, a local Ollama. Report-level ``tinyic``/``configuration`` and
   ``tinyic``/``credential_store`` probes are surfaced as whole-run diagnostics,
   never as a fixable provider lane.
2. **CHOOSE** — per provider, a first-class two-branch chooser: *Use your
   subscription* vs *Enter an API key*, each with "best for" copy. The Anthropic
   subscription branch carries the policy note (own-login reuse, draws from
   Pro/Max limits, ``policy_guard`` state) and is disabled with a clear message
   when ``policy_guard`` is off. Skipping a provider is first-class.
3. **CONNECT** — subscription branches invoke the auth module's login
   entrypoints (OpenAI device-code preferred: render the user code + URL and
   poll the completion via injected callbacks; Anthropic reuses the user's own
   Claude Code login — TinyIC never runs a Claude.ai login). The API-key branch
   takes a *masked* input and persists it through the profiles store.
4. **VERIFY** — completion is gated on a per-lane verification probe (injectable;
   a real one-token live check only under ``live_api``). A failing verify **never
   persists the route and never overwrites an existing working profile**: the
   ephemeral candidate is checked *before* anything is written.
5. **SUMMARY** — a final card mirroring each persona's resolved binding
   ``{model, auth lane, thinking}`` from the default preset, where credentials
   were stored (keyring vs file), and the next-step hint.

Re-running is an idempotent verify-and-repair pass: providers that already work
default to *keep current*, and only what the user explicitly reconfigures is
touched.
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static

from tinyic.auth.doctor import DoctorReport, ProbeResult
from tinyic.auth.live_probe import live_token_probe
from tinyic.auth.manager import (
    DEFAULT_PROVIDER_ENV_REFS,
    AuthCandidate,
    AuthManager,
    AuthResolutionError,
)
from tinyic.auth.profiles import AuthLane, AuthProfile, ProfileKind

__all__ = [
    "OnboardApp",
    "OnboardController",
    "OnboardScreen",
    "ProviderPlan",
    "LaneStatus",
    "Choice",
    "SummaryRow",
    "OnboardSummary",
    "build_controller",
    "run_onboard",
]


# --------------------------------------------------------------------------- #
# Copy — "best for" branch text, policy note, provider labels, reason hints
# --------------------------------------------------------------------------- #

PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "xai": "xAI",
    "deepseek": "DeepSeek",
    "ollama": "Ollama (local)",
}

# "Best for:" copy keyed by (provider, lane). Missing pairs fall back to the
# generic api-key line in :func:`best_for`.
BEST_FOR: dict[tuple[str, str], str] = {
    ("openai", "subscription"): (
        "Best for: ChatGPT Plus / Pro / Team subscribers. Reuses your existing "
        "plan through the official Codex runtime — no per-token bill. Usage is "
        "metered as messages per rolling 5-hour window."
    ),
    ("openai", "api_key"): (
        "Best for: pay-as-you-go, headless / CI, and high-volume runs. Billed "
        "per token via the OpenAI API. Some flagship subscription-only models "
        "are unavailable on this lane."
    ),
    ("anthropic", "subscription"): (
        "Best for: Claude Pro / Max subscribers. Reuses your own signed-in "
        "Claude Code login — identity-honest, no separate sign-in to run."
    ),
    ("anthropic", "api_key"): (
        "Best for: pay-as-you-go Claude access. Billed per token via the "
        "Anthropic API."
    ),
    ("google", "api_key"): (
        "Best for: Gemini access via a Google AI Studio API key. Billed per "
        "token."
    ),
    ("xai", "api_key"): (
        "Best for: Grok access (and X / Twitter sentiment) via an xAI API key. "
        "Billed per token."
    ),
    ("deepseek", "api_key"): (
        "Best for: low-cost reasoning via a DeepSeek API key. Billed per token."
    ),
    ("ollama", "local"): (
        "Best for: fully local, private, offline inference. No credentials are "
        "stored — just a running Ollama."
    ),
}

# Always shown on the Anthropic subscription branch (task + PRD FR-2.3).
ANTHROPIC_POLICY_NOTE = (
    "Policy: uses your own logged-in Claude Code via official plumbing only "
    "(Claude Agent SDK / `claude -p`). Per Anthropic's June 15 2026 policy this "
    "draws from your Pro/Max limits. TinyIC never runs a Claude.ai login of its "
    "own. Governed by the `policy_guard` switch in tinyic.toml."
)
POLICY_DISABLED_MSG = (
    "Disabled by policy_guard (auth.anthropic.policy_guard = false). The "
    "Anthropic subscription lane is turned off — use an API key instead."
)

# Secret-free, human explanations for the reason codes a verify/connect can
# surface. Kept local so the wizard never leaks the doctor's internal message
# table or any runtime/exception text.
REASON_HINTS: dict[str, str] = {
    "ok": "Verified.",
    "missing_credential": "No usable credential was provided.",
    "invalid_credential": "The credential was not accepted.",
    "expired": "The credential is expired.",
    "probe_failed": "The verification check did not pass.",
    "runtime_unavailable": "The required runtime is unavailable — is it installed and signed in?",
    "policy_disabled": "This lane is disabled by policy_guard.",
    "usage_limited": "The credential is usage-limited right now.",
    "subscription_required": "This route needs a subscription profile.",
    "lane_incompatible": "That profile belongs to another lane.",
    "unsupported_thinking_level": "The model rejects the selected thinking level.",
    "unknown_provider": "The provider is not registered.",
}

_PROVIDER_ORDER = ("openai", "anthropic", "google", "xai", "deepseek", "ollama")
_STATUS_MARK = {"ok": ("✓", "bold green"), "warning": ("▲", "bold yellow"),
                "error": ("✗", "bold red")}

NEXT_STEP_HINT = "uv run tinyic debate AAPL"


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


def best_for(provider: str, lane: str) -> str:
    text = BEST_FOR.get((provider, lane))
    if text:
        return text
    if lane == "api_key":
        return (
            f"Best for: pay-as-you-go {provider_label(provider)} access via an "
            "API key. Billed per token."
        )
    if lane == "local":
        return "Best for: local, offline inference. No credentials are stored."
    return "Best for: subscription reuse through official plumbing."


def reason_hint(reason: str | None) -> str:
    if not reason:
        return ""
    return REASON_HINTS.get(reason, "The verification check did not pass.")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

class OnboardScreen(str, Enum):
    """The wizard's discrete steps (VERIFY is a transient action within CONNECT)."""

    DETECT = "detect"
    CHOOSE = "choose"
    CONNECT_KEY = "connect_key"
    CONNECT_SUB = "connect_sub"
    SUMMARY = "summary"


@dataclass(frozen=True)
class LaneStatus:
    """One provider-lane's current readiness, mirrored from a doctor probe."""

    lane: str
    status: str  # "ok" | "warning" | "error"
    reason_code: str
    message: str
    auth_profile: str | None
    model_ref: str | None
    required: bool

    @classmethod
    def from_probe(cls, probe: ProbeResult) -> "LaneStatus":
        return cls(
            lane=probe.lane,
            status=probe.status.value,
            reason_code=probe.reason_code,
            message=probe.message,
            auth_profile=probe.auth_profile,
            model_ref=probe.model_ref,
            required=probe.required,
        )


@dataclass
class ProviderPlan:
    """One provider's detected lanes plus the user's in-wizard decisions."""

    provider: str
    lanes: dict[str, LaneStatus] = field(default_factory=dict)
    outcome: str | None = None  # "verified" | "skipped" | "kept" | None
    persisted_ref: str | None = None
    persisted_lane: str | None = None
    error: str | None = None
    verifying: bool = False

    @property
    def already_ok(self) -> bool:
        """True when this provider already has a working lane (verified or detected)."""
        if self.outcome in {"verified", "kept"}:
            return True
        return any(lane.status == "ok" for lane in self.lanes.values())


@dataclass
class Choice:
    """A single selectable menu row on a menu screen."""

    id: str
    label: str
    enabled: bool = True
    detail: str = ""
    note: str | None = None
    disabled_reason: str | None = None


@dataclass(frozen=True)
class SummaryRow:
    """One persona's resolved binding for the final summary card."""

    persona: str
    model_ref: str
    thinking: str
    lane: str | None
    auth_ref: str | None
    reason: str | None


@dataclass(frozen=True)
class OnboardSummary:
    rows: tuple[SummaryRow, ...]
    backend: str
    preset: str


# --------------------------------------------------------------------------- #
# Injectable seams — the real network/login/verify calls (fakes in tests)
# --------------------------------------------------------------------------- #

def _reason_str(value: object) -> str:
    """Reduce a probe/enum/bool/str verify result to a plain reason code."""
    if isinstance(value, bool):
        return "ok" if value else "probe_failed"
    reason = getattr(value, "reason", value)
    reason = getattr(reason, "value", reason)
    return reason if isinstance(reason, str) and reason else "probe_failed"


def _default_openai_login_factory():
    """Open one official Codex login session (delegates all OAuth to app-server)."""
    from tinyic.auth.openai import CodexLoginSession

    return CodexLoginSession()


# --------------------------------------------------------------------------- #
# The controller — the pure, Textual-free state machine
# --------------------------------------------------------------------------- #

class OnboardController:
    """The onboarding state machine, persistence, and verify gating.

    All side effects go through injected seams so the whole flow is exercised
    offline:

    * ``report_factory()`` → a :class:`~tinyic.auth.doctor.DoctorReport` (DETECT).
    * ``verify_probe(binding, candidate)`` → a reason code (VERIFY gate).
    * ``openai_login_factory()`` → a context-managed login session exposing
      ``start(mode)`` / ``wait(challenge)`` (the OpenAI device-code CONNECT).

    Credentials are only ever persisted through ``manager.store``; the wizard
    never writes a credential file itself and never keeps or echoes a secret.
    """

    def __init__(
        self,
        *,
        manager: AuthManager,
        report_factory: Callable[[], DoctorReport],
        config_path: str | None = None,
        verify_probe: Callable[[object, object], object] | None = None,
        openai_login_factory: Callable[[], object] | None = None,
    ) -> None:
        self.manager = manager
        self.store = manager.store
        self.config_path = config_path
        self.report_factory = report_factory
        # The VERIFY gate shares the doctor's one live-check seam (live_probe.py),
        # so the interactive wizard and headless ``doctor`` can never drift.
        self.verify_probe = verify_probe or live_token_probe
        self.openai_login_factory = openai_login_factory or _default_openai_login_factory

        self.screen = OnboardScreen.DETECT
        self.report: DoctorReport | None = None
        self.plans: list[ProviderPlan] = []
        self.diagnostics: list[LaneStatus] = []
        self.summary: OnboardSummary | None = None

        self._cursor = 0
        self._highlight = 0
        self._sub_stage = "menu"  # "menu" | "device_wait"

        # Device-code login is kept alive between start() and wait().
        self._login_cm: object | None = None
        self._login_session: object | None = None
        self._challenge: object | None = None

    # -- lifecycle -------------------------------------------------------- #

    def start(self) -> None:
        """Run the initial detection and land on the DETECT overview."""
        self.refresh_detection()
        self.screen = OnboardScreen.DETECT
        self._cursor = 0
        self._highlight = 0

    def refresh_detection(self) -> None:
        """(Re-)run the doctor report and rebuild the provider plans.

        Prior in-wizard outcomes are preserved per provider so a mid-flow
        re-detect never discards a route the user just verified.
        """
        prior = {
            plan.provider: (
                plan.outcome,
                plan.persisted_ref,
                plan.persisted_lane,
            )
            for plan in self.plans
        }
        report = self.report_factory()
        self.report = report
        by_provider: dict[str, dict[str, LaneStatus]] = {}
        diagnostics: list[LaneStatus] = []
        for probe in report.probes:
            status = LaneStatus.from_probe(probe)
            if probe.provider == "tinyic":
                diagnostics.append(status)
                continue
            by_provider.setdefault(probe.provider, {})[probe.lane] = status
        plans: list[ProviderPlan] = []
        for provider in self._ordered_providers(by_provider):
            plan = ProviderPlan(provider=provider, lanes=by_provider[provider])
            if provider in prior:
                plan.outcome, plan.persisted_ref, plan.persisted_lane = prior[provider]
            plans.append(plan)
        self.plans = plans
        self.diagnostics = diagnostics

    @staticmethod
    def _ordered_providers(names: Mapping[str, object]) -> list[str]:
        known = [name for name in _PROVIDER_ORDER if name in names]
        extra = sorted(name for name in names if name not in _PROVIDER_ORDER)
        return known + extra

    # -- current view ----------------------------------------------------- #

    def current_plan(self) -> ProviderPlan | None:
        if 0 <= self._cursor < len(self.plans):
            return self.plans[self._cursor]
        return None

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def highlight(self) -> int:
        return self._highlight

    @property
    def sub_stage(self) -> str:
        return self._sub_stage

    @property
    def challenge(self) -> object | None:
        return self._challenge

    def choices(self) -> list[Choice]:
        """The selectable rows for the current menu screen (empty on input screens)."""
        if self.screen is OnboardScreen.DETECT:
            label = "Begin setup →" if self.plans else "No providers to configure →"
            return [Choice("begin", label)]
        if self.screen is OnboardScreen.CHOOSE:
            plan = self.current_plan()
            return self._choose_choices(plan) if plan else []
        if self.screen is OnboardScreen.CONNECT_SUB and self._sub_stage == "menu":
            plan = self.current_plan()
            return self._sub_choices(plan) if plan else []
        if self.screen is OnboardScreen.SUMMARY:
            return [Choice("redetect", "Re-run detection"), Choice("finish", "Finish")]
        return []

    def current_choice(self) -> Choice | None:
        choices = self.choices()
        if 0 <= self._highlight < len(choices):
            return choices[self._highlight]
        return None

    def _branches(self, plan: ProviderPlan) -> list[str]:
        order: list[str] = []
        if "subscription" in plan.lanes:
            order.append("subscription")
        if "api_key" in plan.lanes:
            order.append("api_key")
        if "local" in plan.lanes:
            order.append("local")
        order.append("skip")
        return order

    def _choose_choices(self, plan: ProviderPlan) -> list[Choice]:
        choices: list[Choice] = []
        for branch in self._branches(plan):
            if branch == "subscription":
                anthropic_off = (
                    plan.provider == "anthropic"
                    and not self.manager.anthropic_policy_guard
                )
                choices.append(
                    Choice(
                        "subscription",
                        "Use your subscription",
                        enabled=not anthropic_off,
                        detail=best_for(plan.provider, "subscription"),
                        note=ANTHROPIC_POLICY_NOTE
                        if plan.provider == "anthropic"
                        else None,
                        disabled_reason=POLICY_DISABLED_MSG if anthropic_off else None,
                    )
                )
            elif branch == "api_key":
                choices.append(
                    Choice(
                        "api_key",
                        "Enter an API key",
                        detail=best_for(plan.provider, "api_key"),
                    )
                )
            elif branch == "local":
                choices.append(
                    Choice(
                        "local",
                        "Use the local runtime",
                        detail=best_for(plan.provider, "local"),
                    )
                )
            else:  # skip
                label = "Keep current setup" if plan.already_ok else "Skip this provider"
                choices.append(
                    Choice(
                        "skip",
                        label,
                        detail="Leave this provider unchanged. Skipping is fine.",
                    )
                )
        return choices

    def _sub_choices(self, plan: ProviderPlan) -> list[Choice]:
        choices: list[Choice] = []
        if plan.provider == "openai":
            detected = plan.lanes.get("subscription")
            if detected is not None and detected.status == "ok":
                choices.append(
                    Choice(
                        "reuse",
                        "Reuse existing Codex ChatGPT sign-in",
                        detail=(
                            "A working ~/.codex sign-in was detected. Reuse it "
                            "read-through — TinyIC never rewrites Codex credentials."
                        ),
                    )
                )
            choices.append(
                Choice(
                    "device",
                    "Sign in with ChatGPT (device code)",
                    detail=(
                        "Opens the official Codex device-code login; authorize in "
                        "a browser — nothing to paste back here."
                    ),
                )
            )
        elif plan.provider == "anthropic":
            choices.append(
                Choice(
                    "runtime",
                    "Use my Claude Code login",
                    detail=best_for("anthropic", "subscription"),
                    note=ANTHROPIC_POLICY_NOTE,
                )
            )
        elif plan.provider == "ollama":
            choices.append(
                Choice(
                    "ollama",
                    "Verify the local Ollama runtime",
                    detail=best_for("ollama", "local"),
                )
            )
        choices.append(Choice("back", "← Back"))
        return choices

    # -- navigation ------------------------------------------------------- #

    def move(self, delta: int) -> None:
        count = len(self.choices())
        if count:
            self._highlight = max(0, min(count - 1, self._highlight + delta))

    def select_index(self, index: int) -> None:
        if 0 <= index < len(self.choices()):
            self._highlight = index

    def activate(self) -> tuple[str, object | None]:
        """Act on the highlighted choice.

        Returns ``(kind, payload)`` telling the view how to follow up:

        * ``("navigate", None)`` — a pure, synchronous transition (just redraw).
        * ``("verify", fn)`` — run ``fn`` (a zero-arg verify+persist) off the UI
          thread; it advances on success or sets ``plan.error`` on failure.
        * ``("device", None)`` — a device-code login was started; run
          :meth:`finish_subscription_login` off the UI thread.
        * ``("exit", None)`` — leave the wizard.
        """
        if self.screen is OnboardScreen.DETECT:
            if not self.plans:
                self.screen = OnboardScreen.SUMMARY
                self._build_summary()
            else:
                self._cursor = 0
                self._enter_choose()
            return ("navigate", None)
        if self.screen is OnboardScreen.CHOOSE:
            choice = self.current_choice()
            return self._activate_choose(choice) if choice else ("navigate", None)
        if self.screen is OnboardScreen.CONNECT_SUB and self._sub_stage == "menu":
            choice = self.current_choice()
            return self._activate_sub(choice) if choice else ("navigate", None)
        if self.screen is OnboardScreen.SUMMARY:
            choice = self.current_choice()
            if choice and choice.id == "finish":
                return ("exit", None)
            if choice and choice.id == "redetect":
                self.refresh_detection()
                self.screen = OnboardScreen.DETECT
                self._highlight = 0
            return ("navigate", None)
        return ("navigate", None)

    def _activate_choose(self, choice: Choice) -> tuple[str, object | None]:
        plan = self.current_plan()
        if plan is None:
            return ("navigate", None)
        plan.error = None
        if choice.id == "skip":
            plan.outcome = "kept" if plan.already_ok else "skipped"
            self._advance()
        elif choice.id == "api_key":
            self.screen = OnboardScreen.CONNECT_KEY
        elif choice.id in {"subscription", "local"}:
            if not choice.enabled:
                # Surface the short reason chip; the full policy sentence still
                # shows in the disabled branch's detail (see ``_menu_lines``).
                plan.error = "policy_disabled"
                return ("navigate", None)
            self.screen = OnboardScreen.CONNECT_SUB
            self._sub_stage = "menu"
            self._highlight = 0
        return ("navigate", None)

    def _activate_sub(self, choice: Choice) -> tuple[str, object | None]:
        plan = self.current_plan()
        if plan is None:
            return ("navigate", None)
        if choice.id == "back":
            self.screen = OnboardScreen.CHOOSE
            self._enter_choose(reset_highlight=False)
            return ("navigate", None)
        plan.error = None
        if choice.id == "reuse":
            return ("verify", self.confirm_readthrough)
        if choice.id == "runtime":
            return ("verify", self.confirm_runtime)
        if choice.id == "ollama":
            return ("verify", self.confirm_ollama)
        if choice.id == "device":
            try:
                self.start_subscription_login()
            except Exception:
                self._close_login()
                plan.error = "runtime_unavailable"
                return ("navigate", None)
            return ("device", None)
        return ("navigate", None)

    def back(self) -> bool:
        """Back out one step. Returns ``False`` only when the app should exit."""
        screen = self.screen
        if screen is OnboardScreen.DETECT:
            return False
        if screen is OnboardScreen.CHOOSE:
            if self._cursor > 0:
                self._cursor -= 1
                self._enter_choose()
            else:
                self.screen = OnboardScreen.DETECT
                self._highlight = 0
            return True
        if screen is OnboardScreen.CONNECT_KEY:
            self.screen = OnboardScreen.CHOOSE
            self._enter_choose(reset_highlight=False)
            return True
        if screen is OnboardScreen.CONNECT_SUB:
            if self._sub_stage == "device_wait":
                self._close_login()
                self._sub_stage = "menu"
                return True
            self.screen = OnboardScreen.CHOOSE
            self._enter_choose(reset_highlight=False)
            return True
        if screen is OnboardScreen.SUMMARY:
            self.refresh_detection()
            self.screen = OnboardScreen.DETECT
            self._highlight = 0
            return True
        return True

    def _enter_choose(self, *, reset_highlight: bool = True) -> None:
        self.screen = OnboardScreen.CHOOSE
        self._sub_stage = "menu"
        plan = self.current_plan()
        if not reset_highlight or plan is None:
            self._highlight = min(self._highlight, max(0, len(self.choices()) - 1))
            return
        # Verify-and-repair default: land the cursor on "keep current" when the
        # provider already works, so re-running only changes what the user picks.
        choices = self.choices()
        if plan.already_ok:
            for index, choice in enumerate(choices):
                if choice.id == "skip":
                    self._highlight = index
                    return
        self._highlight = 0

    def _advance(self) -> None:
        self._cursor += 1
        if self._cursor >= len(self.plans):
            self.screen = OnboardScreen.SUMMARY
            self._build_summary()
        else:
            self._enter_choose()

    # -- CONNECT / VERIFY: the credential-collecting actions -------------- #

    def submit_key(self, secret: str) -> bool:
        """Verify a pasted API key and, only on success, persist it."""
        plan = self.current_plan()
        if plan is None:
            return False
        secret = (secret or "").strip()
        if not secret:
            plan.error = "missing_credential"
            return False
        return self._verify_and_persist(
            plan,
            kind=ProfileKind.API_KEY,
            lane="api_key",
            ref=f"{plan.provider}:key",
            secret=secret,
        )

    def confirm_readthrough(self) -> bool:
        """Persist a secretless read-through marker for an existing Codex sign-in."""
        plan = self.current_plan()
        if plan is None:
            return False
        return self._verify_and_persist(
            plan,
            kind=ProfileKind.CODEX_READTHROUGH,
            lane="subscription",
            ref="openai:codex",
        )

    def confirm_runtime(self) -> bool:
        """Persist the Anthropic Claude-runtime route marker (policy-gated)."""
        plan = self.current_plan()
        if plan is None:
            return False
        if not self.manager.anthropic_policy_guard:
            plan.error = "policy_disabled"
            return False
        return self._verify_and_persist(
            plan,
            kind=ProfileKind.CLAUDE_RUNTIME,
            lane="subscription",
            ref="anthropic:claude-code",
        )

    def confirm_ollama(self) -> bool:
        """Verify the local Ollama runtime — no credential to store."""
        plan = self.current_plan()
        if plan is None:
            return False
        return self._verify_and_persist(plan, kind=None, lane="local", ref=None)

    def start_subscription_login(self):
        """Open the injected login session and start a device-code challenge."""
        from tinyic.auth.openai import LoginMode

        self._close_login()
        cm = self.openai_login_factory()
        session = cm.__enter__()
        try:
            challenge = session.start(LoginMode.DEVICE_CODE)
        except Exception:
            with contextlib.suppress(Exception):
                cm.__exit__(None, None, None)
            raise
        self._login_cm = cm
        self._login_session = session
        self._challenge = challenge
        self._sub_stage = "device_wait"
        return challenge

    def finish_subscription_login(self) -> bool:
        """Wait for the device-code login to complete, then verify + persist."""
        plan = self.current_plan()
        session = self._login_session
        challenge = self._challenge
        if plan is None or session is None or challenge is None:
            self._close_login()
            self._sub_stage = "menu"
            return False
        try:
            result = session.wait(challenge)
            reason = _reason_str(result)
        except Exception:
            reason = "runtime_unavailable"
        finally:
            self._close_login()
        if reason != "ok":
            plan.error = reason
            self._sub_stage = "menu"
            return False
        self._sub_stage = "menu"
        return self._verify_and_persist(
            plan,
            kind=ProfileKind.OPENAI_OAUTH,
            lane="subscription",
            ref="openai:chatgpt",
        )

    def _close_login(self) -> None:
        cm = self._login_cm
        self._login_cm = None
        self._login_session = None
        self._challenge = None
        if cm is not None:
            with contextlib.suppress(Exception):
                cm.__exit__(None, None, None)

    def _verify_and_persist(
        self,
        plan: ProviderPlan,
        *,
        kind: ProfileKind | None,
        lane: str,
        ref: str | None,
        secret: str | None = None,
    ) -> bool:
        """The VERIFY gate: check an ephemeral candidate, persist only on pass.

        Verification runs against a candidate that is **not** yet in the store,
        so a failure never persists the route and never overwrites an existing
        working profile.
        """
        provider = plan.provider
        try:
            candidate = self._build_candidate(provider, kind, ref, secret)
        except Exception:
            plan.error = "invalid_credential"
            return False
        binding = self._probe_binding(provider, lane)
        try:
            reason = _reason_str(self.verify_probe(binding, candidate))
        except Exception:
            reason = "probe_failed"
        if reason != "ok":
            plan.error = reason
            return False
        if kind is not None and candidate is not None:
            try:
                self.store.put(candidate.profile)
            except Exception:
                plan.error = "probe_failed"
                return False
            try:
                rest = [
                    existing
                    for existing in self.store.get_auth_order(provider)
                    if existing != ref
                ]
                self.store.set_auth_order(provider, [ref, *rest])
            except Exception:
                # Keep the two writes failure-coherent: if ordering fails, undo
                # the just-put profile so a partial failure can never leave a
                # profile stranded outside the provider's auth order.
                with contextlib.suppress(Exception):
                    self.store.delete(ref)
                plan.error = "probe_failed"
                return False
            plan.persisted_ref = ref
            plan.persisted_lane = lane
        plan.outcome = "verified"
        plan.error = None
        self._advance()
        return True

    @staticmethod
    def _build_candidate(
        provider: str,
        kind: ProfileKind | None,
        ref: str | None,
        secret: str | None,
    ) -> AuthCandidate | None:
        if kind is None or ref is None:
            return None
        if kind is ProfileKind.API_KEY:
            profile = AuthProfile(ref, ProfileKind.API_KEY, AuthLane.API_KEY, secret)
            env_refs = DEFAULT_PROVIDER_ENV_REFS.get(provider, ())
            return AuthCandidate(
                profile, credential_ref=env_refs[0] if env_refs else None
            )
        # Secretless subscription route markers (read-through / runtime / oauth).
        profile = AuthProfile(ref, kind, AuthLane.SUBSCRIPTION)
        return AuthCandidate(profile)

    def _probe_binding(self, provider: str, lane: str):
        """Build a minimal binding for the live verify (only used by the real probe)."""
        from tinyic.models.binding import ModelBinding
        from tinyic.models.thinking import ThinkingLevel

        try:
            from tinyic.models.registry import default_registry

            registry = default_registry()
            catalog = registry.get(provider).catalog()
            provider_obj = registry.get(provider)
            if lane == "subscription":
                model = next(
                    (m for m in catalog if provider_obj.is_subscription_only(m)),
                    catalog[0] if catalog else "probe-model",
                )
            else:
                model = next(
                    (m for m in catalog if not provider_obj.is_subscription_only(m)),
                    catalog[0] if catalog else "probe-model",
                )
            profile = provider_obj.thinking_profile(model)
            thinking = profile.supported[0] if profile.supported else ThinkingLevel.MEDIUM
            return ModelBinding(f"{provider}/{model}", thinking_level=thinking)
        except Exception:
            return ModelBinding(f"{provider}/probe-model")

    # -- SUMMARY ---------------------------------------------------------- #

    def _build_summary(self) -> None:
        from tinyic.personas.registry import list_personas

        preset = self._load_default_preset()
        rows: list[SummaryRow] = []
        for name in list_personas():
            binding = preset.persona_binding(name)
            lane, ref, reason = self._resolve_lane(binding)
            rows.append(
                SummaryRow(
                    persona=name.replace("_", " ").title(),
                    model_ref=binding.model_ref,
                    thinking=binding.thinking_level.value,
                    lane=lane,
                    auth_ref=ref,
                    reason=reason,
                )
            )
        self.summary = OnboardSummary(
            rows=tuple(rows), backend=self._storage_backend(), preset=preset.name
        )

    def _load_default_preset(self):
        from tinyic.models.presets import builtin_default_preset, load_preset

        try:
            return load_preset(None, self.config_path)
        except Exception:
            return builtin_default_preset()

    def _resolve_lane(self, binding) -> tuple[str | None, str | None, str | None]:
        try:
            candidates = self.manager.candidates(binding)
        except AuthResolutionError as exc:
            return None, None, exc.reason_code
        except Exception:
            return None, None, "probe_failed"
        if not candidates:
            return None, None, "missing_credential"
        first = candidates[0]
        return first.lane.value, first.ref, None

    def _storage_backend(self) -> str:
        try:
            return self.store.backend
        except Exception:
            return "file"


# --------------------------------------------------------------------------- #
# The Textual app — a thin renderer over the controller
# --------------------------------------------------------------------------- #

class OnboardApp(App):
    """Render the onboarding wizard and route keys to :class:`OnboardController`."""

    TITLE = "TinyIC onboarding"
    AUTO_FOCUS = None
    CSS = """
    Screen { layout: vertical; }

    #onboard-title {
        height: auto;
        min-height: 2;
        padding: 0 1;
        background: $panel;
        border-bottom: heavy $accent;
        text-style: bold;
    }

    #onboard-body { height: 1fr; padding: 0 1; }
    #onboard-view { height: auto; padding: 1 0; }

    #key-input {
        height: auto;
        margin: 1 0 0 0;
        border: round $warning;
    }

    #onboard-footer {
        height: auto;
        padding: 0 1;
        border-top: heavy $panel-lighten-2;
    }
    #onboard-status { height: auto; color: $text-muted; }
    """

    # Only a hard-exit hatch lives in BINDINGS; every wizard key is routed
    # through ``on_key`` so single keys and masked typing never fight.
    BINDINGS = [("ctrl+c", "quit", "Force quit")]

    def __init__(
        self,
        controller: OnboardController,
        *,
        threaded_verify: bool = True,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.threaded_verify = threaded_verify
        self._header = Static(id="onboard-title")
        self._view = Static(id="onboard-view")
        self._key_input = Input(password=True, placeholder="paste key — hidden", id="key-input")
        self._status = Static(id="onboard-status")

    def compose(self) -> ComposeResult:
        yield self._header
        with VerticalScroll(id="onboard-body"):
            yield self._view
            yield self._key_input
        with Vertical(id="onboard-footer"):
            yield self._status

    def on_mount(self) -> None:
        self.controller.start()
        self._sync()

    def on_unmount(self) -> None:
        # Secrets hygiene: never let a pasted key survive in the widget past the
        # app's own lifetime (e.g. force-quit straight from the key screen).
        self._clear_key_input()

    # -- render ----------------------------------------------------------- #

    def _clear_key_input(self) -> None:
        """Wipe the masked key field so a pasted secret can't linger in memory."""
        if self._key_input.value:
            self._key_input.value = ""

    def _sync(self) -> None:
        self._header.update(self._render_title())
        self._view.update(self.render_body())
        is_key = self.controller.screen is OnboardScreen.CONNECT_KEY
        self._key_input.display = is_key
        # Every exit from the key screen (back / esc / step transition) funnels
        # through _sync; clear the secret the instant we are no longer on it.
        if not is_key:
            self._clear_key_input()
        if is_key and self.focused is not self._key_input:
            self.set_focus(self._key_input)
        elif not is_key and self.focused is self._key_input:
            self.set_focus(None)
        self._status.update(self._render_status())

    def _render_title(self) -> Text:
        text = Text()
        text.append("TinyIC onboarding", style="bold")
        step = {
            OnboardScreen.DETECT: "detect",
            OnboardScreen.CHOOSE: "choose",
            OnboardScreen.CONNECT_KEY: "connect · api key",
            OnboardScreen.CONNECT_SUB: "connect · subscription",
            OnboardScreen.SUMMARY: "summary",
        }[self.controller.screen]
        text.append(f"   {step}", style="dim")
        plan = self.controller.current_plan()
        if plan is not None and self.controller.screen in {
            OnboardScreen.CHOOSE,
            OnboardScreen.CONNECT_KEY,
            OnboardScreen.CONNECT_SUB,
        }:
            position = f"   {self.controller.cursor + 1}/{len(self.controller.plans)}"
            text.append(position, style="dim")
        return text

    def render_body(self) -> Text:
        screen = self.controller.screen
        if screen is OnboardScreen.DETECT:
            return self._render_detect()
        if screen is OnboardScreen.CHOOSE:
            return self._render_choose()
        if screen is OnboardScreen.CONNECT_KEY:
            return self._render_key()
        if screen is OnboardScreen.CONNECT_SUB:
            return self._render_sub()
        return self._render_summary()

    def _render_detect(self) -> Text:
        text = Text()
        text.append("Detected access\n", style="bold underline")
        text.append(
            "What already works on this machine — env keys, a Codex sign-in, a "
            "Claude Code login, a local Ollama.\n\n",
            style="dim",
        )
        if not self.controller.plans:
            text.append("No configurable providers were found.\n", style="yellow")
        for plan in self.controller.plans:
            text.append(f"{provider_label(plan.provider)}\n", style="bold")
            for lane in sorted(plan.lanes):
                text.append_text(self._lane_line(plan.lanes[lane]))
            if plan.outcome == "verified":
                text.append("   • configured this session ✓\n", style="green")
        if self.controller.diagnostics:
            text.append("\nSetup diagnostics\n", style="bold yellow")
            for diag in self.controller.diagnostics:
                text.append(
                    f"   ⚠ {diag.reason_code} — {diag.message}\n", style="yellow"
                )
        text.append("\n")
        text.append_text(self._menu_lines())
        return text

    def _lane_line(self, lane: LaneStatus) -> Text:
        mark, style = _STATUS_MARK.get(lane.status, ("·", "dim"))
        text = Text()
        text.append(f"   {mark} ", style=style)
        text.append(f"{lane.lane:<13}", style="cyan")
        text.append(f"{lane.reason_code}", style=style)
        if lane.auth_profile:
            text.append(f"  ({lane.auth_profile})", style="dim")
        text.append("\n")
        return text

    def _render_choose(self) -> Text:
        plan = self.controller.current_plan()
        text = Text()
        if plan is None:
            return text
        text.append(f"Configure {provider_label(plan.provider)}\n", style="bold underline")
        if plan.already_ok:
            text.append("Already working — reconfigure only if you want to.\n", style="green")
        text.append("\n")
        text.append_text(self._menu_lines())
        text.append_text(self._error_line(plan))
        return text

    def _render_sub(self) -> Text:
        plan = self.controller.current_plan()
        text = Text()
        if plan is None:
            return text
        if self.controller.sub_stage == "device_wait":
            return self._render_device()
        text.append(
            f"Connect {provider_label(plan.provider)} subscription\n",
            style="bold underline",
        )
        text.append("\n")
        text.append_text(self._menu_lines())
        text.append_text(self._error_line(plan))
        return text

    def _render_device(self) -> Text:
        challenge = self.controller.challenge
        text = Text()
        text.append("Sign in with ChatGPT\n", style="bold underline")
        text.append("\nGo to ", style="none")
        url = getattr(challenge, "verification_url", None) or "(the URL shown by Codex)"
        text.append(url, style="bold cyan")
        text.append("\nand enter the code:\n\n", style="none")
        code = getattr(challenge, "user_code", None) or "…"
        text.append(f"    {code}\n\n", style="bold green")
        text.append("Waiting for authorization…  ", style="yellow")
        text.append("esc to cancel", style="dim")
        return text

    def _render_key(self) -> Text:
        plan = self.controller.current_plan()
        text = Text()
        if plan is None:
            return text
        text.append(
            f"Enter your {provider_label(plan.provider)} API key\n",
            style="bold underline",
        )
        text.append(
            "\nInput is hidden and never logged. Press enter to verify with a "
            "one-token live check; esc to cancel.\n",
            style="dim",
        )
        if plan.verifying:
            text.append("\nVerifying…\n", style="yellow")
        text.append_text(self._error_line(plan))
        return text

    def _render_summary(self) -> Text:
        summary = self.controller.summary
        text = Text()
        text.append("Setup summary\n", style="bold underline")
        if summary is None:
            return text
        text.append(f"\nResolved committee bindings — preset {summary.preset}\n", style="bold")
        for row in summary.rows:
            text.append(f"  {row.persona:<18}", style="bold")
            text.append(f"{row.model_ref}", style="cyan")
            text.append(f"  think {row.thinking}", style="dim")
            if row.lane:
                text.append(f"  · {row.lane}", style="green")
                if row.auth_ref:
                    text.append(f" ({row.auth_ref})", style="dim")
            else:
                text.append(f"  · unresolved: {row.reason}", style="red")
            text.append("\n")
        text.append(
            f"\nCredentials stored in: {summary.backend}\n", style="bold"
        )
        text.append("Next: ", style="dim")
        text.append(NEXT_STEP_HINT, style="bold green")
        text.append("\n\n")
        text.append_text(self._menu_lines())
        return text

    def _menu_lines(self) -> Text:
        text = Text()
        choices = self.controller.choices()
        highlight = self.controller.highlight
        for index, choice in enumerate(choices):
            selected = index == highlight
            marker = "▶ " if selected else "  "
            style = "bold" if choice.enabled else "dim strike"
            if selected and choice.enabled:
                style = "bold reverse"
            text.append(f"{marker}{choice.label}\n", style=style)
            if selected:
                if choice.detail:
                    text.append(f"    {choice.detail}\n", style="dim")
                if choice.note:
                    text.append(f"    {choice.note}\n", style="italic yellow")
                if not choice.enabled and choice.disabled_reason:
                    text.append(f"    {choice.disabled_reason}\n", style="red")
        return text

    def _error_line(self, plan: ProviderPlan) -> Text:
        text = Text()
        if plan.verifying:
            text.append("\nVerifying…  (one-token live check)\n", style="yellow")
        elif plan.error:
            text.append(
                f"\n✗ Not saved — {plan.error}: {reason_hint(plan.error)}\n",
                style="red",
            )
            text.append("  Nothing was overwritten. Try again or pick another lane.\n", style="dim")
        return text

    def _render_status(self) -> Text:
        screen = self.controller.screen
        text = Text()
        if screen is OnboardScreen.CONNECT_KEY:
            text.append("type key (hidden)", style="bold")
            text.append("  ·  enter verify · esc cancel", style="dim")
            return text
        if screen is OnboardScreen.CONNECT_SUB and self.controller.sub_stage == "device_wait":
            text.append("waiting for authorization…", style="bold yellow")
            text.append("  ·  esc cancel", style="dim")
            return text
        text.append("↑↓ move", style="bold")
        if screen is OnboardScreen.DETECT:
            text.append("  ·  enter begin · q quit", style="dim")
        elif screen is OnboardScreen.SUMMARY:
            text.append("  ·  enter select · esc re-detect · q quit", style="dim")
        else:
            text.append("  ·  enter select · esc back · q quit", style="dim")
        return text

    # -- key routing ------------------------------------------------------ #

    def on_key(self, event: events.Key) -> None:
        if len(self.screen_stack) > 1:
            return
        if event.key == "escape":
            if not self.controller.back():
                self.exit()
            else:
                self._sync()
            event.stop()
            event.prevent_default()
            return
        if self.focused is self._key_input:
            return  # masked typing — Input owns it (enter -> on_input_submitted)

        key = event.key
        char = event.character
        handled = True
        if key in {"up", "left"} or char == "k":
            self.controller.move(-1)
        elif key in {"down", "right"} or char == "j":
            self.controller.move(1)
        elif key in {"enter", "space"}:
            self._activate()
            event.stop()
            event.prevent_default()
            return
        elif char is not None and char.isdigit() and char != "0":
            self.controller.select_index(int(char) - 1)
        elif char == "q":
            self.exit()
            return
        else:
            handled = False
        if handled:
            self._sync()
            event.stop()
            event.prevent_default()

    def _activate(self) -> None:
        kind, payload = self.controller.activate()
        if kind == "exit":
            self.exit()
            return
        if kind == "verify" and callable(payload):
            self._run_off_thread(payload)
            return
        if kind == "device":
            self._run_off_thread(self.controller.finish_subscription_login)
            return
        self._sync()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is not self._key_input:
            return
        value = event.value
        self._key_input.value = ""
        self._run_off_thread(lambda: self.controller.submit_key(value))
        event.stop()

    def _run_off_thread(self, fn: Callable[[], object]) -> None:
        """Run a verify/login tail off the UI thread, marking the plan busy.

        The live 1-token check and the device-code wait can block; running them
        in a worker keeps the wizard responsive. Tests pass
        ``threaded_verify=False`` so the (instant) fakes run inline and the flow
        stays deterministic.
        """
        plan = self.controller.current_plan()
        if plan is not None:
            plan.verifying = True
        self._sync()

        def done() -> None:
            if plan is not None:
                plan.verifying = False
            self._sync()

        if self.threaded_verify:
            def worker() -> None:
                try:
                    fn()
                finally:
                    self.call_from_thread(done)

            self.run_worker(worker, thread=True, exclusive=True)
        else:
            try:
                fn()
            finally:
                done()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def build_controller(
    *,
    config_path: str | None = None,
    manager: AuthManager | None = None,
    report_factory: Callable[[], DoctorReport] | None = None,
    verify_probe: Callable[[object, object], object] | None = None,
    openai_login_factory: Callable[[], object] | None = None,
) -> OnboardController:
    """Build a production controller, wiring the default doctor-report seam."""
    manager = manager or AuthManager.from_config(config_path)
    if report_factory is None:
        def report_factory() -> DoctorReport:  # noqa: E306 - local default seam
            from tinyic.auth.doctor import run_doctor

            # TinyTroupe import chatter must never corrupt the live screen.
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                return run_doctor(config_path=config_path, manager=manager)

    return OnboardController(
        manager=manager,
        report_factory=report_factory,
        config_path=config_path,
        verify_probe=verify_probe,
        openai_login_factory=openai_login_factory,
    )


def run_onboard(*, config_path: str | None = None) -> None:
    """Launch the interactive onboarding wizard (``tinyic onboard``)."""
    controller = build_controller(config_path=config_path)
    OnboardApp(controller).run()
