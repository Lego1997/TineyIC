# Phase 7 Context: Release Hardening

## Phase Goal
Zero deprecation warnings, zero deselected tests, and a cost stats hook ready for UI consumption.

## Requirements
- **HARD-01**: Migrate edgartools from deprecated `edgar.files.html`/`edgar.files.htmltools` to `edgar.documents.HTMLParser`
- **HARD-02**: Stabilize live API tests — all 7 deselected `live_api` tests pass or removed with rationale
- **HARD-03**: Fix Pydantic v1-style `class Config` in SimulationValidator → `ConfigDict`
- **HARD-04**: Expose per-debate cost stats from `OpenAIClient.get_cost_stats()` to application layer

## Success Criteria
1. `uv run pytest tests/ -x -m "not live_api"` produces 0 deprecation warnings from edgartools or Pydantic in project-owned code
2. edgartools usage in `src/tinyic/data/filings.py` uses `edgar.documents.HTMLParser` (or equivalent non-deprecated API) instead of `edgar.files.html` / `edgar.files.htmltools`
3. All 7 previously-deselected `live_api` tests either pass when API keys are present, or are removed with a documented rationale in the test file
4. `SimulationValidator` in TinyTroupe fork uses `model_config = ConfigDict(...)` instead of `class Config`
5. A function `get_debate_cost_stats()` exists that returns token counts and estimated cost from `OpenAIClient`, callable after a debate completes

## Current State (Research Findings)

### HARD-01: edgartools deprecation
- **edgartools version**: 5.25.1 (latest available)
- **Our code**: `src/tinyic/data/filings.py` does NOT directly import `edgar.files.html` or `edgar.files.htmltools`
- **Problem**: Warnings fire from edgartools' *own internal imports* when we call `latest.obj()` and `latest.text()`:
  - `edgar._markdown` → `edgar.files.html_documents` (HtmlDocument deprecated)
  - `edgar.files.markdown` → `edgar.files.html` (BaseNode, Document deprecated)
  - `edgar._filings` → `edgar.files.htmltools` (html_sections deprecated)
- **These warnings trigger at import time**, not from our API calls
- **New API available**: `edgar.documents.HTMLParser` with `.parse()`, `.parse_file()`, `.parse_url()` methods
- **Filing object**: `latest.document` returns an `Attachment` with `.markdown()`, `.content()` methods
- **Strategy**: Rewrite filings.py to use `HTMLParser` for text extraction + suppress import-level edgartools warnings in pytest config (since those are from the package itself)

### HARD-02: live_api tests (7 deselected)
1. `test_live_aapl` — `tests/test_data_pipeline.py:914` (end-to-end data pipeline)
2. `test_listen_act_gpt52` — `tests/test_investor_persona.py:80` (FOUND-03 validation)
3. `test_sdk_v2_compatibility` — `tests/test_investor_persona.py:112` (FOUND-04 validation)
4. `test_differentiation` — `tests/test_personas.py:401` (persona differentiation)
5. `test_analyze_company_returns_structure` — `tests/test_personas.py:436` (persona analysis)
6. `test_format_vote_returns_structure` — `tests/test_personas.py:451` (vote format)
7. `test_live_debate` — `tests/test_debate_live.py:11` (full debate, ~$1-2 API cost)

### HARD-03: Pydantic class Config
Two v1-style `class Config` blocks in `src/tinytroupe/validation/simulation_validator.py`:
1. `SimulationExperimentDataset` (line 68) — `extra = "forbid"`, `validate_assignment = True`
2. `SimulationExperimentEmpiricalValidationResult` (line 735) — same settings

All tinyic models (data/models.py, debate/models.py) are already v2-compliant.

### HARD-04: Cost stats exposure
- `OpenAIClient.get_cost_stats()` exists (openai_client.py:695) — returns dict with input_tokens, output_tokens, total_tokens, model_calls, cached_calls
- `TinyWorld.get_cost_stats()` exists (tiny_world.py:1021) — adds per-agent and per-step breakdowns
- `DebateOrchestrator` inherits from `TinyWorld`, so `get_cost_stats()` is already available on the orchestrator
- **Missing**: No `cost_stats` field on `DebateResult` model, no wiring in `run_debate()`, no application-layer function in `tinyic`

## Dependencies
Phase 7 is a hard gate — all v1.1 feature phases depend on it.
No upstream dependencies.

## Test Baseline
- 154 tests passing, 7 deselected (live_api)
- 5 warnings: 3 edgartools deprecation, 2 Pydantic deprecation
