# Phase 10: UI Delivery - Context

**Phase:** 10
**Name:** UI Delivery
**Goal:** Users see company data alongside the debate, can download all outputs, and see what the debate cost

## Requirements

| ID | Description | Priority |
|----|-------------|----------|
| UI-05 | Company data sidebar visible during debate: key financials (P/E, market cap, revenue, margins), a price chart, data freshness timestamp, and data source warnings | MUST |
| UI-07 | Download buttons for investment memo (Markdown/DOCX), scorecard (Markdown), and full transcript (Markdown) after debate completes | MUST |
| OPS-01 | Per-debate token usage (input/output tokens) and estimated cost displayed in the UI after debate completes | MUST |

## Success Criteria

1. A sidebar panel shows key financials (P/E, market cap, revenue, margins), a price chart, data freshness timestamp, and any data source warnings -- visible before and during the debate
2. After debate completes, download buttons are available for: investment memo (Markdown/DOCX), scorecard (Markdown), and full transcript (Markdown)
3. Per-debate token usage (input/output tokens) and estimated cost are displayed in the UI after the debate completes
4. The data sidebar updates immediately after data fetching (before debate rounds begin), not after the debate ends
5. All new UI components work correctly with the existing debate flow (real-time display, steering, phase pauses)

## Dependencies

- Phase 7 (Release Hardening) -- COMPLETE: `get_debate_cost_stats()` in `tinyic.debate.__init__`
- Phase 9 (Memo & Disagreement Engine) -- COMPLETE: `generate_memo()`, `extract_disagreements()`, `ExportManager`, `InvestmentMemo.to_markdown()`, `DisagreementAnalysis.to_markdown()`

## Key Decisions

- Data sidebar as a right column in the main content area (not Streamlit sidebar, which is full with ticker+personas)
- Price chart via yfinance `.history()` -- fetched in worker thread alongside DataPackage, stored in session state
- Price history is UI-only data, NOT added to DataPackage model (which is designed for LLM context injection)
- Memo and disagreement generation happen in the worker thread after vote extraction (adds latency but keeps UI responsive)
- New `data_ready` event from worker to UI signals data availability before debate starts
- Download buttons use `st.download_button` with in-memory byte conversion (no temp files)
- DOCX download gated on `has_pandoc()` availability, consistent with Phase 9
- Post-debate view reorganized with tabs: Scorecard | Memo | Disagreements
- Cost display as `st.metric` components showing input/output tokens and estimated USD cost

## Key Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Price history fetch adds latency to data pipeline | LOW | Fetch 1yr daily prices (lightweight); runs in worker thread alongside existing data fetches |
| Memo generation adds post-debate latency (2 LLM calls) | MODERATE | Show "Generating memo..." spinner; generation happens in worker thread |
| DOCX export unavailable on some systems | LOW | Graceful fallback -- only show DOCX button when `has_pandoc()` is true |
| Data sidebar column reduces debate display width | LOW | Use 2:1 column ratio (debate gets 2/3 width); sidebar content is compact |
| DataPackage object may not be pickle-safe for session state | LOW | Store extracted fields (FinancialData dict, warnings list) instead of full object |

## Architectural Context

### Files to Modify
- `src/tinyic/ui/app.py` -- Data sidebar, memo/disagreement views, download buttons, cost display, data_ready event handling
- `src/tinyic/data/financials.py` -- Add `fetch_price_history()` function
- `tests/test_ui.py` -- Tests for new UI components

### Key APIs (Existing)
- `generate_memo(debate_result, data_package) -> InvestmentMemo` -- LLM synthesis
- `extract_disagreements(debate_result) -> DisagreementAnalysis` -- LLM extraction
- `get_debate_cost_stats(result) -> dict` -- Token counts and estimated cost
- `InvestmentMemo.to_markdown() -> str` -- Memo as Markdown
- `DisagreementAnalysis.to_markdown() -> str` -- Disagreements as Markdown
- `Scorecard.to_markdown() -> str` -- Scorecard as Markdown
- `ExportManager.export_markdown(content, filename) -> Path` -- Write Markdown file
- `ExportManager.export_docx(content, filename) -> Optional[Path]` -- Write DOCX file (pandoc-gated)
- `has_pandoc() -> bool` -- Check DOCX capability
- `DataPackage.financials` -- FinancialData with P/E, market cap, revenue, margins
- `DataPackage.fetched_at` -- Timestamp for data freshness
- `DataPackage.warnings` -- List of data source warnings
- `orchestrator.get_cost_stats()` -- Raw stats from TinyWorld (base_stats.input_tokens, etc.)

### Current UI Architecture (app.py)
- Session state: status flow (idle → ready → fetching → debating → extracting → complete)
- Background thread (`_debate_worker`) communicates via `ui_queue` with message types: status, phase, agent_start, message, phase_complete, complete, partial_complete, error
- `st.fragment` with `run_every=2` for real-time debate display
- Sidebar: ticker input, persona checkboxes, Start/New Debate buttons
- Main area: debate log rendered via `st.chat_message`, scorecard after completion
- Currently: only scorecard download button (Markdown), no memo/disagreement views, no data sidebar, no cost display

## Source Material
- Research: Derived from codebase exploration (no external research needed)
- Roadmap: `.planning/ROADMAP.md` (Phase 10 section)
- Phase 9 plans: `.planning/phases/09-memo-disagreement-engine/09-01-PLAN.md`, `09-02-PLAN.md`
