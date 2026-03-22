---
phase: 06-enhanced-ticker-resolution
plan: 01
subsystem: data-pipeline, ui
tags: [ticker-resolution, yfinance, international-tickers, name-search, fallback-chain]
dependency_graph:
  requires: [05-02]
  provides: [enhanced-ticker-resolver, company-name-search, international-ticker-support]
  affects: [pipeline, ui-sidebar, data-pipeline-tests]
tech_stack:
  added: [yfinance.Search, yfinance.fast_info]
  patterns: [4-layer-fallback-chain, input-classification-heuristic, 3-tuple-return]
key_files:
  created: []
  modified:
    - src/tinyic/data/ticker_resolver.py
    - src/tinyic/data/pipeline.py
    - src/tinyic/ui/app.py
    - tests/test_data_pipeline.py
decisions:
  - Mixed-case heuristic for ticker vs company name classification (title case = name, pure upper/lower = ticker)
metrics:
  duration: 4min
  completed: 2026-03-22
  tasks: 2
  files: 4
  tests_added: 20
  tests_total: 154
---

# Phase 06 Plan 01: Enhanced Ticker Resolution Summary

Enhanced ticker resolver with 4-layer fallback chain (.info -> fast_info -> Search -> invalid), company name search via yfinance.Search, and international exchange support (0700.HK, 7203.T, SAP.DE).

## Tasks Completed

### Task 1: Rewrite ticker resolver with fallback chain, name search, and international support
**Commit:** b15c9a3

Rewrote `src/tinyic/data/ticker_resolver.py` with:
- **Input classification**: `_looks_like_ticker()` heuristic distinguishes ticker symbols from company names using case analysis (mixed case like "Apple" = name, pure upper/lower like "AAPL" or "aapl" = ticker)
- **Ticker normalization**: `_normalize_ticker()` strips and uppercases (handles "sap.de" -> "SAP.DE")
- **4-layer fallback chain** for ticker-like input:
  1. `_try_info()` - yfinance Ticker.info for full metadata with company name
  2. `_try_fast_info()` - lightweight validity check via fast_info.currency
  3. `_try_search()` - yfinance Search API for partial/misspelled tickers
  4. Return invalid `(False, "", "")`
- **Company name search** path via `_try_search()` with EQUITY preference, fallback to direct lookup
- **3-tuple return**: `(is_valid, ticker_symbol, company_name)`

Updated and added tests:
- Updated 5 existing TestTickerResolver tests for 3-tuple return
- Added TestResolveTickerNameSearch (5 tests): Apple, Tencent, EQUITY preference, no results, exception
- Added TestResolveTickerInternational (4 tests): HK, Tokyo, German, lowercase normalization
- Added TestResolveTickerFallback (7 tests): all fallback chain combinations including edge cases
- Added TestLooksLikeTicker (3 tests): US tickers, international, company names

### Task 2: Update pipeline and UI callers for new return type
**Commit:** 272bc2a

- **pipeline.py**: Updated `build_data_package()` to unpack 3-tuple `(is_valid, resolved_ticker, company_name)` and use `resolved_ticker` for all downstream fetcher calls, DataPackage construction, and logging
- **app.py**: Changed sidebar input label to "Company or Ticker", placeholder to "e.g., AAPL, Apple, 0700.HK", removed `.upper().strip()` on input, added `_last_query` tracking, updated `_resolve_ticker()` to set `st.session_state.ticker` from resolved symbol, improved error message
- **test_data_pipeline.py**: Updated all 5 TestBuildDataPackage mock return values from 2-tuple to 3-tuple

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed _looks_like_ticker heuristic for mixed-case company names**
- **Found during:** Task 1
- **Issue:** Plan specified `_looks_like_ticker` should uppercase input before matching, but this makes "Apple" match as a ticker (since "APPLE" matches `[A-Z0-9]{1,5}`). The plan also specified "Returns False for: Apple" which contradicts the uppercasing approach.
- **Fix:** Added mixed-case detection: if input starts with uppercase letter and contains lowercase letters (title case pattern), classify as company name. Pure uppercase, pure lowercase, and numeric-prefixed inputs classify as tickers.
- **Files modified:** src/tinyic/data/ticker_resolver.py
- **Commit:** b15c9a3

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Title-case detection for company names | "Apple" (title case) is clearly a company name, not a ticker. The `_looks_like_ticker` heuristic checks if input starts with uppercase and contains lowercase -- if so, it's a name. This handles "Apple", "Tencent", "Toyota" correctly while still accepting "AAPL", "aapl", "0700.HK" as tickers. |

## Verification Results

- `uv run pytest tests/test_data_pipeline.py -x -v --timeout=60`: 53 passed (all non-live tests)
- `uv run pytest tests/ -x -v --timeout=120 -m "not live_api"`: 154 passed, 0 failed
- All existing tests updated and passing with no regressions

## Self-Check: PASSED

All 4 modified files exist on disk. Both task commits (b15c9a3, 272bc2a) verified in git history. 154 tests pass.
