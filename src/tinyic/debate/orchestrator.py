"""DebateOrchestrator -- a TinyWorld subclass for structured investment debates."""

import queue
import threading

from tinytroupe import config_manager
from tinytroupe.agent import TinyPerson
from tinytroupe.environment.tiny_world import TinyWorld
from tinytroupe.session import Session

from tinyic.constants import MAX_PERSONAS, MIN_PERSONAS
from tinyic.data.models import DataPackage
from tinyic.events import EventEnvelope, EventLog
from tinyic.usage import (
    MODEL_PRICES_USD_PER_MILLION,
    diff_cost_counters,
    estimate_model_keyed_cost,
    snapshot_cost_counters,
)

from .models import DebatePhase
from .prompts import (
    CONTEXT_PREAMBLE,
    DEVILS_ADVOCATE_PROMPT,
    PHASE_PROMPTS,
    PHILOSOPHY_HOOKS,
    REINFORCEMENT_TEMPLATE,
    ROLE_RELEASE_PROMPT,
)


CANONICAL_PHASE_NAMES: dict[DebatePhase, str] = {
    DebatePhase.OPENING: "opening",
    DebatePhase.CROSS_EXAM: "cross_exam",
    DebatePhase.REBUTTAL: "rebuttal",
    DebatePhase.VERDICT: "verdict",
}

CANONICAL_TURN_ROLES: dict[DebatePhase, str] = {
    DebatePhase.OPENING: "statement",
    DebatePhase.CROSS_EXAM: "challenge",
    DebatePhase.REBUTTAL: "rebuttal",
    DebatePhase.VERDICT: "verdict",
}


class DebateOrchestrator(TinyWorld):
    """Run a structured multi-phase investment debate.

    Phases proceed in fixed order: OPENING -> CROSS_EXAM -> REBUTTAL -> VERDICT.
    Each phase broadcasts a goal to every agent, then agents act sequentially
    in a stable (non-randomized) order.
    """

    PHASE_ORDER = [
        DebatePhase.OPENING,
        DebatePhase.CROSS_EXAM,
        DebatePhase.REBUTTAL,
        DebatePhase.VERDICT,
    ]

    def __init__(
        self,
        name: str,
        personas: list,
        data_package: DataPackage,
        session: Session | None = None,
        event_log: EventLog | None = None,
        **kwargs,
    ):
        if len(personas) < MIN_PERSONAS:
            raise ValueError(
                f"At least {MIN_PERSONAS} personas required, got {len(personas)}"
            )
        if len(personas) > MAX_PERSONAS:
            raise ValueError(
                f"At most {MAX_PERSONAS} personas allowed, got {len(personas)}"
            )

        moved_personas = []
        world_initialized = False
        try:
            # The pre-M6 Streamlit worker loads personas before constructing
            # the orchestrator and cannot pass a Session without changing that
            # UI file. Isolated default-loaded personas are adopted into the
            # first persona's scope here. The batch is preflighted and every
            # move is rolled back if any later constructor step fails.
            if session is None:
                real_personas = [
                    persona
                    for persona in personas
                    if isinstance(persona, TinyPerson)
                ]
                if real_personas:
                    session = real_personas[0].session
                    planned_agents = dict(session.agents)
                    for persona in real_personas[1:]:
                        if (
                            persona.session is not session
                            and persona.environment is not None
                        ):
                            raise ValueError(
                                f"Cannot move agent '{persona.name}' while it is "
                                f"attached to environment "
                                f"'{persona.environment.name}'."
                            )
                        existing = planned_agents.get(persona.name)
                        if existing is not None and existing is not persona:
                            raise ValueError(
                                f"Agent name {persona.name} is already in use."
                            )
                        planned_agents[persona.name] = persona
                    for persona in real_personas[1:]:
                        if persona.session is not session:
                            original_session = persona.session
                            persona.move_to_session(session)
                            moved_personas.append((persona, original_session))

            super().__init__(
                name=name,
                agents=personas,
                broadcast_if_no_target=True,
                session=session,
                **kwargs,
            )
            world_initialized = True

            self.data_package = data_package
            self.event_log = event_log
            self.current_phase = DebatePhase.SETUP
            self._phase_index = 0
            self._phase_history: list[str] = []
            # M1 records turn-level deltas from legacy cumulative counters.
            # M2 adapters replace these snapshots with native per-call usage
            # while retaining the same usage_ref event relationship.
            self.turn_usage_refs: dict[str, int | str | None] = {}

            self.make_everyone_accessible()

            # Optional streaming callbacks (set by UI before run_debate)
            self.on_phase_start = None   # Optional[Callable[[str], None]] -- called with phase.value
            self.on_agent_start = None   # Optional[Callable[[str, str], None]] -- called with (agent.name, phase.value)
            self.on_agent_done = None    # Optional[Callable[[str, str, list], None]] -- called with (agent.name, phase.value, actions)

            # Optional message queue for user steering (set by UI)
            self.message_queue = None  # Optional[queue.Queue] -- items are (message_str, target_agent_name_or_None)
            # Optional phase gate for inter-phase pausing (set by UI)
            self.phase_gate = None     # Optional[threading.Event] -- if set, _step waits for it before proceeding

            # Anti-convergence: devil's advocate rotation state
            self._da_index = 0
            self._current_devils_advocate = None
        except Exception:
            if world_initialized:
                self.dispose()
            for persona, original_session in reversed(moved_personas):
                persona.move_to_session(original_session)
            raise

    # ------------------------------------------------------------------
    # Context injection
    # ------------------------------------------------------------------

    def inject_context(self) -> None:
        """Broadcast the financial data package to all agents."""
        preamble = CONTEXT_PREAMBLE.format(
            company_name=self.data_package.company_name,
            ticker=self.data_package.ticker,
            context_data=self.data_package.to_context_string(),
        )
        self.broadcast(preamble)

    # ------------------------------------------------------------------
    # Anti-convergence helpers
    # ------------------------------------------------------------------

    def _get_reinforcement_prompt(self, agent) -> str:
        """Build a persona-specific reinforcement prompt for *agent*."""
        hook = PHILOSOPHY_HOOKS.get(
            agent.name, "Stay true to your unique perspective."
        )
        return REINFORCEMENT_TEMPLATE.format(name=agent.name, philosophy_hook=hook)

    def _select_devils_advocate(self):
        """Pick the next devil's advocate via round-robin and store the result."""
        da = self.agents[self._da_index % len(self.agents)]
        self._da_index += 1
        self._current_devils_advocate = da
        return da

    # ------------------------------------------------------------------
    # Event-stream helpers
    # ------------------------------------------------------------------

    def _emit_event(
        self, event_type: str, payload: dict
    ) -> EventEnvelope | None:
        """Emit one engine event when an event log is attached."""
        if self.event_log is not None:
            return self.event_log.emit(event_type, payload)
        return None

    @staticmethod
    def _usage_snapshot() -> dict:
        """Read legacy cumulative counters without making usage load-bearing."""
        try:
            from tinytroupe.clients import client as resolve_client

            return snapshot_cost_counters(resolve_client())
        except Exception:
            return {}

    @staticmethod
    def _model_ref() -> str:
        model = str(config_manager.get("model") or "unknown")
        if "/" in model:
            return model
        provider = str(config_manager.get("api_type") or "openai")
        return f"{provider}/{model}"

    def _canonical_phase(self, phase: DebatePhase) -> str:
        return CANONICAL_PHASE_NAMES[phase]

    def _turn_id(self, agent_index: int) -> str:
        """Return a stable ID derived only from deterministic debate order."""
        ordinal = self._phase_index * len(self.agents) + agent_index + 1
        return f"turn-{ordinal:04d}"

    @staticmethod
    def _action_texts(actions: list, action_type: str) -> list[str]:
        texts: list[str] = []
        for action in actions:
            if not isinstance(action, dict) or action.get("type") != action_type:
                continue
            content = action.get("content")
            if content is not None and str(content):
                texts.append(str(content))
        return texts

    @staticmethod
    def _cognitive_state(agent, committed_actions) -> dict:
        """Select the final committed cognitive state for a speaker turn."""
        if isinstance(committed_actions, list):
            for committed in reversed(committed_actions):
                if not isinstance(committed, dict):
                    continue
                state = committed.get("cognitive_state")
                if isinstance(state, dict) and state:
                    return state

        state = getattr(agent, "_mental_state", None)
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _state_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "; ".join(str(item) for item in value)
        return str(value)

    def _emit_committed_turn(
        self,
        *,
        agent,
        phase: DebatePhase,
        turn_id: str,
        actions: list,
        committed_actions,
        usage_delta: dict,
    ) -> None:
        """Emit the completed action parts and state for one committed turn."""
        if self.event_log is None:
            return

        for event_type, action_type in (
            ("think_completed", "THINK"),
            ("talk_completed", "TALK"),
        ):
            texts = self._action_texts(actions, action_type)
            if texts:
                self._emit_event(
                    event_type,
                    {"turn_id": turn_id, "full_text": "\n\n".join(texts)},
                )

        state = self._cognitive_state(agent, committed_actions)
        state_payload = {
            "turn_id": turn_id,
            "persona": agent.name,
            "goals": self._state_text(state.get("goals")),
            "attention": self._state_text(state.get("attention")),
            "emotions": self._state_text(state.get("emotions")),
        }
        context = state.get("context")
        if context is not None:
            state_payload["context"] = (
                context
                if isinstance(context, list)
                else [self._state_text(context)]
            )
        self._emit_event("cognitive_state", state_payload)

        usage_ref = None
        if any(
            usage_delta.get(field, 0)
            for field in (
                "input_tokens",
                "output_tokens",
                "model_calls",
                "cached_calls",
            )
        ):
            model_ref = self._model_ref()
            cost = estimate_model_keyed_cost(
                {model_ref: usage_delta}, MODEL_PRICES_USD_PER_MILLION
            )
            usage_payload = {
                "turn_id": turn_id,
                "persona": agent.name,
                "purpose": "turn",
                "model_ref": model_ref,
                "input_tokens": int(usage_delta.get("input_tokens", 0)),
                "output_tokens": int(usage_delta.get("output_tokens", 0)),
                # The legacy client exposes local cache-call counts, not
                # provider cached-input tokens. M2 adapters populate this.
                "cached_tokens": 0,
            }
            if cost is not None:
                usage_payload["cost_usd"] = round(cost, 8)
            usage_event = self._emit_event("usage", usage_payload)
            if usage_event is not None:
                usage_ref = usage_event.seq
        self.turn_usage_refs[turn_id] = usage_ref

        canonical_phase = self._canonical_phase(phase)
        self._emit_event(
            "turn_completed",
            {
                "turn_id": turn_id,
                "persona": agent.name,
                "phase": canonical_phase,
                "interrupted": False,
                "usage_ref": usage_ref,
            },
        )

    # ------------------------------------------------------------------
    # Step override (replaces TinyWorld._step entirely)
    # ------------------------------------------------------------------

    def _step(self, timedelta_per_step=None, **kwargs):
        """Execute one debate phase: broadcast goal, then each agent acts in order."""
        if self._phase_index >= len(self.PHASE_ORDER):
            self.current_phase = DebatePhase.COMPLETE
            return {}

        # Wait for phase gate if set (inter-phase pause)
        if self.phase_gate is not None:
            self.phase_gate.wait()
            self.phase_gate.clear()  # Reset for next phase

        phase = self.PHASE_ORDER[self._phase_index]
        self.current_phase = phase
        canonical_phase = self._canonical_phase(phase)

        # Notify: phase starting
        if self.on_phase_start:
            self.on_phase_start(phase.value)

        # Broadcast the phase prompt as an internal goal
        prompt = PHASE_PROMPTS[phase].format(company=self.data_package.company_name)
        self.broadcast_internal_goal(prompt)

        # Devil's advocate: select DA before agent loop in CROSS_EXAM
        if phase == DebatePhase.CROSS_EXAM:
            self._select_devils_advocate()

        phase_payload = {"phase": canonical_phase, "index": self._phase_index}
        if phase == DebatePhase.CROSS_EXAM:
            phase_payload["da_persona"] = self._current_devils_advocate.name
        self._emit_event("phase_started", phase_payload)

        # Role release: notify previous DA at REBUTTAL start (before any agent acts)
        if phase == DebatePhase.REBUTTAL and self._current_devils_advocate is not None:
            self._current_devils_advocate.listen(ROLE_RELEASE_PROMPT)

        # Agents act sequentially in stable order
        agents_actions: dict = {}
        for agent_index, agent in enumerate(self.agents):
            # Drain message queue before each agent acts
            self._process_message_queue()

            # Anti-convergence: inject persona-specific reinforcement (ALL phases)
            reinforcement = self._get_reinforcement_prompt(agent)
            agent.listen(reinforcement)

            # Devil's advocate: inject DA prompt during CROSS_EXAM only
            if phase == DebatePhase.CROSS_EXAM and agent == self._current_devils_advocate:
                agent.listen(DEVILS_ADVOCATE_PROMPT)

            turn_id = self._turn_id(agent_index)
            self._emit_event(
                "turn_started",
                {
                    "turn_id": turn_id,
                    "persona": agent.name,
                    "phase": canonical_phase,
                    "role": CANONICAL_TURN_ROLES[phase],
                },
            )

            # Notify: agent about to act
            if self.on_agent_start:
                self.on_agent_start(agent.name, phase.value)

            usage_before = self._usage_snapshot()
            committed_actions = agent.act(return_actions=True)
            latest = agent.pop_latest_actions()
            agents_actions[agent.name] = latest
            self._handle_actions(agent, latest)
            usage_after = self._usage_snapshot()
            usage_delta = (
                diff_cost_counters(usage_after, usage_before)
                if usage_before and usage_after
                else {}
            )
            self._emit_committed_turn(
                agent=agent,
                phase=phase,
                turn_id=turn_id,
                actions=latest,
                committed_actions=committed_actions,
                usage_delta=usage_delta,
            )

            # Notify: agent finished
            if self.on_agent_done:
                self.on_agent_done(agent.name, phase.value, latest)

        self._phase_history.append(phase.value)
        self._emit_event(
            "phase_completed",
            {
                "phase": canonical_phase,
                "index": self._phase_index,
                "turn_count": len(self.agents),
            },
        )
        self._phase_index += 1

        if self._phase_index >= len(self.PHASE_ORDER):
            self.current_phase = DebatePhase.COMPLETE

        return agents_actions

    def _process_message_queue(self):
        """Drain the message queue, injecting user messages into the debate.

        Messages are tuples of (text, target_agent_name_or_None).
        If target is None, broadcast to all agents.
        If target is a valid agent name, deliver via agent.listen() to that agent
        and broadcast an observation to others.
        """
        if self.message_queue is None:
            return

        while True:
            try:
                text, target = self.message_queue.get_nowait()
            except queue.Empty:
                break

            if target and target in self.name_to_agent:
                # Targeted message: direct to target, observation to others
                target_agent = self.name_to_agent[target]
                target_agent.listen(f"[Moderator to {target}]: {text}")
                for agent in self.agents:
                    if agent.name != target:
                        agent.listen(
                            f"[Moderator asked {target}]: {text}"
                        )
            else:
                # Broadcast to all agents
                self.broadcast(f"[Moderator]: {text}")

    # ------------------------------------------------------------------
    # Convenience runner
    # ------------------------------------------------------------------

    def run_debate(self) -> None:
        """Run the full debate: inject context, then execute all phases."""
        self.inject_context()
        # If phase_gate is set, signal it for the first phase
        if self.phase_gate is not None:
            self.phase_gate.set()
        self.run(
            steps=len(self.PHASE_ORDER),
            parallelize=False,
            randomize_agents_order=False,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_complete(self) -> bool:
        """Whether all debate phases have been executed."""
        return self.current_phase == DebatePhase.COMPLETE
