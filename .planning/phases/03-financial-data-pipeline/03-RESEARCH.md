# Phase 3: Financial Data Pipeline - Research

**Researched:** 2026-03-21
**Domain:** Financial data fetching (yfinance, edgartools, xAI API), data normalization, DataPackage design
**Confidence:** MEDIUM-HIGH

## Summary

Phase 3 requires building a pipeline that takes a stock ticker and produces a normalized DataPackage containing financial fundamentals, SEC filings, recent news, and X/Twitter sentiment. The pipeline must use three data sources: **yfinance** (financials + news), **edgartools** (SEC 10-K/10-Q filings), and the **xAI API** (X/Twitter sentiment via x_search tool). All three are free for v1 usage.

The primary design challenge is graceful degradation -- each data source can fail independently (invalid ticker, network issues, missing filings, API key not set), and the pipeline must assemble a partial DataPackage rather than crashing. The secondary challenge is token budget management: the DataPackage must be small enough to inject into GPT-5.2's context window alongside persona system prompts, meaning raw financial data must be summarized/truncated.

**Primary recommendation:** Use a Pydantic BaseModel for the DataPackage (serialization, validation, and schema generation built-in; pydantic already in the dependency tree via TinyTroupe). Build each data source as an independent fetcher function that returns Optional data, with a top-level `build_data_package(ticker)` orchestrator that assembles results.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DATA-01 | User enters a stock ticker and system resolves it to correct company name and confirms validity | yfinance Ticker.info provides shortName/longName; validate by checking info dict is non-empty |
| DATA-02 | System fetches financial fundamentals (income statement, balance sheet, key ratios) via yfinance | yfinance Ticker.income_stmt, balance_sheet, cashflow return DataFrames; .info contains key ratios |
| DATA-03 | System fetches SEC filings (10-K summary, 10-Q summary) via edgartools | edgartools Company.get_filings(form="10-K"/"10-Q"), filing.text()/markdown() for content |
| DATA-04 | System fetches recent company news via yfinance Ticker.news | Ticker.news / get_news(count=10) returns list of dicts with title, link, publisher |
| DATA-05 | System fetches views and contrarian views from X/Twitter via xAI API | xAI Responses API with x_search tool via OpenAI SDK compatibility layer |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| yfinance | 1.2.0 | Financial fundamentals, company info, news | De facto standard for free Yahoo Finance data in Python; latest release Feb 2026 |
| edgartools | 5.25.x | SEC EDGAR filings (10-K, 10-Q) | Best Python library for structured SEC data; free, no API key; v5 released Dec 2025 with major improvements |
| openai | >=1.65 (already installed) | xAI API client via base_url override | Already in project; xAI API is OpenAI SDK-compatible, no need for separate xai-sdk |
| pydantic | >=2.5.0 (already installed) | DataPackage model with validation and JSON serialization | Already a dependency via TinyTroupe; provides model_dump_json() for serialization |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pandas | (already installed) | DataFrame handling from yfinance | Financial statement data comes as DataFrames; convert to dicts for DataPackage |
| logging | stdlib | Warning/error logging for partial failures | Every fetcher logs warnings on failure instead of raising |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| openai SDK for xAI | xai-sdk (pip install xai-sdk) | xai-sdk is gRPC-based, more complex; OpenAI SDK already in project and xAI explicitly supports it via base_url |
| pydantic BaseModel | stdlib @dataclass + dataclasses.asdict | Loses validation, no model_dump_json(), would need custom serializer |
| edgartools | sec-edgar-api, sec-edgar-downloader | edgartools has structured TenK/TenQ objects with .text()/.markdown(); others return raw HTML |

**Installation:**
```bash
uv add yfinance edgartools --package tinyic
```

No need to add openai or pydantic -- already present in dependency tree.

## Architecture Patterns

### Recommended Project Structure
```
src/tinyic/data/
  __init__.py           # Public API: build_data_package(), resolve_ticker()
  models.py             # DataPackage, FinancialData, FilingSummary, NewsSummary, SocialSentiment Pydantic models
  ticker_resolver.py    # Ticker validation and company name resolution (DATA-01)
  financials.py         # yfinance fundamentals fetcher (DATA-02)
  filings.py            # edgartools SEC filings fetcher (DATA-03)
  news.py               # yfinance news fetcher (DATA-04)
  social.py             # xAI API X/Twitter sentiment fetcher (DATA-05)
  pipeline.py           # Orchestrator: build_data_package() assembles all sources
```

### Pattern 1: Independent Fetcher Functions with Optional Returns

**What:** Each data source is a standalone function that returns `Optional[DataModel]`. On failure, it logs a warning and returns `None`. The orchestrator calls all fetchers and assembles a DataPackage with whatever data is available.

**When to use:** Always -- this is the core pattern for graceful degradation (DATA-05 success criteria: "logs a warning and continues with available data").

**Example:**
```python
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def fetch_financials(ticker: str) -> Optional[FinancialData]:
    """Fetch financial fundamentals via yfinance. Returns None on failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        income = t.income_stmt
        if income is None or income.empty:
            logger.warning("No income statement data for %s", ticker)
            return None
        # ... process and return FinancialData
    except Exception as e:
        logger.warning("Failed to fetch financials for %s: %s", ticker, e)
        return None
```

### Pattern 2: Pydantic Models for DataPackage

**What:** Use Pydantic BaseModel for the DataPackage and all sub-models. Provides `.model_dump_json()` for serialization and `.model_dump()` for dict conversion. Inject into persona context as JSON string or dict.

**When to use:** Always -- this is the canonical pattern for data that needs validation and serialization.

**Example:**
```python
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class FinancialData(BaseModel):
    """Summarized financial fundamentals."""
    revenue_ttm: Optional[float] = None
    net_income_ttm: Optional[float] = None
    total_assets: Optional[float] = None
    total_debt: Optional[float] = None
    free_cash_flow: Optional[float] = None
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    profit_margin: Optional[float] = None
    roe: Optional[float] = None
    debt_to_equity: Optional[float] = None
    # Key income statement rows (most recent year)
    income_summary: Optional[dict] = None
    # Key balance sheet rows (most recent year)
    balance_summary: Optional[dict] = None

class FilingSummary(BaseModel):
    """Summarized SEC filing."""
    form_type: str  # "10-K" or "10-Q"
    filing_date: Optional[str] = None
    period_end: Optional[str] = None
    text_summary: Optional[str] = None  # Truncated text, max ~2000 chars

class NewsSummary(BaseModel):
    """Recent news articles."""
    articles: list[dict] = Field(default_factory=list)
    # Each dict: {title, publisher, publish_date, link}

class SocialSentiment(BaseModel):
    """X/Twitter sentiment from xAI API."""
    query: str
    summary: Optional[str] = None  # Grok's analysis of sentiment
    bullish_points: list[str] = Field(default_factory=list)
    bearish_points: list[str] = Field(default_factory=list)

class DataPackage(BaseModel):
    """Complete data bundle for persona consumption."""
    ticker: str
    company_name: str
    fetched_at: datetime
    financials: Optional[FinancialData] = None
    filing_10k: Optional[FilingSummary] = None
    filing_10q: Optional[FilingSummary] = None
    news: Optional[NewsSummary] = None
    social: Optional[SocialSentiment] = None
    warnings: list[str] = Field(default_factory=list)

    def to_context_string(self) -> str:
        """Format as a string suitable for LLM context injection."""
        # Serialize to JSON, truncate if over budget
        return self.model_dump_json(indent=2, exclude_none=True)
```

### Pattern 3: xAI API via OpenAI SDK Compatibility

**What:** Use the existing OpenAI SDK (already in the project) with `base_url="https://api.x.ai/v1"` to call the xAI Responses API with the `x_search` tool. This avoids adding a new SDK dependency.

**When to use:** For DATA-05 (X/Twitter sentiment).

**Example:**
```python
from openai import OpenAI
import os

def fetch_social_sentiment(ticker: str, company_name: str) -> Optional[SocialSentiment]:
    """Fetch X/Twitter sentiment via xAI API x_search tool."""
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        logger.warning("XAI_API_KEY not set, skipping social sentiment")
        return None

    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )

        response = client.responses.create(
            model="grok-4-1-fast-non-reasoning",
            input=[{
                "role": "user",
                "content": (
                    f"Search X/Twitter for recent posts about {company_name} (${ticker}). "
                    f"Summarize: 1) Overall sentiment (bullish/bearish/mixed), "
                    f"2) Top 3-5 bullish arguments people are making, "
                    f"3) Top 3-5 bearish/contrarian arguments people are making. "
                    f"Be specific and cite actual viewpoints."
                ),
            }],
            tools=[{"type": "x_search"}],
        )

        # Parse response
        text = response.output_text
        return SocialSentiment(
            query=f"${ticker} {company_name}",
            summary=text,
            bullish_points=[],  # Parse from text or use structured output
            bearish_points=[],
        )
    except Exception as e:
        logger.warning("Failed to fetch social sentiment for %s: %s", ticker, e)
        return None
```

### Pattern 4: Ticker Resolution and Validation

**What:** Use `yfinance.Ticker(symbol).info` to validate a ticker. If `.info` returns a dict with `shortName` or `longName`, the ticker is valid. If it returns an empty dict or raises an exception, the ticker is invalid.

**Example:**
```python
import yfinance as yf

def resolve_ticker(ticker: str) -> tuple[bool, str]:
    """Resolve and validate a stock ticker.

    Returns:
        (is_valid, company_name) -- company_name is empty string if invalid.
    """
    try:
        t = yf.Ticker(ticker.upper().strip())
        info = t.info
        name = info.get("longName") or info.get("shortName") or ""
        if not name:
            return (False, "")
        return (True, name)
    except Exception:
        return (False, "")
```

### Anti-Patterns to Avoid

- **Raising exceptions from fetchers:** Never let a single data source failure crash the pipeline. Each fetcher returns Optional or logs a warning.
- **Dumping raw DataFrames into context:** yfinance returns multi-year DataFrames with dozens of rows. Dumping these raw will blow the token budget. Always summarize to key metrics.
- **Blocking on xAI API when key is missing:** The xAI API requires an API key. If `XAI_API_KEY` is not set, skip gracefully -- this is the only non-free source that needs a key.
- **Fetching full filing text:** A 10-K filing can be 100K+ characters. Never inject the full text. Extract only the first N characters or key sections (business description, risk factors, MD&A).
- **Not setting EDGAR identity:** edgartools requires `set_identity()` before any SEC requests. Without it, requests will be rejected.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Financial data fetching | Custom Yahoo Finance scraper | yfinance library | Yahoo changes layout frequently; yfinance handles this |
| SEC filing parsing | HTML/XML parser for EDGAR | edgartools with TenK/TenQ objects | Filing structure is complex; edgartools provides structured Python objects |
| X/Twitter search | Twitter API v2 direct integration | xAI Responses API with x_search tool | xAI handles the X search, analysis, and formatting; no need for Twitter API credentials |
| JSON serialization | Custom dict serialization | Pydantic BaseModel.model_dump_json() | Handles Optional fields, datetime, nested models automatically |
| DataFrame to dict conversion | Manual column/row iteration | pandas .to_dict() | Standard method, handles NaN, dates, etc |
| Rate limiting for SEC | Manual sleep/retry logic | edgartools built-in rate limiting | Default 9 req/s, respects SEC 10 req/s limit |

**Key insight:** All three data sources have well-established Python libraries or API patterns. The value of this phase is in the _orchestration_ and _normalization_, not in reimplementing data fetching.

## Common Pitfalls

### Pitfall 1: yfinance Returns Empty DataFrames Silently
**What goes wrong:** `Ticker.income_stmt` can return an empty DataFrame for valid tickers when Yahoo Finance has no data, or when the ticker is for a non-US market, or due to transient API issues.
**Why it happens:** yfinance is an unofficial scraper; Yahoo Finance can change its backend or restrict data at any time.
**How to avoid:** Always check `df.empty` before processing. Treat empty results as a warning, not an error.
**Warning signs:** Tests pass locally but fail in CI due to network issues or Yahoo rate limiting.

### Pitfall 2: yfinance Ticker.info Returns Partial or Empty Dict
**What goes wrong:** For some tickers (especially recently listed, delisted, or non-US), `.info` returns a dict missing `longName`, `shortName`, or financial ratios. Some keys that used to exist (like `companyOfficers`, `address`) were removed in mid-2023.
**Why it happens:** Yahoo Finance periodically changes what data it serves through its undocumented API.
**How to avoid:** Always use `.get()` with defaults. Never assume a key exists. Validate ticker by checking for at least `shortName` or `longName`.
**Warning signs:** `KeyError` on `info["longName"]`.

### Pitfall 3: edgartools Requires set_identity() Before Any Call
**What goes wrong:** SEC EDGAR rejects requests without a User-Agent header. edgartools will fail silently or raise an error.
**Why it happens:** SEC policy requires identification of all API consumers.
**How to avoid:** Call `set_identity("openIC user@example.com")` at module load time or in pipeline initialization. Can also set `EDGAR_IDENTITY` env var.
**Warning signs:** HTTP 403 errors or empty responses from EDGAR.

### Pitfall 4: 10-K Filing Text is Enormous
**What goes wrong:** A single 10-K filing text can be 100K-500K characters. Injecting this into an LLM context window alongside 6 persona prompts would leave no room for the debate.
**Why it happens:** 10-K filings are comprehensive legal documents covering all aspects of a company.
**How to avoid:** Extract only key sections (business description, risk factors first paragraphs) and truncate to a budget (e.g., 2000-3000 characters per filing). Or use `.obj()` to get a TenK object and access specific items like `tenk["Item 1"]` (Business), `tenk["Item 1A"]` (Risk Factors), `tenk["Item 7"]` (MD&A).
**Warning signs:** DataPackage JSON exceeds 50K characters.

### Pitfall 5: xAI API Responses Format Differs from OpenAI
**What goes wrong:** The xAI Responses API returns data in a format that differs from OpenAI's Chat Completions API. The response object has `output_text` for the final text, and citations may be included.
**Why it happens:** xAI uses the newer Responses API format, not the older Chat Completions format.
**How to avoid:** Use `response.output_text` to extract the text response. Do not assume `response.choices[0].message.content` structure.
**Warning signs:** AttributeError when trying to access `.choices` on the response.

### Pitfall 6: Token Budget Exceeded
**What goes wrong:** The DataPackage is too large to inject into the persona context alongside system prompts, leading to truncated analysis or API errors.
**Why it happens:** Raw financial data (multi-year statements, full filing text, many news articles) can easily exceed 10K-20K tokens.
**How to avoid:** Budget: ~4000-6000 tokens for the entire DataPackage. Summarize financials to key ratios and most recent year only. Truncate filing text to 2000-3000 chars each. Limit news to 5-8 headlines. Limit social sentiment to 500-1000 chars.
**Warning signs:** `to_context_string()` output exceeds 15K characters.

## Code Examples

### Ticker Resolution (DATA-01)
```python
# Source: yfinance official docs + GitHub issues research
import yfinance as yf

def resolve_ticker(ticker: str) -> tuple[bool, str]:
    """Validate ticker and return (is_valid, company_name)."""
    try:
        t = yf.Ticker(ticker.upper().strip())
        info = t.info or {}
        name = info.get("longName") or info.get("shortName") or ""
        if not name:
            return (False, "")
        return (True, name)
    except Exception:
        return (False, "")
```

### Financial Fundamentals (DATA-02)
```python
# Source: yfinance API reference
import yfinance as yf

def fetch_financials(ticker: str) -> dict:
    t = yf.Ticker(ticker)

    # Key ratios from .info
    info = t.info or {}
    ratios = {
        "pe_ratio": info.get("trailingPE"),
        "pb_ratio": info.get("priceToBook"),
        "profit_margin": info.get("profitMargins"),
        "roe": info.get("returnOnEquity"),
        "debt_to_equity": info.get("debtToEquity"),
        "market_cap": info.get("marketCap"),
        "revenue": info.get("totalRevenue"),
        "free_cash_flow": info.get("freeCashflow"),
    }

    # Income statement (most recent year, as dict)
    income = t.get_income_stmt(as_dict=True, freq="yearly")
    # income is dict of {date: {line_item: value}}

    # Balance sheet (most recent year)
    balance = t.get_balance_sheet(as_dict=True, freq="yearly")

    return {"ratios": ratios, "income": income, "balance": balance}
```

### SEC Filings (DATA-03)
```python
# Source: edgartools official docs
from edgar import Company, set_identity

set_identity("openIC research@example.com")

def fetch_filing_summary(ticker: str, form_type: str = "10-K") -> dict | None:
    """Fetch latest SEC filing summary."""
    try:
        company = Company(ticker)
        filings = company.get_filings(form=form_type)
        if not filings:
            return None
        latest = filings.latest()
        if not latest:
            return None

        # Get structured object for item access
        filing_obj = latest.obj()

        # For 10-K: extract key sections
        text_parts = []
        if form_type == "10-K" and filing_obj:
            # Try to access specific items
            for item_key in ["Item 1", "Item 1A", "Item 7"]:
                try:
                    section = filing_obj[item_key]
                    if section:
                        text_parts.append(f"## {item_key}\n{str(section)[:1000]}")
                except (KeyError, TypeError):
                    pass

        # Fallback: get plain text and truncate
        if not text_parts:
            text = latest.text()
            text_parts = [text[:3000]] if text else []

        return {
            "form_type": form_type,
            "filing_date": str(latest.filing_date) if hasattr(latest, 'filing_date') else None,
            "text_summary": "\n\n".join(text_parts)[:3000],
        }
    except Exception as e:
        logger.warning("Failed to fetch %s for %s: %s", form_type, ticker, e)
        return None
```

### News Fetching (DATA-04)
```python
# Source: yfinance API reference
import yfinance as yf

def fetch_news(ticker: str, count: int = 8) -> list[dict]:
    """Fetch recent news articles via yfinance."""
    try:
        t = yf.Ticker(ticker)
        news_items = t.get_news(count=count)
        if not news_items:
            return []
        return [
            {
                "title": item.get("title", ""),
                "publisher": item.get("publisher", ""),
                "link": item.get("link", ""),
                "publish_time": item.get("providerPublishTime"),
            }
            for item in news_items
        ]
    except Exception as e:
        logger.warning("Failed to fetch news for %s: %s", ticker, e)
        return []
```

### X/Twitter Sentiment via xAI (DATA-05)
```python
# Source: xAI official docs (docs.x.ai)
from openai import OpenAI
import os

def fetch_social_sentiment(ticker: str, company_name: str) -> dict | None:
    """Fetch X/Twitter sentiment via xAI Responses API."""
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        logger.warning("XAI_API_KEY not set, skipping X/Twitter sentiment")
        return None

    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )
        response = client.responses.create(
            model="grok-4-1-fast-non-reasoning",
            input=[{
                "role": "user",
                "content": (
                    f"Search X/Twitter for recent discussion about {company_name} "
                    f"(${ticker}) as a stock investment. Provide:\n"
                    f"1. Overall sentiment (bullish/bearish/mixed)\n"
                    f"2. Top 3-5 bullish arguments/viewpoints\n"
                    f"3. Top 3-5 bearish/contrarian arguments/viewpoints\n"
                    f"Be specific, cite actual viewpoints from X posts."
                ),
            }],
            tools=[{"type": "x_search"}],
        )
        return {
            "query": f"${ticker} {company_name}",
            "summary": response.output_text,
        }
    except Exception as e:
        logger.warning("Failed to fetch social sentiment for %s: %s", ticker, e)
        return None
```

### Pipeline Orchestrator
```python
def build_data_package(ticker: str) -> DataPackage:
    """Build complete DataPackage for a ticker. Gracefully handles partial failures."""
    warnings = []

    # Step 1: Validate ticker (DATA-01)
    is_valid, company_name = resolve_ticker(ticker)
    if not is_valid:
        raise ValueError(f"Invalid ticker: {ticker}")

    # Step 2: Fetch all data sources (DATA-02 through DATA-05)
    financials = fetch_financials(ticker)
    filing_10k = fetch_filing_summary(ticker, "10-K")
    filing_10q = fetch_filing_summary(ticker, "10-Q")
    news = fetch_news(ticker)
    social = fetch_social_sentiment(ticker, company_name)

    # Step 3: Assemble DataPackage with warnings for missing data
    if financials is None:
        warnings.append("Financial fundamentals unavailable")
    if filing_10k is None:
        warnings.append("10-K filing unavailable")
    if filing_10q is None:
        warnings.append("10-Q filing unavailable")
    if not news:
        warnings.append("No recent news found")
    if social is None:
        warnings.append("X/Twitter sentiment unavailable")

    return DataPackage(
        ticker=ticker.upper(),
        company_name=company_name,
        fetched_at=datetime.now(),
        financials=FinancialData(**financials["ratios"]) if financials else None,
        filing_10k=FilingSummary(**filing_10k) if filing_10k else None,
        filing_10q=FilingSummary(**filing_10q) if filing_10q else None,
        news=NewsSummary(articles=news) if news else None,
        social=SocialSentiment(**social) if social else None,
        warnings=warnings,
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| yfinance < 1.0 financials API | yfinance 1.2.x with get_income_stmt(freq=) | Feb 2026 | New method signatures; `freq` param replaces separate quarterly methods |
| edgartools v4.x | edgartools v5.25.x | Dec 2025 | Major restructure: TenK/TenQ data objects, XBRL standardization, .obj() method |
| xAI Live Search API (deprecated) | xAI Responses API with x_search tool | Jan 2026 | Old API returns 410 Gone; must use Responses API with tools array |
| xai-sdk (gRPC-based) | OpenAI SDK with base_url override | 2025-2026 | OpenAI SDK compatibility is the recommended approach per xAI docs |

**Deprecated/outdated:**
- xAI Live Search API: Retired January 12, 2026. Returns 410 status code. Use Responses API instead.
- yfinance `.financials` property: Still works but `.income_stmt` / `get_income_stmt()` is the current standard.
- edgartools v4.x patterns: v5 introduced significant API changes; use `Company.get_filings()` and `filing.obj()`.

## Token Budget Analysis

**Context for budget:** GPT-5.2 supports large context windows. However, each persona prompt is substantial (the Phase 2 persona configs are detailed JSON files with philosophy, beliefs, vocabulary). With 6 personas potentially in a debate, the DataPackage needs to be lean.

**Estimated token budgets:**
| Component | Target Chars | Est. Tokens | Notes |
|-----------|-------------|-------------|-------|
| Company info + ratios | ~500 | ~125 | Name, ticker, 8-10 key ratios |
| Income statement summary | ~800 | ~200 | Most recent year, key rows only |
| Balance sheet summary | ~800 | ~200 | Most recent year, key rows only |
| 10-K summary | ~3000 | ~750 | Business + Risk Factors + MD&A excerpts |
| 10-Q summary | ~2000 | ~500 | Most recent quarter highlights |
| News headlines | ~1000 | ~250 | 5-8 headlines with publisher/date |
| Social sentiment | ~1500 | ~375 | Bull/bear summary from X |
| **Total DataPackage** | **~9600** | **~2400** | Well within budget for injection |

**Recommendation:** Cap `to_context_string()` at ~12K characters (~3000 tokens). This leaves ample room for persona system prompts and debate context.

## Open Questions

1. **yfinance reliability in 2026**
   - What we know: yfinance 1.2.0 released Feb 2026; some users report 404 errors and empty DataFrames for valid tickers.
   - What's unclear: How reliable is it for the 20-30 most common US large-cap tickers that openIC will primarily target?
   - Recommendation: Build with yfinance but add comprehensive try/except handling and consider yahoo_fin as a fallback library if yfinance proves unreliable. Test with AAPL, MSFT, GOOGL, BRK-B as canaries.

2. **xAI API model availability and pricing**
   - What we know: grok-4-1-fast-non-reasoning is $0.20/$0.50 per M tokens; x_search tool calls priced at $2.50-$5 per 1000 calls. New accounts get $25 free credits.
   - What's unclear: Whether `responses.create()` with OpenAI SDK is fully stable for the x_search tool, since the xAI SDK uses a different gRPC approach.
   - Recommendation: Use OpenAI SDK approach (already in project). If it fails, fall back to the xai-sdk package. Test with a simple sentiment query during development.

3. **edgartools TenK item access API**
   - What we know: edgartools v5 provides `filing.obj()` returning TenK/TenQ objects; docs show `tenk["Item 1"]` access pattern.
   - What's unclear: Whether all 10-K filings parse correctly into TenK objects, especially older filings or filings with non-standard HTML.
   - Recommendation: Use `.obj()` with fallback to `.text()[:3000]` when structured access fails. Only fetch the most recent filing.

4. **OpenAI SDK version compatibility with xAI Responses API**
   - What we know: xAI docs show `client.responses.create()` pattern with OpenAI SDK. The project uses openai>=1.65.
   - What's unclear: Which minimum OpenAI SDK version supports the `.responses` namespace. This is a newer API.
   - Recommendation: Verify `client.responses.create()` works with the installed openai version. If not, the HTTP endpoint can be called directly with `httpx` (already installed) as a fallback.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (already configured) |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_data_pipeline.py -x` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DATA-01 | Ticker resolution returns (True, name) for valid ticker, (False, "") for invalid | unit (mock yfinance) | `uv run pytest tests/test_data_pipeline.py::test_resolve_ticker -x` | No -- Wave 0 |
| DATA-02 | Financial fundamentals returns FinancialData with key ratios | unit (mock yfinance) | `uv run pytest tests/test_data_pipeline.py::test_fetch_financials -x` | No -- Wave 0 |
| DATA-03 | SEC filings returns FilingSummary with truncated text | unit (mock edgartools) | `uv run pytest tests/test_data_pipeline.py::test_fetch_filings -x` | No -- Wave 0 |
| DATA-04 | News returns list of article dicts | unit (mock yfinance) | `uv run pytest tests/test_data_pipeline.py::test_fetch_news -x` | No -- Wave 0 |
| DATA-05 | Social sentiment returns SocialSentiment or None if no key | unit (mock openai) | `uv run pytest tests/test_data_pipeline.py::test_fetch_social -x` | No -- Wave 0 |
| DATA-ALL | build_data_package assembles partial results with warnings | unit (mocked sources) | `uv run pytest tests/test_data_pipeline.py::test_build_data_package -x` | No -- Wave 0 |
| DATA-ALL | DataPackage.to_context_string() under token budget | unit | `uv run pytest tests/test_data_pipeline.py::test_context_string_budget -x` | No -- Wave 0 |
| INTEGRATION | Full pipeline with live APIs for AAPL | integration (live_api marker) | `uv run pytest tests/test_data_pipeline.py -m live_api -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_data_pipeline.py -x`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before verification

### Wave 0 Gaps
- [ ] `tests/test_data_pipeline.py` -- all DATA-XX unit tests with mocked external APIs
- [ ] `tests/test_data_models.py` -- DataPackage model serialization and validation tests
- [ ] Live API integration tests with `@pytest.mark.live_api` marker (for AAPL canary test)

## Sources

### Primary (HIGH confidence)
- [yfinance official docs](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.html) -- Ticker API reference, financial statement methods, news
- [yfinance PyPI](https://pypi.org/project/yfinance/) -- Version 1.2.0, release date Feb 2026
- [edgartools official docs](https://edgartools.readthedocs.io/) -- Company class, filing access, XBRL extraction
- [edgartools PyPI](https://pypi.org/project/edgartools/) -- Version 5.25.1, Python >=3.10
- [edgartools GitHub](https://github.com/dgunning/edgartools) -- README with code examples
- [xAI X Search docs](https://docs.x.ai/developers/tools/x-search) -- x_search tool parameters, response format
- [xAI Tools Overview](https://docs.x.ai/docs/guides/tools/overview) -- Available tools, authentication
- [xAI Models and Pricing](https://docs.x.ai/developers/models) -- grok-4-1-fast pricing, context windows
- [xAI Getting Started](https://docs.x.ai/developers/quickstart) -- SDK installation, OpenAI compatibility

### Secondary (MEDIUM confidence)
- [yfinance GitHub issues](https://github.com/ranaroussi/yfinance/issues) -- Known issues with empty DataFrames, 404 errors, missing .info keys
- [edgartools configuration docs](https://edgartools.readthedocs.io/en/stable/configuration/) -- set_identity, rate limiting (9 req/s default)
- [edgartools data objects docs](https://edgartools.readthedocs.io/en/latest/concepts/data-objects/) -- TenK/TenQ object properties
- [xAI free credits](https://www.getaiperks.com/en/blogs/22-xai-grok-free-credits) -- $25 signup credits, $150/month data sharing program

### Tertiary (LOW confidence)
- [yfinance news format](https://algotrading101.com/learn/yfinance-guide/) -- News dict keys (title, link, publisher, providerPublishTime, uuid) -- verified against GitHub issues but exact current format may vary
- xAI `responses.create()` with OpenAI SDK -- documented in xAI quickstart but unclear which openai SDK minimum version is required for `.responses` namespace

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- yfinance, edgartools, openai SDK are well-documented and verified
- Architecture: HIGH -- fetcher pattern with Optional returns is standard; Pydantic BaseModel is proven
- Pitfalls: MEDIUM-HIGH -- based on yfinance GitHub issues and edgartools docs; xAI API is newer and less battle-tested
- xAI integration: MEDIUM -- OpenAI SDK compatibility is documented but the Responses API is relatively new; may need fallback to direct HTTP or xai-sdk

**Research date:** 2026-03-21
**Valid until:** 2026-04-07 (yfinance can break any time due to Yahoo changes; xAI API is evolving)
