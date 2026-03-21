# Phase 4: Debate Engine and Verdict Extraction - Research

**Researched:** 2026-03-22
**Domain:** Multi-agent debate orchestration via TinyTroupe TinyWorld, structured extraction, Pydantic output models
**Confidence:** HIGH

## Summary

Phase 4 connects everything built so far -- personas (Phase 2) and data pipeline (Phase 3) -- into a multi-agent structured debate. The core challenge is orchestrating sequential, phase-gated debate rounds through TinyWorld's broadcast/run pattern while keeping each persona differentiated across 3-4 rounds of interaction. This requires subclassing TinyWorld (not composing it) to override `_step()` with phase-aware turn management, injecting DataPackage context via `broadcast()` at debate start, and using ResultsExtractor with structured fields/hints to extract buy/hold/sell votes after completion.

The existing TinyWorld codebase supports everything needed. Key discoveries: (1) `_handle_talk()` already broadcasts to all agents when target is None (`broadcast_if_no_target=True` by default), which means agents naturally "hear" each other during `run()`. (2) `_step()` can be overridden in subclasses (TinySocialNetwork demonstrates this pattern). (3) `broadcast_internal_goal()` is the right mechanism for injecting phase-transition instructions (e.g., "You are now in the cross-examination phase"). (4) ResultsExtractor supports `fields` and `fields_hints` parameters for structured extraction -- these map directly to vote/reasoning extraction. (5) Sequential execution (`parallelize=False`) is essential for debate coherence since turn order matters.

The primary risk is persona drift over multiple rounds -- agents tend to converge toward agreement as interaction history accumulates. Mitigation strategies: use `broadcast_internal_goal()` to re-inject persona-specific framing at each phase transition, keep debates to 4 phases (not 4+ rounds per phase), and structure cross-examination to force disagreement by explicitly asking agents to challenge specific claims.

**Primary recommendation:** Subclass TinyWorld as DebateOrchestrator. Override `_step()` to implement phase-gated sequential turns. Use `broadcast()` for DataPackage context injection, `broadcast_internal_goal()` for phase transitions, and `extract_results_from_agents()` with structured fields for vote extraction. Keep total API calls to ~24-36 (6 agents x 4 phases x 1 act each) to manage cost.

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DEBT-01 | Debate follows structured phases: opening statements, cross-examination, rebuttal, final verdict | TinyWorld._step() can be overridden to implement phase-gated logic. Each phase is a distinct step with tailored broadcast_internal_goal() instructions. Enum-based DebatePhase state machine drives transitions. |
| DEBT-02 | Debate orchestrator manages turn order and phase transitions using TinyWorld broadcast+run pattern | TinySocialNetwork proves TinyWorld subclassing works. Override _step() for sequential per-agent control. broadcast_if_no_target=True ensures all agents hear each other's TALK actions. run() drives N steps. |
| DEBT-03 | All personas receive the same DataPackage as shared context at debate start | DataPackage.to_context_string() produces <=12K chars. Inject via broadcast() before first step -- all agents receive same text via listen(). |
| PERS-07 | User can select which personas participate in a given debate (minimum 2) | load_persona(name) from registry, pass subset to DebateOrchestrator constructor. Validate len(personas) >= 2. add_agents() handles registration. |
| OUTP-01 | System extracts buy/hold/sell vote from each persona via ResultsExtractor | ResultsExtractor.extract_results_from_agents() with fields=["vote", "confidence", "reasoning"] and fields_hints for each. Returns list of dicts. |
| OUTP-02 | System generates scorecard displaying all persona votes with key reasoning | Pydantic Scorecard model aggregating Vote objects. Built from ResultsExtractor output. to_markdown() method for display. |

</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| TinyTroupe (fork) | v0.6.0 | Multi-agent environment, TinyWorld, TinyPerson | Already integrated in Phase 1; provides broadcast/run/extraction primitives |
| Pydantic | v2.x | Data models for DebateResult, Vote, Scorecard | Already used for DataPackage; consistent with project patterns |
| Python enum | stdlib | DebatePhase state machine | Simple, no dependencies, type-safe phase tracking |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| ResultsExtractor | TinyTroupe built-in | Structured extraction from agent interaction history | After debate completes, to extract votes |
| logging | stdlib | Debug output for debate flow | All orchestrator methods |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| TinyWorld subclass | Composition (wrap TinyWorld) | Subclass is better: need to override _step(), access internal agents list, leverage _handle_talk() broadcast. Composition would duplicate TinyWorld's agent management. |
| broadcast_internal_goal() for phase transitions | broadcast() with plain text | broadcast_internal_goal() uses INTERNAL_GOAL_FORMULATION stimulus type, which TinyPerson treats as a goal update in cognitive state -- stronger behavioral influence than conversational text |
| ResultsExtractor for votes | Manual text parsing of TALK content | ResultsExtractor uses a separate LLM call with structured extraction prompt -- more reliable than regex/keyword matching on free-form agent speech |

## Architecture Patterns

### Recommended Project Structure
```
src/tinyic/debate/
    __init__.py           # Public API: DebateOrchestrator, run_debate(), DebateResult, Scorecard, DebatePhase
    orchestrator.py       # DebateOrchestrator: TinyWorld subclass managing structured debate phases
    models.py             # DebateResult, Scorecard, Vote, DebatePhase -- Pydantic models for debate output
    extraction.py         # Vote extraction via ResultsExtractor with structured fields
    prompts.py            # Phase-specific prompt templates for broadcast_internal_goal()
```

### Pattern 1: TinyWorld Subclass with Phase-Gated _step()

**What:** DebateOrchestrator subclasses TinyWorld, overrides `_step()` to implement phase-aware sequential turn management. A DebatePhase enum tracks current phase. Each call to `_step()` advances through one debate phase.

**When to use:** For all debate execution.

**Why subclass, not compose:** TinyWorld's `_handle_talk()` method automatically broadcasts TALK actions to all agents when `broadcast_if_no_target=True`. This is the inter-agent communication mechanism -- agents hear each other naturally. Composition would require reimplementing this broadcast logic. Additionally, `_step()` is the designated override point (TinySocialNetwork demonstrates this pattern at line 76-80 of tiny_social_network.py).

```python
# Source: TinyWorld._step() pattern + TinySocialNetwork override pattern
from enum import Enum
from tinytroupe.environment import TinyWorld

class DebatePhase(str, Enum):
    SETUP = "setup"
    OPENING = "opening_statements"
    CROSS_EXAM = "cross_examination"
    REBUTTAL = "rebuttal"
    VERDICT = "final_verdict"
    COMPLETE = "complete"

class DebateOrchestrator(TinyWorld):
    PHASE_ORDER = [
        DebatePhase.OPENING,
        DebatePhase.CROSS_EXAM,
        DebatePhase.REBUTTAL,
        DebatePhase.VERDICT,
    ]

    def __init__(self, name: str, personas: list, data_package, **kwargs):
        super().__init__(
            name=name,
            agents=personas,
            broadcast_if_no_target=True,  # critical: enables inter-agent hearing
            **kwargs,
        )
        self.data_package = data_package
        self.current_phase = DebatePhase.SETUP
        self._phase_index = 0
        self.make_everyone_accessible()

    def _step(self, timedelta_per_step=None, **kwargs):
        """Override: each step = one debate phase, sequential agent turns."""
        phase = self.PHASE_ORDER[self._phase_index]
        self.current_phase = phase

        # 1. Broadcast phase instructions to all agents
        self.broadcast_internal_goal(self._phase_prompt(phase))

        # 2. Sequential agent turns (order matters for debate coherence)
        agents_actions = {}
        for agent in self.agents:
            actions = agent.act(return_actions=True)
            agents_actions[agent.name] = actions
            self._handle_actions(agent, agent.pop_latest_actions())

        # 3. Advance phase
        self._phase_index += 1
        if self._phase_index >= len(self.PHASE_ORDER):
            self.current_phase = DebatePhase.COMPLETE

        return agents_actions
```

### Pattern 2: DataPackage Context Injection via broadcast()

**What:** Before the first debate step, inject the DataPackage as shared context using `broadcast()`. This calls `agent.listen(speech, source=world)` on every agent, adding the data to their conversational history.

**When to use:** Once, before `run()` is called.

```python
# Source: TinyWorld.broadcast() at line 595 of tiny_world.py
def inject_context(self):
    """Inject DataPackage as shared context before debate begins."""
    context_str = self.data_package.to_context_string()
    preamble = (
        f"You are participating in an investment committee debate about "
        f"{self.data_package.company_name} ({self.data_package.ticker}). "
        f"Here is the financial data package for your analysis:\n\n"
        f"{context_str}"
    )
    self.broadcast(preamble)
```

**Why broadcast() not broadcast_context_change():** `broadcast_context_change()` calls `agent.change_context(context)` which replaces the agent's context list in mental state. This is too disruptive -- it overwrites location context. `broadcast()` calls `agent.listen()` which adds a CONVERSATION stimulus to the agent's episodic memory, preserving all other state. The data becomes part of the conversation history that the agent references during act().

### Pattern 3: Phase-Specific Internal Goal Prompts

**What:** Each debate phase gets a distinct `broadcast_internal_goal()` prompt that frames what agents should do in that phase. This uses the INTERNAL_GOAL_FORMULATION stimulus type, which TinyPerson stores in cognitive state goals.

**When to use:** At the start of each phase in `_step()`.

```python
PHASE_PROMPTS = {
    DebatePhase.OPENING: (
        "Present your opening investment thesis on {company}. "
        "State your preliminary assessment (bullish, bearish, or neutral) "
        "and your top 3 reasons, drawing on the financial data provided. "
        "Speak from YOUR unique investment philosophy. "
        "Be specific about which data points support your view."
    ),
    DebatePhase.CROSS_EXAM: (
        "Challenge the other committee members' arguments. "
        "Identify the WEAKEST point in each other investor's thesis. "
        "Ask probing questions. Point out data they may have overlooked or misinterpreted. "
        "Do NOT agree just to be polite -- genuine disagreement produces better analysis. "
        "Stay true to YOUR investment philosophy when critiquing others."
    ),
    DebatePhase.REBUTTAL: (
        "Respond to the challenges raised against your thesis. "
        "Defend your position where you believe you are right. "
        "Concede points where the criticism is valid, but explain what it changes. "
        "Refine your thesis based on the discussion -- you may adjust but must explain why. "
        "Do NOT simply agree with the majority."
    ),
    DebatePhase.VERDICT: (
        "Deliver your FINAL investment verdict on {company}. "
        "State clearly: BUY, HOLD, or SELL. "
        "State your confidence level: HIGH, MEDIUM, or LOW. "
        "Give your top 3 reasons for this verdict. "
        "If your view changed during the debate, explain what changed it."
    ),
}
```

### Pattern 4: Structured Vote Extraction

**What:** After debate completes, use ResultsExtractor with explicit fields and field_hints to extract structured votes from each agent's interaction history.

**When to use:** After `run()` completes all 4 debate phases.

```python
# Source: ResultsExtractor.extract_results_from_agents() at line 53 of results_extractor.py
from tinytroupe.extraction import ResultsExtractor

def extract_votes(orchestrator):
    extractor = ResultsExtractor(
        extraction_objective=(
            "Extract the investor's FINAL investment verdict from the debate, "
            "including their vote, confidence level, and key reasoning."
        ),
        situation=(
            f"An investment committee debate about {orchestrator.data_package.company_name} "
            f"({orchestrator.data_package.ticker}) has concluded. "
            f"Extract each investor's final position."
        ),
        fields=["vote", "confidence", "reasoning", "key_risks", "changed_mind"],
        fields_hints={
            "vote": "Must be exactly one of: BUY, HOLD, or SELL",
            "confidence": "Must be exactly one of: HIGH, MEDIUM, or LOW",
            "reasoning": "List of 2-4 bullet points summarizing the investor's key arguments",
            "key_risks": "The top 1-2 risks the investor identified",
            "changed_mind": "Boolean: did the investor change their position during the debate?",
        },
    )

    results = extractor.extract_results_from_agents(
        agents=orchestrator.agents,
        verbose=False,
    )
    return results  # list of dicts, one per agent
```

### Pattern 5: Convenience Function API

**What:** Top-level `run_debate()` function that wires together persona loading, data fetching, debate orchestration, and vote extraction into a single call.

```python
def run_debate(
    ticker: str,
    persona_names: list[str],
    data_package=None,  # optional: pass pre-built DataPackage
) -> DebateResult:
    """Run a complete investment committee debate.

    Args:
        ticker: Stock ticker symbol.
        persona_names: List of persona registry names (min 2).
        data_package: Pre-built DataPackage, or None to fetch live.

    Returns:
        DebateResult with debate transcript and scorecard.
    """
    # Validate
    if len(persona_names) < 2:
        raise ValueError("Minimum 2 personas required for debate")

    # Load personas
    personas = [load_persona(name) for name in persona_names]

    # Build data package if not provided
    if data_package is None:
        data_package = build_data_package(ticker)

    # Create and run debate
    orchestrator = DebateOrchestrator(
        name=f"IC-{ticker}",
        personas=personas,
        data_package=data_package,
    )
    orchestrator.inject_context()
    orchestrator.run(steps=4, parallelize=False, randomize_agents_order=False)

    # Extract votes
    votes = extract_votes(orchestrator)

    # Build result
    return DebateResult(
        ticker=ticker,
        company_name=data_package.company_name,
        votes=votes,
        scorecard=build_scorecard(votes),
        transcript=orchestrator.pretty_current_interactions(),
    )
```

### Anti-Patterns to Avoid

- **Using run() with parallelize=True:** Debate requires sequential turns. Parallel execution means agents act simultaneously without hearing each other's arguments. Always use `parallelize=False` and `randomize_agents_order=False`.

- **Composing TinyWorld instead of subclassing:** You lose `_handle_talk()` broadcast behavior, agent management, communication display, and the entire `run()`/`_step()` lifecycle. Subclassing preserves all of this while only overriding the step logic.

- **Using change_context() for DataPackage injection:** `change_context()` replaces the agent's context dict in mental state. Use `broadcast()` which adds to conversational history via `listen()` -- the data becomes part of what agents reference during `act()`.

- **Running multiple rounds per phase:** Each phase should be exactly 1 step (1 act() per agent). Running multiple rounds within a phase inflates API cost (6 agents x N rounds x 4 phases) and increases convergence risk. 4 total steps = 24 act() calls for 6 agents.

- **Extracting votes by parsing TALK action content:** Agent TALK output is free-form text. Use ResultsExtractor which makes a separate LLM call with structured extraction prompt -- much more reliable than regex or keyword matching.

- **Not using make_everyone_accessible():** Without this, agents cannot "see" each other in the environment. Call it once in __init__() so all agents are accessible to each other during the debate.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Inter-agent message passing | Custom message queue or pub/sub | TinyWorld._handle_talk() with broadcast_if_no_target=True | Already broadcasts TALK actions to all agents; handles source/target routing automatically |
| Structured data extraction from LLM output | Regex parsing of agent TALK content | ResultsExtractor with fields + fields_hints | Uses Mustache-templated extraction prompt + separate LLM call; handles JSON parsing, null fields, format validation |
| Agent memory and interaction history | Custom conversation log | TinyPerson.episodic_memory + pretty_current_interactions() | Built into TinyPerson; automatically tracks all stimuli and actions with timestamps |
| Phase transition state machine | Ad-hoc if/else chains | Python Enum + list index | Clean, testable, serializable phase tracking |
| Agent cognitive state updates | Manual prompt rebuilding | broadcast_internal_goal() -> internalize_goal() -> _update_cognitive_state() | Properly updates goals in mental state and triggers prompt reset |

**Key insight:** TinyWorld and TinyPerson already implement the entire multi-agent communication substrate. The debate engine's job is purely orchestration -- deciding WHEN and WHAT to broadcast, not HOW agents communicate.

## Common Pitfalls

### Pitfall 1: Persona Convergence Over Multiple Rounds
**What goes wrong:** By the rebuttal/verdict phases, all 6 agents start agreeing and producing similar analyses. The LLM's tendency toward consensus overrides persona differentiation.
**Why it happens:** Each agent's episodic memory accumulates the other agents' arguments. The LLM's RLHF training rewards agreement and politeness. After hearing 5 other perspectives, agents "update" toward the majority view.
**How to avoid:**
1. Use `broadcast_internal_goal()` at each phase with explicit anti-convergence language: "Do NOT simply agree with the majority. Stay true to YOUR investment philosophy."
2. Structure cross-examination to force disagreement: "Identify the WEAKEST point in each other investor's thesis."
3. Keep debates to 4 phases total (not 4+ rounds per phase) to limit exposure to convergence pressure.
4. The persona configs already have strong "NEVER" constraints and "REJECT" beliefs that resist convergence -- these are in the system prompt and persist across all rounds.
**Warning signs:** Multiple agents giving the same vote with similar reasoning; agents citing other agents' frameworks instead of their own.

### Pitfall 2: Token Budget Explosion
**What goes wrong:** 6 agents x 4 phases x long responses = massive token consumption and API cost. The interaction history grows with each phase, so later phases include earlier conversations in the prompt.
**Why it happens:** Each agent.act() call includes the agent's full episodic memory (all prior stimuli + actions) in the prompt. By phase 4, each agent has heard 5 other agents' statements x 3 prior phases = 15+ messages in history, plus the DataPackage context (~12K chars).
**How to avoid:**
1. Keep phases to 1 act() per agent per phase (no multi-round phases).
2. DataPackage.to_context_string() already caps at 12K chars.
3. Total budget: 4 phases x 6 agents x 1 act = 24 LLM calls for the debate + 6 extraction calls = 30 total. At ~$0.05-0.10 per GPT-5.2 call with reasoning, that is $1.50-$3.00 per debate.
4. Consider using `last_n` parameter on pretty_current_interactions() for extraction to limit context sent to the extraction LLM.
**Warning signs:** Debate taking >5 minutes; cost per debate exceeding $5.

### Pitfall 3: Agents Not Hearing Each Other
**What goes wrong:** Agents produce opening statements but don't reference or respond to what other agents said.
**Why it happens:** If `broadcast_if_no_target=True` is not set, or if `make_everyone_accessible()` is not called, TALK actions may not be delivered to other agents. Also, if using `parallelize=True`, agents act simultaneously without seeing each other's latest output.
**How to avoid:**
1. Always set `broadcast_if_no_target=True` (default in TinyWorld.__init__()).
2. Call `make_everyone_accessible()` in DebateOrchestrator.__init__().
3. Always use `parallelize=False` and `randomize_agents_order=False` in run().
4. Sequential execution ensures Agent B hears Agent A's TALK before acting.
**Warning signs:** Agents producing generic analyses without referencing other speakers; cross-examination that asks generic questions instead of responding to specific claims.

### Pitfall 4: pop_latest_actions() Consumed Before Extraction
**What goes wrong:** Calling `pop_latest_actions()` clears the action buffer. If the orchestrator pops actions during _step() (which it must, for _handle_actions), those actions are gone from the buffer for later inspection.
**Why it happens:** `pop_latest_actions()` is destructive -- it clears `_actions_buffer` and returns the actions. TinyWorld._step() calls `_handle_actions(agent, agent.pop_latest_actions())` which consumes the buffer.
**How to avoid:** This is actually fine -- the actions are stored in episodic memory via `store_in_memory()` before being buffered. ResultsExtractor reads from `agent.pretty_current_interactions()` which pulls from episodic memory, not from the actions buffer. The extraction path is independent of the actions buffer.
**Warning signs:** None if using ResultsExtractor correctly. Only a problem if you try to manually inspect pop_latest_actions() after run() completes.

### Pitfall 5: TinyPerson Name Conflicts in Global Registry
**What goes wrong:** TinyPerson maintains a class-level `all_agents` dict keyed by name. If you create multiple DebateOrchestrators (e.g., in tests), agent names collide.
**Why it happens:** `TinyPerson.__init__()` registers the agent in `TinyPerson.all_agents`. Loading "Warren Buffett" twice raises or overwrites.
**How to avoid:** Clear `TinyPerson.all_agents` between debates or in test fixtures (the test suite already does this -- see conftest.py's `clear_agent_registry` fixture). For production, ensure single-debate-at-a-time execution or add debate-scoped namespacing.
**Warning signs:** ValueError on "Agent names must be unique" when running consecutive debates.

## Code Examples

### Complete Debate Flow (Verified from Source Code Analysis)

```python
# Source: Synthesized from TinyWorld, TinyPerson, ResultsExtractor source code
from tinyic.personas.registry import load_persona
from tinyic.data.pipeline import build_data_package
from tinyic.debate.orchestrator import DebateOrchestrator
from tinyic.debate.extraction import extract_votes
from tinyic.debate.models import DebateResult

# 1. User selects personas (PERS-07)
persona_names = ["warren_buffett", "charlie_munger", "benjamin_graham"]
personas = [load_persona(name) for name in persona_names]

# 2. Build shared data context (DEBT-03)
data_package = build_data_package("AAPL")

# 3. Create orchestrator (DEBT-02)
orchestrator = DebateOrchestrator(
    name="IC-AAPL",
    personas=personas,
    data_package=data_package,
)

# 4. Inject context and run debate (DEBT-01)
orchestrator.inject_context()
orchestrator.run(steps=4, parallelize=False, randomize_agents_order=False)

# 5. Extract votes (OUTP-01)
votes = extract_votes(orchestrator)

# 6. Build scorecard (OUTP-02)
scorecard = build_scorecard(votes, data_package)
print(scorecard.to_markdown())
```

### TinyWorld._handle_talk() Broadcast Behavior (Verified)

```python
# Source: tiny_world.py lines 571-589
# When an agent TALKs without specifying a target, the world broadcasts to all:
def _handle_talk(self, source_agent, content, target):
    target_agent = self.get_agent_by_name(target)
    if target_agent is not None:
        target_agent.listen(content, source=source_agent)
    elif self.broadcast_if_no_target:
        # This is the key mechanism: all other agents hear the speech
        self.broadcast(content, source=source_agent)
```

### ResultsExtractor Field Hints (Verified)

```python
# Source: results_extractor.py lines 115-121, extraction prompt mustache template
# fields_hints are rendered as "Additional constraint for field `X`: Y"
extractor = ResultsExtractor(
    fields=["vote", "confidence", "reasoning"],
    fields_hints={
        "vote": "Must be exactly one of: BUY, HOLD, or SELL. Nothing else.",
        "confidence": "Must be exactly one of: HIGH, MEDIUM, or LOW",
        "reasoning": "A list of 2-4 concise bullet points",
    },
)
# The Mustache template renders these as:
# - Additional constraint for field `vote`: Must be exactly one of: BUY, HOLD, or SELL. Nothing else.
# - Additional constraint for field `confidence`: Must be exactly one of: HIGH, MEDIUM, or LOW
```

### Pydantic Output Models

```python
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class VoteChoice(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"

class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class Vote(BaseModel):
    """A single investor's vote extracted from debate."""
    investor: str
    vote: VoteChoice
    confidence: Confidence
    reasoning: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    changed_mind: bool = False

class Scorecard(BaseModel):
    """Aggregated scorecard from all investor votes."""
    ticker: str
    company_name: str
    votes: list[Vote]
    consensus: Optional[VoteChoice] = None  # majority vote, None if tied
    bull_count: int = 0
    bear_count: int = 0
    hold_count: int = 0

    def to_markdown(self) -> str:
        """Format scorecard as markdown table."""
        lines = [
            f"# Investment Committee Scorecard: {self.company_name} ({self.ticker})",
            "",
            f"**Consensus:** {self.consensus.value if self.consensus else 'NO CONSENSUS'}",
            f"**Bulls:** {self.bull_count} | **Bears:** {self.bear_count} | **Holds:** {self.hold_count}",
            "",
            "| Investor | Vote | Confidence | Key Reasoning |",
            "|----------|------|------------|---------------|",
        ]
        for vote in self.votes:
            reasoning = "; ".join(vote.reasoning[:3])
            lines.append(f"| {vote.investor} | {vote.vote.value} | {vote.confidence.value} | {reasoning} |")
        return "\n".join(lines)

class DebateResult(BaseModel):
    """Complete result from a debate session."""
    ticker: str
    company_name: str
    scorecard: Scorecard
    phases_completed: list[str]
    transcript: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual agent.listen()/act() loops | TinyWorld.run(steps=N) with _step() override | TinyTroupe v0.5+ | Cleaner orchestration with built-in intervention support |
| String parsing of agent output | ResultsExtractor with structured fields | TinyTroupe v0.5+ | Reliable JSON extraction via separate LLM call |
| Parallel agent execution | Sequential with randomize_agents_order=False | Project-specific | Required for debate coherence -- agents must hear each other in order |

**Not applicable (deferred to v2):**
- DEBT-04 (persona drift mitigation via system prompt re-injection each round) -- v1 uses broadcast_internal_goal() anti-convergence prompts instead
- DEBT-05 (structural dissent) -- v1 relies on persona configs' natural disagreement
- DEBT-06 (fact-checking layer) -- v1 trusts agent faithfulness to DataPackage

## Open Questions

1. **Optimal cross-examination structure**
   - What we know: Cross-exam works best when agents are directed to critique specific claims, not generic questions.
   - What's unclear: Should each agent address ALL other agents, or only 1-2? With 6 agents, addressing all produces long responses.
   - Recommendation: Have each agent address the 2 most different perspectives (e.g., Buffett challenges Graham's quantitative-only approach). This can be hardcoded or dynamically determined from opening statements. Start with "challenge the arguments you disagree with most" and iterate.

2. **Episodic memory growth and context window**
   - What we know: Each agent accumulates all stimuli (broadcasts, TALK from others) in episodic memory. By phase 4, each agent may have 20+ messages.
   - What's unclear: Whether GPT-5.2's 128K context window is sufficient for 6 agents x 4 phases of accumulated debate history plus system prompt plus DataPackage.
   - Recommendation: Monitor token counts. If hitting limits, use `last_n` on pretty_current_interactions() for extraction. The debate itself should be fine since each agent only has their own history (not all 6), but the extraction LLM call via extract_results_from_world() sends ALL agents' histories.

3. **Vote extraction reliability**
   - What we know: ResultsExtractor produces JSON via LLM; the fields_hints mechanism constrains output format.
   - What's unclear: How reliably GPT-5.2 follows the "exactly BUY/HOLD/SELL" constraint. May return variations like "STRONG BUY" or "CONDITIONAL SELL".
   - Recommendation: Parse extracted vote with fuzzy matching fallback. If vote string contains "BUY" -> BUY, "SELL" -> SELL, else HOLD. Add validation in Vote model's validator.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest with markers |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_debate.py -x -v --timeout=60` |
| Full suite command | `uv run pytest tests/ -v --timeout=120` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DEBT-01 | Debate runs through 4 structured phases | unit (mocked agents) | `uv run pytest tests/test_debate.py::TestDebateOrchestrator::test_four_phases -x` | No -- Wave 0 |
| DEBT-02 | Orchestrator manages sequential turn order | unit (mocked agents) | `uv run pytest tests/test_debate.py::TestDebateOrchestrator::test_sequential_turns -x` | No -- Wave 0 |
| DEBT-03 | DataPackage broadcast to all agents at start | unit (mocked agents) | `uv run pytest tests/test_debate.py::TestDebateOrchestrator::test_context_injection -x` | No -- Wave 0 |
| PERS-07 | User selects subset of personas (min 2) | unit | `uv run pytest tests/test_debate.py::TestDebateOrchestrator::test_persona_selection -x` | No -- Wave 0 |
| OUTP-01 | Extract buy/hold/sell vote via ResultsExtractor | unit (mocked extractor) | `uv run pytest tests/test_debate.py::TestVoteExtraction::test_extract_votes -x` | No -- Wave 0 |
| OUTP-02 | Generate scorecard with votes + reasoning | unit | `uv run pytest tests/test_debate.py::TestScorecard::test_scorecard_generation -x` | No -- Wave 0 |
| E2E | Full debate with live API | live_api | `uv run pytest tests/test_debate.py::test_live_debate -x -m live_api --timeout=300` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_debate.py -x -v --timeout=60`
- **Per wave merge:** `uv run pytest tests/ -v --timeout=120`
- **Phase gate:** Full suite green before verification

### Wave 0 Gaps
- [ ] `tests/test_debate.py` -- all debate orchestrator and extraction tests
- [ ] Mock fixtures for TinyPerson agents that return predetermined TALK actions (avoid live API calls in unit tests)
- [ ] Pydantic model tests for Vote, Scorecard, DebateResult validation

## Sources

### Primary (HIGH confidence)
- TinyWorld source code: `src/tinytroupe/environment/tiny_world.py` -- broadcast(), run(), _step(), _handle_talk(), _handle_actions(), broadcast_internal_goal(), broadcast_context_change(), make_everyone_accessible()
- TinySocialNetwork source code: `src/tinytroupe/environment/tiny_social_network.py` -- demonstrates TinyWorld subclass pattern with _step() override
- TinyPerson source code: `src/tinytroupe/agent/tiny_person.py` -- listen(), act(), pop_latest_actions(), internalize_goal(), change_context(), pretty_current_interactions(), _observe(), _update_cognitive_state()
- ResultsExtractor source code: `src/tinytroupe/extraction/results_extractor.py` -- extract_results_from_agents(), extract_results_from_agent(), extract_results_from_world(), fields/fields_hints mechanism
- Extraction prompt template: `src/tinytroupe/extraction/prompts/interaction_results_extractor.mustache` -- Mustache template with fields/fields_hints rendering
- Intervention source code: `src/tinytroupe/steering/intervention.py` -- Intervention class pattern (not needed for v1 but shows TinyWorld extensibility)
- InvestorPersona source code: `src/tinyic/personas/base.py` -- analyze_company(), format_vote(), _load_philosophy()
- DataPackage source code: `src/tinyic/data/models.py` -- to_context_string(), model structure
- Existing persona configs: `src/tinyic/personas/configs/*.agent.json` -- style, beliefs, personality.traits, skills, behaviors, other_facts structure
- Project constants: `src/tinyic/constants.py` -- DEFAULT_DEBATE_ROUNDS=4, MIN_PERSONAS=2, MAX_PERSONAS=6
- 04-CONTEXT.md: Phase 4 context document with constraints and architecture target

### Secondary (MEDIUM confidence)
- Phase 2 research: `.planning/phases/02-persona-engineering/02-RESEARCH.md` -- anti-convergence architecture, persona differentiation strategies
- Project REQUIREMENTS.md: `.planning/REQUIREMENTS.md` -- full requirement definitions and v2 deferral list

### Tertiary (LOW confidence)
- Token budget estimates for GPT-5.2 with reasoning_effort=xhigh -- based on general GPT-4/5 pricing patterns, actual costs may differ

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all components (TinyWorld, ResultsExtractor, Pydantic) are already in the project and fully source-verified
- Architecture: HIGH -- subclass pattern proven by TinySocialNetwork; broadcast/run/extraction APIs verified line-by-line in source
- Pitfalls: HIGH -- convergence risk is well-documented in LLM multi-agent literature; token budget and agent registry issues verified from source
- Code examples: HIGH -- all patterns verified against actual source code line numbers

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (stable -- TinyTroupe fork is version-locked, no upstream changes expected)
