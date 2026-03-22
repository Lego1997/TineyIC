"""DebateOrchestrator -- a TinyWorld subclass for structured investment debates."""

from tinytroupe.environment.tiny_world import TinyWorld

from tinyic.constants import MAX_PERSONAS, MIN_PERSONAS
from tinyic.data.models import DataPackage

from .models import DebatePhase
from .prompts import CONTEXT_PREAMBLE, PHASE_PROMPTS


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

    def __init__(self, name: str, personas: list, data_package: DataPackage, **kwargs):
        if len(personas) < MIN_PERSONAS:
            raise ValueError(
                f"At least {MIN_PERSONAS} personas required, got {len(personas)}"
            )
        if len(personas) > MAX_PERSONAS:
            raise ValueError(
                f"At most {MAX_PERSONAS} personas allowed, got {len(personas)}"
            )

        super().__init__(
            name=name,
            agents=personas,
            broadcast_if_no_target=True,
            **kwargs,
        )

        self.data_package = data_package
        self.current_phase = DebatePhase.SETUP
        self._phase_index = 0
        self._phase_history: list[str] = []

        self.make_everyone_accessible()

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
    # Step override (replaces TinyWorld._step entirely)
    # ------------------------------------------------------------------

    def _step(self, timedelta_per_step=None, **kwargs):
        """Execute one debate phase: broadcast goal, then each agent acts in order."""
        if self._phase_index >= len(self.PHASE_ORDER):
            self.current_phase = DebatePhase.COMPLETE
            return {}

        phase = self.PHASE_ORDER[self._phase_index]
        self.current_phase = phase

        # Broadcast the phase prompt as an internal goal
        prompt = PHASE_PROMPTS[phase].format(company=self.data_package.company_name)
        self.broadcast_internal_goal(prompt)

        # Agents act sequentially in stable order
        agents_actions: dict = {}
        for agent in self.agents:
            actions = agent.act(return_actions=True)
            agents_actions[agent.name] = actions
            self._handle_actions(agent, agent.pop_latest_actions())

        self._phase_history.append(phase.value)
        self._phase_index += 1

        if self._phase_index >= len(self.PHASE_ORDER):
            self.current_phase = DebatePhase.COMPLETE

        return agents_actions

    # ------------------------------------------------------------------
    # Convenience runner
    # ------------------------------------------------------------------

    def run_debate(self) -> None:
        """Run the full debate: inject context, then execute all phases."""
        self.inject_context()
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
