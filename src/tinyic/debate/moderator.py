"""The debate Moderator -- a restraint-first, non-voting procedure owner (FR-4.1).

The moderator is a *system component*, not a seventh debating voice. It never
casts a vote and never contributes analysis; it only runs procedure:

* **Phase gating** -- blocks between phases when a caller wired a phase gate.
* **Exchange caps (FR-4.2)** -- owns the per-phase ceiling on how many
  exchange-rounds a phase may run and clamps the protocol's request to it.
* **Devil's-advocate selection + announcement (FR-4.3)** -- deterministic
  rotation keyed on a persisted per-install counter (fixes review defect B8,
  where a fresh orchestrator reset the counter so the DA never rotated), plus an
  explicit ``--da <persona>`` override.
* **Steering delivery points** -- drains queued user steering at turn
  boundaries, keeping the existing queue semantics.

It "speaks" only for procedure (phase banners, the DA announcement, steering
acknowledgements), and those are carried by existing schema events
(``phase_started``/``phase_completed``, ``phase_started.da_persona``, the
``[Moderator]`` steering relays) -- never as persona turns.

An optional lightweight :class:`~tinyic.models.binding_client.BindingClient`
(the preset's moderator slot) may back the moderator, but every Stage-1 duty
above is rules-only and needs no LLM. The binding is reserved for the
structured-verdict recording and memo-request duties that arrive with FR-4.4 /
FR-4.5.
"""

from __future__ import annotations

import queue as _queue
from collections.abc import Iterable, Mapping

from tinyic import state

#: Inclusive bounds on a configurable exchange cap. The sycophancy evidence caps
#: productive debate at 2-3 exchanges (docs/research 5); one is the floor.
MIN_EXCHANGES = 1
MAX_EXCHANGES = 3

#: Declared protocol ceilings published in ``debate_started.caps`` (FR-4.2):
#: cross-exam allows up to two exchanges per challenged persona
#: (challenge -> response); every other phase is a single statement each.
DEFAULT_EXCHANGE_CAPS: dict[str, int] = {
    "opening": 1,
    "cross_exam": 2,
    "rebuttal": 1,
    "verdict": 1,
}

#: Exchange-rounds the Stage-1 base protocol actually requests per phase. One
#: round per phase preserves the historical turn structure; the cross-exam
#: challenge->response sub-round that consumes the 2-exchange budget lands with
#: the structured artifacts of FR-4.4, which will raise the cross-exam request
#: to its cap. ``rounds_for`` always clamps the request to the cap.
DEFAULT_REQUESTED_EXCHANGES: dict[str, int] = {
    "opening": 1,
    "cross_exam": 1,
    "rebuttal": 1,
    "verdict": 1,
}

#: Persisted per-install counter name for devil's-advocate rotation.
DA_ROTATION_COUNTER = "da_rotation"


class ModeratorError(ValueError):
    """An out-of-bounds cap or an unresolvable devil's-advocate override."""


def resolve_caps(caps: Mapping[str, int] | None) -> dict[str, int]:
    """Merge caller ``caps`` over the FR-4.2 defaults, enforcing per-phase bounds.

    Unknown phase keys are ignored (additive-tolerant, matching the renderer
    contract for unknown fields); a known phase set out of ``[MIN_EXCHANGES,
    MAX_EXCHANGES]`` or to a non-integer raises :class:`ModeratorError`.
    """
    resolved = dict(DEFAULT_EXCHANGE_CAPS)
    if caps:
        for phase, value in caps.items():
            if phase not in resolved:
                continue
            if not isinstance(value, int) or isinstance(value, bool):
                raise ModeratorError(
                    f"exchange cap for {phase!r} must be an integer, got {value!r}"
                )
            if not (MIN_EXCHANGES <= value <= MAX_EXCHANGES):
                raise ModeratorError(
                    f"exchange cap for {phase!r} is {value}; must be within "
                    f"[{MIN_EXCHANGES}, {MAX_EXCHANGES}]"
                )
            resolved[phase] = value
    return resolved


class Moderator:
    """Restraint-first, non-voting owner of debate procedure (FR-4.1)."""

    def __init__(
        self,
        *,
        caps: Mapping[str, int] | None = None,
        requested_exchanges: Mapping[str, int] | None = None,
        da_override: str | None = None,
        binding_client=None,
    ) -> None:
        self.exchange_caps = resolve_caps(caps)
        self.requested_exchanges = dict(DEFAULT_REQUESTED_EXCHANGES)
        if requested_exchanges:
            self.requested_exchanges.update(requested_exchanges)
        self.da_override = (
            da_override.strip()
            if isinstance(da_override, str) and da_override.strip()
            else None
        )
        # Optional preset moderator binding; rules-only Stage-1 duties never call
        # it, so it is simply held for the later structured-verdict/memo work.
        self.binding_client = binding_client
        self.current_devils_advocate = None

    @property
    def moderator_ref(self) -> str:
        """Identifier published in ``debate_started.moderator``.

        Every Stage-1 duty is rules-based, so the moderator reports ``"rules"``
        regardless of an available binding -- the binding does no LLM work yet.
        """
        return "rules"

    # ------------------------------------------------------------------
    # Exchange caps (FR-4.2)
    # ------------------------------------------------------------------

    def cap_for(self, phase: str) -> int:
        """The declared exchange ceiling for ``phase`` (canonical key)."""
        return self.exchange_caps.get(phase, 1)

    def rounds_for(self, phase: str) -> int:
        """Exchange-rounds to run for ``phase``, never exceeding its cap.

        Enforcement lives here: whatever the protocol requests, the moderator
        clamps it to the phase cap, so a debate can never exceed its declared
        ceiling.
        """
        requested = self.requested_exchanges.get(phase, 1)
        return max(0, min(requested, self.cap_for(phase)))

    # ------------------------------------------------------------------
    # Devil's advocate (FR-4.3, B8)
    # ------------------------------------------------------------------

    def _match_override(self, agents: list):
        want = self.da_override.casefold()
        want_snake = want.replace(" ", "_")
        for agent in agents:
            name = str(getattr(agent, "name", "")).casefold()
            if name == want or name.replace(" ", "_") == want_snake:
                return agent
        return None

    def validate_override(self, agents: Iterable) -> None:
        """Fail fast if a ``--da`` override matches no committee member."""
        if self.da_override is None:
            return
        agents = list(agents)
        if self._match_override(agents) is None:
            available = ", ".join(
                str(getattr(agent, "name", "")) for agent in agents
            )
            raise ModeratorError(
                f"devil's-advocate override {self.da_override!r} matches no "
                f"committee member; available: {available}"
            )

    def select_devils_advocate(self, agents: Iterable):
        """Choose and record this phase's devil's advocate.

        An explicit override wins and does not consume a rotation slot;
        otherwise the persisted per-install counter selects the next member and
        is advanced so the *next* debate rotates on (B8 fix).
        """
        agents = list(agents)
        if not agents:
            raise ModeratorError(
                "cannot select a devil's advocate from an empty committee"
            )
        if self.da_override is not None:
            self.validate_override(agents)
            chosen = self._match_override(agents)
        else:
            slot = state.advance_counter(DA_ROTATION_COUNTER)
            chosen = agents[slot % len(agents)]
        self.current_devils_advocate = chosen
        return chosen

    # ------------------------------------------------------------------
    # Phase gating & steering delivery points
    # ------------------------------------------------------------------

    def gate_phase(self, phase_gate) -> None:
        """Block until ``phase_gate`` is released, then re-arm it for next phase."""
        if phase_gate is not None:
            phase_gate.wait()
            phase_gate.clear()

    def deliver_steering(
        self, message_queue, *, agents, name_to_agent, broadcast
    ) -> None:
        """Drain queued user steering at a turn boundary (existing semantics).

        Targeted messages go to the named persona with an observation relayed to
        the others; untargeted messages broadcast. The ``[Moderator]`` framing is
        preserved so downstream renderers see procedure, not a persona turn.
        """
        if message_queue is None:
            return
        while True:
            try:
                text, target = message_queue.get_nowait()
            except _queue.Empty:
                break
            if target and target in name_to_agent:
                target_agent = name_to_agent[target]
                target_agent.listen(f"[Moderator to {target}]: {text}")
                for agent in agents:
                    if agent.name != target:
                        agent.listen(f"[Moderator asked {target}]: {text}")
            else:
                broadcast(f"[Moderator]: {text}")


__all__ = [
    "DA_ROTATION_COUNTER",
    "DEFAULT_EXCHANGE_CAPS",
    "DEFAULT_REQUESTED_EXCHANGES",
    "MAX_EXCHANGES",
    "MIN_EXCHANGES",
    "Moderator",
    "ModeratorError",
    "resolve_caps",
]
