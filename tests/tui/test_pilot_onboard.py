"""Textual ``App.run_test`` pilots for the onboarding wizard (FR-2.4, stage 2).

These drive the *real* :class:`~tinyic.tui.onboard.OnboardApp` end to end through
its single ``on_key`` router — synthetic doctor reports in, fake credential
stores and fake login callbacks injected — and assert what a human actually sees
on screen and what lands (or, crucially, does *not* land) in the store:

* (a) DETECT renders per-provider × lane status from a synthetic report, and the
  reserved ``tinyic`` pseudo-provider rows surface as whole-run diagnostics, not
  as fixable provider lanes.
* (b) the CHOOSE chooser renders the "best for" copy for each branch; the
  Anthropic subscription branch carries the policy note and is disabled (with the
  ``policy_guard`` reason) when the guard is off.
* (c) the OpenAI device-code flow renders the user code + verification URL and
  completes through a fake poll; the API-key branch is a masked field (the secret
  never appears in the rendered screen) and persists through the fake store.
* (d) a failing verify leaves an existing working profile untouched and blocks
  completion — the repair semantics.
* (e) the SUMMARY card shows the resolved persona bindings and the storage
  backend.
* (f) escape backs out at every step and finally exits, without ever persisting a
  partial route (including cancelling a live device-code wait).
* (g) re-running against an already-working config is an idempotent no-op
  walkthrough: every provider defaults to *keep current*, nothing is verified,
  nothing is written.

Every side effect is an injected fake, so the whole file runs offline; the real
device-code login and the live one-token verify stay behind ``live_api`` and a
human running ``tinyic onboard``.  The controller-level twins of these
guarantees live in ``tests/test_m3_onboard_wizard.py``; here we prove they hold
when driven through the mounted TUI's key routing, focus handling, and
off-thread verify worker.
"""

from __future__ import annotations

import threading

from tinyic.auth.doctor import build_report
from tinyic.auth.profiles import AuthLane, AuthProfile, ProfileKind
from tinyic.tui.onboard import (
    ANTHROPIC_POLICY_NOTE,
    NEXT_STEP_HINT,
    POLICY_DISABLED_MSG,
    OnboardApp,
    OnboardScreen,
)

# Reuse the offline fakes/builders that back the controller-level suite so the
# pilots and the unit tests cannot drift apart on what a report/store looks like.
from tests.test_m3_onboard_wizard import (
    _controller,
    _default_report,
    _manager,
    _probe,
    _store,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

async def _pump_until(pilot, predicate, *, limit: int = 300) -> None:
    """Pause the event loop until ``predicate`` holds (or fail after ``limit``).

    The device-code CONNECT and the verify tail run in a Textual thread worker
    that marshals its result back with ``call_from_thread``; this is the
    timer-less way to wait for that hop deterministically.
    """
    for _ in range(limit):
        if predicate():
            return
        await pilot.pause()
    assert predicate(), "predicate was never satisfied before the loop ran out"


def _all_ok_report():
    """A report where every provider already has one working lane.

    Drives the verify-and-repair default: each provider is ``already_ok`` so the
    chooser lands on *Keep current setup* and a straight walkthrough touches
    nothing.
    """
    return build_report(
        [
            _probe(
                "openai", "api_key", status="ok", reason="ok", required=True,
                profile="openai:key", model="openai/gpt-5.2",
            ),
            _probe("openai", "subscription", reason="runtime_unavailable"),
            _probe("anthropic", "api_key"),
            _probe("anthropic", "subscription", status="ok", reason="ok"),
            _probe("google", "api_key", status="ok", reason="ok", profile="google:key"),
            _probe("ollama", "local", status="ok", reason="ok"),
        ],
        preset="default",
    )


class BlockingLoginSession:
    """A device-code login whose ``wait`` parks until the test releases it.

    ``start`` returns the challenge synchronously (so the device screen renders
    the code + URL immediately), while ``wait`` blocks on an event — mirroring a
    real ``account/login/completed`` notification the human triggers in a
    browser.  Cancelling closes the session; like the real RPC that ends the
    notification stream, a closed ``wait`` reports ``runtime_unavailable`` and
    therefore never persists.
    """

    def __init__(
        self,
        release: threading.Event,
        *,
        user_code: str = "WXYZ-1234",
        url: str = "https://auth.example/device",
        success: bool = True,
    ) -> None:
        self.release = release
        self.user_code = user_code
        self.url = url
        self.success = success
        self.opened = False
        self.closed = False
        self.waited = False

    def __enter__(self) -> "BlockingLoginSession":
        self.opened = True
        return self

    def __exit__(self, *_args: object) -> bool:
        self.closed = True
        return False

    def start(self, mode):
        from tinyic.auth.openai import LoginChallenge, LoginMode

        return LoginChallenge(
            LoginMode.DEVICE_CODE,
            "login-fake",
            verification_url=self.url,
            user_code=self.user_code,
        )

    def wait(self, _challenge):
        from tinyic.auth.openai import OpenAIAuthProbe, OpenAIAuthReason

        self.waited = True
        self.release.wait(timeout=5)
        if self.closed:
            return OpenAIAuthProbe(
                OpenAIAuthReason.RUNTIME_UNAVAILABLE, "runtime", "codex_runtime"
            )
        reason = (
            OpenAIAuthReason.OK if self.success else OpenAIAuthReason.INVALID_CREDENTIAL
        )
        return OpenAIAuthProbe(reason, "runtime", "codex_runtime")


# --------------------------------------------------------------------------- #
# (a) DETECT renders provider × lane status + the tinyic pseudo-provider rows.
# --------------------------------------------------------------------------- #

async def test_a_detect_renders_lane_status_and_tinyic_diagnostics(tmp_path):
    report = _default_report(
        extra=[
            _probe("tinyic", "configuration", status="error",
                   reason="invalid_preset", required=True),
            _probe("tinyic", "credential_store", status="error",
                   reason="probe_failed", required=True),
        ]
    )
    controller = _controller(tmp_path, report=report)
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.render_body().plain

        # Per-provider headers and per-lane rows (mark + reason code) are drawn.
        assert "Detected access" in body
        for label in ("OpenAI", "Anthropic", "Google", "Ollama (local)"):
            assert label in body, f"missing provider header {label!r}"
        assert "missing_credential" in body      # openai api_key (required, ✗)
        assert "runtime_unavailable" in body      # openai subscription / ollama
        assert "✗" in body and "▲" in body        # error + warning status marks

        # The reserved pseudo-provider is a whole-run diagnostic, not a lane.
        assert "Setup diagnostics" in body
        assert "invalid_preset" in body and "probe_failed" in body
        assert all(plan.provider != "tinyic" for plan in controller.plans)
        # It never appears as a configurable provider header the user can fix.
        assert [d.reason_code for d in controller.diagnostics] == [
            "invalid_preset",
            "probe_failed",
        ]

        # The overview offers the single "begin" action.
        assert "Begin setup" in body


# --------------------------------------------------------------------------- #
# (b) The chooser renders "best for" copy; Anthropic carries the policy note.
# --------------------------------------------------------------------------- #

async def test_b_chooser_renders_best_for_copy(tmp_path):
    controller = _controller(tmp_path)
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # DETECT -> CHOOSE openai
        await pilot.pause()
        assert controller.screen is OnboardScreen.CHOOSE

        # Subscription is highlighted first: its label + "best for" copy render.
        body = app.render_body().plain
        assert "Use your subscription" in body
        assert "Enter an API key" in body
        assert "Best for:" in body and "5-hour" in body  # subscription detail

        # Moving the highlight to the API-key branch renders its own copy.
        await pilot.press("down")
        await pilot.pause()
        body = app.render_body().plain
        assert "Best for:" in body and "pay-as-you-go" in body


async def test_b_anthropic_branch_shows_policy_note(tmp_path):
    controller = _controller(tmp_path)
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # -> CHOOSE openai
        await pilot.pause()
        # Skip openai (row 3) and advance to the Anthropic chooser.
        await pilot.press("3")
        await pilot.press("enter")
        await pilot.pause()
        assert controller.current_plan().provider == "anthropic"

        # The subscription branch is enabled and carries the full policy note.
        sub = controller.choices()[0]
        assert sub.id == "subscription" and sub.enabled is True
        body = app.render_body().plain
        assert ANTHROPIC_POLICY_NOTE in body
        assert "Claude.ai login" in body and "Pro/Max" in body


async def test_b_policy_guard_off_disables_anthropic_subscription(tmp_path):
    controller = _controller(tmp_path, policy_guard=False)
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # CHOOSE openai
        await pilot.pause()
        await pilot.press("3")      # skip openai
        await pilot.press("enter")
        await pilot.pause()
        assert controller.current_plan().provider == "anthropic"

        sub = controller.choices()[0]
        assert sub.id == "subscription" and sub.enabled is False
        body = app.render_body().plain
        assert POLICY_DISABLED_MSG in body
        assert "policy_guard" in body

        # Activating the disabled branch neither advances nor stores anything.
        await pilot.press("enter")
        await pilot.pause()
        assert controller.screen is OnboardScreen.CHOOSE
        assert controller.current_plan().provider == "anthropic"
        # The error chip is the short reason code; the full policy sentence stays
        # in the branch detail (asserted above via POLICY_DISABLED_MSG in body).
        assert controller.current_plan().error == "policy_disabled"


# --------------------------------------------------------------------------- #
# (c) Device-code renders code+URL and completes; API-key is masked + persists.
# --------------------------------------------------------------------------- #

async def test_c_device_code_flow_renders_code_url_and_completes(tmp_path):
    release = threading.Event()
    session = BlockingLoginSession(release, user_code="WXYZ-1234",
                                   url="https://auth.example/dev")
    store = _store(tmp_path)
    controller = _controller(
        tmp_path, manager=_manager(store), login_factory=lambda: session
    )
    app = OnboardApp(controller)  # real threaded verify worker
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # CHOOSE openai (subscription highlighted)
        await pilot.pause()
        await pilot.press("enter")  # CONNECT_SUB (device is the only option)
        await pilot.pause()
        assert controller.screen is OnboardScreen.CONNECT_SUB
        assert "device" in [c.id for c in controller.choices()]

        # Start the device login; the worker parks in wait() while we render.
        await pilot.press("enter")
        await _pump_until(pilot, lambda: controller.sub_stage == "device_wait")
        body = app.render_body().plain
        assert "WXYZ-1234" in body            # the user code to type
        assert "auth.example/dev" in body      # the verification URL to visit
        assert "Waiting for authorization" in body

        # Release the fake poll -> verify + persist the secretless OAuth marker.
        release.set()
        # The verified lane lands on the MODEL step; esc keeps the default.
        await _pump_until(
            pilot, lambda: controller.screen is OnboardScreen.MODEL
        )
        await pilot.press("escape")
        await _pump_until(
            pilot,
            lambda: controller.current_plan() is not None
            and controller.current_plan().provider == "anthropic",
        )
        marker = store.get("openai:chatgpt")
        assert marker is not None
        assert marker.kind is ProfileKind.OPENAI_OAUTH
        assert marker.secret is None and marker.lane is AuthLane.SUBSCRIPTION
        assert session.waited and session.closed


async def test_c_api_key_input_is_masked_and_persists(tmp_path):
    secret = "sk-SUPER-SECRET-9999"
    store = _store(tmp_path)
    controller = _controller(tmp_path, manager=_manager(store))
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # CHOOSE openai (subscription highlighted)
        await pilot.pause()
        await pilot.press("down")   # -> "Enter an API key"
        await pilot.press("enter")  # -> CONNECT_KEY
        await pilot.pause()
        assert controller.screen is OnboardScreen.CONNECT_KEY

        key_input = app.query_one("#key-input")
        assert key_input.password is True          # the field itself is masked
        assert app.focused is key_input            # focus moved to it
        key_input.value = secret
        await pilot.pause()

        # The secret is not on screen: the rendered terminal shows bullets only.
        screen = app.export_screenshot()
        assert secret not in screen
        assert "•" in screen                       # masked glyphs are present
        assert secret not in app.render_body().plain
        assert secret not in app._render_status().plain

        # Submitting verifies then persists through the fake store, and advances.
        await pilot.press("enter")
        await pilot.pause()
        profile = store.get("openai:key")
        assert profile is not None and profile.lane is AuthLane.API_KEY
        assert store.get_auth_order("openai") == ("openai:key",)
        assert controller.plans[0].outcome == "verified"
        # The verified lane lands on the MODEL step; esc keeps the default.
        assert controller.screen is OnboardScreen.MODEL
        await pilot.press("escape")
        await pilot.pause()
        assert controller.current_plan().provider == "anthropic"

        # The secret survives nowhere the user (or a log) could read it.
        assert secret not in app.export_screenshot()
        assert secret not in repr(profile)


# --------------------------------------------------------------------------- #
# (d) A failed verify never overwrites a working profile and blocks completion.
# --------------------------------------------------------------------------- #

async def test_d_failed_verify_leaves_existing_untouched_and_blocks(tmp_path):
    existing = AuthProfile("openai:key", ProfileKind.API_KEY, AuthLane.API_KEY,
                           "OLD-GOOD")
    store = _store(tmp_path, existing)
    controller = _controller(
        tmp_path,
        manager=_manager(store),
        report=_default_report(openai_key_ok=True),
        verify=lambda _b, _c: "invalid_credential",
    )
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # CHOOSE openai (already ok -> lands on skip)
        await pilot.pause()
        # Move up off "Keep current setup" to the API-key branch and open it.
        await pilot.press("up")
        assert controller.current_choice().id == "api_key"
        await pilot.press("enter")
        await pilot.pause()
        assert controller.screen is OnboardScreen.CONNECT_KEY

        app.query_one("#key-input").value = "BAD-NEW-KEY"
        await pilot.press("enter")
        await pilot.pause()

        # The existing working credential is untouched; nothing was overwritten.
        assert store.get("openai:key").secret == "OLD-GOOD"
        assert controller.plans[0].outcome is None
        assert controller.plans[0].error == "invalid_credential"
        # Completion is blocked: still on the key screen, not advanced/summary.
        assert controller.screen is OnboardScreen.CONNECT_KEY
        assert controller.current_plan().provider == "openai"
        assert controller.summary is None

        body = app.render_body().plain
        assert "Not saved" in body and "invalid_credential" in body
        assert "Nothing was overwritten" in body


# --------------------------------------------------------------------------- #
# (e) The SUMMARY card shows resolved bindings and the storage location.
# --------------------------------------------------------------------------- #

async def test_e_summary_card_shows_bindings_and_storage(tmp_path):
    config = tmp_path / "tinyic.toml"
    config.write_text(
        '[presets.default]\nmodel = "openai/gpt-5.2"\nthinking = "high"\n',
        encoding="utf-8",
    )
    key = AuthProfile("openai:key", ProfileKind.API_KEY, AuthLane.API_KEY, "sk-x")
    store = _store(tmp_path, key)
    controller = _controller(
        tmp_path,
        manager=_manager(store),
        config_path=str(config),
        report=_all_ok_report(),
    )
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # begin
        await pilot.pause()
        # Every provider already works, so each defaults to "Keep current setup":
        # a straight walk through them lands on the summary card.
        for _ in range(4):
            assert controller.current_choice().id == "skip"
            await pilot.press("enter")
            await pilot.pause()
        assert controller.screen is OnboardScreen.SUMMARY

        body = app.render_body().plain
        # Resolved committee bindings: model + thinking + the api_key lane.
        assert "Resolved committee bindings" in body
        assert "openai/gpt-5.2" in body
        assert "think high" in body
        assert "api_key" in body and "openai:key" in body
        assert len(controller.summary.rows) == 6
        # Storage location + next-step hint are on the card.
        assert "Credentials stored in: keyring" in body
        assert NEXT_STEP_HINT in body


# --------------------------------------------------------------------------- #
# (f) Escape backs out at every step and exits, without partial persistence.
# --------------------------------------------------------------------------- #

async def test_f_escape_backs_out_each_step_without_persistence(tmp_path):
    secret = "sk-DISCARD-ME-4242"
    store = _store(tmp_path)
    controller = _controller(tmp_path, manager=_manager(store))
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # DETECT -> CHOOSE openai
        await pilot.pause()
        assert controller.screen is OnboardScreen.CHOOSE

        # Into the masked key screen; type a secret, then escape to cancel.
        await pilot.press("down")   # api_key
        await pilot.press("enter")  # -> CONNECT_KEY
        await pilot.pause()
        app.query_one("#key-input").value = secret
        await pilot.pause()
        await pilot.press("escape")  # cancel -> CHOOSE
        await pilot.pause()
        assert controller.screen is OnboardScreen.CHOOSE
        assert secret not in app.render_body().plain  # nothing echoed after cancel

        # Into the subscription menu, then escape back to CHOOSE.
        await pilot.press("up")     # back to subscription
        await pilot.press("enter")  # -> CONNECT_SUB
        await pilot.pause()
        assert controller.screen is OnboardScreen.CONNECT_SUB
        await pilot.press("escape")  # -> CHOOSE
        await pilot.pause()
        assert controller.screen is OnboardScreen.CHOOSE

        # Escape from the first provider's CHOOSE returns to DETECT...
        await pilot.press("escape")
        await pilot.pause()
        assert controller.screen is OnboardScreen.DETECT
        # ...and escape at DETECT exits the wizard.
        await pilot.press("escape")
        await pilot.pause()

    assert app.return_code == 0
    # Nothing was ever persisted and no summary was reached.
    assert store.list() == ()
    assert controller.summary is None


async def test_f_escape_cancels_device_wait_without_persistence(tmp_path):
    release = threading.Event()
    session = BlockingLoginSession(release)
    store = _store(tmp_path)
    controller = _controller(
        tmp_path, manager=_manager(store), login_factory=lambda: session
    )
    app = OnboardApp(controller)  # threaded worker
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # CHOOSE openai
        await pilot.pause()
        await pilot.press("enter")  # CONNECT_SUB
        await pilot.pause()
        await pilot.press("enter")  # start device login
        await _pump_until(pilot, lambda: controller.sub_stage == "device_wait")

        # Escape cancels the in-flight login and closes the session.
        await pilot.press("escape")
        await pilot.pause()
        assert controller.sub_stage == "menu"
        assert session.closed is True
        assert controller.screen is OnboardScreen.CONNECT_SUB

        # Release the parked worker so it unwinds; a closed wait persists nothing.
        release.set()
        await _pump_until(pilot, lambda: not app.workers)
        assert store.get("openai:chatgpt") is None
        assert store.list() == ()
        assert controller.current_plan().provider == "openai"  # never advanced


# --------------------------------------------------------------------------- #
# (g) Re-running against an already-working config is a no-op walkthrough.
# --------------------------------------------------------------------------- #

async def test_g_rerun_existing_working_config_is_a_noop(tmp_path):
    verify_calls: list[object] = []

    seed = AuthProfile("openai:key", ProfileKind.API_KEY, AuthLane.API_KEY,
                       "sk-seed")
    store = _store(tmp_path, seed)
    before = ([p.public_dict() for p in store.list()],
              store.get_auth_order("openai"))

    controller = _controller(
        tmp_path,
        manager=_manager(store),
        report=_all_ok_report(),
        verify=lambda b, c: verify_calls.append((b, c)) or "ok",
    )
    app = OnboardApp(controller, threaded_verify=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # begin -> first provider CHOOSE
        await pilot.pause()

        # Each already-working provider defaults to "Keep current setup".
        for provider in ("openai", "anthropic", "google", "ollama"):
            assert controller.current_plan().provider == provider
            choice = controller.current_choice()
            assert choice.id == "skip"
            assert choice.label == "Keep current setup"
            await pilot.press("enter")
            await pilot.pause()

        assert controller.screen is OnboardScreen.SUMMARY
        assert [p.outcome for p in controller.plans] == ["kept"] * 4

    # A no-op: verify never fired and the store is byte-for-byte unchanged.
    assert verify_calls == []
    after = ([p.public_dict() for p in store.list()],
             store.get_auth_order("openai"))
    assert after == before
    assert store.get("openai:key").secret == "sk-seed"
