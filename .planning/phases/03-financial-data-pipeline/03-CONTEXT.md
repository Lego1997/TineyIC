# Phase 3 Context: Financial Data Pipeline

## Phase Goal
The system can take a stock ticker and produce a complete, normalized data package ready for persona consumption.

## Requirements
- DATA-01: User enters a stock ticker and system resolves it to correct company name and confirms validity
- DATA-02: System fetches financial fundamentals (income statement, balance sheet, key ratios) via yfinance
- DATA-03: System fetches SEC filings (10-K summary, 10-Q summary) via edgartools
- DATA-04: System fetches recent company news via yfinance Ticker.news
- DATA-05: System fetches views and contrarian views from X/Twitter via xAI API

## Success Criteria
1. User enters a valid US stock ticker (e.g., "AAPL") and the system resolves it to the correct company name and confirms validity
2. The system fetches and returns financial fundamentals (income statement, balance sheet, key ratios), SEC filings (10-K summary, 10-Q summary), and recent news -- all from free APIs (yfinance, edgartools)
3. The system fetches recent X/Twitter posts and sentiment about the company via the xAI API
4. All fetched data is bundled into a single DataPackage object that can be serialized and injected into persona context
5. When a data source fails or returns incomplete data, the system logs a warning and continues with available data rather than crashing

## Key Constraints
- Free APIs only: yfinance, edgartools (no API key), xAI API (needs XAI_API_KEY)
- DataPackage must be under ~3000 tokens (~12000 chars) to fit in persona context alongside system prompts (research validated 12K cap is realistic for full data package with truncated filings)
- Pydantic BaseModel for DataPackage (already in dependency tree via TinyTroupe)
- Independent fetcher functions returning Optional -- graceful degradation, never crash on single source failure
- edgartools requires set_identity() before SEC calls
- xAI API is OpenAI SDK-compatible via base_url override (no new SDK needed)
- Filing text must be truncated: 10-K to ~3000 chars (key sections only), 10-Q to ~2000 chars
- News limited to 5-8 headlines
- New dependencies needed: yfinance, edgartools (via `uv add --package tinyic`)

## Technical Foundation (from Phase 1-2)
- Project structure: src/tinyic/data/ module exists with empty __init__.py
- Package config: src/tinyic/pyproject.toml lists tinyic.data as a setuptools package
- InvestorPersona.analyze_company() accepts a data_package dict (src/tinyic/personas/base.py)
- Test infrastructure: tests/conftest.py with has_api_key fixture, pytest configured in root pyproject.toml
- OpenAI SDK already installed (>=1.65), pydantic already in dependency tree
- Proxy gateway requires stream=True (for OpenAI calls, not relevant for yfinance/edgartools)

## Architecture (from Research)
```
src/tinyic/data/
  __init__.py           # Public API: build_data_package(), resolve_ticker()
  models.py             # DataPackage, FinancialData, FilingSummary, NewsSummary, SocialSentiment
  ticker_resolver.py    # Ticker validation and company name resolution (DATA-01)
  financials.py         # yfinance fundamentals fetcher (DATA-02)
  filings.py            # edgartools SEC filings fetcher (DATA-03)
  news.py               # yfinance news fetcher (DATA-04)
  social.py             # xAI API X/Twitter sentiment fetcher (DATA-05)
  pipeline.py           # Orchestrator: build_data_package() assembles all sources
```
