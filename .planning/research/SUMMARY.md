# Research Summary: openIC

**Domain:** AI-powered multi-agent investment committee simulation
**Researched:** 2026-03-20
**Overall confidence:** HIGH

## Executive Summary

openIC is an AI investment committee simulator built on Microsoft's TinyTroupe persona simulation framework, using OpenAI GPT-5.2 for inference, Streamlit for the UI, and free financial data from yfinance and SEC EDGAR. The 2026 Python ecosystem provides mature, well-maintained libraries for every component, and the project's constraints (OpenAI-only, free data, Streamlit UI, TinyTroupe fork) eliminate most technology selection ambiguity. The recommended stack -- Python 3.12, uv, TinyTroupe 0.6.0 fork, openai SDK, Streamlit 1.55.0, yfinance 1.2.0, edgartools -- is well-tested and compatible. The one unresolved technical risk is TinyTroupe's compatibility with OpenAI SDK v2.x (TinyTroupe pins >=1.65, current SDK is 2.29.0), which must be validated hands-on in the first development phase.

The product's success depends far more on prompt craft than on code. Research across all four domains converges on one conclusion: **persona differentiation is the make-or-break challenge**. Peer-reviewed studies demonstrate that LLM agents systematically converge toward base model biases, abandon assigned personas over multi-turn conversations, and exhibit confirmation bias in financial analysis. All six investor personas sounding like the same generic financial analyst is the single most likely failure mode, and it would destroy the product's entire value proposition. This means persona engineering -- distilling Buffett's moat analysis, Graham's margin of safety, Marks' second-level thinking, Lynch's consumer intuition, Munger's mental models, and Li Lu's emerging-market lens into robustly differentiated prompts -- must be treated as the project's highest-priority, most time-consuming work, validated before the debate engine is built.

The architecture follows a clean layered pattern: a financial data pipeline fetches and normalizes company data into a DataPackage, investor personas (TinyPerson subclasses) receive this data as shared context, a debate orchestrator (TinyWorld subclass) manages structured phases (opening, cross-examination, rebuttal, verdict), and extraction components produce scorecards and investment memos from the debate transcript. The key architectural decisions -- pre-fetching data rather than letting agents query it, explicit phase orchestration rather than free-form simulation, persona configs as JSON files rather than inline code, and event-driven UI updates decoupled from the debate engine -- are all well-supported by TinyTroupe's documented extension model and domain best practices. Cost management (token budgets, prompt caching, conversation summarization) must be designed in from the start, as 6 agents across multiple rounds can generate 18-30+ API calls per debate at $3-8 each.

## Key Findings

### From STACK.md

- **Python 3.12** -- safe compatibility sweet spot for TinyTroupe (>=3.10), Streamlit (>=3.10), and all financial libraries
- **uv** -- consensus package manager for new Python projects in 2026; replaces pip/poetry with lockfile support and 10-100x speed
- **TinyTroupe 0.6.0 fork** -- latest release (Feb 2026), provides TinyPerson/TinyWorld persona simulation. Maintain as local package in monorepo, not git submodule
- **OpenAI GPT-5.2** with reasoning effort parameter (`xhigh`) -- $1.75/1M input, $14/1M output. Use gpt-5.2-mini for dev/testing
- **Streamlit 1.55.0** -- chat components (`st.chat_message`, `st.write_stream`) are a natural fit for debate display
- **yfinance 1.2.0** -- free fundamentals, price data, basic news; start with `Ticker.news` before adding separate news API
- **edgartools** -- free SEC EDGAR parsing with typed Python objects and XBRL normalization; superior to raw XBRL parsing
- **Critical version risk:** TinyTroupe pins openai >=1.65, but current SDK is 2.29.0 (v2.x). Must verify fork compatibility early
- **Codex 5.3 is not appropriate** -- it is coding-specialized, not suited for financial analysis. Use gpt-5.2 for all persona reasoning

### From FEATURES.md

**Table stakes (must ship):**
1. Ticker input and company identification
2. Financial data fetching (fundamentals, price, ratios)
3. 6 distinct investor personas with differentiated philosophies
4. Structured multi-round debate
5. Real-time debate display via Streamlit chat elements
6. Buy/Hold/Sell verdict per persona
7. Scorecard summarizing all verdicts
8. Investment memo generation

**Differentiators (should ship):**
1. Philosophically grounded personas (not shallow role prompts) -- this is the core differentiator
2. User steering mid-debate (inject questions/topics)
3. Cross-persona disagreement highlighting
4. Company data context panel (sidebar)
5. Configurable debate parameters (rounds, persona selection)
6. Debate transcript export (Markdown/PDF)

**Anti-features (explicitly defer or never build):**
- Trade execution, backtesting, paid data APIs, RAG over investor writings, real-time streaming, user accounts, mobile app, custom persona creation, multi-company comparison, social media sentiment

**Cost estimate:** $3-8 per debate session (6 agents x multiple rounds x GPT-5.2). Persona philosophy distillation is the longest dev effort at 2-3 weeks.

### From ARCHITECTURE.md

- **5-layer architecture:** Presentation (Streamlit) > Orchestration (DebateOrchestrator extending TinyWorld) > Agent (InvestorPersona extending TinyPerson) + Extraction (VoteExtractor, MemoGenerator) > Financial Data (CompanyDataPipeline) > Infrastructure (OpenAI client, TinyTroupe core)
- **DataPackage pattern:** All financial data fetched once, normalized into a single structured bundle, injected into all agents at debate start. No agent-driven data fetching
- **Phased debate:** Opening statements > Cross-examination (N rounds) > Rebuttal > Final verdict. Orchestrator controls turn order and phase transitions
- **Event-driven UI:** Debate emits events (agent_spoke, phase_changed, vote_cast) that Streamlit subscribes to via session_state
- **Persona configs as JSON:** Follows TinyTroupe's `.agent.json` pattern. Version-controllable, reviewable by domain experts
- **Directory structure:** `tinytroupe/` (fork), `openic/` (personas, orchestrator, data, extraction), `app/` (Streamlit UI), `tests/`
- **Key anti-patterns to avoid:** Modifying TinyTroupe core directly (use subclassing), agents fetching own data, free-form simulation without phase structure, inline persona prompts, monolithic Streamlit file

### From PITFALLS.md

**Critical (6):**

| # | Pitfall | Prevention |
|---|---------|------------|
| 1 | Persona convergence -- all investors sound the same | Contrastive prompting, differentiation test suite, anti-persona instructions |
| 2 | Persona drift over debate rounds (attention decay) | System prompt re-injection each turn, 3-4 rounds max, separate LLM contexts per agent |
| 3 | LLM confirmation bias in financial analysis | Structural bear-case roles, blind data presentation, independent pre-debate analysis |
| 4 | Error cascading in multi-agent debate | Fact-checking layer, moderator agent, grounded citations verified against source data |
| 5 | Financial data unreliability from free sources | Data validation layer with completeness thresholds, fallback sources, aggressive caching |
| 6 | TinyTroupe fork maintenance burden | Extend via subclassing, minimize core edits, pin to v0.6.0, integration test suite |

**Moderate (5):**

| # | Pitfall | Prevention |
|---|---------|------------|
| 7 | API cost explosion from multi-agent conversations | Token budgets, prompt caching (10% cost), round summaries instead of full transcripts |
| 8 | Groupthink and unanimous verdicts | Structural dissent requirement, devil's advocate rotation, pre/post debate voting |
| 9 | Streamlit rerun architecture vs. long-running debates | Background thread for debate engine, state checkpointing, `st.cache_resource` |
| 10 | Ethical/legal risk of impersonating real public figures | Prominent disclaimers, consider philosophy-based names ("The Moat Builder"), no financial advice claims |
| 11 | LLM hallucination of financial "facts" | Constrain to provided data only, post-process numerical claim verification |

**Minor (4):** XBRL taxonomy inconsistency, overwhelming transcript length, OpenAI model naming instability, over-engineering debate before validating personas.

**Key ordering insight from pitfalls research:** Validate persona differentiation before building anything else. Pitfalls 1, 2, 3, and 8 all stem from LLMs converging to generic output. If personas are not convincingly distinct in isolation, no amount of debate infrastructure will save the product.

## Implications for Roadmap

### Suggested Phase Structure

**Phase 1: Foundation and Validation**
- Fork TinyTroupe v0.6.0, validate OpenAI SDK v2.x compatibility, set up project with uv + Python 3.12
- Configure model registry (abstract model names behind config), set up .env for API keys
- Validate that TinyPerson subclassing works as documented -- create a minimal InvestorPersona that can listen() and act()
- Decide persona naming strategy (real names with disclaimers vs. philosophy-based names) to resolve ethical/legal risk early
- **Delivers:** Working TinyTroupe fork with GPT-5.2, validated extension pattern, project skeleton
- **Pitfalls to avoid:** SDK incompatibility (5.6), model naming instability (14), ethical naming risk (10)
- **Research flag:** Needs `/gsd:research-phase` if SDK v2.x proves incompatible -- would need to investigate specific breaking changes and patching strategy

**Phase 2: Persona Engineering**
- Craft 6 investor persona JSON configs with deep philosophy distillation from public writings
- Build persona differentiation test suite: 5-10 canonical test cases per persona where the "right" answer differs by philosophy
- Test each persona in isolation (give financial data, get analysis) before any multi-agent interaction
- Implement contrastive prompting (anti-persona instructions, distinctive vocabulary, known biases)
- Build the CompanyDataPipeline + DataPackage in parallel (data fetching is independent of persona work)
- Implement data validation layer with completeness thresholds and fallback sources
- **Delivers:** 6 differentiated personas passing automated distinctiveness tests, reliable financial data pipeline
- **Pitfalls to avoid:** Persona convergence (1), over-engineering before validation (15), data unreliability (5), XBRL inconsistency (12)
- **Research flag:** Needs extensive domain research (Buffett's shareholder letters, Graham's Security Analysis, Marks' memos, Lynch's books). This is content research, not tech research. Likely needs `/gsd:research-phase` for persona prompt crafting methodology

**Phase 3: Debate Engine**
- Build DebateOrchestrator (TinyWorld subclass) with explicit phase management: opening, cross-examination, rebuttal, verdict
- Implement persona drift mitigation: system prompt re-injection each turn, 3-4 round limit, conversation summarization
- Implement structural dissent: at least 1-2 agents incentivized to argue the bear case
- Add fact-checking layer: verify numerical claims against source DataPackage between rounds
- Implement token budget tracking with per-debate cost estimation and hard limits
- Test end-to-end in terminal/notebook (no UI yet)
- **Delivers:** Complete debate running in CLI producing structured transcripts, votes, and cost reports
- **Pitfalls to avoid:** Persona drift (2), confirmation bias (3), error cascading (4), groupthink (8), hallucination (11), cost explosion (7)
- **Research flag:** Debate orchestration on TinyWorld is novel -- no existing examples of phased debate. May need `/gsd:research-phase` for TinyTroupe interaction loop customization

**Phase 4: Extraction and Output**
- Build VoteExtractor (extends ResultsExtractor) to pull structured buy/hold/sell votes from agent memory
- Build ScorecardBuilder to aggregate votes into comparison matrix
- Build MemoGenerator to synthesize debate into investment memo (exec summary, bull/bear case, risks, valuation, verdict)
- Add cross-persona disagreement highlighting as post-debate analysis
- **Delivers:** Structured scorecard and downloadable investment memo from any completed debate
- **Pitfalls to avoid:** Hallucination in memo (11) -- memo must cite only debate-sourced claims
- **Research flag:** Standard patterns -- TinyTroupe's ResultsExtractor is well-documented. Skip additional research

**Phase 5: Streamlit UI**
- Build ticker input with company resolution and validation
- Implement real-time debate display with `st.chat_message` and streaming
- Run debate engine in background thread/process decoupled from Streamlit rerun cycle
- Build company data context panel (sidebar with key financials, price chart)
- Render scorecard and memo with download buttons (Markdown/PDF)
- Add user steering via `st.chat_input` injecting moderator questions into active debate
- Add configurable debate parameters (rounds, persona selection)
- **Delivers:** Complete user journey from ticker input to debate observation to scorecard/memo download
- **Pitfalls to avoid:** Streamlit rerun conflicts (9), overwhelming transcript length (13)
- **Research flag:** Standard Streamlit patterns. Skip additional research unless background process integration proves tricky

**Phase 6: Polish and Hardening**
- End-to-end integration testing across diverse tickers (large-cap, small-cap, international, edge cases)
- Cost tracking display (show estimated and actual cost per debate)
- Error handling for data gaps, rate limits, API failures
- Response length constraints to prevent transcript bloat
- Disclaimers, about page, usage documentation
- Performance optimization (prompt caching, data caching with TTL)
- **Delivers:** Production-quality single-user tool ready for sharing
- **Pitfalls to avoid:** All minor pitfalls addressed here as final sweep

### Phase Ordering Rationale

The ordering is strictly driven by dependencies and risk:

1. **Foundation first** because everything depends on the TinyTroupe fork working with GPT-5.2. If SDK compatibility fails, the entire approach needs rethinking.
2. **Personas before debate engine** because the pitfalls research is unambiguous: validating persona differentiation in isolation before building multi-agent infrastructure is the single most important phase-ordering decision. Building the debate system on undifferentiated personas wastes all downstream effort.
3. **Data pipeline parallel with personas** because data fetching has no dependency on persona work, and the debate engine needs both.
4. **Debate engine after personas and data** because it consumes both as inputs.
5. **Extraction after debate** because it processes debate output.
6. **UI last** because Streamlit is fast to build and benefits from having real debate output to display. Every preceding layer can be tested in notebooks/CLI.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified against PyPI/GitHub (March 2026). Clear, unambiguous choices driven by project constraints. |
| Features | HIGH | Feature landscape well-scoped by PROJECT.md. Table stakes vs. differentiators are clear. Competitive landscape mapped. |
| Architecture | HIGH | TinyTroupe's TinyPerson/TinyWorld map directly to investor personas and debate environments. Extension points are documented. |
| Pitfalls | HIGH | Critical pitfalls backed by peer-reviewed research (EMNLP 2024, multiple 2025 papers). Financial-domain-specific risks well-documented. |
| OpenAI SDK compatibility | MEDIUM | TinyTroupe pins openai >=1.65 but current is 2.29.0 (v2.x). Cannot confirm without hands-on testing. Phase 1 blocker. |
| GPT-5.2 persona quality | MEDIUM | Model confirmed, but persona differentiation under `xhigh` reasoning effort for financial debate is unverified. Requires empirical validation. |
| Debate orchestration pattern | MEDIUM | TinyWorld subclassing is documented, but phased debate management is novel -- no existing implementations found. Will need iteration. |
| Streamlit + long-running debate | MEDIUM | Chat components are mature, but decoupling a multi-minute background debate from Streamlit's rerun model needs careful engineering. |

### Gaps to Address During Planning

1. **OpenAI SDK v1 vs v2 in TinyTroupe fork:** Cannot resolve via research. Inspect `openai_utils.py` (or equivalent) in TinyTroupe source during Phase 1. If v2.x breaks the fork, options include: (a) pin to latest openai 1.x, (b) patch the fork's client initialization, or (c) use TinyTroupe's config system to swap clients.

2. **TinyTroupe streaming to Streamlit:** TinyTroupe collects complete LLM responses by default. Whether token-level streaming can be surfaced through the fork to Streamlit's `st.write_stream` needs code-level investigation. May require modifying the fork's LLM client wrapper.

3. **Persona prompt quality:** Technology research cannot substitute for reading Buffett's shareholder letters, Graham's Security Analysis, Marks' memos, and Lynch's books. Phase 2 requires genuine domain expertise in value investing philosophy.

4. **Empirical cost modeling:** Per-debate costs depend on prompt size, response length, round count, and model tier. Need to run real debates during Phase 3 to establish baselines and calibrate token budgets.

5. **edgartools version pinning:** Library is actively maintained (last release Mar 13, 2026) but exact version was not confirmed. Pin after initial installation.

## Sources

### Core Framework
- [TinyTroupe GitHub](https://github.com/microsoft/TinyTroupe) -- v0.6.0 (Feb 2, 2026)
- [TinyTroupe API Docs](https://microsoft.github.io/TinyTroupe/api/tinytroupe/index.html)
- [TinyTroupe Wiki: Principles and Mechanisms](https://github.com/microsoft/TinyTroupe/wiki/Principles-and-mechanisms)
- [TinyTroupe Paper (arXiv)](https://arxiv.org/html/2507.09788v1)

### Libraries and Tools
- [OpenAI Python SDK](https://pypi.org/project/openai/) -- v2.29.0
- [GPT-5.2 Model Docs](https://developers.openai.com/api/docs/models/gpt-5.2)
- [GPT-5.2 Pricing](https://developers.openai.com/api/docs/pricing)
- [Streamlit](https://pypi.org/project/streamlit/) -- v1.55.0
- [Streamlit Chat Elements](https://docs.streamlit.io/develop/api-reference/chat)
- [yfinance](https://pypi.org/project/yfinance/) -- v1.2.0
- [edgartools](https://github.com/dgunning/edgartools)
- [uv Package Manager](https://docs.astral.sh/uv/)

### Competitive Landscape
- [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) -- 12 investor persona agents
- [TradingAgents Framework](https://tradingagents-ai.github.io/) -- bull/bear debate mechanism
- [AI Hedge Fund ecosystem overview](https://yuv.ai/blog/ai-hedge-fund-when-multiple-ai-agents-become-your-investment-committee)

### Pitfall Research (Peer-Reviewed)
- [Systematic Biases in LLM Simulations of Debates](https://arxiv.org/abs/2402.04049) -- Chuang et al., EMNLP 2024
- [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657) -- Cemri et al., 2025
- [Measuring and Controlling Persona Drift](https://arxiv.org/html/2402.10962v1) -- Zheng et al., 2024
- [Your AI, Not Your View: Bias in LLM Investment Analysis](https://arxiv.org/abs/2507.20957) -- Lee & Seo, 2025
- [FAITH: Tabular Hallucinations in Finance](https://arxiv.org/abs/2508.05201) -- 2025
- [Tipping the Balance: Human Intervention in LLM Debate](https://asistdl.onlinelibrary.wiley.com/doi/10.1002/pra2.1034)
