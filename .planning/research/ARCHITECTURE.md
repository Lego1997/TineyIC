# Architecture Patterns

**Domain:** AI-powered multi-agent investment committee simulation (TinyTroupe fork)
**Researched:** 2026-03-20

## Recommended Architecture

openIC is a layered system built on top of Microsoft TinyTroupe's agent/environment abstractions. The architecture has five major layers, where each layer depends only on the layers below it.

```
+------------------------------------------------------------------+
|                     PRESENTATION LAYER                           |
|  Streamlit UI (debate view, ticker input, scorecard, memo)       |
+------------------------------------------------------------------+
           |                              ^
           v                              |
+------------------------------------------------------------------+
|                    ORCHESTRATION LAYER                            |
|  DebateOrchestrator (extends TinyWorld)                          |
|  - Manages debate phases (opening, rounds, deliberation, vote)   |
|  - Routes financial data as stimuli to agents                    |
|  - Enforces turn order and time boxing                           |
|  - Emits events for UI streaming                                 |
+------------------------------------------------------------------+
           |                              ^
           v                              |
+------------------------------+  +-------------------------------+
|       AGENT LAYER            |  |    EXTRACTION LAYER           |
|  InvestorPersona             |  |  VoteExtractor                |
|  (extends TinyPerson)        |  |  (extends ResultsExtractor)   |
|  - 6 investor personas       |  |  MemoGenerator                |
|  - Philosophy prompts        |  |  (extends ArtifactExporter)   |
|  - Investment mental model   |  |  ScorecardBuilder             |
|  - Position tracker          |  |                               |
+------------------------------+  +-------------------------------+
           |
           v
+------------------------------------------------------------------+
|                   FINANCIAL DATA LAYER                            |
|  CompanyDataPipeline                                             |
|  - yfinance (price, fundamentals, ratios)                        |
|  - edgartools (SEC filings, XBRL financials)                     |
|  - News API (recent headlines, sentiment)                        |
|  - DataPackage (normalized bundle for agent consumption)          |
+------------------------------------------------------------------+
           |
           v
+------------------------------------------------------------------+
|                   INFRASTRUCTURE LAYER                            |
|  OpenAI API client (GPT 5.2 / Codex 5.3)                        |
|  TinyTroupe core (config, control, caching)                      |
|  Session/state management                                        |
+------------------------------------------------------------------+
```

### Component Boundaries

| Component | Responsibility | Communicates With | Module Location |
|-----------|---------------|-------------------|-----------------|
| **Streamlit UI** | User input, debate display, output rendering | Orchestrator (calls), Extraction (reads) | `app/` |
| **DebateOrchestrator** | Debate flow control, phase transitions, agent coordination | Agents (stimuli/actions), Data Pipeline (reads), Extraction (triggers) | `openic/orchestrator.py` |
| **InvestorPersona** | Embodies one investor's philosophy, generates analysis/arguments | Orchestrator (receives stimuli, returns actions), other personas (via TinyWorld broadcast) | `openic/personas/` |
| **CompanyDataPipeline** | Fetches, normalizes, caches financial data for a ticker | External APIs (yfinance, EDGAR, news), Orchestrator (provides DataPackage) | `openic/data/` |
| **VoteExtractor / ScorecardBuilder** | Extracts structured results from debate transcript | Agents (reads episodic memory), Orchestrator (triggered by) | `openic/extraction/` |
| **MemoGenerator** | Produces investment memo from debate results | Extraction results, OpenAI API (for summarization) | `openic/extraction/` |
| **TinyTroupe Core** | Agent runtime, LLM calls, caching, simulation control | OpenAI API, all layers above | `tinytroupe/` (forked) |

### Data Flow

The system has two primary data flows: the **debate flow** (real-time, interactive) and the **output flow** (post-debate, batch).

**Debate Flow (real-time):**

```
User enters ticker
       |
       v
CompanyDataPipeline fetches data --> DataPackage (structured dict)
       |
       v
DebateOrchestrator creates TinyWorld with 6 InvestorPersonas
       |
       v
Orchestrator injects DataPackage as initial stimulus to all agents
       |
       v
[DEBATE LOOP - multiple rounds]
  |
  |-- Phase 1: Opening Statements
  |   Each agent: listen(company_data) --> act() --> TALK action
  |   Orchestrator broadcasts each statement to all other agents
  |
  |-- Phase 2: Cross-Examination (N rounds)
  |   Orchestrator selects speaker order per round
  |   Each agent: listen(previous_statements) --> act() --> TALK action
  |   Agents respond to specific arguments from other personas
  |
  |-- Phase 3: Rebuttal
  |   Each agent given chance to address strongest counterarguments
  |
  |-- Phase 4: Final Verdict
  |   Each agent: think() --> produce buy/hold/sell vote with reasoning
  |
  v
All actions and statements stored in agent episodic memory
       |
       v
[Throughout: Streamlit UI receives events and renders in real-time]
```

**Output Flow (post-debate):**

```
Debate completes
       |
       v
VoteExtractor pulls structured votes from each agent's memory
       |
       v
ScorecardBuilder aggregates votes into comparison matrix
       |
       v
MemoGenerator synthesizes debate into investment memo
  (uses LLM to summarize thesis, risks, valuation, verdict)
       |
       v
Streamlit UI renders scorecard + downloadable memo
```

**User Steering Flow (optional, during debate):**

```
User enters question/prompt via st.chat_input
       |
       v
Orchestrator injects as USER_QUESTION stimulus
       |
       v
Next speaking agent addresses user question before continuing debate
```

## Patterns to Follow

### Pattern 1: TinyTroupe Extension via Subclassing

**What:** Extend TinyPerson and TinyWorld rather than modifying them. This keeps the fork maintainable and allows pulling upstream improvements.

**When:** Always. Every openIC-specific behavior should live in subclasses, not patches to TinyTroupe core.

**Example:**

```python
# openic/personas/investor_persona.py
from tinytroupe.agent import TinyPerson

class InvestorPersona(TinyPerson):
    """An investor agent with financial analysis capabilities."""

    def __init__(self, investor_config: dict):
        super().__init__(name=investor_config["name"])

        # Standard TinyPerson persona definition
        self.define("occupation", "Professional Investor")
        self.define("philosophy", investor_config["philosophy_prompt"])
        self.define("investment_framework", investor_config["framework"])

        # openIC-specific state
        self.current_position = None  # buy/hold/sell
        self.confidence = None
        self.key_arguments = []

    def analyze_company(self, data_package: dict) -> None:
        """Receive company data as a structured stimulus."""
        stimulus = self._format_financial_stimulus(data_package)
        self.listen(stimulus)

    def _format_financial_stimulus(self, data_package: dict) -> str:
        """Convert DataPackage to natural language stimulus."""
        # Transform structured data into prose the LLM can reason about
        ...
```

**Confidence:** HIGH -- this follows TinyTroupe's explicitly documented extension model.

### Pattern 2: Phased Debate Orchestration via Custom TinyWorld

**What:** Subclass TinyWorld to create a DebateOrchestrator that manages structured debate phases rather than free-form simulation steps. TinyWorld's `run()` method steps through agents in parallel; the orchestrator overrides this to enforce debate structure.

**When:** For managing the multi-round debate flow.

**Example:**

```python
# openic/orchestrator.py
from tinytroupe.environment import TinyWorld

class DebateOrchestrator(TinyWorld):
    """Manages structured investment debate among investor personas."""

    PHASES = ["opening", "cross_examination", "rebuttal", "verdict"]

    def __init__(self, company_data: dict, personas: list):
        super().__init__(name=f"IC-{company_data['ticker']}", agents=personas)
        self.company_data = company_data
        self.current_phase = None
        self.round_number = 0
        self.event_callbacks = []  # For UI streaming

    def run_debate(self, rounds: int = 3) -> dict:
        """Execute full debate and return results."""
        self._run_phase_opening()
        for r in range(rounds):
            self._run_phase_cross_examination(r)
        self._run_phase_rebuttal()
        return self._run_phase_verdict()

    def _emit_event(self, event_type: str, data: dict):
        """Notify UI listeners of debate progress."""
        for callback in self.event_callbacks:
            callback(event_type, data)
```

**Confidence:** HIGH -- TinyWorld is explicitly designed to be subclassed per the docs.

### Pattern 3: DataPackage as Normalized Financial Bundle

**What:** All financial data for a company is fetched once, normalized into a single `DataPackage` dict, and injected into the debate as the initial stimulus. No agent fetches its own data.

**When:** At debate start, before any agent interaction.

**Rationale:** Agents should reason over the same data (like real investment committees reviewing the same materials). Having agents fetch their own data would create inconsistency and add latency during the debate.

```python
# openic/data/pipeline.py
@dataclass
class DataPackage:
    ticker: str
    company_name: str
    sector: str
    market_cap: float
    # Price data
    current_price: float
    price_history_1y: pd.DataFrame
    # Fundamentals (from yfinance + EDGAR)
    income_statement: pd.DataFrame
    balance_sheet: pd.DataFrame
    cash_flow: pd.DataFrame
    key_ratios: dict  # PE, PB, ROE, debt/equity, FCF yield, etc.
    # SEC filings
    latest_10k_summary: str  # Extracted text, not raw XBRL
    latest_10q_summary: str
    # News
    recent_headlines: list[dict]  # title, date, source, sentiment
    # Metadata
    fetched_at: datetime
```

**Confidence:** HIGH -- standard pattern in financial analysis systems.

### Pattern 4: Event-Driven UI Updates via Callbacks

**What:** The DebateOrchestrator emits events (agent_spoke, phase_changed, vote_cast) that the Streamlit UI subscribes to. This decouples the simulation from the presentation.

**When:** Throughout the debate for real-time display.

**Rationale:** Streamlit reruns the full script on each interaction. Using `st.session_state` to store the debate state and event log, combined with `st.chat_message` containers for each agent utterance, provides a natural debate display.

```python
# In Streamlit app
for event in st.session_state.debate_events:
    if event["type"] == "agent_spoke":
        with st.chat_message(name=event["agent"], avatar=event["avatar"]):
            st.write(event["content"])
    elif event["type"] == "phase_changed":
        st.divider()
        st.subheader(f"Phase: {event['phase']}")
```

**Confidence:** MEDIUM -- Streamlit's chat elements support this pattern well, but real-time streaming during a long-running debate will require careful handling of Streamlit's execution model (likely using `st.empty()` containers and incremental updates via session state).

### Pattern 5: Persona Configuration as JSON Files

**What:** Each investor persona is defined as a JSON configuration file containing their philosophy, key principles, communication style, and decision heuristics. This follows TinyTroupe's native `.agent.json` pattern.

**When:** For all 6 investor personas.

**Rationale:** JSON configs are version-controllable, reviewable by domain experts, and follow TinyTroupe's documented pattern for persona definition.

```json
{
  "name": "Warren Buffett",
  "age": 95,
  "occupation": "Chairman, Berkshire Hathaway",
  "personality": [
    "Patient and long-term oriented",
    "Folksy communication style with Omaha metaphors",
    "Decisive once conviction is established"
  ],
  "investment_philosophy": "Buy wonderful companies at fair prices...",
  "key_frameworks": [
    "Circle of competence",
    "Moat analysis (durable competitive advantage)",
    "Owner earnings over reported earnings",
    "Margin of safety"
  ],
  "decision_heuristics": [
    "Would I be comfortable owning this if the market closed for 10 years?",
    "Is this business simple enough to understand?",
    "Does management have integrity and talent?"
  ],
  "communication_style": "Uses folksy metaphors, Omaha references, quotes from his letters...",
  "known_biases": [
    "Prefers US-centric businesses",
    "Skeptical of technology (historically, less so recently)",
    "Anchors to book value"
  ]
}
```

**Confidence:** HIGH -- directly follows TinyTroupe's documented agent specification pattern.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Modifying TinyTroupe Core Files Directly

**What:** Editing files inside `tinytroupe/` to add openIC-specific behavior.
**Why bad:** Makes pulling upstream updates painful. Creates hidden dependencies. Blurs the boundary between framework and application.
**Instead:** Subclass TinyPerson, TinyWorld, ResultsExtractor. Only modify TinyTroupe core for genuine bugs or configuration changes (e.g., swapping GPT-4 references for GPT-5.2).

### Anti-Pattern 2: Agents Fetching Their Own Financial Data

**What:** Giving agents TinyTool-based access to yfinance/EDGAR so they can query data during the debate.
**Why bad:** Agents may query different data at different times, creating an inconsistent information base. Adds latency during debate. Makes token usage unpredictable. Real investment committees work from a shared data room.
**Instead:** Pre-fetch all data into a DataPackage, inject once at debate start.

### Anti-Pattern 3: Free-Form Simulation Without Phase Structure

**What:** Using TinyWorld's default `run(steps=N)` and hoping agents self-organize into a debate.
**Why bad:** Without explicit phase management, agents will meander. The debate will lack structure. Some agents may dominate while others go silent. No clear path to producing a vote.
**Instead:** Explicitly orchestrate phases (opening, cross-examination, rebuttal, verdict) with the DebateOrchestrator controlling who speaks when.

### Anti-Pattern 4: Embedding Persona Prompts in Python Code

**What:** Defining investor philosophies as inline strings in Python files.
**Why bad:** Persona prompts are the most iterated-on part of the system. They need review by people who understand investing, not just code. Inline strings are hard to diff and maintain.
**Instead:** JSON config files in `openic/personas/configs/`, loaded at runtime.

### Anti-Pattern 5: Monolithic Streamlit File

**What:** Putting all UI logic in a single `app.py`.
**Why bad:** Streamlit apps grow quickly. Mixing debate orchestration, data fetching, and UI layout in one file creates a maintenance nightmare.
**Instead:** Separate into `app/main.py` (entry), `app/pages/` (Streamlit multipage), `app/components/` (reusable UI elements). Keep all business logic in `openic/`.

## Suggested Directory Structure

```
openIC/
|-- tinytroupe/              # Forked TinyTroupe (minimal modifications)
|   |-- agent.py             # TinyPerson, TinyMentalFaculty
|   |-- environment.py       # TinyWorld
|   |-- extraction.py        # ResultsExtractor, ArtifactExporter
|   |-- control.py           # Simulation control, caching
|   |-- clients.py           # LLM API clients
|   |-- factory.py           # TinyPersonFactory
|   |-- config.ini           # Framework configuration
|   +-- ...
|
|-- openic/                  # Application-specific code
|   |-- __init__.py
|   |-- personas/
|   |   |-- __init__.py
|   |   |-- investor_persona.py    # InvestorPersona(TinyPerson)
|   |   |-- persona_loader.py      # Load/validate JSON configs
|   |   +-- configs/
|   |       |-- buffett.json
|   |       |-- munger.json
|   |       |-- graham.json
|   |       |-- lynch.json
|   |       |-- marks.json
|   |       +-- li_lu.json
|   |
|   |-- orchestrator.py       # DebateOrchestrator(TinyWorld)
|   |
|   |-- data/
|   |   |-- __init__.py
|   |   |-- pipeline.py       # CompanyDataPipeline
|   |   |-- yfinance_source.py
|   |   |-- edgar_source.py
|   |   |-- news_source.py
|   |   +-- data_package.py   # DataPackage dataclass
|   |
|   +-- extraction/
|       |-- __init__.py
|       |-- vote_extractor.py    # VoteExtractor(ResultsExtractor)
|       |-- scorecard.py         # ScorecardBuilder
|       +-- memo_generator.py    # MemoGenerator(ArtifactExporter)
|
|-- app/                     # Streamlit UI
|   |-- main.py              # Entry point (streamlit run app/main.py)
|   |-- components/
|   |   |-- debate_view.py   # Chat-style debate display
|   |   |-- ticker_input.py  # Company search/input
|   |   |-- scorecard.py     # Vote display grid
|   |   +-- memo_view.py     # Investment memo display
|   +-- assets/
|       +-- avatars/         # Investor avatar images
|
|-- tests/
|   |-- test_personas/
|   |-- test_orchestrator/
|   |-- test_data/
|   +-- test_extraction/
|
|-- config.ini               # OpenAI API config, model selection
|-- requirements.txt
+-- pyproject.toml
```

## Scalability Considerations

| Concern | 1 user (v1) | 10 concurrent users | 100 concurrent users |
|---------|-------------|---------------------|----------------------|
| **LLM API costs** | ~$2-5 per debate (6 agents x multiple rounds x GPT-5.2) | Costs scale linearly; consider caching common tickers | Need token budgets, model tier fallback |
| **API rate limits** | Not a concern | May hit OpenAI rate limits | Queueing system, rate limiter required |
| **Financial data fetch** | 5-10 sec per ticker | Cache DataPackages (TTL: 1 hour) | Redis/memcached for shared cache |
| **Debate latency** | 2-5 min for full debate | Each debate independent | Consider async debate execution |
| **Streamlit** | Single process, fine | Streamlit Community Cloud limited | Need proper deployment (Streamlit on K8s or switch to FastAPI) |

For v1 (single user), none of these are blockers. The architecture supports later scaling because:
- DataPackage caching is built in from the start
- Orchestrator is decoupled from UI
- Business logic lives in `openic/`, not in Streamlit code

## Build Order (Dependency Graph)

The build order is driven by component dependencies. Each phase unlocks the next.

```
Phase 1: Foundation
  |-- Fork TinyTroupe, configure for GPT-5.2
  |-- InvestorPersona subclass (basic, 1-2 personas)
  +-- Prove: persona can listen() and act() with investment context

Phase 2: Financial Data
  |-- CompanyDataPipeline + DataPackage
  |-- yfinance integration
  |-- edgartools (SEC EDGAR) integration
  +-- Prove: can produce a complete DataPackage for any ticker

Phase 3: Debate Engine
  |-- DebateOrchestrator with phase management
  |-- All 6 persona JSON configs
  |-- Multi-round debate flow
  +-- Prove: full debate runs end-to-end in terminal/notebook

Phase 4: Output Generation
  |-- VoteExtractor + ScorecardBuilder
  |-- MemoGenerator
  +-- Prove: structured scorecard + memo from debate transcript

Phase 5: UI
  |-- Streamlit app with ticker input
  |-- Real-time debate display
  |-- Scorecard + memo rendering
  |-- User steering (optional questions during debate)
  +-- Prove: complete user journey from ticker to memo
```

**Why this order:**
- Phase 1 must come first because everything depends on the TinyTroupe fork working with GPT-5.2 and the persona subclass pattern being validated.
- Phase 2 can be developed in parallel with persona refinement, but the orchestrator (Phase 3) needs DataPackage to inject into debates.
- Phase 3 is the core product: the debate engine. It requires both personas and data.
- Phase 4 depends on Phase 3 producing debate transcripts to extract from.
- Phase 5 (UI) comes last because every preceding layer can be tested in a Jupyter notebook or CLI. Building UI first would create fragile coupling.

## Key Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Fork vs. dependency | Fork | Need to modify config for GPT-5.2, may need to adjust prompt templates. Subclassing alone may not suffice for all changes. |
| Data injection model | Pre-fetch, inject once | Consistency, predictability, lower token usage vs. agent-driven data fetching |
| Debate structure | Explicit phase orchestration | Structured output requires structured input. Free-form simulation would produce unpredictable debates. |
| Persona storage | JSON config files | Separates domain knowledge from code. Iterable by non-engineers. Follows TinyTroupe convention. |
| UI framework | Streamlit | Python-native, fast to build, chat elements for debate display. Matches TinyTroupe ecosystem. |
| Output extraction | TinyTroupe's ResultsExtractor | Already designed for pulling structured data from agent interactions. Extend, don't rebuild. |
| edgartools over sec-api | edgartools | Free, no API key needed, returns structured Python objects and DataFrames. Actively maintained. |

## Sources

- [Microsoft TinyTroupe GitHub](https://github.com/microsoft/TinyTroupe) -- PRIMARY
- [TinyTroupe API Documentation](https://microsoft.github.io/TinyTroupe/api/tinytroupe/index.html)
- [TinyTroupe Wiki: Principles and Mechanisms](https://github.com/microsoft/TinyTroupe/wiki/Principles-and-mechanisms)
- [TinyTroupe Paper (arXiv)](https://arxiv.org/html/2507.09788v1)
- [EdgarTools GitHub](https://github.com/dgunning/edgartools)
- [Streamlit Chat Elements Docs](https://docs.streamlit.io/develop/api-reference/chat)
- [Multi-Agent AI Trading System Architecture](https://medium.com/@ishveen/building-a-multi-agent-ai-trading-system-technical-deep-dive-into-architecture-b5ba216e70f3)
- [AI Agents in Financial Markets (arXiv)](https://arxiv.org/html/2603.13942)
- [Multi-Agent Orchestration Patterns 2025-2026](https://www.onabout.ai/p/mastering-multi-agent-orchestration-architectures-patterns-roi-benchmarks-for-2025-2026)

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| TinyTroupe extension model | HIGH | Documented in official wiki, paper, and API docs. Subclassing TinyPerson/TinyWorld is the intended pattern. |
| Financial data pipeline | HIGH | yfinance and edgartools are well-documented, free, actively maintained. Standard pattern. |
| Debate orchestration | MEDIUM | Custom TinyWorld subclass is documented, but specific debate-phase management is novel -- no existing examples found. Will need iteration. |
| Streamlit debate display | MEDIUM | Chat elements work well for conversation display. Real-time streaming during long-running simulation needs careful session state management -- not a standard Streamlit pattern. |
| Output extraction | HIGH | TinyTroupe's ResultsExtractor is explicitly designed for this. Extending it for votes/memos is straightforward. |
| Build order | HIGH | Dependencies are clear and each phase can be validated independently before moving to the next. |
