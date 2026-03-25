# Phase 12: Deep Research Pipeline - Research

**Researched:** 2026-03-25
**Domain:** Web search APIs, LLM synthesis, structured output, financial research automation
**Confidence:** HIGH

## Summary

Phase 12 adds a deep research pipeline to `build_data_package()` that uses web search APIs + LLM synthesis to produce a structured `ResearchBrief` covering business model, competition, industry trends, management, catalysts, and analyst perspectives. The research brief enriches every persona's context beyond raw financial data.

The **primary recommendation** is to use **OpenAI's built-in `web_search` tool** via the Responses API for the search step, then a **separate `responses.parse()` call** with a Pydantic `ResearchBrief` model for structured synthesis. This avoids adding any new dependency -- the project already uses the OpenAI SDK (v2.29.0) with `client.responses.create()` for xAI social sentiment, and the exact same pattern works for OpenAI's own web search. Tavily is the recommended fallback if OpenAI web search proves insufficient for financial research quality.

**Primary recommendation:** Use OpenAI `web_search` tool (zero new dependencies) with a two-step search-then-synthesize pattern, falling back to Tavily if search quality is inadequate.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DATA-06 | `build_data_package()` includes optional deep research step using web search APIs + LLM synthesis producing structured `ResearchBrief` | Two-step pattern: OpenAI web_search for retrieval, responses.parse() for structured synthesis. Integrates into existing pipeline.py as a new step between social sentiment and package assembly. |
| DATA-07 | ResearchBrief covers: business model/competitive moat, industry trends/macro, management track record/capital allocation, recent catalysts (6 months), bull/bear analyst perspectives | Pydantic model with 5 typed sections. LLM synthesis prompt structures output into these exact dimensions. Multiple search queries target each dimension. |
| DATA-08 | ResearchBrief stored on DataPackage, included in `to_context_string()` | New Optional[ResearchBrief] field on DataPackage. `to_context_string()` updated with truncation logic. Pre-summarized brief (~3000 chars) fits within existing 12K budget. |
| DATA-09 | Research step toggleable via UI checkbox (default: enabled), graceful degradation on failure | `enable_research` parameter on `build_data_package()`, UI checkbox in sidebar. try/except pattern matches existing social.py graceful degradation. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| openai | 2.29.0 (already installed) | Web search via Responses API + LLM synthesis | Already in project. `web_search` tool avoids new dependencies. |
| pydantic | 2.x (already installed) | `ResearchBrief` model definition | Already used for all data models in `tinyic.data.models`. |

### Supporting (Fallback Only)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tavily-python | 0.5.x | Alternative web search API | Only if OpenAI web_search quality is insufficient for financial research. Has `topic="finance"` mode. |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| OpenAI web_search | Tavily Search API | Tavily has `topic="finance"` mode and returns raw content snippets. Costs $0.008/credit vs OpenAI's ~$0.01/search + token costs. Adds a dependency + API key. |
| OpenAI web_search | Brave Search API | REST-only (no Python SDK), removed free tier in 2026, $5/1K requests. No financial-specific mode. |
| OpenAI web_search | xAI x_search | Already used for social sentiment. Could be repurposed, but x_search is optimized for X/Twitter content, not general web. |

**Installation:**
```bash
# No new dependencies needed for primary approach
# Only if Tavily fallback is chosen:
uv add tavily-python
```

## Architecture Patterns

### Recommended Project Structure
```
src/tinyic/data/
  models.py           # Add ResearchBrief model
  research.py          # NEW: web search + LLM synthesis module
  pipeline.py          # Add optional research step
src/tinyic/ui/
  app.py              # Add "Deep Research" checkbox in sidebar
tests/
  test_data_pipeline.py  # Add research tests
```

### Pattern 1: Two-Step Search-Then-Synthesize (RECOMMENDED)

**What:** Separate web search retrieval from structured LLM synthesis to avoid the known issues with combining `web_search` + structured output in a single API call.

**When to use:** Always. OpenAI's web_search tool corrupts JSON output when combined with structured output schemas in a single call. This is a well-documented issue.

**Why two steps:**
- OpenAI web_search + structured output in one call has "extremely high failure rate" with truncated/corrupted JSON (verified via OpenAI community reports, multiple users, HIGH confidence)
- The xAI social.py module already uses this text-output pattern successfully
- The memo.py module already demonstrates the synthesis-from-text pattern

**Step 1 -- Search (text output):**
```python
from openai import OpenAI

def _web_search(query: str, model: str = "gpt-4.1-mini") -> str:
    """Execute web search via OpenAI Responses API.

    Uses a cheaper/faster model for search since the heavy
    lifting is just retrieving and summarizing web content.
    """
    client = OpenAI()  # Uses OPENAI_API_KEY from env
    response = client.responses.create(
        model=model,
        tools=[{
            "type": "web_search",
            "search_context_size": "medium",
        }],
        input=query,
    )
    return response.output_text
```

**Step 2 -- Synthesize into structured output:**
```python
from openai import OpenAI
from pydantic import BaseModel, Field
from typing import Optional

class ResearchBrief(BaseModel):
    """Structured research brief for investment analysis."""
    business_model: str = Field(description="Business model and competitive moat analysis")
    industry_trends: str = Field(description="Industry trends, macro tailwinds/headwinds")
    management: str = Field(description="Management track record and capital allocation")
    recent_catalysts: str = Field(description="Recent catalysts and developments (last 6 months)")
    bull_bear_cases: str = Field(description="Bull and bear investment cases from analyst perspectives")
    sources: list[str] = Field(default_factory=list, description="Source URLs from web search")

def _synthesize_brief(raw_research: str, ticker: str, company_name: str) -> ResearchBrief:
    """Synthesize raw search results into a structured ResearchBrief."""
    client = OpenAI()
    response = client.responses.parse(
        model="gpt-4.1-mini",  # Fast model for synthesis
        instructions=(
            "You are a senior equity research analyst. Synthesize the provided "
            "web search results into a structured investment research brief. "
            "Each section should be 2-4 sentences, factual, and cite specific "
            "data points. Keep total output concise (~2500 characters)."
        ),
        input=(
            f"Company: {company_name} ({ticker})\n\n"
            f"## Raw Research Results\n{raw_research}"
        ),
        text_format=ResearchBrief,
    )
    return response.output_parsed
```

### Pattern 2: Multi-Query Search Strategy

**What:** Fire multiple targeted search queries (one per ResearchBrief dimension) to get better coverage than a single broad query.

**When to use:** Always. A single query like "AAPL investment analysis" returns shallow results. Targeted queries produce richer material.

**Example queries for each dimension:**
```python
def _build_search_queries(ticker: str, company_name: str) -> list[str]:
    """Build targeted search queries for each research dimension."""
    return [
        f"{company_name} ({ticker}) business model competitive advantages moat 2026",
        f"{company_name} industry trends market outlook tailwinds headwinds 2026",
        f"{company_name} CEO management track record capital allocation strategy",
        f"{company_name} ({ticker}) recent news catalysts developments last 6 months",
        f"{company_name} ({ticker}) bull bear case analyst rating price target",
    ]
```

### Pattern 3: Graceful Degradation (Matching Existing Pipeline Pattern)

**What:** Each data source fails independently. Research failure should not block the debate.

**When to use:** Always. This is the established pattern in `pipeline.py`.

**Example:**
```python
# In pipeline.py build_data_package():
research_brief = None
if enable_research:
    try:
        research_brief = fetch_research_brief(resolved_ticker, company_name)
    except Exception as e:
        logger.warning("Deep research failed for %s: %s", resolved_ticker, e)
        warnings.append("Deep research unavailable")
```

### Anti-Patterns to Avoid
- **Single-call web_search + structured output:** Do NOT combine `web_search` tool with `text_format`/structured output in the same API call. This produces corrupted JSON. Always use two separate calls.
- **Using the debate model (gpt-5.2) for search:** Search retrieval does not need the most expensive model. Use `gpt-4.1-mini` for search and synthesis to control costs. The brief is pre-computed before debate starts.
- **Unbounded search result text:** Raw web search output can be very long. Truncate before passing to synthesis step.
- **Blocking on multiple sequential searches:** If using multi-query strategy, consider whether queries can be parallelized (they can, since they're independent).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Web search | Custom scraping / BeautifulSoup pipeline | OpenAI `web_search` tool or Tavily API | Web scraping is fragile, blocks change, rate limiting is complex. API handles all of this. |
| Structured LLM output | Manual JSON parsing with regex | `responses.parse()` with Pydantic model | SDK handles schema injection, JSON validation, type coercion. `extract_json()` in memo.py works but is more fragile. |
| Search result deduplication | Custom dedup logic | Let the LLM synthesis step handle it | The synthesis prompt naturally merges overlapping information from multiple queries. |
| Context budget management | Complex token counting | Character-based truncation (existing pattern) | The project already uses char-based caps (12K for `to_context_string()`, 3000 for filing summaries). Keep consistent. |

**Key insight:** The project already has both the OpenAI SDK and the Responses API pattern (social.py). Adding web search is a configuration change, not a new integration.

## Common Pitfalls

### Pitfall 1: Combining web_search + structured output in one call
**What goes wrong:** JSON output is truncated mid-string, schema is ignored, response appears successful (HTTP 200) but content is corrupted.
**Why it happens:** OpenAI's web_search tool injects search context that consumes token budget unpredictably, leaving insufficient room for structured JSON output.
**How to avoid:** Always use two separate API calls: (1) web_search with plain text output, (2) responses.parse() with Pydantic model.
**Warning signs:** Truncated JSON, `finish_reason: "stop"` but incomplete content.

### Pitfall 2: Research brief bloating context window
**What goes wrong:** A verbose research brief pushes `to_context_string()` well past its 12K char budget, causing filing and social data to be aggressively truncated.
**Why it happens:** Each of 5 search queries can return 2000+ characters. Synthesis without length constraints produces 5000+ chars.
**How to avoid:** Cap the ResearchBrief at ~3000 characters total. The synthesis prompt must specify this limit. Each section should be 2-4 sentences (not paragraphs). Update `to_context_string()` to truncate research_brief before other fields.
**Warning signs:** `to_context_string()` output consistently at 12K with truncated `...` in filing/social sections.

### Pitfall 3: OpenAI web_search cost surprise
**What goes wrong:** Each web_search call costs ~$0.01 (tool call fee) + input token costs for search context. With 5 queries per company, research adds ~$0.05-0.10 per debate.
**Why it happens:** Dual billing: $10/1K tool calls + search content tokens billed at model input rates. `search_context_size: "medium"` adds significant token volume.
**How to avoid:** Use `gpt-4.1-mini` (cheapest model) for search. Set `search_context_size: "low"` to minimize token costs. Monitor with cost tracking already in the project.
**Warning signs:** Unexpectedly high API bills. Check cost stats in OpenAI dashboard.

### Pitfall 4: Search results are stale or irrelevant
**What goes wrong:** Generic queries return SEO-optimized content rather than actual financial analysis.
**Why it happens:** Web search quality varies. Financial content is heavily SEO'd.
**How to avoid:** Use targeted queries with specific financial terms. Include the current year in queries. For Tavily, use `topic="finance"` mode.
**Warning signs:** Research brief contains generic marketing copy instead of financial analysis.

### Pitfall 5: API key not set -- silent failure
**What goes wrong:** If using Tavily as fallback, missing `TAVILY_API_KEY` causes silent failure.
**Why it happens:** OpenAI web_search uses the same `OPENAI_API_KEY` already required by the project. But Tavily requires a separate key.
**How to avoid:** Primary approach (OpenAI web_search) reuses existing `OPENAI_API_KEY` -- no new key needed. If Tavily is added, follow the same pattern as `social.py` (check for key, log warning, return None).
**Warning signs:** Research brief is always None without any error in logs.

## Code Examples

### Complete Research Module Pattern
```python
# src/tinyic/data/research.py
"""Deep research pipeline: web search + LLM synthesis."""

import logging
import os
from typing import Optional

from .models import ResearchBrief

logger = logging.getLogger(__name__)

# Maximum characters for raw search results before synthesis
MAX_RAW_RESEARCH_LENGTH = 8000

def _build_search_queries(ticker: str, company_name: str) -> list[str]:
    """Build targeted search queries for each research dimension."""
    return [
        f"{company_name} ({ticker}) business model competitive advantages moat 2026",
        f"{company_name} industry trends market size outlook 2026",
        f"{company_name} CEO management capital allocation track record",
        f"{company_name} ({ticker}) recent news catalysts last 6 months",
        f"{company_name} ({ticker}) bull bear analyst rating investment thesis",
    ]

def _web_search_openai(query: str) -> Optional[str]:
    """Execute a single web search via OpenAI Responses API."""
    try:
        from openai import OpenAI
        client = OpenAI()  # Uses OPENAI_API_KEY
        response = client.responses.create(
            model="gpt-4.1-mini",
            tools=[{"type": "web_search", "search_context_size": "low"}],
            input=query,
        )
        return response.output_text
    except Exception as e:
        logger.warning("Web search failed for query '%s': %s", query, e)
        return None

def _synthesize_brief(
    raw_research: str, ticker: str, company_name: str
) -> Optional[ResearchBrief]:
    """Synthesize raw search results into structured ResearchBrief."""
    try:
        from openai import OpenAI
        client = OpenAI()
        response = client.responses.parse(
            model="gpt-4.1-mini",
            instructions=(
                "You are a senior equity research analyst. Synthesize the provided "
                "web search results into a structured investment research brief for "
                f"{company_name} ({ticker}). "
                "Each section: 2-4 factual sentences with specific data points. "
                "Total output must be under 2500 characters. "
                "Be specific and quantitative where possible."
            ),
            input=raw_research,
            text_format=ResearchBrief,
        )
        return response.output_parsed
    except Exception as e:
        logger.warning("Research synthesis failed for %s: %s", ticker, e)
        return None

def fetch_research_brief(
    ticker: str, company_name: str
) -> Optional[ResearchBrief]:
    """Fetch and synthesize a deep research brief for a company.

    Executes multiple targeted web searches, concatenates results,
    and synthesizes into a structured ResearchBrief via LLM.

    Args:
        ticker: Stock ticker symbol.
        company_name: Full company name.

    Returns:
        ResearchBrief with structured analysis, or None on failure.
    """
    queries = _build_search_queries(ticker, company_name)

    # Execute searches (could be parallelized in future)
    raw_parts = []
    for query in queries:
        result = _web_search_openai(query)
        if result:
            raw_parts.append(result)

    if not raw_parts:
        logger.warning("All web searches failed for %s", ticker)
        return None

    # Combine and truncate raw results
    raw_research = "\n\n---\n\n".join(raw_parts)
    if len(raw_research) > MAX_RAW_RESEARCH_LENGTH:
        raw_research = raw_research[:MAX_RAW_RESEARCH_LENGTH] + "..."

    # Synthesize into structured brief
    return _synthesize_brief(raw_research, ticker, company_name)
```

### ResearchBrief Pydantic Model
```python
# Added to src/tinyic/data/models.py

class ResearchBrief(BaseModel):
    """LLM-synthesized research brief from web search results."""
    business_model: str = Field(
        default="",
        description="Business model and competitive moat analysis"
    )
    industry_trends: str = Field(
        default="",
        description="Industry trends, macro tailwinds/headwinds"
    )
    management: str = Field(
        default="",
        description="Management track record and capital allocation"
    )
    recent_catalysts: str = Field(
        default="",
        description="Recent catalysts and developments (last 6 months)"
    )
    bull_bear_cases: str = Field(
        default="",
        description="Bull and bear investment cases from analyst perspectives"
    )
    sources: list[str] = Field(
        default_factory=list,
        description="Source URLs from web search"
    )
```

### DataPackage Integration
```python
# Updated DataPackage in models.py

class DataPackage(BaseModel):
    # ... existing fields ...
    research_brief: Optional[ResearchBrief] = None  # NEW

    def to_context_string(self) -> str:
        json_str = self.model_dump_json(indent=2, exclude_none=True)
        if len(json_str) > 12000:
            data = self.model_dump(exclude_none=True)
            # Truncate research brief first (it's supplementary)
            if "research_brief" in data:
                for key in ["business_model", "industry_trends", "management",
                            "recent_catalysts", "bull_bear_cases"]:
                    if key in data["research_brief"]:
                        data["research_brief"][key] = data["research_brief"][key][:400] + "..."
                json_str = self.__class__.model_validate(data).model_dump_json(
                    indent=2, exclude_none=True
                )
            # Then truncate social/filings as before
            for key in ["social", "filing_10q", "filing_10k"]:
                if key in data and len(json_str) > 12000:
                    # ... existing truncation logic ...
                    pass
        return json_str
```

### Pipeline Integration
```python
# Updated build_data_package in pipeline.py

def build_data_package(
    ticker: str,
    enable_research: bool = True,  # NEW parameter (DATA-09)
) -> DataPackage:
    # ... existing steps 1-6 ...

    # Step 7: Deep research (DATA-06)
    research_brief = None
    if enable_research:
        try:
            from .research import fetch_research_brief
            research_brief = fetch_research_brief(resolved_ticker, company_name)
        except Exception as e:
            logger.warning("Deep research failed for %s: %s", resolved_ticker, e)
            warnings.append("Deep research unavailable")
        if research_brief is None and enable_research:
            warnings.append("Deep research unavailable")

    # Step 8: Assemble DataPackage
    package = DataPackage(
        # ... existing fields ...
        research_brief=research_brief,  # NEW
    )
```

### UI Checkbox Integration
```python
# In render_sidebar() in app.py, after the Model section:

st.divider()
st.subheader("Research")
enable_research = st.checkbox(
    "Deep Research",
    value=True,
    help="Web search + LLM analysis of business, industry, management, and analyst views",
    disabled=disabled,
    key="enable_research",
)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `web_search_preview` tool type | `web_search` tool type | Aug 2025 | Cheaper ($10/1K vs $25/1K), domain filtering support, better quality |
| Manual JSON extraction (`extract_json()`) | `responses.parse()` with Pydantic | OpenAI SDK 1.40+ | Guaranteed valid JSON matching schema, type-safe parsed output |
| Tavily as default AI search | OpenAI built-in web_search | Mar 2025 | No extra dependency, same SDK, simpler integration |

**Deprecated/outdated:**
- `web_search_preview` and `web_search_preview_2025_03_11`: Legacy tool types. Use `web_search` instead ($10/1K vs $25/1K).
- `gpt-4o-search-preview`: Deprecated model variant. Use standard models with `web_search` tool.

## Token Budget Analysis

**Current context budget:**
- `to_context_string()` caps at ~12,000 characters
- Typical DataPackage without research: ~6,000-8,000 characters
- Available headroom: ~4,000-6,000 characters

**Research brief target:**
- Each section: 2-4 sentences (~200-400 chars each)
- 5 sections + sources: ~1,500-2,500 characters
- **Target: 2,500 characters max** for the entire ResearchBrief

**Truncation priority (if over 12K):**
1. Research brief sections (supplementary, can be shortened)
2. Social sentiment summary (already truncated to 1000 chars)
3. Filing text summaries (10-Q first, then 10-K)

**Pre-summarization is required.** The synthesis prompt must enforce a character limit. Raw search results (potentially 10K+ chars from 5 queries) must be condensed into a ~2,500 char brief before storage.

## Cost Estimates

| Operation | Model | Cost per call | Calls per debate | Total |
|-----------|-------|---------------|------------------|-------|
| Web search (5 queries) | gpt-4.1-mini | ~$0.005-0.01 each | 5 | ~$0.025-0.05 |
| Synthesis | gpt-4.1-mini | ~$0.005 | 1 | ~$0.005 |
| **Total research cost** | | | | **~$0.03-0.06** |

Compare to existing debate cost: ~$0.50-2.00 per debate with gpt-5.2. Research adds 3-6% to total cost.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.x |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_data_pipeline.py -x -q` |
| Full suite command | `uv run pytest tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DATA-06 | `fetch_research_brief()` returns ResearchBrief from mocked search | unit | `uv run pytest tests/test_data_pipeline.py::TestResearchBrief::test_fetch_research_brief_success -x` | No -- Wave 0 |
| DATA-06 | `build_data_package()` includes research_brief field | unit | `uv run pytest tests/test_data_pipeline.py::TestResearchPipeline::test_pipeline_with_research -x` | No -- Wave 0 |
| DATA-07 | ResearchBrief model has all 5 required sections | unit | `uv run pytest tests/test_data_pipeline.py::TestResearchBrief::test_model_fields -x` | No -- Wave 0 |
| DATA-08 | `to_context_string()` includes research_brief when present | unit | `uv run pytest tests/test_data_pipeline.py::TestResearchBrief::test_context_string_includes_research -x` | No -- Wave 0 |
| DATA-08 | `to_context_string()` truncates research_brief when over budget | unit | `uv run pytest tests/test_data_pipeline.py::TestResearchBrief::test_context_string_truncation -x` | No -- Wave 0 |
| DATA-09 | `enable_research=False` skips research step | unit | `uv run pytest tests/test_data_pipeline.py::TestResearchPipeline::test_pipeline_research_disabled -x` | No -- Wave 0 |
| DATA-09 | Research failure produces warning, not crash | unit | `uv run pytest tests/test_data_pipeline.py::TestResearchPipeline::test_pipeline_research_failure_graceful -x` | No -- Wave 0 |
| DATA-06 | Live API integration (web search + synthesis) | integration (live_api) | `uv run pytest tests/test_data_pipeline.py::TestResearchLive -x -m live_api` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_data_pipeline.py -x -q`
- **Per wave merge:** `uv run pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_data_pipeline.py` -- add TestResearchBrief and TestResearchPipeline classes
- [ ] Mock patterns for OpenAI `responses.create()` with web_search tool output
- [ ] Mock patterns for OpenAI `responses.parse()` with Pydantic model output
- [ ] Fixture for sample ResearchBrief data

## Open Questions

1. **Should search queries be parallelized?**
   - What we know: 5 sequential searches add ~5-10 seconds latency. The data pipeline already runs sequentially.
   - What's unclear: Whether concurrent OpenAI API calls are safe given the existing tinytroupe concurrency semaphore.
   - Recommendation: Start sequential (simpler, matches existing pattern). Parallelize in a future optimization pass if latency is a concern.

2. **Should the selected model (from Phase 11) be used for search/synthesis?**
   - What we know: The ROADMAP says "research uses the selected model." But gpt-5.2 is expensive for search retrieval.
   - What's unclear: Whether the user intent is "use the same model" or "use the model selection infrastructure."
   - Recommendation: Use `gpt-4.1-mini` for search/synthesis by default (cost efficiency). The selected model is for the actual debate personas. Document this design decision.

3. **OpenAI web_search financial quality vs Tavily `topic="finance"`**
   - What we know: Tavily has a dedicated `topic="finance"` mode that optimizes results. OpenAI web_search is general-purpose.
   - What's unclear: Whether financial research quality differs meaningfully in practice.
   - Recommendation: Start with OpenAI web_search (zero dependencies). If quality is poor, add Tavily as an alternative search backend behind an abstraction layer.

## Sources

### Primary (HIGH confidence)
- OpenAI SDK v2.29.0 (installed in project) -- `responses.create()` with `web_search` tool, `responses.parse()` with Pydantic
- Project codebase: `social.py` demonstrates `client.responses.create()` with tool pattern, `memo.py` demonstrates LLM synthesis pattern
- OpenAI Cookbook: [Web Search and States with Responses API](https://developers.openai.com/cookbook/examples/responses_api/responses_example) -- code examples for web_search tool usage and response structure
- OpenAI Community: [Web Search + Structured Output issues](https://community.openai.com/t/web-search-completion-cuts-off-response-and-ignores-structured-outputs-on-complex-prompts/1229963) -- documented failure mode, two-step workaround
- OpenAI Community: [Integrating web_search and structured output](https://community.openai.com/t/how-can-i-integrate-web-search-and-structured-output-together-in-a-single-request-within-the-openai-api/1361202) -- confirms separate calls needed
- OpenAI Community: [New web_search tool ($10/1K vs $25/1K)](https://community.openai.com/t/new-search-tool-web-search-2025-08-26-and-web-search-vs-legacy-web-search-preview/1354682) -- pricing reduction, domain filtering

### Secondary (MEDIUM confidence)
- [Tavily API Reference](https://docs.tavily.com/documentation/api-reference/endpoint/search) -- search endpoint params, response schema, `topic="finance"` mode
- [Tavily Python SDK](https://github.com/tavily-ai/tavily-python) -- installation, usage, async support
- [Tavily Pricing](https://www.tavily.com/pricing) -- $0.008/credit, 1000 free credits/month
- OpenAI Community: [Web search cost breakdown](https://community.openai.com/t/help-me-understand-the-web-tool-cost/1369991) -- ~$0.01 per search, dual billing structure

### Tertiary (LOW confidence)
- Brave Search API pricing ($5/1K requests, removed free tier in 2026) -- from multiple web sources but not verified against official API dashboard

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- OpenAI SDK already in project, web_search tool verified in official cookbook
- Architecture: HIGH -- two-step pattern verified as necessary, existing codebase patterns well understood
- Pitfalls: HIGH -- web_search + structured output corruption issue verified across multiple community reports
- Cost estimates: MEDIUM -- based on published pricing, actual costs depend on search_context_size and response length
- Tavily fallback: MEDIUM -- API documented but not tested in this project context

**Research date:** 2026-03-25
**Valid until:** 2026-04-25 (stable -- OpenAI web_search tool is GA, patterns well established)
