---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: "Output Quality + Debate Robustness + Polish"
status: archived
last_updated: "2026-03-26T00:00:00Z"
progress:
  total_phases: 6
  completed_phases: 6
  total_plans: 11
  completed_plans: 11
---

# State: openIC

## Project Reference

**Core Value:** Investor personas must be convincingly distinct and philosophically accurate -- each argues from their real-world framework, producing genuinely differentiated perspectives.

**Current Focus:** v1.1 milestone archived. Next milestone not yet planned.

## Current Position

Phase: 12 (deep-research-pipeline) -- COMPLETE
Plan: 2 of 2 complete

## v1 Completion Summary

v1 milestone passed audit on 2026-03-22:

- 6 phases, 11 plans, 154 tests passing, 28/28 requirements complete
- 14/14 cross-phase integration connections verified
- End-to-end flow (ticker → resolve → fetch → debate → scorecard) complete

## v1.1 Phase Overview

| Phase | Name | Status | Depends On |
|-------|------|--------|------------|
| 7 | Release Hardening | Complete | — (gate) |
| 8 | Debate Quality Controls | Complete | Phase 7 |
| 9 | Memo & Disagreement Engine | Complete | Phase 7, 8 |
| 10 | UI Delivery | Complete | Phase 7, 9 |
| 11 | Model Selection | Complete | Phase 7 |
| 12 | Deep Research Pipeline | Complete (2/2) | Phase 7, 11 |

## Performance Metrics

### v1 (archived)

| Metric | Value |
|--------|-------|
| Plans completed | 11/11 |
| Phases completed | 6/6 |
| Requirements completed | 28/28 |
| Total tests | 154 (+ 7 live API) |
| Phase 10 P01 | 30min | 4 tasks | 3 files |

### v1.1

| Metric | Value |
|--------|-------|
| Plans completed | 11/11 (Phase 7: 2/2, Phase 8: 2/2, Phase 9: 2/2, Phase 10: 2/2, Phase 11: 1/1, Phase 12: 2/2) |
| Phases completed | 6/6 |
| Requirements completed | 20/20 |
| Total tests | 261 (+ 7 live API) |
| Phase 12 P02 | 24min | 5 tasks | 2 files |

## Accumulated Context

### Key Decisions (v1.1)

| Decision | Rationale | Phase |
|----------|-----------|-------|
| Hardening before features | Fix deprecations and test flakiness before adding new capabilities; prevents cascading breakage | Phase 7 |
| Persona re-injection already exists | TinyPerson.reset_prompt() runs every act() call; DEBT-04 needs structural anti-convergence, not re-injection | Phase 8 |
| Rotating devil's advocate over forced bears | Confine dissent to cross-exam mechanics; keep final votes unconstrained for authenticity | Phase 8 |
| Merge memo + export into one phase | No reason to split backend artifact generation from export wiring | Phase 9 |
| Pull PERS-08-lite and OPS-01-lite into v1.1 | High value, low implementation cost in current codebase | Phase 8, 10 |
| Keep fact-checking (DEBT-06) in v2 | Transformational scope that would destabilize v1.1 timeline | v2 |
| Keep test_live_aapl without has_api_key | Only needs network (yfinance+edgartools), not OPENAI_API_KEY; adding fixture would skip it unnecessarily | Phase 7 |
| GPT-5.2 pricing for cost estimation | $2.50/M input, $10.00/M output as approximation in get_debate_cost_stats() | Phase 7 |
| Use latest.markdown() fallback over latest.text() | .text() triggers deprecated imports; .markdown() provides richer formatted output | Phase 7 |
| Pre-import edgartools in conftest.py | Ensures import-level deprecation warnings fire outside per-test context; compatible with -W error flag | Phase 7 |
| Regex section extraction from markdown | More robust than deprecated .obj() structured API for filing section access | Phase 7 |
| Fallback philosophy hook for unknown personas | Generic "Stay true to your unique perspective." text for names not in PHILOSOPHY_HOOKS | Phase 8 |
| Reinforcement via listen() not internalize_goal() | listen() adds to conversation memory; internalize_goal() would create conflicting goals | Phase 8 |
| DA selection once before agent loop | Ensures exactly one DA per cross-exam phase, not per-agent selection | Phase 8 |
| TF-IDF cosine similarity for differentiation | scikit-learn already installed; robust offline metric for persona text distinctness | Phase 8 |
| Hand-crafted fixture over recorded API output | Ensures deterministic, API-independent regression baseline with controlled vocabulary | Phase 8 |
| Role release before agent loop at REBUTTAL | DA gets clean release before any agent acts in rebuttal phase | Phase 8 |
| Direct file write for Markdown, ArtifactExporter for DOCX only | ArtifactExporter's dedent() strips Markdown indentation; direct write preserves formatting | Phase 9 |
| Lazy-import ArtifactExporter in export.py | Avoids pulling pandas/pypandoc/markdown at module import time; only DOCX path needs them | Phase 9 |
| Transcript truncation at 8000 chars (keep end) | Cross-exam and verdict phases are most informative for memo synthesis; opening can be trimmed | Phase 9 |
| Module-level yfinance import in financials.py | Enables mock patching at tinyic.data.financials.yf; local import would create unpatchable function-scoped name | Phase 10 |
| Data sidebar in main content columns, not Streamlit sidebar | Streamlit sidebar already full with ticker input and persona checkboxes; 2:1 column ratio preserves debate width | Phase 10 |
| Price history is UI-only data | Not added to DataPackage model (designed for LLM context); fetched and stored separately for chart rendering | Phase 10 |
| OpenAI Responses API for web search | Leverages existing OPENAI_API_KEY; web_search_preview tool provides grounded results | Phase 12 |
| Two-stage search (company + environment) | Separate queries for company fundamentals and market/analyst context gives better coverage | Phase 12 |
| TinyTroupe client for research synthesis | Consistent with memo.py pattern; respects runtime model selection from Phase 11 | Phase 12 |
| Context budget 12K -> 20K chars | Research brief adds ~3K chars; 20K still fits within LLM context windows | Phase 12 |
| deep_research=True by default | Research enrichment is valuable enough to be opt-out, not opt-in | Phase 12 |
| Research checkbox between Model and Start | Logical sidebar grouping; uses same disabled flag as other controls | Phase 12 |
| research_available via data_ready event | Avoids UI thread reading DataPackage directly; consistent with event-driven pattern | Phase 12 |

### Key Risks (v1.1)

| Risk | Severity | Mitigation | Phase |
|------|----------|------------|-------|
| Structural dissent damages persona authenticity | MODERATE | Confine to cross-exam role; keep final vote unconstrained | 8 |
| Memo generation may hallucinate unsupported claims | MODERATE | Require section-level grounding to scorecard/data-package evidence | 9 |
| Export reliability varies by runtime toolchain | LOW | Markdown as guaranteed fallback; DOCX gated on pandoc availability | 9 |
| Scope creep pushes v1.1 toward transformative | MODERATE | Use phase gates; defer fact-checking and data-validation to v2 | All |
| Persona drift over multi-turn debate | HIGH (from v1) | Anti-convergence controls + differentiation regression tests | 8 |

### Todos

- [x] Plan Phase 7: Release Hardening (2 plans created)
- [x] Plan Phase 8: Debate Quality Controls (2 plans executed)
- [x] Plan Phase 9: Memo & Disagreement Engine (2 plans created)
- [x] Plan Phase 10: UI Delivery (2 plans created)
- [x] Plan Phase 11: Model Selection (1 plan)
- [x] Plan Phase 12: Deep Research Pipeline (2/2 complete)

### Blockers

None currently.

## Session Continuity

**Last action:** v1.1 milestone archived (audit passed, 20/20 requirements, 6/6 integration seams).
**Next action:** Run `/gsd:new-milestone` to start v2 planning.
**Context to preserve:** 261 tests passing. 48/48 requirements across v1 + v1.1. Archives at `.planning/milestones/v1.1-ROADMAP.md` and `v1.1-REQUIREMENTS.md`.

---
*State initialized: 2026-03-20*
*Last updated: 2026-03-26 -- v1.1 milestone archived.*
