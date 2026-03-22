# Phase 4 Context: Debate Engine and Verdict Extraction

## Phase Goal
Personas can conduct a structured investment debate about a company and produce individual buy/hold/sell votes with reasoning.

## Requirements
- DEBT-01: Debate follows structured phases: opening statements, cross-examination, rebuttal, final verdict
- DEBT-02: Debate orchestrator manages turn order and phase transitions using TinyWorld broadcast+run pattern
- DEBT-03: All personas receive the same financial DataPackage as shared context at debate start
- PERS-07: User can select which personas participate in a given debate (minimum 2)
- OUTP-01: System extracts a buy/hold/sell vote from each participating persona via ResultsExtractor
- OUTP-02: System generates a scorecard displaying all persona votes with key reasoning

## Success Criteria
1. A debate runs through all structured phases -- opening statements, cross-examination, rebuttal, final verdict -- with the orchestrator managing turn order and transitions
2. All personas receive the same DataPackage as shared context at debate start, and their statements reference this data
3. The user can select a subset of the 6 personas to participate in a given debate (minimum 2)
4. After the debate completes, the system extracts a structured buy/hold/sell vote from each persona via ResultsExtractor, with key reasoning attached
5. A scorecard is generated showing all persona votes and their core reasoning in a structured comparison format

## Key Constraints
- Must use TinyTroupe's TinyWorld as the environment (broadcast + run pattern)
- TinyWorld._handle_talk() broadcasts to all agents when target is None -- this is the inter-agent communication mechanism
- TinyWorld.run(steps=N) drives N rounds of agent activity -- each step calls all agents' act() in parallel/sequential
- ResultsExtractor.extract_results_from_agents() sends agent interaction history to LLM for structured extraction
- Must use sequential (not parallel) agent execution for debate -- turn order matters
- InvestorPersona extends TinyPerson with analyze_company() and format_vote() -- these are v1 stubs, debate engine may enhance or wrap them
- DataPackage.to_context_string() produces <=12K chars for LLM context injection
- API cost concern: 6 agents x multiple rounds -- need to limit debate to 3-4 rounds total
- Persona drift risk: system prompt re-injection between rounds may be needed
- GPT-5.2 with reasoning_effort=xhigh -- proxy gateway requires stream=True

## Technical Foundation (from Phases 1-3)
- InvestorPersona (src/tinyic/personas/base.py): TinyPerson subclass with analyze_company(), format_vote(), _load_philosophy()
- Persona registry (src/tinyic/personas/registry.py): load_persona(name) -> InvestorPersona, list_personas() -> 6 names
- 6 persona configs in src/tinyic/personas/configs/*.agent.json with 4-layer anti-convergence architecture
- DataPackage (src/tinyic/data/models.py): Pydantic model with ticker, company_name, financials, filings, news, social
- build_data_package(ticker) (src/tinyic/data/pipeline.py): orchestrator that fetches all data sources
- TinyWorld (src/tinytroupe/environment/tiny_world.py): broadcast(), run(), _handle_actions(), make_everyone_accessible()
- ResultsExtractor (src/tinytroupe/extraction/results_extractor.py): extract_results_from_agents(), extract_results_from_agent()
- Debate module stub: src/tinyic/debate/__init__.py (empty, ready for implementation)
- 91 tests passing (57 persona + 35 data pipeline tests)
- pop_latest_actions() is the reliable way to get agent output (act() return value is unreliable)

## Architecture (target)
```
src/tinyic/debate/
  __init__.py           # Public API: DebateOrchestrator, run_debate(), DebateResult, Scorecard
  orchestrator.py       # DebateOrchestrator: TinyWorld subclass managing structured debate phases
  models.py             # DebateResult, Scorecard, Vote -- Pydantic models for debate output
  extraction.py         # Vote extraction via ResultsExtractor with structured fields
```
