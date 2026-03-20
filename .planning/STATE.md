# State: openIC

## Project Reference

**Core Value:** Investor personas must be convincingly distinct and philosophically accurate -- each argues from their real-world framework, producing genuinely differentiated perspectives.

**Current Focus:** Roadmap created, awaiting approval to begin Phase 1 planning.

## Current Position

**Milestone:** v1
**Phase:** 1 of 5 (Foundation and TinyTroupe Integration)
**Plan:** Not yet planned
**Status:** Not started

```
Phase 1 [ ] Foundation and TinyTroupe Integration
Phase 2 [ ] Persona Engineering
Phase 3 [ ] Financial Data Pipeline
Phase 4 [ ] Debate Engine and Verdict Extraction
Phase 5 [ ] Streamlit UI

Overall: [..........] 0%
```

## Performance Metrics

| Metric | Value |
|--------|-------|
| Plans completed | 0 |
| Plans total | TBD (after phase planning) |
| Phases completed | 0/5 |
| Requirements completed | 0/25 |
| Estimated cost/debate | $3-8 (from research) |

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

### Key Risks

| Risk | Severity | Mitigation | Phase |
|------|----------|------------|-------|
| OpenAI SDK v2.x incompatibility with TinyTroupe | HIGH | Validate first thing in Phase 1; fallback: pin openai 1.x or patch fork | 1 |
| Persona convergence (all sound the same) | CRITICAL | Contrastive prompting, distinct vocabulary, isolation testing | 2 |
| Persona drift over multi-turn debate | HIGH | System prompt re-injection, 3-4 round limit | 4 |
| API cost explosion (6 agents x multiple rounds) | MODERATE | Token budgets, prompt caching, round summaries | 4 |
| Streamlit rerun vs long-running debate | MODERATE | Background thread, state checkpointing | 5 |

### Todos

- [ ] Begin Phase 1 planning after roadmap approval

### Blockers

None currently.

## Session Continuity

**Last action:** Roadmap created with 5 phases covering 25/25 v1 requirements
**Next action:** User reviews roadmap, then `/gsd:plan-phase 1` to plan Phase 1
**Context to preserve:** Research strongly recommends validating persona differentiation before building debate engine. Phases 2 and 3 can run in parallel.

---
*State initialized: 2026-03-20*
*Last updated: 2026-03-20*
