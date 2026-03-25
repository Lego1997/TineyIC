---
phase: 10-ui-delivery
plan: 02
subsystem: ui
tags: [streamlit, memo, disagreements, downloads, tabs]

requires:
  - phase: 10-ui-delivery
    plan: 01
    provides: "data_ready event, data sidebar, cost display, 2:1 column layout, data_package in session_state"
  - phase: 09-memo-disagreement-engine
    provides: "generate_memo(), extract_disagreements(), InvestmentMemo, DisagreementAnalysis, ExportManager"
provides:
  - "render_memo_tab() showing 5 memo sections with grounding metadata"
  - "render_disagreements_tab() showing up to 3 disagreement dimensions with evidence quotes"
  - "_render_download_buttons() for scorecard (MD), memo (MD/DOCX), transcript (MD)"
  - "Tabbed post-debate view: Scorecard | Investment Memo | Disagreements"
  - "Memo/disagreement generation in _debate_worker with 'generating' status"
  - "Graceful fallback when memo/disagreement generation fails"
affects: [ui-delivery]

tech-stack:
  added: []
  patterns: ["tabbed post-debate view with st.tabs", "generating status for post-debate LLM synthesis", "centralized download buttons below tabs"]

key-files:
  created: []
  modified:
    - src/tinyic/ui/app.py
    - tests/test_ui.py

key-decisions:
  - "Downloads centralized in _render_download_buttons() below tabs, not per-tab"
  - "Old scorecard download button removed from render_scorecard() to avoid duplication"
  - "DOCX download gated on has_pandoc() with temp directory for conversion"

patterns-established:
  - "generating status: worker sends status update before post-debate LLM synthesis"
  - "tabbed results: st.tabs() for multi-artifact post-debate view"
  - "centralized downloads: single download section below all result tabs"

requirements-completed: [UI-07]

duration: 15min
completed: 2026-03-25
---

# Phase 10 Plan 02: Memo/Disagreement Views + Download Buttons Summary

**Tabbed post-debate view with investment memo, disagreement analysis, and download buttons for all debate artifacts**

## Performance

- **Duration:** 15 min
- **Tasks:** 4
- **Files modified:** 2

## Accomplishments
- _debate_worker generates memo and disagreements post-debate via generate_memo() and extract_disagreements()
- "generating" status added to sidebar locks, fragment polling, and main routing with spinner message
- render_memo_tab() renders all 5 memo sections (Executive Summary, Investment Thesis, Key Risks, Valuation Discussion, Final Verdict) with contributing personas and data references
- render_disagreements_tab() renders up to 3 disagreement dimensions with named sides, evidence quotes in blockquotes, and resolution text
- _render_download_buttons() provides downloads for: scorecard (MD), investment memo (MD), investment memo (DOCX when pandoc available), and full transcript (MD)
- Post-debate view reorganized with tabs: Scorecard | Investment Memo | Disagreements
- Old scorecard download button removed from render_scorecard() -- centralized in _render_download_buttons()
- Graceful fallback when memo/disagreement generation fails (no crash, info message)
- 9 new tests covering memo tab, disagreements tab, and download button logic
- All 223 non-live tests pass with zero regressions

## Files Modified
- `src/tinyic/ui/app.py` - Added render_memo_tab(), render_disagreements_tab(), _render_download_buttons(), memo/disagreement generation in worker, "generating" status, tabbed post-debate view
- `tests/test_ui.py` - Added TestMemoTab (3), TestDisagreementsTab (3), TestDownloadButtons (3) = 9 new tests

## Deviations from Plan

None -- all tasks executed as specified.

## Issues Encountered
- Live API tests (test_live_debate, test_listen_act_gpt52) timeout/fail due to network conditions -- pre-existing, not caused by our changes

## User Setup Required
None - pandoc is optional (DOCX button only appears when available).

## Self-Check: PASSED

- All new UI functions importable (render_memo_tab, render_disagreements_tab, _render_download_buttons)
- 37 UI tests passing (28 existing + 9 new)
- 223 total non-live tests passing
- Phase 10 fully complete (both plans executed)

---
*Phase: 10-ui-delivery*
*Completed: 2026-03-25*
