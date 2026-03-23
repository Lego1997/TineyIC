# Phase 7 Research: Release Hardening

## edgartools HTMLParser Migration

### Current Filing Code Path
`src/tinyic/data/filings.py`:
1. `Company(ticker)` → `get_filings(form=type)` → `filings.latest()` → `EntityFiling`
2. Primary: `latest.obj()` → structured filing with `filing_obj["Item 1"]` etc.
3. Fallback: `latest.text()` → plain text

### Why Warnings Fire
edgartools 5.25.1 emits deprecation warnings at *module import time*:
- `edgar.__init__` → `edgar._filings` → `edgar._markdown` → `edgar.files.html_documents` (deprecated)
- `edgar._filings` → `edgar.files.htmltools` (deprecated)

These fire even before we call any API. Our code never directly imports deprecated modules.

### New API: edgar.documents.HTMLParser
Available in edgartools 5.25.1:
- `HTMLParser.parse(html_string)` → `Document`
- `HTMLParser.parse_url(url)` → `Document`
- `HTMLParser.create_for_ai()` factory for AI-optimized parsing
- `HTMLParser.create_for_accuracy()` factory for maximum fidelity

### Migration Strategy
1. **Rewrite filings.py** to use `HTMLParser` for text extraction instead of `.obj()` / `.text()`
   - Get filing HTML via `latest.document.content()` or `latest.html()`
   - Parse with `HTMLParser.parse()` to get structured Document
   - Extract sections from the Document
2. **Suppress import-level warnings** in pytest config since they're from edgartools internal imports, not our code
   - Add `filterwarnings` in `pyproject.toml` `[tool.pytest.ini_options]`

### Alternative: Attachment.markdown()
`latest.document` returns an `Attachment` with a `.markdown()` method. This could be simpler than HTMLParser for our use case (we just need text content).

**Recommended approach**: Use `latest.document.markdown()` as primary path, `HTMLParser` as structured fallback. Suppress import-level deprecation warnings from edgartools in pytest config.

## Pydantic ConfigDict Migration

### Files to Change
`src/tinytroupe/validation/simulation_validator.py`:
- Line 68: `SimulationExperimentDataset.class Config` → `model_config = ConfigDict(extra="forbid", validate_assignment=True)`
- Line 735: `SimulationExperimentEmpiricalValidationResult.class Config` → same

### Import Change
Add `from pydantic import ConfigDict` to existing pydantic import line.

## Live API Test Audit

| Test | File | Purpose | Recommendation |
|------|------|---------|----------------|
| test_live_aapl | test_data_pipeline.py:914 | E2E data pipeline | Keep — validates DATA-01..05 |
| test_listen_act_gpt52 | test_investor_persona.py:80 | GPT-5.2 integration | Keep — validates FOUND-03 |
| test_sdk_v2_compatibility | test_investor_persona.py:112 | SDK v2 compat | Keep — validates FOUND-04 |
| test_differentiation | test_personas.py:401 | Persona differentiation | Keep — validates PERS-01..06 |
| test_analyze_company_returns_structure | test_personas.py:436 | Persona analysis contract | Keep — validates persona API |
| test_format_vote_returns_structure | test_personas.py:451 | Vote format contract | Keep — validates vote API |
| test_live_debate | test_debate_live.py | Full debate E2E (~$1-2) | Keep — validates DEBT-01..03 |

All 7 tests serve as requirement validation. Recommendation: Keep all, ensure they pass with API key present.

## Cost Stats Wiring

### Existing Infrastructure
- `OpenAIClient.get_cost_stats()` → `{input_tokens, output_tokens, total_tokens, model_calls, cached_calls}`
- `TinyWorld.get_cost_stats()` → adds `{per_agent, per_step, per_agent_per_step}` breakdowns
- `DebateOrchestrator` inherits `TinyWorld.get_cost_stats()`

### What to Build
1. Add `cost_stats: Optional[dict] = None` to `DebateResult` model
2. In `run_debate()`: call `orchestrator.get_cost_stats()` after debate and attach to result
3. Create `get_debate_cost_stats(result: DebateResult) -> dict` convenience function
4. Add estimated cost calculation: `(input_tokens * price_per_1k_input + output_tokens * price_per_1k_output)`
