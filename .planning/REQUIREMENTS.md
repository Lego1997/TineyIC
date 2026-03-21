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
- [ ] **PERS-07**: User can select which personas participate in a given debate

### Financial Data

- [x] **DATA-01**: User can enter a stock ticker and system resolves it to a valid company
- [x] **DATA-02**: System fetches financial fundamentals (income statement, balance sheet, key ratios) via yfinance
- [x] **DATA-03**: System fetches SEC filings (10-K, 10-Q) via edgartools
- [x] **DATA-04**: System fetches recent company news via yfinance Ticker.news
- [x] **DATA-05**: System fetches views and contrarian views from X/Twitter via xAI API

### Debate

- [ ] **DEBT-01**: Debate follows structured phases: opening statements, cross-examination, rebuttal, final verdict
- [ ] **DEBT-02**: Debate orchestrator manages turn order and phase transitions using TinyWorld broadcast+run pattern
- [ ] **DEBT-03**: All personas receive the same financial DataPackage as shared context at debate start

### Output

- [ ] **OUTP-01**: System extracts a buy/hold/sell vote from each participating persona via ResultsExtractor
- [ ] **OUTP-02**: System generates a scorecard displaying all persona votes with key reasoning

### UI

- [ ] **UI-01**: Streamlit app with ticker input field and company resolution
- [ ] **UI-02**: Real-time debate display showing each persona's statements via st.chat_message
- [ ] **UI-03**: Scorecard view displaying all votes and reasoning after debate completes
- [ ] **UI-04**: User can inject questions or steer the debate mid-session via chat input

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Persona Quality

- **PERS-08**: Automated persona differentiation test suite ensuring distinct analyses
- **PERS-09**: Contrastive prompting with anti-persona instructions to prevent convergence

### Debate Enhancements

- **DEBT-04**: Persona drift mitigation via system prompt re-injection each round
- **DEBT-05**: Structural dissent requiring at least 1-2 personas to argue the bear case
- **DEBT-06**: Fact-checking layer verifying numerical claims against source DataPackage

### Output Enhancements

- **OUTP-03**: Full investment memo (thesis, risks, valuation, verdict) via ResultsReporter
- **OUTP-04**: Cross-persona disagreement highlighting
- **OUTP-05**: Export scorecard and memo as Markdown/DOCX via ArtifactExporter

### UI Enhancements

- **UI-05**: Company data sidebar panel with key financials and price chart
- **UI-06**: Configurable debate parameters (number of rounds, temperature)
- **UI-07**: Download buttons for scorecard and memo

### Operational

- **OPS-01**: Per-debate cost tracking and display
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
| PERS-07 | Phase 4: Debate Engine and Verdict Extraction | Pending |
| DATA-01 | Phase 3: Financial Data Pipeline | Complete |
| DATA-02 | Phase 3: Financial Data Pipeline | Complete |
| DATA-03 | Phase 3: Financial Data Pipeline | Complete |
| DATA-04 | Phase 3: Financial Data Pipeline | Complete |
| DATA-05 | Phase 3: Financial Data Pipeline | Complete |
| DEBT-01 | Phase 4: Debate Engine and Verdict Extraction | Pending |
| DEBT-02 | Phase 4: Debate Engine and Verdict Extraction | Pending |
| DEBT-03 | Phase 4: Debate Engine and Verdict Extraction | Pending |
| OUTP-01 | Phase 4: Debate Engine and Verdict Extraction | Pending |
| OUTP-02 | Phase 4: Debate Engine and Verdict Extraction | Pending |
| UI-01 | Phase 5: Streamlit UI | Pending |
| UI-02 | Phase 5: Streamlit UI | Pending |
| UI-03 | Phase 5: Streamlit UI | Pending |
| UI-04 | Phase 5: Streamlit UI | Pending |

**Coverage:**
- v1 requirements: 25 total
- Mapped to phases: 25
- Unmapped: 0

---
*Requirements defined: 2026-03-20*
*Last updated: 2026-03-22 after Plan 03-02 completion (Phase 3 complete)*
