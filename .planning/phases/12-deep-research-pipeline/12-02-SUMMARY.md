---
phase: 12
plan: 2
subsystem: ui
tags: [deep-research, toggle, sidebar, streamlit, worker-wiring]
dependency_graph:
  requires: [ResearchBrief, build_research_brief, build_data_package, deep_research_toggle]
  provides: [deep_research_checkbox, research_available_flag, research_status_message]
  affects: [render_sidebar, _start_debate, _debate_worker, _drain_queue, render_data_sidebar, debate_fragment]
tech_stack:
  added: []
  patterns: [session-state-toggle, event-flag-propagation]
key_files:
  created: []
  modified:
    - src/tinyic/ui/app.py
    - tests/test_ui.py
decisions:
  - "deep_research=True in init_state defaults -- matches pipeline default, research is opt-out"
  - "Research checkbox placed between Model section and Start Debate button for logical grouping"
  - "research_available flag propagated via data_ready event to avoid UI-thread reading DataPackage directly"
metrics:
  tasks: 5
  files_created: 0
  files_modified: 2
  tests_added: 7
  duration: "24min"
  completed: "2026-03-25"
---

# Phase 12 Plan 2: Deep Research UI Toggle + Worker Wiring Summary

Deep Research checkbox in Streamlit sidebar defaulting to enabled, wired through _start_debate -> _debate_worker -> build_data_package with research-aware status messaging and "Enhanced with deep research" indicator in data sidebar.

## What Was Built

### Deep Research Checkbox (Task 1)
- Added `deep_research: True` to `init_state()` defaults dict
- Added `research_available: False` to defaults for tracking post-fetch state
- New "Research" subheader in sidebar between Model section and Start Debate button
- Checkbox uses `disabled` flag (same as other sidebar controls) -- locked during active debate
- Stored in `st.session_state.deep_research`

### Worker Wiring (Task 2)
- `_start_debate()` reads `st.session_state.deep_research` and passes via Thread kwargs
- `_debate_worker()` signature extended: `deep_research=True` parameter
- `build_data_package(ticker, deep_research=deep_research)` call passes toggle through
- `data_ready` event dict includes `research_available: data_package.research_brief is not None`

### Research Status Messaging (Task 3)
- `_drain_queue()` data_ready handler stores `research_available` in session state
- Fetching status message conditionally shows "...and performing deep research..." when enabled
- `render_data_sidebar()` shows "Enhanced with deep research" caption when research brief was present

### Tests (Task 4)
- 7 new tests in `TestDeepResearchToggle` class:
  - init_state defaults include deep_research key
  - deep_research defaults to True
  - _debate_worker accepts deep_research parameter
  - _debate_worker deep_research defaults to True
  - ResearchBrief model importable with expected fields
  - build_research_brief function importable
  - build_data_package accepts deep_research parameter with True default

### Full Regression Check (Task 5)
- 261 tests pass (254 existing + 7 new), 7 deselected (live_api)
- No regressions in any existing test

## Deviations from Plan

None -- plan executed exactly as written.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1-5 | d674c90 | Deep Research toggle UI + worker wiring + 7 tests |

## Test Results

261 tests passing, 7 deselected (live_api). All 7 new tests pass. All existing tests pass unchanged.

## Self-Check: PASSED

- src/tinyic/ui/app.py: FOUND
- tests/test_ui.py: FOUND
- Commit d674c90: FOUND
