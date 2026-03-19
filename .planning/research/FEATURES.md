# Feature Landscape

**Domain:** AI-powered investment committee simulation / multi-agent debate platform
**Researched:** 2026-03-20

## Table Stakes

Features users expect from any AI investment committee / multi-agent analysis product. Missing any of these and the product feels broken or toy-like.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Ticker input and company identification** | Core entry point; user types a ticker and the system resolves it to a real company with basic info (name, sector, price) | Low | yfinance `Ticker.info` handles this trivially |
| **Financial data fetching (fundamentals)** | Every comparable product (ai-hedge-fund, TradingAgents, FinRobot) fetches real financials -- income statement, balance sheet, cash flow, key ratios | Medium | yfinance for price/financials, SEC EDGAR for filings. Must handle rate limits (360 req/hr on Yahoo) and missing data gracefully |
| **Multiple distinct investor personas** | The entire value proposition. ai-hedge-fund has 12 persona agents (Buffett, Munger, Graham, Lynch, etc.). Users expect each persona to sound and reason differently | High | This is the hardest and most critical feature. Philosophy distillation from real writings is what separates openIC from generic multi-agent chat. 6 personas for v1 is appropriate |
| **Structured multi-round debate** | TradingAgents uses bull/bear researchers with configurable debate rounds. Multi-agent debate is the defining interaction pattern in this domain -- agents must respond to each other, not just give independent opinions | High | Need a debate orchestrator that manages turn-taking, ensures personas reference each other's arguments, and drives toward convergence. TinyTroupe's TinyWorld provides the foundation |
| **Buy/Hold/Sell verdict per persona** | Every comparable system outputs a clear signal per agent. Users need an actionable takeaway, not just conversation | Low | Simple structured extraction after debate concludes |
| **Scorecard / summary output** | ai-hedge-fund produces per-agent analysis summaries. Users expect a consolidated view of who said what and why | Medium | Tabular format: persona, verdict, confidence, key reasoning. Must be generated after debate concludes |
| **Investment memo generation** | Standard output in the AI investment analysis space. V7, GrowthSphere, Deliverables.ai, and others all produce structured memos with: executive summary, thesis, risks, valuation assessment, verdict | Medium | Sections: executive summary, bull case, bear case, key risks, valuation discussion, final verdict. LLM synthesizes from debate transcript |
| **Real-time debate display** | Users want to watch the debate unfold, not wait for a batch result. Streamlit's `st.chat_message` and `st.write_stream` support this natively | Medium | Use Streamlit chat elements with streaming. Each persona gets a distinct avatar/name. Messages appear as the debate progresses |
| **Basic error handling for data gaps** | Free APIs have gaps -- delisted tickers, missing fundamentals for foreign stocks, rate limits. Must fail gracefully with informative messages | Low | Validate data availability before starting debate. Show warnings for partial data rather than crashing |

## Differentiators

Features that would set openIC apart from existing projects. Not expected, but create real value.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Philosophically grounded personas (not generic)** | Most AI investor agents use shallow role prompts ("You are Warren Buffett"). openIC distills actual investment frameworks from real writings -- decision heuristics, mental models, communication style, known biases. This is the core differentiator | Very High | Requires deep research into each investor's published works. The prompt engineering is the product. Graham's margin of safety framework, Buffett's moat analysis, Marks' second-level thinking, Lynch's "invest in what you know" -- each must produce materially different analysis |
| **User steering mid-debate** | Research shows humans can "tip the balance" in LLM multi-agent debates. Letting users inject questions ("What about their debt load?" or "How does this compare to Coca-Cola?") makes it interactive, not passive | Medium | TinyTroupe supports stimulus injection. Implement as optional `st.chat_input` that feeds user messages into the debate as a "moderator" role. LLMs weigh human input more heavily than AI opinion (research finding) |
| **Persona-specific reasoning traces** | Show not just the verdict but the analytical framework each persona applied. Buffett's persona should show moat assessment, earnings power analysis. Graham's should show net-net calculation, margin of safety | High | Structured output per persona: framework applied, key metrics examined, conclusion chain. Goes beyond "I think buy" to "Based on my framework of X, examining Y and Z, I conclude..." |
| **Cross-persona disagreement highlighting** | Automatically surface where personas fundamentally disagree and why. "Buffett says BUY because of moat strength; Marks says HOLD because of cycle risk" | Medium | Post-debate analysis pass that extracts key disagreement axes. More valuable than raw transcript for decision-making |
| **Debate transcript export** | Full debate as downloadable Markdown/PDF. Useful for sharing, archiving, or further analysis | Low | Streamlit download button with formatted transcript. Low effort, high utility |
| **Company data context panel** | Sidebar showing key financials, recent news, price chart alongside the debate. Gives users context without leaving the debate view | Medium | Streamlit sidebar with yfinance data: price chart (plotly), key ratios table, recent headlines. Already proven in multiple Streamlit financial dashboards |
| **Configurable debate parameters** | Let users adjust: number of debate rounds, which personas participate, focus areas (valuation vs growth vs risk). TradingAgents makes debate rounds configurable | Low-Medium | Streamlit sidebar controls. Start simple (rounds + persona selection), expand later |
| **Historical debate comparison** | Run the same company through the committee at different dates to see how analysis changes over time | Medium | Requires storing past debate results. Valuable but v2+ feature -- needs persistence layer |

## Anti-Features

Features to explicitly NOT build. These are tempting but wrong for openIC's scope, audience, or philosophy.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Trade execution or portfolio management** | openIC is an analysis and education tool, not a trading system. Adding execution creates liability, regulatory concerns, and distracts from the core value of debate simulation | Keep the explicit disclaimer: "For educational purposes only. Not financial advice." Output analysis only |
| **Backtesting against market returns** | ai-hedge-fund has this, but it creates false confidence. Persona-based debate analysis is qualitative -- backtesting implies quantitative predictive power that does not exist | If anything, show historical accuracy as a curiosity, not a feature. Focus on the quality of reasoning, not prediction accuracy |
| **Paid financial data API integration** | Adds cost, complexity, and API key management. Free sources (yfinance, SEC EDGAR) provide sufficient data for qualitative investment debate | Stick with free APIs for v1. Note in docs that data depth is limited. If users need Bloomberg-level data, they're not the target audience |
| **RAG over investor writings** | Tempting for accuracy, but adds vector DB complexity, chunking challenges, and retrieval quality issues. Curated prompts give more control over persona consistency | Use carefully crafted system prompts distilled from real writings. Better to have 6 excellent curated personas than 6 mediocre RAG-powered ones |
| **Real-time market data streaming** | Unnecessary for debate-style analysis. Batch fetch at debate start is sufficient. Streaming adds WebSocket complexity for zero analytical value | Fetch once at debate initiation. Display "Data as of [timestamp]" clearly |
| **User accounts and authentication** | Adds significant complexity (auth, storage, sessions) for a single-user educational tool | Single-user, no auth for v1. If multi-user is ever needed, Streamlit Community Cloud handles basic auth |
| **Mobile app** | Streamlit is responsive enough for mobile browsers. Native mobile development is massive scope creep | Ensure Streamlit layout works reasonably on mobile viewports, but optimize for desktop |
| **Custom persona creation by users** | Sounds fun but persona quality depends on deep philosophy distillation. User-created personas would be shallow and undermine the product's credibility | Keep personas curated and high-quality. Perhaps allow persona selection (which 3-6 sit on the committee) but not creation |
| **Multi-company comparison debates** | Running debates on multiple companies simultaneously creates confusion and dilutes analysis depth | One company per debate session. Users can run separate sessions to compare |
| **Sentiment analysis from social media** | Twitter/Reddit sentiment is noisy, often misleading, and philosophically at odds with value investing (which the persona panel represents) | News from free APIs is sufficient context. Value investors explicitly ignore market sentiment noise |

## Feature Dependencies

```
Ticker Input
  --> Financial Data Fetching (needs valid ticker)
    --> Persona Analysis (needs financial data as context)
      --> Structured Debate (needs personas loaded with company data)
        --> Real-time Debate Display (needs debate messages to stream)
        --> User Steering (injects into active debate)
          --> Verdict Extraction (needs debate to conclude)
            --> Scorecard Generation (needs all verdicts)
            --> Investment Memo (needs debate transcript + verdicts)
              --> Transcript Export (needs memo + transcript)

Persona Philosophy Distillation (parallel, prerequisite to all debates)
  --> Persona-specific Reasoning Traces (extension of persona quality)

Company Data Context Panel (parallel to debate, needs financial data)

Cross-persona Disagreement Highlighting (post-processing of debate transcript)

Configurable Debate Parameters (UI controls, affects debate orchestration)
```

## MVP Recommendation

### Must ship (Phase 1 -- core loop):
1. **Ticker input and company identification** -- the entry point
2. **Financial data fetching** -- yfinance fundamentals + price data
3. **6 investor personas with distinct philosophies** -- the product's soul
4. **Structured multi-round debate** -- the core interaction
5. **Real-time debate display** -- Streamlit chat interface showing debate as it happens
6. **Buy/Hold/Sell scorecard** -- the actionable output
7. **Investment memo generation** -- the shareable artifact

### Should ship (Phase 2 -- polish and differentiation):
8. **User steering mid-debate** -- makes it interactive
9. **Company data context panel** -- sidebar with financials/charts
10. **Configurable debate parameters** -- rounds, persona selection
11. **Cross-persona disagreement highlighting** -- surfacing key tensions
12. **Debate transcript export** -- Markdown/PDF download

### Defer (Phase 3+ or never):
- **Persona-specific reasoning traces** -- requires deep prompt engineering iteration after v1 personas are validated
- **Historical debate comparison** -- needs persistence layer
- **Custom persona creation** -- only if user demand justifies quality tradeoffs

## Complexity Budget Estimate

| Feature | Dev Effort | LLM Cost per Run | Risk |
|---------|-----------|-------------------|------|
| Ticker input + data fetch | 1-2 days | None | Low -- well-trodden path |
| Persona philosophy distillation (6 personas) | 2-3 weeks | None (prompt engineering) | HIGH -- this is the make-or-break work |
| Debate orchestration (TinyTroupe fork) | 1-2 weeks | ~$2-5 per debate (6 agents x multiple rounds x GPT-5.2) | Medium -- TinyTroupe provides foundation but needs investment-specific customization |
| Streamlit debate UI | 3-5 days | None | Low -- Streamlit chat primitives are mature |
| Scorecard generation | 1-2 days | Minimal (one LLM call) | Low |
| Investment memo generation | 2-3 days | ~$0.50-1 per memo (one synthesis call) | Low-Medium -- getting the format right takes iteration |
| User steering | 2-3 days | Marginal increase | Low -- TinyTroupe supports stimulus injection |
| Data context panel | 2-3 days | None | Low -- standard Streamlit dashboard pattern |
| Transcript export | 1 day | None | Low |

**Total estimated LLM cost per debate session:** $3-8 depending on model, rounds, and persona count. This is consistent with community reports ($4-5 per company for AutoGen-based systems, less for optimized implementations).

## Competitive Landscape Context

| Project | Approach | openIC Differentiator |
|---------|----------|----------------------|
| virattt/ai-hedge-fund | 12 investor persona agents + 6 technical agents, CLI + web, backtesting | openIC focuses on debate quality and philosophical accuracy over trading signals. Debate is observable and interactive, not just output |
| TradingAgents | Bull/bear researcher debate, 7 roles, structured communication | openIC uses named real-world investor personas (not generic bull/bear), making the debate educational and entertaining |
| FinRobot | Open-source financial AI agents, market participant simulation | openIC is narrower and more opinionated -- specifically an investment committee debate, not a general financial agent platform |
| Kavout / Reflexivity / Fiscal.ai | Commercial platforms, institutional-grade, expensive | openIC is free, open-source, educational. Targets individual investors who want to learn, not institutions |
| Intelligent Livermore ETF | Commercial fund using AI trained on investor writings | openIC makes the debate process transparent and observable. The fund is a black box; openIC is a glass box |

## Sources

- [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) -- closest comparable open-source project with 12 investor persona agents
- [TradingAgents framework](https://tradingagents-ai.github.io/) -- multi-agent LLM trading framework with bull/bear debate mechanism
- [TinyTroupe by Microsoft](https://github.com/microsoft/TinyTroupe) -- LLM-powered multiagent persona simulation (the fork base)
- [AI Hedge Fund: When Multiple AI Agents Become Your Investment Committee](https://yuv.ai/blog/ai-hedge-fund-when-multiple-ai-agents-become-your-investment-committee) -- ecosystem overview
- [I Built an AI Multi-Agent Team That Debates Stocks](https://aimightbewrong.substack.com/p/ai-investment-committee-multi-agent-investing) -- practitioner experience with cost analysis ($4-5 per company)
- [Patterns for Democratic Multi-Agent AI: Debate-Based Consensus](https://medium.com/@edoardo.schepis/patterns-for-democratic-multi-agent-ai-debate-based-consensus-part-2-implementation-2348bf28f6a6) -- debate orchestration patterns
- [Tipping the Balance: Human Intervention in LLM Multi-Agent Debate](https://asistdl.onlinelibrary.wiley.com/doi/10.1002/pra2.1034) -- research on user steering in multi-agent debates
- [Interactive Debugging and Steering of Multi-Agent AI Systems (CHI 2025)](https://dl.acm.org/doi/full/10.1145/3706598.3713581) -- AGDebugger for interactive agent steering
- [Streamlit Chat Elements](https://docs.streamlit.io/develop/api-reference/chat) -- st.chat_message and st.write_stream documentation
- [yfinance rate limiting](https://github.com/ranaroussi/yfinance/issues/2128) -- 360 req/hr limit, blocking risks
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) -- free, no auth required
- [V7 AI Investment Memo Generation](https://www.v7labs.com/automations/ai-investment-memo-generation) -- memo structure standards
- [Investment Memo template](https://deliverables.ai/guides/investment-memo) -- standard memo components
