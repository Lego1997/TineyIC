---
phase: 10-ui-delivery
plan: 01
subsystem: ui
tags: [streamlit, yfinance, price-chart, cost-display, data-sidebar]

requires:
  - phase: 07-release-hardening
    provides: "get_debate_cost_stats() for token/cost formatting"
  - phase: 09-memo-disagreement-engine
    provides: "InvestmentMemo, DisagreementAnalysis, ExportManager for Plan 10-02"
provides:
  - "fetch_price_history() for 1yr daily price data from yfinance"
  - "render_data_sidebar() showing P/E, market cap, revenue, margins, ROE, D/E, price chart, freshness, warnings"
  - "_render_cost_display() showing input/output tokens and estimated USD cost"
  - "data_ready event from _debate_worker for immediate sidebar rendering"
  - "cost_stats in DebateResult for post-debate cost display"
  - "2:1 column layout (debate left, data right) during active and completed debate"
affects: [10-02-plan, ui-delivery]

tech-stack:
  added: []
  patterns: ["data_ready event pattern for pre-debate UI updates", "2:1 column layout for sidebar data", "session state for cross-thread sidebar data"]

key-files:
  created: []
  modified:
    - src/tinyic/data/financials.py
    - src/tinyic/ui/app.py
    - tests/test_ui.py

key-decisions:
  - "Module-level yfinance import in financials.py for testability (vs local import pattern)"
  - "Data sidebar in main content area columns (not Streamlit sidebar) to preserve existing ticker/persona sidebar"
  - "Price history is UI-only data, not added to DataPackage model"

patterns-established:
  - "data_ready event: worker thread sends UI data before debate starts via ui_queue"
  - "2:1 column layout: st.columns([2, 1]) for debate content + data sidebar"
  - "Conditional columns: use st.container() when no data available, columns when data ready"

requirements-completed: [UI-05, OPS-01]

duration: 30min
completed: 2026-03-23
---

# Phase 10 Plan 01: Data Sidebar + Cost Display Summary

**Company data sidebar with P/E, market cap, revenue, margins, price chart, freshness, warnings; per-debate cost display with token counts and estimated USD cost**

## Performance

- **Duration:** 30 min
- **Started:** 2026-03-23T10:42:30Z
- **Completed:** 2026-03-23T11:12:30Z
- **Tasks:** 4
- **Files modified:** 3

## Accomplishments
- fetch_price_history() returns 1yr daily prices from yfinance with graceful failure to empty list
- Data sidebar renders immediately after data fetch (before debate starts) via data_ready event
- 2:1 column layout shows debate content alongside company data during active debate and after completion
- Per-debate token usage (input/output tokens) and estimated USD cost displayed after debate completes
- cost_stats flows from orchestrator.get_cost_stats() through DebateResult to get_debate_cost_stats() to st.metric display
- 8 new tests covering price history, data sidebar, cost display, and data_ready event handling
- All 215 non-live tests pass with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Add fetch_price_history to financials.py with tests (TDD)** - `afecdee` (test RED) + `8d548f6` (feat GREEN)
2. **Task 2: Modify _debate_worker and _drain_queue for data_ready event and cost_stats** - `4972e88` (feat)
3. **Task 3: Implement render_data_sidebar(), _render_cost_display(), and update main layout** - `737259f` (feat)
4. **Task 4: Add tests for data sidebar, cost display, and data_ready event** - `803b53d` (test)

## Files Created/Modified
- `src/tinyic/data/financials.py` - Added fetch_price_history() for 1yr daily price data; module-level yfinance import
- `src/tinyic/ui/app.py` - Added render_data_sidebar(), _render_cost_display(), data_ready event handling, 2:1 column layout, sidebar session state defaults
- `tests/test_ui.py` - Added TestFetchPriceHistory (3), TestDataSidebarHelpers (1), TestCostDisplay (2), TestDataReadyEvent (2) = 8 new tests

## Decisions Made
- Used module-level `import yfinance as yf` in financials.py instead of local import pattern for testability (patch target needs module-level name)
- Data sidebar renders in main content area using st.columns([2, 1]), not in the Streamlit sidebar (which is full with ticker input and persona checkboxes)
- Price history is fetched alongside DataPackage but stored separately -- it's UI-only data, not injected into LLM context

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Module-level yfinance import for patch compatibility**
- **Found during:** Task 1 (fetch_price_history implementation)
- **Issue:** Plan specified local import pattern (`import yfinance as yf` inside function) but tests patch `tinyic.data.financials.yf` at module level. Local import creates a function-scoped name that can't be patched at module level.
- **Fix:** Added `import yfinance as yf` at module level in financials.py so patch target works correctly
- **Files modified:** src/tinyic/data/financials.py
- **Verification:** All 3 TestFetchPriceHistory tests pass with mocked yfinance
- **Committed in:** 8d548f6 (Task 1 GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Minimal -- same behavior, different import location for testability. No scope creep.

## Issues Encountered
- Worktree venv required `uv sync --all-packages` to install workspace packages (tinytroupe, tinyic) -- standard `uv sync` only installed root-level dev deps
- Live API tests (test_live_debate, test_listen_act_gpt52, test_sdk_v2_compatibility, test_differentiation) timeout/fail due to network conditions -- pre-existing, not caused by our changes

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Data sidebar and cost display complete, ready for Plan 10-02 (memo/disagreement views, download buttons)
- data_package stored in session_state.data_package for Plan 10-02 memo generation
- All existing UI functionality preserved: real-time debate display, user steering, phase pauses, scorecard

## Self-Check: PASSED

- All 4 key files exist (financials.py, app.py, test_ui.py, 10-01-SUMMARY.md)
- All 5 commits verified (afecdee, 8d548f6, 4972e88, 737259f, 803b53d)
- 28 UI tests passing, 215 total non-live tests passing

---
*Phase: 10-ui-delivery*
*Completed: 2026-03-23*
