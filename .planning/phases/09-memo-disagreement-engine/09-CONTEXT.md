# Phase 9: Memo & Disagreement Engine - Context

**Phase:** 9
**Name:** Memo & Disagreement Engine
**Goal:** The system produces a publishable investment memo and a structured disagreement analysis from every completed debate

## Requirements

| ID | Description | Priority |
|----|-------------|----------|
| OUTP-03 | Full narrative investment memo with structured sections (Executive Summary, Investment Thesis, Key Risks, Valuation Discussion, Final Verdict) synthesized from debate transcript + scorecard + data package via LLM. Section-level grounding: each section references which personas contributed and which data points support claims. | MUST |
| OUTP-04 | Cross-persona disagreement extraction identifying top 3 dimensions where personas diverged most (e.g., valuation methodology, risk assessment, growth outlook) with evidence quotes from transcript | MUST |
| OUTP-05 | Export memo, scorecard, and transcript as Markdown (guaranteed) and DOCX (when pandoc available, graceful fallback to Markdown-only) | MUST |

## Success Criteria

1. After a debate completes, the system generates an `InvestmentMemo` with structured sections: Executive Summary, Investment Thesis, Key Risks, Valuation Discussion, Final Verdict -- each synthesized from debate transcript + scorecard + data package
2. The memo includes section-level grounding: each section references which personas contributed the underlying arguments and which data points support the claims
3. A `DisagreementAnalysis` object identifies the top 3 dimensions where personas diverged most (e.g., valuation methodology, risk assessment, growth outlook) with evidence quotes from the transcript
4. Both memo and scorecard can be exported as Markdown files (guaranteed) and DOCX files (when pandoc is available, with graceful fallback to Markdown-only)
5. Unit tests verify memo structure, disagreement extraction, and export formats without API calls

## Dependencies

- Phase 7 (Release Hardening) -- COMPLETE: clean test baseline, cost stats hook
- Phase 8 (Debate Quality Controls) -- COMPLETE: anti-convergence controls, better debate quality produces better memo source material

## Key Decisions

- Use `client().send_message()` for LLM synthesis (not ResultsExtractor, which is per-agent only)
- Use `tinytroupe.utils.extract_json()` for parsing structured LLM output
- Memo generation is a post-debate LLM call, separate from vote extraction
- Disagreement extraction is a separate LLM call analyzing the transcript for divergence dimensions
- Wrap ArtifactExporter for DOCX export; implement Markdown export directly (simpler, no dependency)
- DOCX export gated on pandoc availability with graceful fallback to Markdown-only
- New models (InvestmentMemo, DisagreementAnalysis) live in models.py alongside existing Vote/Scorecard
- New generation logic lives in memo.py (new module) alongside existing extraction.py
- Export logic lives in export.py (new module) to keep concerns separated
- DebateResult gains optional memo and disagreement_analysis fields

## Key Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Memo generation may hallucinate claims not in transcript | MODERATE | Require section-level grounding in prompt; include transcript + scorecard + data package as context |
| LLM synthesis call adds latency and cost post-debate | LOW | Memo generation is optional/async; cost tracked via existing OpenAIClient stats |
| Disagreement extraction may miss subtle philosophical differences | LOW | Focus on top 3 most prominent divergences; include evidence quotes for verification |
| DOCX export fails on systems without pandoc | LOW | Graceful fallback to Markdown-only; test both paths |
| Large transcripts may exceed context window | MODERATE | Truncate transcript to ~8K chars if needed; prioritize cross-exam and verdict phases |

## Architectural Context

### Files to Create
- `src/tinyic/debate/memo.py` -- MemoGenerator class (LLM synthesis for memo + disagreement)
- `src/tinyic/export.py` -- ExportManager for Markdown/DOCX export
- `tests/test_memo.py` -- Unit tests for memo generation and disagreement extraction
- `tests/test_export.py` -- Unit tests for export functionality

### Files to Modify
- `src/tinyic/debate/models.py` -- Add InvestmentMemo, MemoSection, DisagreementAnalysis, Disagreement models; extend DebateResult
- `src/tinyic/debate/__init__.py` -- Export new classes

### Key APIs (TinyTroupe)
- `client().send_message(messages)` -- Direct LLM call for synthesis
- `tinytroupe.utils.extract_json(text)` -- Parse JSON from LLM output
- `ArtifactExporter.export(name, data, content_type, content_format, target_format)` -- DOCX export
- `markdown.markdown(content)` -- Markdown to HTML conversion (used internally by ArtifactExporter)
- `pypandoc.convert_text(html, 'docx', format='html', outputfile=path)` -- HTML to DOCX

### Key APIs (openIC)
- `DebateResult` -- Contains transcript, scorecard, cost_stats; will gain memo and disagreement_analysis
- `Scorecard.to_markdown()` -- Existing markdown rendering for scorecard
- `DataPackage.to_context_string()` -- Financial data as context string
- `extract_votes(orchestrator)` -- Existing vote extraction pattern to follow
- `build_scorecard(votes, ticker, company_name)` -- Existing scorecard builder pattern

## Source Material
- Research: `.planning/phases/09-memo-disagreement-engine/09-RESEARCH.md`
- Roadmap: `.planning/ROADMAP.md` (Phase 9 section)
- Requirements: `.planning/REQUIREMENTS.md` (OUTP-03, OUTP-04, OUTP-05)
