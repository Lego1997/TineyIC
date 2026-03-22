# Phase 6 Context: Enhanced Ticker Resolution

## Phase Goal
Users can enter a company name (e.g., "Apple", "Tencent") or a ticker from any major exchange (NYSE, NASDAQ, HKEX, TSE, Frankfurt, Euronext, LSE, etc.) and get correct resolution.

## Requirements
- RESOLVE-01: Entering a company name (e.g., "Apple", "Toyota", "Tencent") resolves to the correct ticker and company name via yfinance Search
- RESOLVE-02: International tickers work (e.g., 0700.HK, 7203.T, SAP.DE, MC.PA) -- the resolver accepts exchange-suffixed symbols
- RESOLVE-03: The resolver is robust against yfinance `.info` failures -- uses multiple fallback strategies (fast_info, Search API) before returning invalid

## Success Criteria
1. Entering a company name (e.g., "Apple", "Toyota", "Tencent") resolves to the correct ticker and company name via yfinance Search
2. International tickers work (e.g., 0700.HK, 7203.T, SAP.DE, MC.PA) -- the resolver accepts exchange-suffixed symbols
3. The resolver is robust against yfinance `.info` failures -- uses multiple fallback strategies (fast_info, Search API) before returning invalid

## Key Constraints
- Must remain backward-compatible with existing `build_data_package()` and `_resolve_ticker()` callers
- Return type changes from `(bool, str)` to `(bool, str, str)` -- `(is_valid, ticker_symbol, company_name)`
- yfinance lazy import pattern must be preserved (import inside function body)
- Free APIs only -- no paid ticker resolution services
- UI must stop auto-uppercasing input (breaks company names and some international formats)

## Technical Foundation (from Phases 3, 5)
- `resolve_ticker(ticker)` -> `(bool, str)` in `src/tinyic/data/ticker_resolver.py` -- single yfinance.Ticker().info lookup
- `build_data_package(ticker)` in `src/tinyic/data/pipeline.py` -- calls resolve_ticker(), raises ValueError if invalid
- `_resolve_ticker(ticker)` in `src/tinyic/ui/app.py` -- UI helper that calls resolve_ticker(), updates session_state
- UI auto-uppercases and strips input before passing to resolver (line 132-133 of app.py)
- 5 existing resolver tests in `tests/test_data_pipeline.py` (TestResolveTicker class)

## Known Context (from STATE.md)
- `yfinance.Search("Apple")` works and returns AAPL (tested by user)
- `yfinance.Ticker("0700.HK").info` works for international tickers (tested by user)
- `yfinance.Ticker().fast_info` exists as a lightweight alternative to `.info`
- The "always invalid" bug in Streamlit may be a runtime issue with the current resolver

<decisions>
## Implementation Decisions

### 1. Input Classification Strategy

**Heuristic-based classification: ticker vs company name.**

- If input looks like a ticker (all uppercase after strip, or contains `.` exchange suffix like `.HK`), try direct ticker lookup first.
- If input looks like a company name (contains lowercase letters, spaces, or is longer than 6 chars), try name search first.
- Both paths fall through to the other strategy if the primary fails, so the classification doesn't need to be perfect.

### 2. Resolver Fallback Chain

**4-layer fallback for direct ticker lookup:**

1. `yfinance.Ticker(symbol).info` -- current approach, gets longName/shortName
2. `yfinance.Ticker(symbol).fast_info` -- lightweight, gets basic validity
3. `yfinance.Search(symbol)` -- search API, handles partial/misspelled tickers
4. Return invalid

**For company name input:**
1. `yfinance.Search(name)` -- returns list of matches with ticker symbols
2. Take first result, validate with `yfinance.Ticker(result).info` or `fast_info`
3. Return invalid if no results

### 3. Return Type Change

**Expand to 3-tuple: `(is_valid, ticker_symbol, company_name)`.**

- The ticker symbol is needed because the input might be a company name -- callers need to know the resolved symbol.
- All callers (`build_data_package`, `_resolve_ticker` in UI) must be updated.
- Backward-compatible pattern: add `ticker_symbol` as middle element.

### 4. UI Input Handling

**Remove auto-uppercase. Update placeholder.**

- Stop calling `.upper().strip()` on input in `render_sidebar()`.
- Let the resolver handle normalization internally.
- Change placeholder to: `"e.g., AAPL, Apple, 0700.HK"`
- Change label from "Stock Ticker" to "Company or Ticker"
- Display resolved ticker alongside company name: `"Apple Inc. (AAPL)"`

### 5. International Ticker Normalization

**Accept exchange suffixes as-is, normalize only the base.**

- Input "0700.hk" normalizes to "0700.HK" (uppercase the suffix).
- Input "sap.de" normalizes to "SAP.DE".
- The base part before the dot gets uppercased for letter-based tickers.
- Numeric bases (like "0700") are left as-is.

</decisions>

<code_context>
## Existing Code Insights

### Current Resolver (src/tinyic/data/ticker_resolver.py)
```python
def resolve_ticker(ticker: str) -> tuple[bool, str]:
    t = yf.Ticker(ticker.upper().strip())
    info = t.info or {}
    name = info.get("longName") or info.get("shortName") or ""
    if not name:
        return (False, "")
    return (True, name)
```

### Callers to Update
1. `build_data_package()` (pipeline.py:47): `is_valid, company_name = resolve_ticker(ticker)`
2. `_resolve_ticker()` (app.py:186): `is_valid, name = resolve_ticker(ticker)`

### Test Patterns (test_data_pipeline.py)
- `@patch("yfinance.Ticker")` for mocking
- MagicMock instances with `.info` dict
- Tests: valid, invalid, exception, normalizes_input, shortname_fallback

### yfinance API Surface
- `yfinance.Ticker(symbol).info` -> dict with longName, shortName, etc.
- `yfinance.Ticker(symbol).fast_info` -> FastInfo object with basic data
- `yfinance.Search(query)` -> SearchResult with .quotes list of dicts
  - Each quote has: symbol, shortname, longname, exchange, quoteType, etc.

</code_context>

<deferred>
## Deferred Ideas

None -- all discussion stayed within phase scope.

</deferred>

---

*Phase: 06-enhanced-ticker-resolution*
*Context gathered: 2026-03-22*
