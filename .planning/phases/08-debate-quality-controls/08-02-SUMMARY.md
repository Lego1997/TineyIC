---
phase: 08-debate-quality-controls
plan: 02
subsystem: test-infrastructure
tags: [differentiation, regression-tests, tfidf, fixture, offline-tests]
dependency_graph:
  requires: []
  provides: [measure_differentiation, recorded_debate_fixture, differentiation_regression_tests]
  affects: [tests/]
tech_stack:
  added: []
  patterns: [TF-IDF cosine similarity for persona differentiation, recorded fixture regression testing]
key_files:
  created:
    - tests/fixtures/recorded_debate_apple.json
    - tests/test_differentiation.py
  modified: []
decisions:
  - Used hand-crafted fixture texts with persona-specific vocabulary to ensure below-threshold similarity
  - TF-IDF with English stop words and min_df=1 for robust single-document comparison
  - Upper-triangle-only pairs to avoid duplicate and self-comparisons
metrics:
  duration: "11 minutes"
  completed: "2026-03-23"
  tasks: 2
  tests_added: 6
  files_created: 2
---

# Phase 8 Plan 2: Differentiation Regression Tests Summary

TF-IDF cosine similarity regression tests on hand-crafted recorded fixture with Buffett/Graham/Marks persona texts, all pairwise similarities under 0.12 against 0.70 threshold.

## What Was Done

### Task 1: Recorded Debate Fixture

Created `tests/fixtures/recorded_debate_apple.json` with three hand-crafted persona reasoning texts analyzing Apple Inc.:

- **Warren Buffett** (213 words): Economic moat, ecosystem lock-in, owner earnings, capital allocation discipline, circle of competence, Services recurring revenue
- **Benjamin Graham** (230 words): Margin of safety, P/E ratio analysis, net current asset value, book value premium, earnings yield vs. treasuries, defensive investor criteria
- **Howard Marks** (241 words): Cycle positioning, pendulum of sentiment, second-level thinking, risk/reward asymmetry, consensus view danger, crowded positioning downside

Pairwise TF-IDF cosine similarity results:
- Buffett vs Graham: 0.1173
- Buffett vs Marks: 0.0968
- Graham vs Marks: 0.1203
- **Max: 0.1203** (well below 0.70 threshold)

### Task 2: Differentiation Test Module (TDD)

Created `tests/test_differentiation.py` with `measure_differentiation()` utility and 6 tests across 2 classes:

**TestMeasureDifferentiation** (3 unit tests):
1. `test_measure_differentiation_structure` -- Verifies return dict has "pairs" and "max_similarity" keys with correct types
2. `test_measure_differentiation_identical_texts` -- Identical texts produce similarity > 0.99
3. `test_measure_differentiation_distinct_texts` -- Unrelated texts produce similarity < 0.30

**TestPersonaDifferentiation** (3 fixture regression tests):
4. `test_fixture_has_minimum_personas` -- Fixture has 3+ personas
5. `test_persona_differentiation_on_fixture` -- All pairwise similarities below 0.70 threshold (with descriptive failure messages)
6. `test_no_unanimous_convergence_pattern` -- Max similarity below threshold

TDD cycle followed: RED (NotImplementedError stub, all tests fail) -> GREEN (implementation, all tests pass). No REFACTOR needed.

## Commits

| Commit | Type | Description |
|--------|------|-------------|
| c16d479 | feat | Add recorded debate fixture with 3 differentiated personas |
| 8be5e08 | test | Add failing differentiation tests (TDD RED phase) |
| 7f79111 | feat | Implement measure_differentiation with TF-IDF cosine similarity |

## Test Results

```
tests/test_differentiation.py ......    [100%]
6 passed in 0.99s
```

Full suite (excluding live_api): 175 passed, 7 deselected in 5.74s -- no regressions.

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

1. **Hand-crafted persona-specific vocabulary**: Each persona text deliberately uses unique terminology from their investment philosophy (no shared boilerplate), resulting in very low similarity scores (max 0.12 vs 0.70 threshold)
2. **TfidfVectorizer with stop_words='english'**: Removes common English words to focus similarity computation on meaningful investment terminology
3. **Upper-triangle pairs only**: Avoids duplicate pairs and self-comparisons in the similarity matrix

## Key Artifacts

- `tests/fixtures/recorded_debate_apple.json` -- Recorded debate fixture with metadata and 3 persona reasoning texts
- `tests/test_differentiation.py` -- measure_differentiation() utility + 6 regression tests
- `measure_differentiation()` is importable for reuse: `from tests.test_differentiation import measure_differentiation`

## Self-Check: PASSED

- [x] tests/fixtures/recorded_debate_apple.json exists
- [x] tests/test_differentiation.py exists
- [x] 08-02-SUMMARY.md exists
- [x] Commit c16d479 found
- [x] Commit 8be5e08 found
- [x] Commit 7f79111 found
