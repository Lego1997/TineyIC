---
phase: "07"
plan: "02"
subsystem: debate-engine, test-infrastructure
tags: [cost-stats, live-api-tests, hardening]
dependency_graph:
  requires: []
  provides: [cost-stats-api, stable-live-tests]
  affects: [debate-result-model, debate-init, test-suite]
tech_stack:
  added: []
  patterns: [convenience-wrapper, optional-field-backward-compat]
key_files:
  created: []
  modified:
    - src/tinyic/debate/models.py
    - src/tinyic/debate/__init__.py
    - tests/test_debate.py
    - tests/test_investor_persona.py
    - tests/test_personas.py
decisions:
  - "Keep test_live_aapl without has_api_key fixture -- it genuinely only needs network, not an API key"
  - "Use GPT-5.2 pricing approximation ($2.50/M input, $10.00/M output) for cost estimation"
  - "Handle both TinyWorld nested format (base_stats key) and raw OpenAIClient format in get_debate_cost_stats"
metrics:
  duration_seconds: 375
  completed: "2026-03-23T02:36:30Z"
  tasks_completed: 5
  tasks_total: 5
  tests_added: 6
  tests_total: 160
requirements: [HARD-02, HARD-04]
---

# Phase 7 Plan 2: Live API Test Stabilization + Cost Stats Exposure Summary

Wire DebateOrchestrator.get_cost_stats() through run_debate() into DebateResult.cost_stats with get_debate_cost_stats() convenience function; add timeouts to all 7 live_api tests.

## What Was Done

### Task 1: Audit live_api tests

Reviewed all 7 live_api tests across 4 test files:
- **test_live_aapl** (test_data_pipeline.py): Network-only test, no API key needed. Added `@pytest.mark.timeout(120)` and docstring explaining this is a network-only test. (Already committed in 07-01 alongside filing migration.)
- **test_listen_act_gpt52** (test_investor_persona.py): Added `@pytest.mark.timeout(120)`.
- **test_sdk_v2_compatibility** (test_investor_persona.py): Added `@pytest.mark.timeout(120)`.
- **test_differentiation** (test_personas.py): Added `@pytest.mark.timeout(300)` (calls 6 personas).
- **test_analyze_company_returns_structure** (test_personas.py): Added `@pytest.mark.timeout(120)`.
- **test_format_vote_returns_structure** (test_personas.py): Added `@pytest.mark.timeout(120)`.
- **test_live_debate** (test_debate_live.py): Already had `@pytest.mark.timeout(300)`.

All 7 tests retained. All have the `has_api_key` fixture except `test_live_aapl` which only needs network access.

### Task 2: Add cost_stats to DebateResult

Added `cost_stats: Optional[dict] = Field(default=None, ...)` to `DebateResult` in `src/tinyic/debate/models.py`. Backward compatible -- existing code that constructs `DebateResult` without `cost_stats` continues to work.

### Task 3: Wire cost stats in run_debate()

Added `cost_stats = orchestrator.get_cost_stats()` after `orchestrator.run_debate()` in `src/tinyic/debate/__init__.py`. The result dict is passed as `cost_stats=cost_stats` to the `DebateResult` constructor.

### Task 4: Create get_debate_cost_stats() convenience function

Added `get_debate_cost_stats(result: DebateResult) -> dict` to `src/tinyic/debate/__init__.py`. Handles:
- `None` cost_stats (returns zeroed dict)
- Raw format (flat dict with input_tokens, output_tokens, etc.)
- TinyWorld nested format (dict with `base_stats` key)
- GPT-5.2 cost estimation ($2.50/M input, $10.00/M output)

Added to `__all__`.

### Task 5: Add tests for cost stats

Added `TestCostStats` class to `tests/test_debate.py` with 6 tests:
1. `test_debate_result_accepts_cost_stats` -- field acceptance
2. `test_debate_result_cost_stats_optional` -- backward compatibility
3. `test_get_debate_cost_stats_populated` -- formatted stats from populated result
4. `test_get_debate_cost_stats_none` -- graceful None handling
5. `test_get_debate_cost_stats_tinyworld_format` -- nested base_stats format
6. `test_run_debate_populates_cost_stats` -- integration test with mocked orchestrator

Also fixed existing `test_run_debate_wiring` to provide `get_cost_stats` mock return value (required after run_debate() now calls it).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_run_debate_wiring mock after cost_stats wiring**
- **Found during:** Task 5 (test verification)
- **Issue:** Adding `orchestrator.get_cost_stats()` call in `run_debate()` caused existing `test_run_debate_wiring` to fail because the mock orchestrator returned a MagicMock (not a dict) for `get_cost_stats()`, which Pydantic rejected
- **Fix:** Added `mock_orch.get_cost_stats.return_value = {"input_tokens": 0, ...}` to the existing test
- **Files modified:** `tests/test_debate.py`
- **Commit:** f125333

## Verification

```
uv run pytest tests/ -x -m "not live_api" -v
====================== 160 passed, 7 deselected in 4.72s =======================
```

Test count: 154 (baseline) + 6 (new cost stats) = 160 passing.

## Commits

| Hash | Message |
|------|---------|
| f125333 | feat(07-02): stabilize live_api tests and expose debate cost statistics (HARD-02, HARD-04) |

## Key Decisions

1. **test_live_aapl stays without has_api_key**: It only needs yfinance+edgartools (free, network-only). Adding `has_api_key` would cause it to be skipped unnecessarily when running `-m live_api` without OPENAI_API_KEY.
2. **GPT-5.2 pricing for cost estimation**: Used $2.50/M input tokens and $10.00/M output tokens as an approximation.
3. **Dual format support**: `get_debate_cost_stats()` handles both raw `OpenAIClient.get_cost_stats()` format and `TinyWorld.get_cost_stats()` nested format with `base_stats` key.

## Self-Check: PASSED

All 5 modified files exist on disk. Commit f125333 exists in git log. Summary file exists.
