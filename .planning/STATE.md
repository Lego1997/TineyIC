---
gsd_state_version: 1.0
milestone: v0.6
milestone_name: milestone
status: executing
last_updated: "2026-03-22T03:58:00.000Z"
progress:
  total_phases: 5
  completed_phases: 3
  total_plans: 8
  completed_plans: 7
---

# State: openIC

## Project Reference

**Core Value:** Investor personas must be convincingly distinct and philosophically accurate -- each argues from their real-world framework, producing genuinely differentiated perspectives.

**Current Focus:** Phase 04 — debate-engine (IN PROGRESS)

## Current Position

Phase: 04 (debate-engine)
Plan: 1 of 2 complete

## Performance Metrics

| Metric | Value |
|--------|-------|
| Plans completed | 7 |
| Plans total | 8 (Phase 1: 2, Phase 2: 2, Phase 3: 2, Phase 4: 2) |
| Phases completed | 3/5 |
| Requirements completed | 15/25 |
| Estimated cost/debate | $3-8 (from research) |
| Phase 01 P01 | 66min | 2 tasks | 112 files |
| Phase 01 P02 | 21min | 2 tasks | 3 files |
| Phase 03 P01 | 4min | 2 tasks | 8 files |
| Phase 03 P02 | 4min | 2 tasks | 5 files |
| Phase 04 P01 | 11min | 2 tasks | 5 files |

## Accumulated Context

### Key Decisions

| Decision | Rationale | Phase |
|----------|-----------|-------|
| Fork TinyTroupe as local package (not submodule) | Need deep customization, avoid submodule complexity | Phase 1 |
| Personas before debate engine | Research unambiguous: validate differentiation in isolation before multi-agent | Phase 2 before 4 |
| Curated prompts over RAG | Simpler, more controllable persona accuracy for v1 | Phase 2 |
| Free data sources only | No cost barrier for v1 (yfinance, edgartools, xAI API) | Phase 3 |
| Lazy imports in fetchers | yfinance imported inside function body to avoid import-time side effects | Phase 3 |
| Real pandas DataFrames in tests | Mock yfinance Ticker but use real DataFrames for .empty/.iloc/.index | Phase 3 |
| GPT-5.2 (not Codex 5.3) | Codex is coding-specialized, not suited for financial analysis | Phase 1 |
| Phases 2+3 parallelizable | Data pipeline has no dependency on persona work | Phases 2-3 |
| GPT-5 models treated as reasoning models | Patched _is_reasoning_model() to include "gpt-5" so reasoning_effort is passed | Phase 1 |
| llama-index deferred (wrapped in try/except) | Version incompatibility; semantic memory not needed for v1 | Phase 1 |
| InvestorPersona uses include_persona_definitions() | load_specification() is a factory method; merge JSON into existing instance instead | Phase 1 |
| Proxy gateway requires stream=True | API proxy rejects non-streaming; added _collect_stream() to reassemble chunks | Phase 1 |
| base_url from config.ini for proxy routing | Configurable endpoint rather than hardcoded; supports proxy and direct OpenAI | Phase 1 |
| Safe .pop() for reasoning model param cleanup | Prevents KeyError when params already filtered by None-removal | Phase 1 |
| pop_latest_actions() for TinyTroupe action retrieval | TinyTroupe stores actions internally; act() return value is unreliable | Phase 1 |
| 4-layer anti-convergence architecture | Style + contrastive beliefs + signature vocabulary + negative constraints | Phase 2 |
| _sources field outside persona dict | Attribution tracking without polluting TinyTroupe prompt injection | Phase 2 |
| Style field encodes reasoning STRUCTURE not just tone | TinyTroupe over-emphasizes style; each persona starts analysis with different first question | Phase 2 |
| analyze_company/format_vote are simple wrappers for v1 | Phase 4 debate engine will enhance with structured extraction and JSON-formatted votes | Phase 2 |
| Override _step() entirely in DebateOrchestrator | Avoids TinyWorld's parallelization and randomization -- debate needs sequential, stable agent order | Phase 4 |
| Fuzzy vote validator using string containment | Handles ResultsExtractor returning "STRONG BUY" or "CONDITIONAL SELL" robustly | Phase 4 |
| MagicMock without spec for agent mocks | TinyWorld.add_agent dynamically sets agent.environment; spec would block this | Phase 4 |

### Key Risks

| Risk | Severity | Mitigation | Phase |
|------|----------|------------|-------|
| OpenAI SDK v2.x incompatibility with TinyTroupe | RESOLVED | Patched _is_reasoning_model() and fixed max_completion_tokens; imports work | 1 |
| Persona convergence (all sound the same) | MITIGATED | 4-layer anti-convergence (style/beliefs/vocabulary/constraints); 57 unit tests + human checkpoint passed | 2 |
| Persona drift over multi-turn debate | HIGH | System prompt re-injection, 3-4 round limit | 4 |
| API cost explosion (6 agents x multiple rounds) | MODERATE | Token budgets, prompt caching, round summaries | 4 |
| Streamlit rerun vs long-running debate | MODERATE | Background thread, state checkpointing | 5 |

### Todos

- [x] Begin Phase 1 planning after roadmap approval
- [x] Execute Plan 01-01: Scaffold uv workspace and InvestorPersona base class
- [x] Execute Plan 01-02: Validate GPT-5.2 compatibility with live API smoke test
- [x] Begin Phase 2 planning: Persona Engineering
- [x] Execute Plan 02-01: Classic value cluster (Graham, Buffett, Munger) + registry + tests
- [x] Execute Plan 02-02: Modern cluster (Lynch, Marks, Li Lu) + analyze_company/format_vote + live API validation
- [x] Execute Plan 03-01: Pydantic data models, ticker resolver, yfinance fetchers, and 21 unit tests
- [x] Execute Plan 03-02: SEC filings, xAI social sentiment, pipeline orchestrator, and 14 new tests
- [x] Execute Plan 04-01: DebateOrchestrator, models, prompts, and 15 unit tests

### Blockers

None currently.

## Session Continuity

**Last action:** Executed Plan 04-01 (models.py, prompts.py, orchestrator.py, test_debate.py). 106 non-live tests passing (15 new debate tests). Phase 4 Plan 1 complete.
**Next action:** Execute Plan 04-02 (vote extraction via ResultsExtractor, scorecard builder, run_debate convenience function, live API integration test).
**Context to preserve:** Phases 1-3 fully complete. Phase 4 Plan 1 complete: DebateOrchestrator (TinyWorld subclass) with 4-phase debate flow, Pydantic models (DebatePhase, Vote, Scorecard, DebateResult), fuzzy vote validator, PHASE_PROMPTS dict. 7 exports from tinyic.debate (DebateOrchestrator + 6 models). DebateOrchestrator._step() overrides TinyWorld._step() entirely for sequential agent turns. broadcast_internal_goal() sets phase goals, agents act() in stable order. run_debate() = inject_context() + run(steps=4). Tests use MagicMock agents with autouse fixture clearing TinyWorld.all_environments.

---
*State initialized: 2026-03-20*
*Last updated: 2026-03-22 after Plan 04-01 completion*
