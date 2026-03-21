# Phase 3: Financial Data Pipeline - Validation

**Phase:** 03-financial-data-pipeline
**Created:** 2026-03-21

## Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (already configured) |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_data_pipeline.py -x -k "not live_api"` |
| Full suite command | `uv run pytest tests/ -x` |

## Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | Plan |
|--------|----------|-----------|-------------------|------|
| DATA-01 | resolve_ticker returns (True, name) for valid ticker, (False, "") for invalid | unit (mock yfinance) | `uv run pytest tests/test_data_pipeline.py -x -k "test_resolve_ticker"` | 03-01 |
| DATA-02 | fetch_financials returns FinancialData with key ratios and statement summaries | unit (mock yfinance) | `uv run pytest tests/test_data_pipeline.py -x -k "test_fetch_financials"` | 03-01 |
| DATA-03 | fetch_filings returns FilingSummary with truncated text | unit (mock edgartools) | `uv run pytest tests/test_data_pipeline.py -x -k "test_fetch_filings"` | 03-02 |
| DATA-04 | fetch_news returns NewsSummary with article dicts | unit (mock yfinance) | `uv run pytest tests/test_data_pipeline.py -x -k "test_fetch_news"` | 03-01 |
| DATA-05 | fetch_social_sentiment returns SocialSentiment or None if no key | unit (mock openai) | `uv run pytest tests/test_data_pipeline.py -x -k "test_fetch_social"` | 03-02 |
| DATA-ALL | build_data_package assembles partial results with warnings | unit (mocked sources) | `uv run pytest tests/test_data_pipeline.py -x -k "test_build_data_package"` | 03-02 |
| DATA-ALL | DataPackage.to_context_string() under token budget | unit | `uv run pytest tests/test_data_pipeline.py -x -k "test_context_string"` | 03-01 |
| INTEGRATION | Full pipeline with live APIs for AAPL | integration (live_api marker) | `uv run pytest tests/test_data_pipeline.py -m live_api -x` | 03-02 |

## Sampling Rate
- **Per task commit:** `uv run pytest tests/test_data_pipeline.py -x -k "not live_api"`
- **Per wave merge:** `uv run pytest tests/ -x -k "not live_api"`
- **Phase gate:** Full suite green before verification

## Success Criteria Traceability

| Success Criteria | Tests | Requirement |
|-----------------|-------|-------------|
| SC1: Valid ticker resolves to company name | test_resolve_ticker_valid, test_resolve_ticker_invalid | DATA-01 |
| SC2: Fetches fundamentals, filings, news from free APIs | test_fetch_financials_valid, test_fetch_filings_10k_valid, test_fetch_news_valid | DATA-02, DATA-03, DATA-04 |
| SC3: Fetches X/Twitter sentiment via xAI | test_fetch_social_sentiment_valid | DATA-05 |
| SC4: All data in single DataPackage, serializable | test_build_data_package_valid, test_data_package_serialization | DATA-ALL |
| SC5: Graceful degradation on source failure | test_build_data_package_partial_failure, test_build_data_package_all_sources_fail | DATA-ALL |
