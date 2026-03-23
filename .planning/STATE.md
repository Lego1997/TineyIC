---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: "Output Quality + Debate Robustness + Polish"
status: executing
last_updated: "2026-03-23T10:10:00Z"
progress:
  total_phases: 6
  completed_phases: 3
  total_plans: 6
  completed_plans: 6
---

# State: openIC

## Project Reference

**Core Value:** Investor personas must be convincingly distinct and philosophically accurate -- each argues from their real-world framework, producing genuinely differentiated perspectives.

**Current Focus:** v1.1 milestone -- output quality, debate robustness, and polish. Makes the output worth reading and the debate worth watching.

## Current Position

Phase: 09 (memo-disagreement-engine) -- COMPLETE
Plan: 2 of 2 (all plans executed)

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
| 10 | UI Delivery | Pending | Phase 7, 9 |
| 11 | Model Selection | Pending | Phase 7 |
| 12 | Deep Research Pipeline | Pending | Phase 7, 11 |

## Performance Metrics

### v1 (archived)

| Metric | Value |
|--------|-------|
| Plans completed | 11/11 |
| Phases completed | 6/6 |
| Requirements completed | 28/28 |
| Total tests | 154 (+ 7 live API) |

### v1.1

| Metric | Value |
|--------|-------|
| Plans completed | 6/6 (Phase 7: 2/2, Phase 8: 2/2, Phase 9: 2/2) |
| Phases completed | 3/6 |
| Requirements completed | 10/20 |
| Total tests | 208 (+ 7 live API) |

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
- [ ] Plan Phase 10: UI Delivery
- [ ] Plan Phase 11: Model Selection
- [ ] Plan Phase 12: Deep Research Pipeline

### Blockers

None currently.

## Session Continuity

**Last action:** Executed Phase 9 (Memo & Disagreement Engine) -- both plans complete (09-01: models + generators, 09-02: export).
**Next action:** Plan Phase 10 (UI Delivery) or execute Phase 11 (Model Selection, parallel-eligible).
**Context to preserve:** 208 tests passing. Zero deprecation warnings. InvestmentMemo with 5 grounded sections + DisagreementAnalysis with top 3 divergences. ExportManager supports Markdown (guaranteed) and DOCX (pandoc-gated). generate_memo() and extract_disagreements() use client().send_message() with graceful fallback. Transcript truncation at 8000 chars. Lazy-import ArtifactExporter in export.py.

---
*State initialized: 2026-03-20*
*Last updated: 2026-03-23 -- Phase 9 executed (2/2 plans complete: memo+disagreement models/generators, export functionality)*
