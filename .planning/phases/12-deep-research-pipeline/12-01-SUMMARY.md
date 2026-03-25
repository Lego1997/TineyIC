---
phase: 12
plan: 1
subsystem: data-pipeline
tags: [research, web-search, llm-synthesis, pydantic, pipeline]
dependency_graph:
  requires: [tinytroupe-client, openai-responses-api, extract_json]
  provides: [ResearchBrief, build_research_brief, deep_research_toggle]
  affects: [DataPackage, build_data_package, to_context_string]
tech_stack:
  added: [openai-responses-api-web-search]
  patterns: [two-stage-search-then-synthesize, graceful-degradation]
key_files:
  created:
    - src/tinyic/data/research.py
    - tests/test_research.py
  modified:
    - src/tinyic/data/models.py
    - src/tinyic/data/pipeline.py
    - src/tinyic/data/__init__.py
    - tests/test_data_pipeline.py
    - .planning/ROADMAP.md
decisions:
  - "OpenAI Responses API with web_search_preview for web search (leverages existing API key)"
  - "Two-stage search: company-focused + environment/analyst-focused queries for comprehensive coverage"
  - "TinyTroupe client for synthesis (consistent with memo.py pattern, respects model selection)"
  - "Context budget increased from 12K to 20K chars to accommodate research brief"
  - "deep_research=True by default -- enriched research is opt-out, not opt-in"
metrics:
  tasks: 4
  files_created: 2
  files_modified: 5
  tests_added: 19
  completed: "2026-03-25"
---

# Phase 12 Plan 1: ResearchBrief Model, Web Search + LLM Synthesis Pipeline Summary

ResearchBrief Pydantic model with 5 structured fields, two-stage web search pipeline using OpenAI Responses API, LLM synthesis via TinyTroupe client, and full pipeline integration with deep_research toggle defaulting to enabled.

## What Was Built

### ResearchBrief Model (Task 1)
- New Pydantic model with 5 string fields: `business_model`, `industry_trends`, `management`, `recent_catalysts`, `analyst_perspectives`
- All fields default to empty string for graceful partial population
- Added as `Optional[ResearchBrief]` field on `DataPackage`
- `to_context_string()` budget increased from 12K to 20K chars, with research_brief truncation (800 chars per field) before social/filing truncation

### Research Pipeline (Task 2)
- `_web_search()`: Performs web search via OpenAI Responses API with `web_search_preview` tool, capped at 3000 chars per result
- `build_research_brief()`: Two-stage pipeline:
  1. Company search: business model, competitive advantage, management, capital allocation
  2. Environment search: industry trends, catalysts, analyst perspectives, price targets
  3. LLM synthesis: TinyTroupe client formats search results into structured JSON matching ResearchBrief fields
- Graceful degradation at every stage: missing API key returns None, partial search results still proceed to synthesis, LLM failure returns None

### Pipeline Integration (Task 3)
- `build_data_package()` gains `deep_research: bool = True` parameter
- When enabled, calls `build_research_brief()` after social sentiment fetch
- Failed research adds "Deep research unavailable" warning (consistent with existing pattern)
- `ResearchBrief` and `build_research_brief` exported from `tinyic.data` package
- Updated 4 existing pipeline tests to mock `build_research_brief` and adjust warning counts

### 19 Unit Tests
- 3 model tests: creation, defaults, serialization round-trip
- 10 build_research_brief tests: success, company/environment search called, synthesis prompt content, no API key, search failure, synthesis failure, bad JSON, partial search
- 6 pipeline integration tests: with research, without research, default deep_research, failure warning, context string inclusion/exclusion

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Updated existing pipeline tests for build_research_brief compatibility**
- **Found during:** Task 3
- **Issue:** Existing `TestBuildDataPackage` tests in `test_data_pipeline.py` did not mock `build_research_brief`, causing warning count mismatches after pipeline integration
- **Fix:** Added `@patch("tinyic.data.pipeline.build_research_brief")` to 4 existing tests, updated warning counts (3->4, 5->6), updated context budget assertion (12K->20K)
- **Files modified:** `tests/test_data_pipeline.py`
- **Commit:** 18c7e53

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 663340c | ResearchBrief model and DataPackage extension |
| 2 | d430490 | research.py with web search + LLM synthesis and 13 tests |
| 3 | 18c7e53 | Pipeline integration with deep_research toggle |
| 4 | 6ed14b2 | ROADMAP.md update with Phase 12 plan details |

## Test Results

254 tests passing, 7 deselected (live_api). All 19 new tests pass. All existing tests pass with updated mocks.

## Self-Check: PASSED

- All created files exist on disk
- All 4 commit hashes verified in git log
