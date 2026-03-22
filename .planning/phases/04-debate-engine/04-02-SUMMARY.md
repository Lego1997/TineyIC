---
phase: "04"
plan: "02"
subsystem: debate-engine
tags: [debate, extraction, scorecard, vote, run_debate, ResultsExtractor]
dependency_graph:
  requires: [tinyic.debate.orchestrator.DebateOrchestrator, tinyic.debate.models, tinyic.constants, tinytroupe.extraction.ResultsExtractor, tinyic.personas.registry, tinyic.data.pipeline]
  provides: [tinyic.debate.extraction.extract_votes, tinyic.debate.extraction.build_scorecard, tinyic.debate.run_debate]
  affects: [tinyic.debate.__init__]
tech_stack:
  added: [tinytroupe.extraction.ResultsExtractor]
  patterns: [lazy-import, fuzzy-parsing, fallback-votes, strict-majority-consensus]
key_files:
  created:
    - src/tinyic/debate/extraction.py
    - tests/test_debate_live.py
  modified:
    - src/tinyic/debate/__init__.py
    - tests/test_debate.py
decisions:
  - Strict majority consensus (> 50%): ties produce consensus=None
  - Lazy imports in run_debate() for load_persona and build_data_package to avoid circular imports
  - Fallback HOLD/LOW votes when extraction fails (never crash)
  - String-to-list parsing for reasoning fields handles bullet, newline, and plain-text formats
metrics:
  duration: 3min
  completed: "2026-03-22T04:18:00Z"
  tasks: 2
  files_created: 2
  files_modified: 2
  tests_added: 9
  tests_total: 115
---

# Phase 4 Plan 2: Vote Extraction, Scorecard Builder, run_debate() Summary

Vote extraction via ResultsExtractor with fuzzy parsing, scorecard builder with strict-majority consensus, and run_debate() convenience function wiring the full pipeline from persona loading through verdict extraction.

## What Was Built

### extraction.py
- `extract_votes(orchestrator)`: Uses `tinytroupe.extraction.ResultsExtractor` to pull structured vote data from each agent's conversation history. Handles extraction failures with fallback HOLD/LOW votes. Parses LLM output robustly: string-to-list for reasoning/risks, string-to-bool for changed_mind, fuzzy vote matching via model validator.
- `build_scorecard(votes, ticker, company_name)`: Aggregates Vote objects into a Scorecard with bull/bear/hold counts and strict-majority consensus detection (requires > 50% for any consensus).
- Helper functions `_parse_list_field()` and `_parse_bool()` for robust LLM output parsing.

### run_debate() in __init__.py
- Top-level convenience function: takes ticker + persona names, loads personas from registry, builds data package (or uses pre-built), runs full debate, extracts votes, builds scorecard, returns DebateResult.
- Validates minimum persona count before proceeding.
- Uses lazy imports for registry/pipeline to avoid circular dependencies.

### Tests (9 new unit tests + 1 live API test)
- **TestVoteExtraction** (4 tests): success extraction, fuzzy vote matching, total extraction failure fallback, missing fields defaults.
- **TestBuildScorecard** (3 tests): majority consensus, tie/no-consensus, markdown output format.
- **TestRunDebate** (2 tests): minimum personas validation, full wiring verification with mocked components.
- **test_debate_live.py**: End-to-end live API test (marked `live_api`, skipped in CI) that runs a 2-persona debate and validates the complete result structure.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 8f42bde | feat(04-02): add vote extraction, scorecard builder, and run_debate() |
| 2 | 20b922a | test(04-02): add extraction, scorecard, and run_debate tests |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed MIN_PERSONAS violation in test mocks**
- **Found during:** Task 2
- **Issue:** Plan specified single-persona orchestrators for test_extract_votes_fuzzy_vote and test_extract_votes_missing_fields, but DebateOrchestrator enforces MIN_PERSONAS=2.
- **Fix:** Added second mock persona and corresponding extraction result to both tests.
- **Files modified:** tests/test_debate.py

## Verification Results

- 115 tests pass (106 pre-existing + 9 new), 7 deselected (live_api)
- All imports verified: `from tinyic.debate import extract_votes, build_scorecard, run_debate`
- build_scorecard correctly returns consensus=None for 1 BUY + 1 SELL (no majority)
- Scorecard.to_markdown() renders correctly with vote data

## Self-Check: PASSED

All 4 files exist, both commit hashes verified (8f42bde, 20b922a).
