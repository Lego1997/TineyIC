---
phase: "04"
plan: "01"
subsystem: debate-engine
tags: [debate, orchestrator, models, prompts, tinytroupe]
dependency_graph:
  requires: [tinyic.data.models.DataPackage, tinyic.constants, tinytroupe.environment.tiny_world.TinyWorld]
  provides: [tinyic.debate.DebateOrchestrator, tinyic.debate.models, tinyic.debate.prompts]
  affects: [tinyic.debate.__init__]
tech_stack:
  added: []
  patterns: [TinyWorld subclass, phase-driven orchestration, fuzzy enum validation]
key_files:
  created:
    - src/tinyic/debate/models.py
    - src/tinyic/debate/prompts.py
    - src/tinyic/debate/orchestrator.py
    - tests/test_debate.py
  modified:
    - src/tinyic/debate/__init__.py
decisions:
  - "Override _step() entirely rather than calling super() -- avoids TinyWorld's parallel/randomize logic"
  - "MagicMock without spec for agent mocks -- TinyWorld.add_agent sets attributes dynamically"
  - "Fuzzy vote validator uses string containment (BUY/SELL/else HOLD) for robust extraction"
metrics:
  duration_minutes: 11
  completed: "2026-03-22"
  tasks: 2
  files_created: 4
  files_modified: 1
  tests_added: 15
  tests_total_passing: 106
---

# Phase 4 Plan 1: Core Debate Infrastructure Summary

DebateOrchestrator (TinyWorld subclass) with Pydantic models, phase prompts, and 15 unit tests -- structured 4-phase debate flow (opening/cross-exam/rebuttal/verdict) with sequential agent turns and fuzzy vote parsing.

## What Was Built

### Data Models (models.py)
- **DebatePhase** enum: 6 phases (SETUP, OPENING, CROSS_EXAM, REBUTTAL, VERDICT, COMPLETE) with string values
- **VoteChoice** enum: BUY, HOLD, SELL
- **Confidence** enum: HIGH, MEDIUM, LOW
- **Vote** model: with fuzzy validator that maps "STRONG BUY" -> BUY, "CONDITIONAL SELL" -> SELL, etc.
- **Scorecard** model: with `to_markdown()` rendering a table with consensus, vote counts, and per-investor rows
- **DebateResult** model: complete debate output container

### Prompt Templates (prompts.py)
- **PHASE_PROMPTS**: dict mapping 4 debate phases to detailed prompt templates with `{company}` placeholder
- **CONTEXT_PREAMBLE**: template for injecting financial data package into agent context

### Orchestrator (orchestrator.py)
- **DebateOrchestrator(TinyWorld)**: subclass with:
  - Persona count validation (2-6 agents)
  - `inject_context()`: broadcasts DataPackage as formatted preamble
  - `_step()` override: broadcasts phase goal, agents act sequentially in stable order
  - `run_debate()`: convenience method (inject context + run 4 steps)
  - `is_complete` property
  - Phase tracking via `_phase_history` and `_phase_index`

### Tests (test_debate.py)
- 6 model tests: enum values, fuzzy vote validator, scorecard markdown, consensus, DebateResult
- 9 orchestrator tests: min/max personas validation, init state, context injection, 4-phase execution, sequential turns, prompt usage, extra step after completion
- All tests use MagicMock agents (no API calls)
- autouse fixture clears TinyWorld.all_environments between tests

## Test Results

- 15 new debate tests: all passing
- 106 total non-live tests: all passing (91 pre-existing + 15 new)
- 6 live API tests: excluded (pre-existing flakiness with act() return value)

## Commits

| Hash | Message |
|------|---------|
| aa3710e | feat(04-01): add debate data models and phase prompt templates |
| f553f0f | feat(04-01): add DebateOrchestrator and 15 unit tests |

## Deviations from Plan

None -- plan executed exactly as written.

## Key Design Notes

- `_step()` does NOT call `super()._step()` -- it replaces the parent's logic entirely to avoid TinyWorld's parallelization and randomization
- `_step()` does NOT use `@transactional()` decorator -- the parent class already decorates it
- Mock agents use plain MagicMock (not `spec=InvestorPersona`) because TinyWorld.add_agent dynamically sets `agent.environment = self`
- The fuzzy vote validator handles ResultsExtractor returning strings like "STRONG BUY" or "CONDITIONAL SELL" by checking for substring containment

## Self-Check: PASSED

All 6 files found. Both commit hashes verified (aa3710e, f553f0f).
