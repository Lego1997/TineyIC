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

The moderator also **records the structured artifacts** (FR-4.1 / FR-4.4): the
opening thesis and the final verdict each arrive as a mandated fenced block in
the persona's prose, which the moderator lifts into a structured record via the
deterministic parser in :mod:`tinyic.debate.structured`. Because that parse is
deterministic, this duty too is rules-only -- the moderator's optional
:class:`~tinyic.models.binding_client.BindingClient` (the preset's moderator
slot) stays reserved for the memo-request duty that arrives with FR-4.5, so
``moderator_ref`` remains ``"rules"``.
"""

from __future__ import annotations

import queue as _queue
import re
from collections.abc import Iterable, Mapping

from tinyic import state
from .structured import (
    StructuredThesis,
    StructuredVerdict,
    parse_thesis,
    parse_verdict,
)

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


def _normalize_persona_name(value: object) -> str:
    """Normalize display names and registry slugs to the same lookup key."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\s_-]+", "_", value.strip().casefold())


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
        # Optional preset moderator binding; rules-only duties never call it, so
        # it is simply held for the later memo-request work (FR-4.5).
        self.binding_client = binding_client
        self.current_devils_advocate = None
        # Structured records the moderator captures during the debate (FR-4.4),
        # keyed by persona display name. The verdicts are the authoritative vote
        # source consumed first by extraction (LLM extraction is the fallback).
        self.recorded_theses: dict[str, StructuredThesis] = {}
        self.recorded_verdicts: dict[str, StructuredVerdict] = {}

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
    # Structured artifacts (FR-4.4)
    # ------------------------------------------------------------------

    def record_thesis(self, persona: str, text: str) -> StructuredThesis | None:
        """Parse and record ``persona``'s opening thesis block, if present.

        Returns the :class:`StructuredThesis` when the mandated block parses (so
        the orchestrator can emit the ``thesis_recorded`` event) or ``None`` when
        it is absent/malformed, in which case nothing is recorded.
        """
        thesis = parse_thesis(text)
        if thesis is not None:
            self.recorded_theses[persona] = thesis
        return thesis

    def record_verdict(self, persona: str, text: str) -> StructuredVerdict | None:
        """Parse and record ``persona``'s final verdict block, if present.

        A recorded verdict is the authoritative vote source (FR-4.4): vote
        extraction consumes it first and only falls back to an LLM extraction
        pass for personas whose verdict block was absent or malformed.
        """
        verdict = parse_verdict(text)
        if verdict is not None:
            self.recorded_verdicts[persona] = verdict
        return verdict

    # ------------------------------------------------------------------
    # Phase gating & steering delivery points
    # ------------------------------------------------------------------

    def gate_phase(self, phase_gate) -> None:
        """Apply the configured phase-boundary control.

        The web face passes a ``RunControl`` with ``wait_for_phase``. A plain
        ``threading.Event`` remains supported for programmatic callers using the
        pre-v2.2 pause/step seam.
        """
        if phase_gate is not None:
            wait_for_phase = getattr(phase_gate, "wait_for_phase", None)
            if callable(wait_for_phase):
                wait_for_phase()
                return
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
            self.relay_message(
                text,
                target,
                agents=agents,
                name_to_agent=name_to_agent,
                broadcast=broadcast,
            )

    def relay_message(
        self, text, target, *, agents, name_to_agent, broadcast
    ) -> None:
        """Relay one steering message with the ``[Moderator]`` procedure framing.

        Targeted messages reach the named persona and are relayed to the others
        as an observation; an untargeted (or unresolved-target) message
        broadcasts. Shared by the legacy tuple queue (:meth:`deliver_steering`)
        and the M6 engine inbox, so both deliver identically.
        """
        canonical_target = None
        if target:
            wanted = _normalize_persona_name(target)
            canonical_target = next(
                (
                    name
                    for name in name_to_agent
                    if _normalize_persona_name(name) == wanted
                ),
                None,
            )
        if canonical_target is not None:
            target_agent = name_to_agent[canonical_target]
            target_agent.listen(f"[Moderator to {canonical_target}]: {text}")
            for agent in agents:
                if agent.name != canonical_target:
                    agent.listen(
                        f"[Moderator asked {canonical_target}]: {text}"
                    )
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
