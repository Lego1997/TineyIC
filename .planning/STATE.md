---
gsd_state_version: 1.0
milestone: v0.6
milestone_name: milestone
status: phase-complete
last_updated: "2026-03-20T13:45:50.003Z"
progress:
  total_phases: 5
  completed_phases: 1
  total_plans: 2
  completed_plans: 2
---

# State: openIC

## Project Reference

**Core Value:** Investor personas must be convincingly distinct and philosophically accurate -- each argues from their real-world framework, producing genuinely differentiated perspectives.

**Current Focus:** Phase 01 — foundation-and-tinytroupe-integration (COMPLETE)

## Current Position

Phase: 01 (foundation-and-tinytroupe-integration) — COMPLETE
Plan: 2 of 2 (all complete)

## Performance Metrics

| Metric | Value |
|--------|-------|
| Plans completed | 2 |
| Plans total | 2 (Phase 1) |
| Phases completed | 1/5 |
| Requirements completed | 4/25 |
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

### Key Risks

| Risk | Severity | Mitigation | Phase |
|------|----------|------------|-------|
| OpenAI SDK v2.x incompatibility with TinyTroupe | RESOLVED | Patched _is_reasoning_model() and fixed max_completion_tokens; imports work | 1 |
| Persona convergence (all sound the same) | CRITICAL | Contrastive prompting, distinct vocabulary, isolation testing | 2 |
| Persona drift over multi-turn debate | HIGH | System prompt re-injection, 3-4 round limit | 4 |
| API cost explosion (6 agents x multiple rounds) | MODERATE | Token budgets, prompt caching, round summaries | 4 |
| Streamlit rerun vs long-running debate | MODERATE | Background thread, state checkpointing | 5 |

### Todos

- [x] Begin Phase 1 planning after roadmap approval
- [x] Execute Plan 01-01: Scaffold uv workspace and InvestorPersona base class
- [x] Execute Plan 01-02: Validate GPT-5.2 compatibility with live API smoke test
- [ ] Begin Phase 2 planning: Persona Engineering

### Blockers

None currently.

## Session Continuity

**Last action:** Completed 01-02-PLAN.md -- live GPT-5.2 API smoke test validated, user approved coherent financial reasoning output
**Next action:** Begin Phase 2 planning (Persona Engineering) or Phase 3 planning (Financial Data Pipeline) -- these can proceed in parallel
**Context to preserve:** Phase 1 fully complete. TinyTroupe fork has 6 PATCH(tinyIC) patches (llama-index, GPT-5 reasoning, IPython dep, max_completion_tokens, streaming, base_url). Proxy gateway requires stream=True. pop_latest_actions() is the correct way to retrieve TinyTroupe actions. OpenAI SDK v2.29.0 confirmed compatible.

---
*State initialized: 2026-03-20*
*Last updated: 2026-03-20*
