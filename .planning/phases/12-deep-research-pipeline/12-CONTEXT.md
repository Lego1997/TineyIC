# Phase 12: Deep Research Pipeline - Context

**Phase:** 12
**Name:** Deep Research Pipeline
**Goal:** Every debate is informed by a comprehensive, LLM-synthesized research brief that covers the target company's business, competition, industry, management, and analyst perspectives -- not just raw financial data

## Requirements

| ID | Description | Priority |
|----|-------------|----------|
| DATA-06 | `build_data_package()` includes an optional deep research step that uses web search APIs + LLM synthesis to produce a structured `ResearchBrief` | MUST |
| DATA-07 | ResearchBrief covers: business model and competitive moat analysis, industry trends and macro tailwinds/headwinds, management track record and capital allocation, recent catalysts and developments (last 6 months), bull/bear investment cases from public analyst perspectives | MUST |
| DATA-08 | ResearchBrief stored as a new field on `DataPackage` and included in `to_context_string()` so all personas receive the enriched fact base at debate start | MUST |
| DATA-09 | Research step is toggleable via a UI checkbox or parameter (default: enabled); when disabled, the pipeline behaves exactly as in v1 with no performance penalty | MUST |

## Success Criteria

1. `build_data_package()` includes an optional deep research step that uses web search APIs + LLM synthesis to produce a structured `ResearchBrief`
2. The `ResearchBrief` covers at minimum: business model and competitive moat analysis, industry trends and macro tailwinds/headwinds, management track record and capital allocation, recent catalysts and developments (last 6 months), and bull/bear investment cases from public analyst perspectives
3. The `ResearchBrief` is stored as a new field on `DataPackage` and included in `to_context_string()` so all personas receive the enriched fact base at debate start
4. The research step is toggleable via a UI checkbox or parameter (default: enabled) -- when disabled, the pipeline behaves exactly as in v1 with no performance penalty
5. When web search or LLM synthesis fails, the system logs a warning and continues with existing data sources (graceful degradation, consistent with the pipeline's existing pattern)
6. Unit tests verify `ResearchBrief` model, pipeline integration, toggle behavior, and failure handling without requiring API keys

## Dependencies

- Phase 7 (Release Hardening) -- COMPLETE: clean test baseline
- Phase 11 (Model Selection) -- COMPLETE: `config_manager.get("model")` returns selected model at runtime

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| OpenAI Responses API with `web_search_preview` tool for web search | Project already uses OpenAI SDK; avoids adding new dependencies (Tavily, Brave, etc.); consistent with social.py's Responses API pattern |
| Two-stage architecture: web search calls → LLM synthesis into structured brief | Separates search from structuring; allows multiple focused searches for better coverage; synthesis uses TinyTroupe's `client().send_message()` consistent with memo.py |
| ResearchBrief as a dedicated Pydantic model (not free-form text) | Structured sections map to the 5 required research areas; enables per-section context budget management; testable structure |
| Increase `to_context_string()` budget from 12K to 20K chars | Research brief adds ~4-6K chars; without increasing budget, truncation would drop filing/social data to make room |
| `deep_research` parameter on `build_data_package()` defaults to True | Matches requirement (default: enabled); no performance penalty when False since research code never executes |
| Research uses the runtime-selected model via `config_manager.get("model")` | Consistent with Phase 11 model selection; user's chosen model powers both research and debate |
| OPENAI_API_KEY is the only required credential (same as debate) | No new API keys needed; web_search_preview tool is built into OpenAI models |

## Key Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Web search adds 10-30s latency to data fetching | MODERATE | Runs in worker thread; UI shows "Researching..." status; user can disable via checkbox |
| Web search results may be low quality for obscure tickers | LOW | Graceful degradation -- if search returns poor results, LLM synthesis still produces a brief from whatever is available |
| OpenAI Responses API may have different error modes than Chat Completions | LOW | Wrap in try/except consistent with social.py pattern; log warning and skip on failure |
| Research brief inflates context, possibly reducing space for other data | LOW | Managed via to_context_string() budget increase to 20K; brief sections have per-section char limits |
| Model costs increase with research step (additional API calls) | LOW | Cost is captured in existing cost_stats tracking; user can disable research to save costs |

## Architectural Context

### Files to Create
- `src/tinyic/data/research.py` -- ResearchBrief builder: web search + LLM synthesis
- `tests/test_research.py` -- Unit tests for research module

### Files to Modify
- `src/tinyic/data/models.py` -- Add ResearchBrief model, add field to DataPackage, update to_context_string()
- `src/tinyic/data/pipeline.py` -- Add deep_research parameter to build_data_package(), call build_research_brief()
- `src/tinyic/data/__init__.py` -- Export ResearchBrief and build_research_brief
- `src/tinyic/ui/app.py` -- Add Deep Research checkbox to sidebar, wire through to worker
- `tests/test_ui.py` -- Tests for research toggle UI

### Web Search Architecture
```
build_data_package(ticker, deep_research=True)
  └── build_research_brief(ticker, company_name, description)
        ├── _web_search_company(ticker, company_name)      # Business + moat + management
        ├── _web_search_environment(ticker, company_name)   # Industry + macro + catalysts
        └── _synthesize_brief(company_name, ticker, raw_search_results)
              └── client().send_message(...)                # Structured JSON output → ResearchBrief
```

### Key APIs (Existing)
- `client().send_message(messages, temperature=...) -> dict` -- TinyTroupe LLM client (memo.py pattern)
- `extract_json(text) -> dict` -- Parse JSON from LLM output (memo.py pattern)
- `config_manager.get("model") -> str` -- Currently selected model
- `OpenAI.responses.create(model, input, tools) -> Response` -- Responses API (social.py pattern)
- `DataPackage.to_context_string() -> str` -- Context serialization with truncation budget

### Current Pipeline Architecture (pipeline.py)
- Sequential fetches: ticker → description → financials → filings → news → social
- Each fetch is independent; failure adds a warning, doesn't crash
- Returns assembled DataPackage with all available data

## Source Material
- Research: `.planning/phases/12-deep-research-pipeline/12-RESEARCH.md` (if available)
- Roadmap: `.planning/ROADMAP.md` (Phase 12 section)
- Pattern reference: `src/tinyic/data/social.py` (Responses API with tool), `src/tinyic/debate/memo.py` (LLM synthesis with JSON output)
