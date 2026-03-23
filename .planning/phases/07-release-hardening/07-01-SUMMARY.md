---
phase: "07"
plan: "01"
subsystem: "data-pipeline, tinytroupe-fork, test-infra"
tags: [deprecation, edgartools, pydantic, pytest, warnings]
dependency_graph:
  requires: []
  provides: [zero-deprecation-baseline]
  affects: [src/tinyic/data/filings.py, src/tinytroupe/validation/simulation_validator.py, pyproject.toml, tests/conftest.py, tests/test_data_pipeline.py]
tech_stack:
  added: []
  patterns: [HTMLParser-based-filing-extraction, ConfigDict-pydantic-v2, pre-import-warning-suppression]
key_files:
  created: []
  modified:
    - src/tinyic/data/filings.py
    - src/tinytroupe/validation/simulation_validator.py
    - pyproject.toml
    - tests/conftest.py
    - tests/test_data_pipeline.py
decisions:
  - Used latest.markdown() fallback instead of latest.text() to avoid deprecated codepath
  - Pre-import edgartools in conftest.py to suppress import-time deprecation warnings outside test context
  - Added _extract_sections() regex helper for markdown-based section extraction
metrics:
  duration: "9m 2s"
  completed: "2026-03-23"
  tasks: 4
  files_modified: 5
requirements: [HARD-01, HARD-03]
---

# Phase 7 Plan 1: Zero Deprecation Warnings Summary

Migrated edgartools filing extraction from deprecated `.obj()`/`.text()` to HTMLParser + `.markdown()` API, replaced Pydantic v1 `class Config` with `ConfigDict` in TinyTroupe fork, and suppressed third-party import-level warnings via pytest config and conftest pre-import.

## What Was Done

### Task 1: Migrate edgartools filing extraction to HTMLParser

Rewrote `fetch_filings()` in `src/tinyic/data/filings.py` to eliminate calls to `latest.obj()` and `latest.text()`, which triggered deprecated internal edgartools imports.

**New extraction flow:**
1. Primary: `latest.html()` -> `HTMLParser.create_for_ai().parse(html)` -> `doc.to_markdown()` -> `_extract_sections()` for structured section extraction
2. Fallback: `latest.markdown()` -> `_extract_sections()` for section extraction, or raw markdown truncation
3. Added `_extract_sections()` helper that uses regex to find markdown headings matching section keys (Item 1, Item 1A, Item 7 for 10-K; Part I, Item 1, Item 2 for 10-Q)

**Test updates:** Updated 3 test methods in `TestFetchFilings` to mock `.html()` and `.markdown()` instead of `.obj()` and `.text()`. The mock filing objects now return structured markdown with section headings instead of using `filing_obj.__getitem__`.

### Task 2: Fix Pydantic class Config deprecation

In `src/tinytroupe/validation/simulation_validator.py`:
- Added `ConfigDict` to pydantic imports
- Replaced `class Config` in `SimulationExperimentDataset` (line 68) with `model_config = ConfigDict(extra="forbid", validate_assignment=True)`
- Replaced `class Config` in `SimulationExperimentEmpiricalValidationResult` (line 735) with the same `model_config` pattern

### Task 3: Suppress edgartools import-level warnings

Two changes:
1. **pyproject.toml**: Added `filterwarnings` entries to `[tool.pytest.ini_options]` to ignore DeprecationWarning from `edgar.files.*`, `edgar._markdown`, and `edgar._filings` modules
2. **tests/conftest.py**: Pre-import `edgar` module with `warnings.simplefilter("ignore", DeprecationWarning)` inside `pytest_configure`. This ensures import-level warnings fire before the per-test warning context, so `-W error::DeprecationWarning` does not trigger false positives from third-party code.

### Task 4: Verification

All verification criteria passed:
- `uv run pytest tests/ -x -m "not live_api" -W all` -- 0 warnings (grep returns empty)
- `uv run pytest tests/ -x -m "not live_api" -W error::DeprecationWarning` -- 160 passed, 0 failed
- `uv run pytest tests/ -x -m "not live_api"` -- 160 passed, 7 deselected, 0 warnings
- Test count increased from 154 to 160 due to concurrent 07-02 plan adding 6 cost stats tests

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | ca09b95 | feat(07-01): migrate edgartools filing extraction to HTMLParser API |
| 2 | fa716e6 | fix(07-01): replace Pydantic class Config with ConfigDict |
| 3 | 3d30b63 | chore(07-01): add pytest warning filters for edgartools internal deprecations |
| 3+ | 6428554 | fix(07-01): pre-import edgartools in conftest to suppress import-time warnings |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Pre-import edgartools in conftest.py for -W error compatibility**
- **Found during:** Task 4 (verification)
- **Issue:** pytest `-W error::DeprecationWarning` overrides ini `filterwarnings` because cmdline -W options have higher priority than config filters in pytest's warning system. Third-party import-level warnings from edgartools fired inside the per-test warning context and were raised as errors.
- **Fix:** Pre-import `edgar` module in `conftest.py::pytest_configure` with warnings suppressed, so import-level deprecation warnings fire before any test context captures them.
- **Files modified:** tests/conftest.py
- **Commit:** 6428554

**2. [Rule 3 - Blocking] Test mocks updated for new API surface**
- **Found during:** Task 1
- **Issue:** Existing test mocks set up `.obj()` and `.text()` on mock Filing objects, but the new implementation calls `.html()` and `.markdown()` instead.
- **Fix:** Updated 3 test methods to mock `.html()` (returning None to skip HTMLParser) and `.markdown()` (returning structured markdown with section headings) instead of `.obj()` and `.text()`.
- **Files modified:** tests/test_data_pipeline.py
- **Commit:** ca09b95

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Use latest.markdown() as primary fallback, not latest.text() | `.text()` also triggers deprecated imports internally; `.markdown()` provides richer formatted output |
| Pre-import edgar in conftest.py rather than patching pytest warning internals | Minimal, non-invasive approach that works with any `-W` flag combination |
| Regex-based section extraction from markdown | More robust than relying on edgartools' structured section access which was tied to deprecated `.obj()` API |

## Self-Check: PASSED

- All 5 modified files exist on disk
- All 4 commits (ca09b95, fa716e6, 3d30b63, 6428554) found in git log
- 160 tests passing, 0 warnings, 7 deselected
