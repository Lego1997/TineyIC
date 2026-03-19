# Domain Pitfalls

**Domain:** AI-powered investment committee simulation (multi-agent persona debate with financial data)
**Researched:** 2026-03-20

---

## Critical Pitfalls

Mistakes that cause rewrites, credibility failures, or fundamental architectural problems.

---

### Pitfall 1: Persona Convergence — All Investors Sound the Same

**What goes wrong:** Despite detailed persona prompts, all six investor agents converge to a generic "wise financial analyst" voice. Buffett and Graham produce near-identical arguments. The debate feels like one person talking to themselves. The core value proposition -- distinctly differentiated investment philosophies in genuine tension -- collapses.

**Why it happens:** LLMs have strong base model biases that override persona instructions. Research on systematic biases in LLM debate simulations (Chuang et al., EMNLP 2024) demonstrates that "LLM agents generally conform to the inherent social biases of their base models, even if these biases conflict with their assigned identities." Partisan agents shift toward the model's default stance rather than maintaining opposing positions. In an investment context, all agents will drift toward whatever the LLM considers "good investment analysis" -- likely a blend of mainstream value/growth thinking that erases the distinctive edges of each persona.

**Consequences:** The product's entire value proposition fails. Users see six agents agreeing politely rather than six distinct philosophies in productive conflict. Howard Marks' focus on second-level thinking and market cycles becomes indistinguishable from Peter Lynch's "invest in what you know" approach.

**Warning signs:**
- Unanimous or near-unanimous buy/hold/sell votes in early testing
- Persona arguments that could be swapped between investors without anyone noticing
- All agents citing similar reasoning patterns regardless of their assigned philosophy
- Absence of genuine disagreement or contrarian positions

**Prevention:**
1. Build a "persona differentiation test suite" before any other feature. For each persona, define 5-10 canonical positions (e.g., Buffett should reject a high-growth tech startup with no earnings; Lynch should be intrigued by it). Run automated tests that verify differentiation.
2. Use contrastive persona prompting: explicitly state what each investor would NOT say and how they differ from the other panelists. Include "anti-persona" instructions (e.g., "Unlike Peter Lynch, you are deeply skeptical of companies you encounter as a consumer").
3. Inject periodic "persona anchoring" by re-emphasizing the system prompt mid-debate. Research shows attention to system prompts decays sharply between conversation turns (Zheng et al., 2024).
4. Include each investor's distinctive vocabulary and rhetorical patterns, not just their philosophy. Munger's acerbic one-liners are as distinctive as his mental models framework.

**Detection:** Create a blind test: show debate excerpts to someone familiar with these investors and ask them to identify which persona is speaking. If accuracy is near chance (1/6), convergence has occurred.

**Phase relevance:** Must be addressed in the persona creation phase (Phase 2). If convergence is not solved before building the debate system, the entire downstream pipeline produces undifferentiated output.

**Confidence:** HIGH -- supported by peer-reviewed research on LLM debate bias and persona drift.

**Sources:**
- [Systematic Biases in LLM Simulations of Debates](https://arxiv.org/abs/2402.04049) (EMNLP 2024)
- [Measuring and Controlling Persona Drift in Language Model Dialogs](https://arxiv.org/html/2402.10962v1)
- [Your AI, Not Your View: The Bias of LLMs in Investment Analysis](https://arxiv.org/abs/2507.20957)

---

### Pitfall 2: Persona Drift Over the Course of a Debate

**What goes wrong:** Even if personas start differentiated, they degrade over a multi-turn debate. By round 5-8 of discussion, agents have adopted each other's positions, abandoned their distinctive frameworks, or reverted to a generic analysis voice.

**Why it happens:** Attention decay. Research demonstrates "significant persona drift within eight rounds of conversations" in LLaMA2-chat-70B (Zheng et al., 2024). The model allocates diminishing attention weights to system prompt tokens as conversation context grows. Critically, agents don't just lose their persona -- they actively adopt the personas of other agents they are conversing with, as user messages (including other agents' arguments) dilute the system prompt's influence through expanding cone geometry in embedding space.

**Consequences:** A 6-round investment debate starts with sharp disagreements and ends with bland consensus. The scorecard and investment memo reflect homogenized thinking rather than the genuine multi-perspective analysis that is the product's purpose.

**Warning signs:**
- Debate transcripts where the first 2 rounds are interesting and the last 3 are repetitive agreement
- Agents quoting reasoning patterns from other agents' philosophies
- Distinctiveness scores (measured by semantic similarity between agents) declining across debate rounds

**Prevention:**
1. Re-inject the full persona system prompt at each agent's turn, not just at the start. This consumes tokens but is the most reliable mitigation.
2. Keep debates short: 3-4 focused rounds rather than 8-10 meandering ones. Diminishing returns set in fast.
3. Structure debate rounds with explicit phase transitions (e.g., "Now evaluate from your framework's perspective on valuation" vs. "Now respond to criticisms"). Directed prompts re-anchor persona identity.
4. After each agent generates a response, run a lightweight "persona consistency check" -- does this response align with the persona's known framework? If not, regenerate.
5. Consider using separate LLM contexts per agent rather than a shared conversation context, feeding only summaries of other agents' positions rather than full text. This prevents context pollution.

**Detection:** Track semantic similarity between each agent's responses across debate rounds. Plot distinctiveness over time. A downward trend signals drift.

**Phase relevance:** Debate orchestration phase (Phase 3). The debate structure must be designed with drift mitigation built in from the start, not bolted on later.

**Confidence:** HIGH -- quantitatively demonstrated in multiple research papers.

**Sources:**
- [Measuring and Controlling Persona Drift in Language Model Dialogs](https://arxiv.org/html/2402.10962v1)
- [Consistently Simulating Human Personas with Multi-Turn Reinforcement Learning](https://arxiv.org/html/2511.00222v1)

---

### Pitfall 3: LLM Confirmation Bias in Financial Analysis

**What goes wrong:** When presented with company financial data, the LLM exhibits strong confirmation bias -- clinging to its initial assessment regardless of contradicting evidence presented during debate. All agents may anchor on the same initial impression of a company (e.g., "AAPL is a great company") and no amount of contrarian argument shakes it.

**Why it happens:** Research specifically on LLM investment analysis (Lee & Seo, 2025) demonstrates that "when an LLM encounters both supporting evidence and counter-evidence simultaneously, it exhibits a strong confirmation bias, stubbornly adhering to the evidence that confirms its internal knowledge while disregarding counter-evidence." LLMs also show systematic preference for technology stocks, large-cap stocks, and contrarian strategies that persist across models.

**Consequences:** The debate degenerates into six agents agreeing that a well-known company is great (or terrible), with no genuine adversarial analysis. The investment memo lacks the intellectual honesty of a real investment committee. For well-known companies (AAPL, TSLA, GOOG), the LLM's strong prior overwhelms any persona-specific analysis.

**Warning signs:**
- All six investors reaching the same verdict on well-known companies
- Agents acknowledging risks superficially but concluding with the same "buy" recommendation regardless
- Debate on popular companies producing predictably positive outcomes
- Bear cases feeling perfunctory or hedged

**Prevention:**
1. Assign explicit debate roles: at least 1-2 agents should be structurally incentivized to argue the bear case (e.g., "Your job is to find the strongest reasons NOT to invest, even if you ultimately recommend buying"). This mimics real investment committee practice.
2. Feed financial data without company name/ticker first, then reveal identity. This reduces anchoring on brand recognition.
3. Separate the "data analysis" step from the "debate" step. Have agents analyze financials independently before seeing each other's positions, reducing groupthink.
4. Explicitly prompt agents with counter-evidence: "Given that [negative data point], how does your investment framework address this risk?"

**Detection:** Run the same company through the debate multiple times with different data presentations. If the verdict is identical every time regardless of framing, confirmation bias is dominant.

**Phase relevance:** Both persona creation (Phase 2) and debate orchestration (Phase 3). Persona prompts must include each investor's known biases and blind spots. Debate structure must enforce genuine adversarial analysis.

**Confidence:** HIGH -- peer-reviewed research specific to LLM investment analysis.

**Sources:**
- [Your AI, Not Your View: The Bias of LLMs in Investment Analysis](https://arxiv.org/abs/2507.20957)

---

### Pitfall 4: Error Cascading in Multi-Agent Debate Pipeline

**What goes wrong:** A single bad output from one agent early in the debate cascade through the entire system. If the first speaker makes a factual error about the company's financials, subsequent agents incorporate that error into their analysis rather than correcting it. The final memo and scorecard are built on compounded errors.

**Why it happens:** Research on multi-agent LLM system failures identifies 14 distinct failure modes across 3 categories (Cemri et al., 2025). Among the most frequent: "No or Incomplete Verification" (FM-3.2) and "Incorrect Verification" (FM-3.3). Agents tend to echo and validate each other's mistakes rather than catching them -- creating "hallucination loops." The "Bag of Agents" anti-pattern (flat topology with no verification hierarchy) amplifies errors 17x compared to structured approaches.

**Consequences:** The investment memo contains fabricated or incorrect financial data. Users relying on the analysis are misled. A single hallucinated P/E ratio can invalidate the entire debate. This is especially dangerous in a financial context where numerical accuracy is paramount.

**Warning signs:**
- Agents referencing financial metrics that don't match the input data
- Later agents in the debate repeating incorrect claims from earlier agents
- Investment memo containing numbers not present in the source data
- Unanimous agreement based on shared incorrect premises

**Prevention:**
1. Implement a "fact-checking layer" between debate rounds. After each agent speaks, verify any numerical claims against the source financial data before passing the response to the next agent.
2. Design hierarchical debate topology, not flat. Have a moderator/orchestrator agent that validates factual claims and can interrupt to correct errors.
3. Ground every financial claim in explicit data references: agents must cite specific data points from the provided financials, and these citations are mechanically verified.
4. Add a "Challenger" or "Inspector" agent (research shows this recovers up to 96% of lost performance from faulty agents).

**Detection:** Compare financial figures mentioned in debate transcripts against the actual data provided. Any divergence signals hallucination cascading.

**Phase relevance:** Debate orchestration phase (Phase 3) and data integration phase (Phase 2). The data pipeline must provide structured, verifiable financial data, and the debate system must mechanically verify references to it.

**Confidence:** HIGH -- supported by systematic research on multi-agent failure modes.

**Sources:**
- [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657)
- [Why Your Multi-Agent System is Failing: Escaping the 17x Error Trap](https://towardsdatascience.com/why-your-multi-agent-system-is-failing-escaping-the-17x-error-trap-of-the-bag-of-agents/)

---

### Pitfall 5: Financial Data Unreliability from Free Sources

**What goes wrong:** yfinance returns incomplete, incorrect, or silently broken data. Key financial ratios disappear without warning. SEC EDGAR XBRL data has inconsistent naming conventions across companies. The debate is built on a foundation of unreliable financial data, and agents either hallucinate to fill gaps or produce analysis based on wrong numbers.

**Why it happens:** yfinance is a scraper, not an API -- Yahoo can change their site structure at any time, breaking data retrieval silently. Documented issues include: pegRatio field returning None since June 2025 (despite data existing on the website), financials/balance_sheet/cashflow methods returning empty DataFrames for some tickers, Open/High/Low/Close values being identical on trading days for certain markets, and aggressive rate limiting returning "Too Many Requests" errors. SEC EDGAR XBRL uses non-standard financial statement names across companies (e.g., "ConsolidatedStatementsofOperations" vs "ConsolidatedStatementsOfLossIncome"), fiscal year start/end dates vary by company, and rate limits are strict (10 requests/second with mandatory User-Agent header).

**Consequences:** The analysis is only as good as the data feeding it. Missing P/E ratios, empty cash flow statements, or stale prices undermine every downstream persona analysis. Users lose trust when they can verify the numbers are wrong by checking Yahoo Finance directly.

**Warning signs:**
- Empty DataFrames or None values in financial data retrieval
- Rate limit errors during data fetching
- Financial ratios that don't match what users see on Yahoo Finance's website
- Inconsistent data availability across different tickers

**Prevention:**
1. Build a robust data validation layer that checks for completeness before starting any debate. Define a minimum data quality threshold (e.g., must have revenue, net income, total assets, stock price, P/E ratio). If data is insufficient, tell the user rather than proceeding with gaps.
2. Implement fallback data sources: try yfinance first, fall back to SEC EDGAR, and consider Financial Modeling Prep's free tier as a third fallback.
3. Cache financial data aggressively -- fetch once at debate start, validate, and serve from cache throughout the debate.
4. Handle rate limiting with exponential backoff and request queuing. Set a User-Agent header for SEC EDGAR (required by their API).
5. For SEC EDGAR XBRL parsing, use the `edgartools` library which handles taxonomy normalization, or the SEC's companion JSON API (companyfacts endpoint) which is simpler than raw XBRL.
6. Present financial data to users alongside the debate so they can verify numbers.

**Detection:** Automated data quality checks: count of None/NaN fields, comparison with a known-good snapshot, schema validation against expected financial statement structure.

**Phase relevance:** Data integration phase (Phase 2). This must be rock-solid before building the debate system. A debate on bad data is worse than no debate.

**Confidence:** HIGH -- documented in yfinance GitHub issues and SEC EDGAR documentation.

**Sources:**
- [yfinance pegRatio missing since June 2025](https://github.com/ranaroussi/yfinance/issues/2570)
- [yfinance rate limiting issue](https://github.com/ranaroussi/yfinance/issues/2128)
- [SEC EDGAR API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [EdgarTools library](https://github.com/dgunning/edgartools)

---

### Pitfall 6: TinyTroupe Fork Maintenance Burden and Breaking Changes

**What goes wrong:** Forking TinyTroupe creates an immediate maintenance burden. The upstream project is experimental and actively changing -- the v0.5.2 release changed the default model and introduced "considerable quality improvements" with "significant behavioral differences." Your fork diverges from upstream, making it increasingly difficult to incorporate improvements, bug fixes, or new features from Microsoft's ongoing development.

**Why it happens:** TinyTroupe is explicitly labeled "experimental" with "many functions, capabilities, and APIs that can change in the future." The project has made breaking changes between versions (cognitive_state key errors, model default changes, config refactoring). A fork of an experimental project inherits all the instability without the community momentum to address it.

**Consequences:** You either spend significant time tracking and merging upstream changes, or your fork stagnates on a version that accumulates known bugs without fixes. Features you build on top of TinyTroupe's internals may break when you try to merge upstream improvements.

**Warning signs:**
- Upstream releases that break your customizations
- Growing git diff between your fork and upstream main
- Features you need that exist upstream but can't be cleanly merged
- Spending more time on framework maintenance than product features

**Prevention:**
1. Minimize modifications to TinyTroupe's core files. Extend through subclassing and composition, not by editing source files directly. Keep your changes in separate modules that import from TinyTroupe.
2. Use TinyTroupe as a pinned dependency rather than a fork if possible. Only fork if you genuinely need to modify internals that cannot be extended through the public API.
3. If you must fork, maintain a clear separation: keep upstream code in one directory structure and your extensions in another. Document every modification to core files.
4. Pin to a specific TinyTroupe release (v0.5.2 or whatever is current). Don't track main branch.
5. Write integration tests that exercise TinyTroupe's API surface you depend on. When considering an upstream merge, run these tests first to detect breaking changes.

**Detection:** Track upstream releases. When a new version drops, assess the diff against your fork before deciding to merge.

**Phase relevance:** Phase 1 (project setup/foundation). The forking strategy must be decided before any other work begins, as it affects the entire codebase structure.

**Confidence:** HIGH -- based on TinyTroupe's documented experimental status and observed breaking changes in release history.

**Sources:**
- [TinyTroupe GitHub releases](https://github.com/microsoft/TinyTroupe/releases)
- [TinyTroupe Responsible AI FAQ](https://github.com/microsoft/TinyTroupe/blob/main/RESPONSIBLE_AI_FAQ.md)
- [Ollama integration PR issues](https://github.com/microsoft/TinyTroupe/pull/47)

---

## Moderate Pitfalls

---

### Pitfall 7: API Cost Explosion from Multi-Agent Conversations

**What goes wrong:** A single debate with 6 agents over 4 rounds, each receiving full context of all previous statements plus financial data, quickly consumes enormous token counts. With GPT 5.2 "extra high" tier pricing, costs per debate become unsustainable for regular use.

**Why it happens:** Multi-agent systems have multiplicative token costs. Each agent needs: system prompt (~2K tokens) + financial data context (~3-5K tokens) + all previous debate statements (growing per round). For 6 agents over 4 rounds, you're looking at roughly 24 LLM calls, each with an increasingly large context. Token costs compound because each subsequent round includes all prior output as input context. TinyTroupe's existing architecture was flagged for this: "API costs could quickly become problematic due to the amount of interaction between simulated people."

**Prevention:**
1. Use prompt caching (OpenAI supports cached prefixes at 10% cost). Structure prompts so the financial data and persona system prompts are stable prefixes that get cached.
2. Summarize previous debate rounds rather than passing full transcripts. Each agent receives a compressed summary of prior discussion, not verbatim quotes.
3. Implement a token budget per debate with hard limits. Show users estimated cost before running.
4. Consider a tiered approach: use a smaller/cheaper model for preliminary analysis and reserve the expensive model for the final synthesis and memo generation.
5. Batch API usage where possible (50% cost reduction, though slower turnaround).

**Detection:** Log token usage per debate and alert if costs exceed thresholds. Track cost-per-debate over time.

**Phase relevance:** Debate orchestration phase (Phase 3) and initial architecture design (Phase 1). Token economics must be designed into the system from the start.

**Confidence:** HIGH -- based on OpenAI pricing documentation and TinyTroupe community feedback.

---

### Pitfall 8: Groupthink and Unanimous Verdicts

**What goes wrong:** The investment committee simulation produces unanimous or near-unanimous verdicts far more often than a real investment committee would. The debate feels like a formality leading to a predetermined conclusion rather than genuine deliberation.

**Why it happens:** Research on LLM-simulated monetary policy committees found that "when an investment committee converges through debate, outcomes cluster tightly and no dissents occur." LLMs are trained to be agreeable and helpful, which translates to agents that accommodate each other's positions rather than maintaining genuinely adversarial stances. Combined with confirmation bias (Pitfall 3) and persona convergence (Pitfall 1), the structural incentives all push toward consensus.

**Prevention:**
1. Structurally require dissent: at least one agent must argue the opposite position of the majority. Assign a "devil's advocate" role that rotates.
2. Score debates on disagreement diversity, not just quality. A debate where all 6 agree is a bad debate. Build this metric into your evaluation framework.
3. Have agents vote independently BEFORE the debate, then debate, then vote again. Compare pre/post to ensure the debate actually changed minds rather than just confirming an initial consensus.
4. Calibrate against known historical cases: run the debate on companies where these investors publicly disagreed (e.g., Buffett's tech aversion vs. Lynch's enthusiasm for consumer-facing tech). If your simulation agrees on these, something is wrong.

**Detection:** Track vote distribution across many debates. If >80% produce unanimous verdicts, groupthink is dominating.

**Phase relevance:** Debate orchestration phase (Phase 3). Structural dissent mechanisms must be designed into the debate format.

**Confidence:** HIGH -- research-backed and specifically studied in financial committee simulations.

**Sources:**
- [Systematic Biases in LLM Simulations of Debates](https://arxiv.org/abs/2402.04049)

---

### Pitfall 9: Streamlit Rerun Architecture vs. Long-Running Debates

**What goes wrong:** Streamlit reruns the entire script on every user interaction. During a multi-minute debate simulation, a user clicking anything (or even the browser tab losing focus) can disrupt the debate in progress. Session state management becomes fragile, especially for storing the full debate history and intermediate agent outputs.

**Why it happens:** Streamlit's architecture is fundamentally stateless -- it re-executes the script top-to-bottom on every interaction. This conflicts with a long-running, stateful process like a multi-agent debate. Generator objects yielding streaming responses can be lost between reruns. Session state cross-talk has been reported in multi-user scenarios (though v1 is single-user, future scaling would hit this).

**Prevention:**
1. Run the debate engine asynchronously in a background thread/process, decoupled from Streamlit's rerun cycle. Store debate state in session_state and update the UI by polling the background process.
2. Use `st.cache_resource` for any stateful objects (debate engine, agent instances) that must persist across reruns.
3. Disable user input controls during active debate simulation to prevent mid-debate reruns.
4. Implement debate checkpointing: save state after each round so that if a rerun occurs, the debate can resume from the last checkpoint rather than restarting.
5. Use `st.write_stream()` for real-time debate output, which uses Server-Sent Events and handles Streamlit's rerun model better than manual streaming.

**Detection:** Test the UI by interacting with it during an active debate. If the debate restarts or state is lost, the architecture needs fixing.

**Phase relevance:** UI development phase (Phase 4). Must be designed before building the debate display, not retrofitted.

**Confidence:** MEDIUM -- based on documented Streamlit limitations and community reports, though Streamlit has improved significantly in recent releases.

**Sources:**
- [Streamlit Session State docs](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state)
- [Streaming agent response to Streamlit UI](https://github.com/langchain-ai/langchain/issues/15747)

---

### Pitfall 10: Ethical and Legal Risk of Impersonating Real Public Figures

**What goes wrong:** Simulating specific, named, living public figures (Warren Buffett, Howard Marks, Li Lu) as AI personas creates ethical and potentially legal exposure. Users may misinterpret AI-generated opinions as reflecting actual positions of these investors. The product could be seen as putting words in the mouths of real people.

**Why it happens:** Right of publicity laws protect individuals' control over commercial use of their identity (name, image, voice). While educational/parody uses have protections, a product that generates investment analysis "from" Warren Buffett exists in a gray area. The risk is amplified because the domain is financial advice, which has its own regulatory framework. Microsoft's TinyTroupe FAQ explicitly states it is "NOT intended for policy or any consequential decision making."

**Consequences:** Cease-and-desist from investors or their estates. Users making financial decisions based on "what Buffett would say" when the output is fabricated. Reputational risk if the product generates analysis that contradicts these investors' actual published positions, especially if those investors become aware of it.

**Prevention:**
1. Include prominent disclaimers: "These are AI simulations inspired by public investment philosophies. They do not represent the actual views of any real person. This is not financial advice."
2. Consider using persona names that reference the philosophy rather than the person (e.g., "The Moat Builder" instead of "Warren Buffett") with an explanation of the inspiration. This reduces legal exposure while preserving educational value.
3. Add a "Not Financial Advice" disclaimer that users must acknowledge before each debate.
4. Never claim or imply that the simulation accurately predicts what these investors would actually do.
5. Ground personas exclusively in publicly available, published works (books, shareholder letters, public speeches) -- never in private communications or speculative positions.

**Detection:** Legal review of product positioning. User testing to assess whether users perceive outputs as genuine investment advice.

**Phase relevance:** Phase 1 (project design) and Phase 2 (persona creation). The naming and disclaimer strategy must be decided before building personas.

**Confidence:** MEDIUM -- legal landscape is evolving, especially around AI and right of publicity. The risk is real but the severity depends on product distribution and commercial nature.

**Sources:**
- [Can IP Law Protect Your Likeness and Persona from AI?](https://danielrosslawfirm.com/2026/01/29/can-i-use-intellectual-property-to-protect-my-likeness-and-persona/)
- [TinyTroupe Responsible AI FAQ](https://github.com/microsoft/TinyTroupe/blob/main/RESPONSIBLE_AI_FAQ.md)

---

### Pitfall 11: LLM Hallucination of Financial "Facts"

**What goes wrong:** Even when provided with real financial data, LLMs fabricate additional "facts" -- inventing revenue figures for quarters not in the data, hallucinating competitor comparisons, or citing non-existent analyst reports. The generated investment memo contains a mix of real data and convincing-sounding fabrications.

**Why it happens:** LLMs trained on financial text have internalized patterns of financial analysis that include specific numbers. When generating analysis, they pattern-match to produce realistic-sounding figures rather than restricting output to provided data. Research shows alarming Mean Absolute Errors (MAE of $6,357 for stock price predictions in zero-shot settings). Finance-specific hallucination is a documented and active research area.

**Prevention:**
1. Constrain agent outputs to reference only explicitly provided data. Include in the system prompt: "You may ONLY reference financial data provided in the context. If data for a metric is not provided, state that it is unavailable rather than estimating."
2. Post-process debate transcripts to flag any numerical claims not traceable to input data.
3. Structure the financial data input clearly with explicit labels so the LLM can reference specific fields rather than relying on parametric memory.
4. In the investment memo generation step, include a "Data Sources" section that maps each claim to the input data point. Claims without sources are flagged.

**Detection:** Automated extraction of all numerical claims from debate output, cross-referenced against the input financial data dictionary. Any number not in the source data is flagged.

**Phase relevance:** Both data integration (Phase 2) and debate orchestration (Phase 3). Data must be structured for easy referencing, and agents must be constrained to use it.

**Confidence:** HIGH -- extensively documented in financial AI research.

**Sources:**
- [Hidden Dangers of AI Hallucinations in Financial Services](https://www.baytechconsulting.com/blog/hidden-dangers-of-ai-hallucinations-in-financial-services)
- [FAITH: Framework for Assessing Intrinsic Tabular Hallucinations in Finance](https://arxiv.org/abs/2508.05201)

---

## Minor Pitfalls

---

### Pitfall 12: XBRL Taxonomy Inconsistency Across Companies

**What goes wrong:** SEC EDGAR XBRL filings use non-standard element names across companies. Parsing financial statements for AAPL vs. JPM vs. a small-cap company yields different field names for the same concepts, making it impossible to present consistent data to debate agents.

**Prevention:** Use the `edgartools` library or SEC's companyfacts JSON API rather than parsing raw XBRL. These tools handle taxonomy normalization. Define a canonical set of financial metrics (revenue, net income, total assets, free cash flow, P/E, P/B, debt-to-equity) and map from whatever the source provides to your canonical schema.

**Phase relevance:** Data integration phase (Phase 2).

**Confidence:** HIGH.

---

### Pitfall 13: Debate Transcripts Becoming Unreadably Long

**What goes wrong:** Six agents each producing multi-paragraph responses over 4+ rounds creates transcripts that are tens of thousands of words. Users don't read them. The real-time streaming UI becomes overwhelming. The scorecard and memo are the only things users actually want, making the debate a hidden cost center rather than a feature.

**Prevention:** Constrain agent response length aggressively (e.g., 150-250 words per response). Structure rounds with specific prompts ("In 2-3 sentences, state your strongest concern about this company's valuation"). Provide a collapsible/summarized view alongside the full transcript. Front-load the scorecard and memo; make the full debate an optional deep-dive.

**Phase relevance:** UI design phase (Phase 4) and debate orchestration (Phase 3).

**Confidence:** MEDIUM -- based on UX patterns for AI-generated content.

---

### Pitfall 14: OpenAI API Model Availability and Naming Instability

**What goes wrong:** The project targets "GPT 5.2 extra high" and "Codex 5.3 extra high" -- specific model tiers that may change naming, availability, or behavior between OpenAI releases. Hard-coding model names throughout the codebase creates fragility.

**Prevention:** Abstract the model selection behind a configuration layer. Use a model registry pattern where the model name is defined in one place (config file) and all LLM calls reference the registry. This allows swapping models without code changes. Include model-specific prompt adjustments in the registry (different models may need different persona prompt styles).

**Phase relevance:** Phase 1 (project setup/foundation).

**Confidence:** MEDIUM -- OpenAI has a history of renaming and deprecating models.

---

### Pitfall 15: Over-Engineering the Debate Protocol Before Validating Personas

**What goes wrong:** Spending weeks building a sophisticated multi-round debate orchestration system, only to discover that the personas don't produce sufficiently differentiated or interesting output. All the debate infrastructure is wasted because the atoms it operates on (persona responses) lack quality.

**Prevention:** Validate personas FIRST. Before building any debate system, test each persona in isolation: give it financial data and ask for a buy/hold/sell analysis. Compare outputs across personas. Only proceed to multi-agent debate if solo persona outputs are clearly differentiated. This is a cheap, fast validation that should happen in days, not weeks.

**Phase relevance:** This defines phase ordering: persona validation must precede debate system development.

**Confidence:** HIGH -- fundamental engineering principle applied to this specific domain.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Phase 1: TinyTroupe Fork & Setup | Fork divergence from upstream (Pitfall 6) | Extend via subclassing, minimize core modifications, pin version |
| Phase 1: Project Architecture | Token cost structure not considered (Pitfall 7) | Design token budget and caching strategy from day one |
| Phase 1: Project Design | Legal/ethical risk of real names (Pitfall 10) | Decide naming strategy and disclaimer approach before building |
| Phase 2: Persona Creation | Persona convergence (Pitfall 1) | Build differentiation test suite, use contrastive prompting |
| Phase 2: Persona Creation | Over-engineering before validation (Pitfall 15) | Test personas in isolation before building debate system |
| Phase 2: Data Integration | Unreliable free data sources (Pitfall 5) | Data validation layer, fallback sources, completeness thresholds |
| Phase 2: Data Integration | XBRL inconsistency (Pitfall 12) | Use edgartools/companyfacts, canonical metric schema |
| Phase 3: Debate Orchestration | Persona drift over rounds (Pitfall 2) | System prompt re-injection, debate length limits, separate contexts |
| Phase 3: Debate Orchestration | Confirmation bias / groupthink (Pitfalls 3, 8) | Structural dissent roles, independent pre-debate votes |
| Phase 3: Debate Orchestration | Error cascading (Pitfall 4) | Fact-checking layer, moderator agent, grounded citations |
| Phase 3: Debate Orchestration | Financial hallucination (Pitfall 11) | Constrain to provided data, post-process verification |
| Phase 4: Streamlit UI | Rerun architecture conflicts (Pitfall 9) | Background debate engine, checkpointing, cached resources |
| Phase 4: Streamlit UI | Overwhelming transcript length (Pitfall 13) | Response length constraints, collapsible UI, scorecard-first design |
| All Phases | Model naming instability (Pitfall 14) | Abstract model selection behind config registry |

---

## Key Ordering Insight

The single most important phase-ordering lesson from this research: **validate persona differentiation before building anything else**. Pitfalls 1, 2, 3, and 8 all stem from the same root cause -- LLMs converging to generic output. If personas are not convincingly distinct in isolation, no amount of debate orchestration, data integration, or UI polish will save the product. The persona quality is the product.

---

## Sources

### Peer-Reviewed / Academic
- [Systematic Biases in LLM Simulations of Debates](https://arxiv.org/abs/2402.04049) - Chuang et al., EMNLP 2024
- [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657) - Cemri et al., 2025
- [Measuring and Controlling Persona Drift in Language Model Dialogs](https://arxiv.org/html/2402.10962v1) - Zheng et al., 2024
- [Your AI, Not Your View: The Bias of LLMs in Investment Analysis](https://arxiv.org/abs/2507.20957) - Lee & Seo, 2025
- [Consistently Simulating Human Personas with Multi-Turn Reinforcement Learning](https://arxiv.org/html/2511.00222v1) - 2025
- [FAITH: Framework for Assessing Intrinsic Tabular Hallucinations in Finance](https://arxiv.org/abs/2508.05201) - 2025

### Technical Documentation / Issue Trackers
- [TinyTroupe GitHub](https://github.com/microsoft/TinyTroupe)
- [TinyTroupe Responsible AI FAQ](https://github.com/microsoft/TinyTroupe/blob/main/RESPONSIBLE_AI_FAQ.md)
- [yfinance GitHub Issues](https://github.com/ranaroussi/yfinance/issues)
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [EdgarTools](https://github.com/dgunning/edgartools)

### Industry Analysis
- [Why Your Multi-Agent System is Failing (Towards Data Science)](https://towardsdatascience.com/why-your-multi-agent-system-is-failing-escaping-the-17x-error-trap-of-the-bag-of-agents/)
- [Hidden Dangers of AI Hallucinations in Financial Services](https://www.baytechconsulting.com/blog/hidden-dangers-of-ai-hallucinations-in-financial-services)
- [Can IP Law Protect Your Likeness and Persona from AI?](https://danielrosslawfirm.com/2026/01/29/can-i-use-intellectual-property-to-protect-my-likeness-and-persona/)
