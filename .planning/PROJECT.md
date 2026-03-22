# openIC

## What This Is

An AI-powered investment committee simulator that lets users observe famous value investors debate the merits of any public company. Built as a fork of Microsoft's TinyTroupe framework, openIC creates convincing AI personas of legendary investors — each with a distinct philosophy distilled from their public writings — who analyze company data and argue toward a buy/hold/sell verdict. Users enter a ticker, watch the debate unfold, and receive a scorecard plus investment memo.

## Core Value

The investor personas must be convincingly distinct and philosophically accurate — each persona should argue from their real-world investment framework so the debate produces genuinely differentiated perspectives, not generic AI commentary.

## Requirements

### Validated

- [x] Fork and extend TinyTroupe as the multi-agent simulation foundation — *Validated in Phase 1: Foundation*
- [x] OpenAI API integration (GPT 5.2 extra high / Codex 5.3 extra high) — *Validated in Phase 1: Foundation*

### Validated (v1)

- [x] Create 6 investor personas: Warren Buffett, Charlie Munger, Benjamin Graham, Peter Lynch, Howard Marks, Li Lu — *Validated in Phase 2: Persona Engineering*
- [x] Distill each investor's philosophy from public writings (books, shareholder letters, interviews, speeches) into persona prompts — *Validated in Phase 2: Persona Engineering*
- [x] Auto-fetch company financials and data via yfinance, SEC EDGAR, and free news APIs — *Validated in Phase 3: Financial Data Pipeline*
- [x] Simulate a structured investment debate where personas discuss a user-provided company — *Validated in Phase 4: Debate Engine*
- [x] User can observe the debate in real-time and occasionally steer the conversation — *Validated in Phase 5: Streamlit UI*
- [x] Generate a scorecard with each persona's buy/hold/sell vote and reasoning — *Validated in Phase 4: Debate Engine*
- [x] Generate an investment memo summarizing thesis, risks, valuation, and final verdict — *Validated in Phase 4: Debate Engine (scorecard with votes, reasoning, and consensus; narrative memo deferred to v1.1)*
- [x] Streamlit web UI for the debate view, company input, and output display — *Validated in Phase 5: Streamlit UI*

### v1.1 (Active)

- [ ] Migrate deprecated edgartools and Pydantic APIs, fix test flakiness, expose cost stats
- [ ] Anti-convergence controls and rotating devil's advocate in cross-examination
- [ ] Full narrative investment memo with thesis, risks, valuation, verdict sections
- [ ] Cross-persona disagreement extraction and analysis
- [ ] Markdown/DOCX export for memo, scorecard, and transcript
- [ ] Company data sidebar visible during debate
- [ ] Per-debate cost display

### Out of Scope

- RAG pipeline for investor writings — v1 uses curated prompts, not dynamic retrieval
- Real-time market data streaming — batch fetch at debate start is sufficient
- Paid financial data APIs — v1 uses free sources only
- Mobile app — web-first via Streamlit
- User accounts / authentication — single-user tool for v1
- Portfolio tracking or trade execution — analysis only

## Context

- **TinyTroupe**: Microsoft's open-source multi-agent persona simulation framework. Provides the core agent/environment/interaction architecture. openIC forks this to customize for investment debate scenarios.
- **Investor panel**: 6 investors chosen to cover distinct philosophies — deep value (Graham), compounding/moats (Buffett/Munger), growth at reasonable price (Lynch), market cycles/risk (Marks), and emerging markets/China angle (Li Lu).
- **Philosophy distillation**: The most complex and critical phase. Each persona's prompt must encode their investment framework, decision heuristics, communication style, and known biases drawn from decades of public writings.
- **LLM backend**: Using OpenAI third-party API key. Target models are GPT 5.2 extra high or Codex 5.3 extra high for high-quality reasoning in financial analysis.

## Constraints

- **Tech stack**: Python, fork of TinyTroupe, Streamlit for UI — must stay compatible with TinyTroupe's architecture patterns
- **LLM provider**: OpenAI API only (GPT 5.2 extra high / Codex 5.3 extra high)
- **Data sources**: Free APIs only for v1 (yfinance, SEC EDGAR, free news APIs)
- **Persona accuracy**: Philosophy prompts must be grounded in real, attributable public writings — no invented positions

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Fork TinyTroupe (not dependency) | Need deep customization of persona/environment system for investment-specific debate flow | ✓ Done (Phase 1) |
| 6 investor personas for v1 | Covers major value investing schools without overscoping | ✓ Done (Phase 2) |
| Curated prompts over RAG | Simpler for v1, more controllable persona accuracy, avoids vector DB complexity | ✓ Done (Phase 2) |
| yfinance + free APIs for data | No cost barrier, sufficient for v1 analysis quality | ✓ Done (Phase 3) |
| Streamlit for UI | Matches TinyTroupe's existing pattern, fast to build, Python-native | ✓ Done (Phase 5) |
| Hardening before features in v1.1 | Fix deprecations and test flakiness before adding new capabilities | — v1.1 Phase 7 |
| Rotating devil's advocate over forced bears | Better realism — constrain dissent to cross-exam, keep final votes unconstrained | — v1.1 Phase 8 |
| Merge memo + export into one phase | No reason to split backend artifact generation from export wiring | — v1.1 Phase 9 |
| OpenAI GPT 5.2/Codex 5.3 | User's preferred models for high-quality financial reasoning | ✓ Done (Phase 1) |

---
*Last updated: 2026-03-22 — v1 milestone audit passed, all requirements validated*
