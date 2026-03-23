---
phase: 08-debate-quality-controls
plan: 01
subsystem: debate-engine
tags: [anti-convergence, devils-advocate, prompt-engineering, orchestrator]
dependency_graph:
  requires: [Phase 7 clean test baseline]
  provides: [REINFORCEMENT_TEMPLATE, PHILOSOPHY_HOOKS, DEVILS_ADVOCATE_PROMPT, ROLE_RELEASE_PROMPT, _get_reinforcement_prompt, _select_devils_advocate]
  affects: [orchestrator._step, debate quality, persona differentiation]
tech_stack:
  added: []
  patterns: [persona-specific reinforcement injection, round-robin devil's advocate rotation, role release at phase transition]
key_files:
  created: []
  modified:
    - src/tinyic/debate/prompts.py
    - src/tinyic/debate/orchestrator.py
    - tests/test_debate.py
decisions:
  - Fallback philosophy hook for unknown personas uses generic "Stay true to your unique perspective." text
  - Reinforcement injected via agent.listen() after message queue drain, before act() in all 4 phases
  - DA selection happens once before the agent loop in CROSS_EXAM, not per-agent
  - Role release fires once at REBUTTAL start before any agent acts, not inside the per-agent loop
metrics:
  duration_seconds: 459
  completed: "2026-03-23T05:07:48Z"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 9
  tests_total: 175
  files_modified: 3
requirements_completed: [DEBT-04, DEBT-05]
---

# Phase 8 Plan 01: Anti-Convergence Controls + Rotating Devil's Advocate Summary

Persona-specific reinforcement prompts injected before every agent.act() call across all 4 debate phases, plus a rotating devil's advocate role confined to cross-examination with clean role release at rebuttal start.

## What Was Done

### Task 1: Prompt Templates and Philosophy Hooks

Added 4 new constants to `src/tinyic/debate/prompts.py` after existing `CONTEXT_PREAMBLE`:

- **PHILOSOPHY_HOOKS**: Dict mapping 6 known persona names to 1-sentence philosophy descriptions (Buffett, Munger, Graham, Lynch, Marks, Li Lu)
- **REINFORCEMENT_TEMPLATE**: Template with `{name}` and `{philosophy_hook}` placeholders that reminds each persona of their distinct philosophy and urges genuine disagreement
- **DEVILS_ADVOCATE_PROMPT**: Instruction for the designated DA to argue the strongest counter-position during cross-examination
- **ROLE_RELEASE_PROMPT**: Instruction releasing the DA from their role at rebuttal start

Existing `PHASE_PROMPTS` and `CONTEXT_PREAMBLE` were not modified.

**Commit:** `f68d870`

### Task 2: Orchestrator Logic + Tests (TDD)

**RED phase:** Wrote 9 failing tests in two new test classes:

- `TestAntiConvergence` (4 tests): reinforcement before act, philosophy hook content, fallback for unknown names, injection in all 4 phases
- `TestDevilsAdvocate` (5 tests): DA injection during cross-exam only, round-robin rotation, no DA in other phases, role release at rebuttal, no DA constraints on verdict

**Commit:** `1dcee8e`

**GREEN phase:** Implemented orchestrator changes:

1. Updated imports to include all new prompt constants
2. Added `_da_index` and `_current_devils_advocate` state to `__init__()`
3. Added `_get_reinforcement_prompt(agent)` -- looks up persona in PHILOSOPHY_HOOKS with generic fallback
4. Added `_select_devils_advocate()` -- round-robin via `_da_index % len(agents)`
5. Modified `_step()`:
   - Before agent loop: select DA if CROSS_EXAM, send role release if REBUTTAL
   - Inside agent loop: inject reinforcement (all phases), inject DA prompt (CROSS_EXAM + designated agent only)
   - Order: message queue -> reinforcement -> DA prompt -> on_agent_start -> act()

**Commit:** `c6aafb8`

## Test Results

```
tests/test_debate.py: 39 passed (30 existing + 9 new)
tests/ (full suite, excluding live_api): 175 passed, 7 deselected
```

No regressions in existing tests. The additional `agent.listen()` calls from reinforcement injection do not interfere with existing mock assertions because those tests check specific content patterns, not exact call counts.

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Generic fallback hook for unknown personas | Keeps system working if persona names don't match PHILOSOPHY_HOOKS keys exactly |
| DA selection before agent loop (not per-agent) | Ensures exactly one DA per cross-exam phase, selected once |
| Role release outside agent loop | Fires once at REBUTTAL start, ensuring DA gets release before any agent acts in that phase |
| Reinforcement via listen() not internalize_goal() | listen() adds to conversation memory; internalize_goal() would create conflicting internal goals |

## Self-Check: PASSED

- All 3 modified files exist on disk
- All 3 commits (f68d870, 1dcee8e, c6aafb8) verified in git log
- 39/39 debate tests passing, 175/175 total tests passing (7 live_api deselected)
