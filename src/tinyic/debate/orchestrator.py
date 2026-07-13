"""DebateOrchestrator -- a TinyWorld subclass for structured investment debates."""

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
from .moderator import Moderator
from .prompts import (
    CONTEXT_PREAMBLE,
    DEVILS_ADVOCATE_PROMPT,
    PHASE_PROMPTS,
    PHILOSOPHY_HOOKS,
    REINFORCEMENT_TEMPLATE,
    ROLE_RELEASE_PROMPT,
    temperament_clause,
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
        committee=None,
        moderator: Moderator | None = None,
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
            # A resolved model plan (tinyic.models.Committee) or None. When
            # present, each persona's turn routes through its own binding client
            # and usage is captured natively at the adapter boundary; when None,
            # the legacy client()+snapshot-delta path (M1) is used unchanged.
            self.committee = committee
            self.current_phase = DebatePhase.SETUP
            self._phase_index = 0
            self._phase_history: list[str] = []
            # M1 records turn-level deltas from legacy cumulative counters.
            # M2 adapters replace these snapshots with native per-call usage
            # while retaining the same usage_ref event relationship.
            self.turn_usage_refs: dict[str, int | str | None] = {}

            self.make_everyone_accessible()

            # The moderator is a system component (FR-4.1), never a debating
            # voice: it owns phase gating, exchange caps, devil's-advocate
            # rotation, and steering delivery. A rules-only default keeps the
            # pre-M6 Streamlit path (which constructs the orchestrator directly)
            # working without an LLM. Validate any --da override now, while the
            # committee is known, so a bad name fails before the debate runs.
            self.moderator = moderator if moderator is not None else Moderator()
            self.moderator.validate_override(self.agents)

            # Optional streaming callbacks (set by UI before run_debate)
            self.on_phase_start = None   # Optional[Callable[[str], None]] -- called with phase.value
            self.on_agent_start = None   # Optional[Callable[[str, str], None]] -- called with (agent.name, phase.value)
            self.on_agent_done = None    # Optional[Callable[[str, str, list], None]] -- called with (agent.name, phase.value, actions)

            # Optional message queue for user steering (set by UI)
            self.message_queue = None  # Optional[queue.Queue] -- items are (message_str, target_agent_name_or_None)
            # Optional phase gate for inter-phase pausing (set by UI)
            self.phase_gate = None     # Optional[threading.Event] -- if set, _step waits for it before proceeding

            # Devil's-advocate rotation is owned by the moderator (persisted
            # per-install counter, B8 fix); the orchestrator only caches the
            # current selection for role-release bookkeeping.
            self._current_devils_advocate = None
            # Monotonic turn ordinal, stable across any number of exchange-rounds
            # within a phase (so turn ids never collide when a cap allows >1).
            self._turn_seq = 0
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
        """Build the one-line, temperament-aware reinforcement for *agent* (FR-4.3)."""
        hook = PHILOSOPHY_HOOKS.get(
            agent.name, "Stay true to your unique perspective."
        )
        temperament = getattr(agent, "temperament", None)
        return REINFORCEMENT_TEMPLATE.format(
            name=agent.name,
            philosophy_hook=hook,
            temperament_line=temperament_clause(temperament),
        )

    def _select_devils_advocate(self):
        """Delegate devil's-advocate selection to the moderator and cache it."""
        da = self.moderator.select_devils_advocate(self.agents)
        self._current_devils_advocate = da
        return da

    # ------------------------------------------------------------------
    # Structured artifacts (FR-4.4)
    # ------------------------------------------------------------------

    def _capture_structured_record(self, agent, phase, actions) -> None:
        """Record the opening thesis / final verdict from a turn's TALK prose.

        Free-form NL is reserved for cross-exam and rebuttal, so only the opening
        and verdict phases carry a mandated trailing block. The moderator owns
        the structured records (FR-4.1); the orchestrator hands it this turn's
        TALK prose and, for the opening, emits the resulting ``thesis_recorded``
        event. An absent or malformed block records nothing -- the verdict then
        defers to LLM vote extraction, and the opening simply emits no thesis.
        """
        if phase not in (DebatePhase.OPENING, DebatePhase.VERDICT):
            return
        talk = "\n\n".join(self._action_texts(actions, "TALK"))
        if not talk:
            return
        if phase == DebatePhase.OPENING:
            thesis = self.moderator.record_thesis(agent.name, talk)
            if thesis is not None:
                self._emit_event(
                    "thesis_recorded",
                    {
                        "persona": agent.name,
                        "phase": "opening",
                        "stance": thesis.stance,
                        "claims": list(thesis.claims),
                        "confidence": thesis.confidence,
                    },
                )
        else:
            self.moderator.record_verdict(agent.name, talk)

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

    def _next_turn_id(self) -> str:
        """Return the next monotonic turn ID in deterministic debate order.

        A running counter (rather than a positional formula) keeps ids unique
        and gap-free even when a phase runs more than one exchange-round under
        its cap; for the one-round-per-phase base protocol it reproduces the
        historical ``turn-0001``.. sequence exactly.
        """
        self._turn_seq += 1
        return f"turn-{self._turn_seq:04d}"

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

    def _run_bound_turn(self, agent, binding_client, turn_id: str):
        """Route one persona turn through its binding client.

        Wires the client's per-turn sinks so the turn streams as ``think_delta``
        / ``talk_delta`` events (turn-scoped, between ``turn_started`` and the
        completed events): ``on_talk``/``on_think`` carry the scanner-extracted
        action *prose* (never the raw JSON envelope), while ``on_reasoning``
        carries the provider's native reasoning tokens — both THINK sources map
        to ``think_delta`` per the event schema.  ``on_text`` is deliberately
        left unset so the raw completion envelope never leaks into an event.
        The provider's terminal usage is aggregated for this turn, then the
        client is activated so the vendored act loop's ``client()`` calls resolve
        to it.  Returns ``(committed_actions, latest_actions, binding_usage)``.
        """
        from tinyic.models import routing

        turn_usages: list = []
        binding_client.on_usage = (
            lambda usage, model_ref: turn_usages.append(usage)
        )
        binding_client.on_usage_window = lambda snapshot: self._emit_event(
            "usage_window", snapshot.as_payload()
        )
        binding_client.on_reasoning = lambda text: self._emit_event(
            "think_delta", {"turn_id": turn_id, "text": text}
        )
        binding_client.on_talk = lambda text: self._emit_event(
            "talk_delta", {"turn_id": turn_id, "text": text}
        )
        binding_client.on_think = lambda text: self._emit_event(
            "think_delta", {"turn_id": turn_id, "text": text}
        )
        try:
            with routing.activate(binding_client):
                committed_actions = agent.act(return_actions=True)
        finally:
            binding_client.on_usage = None
            binding_client.on_usage_window = None
            binding_client.on_reasoning = None
            binding_client.on_talk = None
            binding_client.on_think = None

        latest = agent.pop_latest_actions()
        self._handle_actions(agent, latest)
        binding_usage = self._aggregate_turn_usage(
            turn_usages, binding_client.binding.model_ref
        )
        return committed_actions, latest, binding_usage

    @staticmethod
    def _aggregate_turn_usage(turn_usages: list, model_ref: str) -> dict:
        """Sum a turn's per-call adapter usage into one attributed record."""
        profiles = [
            getattr(usage, "auth_profile", None)
            for usage in turn_usages
            if getattr(usage, "auth_profile", None)
        ]
        lanes = {
            getattr(usage, "lane", "api_key") for usage in turn_usages
        }
        billable = [
            usage
            for usage in turn_usages
            if getattr(usage, "lane", "api_key") != "subscription"
        ]
        result = {
            "input_tokens": sum(
                getattr(usage, "input_tokens", 0) for usage in turn_usages
            ),
            "output_tokens": sum(
                getattr(usage, "output_tokens", 0) for usage in turn_usages
            ),
            "cached_tokens": sum(
                getattr(usage, "cached_tokens", 0) for usage in turn_usages
            ),
            "model_ref": model_ref,
            "calls": len(turn_usages),
            "billable_input_tokens": sum(
                getattr(usage, "input_tokens", 0) for usage in billable
            ),
            "billable_output_tokens": sum(
                getattr(usage, "output_tokens", 0) for usage in billable
            ),
            "billable_cached_tokens": sum(
                getattr(usage, "cached_tokens", 0) for usage in billable
            ),
            "billable_calls": len(billable),
        }
        if lanes:
            result["lane"] = (
                "subscription" if "subscription" in lanes else "api_key"
            )
        if profiles:
            # A usage-limit rotation can change the actual profile mid-turn;
            # the successful terminal call's profile is authoritative.
            result["auth_profile"] = profiles[-1]
        return result

    def _emit_binding_usage(self, agent, turn_id: str, binding_usage: dict):
        """Emit one turn-scoped usage event from native adapter usage.

        Returns the event ``seq`` for ``turn_completed.usage_ref``, or ``None``
        when the routed turn reported no usage (a degraded lane), leaving
        ``usage_ref`` null as the schema permits.
        """
        input_tokens = int(binding_usage.get("input_tokens", 0))
        output_tokens = int(binding_usage.get("output_tokens", 0))
        cached_tokens = int(binding_usage.get("cached_tokens", 0))
        calls = int(binding_usage.get("calls", 0))
        if not (input_tokens or output_tokens or cached_tokens or calls):
            return None
        model_ref = binding_usage.get("model_ref") or self._model_ref()
        if "billable_input_tokens" in binding_usage:
            billable_input = int(
                binding_usage.get("billable_input_tokens", 0)
            )
            billable_output = int(
                binding_usage.get("billable_output_tokens", 0)
            )
            billable_calls = int(binding_usage.get("billable_calls", 0))
        elif binding_usage.get("lane", "api_key") == "subscription":
            billable_input = billable_output = billable_calls = 0
        else:
            billable_input = input_tokens
            billable_output = output_tokens
            billable_calls = calls
        cost = None
        if billable_input or billable_output or billable_calls:
            cost = estimate_model_keyed_cost(
                {
                    model_ref: {
                        "input_tokens": billable_input,
                        "output_tokens": billable_output,
                    }
                },
                MODEL_PRICES_USD_PER_MILLION,
            )
        usage_payload = {
            "turn_id": turn_id,
            "persona": agent.name,
            "purpose": "turn",
            "model_ref": model_ref,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
        }
        if cost is not None:
            usage_payload["cost_usd"] = round(cost, 8)
        usage_event = self._emit_event("usage", usage_payload)
        return usage_event.seq if usage_event is not None else None

    def _emit_committed_turn(
        self,
        *,
        agent,
        phase: DebatePhase,
        turn_id: str,
        actions: list,
        committed_actions,
        usage_delta: dict,
        binding_usage: dict | None = None,
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
        if binding_usage is not None:
            # Binding-routed turn: native per-call usage from the adapter,
            # replacing the M1 snapshot-delta interim for this turn.
            usage_ref = self._emit_binding_usage(agent, turn_id, binding_usage)
        elif any(
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

        # The moderator gates inter-phase pausing (existing phase_gate semantics).
        self.moderator.gate_phase(self.phase_gate)

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

        # The moderator bounds this phase to its cap (FR-4.2). The Stage-1 base
        # protocol requests one round per phase; a round is every persona acting
        # once in stable order. rounds_for never exceeds the phase cap, so a
        # debate can never run past its declared ceiling.
        rounds = self.moderator.rounds_for(canonical_phase)
        agents_actions: dict = {}
        turn_count = 0
        for _exchange in range(rounds):
            for agent in self.agents:
                # Drain message queue before each agent acts
                self._process_message_queue()

                # Anti-convergence: inject persona-specific reinforcement (ALL phases)
                reinforcement = self._get_reinforcement_prompt(agent)
                agent.listen(reinforcement)

                # Devil's advocate: inject DA prompt during CROSS_EXAM only
                if phase == DebatePhase.CROSS_EXAM and agent == self._current_devils_advocate:
                    agent.listen(DEVILS_ADVOCATE_PROMPT)

                turn_id = self._next_turn_id()
                turn_count += 1
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

                binding_client = (
                    self.committee.client_for(agent.name)
                    if self.committee is not None
                    else None
                )
                if binding_client is not None:
                    # Binding-routed turn: stream think/talk deltas and capture
                    # native per-call usage at the adapter boundary.
                    committed_actions, latest, binding_usage = self._run_bound_turn(
                        agent, binding_client, turn_id
                    )
                    agents_actions[agent.name] = latest
                    self._emit_committed_turn(
                        agent=agent,
                        phase=phase,
                        turn_id=turn_id,
                        actions=latest,
                        committed_actions=committed_actions,
                        usage_delta={},
                        binding_usage=binding_usage,
                    )
                else:
                    # Legacy turn: snapshot the process-global counter delta (M1).
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

                # Structured artifacts (FR-4.4): lift the opening thesis / final
                # verdict from this turn's TALK prose. Emits thesis_recorded for
                # a parsed opening thesis; the verdict is recorded on the
                # moderator for extraction to consume first.
                self._capture_structured_record(agent, phase, latest)

                # Notify: agent finished
                if self.on_agent_done:
                    self.on_agent_done(agent.name, phase.value, latest)

        self._phase_history.append(phase.value)
        self._emit_event(
            "phase_completed",
            {
                "phase": canonical_phase,
                "index": self._phase_index,
                "turn_count": turn_count,
            },
        )
        self._phase_index += 1

        if self._phase_index >= len(self.PHASE_ORDER):
            self.current_phase = DebatePhase.COMPLETE

        return agents_actions

    def _process_message_queue(self):
        """Deliver queued user steering at a turn boundary via the moderator.

        The moderator owns steering delivery points (FR-4.1); the queue
        semantics are unchanged -- messages are ``(text, target_or_None)`` tuples,
        targeted delivery reaches the named persona with an observation relayed to
        the others, and an untargeted message broadcasts.
        """
        self.moderator.deliver_steering(
            self.message_queue,
            agents=self.agents,
            name_to_agent=self.name_to_agent,
            broadcast=self.broadcast,
        )

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
