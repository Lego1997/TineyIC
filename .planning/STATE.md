---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: "Output Quality + Debate Robustness + Polish"
status: executing
last_updated: "2026-03-23T02:36:30Z"
progress:
  total_phases: 6
  completed_phases: 1
  total_plans: 2
  completed_plans: 2
---

# State: openIC

## Project Reference

**Core Value:** Investor personas must be convincingly distinct and philosophically accurate -- each argues from their real-world framework, producing genuinely differentiated perspectives.

**Current Focus:** v1.1 milestone -- output quality, debate robustness, and polish. Makes the output worth reading and the debate worth watching.

## Current Position

Phase: 07 (release-hardening) -- COMPLETE
Plan: 2 of 2

## v1 Completion Summary

v1 milestone passed audit on 2026-03-22:
- 6 phases, 11 plans, 154 tests passing, 28/28 requirements complete
- 14/14 cross-phase integration connections verified
- End-to-end flow (ticker → resolve → fetch → debate → scorecard) complete

## v1.1 Phase Overview

| Phase | Name | Status | Depends On |
|-------|------|--------|------------|
| 7 | Release Hardening | Complete | — (gate) |
| 8 | Debate Quality Controls | Pending | Phase 7 |
| 9 | Memo & Disagreement Engine | Pending | Phase 7, 8 |
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
| Plans completed | 2/2 (Phase 7) |
| Phases completed | 1/6 |
| Requirements completed | 4/20 |
| Total tests | 160 (+ 7 live API) |

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
- [ ] Plan Phase 8: Debate Quality Controls
- [ ] Plan Phase 9: Memo & Disagreement Engine
- [ ] Plan Phase 10: UI Delivery
- [ ] Plan Phase 11: Model Selection
- [ ] Plan Phase 12: Deep Research Pipeline

### Blockers

None currently.

## Session Continuity

**Last action:** Completed Phase 7 (Release Hardening) -- both plans 07-01 and 07-02 executed successfully.
**Next action:** Plan and execute Phase 8 (Debate Quality Controls) or Phase 11 (Model Selection) -- both depend only on Phase 7.
**Context to preserve:** 160 tests passing (154 original + 6 cost stats). edgartools deprecation warnings filtered via pyproject.toml. Pydantic ConfigDict fix applied in simulation_validator.py. DebateResult.cost_stats now populated by run_debate(). get_debate_cost_stats() convenience function available for UI consumption.

---
*State initialized: 2026-03-20*
*Last updated: 2026-03-23 -- Phase 7 complete (2/2 plans)*
