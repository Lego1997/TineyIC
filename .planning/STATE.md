---
gsd_state_version: 1.0
milestone: v0.6
milestone_name: milestone
status: executing
last_updated: "2026-03-21T09:30:00.000Z"
progress:
  total_phases: 5
  completed_phases: 2
  total_plans: 4
  completed_plans: 4
---

# State: openIC

## Project Reference

**Core Value:** Investor personas must be convincingly distinct and philosophically accurate -- each argues from their real-world framework, producing genuinely differentiated perspectives.

**Current Focus:** Phase 02 — persona-engineering (COMPLETED)

## Current Position

Phase: 02 (persona-engineering) — COMPLETED
Plan: 2 of 2 (all complete)

## Performance Metrics

| Metric | Value |
|--------|-------|
| Plans completed | 4 |
| Plans total | 4 (Phase 1: 2, Phase 2: 2) |
| Phases completed | 2/5 |
| Requirements completed | 10/25 |
| Estimated cost/debate | $3-8 (from research) |
| Phase 01 P01 | 66min | 2 tasks | 112 files |
| Phase 01 P02 | 21min | 2 tasks | 3 files |

## Accumulated Context

### Key Decisions

| Decision | Rationale | Phase |
|----------|-----------|-------|
| Fork TinyTroupe as local package (not submodule) | Need deep customization, avoid submodule complexity | Phase 1 |
| Personas before debate engine | Research unambiguous: validate differentiation in isolation before multi-agent | Phase 2 before 4 |
| Curated prompts over RAG | Simpler, more controllable persona accuracy for v1 | Phase 2 |
| Free data sources only | No cost barrier for v1 (yfinance, edgartools, xAI API) | Phase 3 |
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

### Blockers

None currently.

## Session Continuity

**Last action:** Executed Phase 2 (both plans). All 6 persona configs created, registry built, analyze_company/format_vote implemented, 57 unit tests pass, human checkpoint approved.
**Next action:** Begin Phase 3 planning (data pipeline) or Phase 4 planning (debate engine). Phases 3+4 are parallelizable.
**Context to preserve:** Phase 1+2 fully complete. 6 investor personas (Graham/Buffett/Munger/Lynch/Marks/Li Lu) with 4-layer anti-convergence: distinct styles, contrastive beliefs, signature vocabulary, negative constraints. Registry provides load_persona()/list_personas(). analyze_company() uses listen/act pipeline. format_vote() asks for BUY/HOLD/SELL. TinyTroupe fork has 6 PATCH(tinyIC) patches. Proxy gateway requires stream=True. pop_latest_actions() for action retrieval.

---
*State initialized: 2026-03-20*
*Last updated: 2026-03-21*
