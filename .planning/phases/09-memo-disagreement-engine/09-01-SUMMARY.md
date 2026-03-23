# Plan 09-01 Summary: Memo Generation & Disagreement Extraction

**Status:** Complete
**Date:** 2026-03-23

## What was built

### New models in `src/tinyic/debate/models.py`
- **MemoSection**: title, content, contributing_personas, supporting_data
- **InvestmentMemo**: 5 MemoSection fields + `to_markdown()` renderer
- **Disagreement**: dimension, description, sides (persona/position/evidence_quote), resolution
- **DisagreementAnalysis**: list of Disagreements + `to_markdown()` renderer
- **DebateResult** extended with optional `memo` and `disagreement_analysis` fields

### New module: `src/tinyic/debate/memo.py`
- `generate_memo(debate_result, data_package)` → InvestmentMemo via LLM synthesis
- `extract_disagreements(debate_result)` → DisagreementAnalysis via LLM analysis
- Both use `client().send_message()` + `extract_json()` with graceful fallback
- Transcript truncation at 8000 chars (keeps end: cross-exam + verdict)
- Structured system prompts for grounded section-level output

### New tests: `tests/test_memo.py`
- TestMemoModels: 8 tests (model creation, to_markdown, empty grounding, DebateResult integration)
- TestMemoGeneration: 7 tests (success, grounding, prompt content, LLM failure, bad JSON)
- TestDisagreementExtraction: 5 tests (success, evidence, prompt content, failure modes)

### Updated: `src/tinyic/debate/__init__.py`
- Added exports: InvestmentMemo, MemoSection, DisagreementAnalysis, Disagreement, generate_memo, extract_disagreements

## Requirements covered
- OUTP-03: Full narrative investment memo with structured sections and section-level grounding
- OUTP-04: Cross-persona disagreement extraction with evidence quotes

## Test results
- 20 new tests passing
- 0 regressions in existing tests
