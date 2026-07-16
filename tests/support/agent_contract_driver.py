"""A real-subprocess driver for the M6 agent-contract DoD (``test_m6_agent_contract``).

This module is executed **as a fresh child process** (``python -m
tests.support.agent_contract_driver ...``) by the DoD test so the headless
``tinyic`` engine is exercised across a genuine process boundary — real
STDOUT/STDERR/STDIN pipes, real worker + log-tailer + stdin-reader threads, real
event-log file, real exit code — with **zero network**.

It is the offline stand-in for a live provider: the same recorded-SSE wire
transports and deterministic extraction/memo mocks the M2/M6 suites use are
installed here with plain ``setattr`` (a child process cannot receive a pytest
``monkeypatch``), and a canned :class:`~tinyic.data.models.DataPackage` replaces
the data fetch. Everything downstream of that — ``run_debate_command`` and the
whole event pipeline — is the real product code.

Determinism of stdin steering is the one thing a subprocess cannot get for free:
a fully mocked debate would otherwise race to completion before the parent's
steer/interrupt lines are read. Under ``--await-steering`` a :class:`_BarrierInbox`
+ a one-shot barrier in the mocked ``act`` hold the very first turn until both the
steer and the interrupt have been submitted through the real ``--steer-stdin``
reader, so the delivery boundary and the discard-on-arrival interrupt land at the
same turns on every run, on every machine, with no sleeps. Runs that feed no stdin
(``--fail-after-phases``, missing-auth) omit the flag and so never park the first
turn waiting on commands that will not come.

Usage (all via argv/env — the parent's only injection channels):
    python -m tests.support.agent_contract_driver [--fail-after-phases] [--await-steering]
The debate always runs the three-persona committee ``_REGISTRY_3`` on the
recorded ``openai/gpt-5.2`` fixture; the log is written under ``TINYIC_RUNS_DIR``.
Exit code is the real FR-6.2 code (0 complete / 2 partial).
"""

from __future__ import annotations

import sys
import threading

#: Ceiling on how long the first turn parks waiting for the parent's stdin lines
#: (per event). Generous for a loaded CI box; instant on the happy path.
_BARRIER_TIMEOUT_S = 30.0


def _make_steering_barrier(inbox_cls, act_via_binding):
    """Build an ``(act, inbox)`` pair whose first ``act`` blocks until the parent's
    steer **and** interrupt have both been submitted through the real
    ``--steer-stdin`` reader.

    A fully mocked debate would otherwise race to completion before the parent's
    two stdin lines are read, so the steer's delivery boundary and the
    discard-on-arrival interrupt would land at nondeterministic turns. Producers
    still push through the real inbox (the engine emits the authoritative
    ``steering_*`` / ``turn_interrupted`` events); the subclass only *observes*
    arrival to release the one-shot first-turn barrier. The ``_BARRIER_TIMEOUT_S``
    ceiling is a safety net never reached on the happy path (both land in ms).
    """
    steer_seen = threading.Event()
    interrupt_seen = threading.Event()
    first_turn_released = threading.Event()

    class _BarrierInbox(inbox_cls):
        def submit(self, mode, text, **kwargs):
            msg_id = super().submit(mode, text, **kwargs)
            if msg_id is not None:
                steer_seen.set()
            return msg_id

        def request_interrupt(self, **kwargs):
            armed = super().request_interrupt(**kwargs)
            if armed:
                interrupt_seen.set()
            return armed

    def _barrier_act(self, *, return_actions=False, **kwargs):
        # Only the first turn blocks: once both commands are in, delivery lands at
        # the next speaker boundary and the interrupt discards this first turn.
        if not first_turn_released.is_set():
            steer_seen.wait(_BARRIER_TIMEOUT_S)
            interrupt_seen.wait(_BARRIER_TIMEOUT_S)
            first_turn_released.set()
        return act_via_binding(self, return_actions=return_actions, **kwargs)

    return _barrier_act, _BarrierInbox()


def _install_offline_engine(*, fail_after_phases: bool, await_steering: bool):
    """Install the offline fakes and return ``(committee, data_package, inbox)``.

    Mirrors ``tests/test_m6_headless.py``'s ``binding_mocks`` fixture, but applied
    with ``setattr`` (this runs in a child process). Under ``await_steering`` the
    ``act``/inbox are the barrier pair that makes stdin-fed steering deterministic;
    otherwise the turns run straight through with a plain inbox (a run that feeds
    no stdin must not park turn-0001 waiting on steering that never arrives).
    """
    import tinyic.debate as debate_module
    from tinyic.debate.steering import SteeringInbox
    from tinyic.models import StaticCredentialProvider, build_committee
    from tinyic.personas.base import InvestorPersona
    from tinytroupe.agent import TinyPerson

    # The recorded-fixture transport machinery + deterministic mocks shared with
    # the M2 Definition-of-Done suite (same offline debate, zero network).
    from tests.test_m2_dod import (
        _act_via_binding,
        _dod_transport_factory,
        _mock_data_package,
        _mock_disagreements,
        _mock_memo,
        _mock_votes,
        _PERSONAS_3,
        _preset_all,
    )

    if await_steering:
        # This run feeds steer + interrupt over stdin: hold turn-0001 until both
        # are in so their delivery/discard boundaries are deterministic.
        act, inbox = _make_steering_barrier(SteeringInbox, _act_via_binding)
    else:
        # No stdin steering on this run (--fail-after-phases): run the turns
        # straight through with a plain inbox. The barrier here would park
        # turn-0001 for the full timeout waiting on commands that never come.
        def act(self, *, return_actions=False, **kwargs):
            return _act_via_binding(self, return_actions=return_actions, **kwargs)

        inbox = SteeringInbox()

    setattr(InvestorPersona, "act", act)
    setattr(InvestorPersona, "consolidate_episode_memories", lambda _self: False)
    TinyPerson.communication_display = False
    debate_module.generate_memo = _mock_memo
    debate_module.extract_disagreements = _mock_disagreements
    if fail_after_phases:
        # Every phase runs, then vote extraction blows up -> debate_error after
        # >=1 phase completed -> the FR-6.2 "partial" exit code (2).
        def _boom(_orchestrator):
            raise RuntimeError("injected mid-debate extraction failure")

        debate_module.extract_votes = _boom
    else:
        debate_module.extract_votes = _mock_votes

    committee = build_committee(
        _preset_all("openai/gpt-5.2"),
        _PERSONAS_3,
        credentials=StaticCredentialProvider({}),
        transport_factory=_dod_transport_factory,
    )
    return committee, _mock_data_package(), inbox


def main(argv: list[str]) -> int:
    import contextlib

    fail_after_phases = "--fail-after-phases" in argv
    await_steering = "--await-steering" in argv

    # Importing TinyTroupe (transitively, via the engine + offline fakes) prints a
    # one-time disclaimer + config dump to STDOUT. That happens here, in the
    # driver, *before* ``run_debate_command`` installs its own STDOUT->STDERR
    # redirect — so do the imports/setup under a redirect of our own, keeping this
    # process's STDOUT a pristine JSONL channel. ``run_debate_command`` re-reads
    # ``sys.stdout`` at call time (after the block exits), so it still streams to
    # the real STDOUT.
    with contextlib.redirect_stdout(sys.stderr):
        from tinyic.headless import run_debate_command

        committee, data_package, inbox = _install_offline_engine(
            fail_after_phases=fail_after_phases,
            await_steering=await_steering,
        )
        registry_names = [name for name, _display in _persona_pairs()]

    # The real headless engine: streams JSONL to the real STDOUT, human progress
    # to the real STDERR, and reads steering JSON from the real STDIN.
    return run_debate_command(
        "AAPL",
        personas=",".join(registry_names),
        headless=True,
        json_mode=True,
        steer_stdin=True,
        committee=committee,
        data_package=data_package,
        steering=inbox,
    )


def _persona_pairs():
    from tests.test_m2_dod import _PERSONAS_3

    return _PERSONAS_3


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main(sys.argv[1:]))
