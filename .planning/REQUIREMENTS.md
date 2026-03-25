# Requirements: openIC

**Defined:** 2026-03-20
**Core Value:** Investor personas must be convincingly distinct and philosophically accurate -- each argues from their real-world framework, producing genuinely differentiated perspectives.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Foundation

- [x] **FOUND-01**: Project uses Python 3.12 with uv package manager and proper directory structure
- [x] **FOUND-02**: TinyTroupe v0.6.0 is forked and integrated as a local package in monorepo
- [x] **FOUND-03**: OpenAI GPT-5.2 integration works through TinyTroupe with reasoning_effort=xhigh
- [x] **FOUND-04**: OpenAI SDK v2.x compatibility with TinyTroupe fork is validated

### Personas

- [x] **PERS-01**: Warren Buffett persona with philosophy distilled from shareholder letters, interviews, and public writings
- [x] **PERS-02**: Charlie Munger persona with philosophy distilled from speeches, Poor Charlie's Almanack, and public writings
- [x] **PERS-03**: Benjamin Graham persona with philosophy distilled from The Intelligent Investor, Security Analysis, and public writings
- [x] **PERS-04**: Peter Lynch persona with philosophy distilled from One Up on Wall Street, Beating the Street, and public writings
- [x] **PERS-05**: Howard Marks persona with philosophy distilled from investor memos, The Most Important Thing, and public writings
- [x] **PERS-06**: Li Lu persona with philosophy distilled from Columbia lectures, shareholder letters, and public writings
- [x] **PERS-07**: User can select which personas participate in a given debate

### Financial Data

- [x] **DATA-01**: User can enter a stock ticker and system resolves it to a valid company
- [x] **DATA-02**: System fetches financial fundamentals (income statement, balance sheet, key ratios) via yfinance
- [x] **DATA-03**: System fetches SEC filings (10-K, 10-Q) via edgartools
- [x] **DATA-04**: System fetches recent company news via yfinance Ticker.news
- [x] **DATA-05**: System fetches views and contrarian views from X/Twitter via xAI API

### Debate

- [x] **DEBT-01**: Debate follows structured phases: opening statements, cross-examination, rebuttal, final verdict
- [x] **DEBT-02**: Debate orchestrator manages turn order and phase transitions using TinyWorld broadcast+run pattern
- [x] **DEBT-03**: All personas receive the same financial DataPackage as shared context at debate start

### Output

- [x] **OUTP-01**: System extracts a buy/hold/sell vote from each participating persona via ResultsExtractor
- [x] **OUTP-02**: System generates a scorecard displaying all persona votes with key reasoning

### UI

- [x] **UI-01**: Streamlit app with ticker input field and company resolution
- [x] **UI-02**: Real-time debate display showing each persona's statements via st.chat_message
- [x] **UI-03**: Scorecard view displaying all votes and reasoning after debate completes
- [x] **UI-04**: User can inject questions or steer the debate mid-session via chat input

### Enhanced Ticker Resolution

- [x] **RESOLVE-01**: Entering a company name (e.g., "Apple", "Toyota", "Tencent") resolves to the correct ticker and company name via yfinance Search
- [x] **RESOLVE-02**: International tickers work (e.g., 0700.HK, 7203.T, SAP.DE, MC.PA) -- the resolver accepts exchange-suffixed symbols
- [x] **RESOLVE-03**: The resolver is robust against yfinance `.info` failures -- uses multiple fallback strategies (fast_info, Search API) before returning invalid

## v1.1 Requirements

Output quality, debate robustness, and polish. Makes the output worth reading and the debate worth watching.

### Release Hardening

- [x] **HARD-01**: Migrate edgartools usage from deprecated `edgar.files.html` / `edgar.files.htmltools` to `edgar.documents.HTMLParser` before v6.0 removal
- [x] **HARD-02**: Stabilize live API test path -- all 7 currently-deselected `live_api` tests either pass reliably or are removed with documented rationale
- [x] **HARD-03**: Fix Pydantic v1-style `class Config` deprecation in TinyTroupe fork's `SimulationValidator` (use `ConfigDict` instead)
- [x] **HARD-04**: Expose per-debate token/call cost statistics retrieval point from `OpenAIClient.get_cost_stats()` to application layer

### Debate Quality

- [x] **DEBT-04**: Anti-convergence controls that prevent persona opinions from collapsing to consensus over multi-round debates (note: system prompt re-injection already happens via `TinyPerson.reset_prompt()` each `act()` call -- this requires additional structural controls)
- [x] **DEBT-05**: Rotating devil's advocate role during cross-examination phase -- one persona argues the strongest counter-position, with final votes remaining unconstrained
- [x] **PERS-08**: Lightweight automated differentiation regression tests that detect convergence on fixed company fixtures (PERS-08-lite scope -- not full differentiation suite)

### Memo & Disagreement

- [x] **OUTP-03**: Full narrative investment memo with structured sections (thesis, risks, valuation, verdict) synthesized from debate transcript, scorecard, and data package via LLM
- [x] **OUTP-04**: Cross-persona disagreement extraction identifying key dimensions where personas diverge most, with evidence from debate transcript
- [x] **OUTP-05**: Export investment memo and scorecard as Markdown and DOCX via ArtifactExporter with pandoc

### UI Delivery

- [x] **UI-05**: Company data sidebar panel showing key financials, price chart, data freshness indicators, and source warnings -- visible during debate
- [x] **UI-07**: Download buttons for memo, scorecard, and transcript in Markdown and DOCX formats
- [x] **OPS-01**: Per-debate token usage and estimated cost displayed in UI after debate completes

### Model Selection

- [x] **CONFIG-01**: User can select the LLM model for a debate from a dropdown in the Streamlit sidebar, with available models populated from a configurable list (default: GPT-5.2, Codex 5.3)
- [x] **CONFIG-02**: Selected model overrides `config.ini` at runtime and is passed through `DebateOrchestrator` to all `TinyPerson.act()` calls for that debate session
- [x] **CONFIG-03**: Model selection includes a brief description of each model's strengths (e.g., "GPT-5.2: deep reasoning", "Codex 5.3: fast, code-oriented") to help users choose

### Deep Research Pipeline

- [x] **DATA-06**: Before each debate, the system conducts a structured deep research pass on the target company using web search APIs and LLM synthesis, producing a comprehensive research brief that goes beyond the raw financial data sources
- [x] **DATA-07**: Research brief covers key investment analysis dimensions: business model and competitive landscape, industry trends and tailwinds/headwinds, management quality and track record, recent developments and catalysts, and bull/bear cases from public analyst perspectives
- [x] **DATA-08**: Research results are stored in a new `ResearchBrief` model field on `DataPackage` and injected into all persona contexts at debate start, so every persona argues from the same enriched fact base
- [x] **DATA-09**: The research step is configurable (can be skipped for faster debates) and degrades gracefully -- if web search or synthesis fails, the debate proceeds with the existing data sources and a warning

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Persona Quality

- **PERS-09**: Contrastive prompting with anti-persona instructions to prevent convergence

### Debate Enhancements

- **DEBT-06**: Fact-checking layer verifying numerical claims against source DataPackage

### UI Enhancements

- **UI-06**: Configurable debate parameters (number of rounds, temperature)

### Operational

- **OPS-02**: Data validation layer with completeness checks and fallback handling

## Out of Scope

| Feature | Reason |
|---------|--------|
| RAG pipeline over investor writings | v1 uses curated prompts -- simpler, more controllable |
| Real-time market data streaming | Batch fetch at debate start is sufficient |
| Paid financial data APIs | Free sources (yfinance, EDGAR, xAI) only for v1 |
| Mobile app | Web-first via Streamlit |
| User accounts / authentication | Single-user tool for v1 |
| Portfolio tracking / trade execution | Analysis tool only, no trading |
| Backtesting | Not a prediction tool -- debate quality is the value |
| Custom persona creation | Fixed roster of 6 famous investors for v1 |
| Multi-company comparison | One company per debate session |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| FOUND-01 | Phase 1: Foundation and TinyTroupe Integration | Complete |
| FOUND-02 | Phase 1: Foundation and TinyTroupe Integration | Complete |
| FOUND-03 | Phase 1: Foundation and TinyTroupe Integration | Complete |
| FOUND-04 | Phase 1: Foundation and TinyTroupe Integration | Complete |
| PERS-01 | Phase 2: Persona Engineering | Complete |
| PERS-02 | Phase 2: Persona Engineering | Complete |
| PERS-03 | Phase 2: Persona Engineering | Complete |
| PERS-04 | Phase 2: Persona Engineering | Complete |
| PERS-05 | Phase 2: Persona Engineering | Complete |
| PERS-06 | Phase 2: Persona Engineering | Complete |
| PERS-07 | Phase 4: Debate Engine and Verdict Extraction | Complete |
| DATA-01 | Phase 3: Financial Data Pipeline | Complete |
| DATA-02 | Phase 3: Financial Data Pipeline | Complete |
| DATA-03 | Phase 3: Financial Data Pipeline | Complete |
| DATA-04 | Phase 3: Financial Data Pipeline | Complete |
| DATA-05 | Phase 3: Financial Data Pipeline | Complete |
| DEBT-01 | Phase 4: Debate Engine and Verdict Extraction | Complete |
| DEBT-02 | Phase 4: Debate Engine and Verdict Extraction | Complete |
| DEBT-03 | Phase 4: Debate Engine and Verdict Extraction | Complete |
| OUTP-01 | Phase 4: Debate Engine and Verdict Extraction | Complete |
| OUTP-02 | Phase 4: Debate Engine and Verdict Extraction | Complete |
| UI-01 | Phase 5: Streamlit UI | Complete |
| UI-02 | Phase 5: Streamlit UI | Complete |
| UI-03 | Phase 5: Streamlit UI | Complete |
| UI-04 | Phase 5: Streamlit UI | Complete |
| RESOLVE-01 | Phase 6: Enhanced Ticker Resolution | Complete |
| RESOLVE-02 | Phase 6: Enhanced Ticker Resolution | Complete |
| RESOLVE-03 | Phase 6: Enhanced Ticker Resolution | Complete |

### v1.1 Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| HARD-01 | Phase 7: Release Hardening | Complete |
| HARD-02 | Phase 7: Release Hardening | Complete |
| HARD-03 | Phase 7: Release Hardening | Complete |
| HARD-04 | Phase 7: Release Hardening | Complete |
| DEBT-04 | Phase 8: Debate Quality Controls | Complete |
| DEBT-05 | Phase 8: Debate Quality Controls | Complete |
| PERS-08 | Phase 8: Debate Quality Controls | Complete |
| OUTP-03 | Phase 9: Memo & Disagreement Engine | Complete |
| OUTP-04 | Phase 9: Memo & Disagreement Engine | Complete |
| OUTP-05 | Phase 9: Memo & Disagreement Engine | Complete |
| UI-05 | Phase 10: UI Delivery | Complete |
| UI-07 | Phase 10: UI Delivery | Complete |
| OPS-01 | Phase 10: UI Delivery | Complete |
| CONFIG-01 | Phase 11: Model Selection | Complete |
| CONFIG-02 | Phase 11: Model Selection | Complete |
| CONFIG-03 | Phase 11: Model Selection | Complete |
| DATA-06 | Phase 12: Deep Research Pipeline | Complete |
| DATA-07 | Phase 12: Deep Research Pipeline | Complete |
| DATA-08 | Phase 12: Deep Research Pipeline | Complete |
| DATA-09 | Phase 12: Deep Research Pipeline | Complete |

**Coverage:**
- v1 requirements: 28 total, 28 complete
- v1.1 requirements: 20 total, 20 complete
- Mapped to phases: 48/48
- Unmapped: 0

---
*Requirements defined: 2026-03-20*
*Last updated: 2026-03-26 -- v1.1 milestone archived, all 20 requirements validated*
