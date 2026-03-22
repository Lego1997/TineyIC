---
phase: 05-streamlit-ui
plan: 01
status: complete
started: "2026-03-22"
completed: "2026-03-22"
duration: ~8min
tasks: 2
files_modified: 4
tests_added: 8
tests_total: 123
commit: 138ef80
---

# Plan 05-01 Summary: Orchestrator Streaming Callbacks + Streamlit UI App

## What was built

1. **Orchestrator streaming callbacks** -- Added `on_phase_start`, `on_agent_start`, `on_agent_done` optional callback attributes to `DebateOrchestrator.__init__()`. Updated `_step()` to fire callbacks at the right moments. Existing behavior preserved when callbacks are None.

2. **Streamlit dependency** -- Added `streamlit>=1.40.0` to `src/tinyic/pyproject.toml`.

3. **Complete Streamlit application** (`src/tinyic/ui/app.py`) with:
   - Sidebar: ticker input with auto-resolve via `resolve_ticker()`, 6 persona checkboxes with taglines, Start/New Debate buttons, controls lock during active debate
   - Background thread + `queue.Queue` pattern for thread-safe debate execution (background thread NEVER writes to `st.session_state`)
   - Real-time debate display using `st.fragment(run_every=2)` polling, `st.chat_message` per agent
   - Scorecard: color-coded consensus banner, individual vote table with emoji indicators, collapsed transcript, collapsed raw data, markdown download button
   - Re-run with same ticker shortcut
   - `_TESTING` guard for pytest import compatibility

4. **8 unit tests** in `tests/test_ui.py`: 4 orchestrator callback tests + 4 `extract_talk_content` helper tests.

## Requirements delivered

| Requirement | Status |
|-------------|--------|
| UI-01: Ticker input + company resolution | Complete |
| UI-02: Real-time debate display via st.chat_message | Complete |
| UI-03: Scorecard with votes and reasoning | Complete |
