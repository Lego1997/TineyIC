---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: "Output Quality + Debate Robustness + Polish"
status: planning
last_updated: "2026-03-22T12:00:00.000Z"
progress:
  total_phases: 6
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
---

# State: openIC

## Project Reference

**Core Value:** Investor personas must be convincingly distinct and philosophically accurate -- each argues from their real-world framework, producing genuinely differentiated perspectives.

**Current Focus:** v1.1 milestone -- output quality, debate robustness, and polish. Makes the output worth reading and the debate worth watching.

## Current Position

Phase: 07 (release-hardening) -- PENDING
Plan: 0 of TBD

## v1 Completion Summary

v1 milestone passed audit on 2026-03-22:
- 6 phases, 11 plans, 154 tests passing, 28/28 requirements complete
- 14/14 cross-phase integration connections verified
- End-to-end flow (ticker → resolve → fetch → debate → scorecard) complete

## v1.1 Phase Overview

| Phase | Name | Status | Depends On |
|-------|------|--------|------------|
| 7 | Release Hardening | Pending | — (gate) |
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
| Plans completed | 0/TBD |
| Phases completed | 0/6 |
| Requirements completed | 0/20 |
| Total tests | 154 (baseline) |

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

### Key Risks (v1.1)

| Risk | Severity | Mitigation | Phase |
|------|----------|------------|-------|
| Structural dissent damages persona authenticity | MODERATE | Confine to cross-exam role; keep final vote unconstrained | 8 |
| Memo generation may hallucinate unsupported claims | MODERATE | Require section-level grounding to scorecard/data-package evidence | 9 |
| Export reliability varies by runtime toolchain | LOW | Markdown as guaranteed fallback; DOCX gated on pandoc availability | 9 |
| Scope creep pushes v1.1 toward transformative | MODERATE | Use phase gates; defer fact-checking and data-validation to v2 | All |
| Persona drift over multi-turn debate | HIGH (from v1) | Anti-convergence controls + differentiation regression tests | 8 |

### Todos

- [ ] Plan Phase 7: Release Hardening
- [ ] Plan Phase 8: Debate Quality Controls
- [ ] Plan Phase 9: Memo & Disagreement Engine
- [ ] Plan Phase 10: UI Delivery
- [ ] Plan Phase 11: Model Selection
- [ ] Plan Phase 12: Deep Research Pipeline

### Blockers

None currently.

## Session Continuity

**Last action:** v1.1 milestone initialized with 6 phases, 20 requirements. Added Phase 12 (Deep Research Pipeline) for web search + LLM synthesis research briefs.
**Next action:** `/gsd:plan-phase 7` to create the hardening plan.
**Context to preserve:** Codex review identified that persona re-injection already happens via TinyPerson.reset_prompt(); OpenAIClient.get_cost_stats() already exists; ArtifactExporter + pandoc 3.8.3 are available for DOCX export. Phase 7 is a hard gate before feature phases. Model is currently hardcoded via config.ini MODEL field → config_manager.get("model") in openai_client.py; Phase 11 needs to override this at runtime. Phase 12 adds deep research to build_data_package() — current pipeline fetches structured data from yfinance/edgartools/xAI; research brief would add synthesized analysis via web search + LLM. The /deep-research skill pattern (multi-agent research pipeline) should inform the implementation approach.

---
*State initialized: 2026-03-20*
*Last updated: 2026-03-22 — v1.1 milestone initialized*
