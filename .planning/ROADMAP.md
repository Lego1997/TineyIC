# Roadmap: openIC

**Created:** 2026-03-20
**Granularity:** Standard
**Phases:** 6
**Coverage:** 25/25 v1 requirements mapped + 3 v1.1 enhancements

## Phases

- [x] **Phase 1: Foundation and TinyTroupe Integration** - Fork TinyTroupe, validate GPT-5.2 compatibility, establish project skeleton
- [x] **Phase 2: Persona Engineering** - Distill 6 investor philosophies into differentiated persona configs
- [x] **Phase 3: Financial Data Pipeline** - Build data fetching and normalization for company analysis
- [x] **Phase 4: Debate Engine and Verdict Extraction** - Orchestrate structured multi-agent debate and extract structured votes
- [x] **Phase 5: Streamlit UI** - Complete user interface from ticker input to debate display to scorecard
- [x] **Phase 6: Enhanced Ticker Resolution** - Support company name search, international exchanges, and robust resolver fallbacks

## Phase Details

### Phase 1: Foundation and TinyTroupe Integration
**Goal**: A working project skeleton where a TinyPerson subclass can call GPT-5.2 through the forked TinyTroupe framework
**Depends on**: Nothing (first phase)
**Requirements**: FOUND-01, FOUND-02, FOUND-03, FOUND-04
**Success Criteria** (what must be TRUE):
  1. Running `uv run python -c "from tinyic.personas.base import InvestorPersona"` succeeds -- the project structure, dependencies, and TinyTroupe fork are importable
  2. A minimal InvestorPersona (TinyPerson subclass) can receive a `listen()` message and produce an `act()` response using GPT-5.2 with reasoning_effort=xhigh
  3. The TinyTroupe fork works with openai SDK v2.x without errors -- if patching was needed, the patches are isolated and documented
  4. The project runs with `uv` and Python 3.12, with a lockfile and proper dependency resolution
**Plans:** 2 plans

Plans:
- [x] 01-01-PLAN.md -- Scaffold uv workspace, clone TinyTroupe v0.6.0 fork, create InvestorPersona base class and test infrastructure
- [x] 01-02-PLAN.md -- Validate GPT-5.2 compatibility with live API smoke test, patch openai_client.py if needed

### Phase 2: Persona Engineering
**Goal**: Six investor personas that produce demonstrably different analyses of the same company -- each grounded in their real-world investment philosophy
**Depends on**: Phase 1 (need working TinyPerson subclass and GPT-5.2 integration)
**Requirements**: PERS-01, PERS-02, PERS-03, PERS-04, PERS-05, PERS-06
**Success Criteria** (what must be TRUE):
  1. Given the same company data, each of the 6 personas produces analysis that references their specific investment framework (e.g., Buffett discusses moats and owner earnings, Graham discusses margin of safety and net-net value, Marks discusses where we are in the cycle)
  2. A human reader can identify which persona wrote a given analysis without seeing the name attached -- the voice, vocabulary, and reasoning style are distinct
  3. Each persona's philosophy is stored as a JSON config file following TinyTroupe's `.agent.json` pattern, not hardcoded in Python
  4. Each persona config includes attributable source references (specific books, letters, speeches) for the philosophy distilled into the prompt
**Plans:** 2 plans

Plans:
- [x] 02-01-PLAN.md -- Create classic-value persona cluster (Graham, Buffett, Munger) with registry module and unit tests
- [x] 02-02-PLAN.md -- Create modern/diverse persona cluster (Lynch, Marks, Li Lu) with analyze_company/format_vote implementation and live API differentiation validation

### Phase 3: Financial Data Pipeline
**Goal**: The system can take a stock ticker and produce a complete, normalized data package ready for persona consumption
**Depends on**: Phase 1 (need project skeleton and package management)
**Requirements**: DATA-01, DATA-02, DATA-03, DATA-04, DATA-05
**Success Criteria** (what must be TRUE):
  1. User enters a valid US stock ticker (e.g., "AAPL") and the system resolves it to the correct company name and confirms validity
  2. The system fetches and returns financial fundamentals (income statement, balance sheet, key ratios), SEC filings (10-K summary, 10-Q summary), and recent news -- all from free APIs (yfinance, edgartools)
  3. The system fetches recent X/Twitter posts and sentiment about the company via the xAI API
  4. All fetched data is bundled into a single DataPackage object that can be serialized and injected into persona context
  5. When a data source fails or returns incomplete data, the system logs a warning and continues with available data rather than crashing
**Plans:** 2 plans

Plans:
- [x] 03-01-PLAN.md -- Pydantic data models, ticker resolver, yfinance fetchers (financials + news), and unit tests with mocked yfinance
- [x] 03-02-PLAN.md -- edgartools filings fetcher, xAI social sentiment fetcher, pipeline orchestrator (build_data_package), and live API integration test

### Phase 4: Debate Engine and Verdict Extraction
**Goal**: Personas can conduct a structured investment debate about a company and produce individual buy/hold/sell votes with reasoning
**Depends on**: Phase 2 (personas), Phase 3 (data pipeline)
**Requirements**: DEBT-01, DEBT-02, DEBT-03, PERS-07, OUTP-01, OUTP-02
**Success Criteria** (what must be TRUE):
  1. A debate runs through all structured phases -- opening statements, cross-examination, rebuttal, final verdict -- with the orchestrator managing turn order and transitions
  2. All personas receive the same DataPackage as shared context at debate start, and their statements reference this data
  3. The user can select a subset of the 6 personas to participate in a given debate (minimum 2)
  4. After the debate completes, the system extracts a structured buy/hold/sell vote from each persona via ResultsExtractor, with key reasoning attached
  5. A scorecard is generated showing all persona votes and their core reasoning in a structured comparison format
**Plans:** 2 plans

Plans:
- [x] 04-01-PLAN.md -- Data models (DebatePhase, Vote, Scorecard), prompt templates, DebateOrchestrator (TinyWorld subclass), and unit tests with mocked agents
- [x] 04-02-PLAN.md -- Vote extraction via ResultsExtractor, scorecard builder, run_debate() convenience function, and live API integration test

### Phase 5: Streamlit UI
**Goal**: A complete user journey from entering a ticker to watching a live debate to reviewing the final scorecard
**Depends on**: Phase 4 (debate engine and extraction must work end-to-end)
**Requirements**: UI-01, UI-02, UI-03, UI-04
**Success Criteria** (what must be TRUE):
  1. User can enter a stock ticker in the Streamlit app and see the resolved company name before the debate begins
  2. The debate displays in real-time using chat message components, with each persona's statements appearing as they are generated
  3. After the debate completes, the scorecard view shows all persona votes and reasoning in a clear visual layout
  4. User can type a question or steering prompt during an active debate, and it is injected into the next round of discussion
**Plans:** 2 plans

Plans:
- [x] 05-01-PLAN.md -- Orchestrator streaming callbacks, Streamlit app with ticker input, persona selection, real-time debate display, and scorecard view
- [x] 05-02-PLAN.md -- Mid-debate user steering via chat input with @mention targeting, inter-phase pauses, and failure handling

### Phase 6: Enhanced Ticker Resolution
**Goal**: Users can enter a company name (e.g., "Apple", "Tencent") or a ticker from any major exchange (NYSE, NASDAQ, HKEX, TSE, Frankfurt, Euronext, LSE, etc.) and get correct resolution
**Depends on**: Phase 3 (ticker_resolver.py), Phase 5 (UI input handling)
**Requirements**: RESOLVE-01, RESOLVE-02, RESOLVE-03
**Success Criteria** (what must be TRUE):
  1. Entering a company name (e.g., "Apple", "Toyota", "Tencent") resolves to the correct ticker and company name via yfinance Search
  2. International tickers work (e.g., 0700.HK, 7203.T, SAP.DE, MC.PA) -- the resolver accepts exchange-suffixed symbols
  3. The resolver is robust against yfinance `.info` failures -- uses multiple fallback strategies (fast_info, Search API) before returning invalid
**Plans:** TBD

Plans:
- [x] 06-01-PLAN.md -- Enhanced ticker resolver with name search, international support, and robust fallbacks; updated UI input handling and tests

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation and TinyTroupe Integration | 2/2 | Complete | 2026-03-20 |
| 2. Persona Engineering | 2/2 | Complete | 2026-03-22 |
| 3. Financial Data Pipeline | 2/2 | Complete | 2026-03-22 |
| 4. Debate Engine and Verdict Extraction | 2/2 | Complete | 2026-03-22 |
| 5. Streamlit UI | 2/2 | Complete | 2026-03-22 |
| 6. Enhanced Ticker Resolution | 1/1 | Complete | 2026-03-22 |

## Dependency Graph

```
Phase 1: Foundation
   |
   +---> Phase 2: Persona Engineering ---+
   |                                      |
   +---> Phase 3: Financial Data Pipeline +---> Phase 4: Debate Engine ---> Phase 5: UI ---> Phase 6: Enhanced Resolver
```

Phases 2 and 3 can execute in parallel after Phase 1 completes.

## Coverage Map

```
FOUND-01 -> Phase 1
FOUND-02 -> Phase 1
FOUND-03 -> Phase 1
FOUND-04 -> Phase 1
PERS-01  -> Phase 2
PERS-02  -> Phase 2
PERS-03  -> Phase 2
PERS-04  -> Phase 2
PERS-05  -> Phase 2
PERS-06  -> Phase 2
PERS-07  -> Phase 4
DATA-01  -> Phase 3
DATA-02  -> Phase 3
DATA-03  -> Phase 3
DATA-04  -> Phase 3
DATA-05  -> Phase 3
DEBT-01  -> Phase 4
DEBT-02  -> Phase 4
DEBT-03  -> Phase 4
OUTP-01  -> Phase 4
OUTP-02  -> Phase 4
UI-01    -> Phase 5
UI-02    -> Phase 5
UI-03    -> Phase 5
UI-04    -> Phase 5
RESOLVE-01 -> Phase 6
RESOLVE-02 -> Phase 6
RESOLVE-03 -> Phase 6

Mapped: 28/28
Orphaned: 0
```

---
*Roadmap created: 2026-03-20*
*Last updated: 2026-03-22 (Phase 6 complete -- enhanced ticker resolution)*
