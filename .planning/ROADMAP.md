# Roadmap: openIC

**Created:** 2026-03-20
**Granularity:** Standard
**Phases:** 12 (v1: 6 complete, v1.1: 6 pending)
**Coverage:** 28/28 v1 requirements complete + 20 v1.1 requirements mapped

## Phases

### v1 (Complete)
- [x] **Phase 1: Foundation and TinyTroupe Integration** - Fork TinyTroupe, validate GPT-5.2 compatibility, establish project skeleton
- [x] **Phase 2: Persona Engineering** - Distill 6 investor philosophies into differentiated persona configs
- [x] **Phase 3: Financial Data Pipeline** - Build data fetching and normalization for company analysis
- [x] **Phase 4: Debate Engine and Verdict Extraction** - Orchestrate structured multi-agent debate and extract structured votes
- [x] **Phase 5: Streamlit UI** - Complete user interface from ticker input to debate display to scorecard
- [x] **Phase 6: Enhanced Ticker Resolution** - Support company name search, international exchanges, and robust resolver fallbacks

### v1.1 (Active)
- [x] **Phase 7: Release Hardening** - Migrate deprecated APIs, fix test flakiness, eliminate warnings, expose cost stats
- [x] **Phase 8: Debate Quality Controls** - Anti-convergence controls, rotating devil's advocate, differentiation regression tests
- [x] **Phase 9: Memo & Disagreement Engine** - Full narrative investment memo, cross-persona disagreement extraction, Markdown/DOCX export
- [ ] **Phase 10: UI Delivery** - Company data sidebar, memo/disagreement views, download buttons, per-debate cost display
- [x] **Phase 11: Model Selection** - Runtime LLM model selection via UI dropdown, config override per debate session
- [ ] **Phase 12: Deep Research Pipeline** - Web search + LLM synthesis to produce comprehensive research brief for all personas

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

### Phase 7: Release Hardening
**Goal**: Zero deprecation warnings, zero deselected tests, and a cost stats hook ready for UI consumption
**Depends on**: Nothing (first v1.1 phase, independent gate)
**Requirements**: HARD-01, HARD-02, HARD-03, HARD-04
**Success Criteria** (what must be TRUE):
  1. `uv run pytest tests/ -x -m "not live_api"` produces 0 deprecation warnings from edgartools or Pydantic in project-owned code
  2. edgartools usage in `src/tinyic/data/filings.py` uses `edgar.documents.HTMLParser` (or equivalent non-deprecated API) instead of `edgar.files.html` / `edgar.files.htmltools`
  3. All 7 previously-deselected `live_api` tests either pass when API keys are present, or are removed with a documented rationale in the test file
  4. `SimulationValidator` in TinyTroupe fork uses `model_config = ConfigDict(...)` instead of `class Config`
  5. A function `get_debate_cost_stats()` exists that returns token counts and estimated cost from `OpenAIClient`, callable after a debate completes
**Plans:** 2 plans

Plans:
- [x] 07-01-PLAN.md -- Zero deprecation warnings: edgartools HTMLParser migration + Pydantic ConfigDict fix + pytest warning filters
- [x] 07-02-PLAN.md -- Live API test stabilization + cost stats exposure via DebateResult.cost_stats and get_debate_cost_stats()

### Phase 8: Debate Quality Controls
**Goal**: Personas maintain distinct positions throughout multi-round debates, with at least one structurally dissenting voice during cross-examination
**Depends on**: Phase 7 (clean test baseline required)
**Requirements**: DEBT-04, DEBT-05, PERS-08
**Success Criteria** (what must be TRUE):
  1. Anti-convergence controls are active during debate: the orchestrator applies a mechanism (e.g., persona-specific reinforcement prompts, contrastive framing) that prevents opinions from collapsing to unanimous agreement
  2. During the cross-examination phase, one persona is assigned a rotating devil's advocate role that argues the strongest counter-position to the emerging consensus
  3. The devil's advocate assignment rotates and does not constrain final votes -- personas vote independently in the verdict phase
  4. An automated differentiation regression test exists that runs a debate on a fixed company fixture and asserts that persona analyses remain distinct (e.g., no two personas share >70% vocabulary overlap in key reasoning)
  5. The convergence detection test can be run as part of the standard `pytest` suite without API keys (using mocked responses or recorded fixtures)
**Plans:** 2 plans

Plans:
- [x] 08-01-PLAN.md -- Anti-convergence reinforcement prompts + rotating devil's advocate role injection in orchestrator._step()
- [x] 08-02-PLAN.md -- Differentiation regression tests with TF-IDF cosine similarity on recorded debate fixtures

### Phase 9: Memo & Disagreement Engine
**Goal**: The system produces a publishable investment memo and a structured disagreement analysis from every completed debate
**Depends on**: Phase 7 (hardening), Phase 8 (debate quality -- better debates produce better memos)
**Requirements**: OUTP-03, OUTP-04, OUTP-05
**Success Criteria** (what must be TRUE):
  1. After a debate completes, the system generates an `InvestmentMemo` with structured sections: Executive Summary, Investment Thesis, Key Risks, Valuation Discussion, Final Verdict -- each synthesized from debate transcript + scorecard + data package
  2. The memo includes section-level grounding: each section references which personas contributed the underlying arguments and which data points support the claims
  3. A `DisagreementAnalysis` object identifies the top 3 dimensions where personas diverged most (e.g., valuation methodology, risk assessment, growth outlook) with evidence quotes from the transcript
  4. Both memo and scorecard can be exported as Markdown files (guaranteed) and DOCX files (when pandoc is available, with graceful fallback to Markdown-only)
  5. Unit tests verify memo structure, disagreement extraction, and export formats without API calls
**Plans:** 2 plans

Plans:
- [x] 09-01-PLAN.md -- InvestmentMemo + DisagreementAnalysis models, MemoGenerator with LLM synthesis, and unit tests with mocked client
- [x] 09-02-PLAN.md -- ExportManager for Markdown/DOCX output with graceful pandoc fallback and export tests

### Phase 10: UI Delivery
**Goal**: Users see company data alongside the debate, can download all outputs, and see what the debate cost
**Depends on**: Phase 7 (cost stats hook), Phase 9 (memo and export artifacts)
**Requirements**: UI-05, UI-07, OPS-01
**Success Criteria** (what must be TRUE):
  1. A sidebar panel shows key financials (P/E, market cap, revenue, margins), a price chart, data freshness timestamp, and any data source warnings -- visible before and during the debate
  2. After debate completes, download buttons are available for: investment memo (Markdown/DOCX), scorecard (Markdown), and full transcript (Markdown)
  3. Per-debate token usage (input/output tokens) and estimated cost are displayed in the UI after the debate completes
  4. The data sidebar updates immediately after data fetching (before debate rounds begin), not after the debate ends
  5. All new UI components work correctly with the existing debate flow (real-time display, steering, phase pauses)
**Plans:** 2 plans

Plans:
- [x] 10-01-PLAN.md -- Company data sidebar (financials, price chart, warnings) and per-debate cost display (token usage, estimated USD)
- [ ] 10-02-PLAN.md -- Memo/disagreement views, download buttons, post-debate tabs

### Phase 11: Model Selection
**Goal**: Users can choose which LLM model powers the debate, with the selection applied at runtime without editing config files
**Depends on**: Phase 7 (clean baseline)
**Requirements**: CONFIG-01, CONFIG-02, CONFIG-03
**Success Criteria** (what must be TRUE):
  1. The Streamlit sidebar contains a model dropdown populated from a configurable model list (default includes GPT-5.2 and Codex 5.3)
  2. Selecting a model overrides the `config.ini` MODEL setting at runtime for that debate session -- all `TinyPerson.act()` calls during the debate use the selected model
  3. Each model option displays a brief description of its strengths to help users choose (e.g., reasoning depth, speed, cost)
  4. The default selection matches the current `config.ini` MODEL value so existing behavior is preserved when no change is made
  5. Model selection is locked during an active debate (consistent with existing sidebar lock behavior) and only takes effect on the next debate
**Plans:** 1 plan

Plans:
- [x] 11-01-PLAN.md -- Model dropdown in sidebar with runtime config override and tests

### Phase 12: Deep Research Pipeline
**Goal**: Every debate is informed by a comprehensive, LLM-synthesized research brief that covers the target company's business, competition, industry, management, and analyst perspectives -- not just raw financial data
**Depends on**: Phase 7 (clean baseline), Phase 11 (model selection -- research uses the selected model)
**Requirements**: DATA-06, DATA-07, DATA-08, DATA-09
**Success Criteria** (what must be TRUE):
  1. `build_data_package()` includes an optional deep research step that uses web search APIs (e.g., Tavily, Brave Search, or xAI's web search) + LLM synthesis to produce a structured `ResearchBrief`
  2. The `ResearchBrief` covers at minimum: business model and competitive moat analysis, industry trends and macro tailwinds/headwinds, management track record and capital allocation, recent catalysts and developments (last 6 months), and bull/bear investment cases from public analyst perspectives
  3. The `ResearchBrief` is stored as a new field on `DataPackage` and included in `to_context_string()` so all personas receive the enriched fact base at debate start
  4. The research step is toggleable via a UI checkbox or parameter (default: enabled) -- when disabled, the pipeline behaves exactly as in v1 with no performance penalty
  5. When web search or LLM synthesis fails, the system logs a warning and continues with existing data sources (graceful degradation, consistent with the pipeline's existing pattern)
  6. Unit tests verify `ResearchBrief` model, pipeline integration, toggle behavior, and failure handling without requiring API keys
**Plans:** 2 plans

Plans:
- [x] 12-01-PLAN.md -- ResearchBrief model, web search + LLM synthesis pipeline, pipeline integration with deep_research toggle
- [ ] 12-02-PLAN.md -- UI checkbox for deep research toggle, research brief display in data sidebar

## Progress

### v1

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation and TinyTroupe Integration | 2/2 | Complete | 2026-03-20 |
| 2. Persona Engineering | 2/2 | Complete | 2026-03-22 |
| 3. Financial Data Pipeline | 2/2 | Complete | 2026-03-22 |
| 4. Debate Engine and Verdict Extraction | 2/2 | Complete | 2026-03-22 |
| 5. Streamlit UI | 2/2 | Complete | 2026-03-22 |
| 6. Enhanced Ticker Resolution | 1/1 | Complete | 2026-03-22 |

### v1.1

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 7. Release Hardening | 2/2 | Complete | 2026-03-23 |
| 8. Debate Quality Controls | 2/2 | Complete | 2026-03-23 |
| 9. Memo & Disagreement Engine | 2/2 | Complete | 2026-03-23 |
| 10. UI Delivery | 1/2 | In Progress | — |
| 11. Model Selection | 1/1 | Complete | 2026-03-25 |
| 12. Deep Research Pipeline | 1/2 | In Progress | — |

## Dependency Graph

### v1 (Complete)
```
Phase 1: Foundation
   |
   +---> Phase 2: Persona Engineering ---+
   |                                      |
   +---> Phase 3: Financial Data Pipeline +---> Phase 4: Debate Engine ---> Phase 5: UI ---> Phase 6: Enhanced Resolver
```

### v1.1
```
Phase 7: Release Hardening (gate)
   |
   +---> Phase 8: Debate Quality Controls ---> Phase 9: Memo & Disagreement Engine ---> Phase 10: UI Delivery
   |                                                                                        ^
   +---> Phase 11: Model Selection (parallel) --------------------------------------------------+
   |                                                                                        |
   +----------------------------------------------------------------------------------------+
                                          (Phase 7 cost stats hook feeds Phase 10)
```

Phase 7 is a hard gate — all v1.1 feature phases depend on it.
Phase 8 depends on Phase 7 only.
Phase 9 depends on Phase 8 (better debate quality → better memo source material).
Phase 10 depends on Phase 9 (needs memo/export artifacts) and Phase 7 (needs cost stats).
Phase 11 depends on Phase 7 only — can run in parallel with Phases 8-9.
Phase 12 depends on Phase 7 + Phase 11 (uses selected model for synthesis) — can run in parallel with Phase 8-9.

## Coverage Map

```
# v1 (28/28 complete)
FOUND-01   -> Phase 1  [complete]
FOUND-02   -> Phase 1  [complete]
FOUND-03   -> Phase 1  [complete]
FOUND-04   -> Phase 1  [complete]
PERS-01    -> Phase 2  [complete]
PERS-02    -> Phase 2  [complete]
PERS-03    -> Phase 2  [complete]
PERS-04    -> Phase 2  [complete]
PERS-05    -> Phase 2  [complete]
PERS-06    -> Phase 2  [complete]
PERS-07    -> Phase 4  [complete]
DATA-01    -> Phase 3  [complete]
DATA-02    -> Phase 3  [complete]
DATA-03    -> Phase 3  [complete]
DATA-04    -> Phase 3  [complete]
DATA-05    -> Phase 3  [complete]
DEBT-01    -> Phase 4  [complete]
DEBT-02    -> Phase 4  [complete]
DEBT-03    -> Phase 4  [complete]
OUTP-01    -> Phase 4  [complete]
OUTP-02    -> Phase 4  [complete]
UI-01      -> Phase 5  [complete]
UI-02      -> Phase 5  [complete]
UI-03      -> Phase 5  [complete]
UI-04      -> Phase 5  [complete]
RESOLVE-01 -> Phase 6  [complete]
RESOLVE-02 -> Phase 6  [complete]
RESOLVE-03 -> Phase 6  [complete]

# v1.1 (20/20 mapped, 4 complete)
HARD-01  -> Phase 7   [complete]
HARD-02  -> Phase 7   [complete]
HARD-03  -> Phase 7   [complete]
HARD-04  -> Phase 7   [complete]
DEBT-04  -> Phase 8   [complete]
DEBT-05  -> Phase 8   [complete]
PERS-08  -> Phase 8   [complete]
OUTP-03  -> Phase 9   [complete]
OUTP-04  -> Phase 9   [complete]
OUTP-05  -> Phase 9   [complete]
UI-05    -> Phase 10  [complete]
UI-07    -> Phase 10  [pending]
OPS-01    -> Phase 10  [complete]
CONFIG-01 -> Phase 11  [complete]
CONFIG-02 -> Phase 11  [complete]
CONFIG-03 -> Phase 11  [complete]
DATA-06   -> Phase 12  [pending]
DATA-07   -> Phase 12  [pending]
DATA-08   -> Phase 12  [pending]
DATA-09   -> Phase 12  [pending]

Mapped: 48/48
Orphaned: 0
```

---
*Roadmap created: 2026-03-20*
*Last updated: 2026-03-25 -- Phase 12 Plan 1 complete (ResearchBrief model + web search + LLM synthesis pipeline)*
