# Phase 5 Context: Streamlit UI

## Phase Goal
A complete user journey from entering a ticker to watching a live debate to reviewing the final scorecard.

## Requirements
- UI-01: Streamlit app with ticker input field and company resolution
- UI-02: Real-time debate display showing each persona's statements via st.chat_message
- UI-03: Scorecard view displaying all votes and reasoning after debate completes
- UI-04: User can inject questions or steer the debate mid-session via chat input

## Success Criteria
1. User can enter a stock ticker and see resolved company name before debate begins
2. Debate displays in real-time using chat message components, with each persona's statements appearing as they are generated
3. After debate completes, scorecard view shows all persona votes and reasoning in clear visual layout
4. User can type a question or steering prompt during an active debate, and it is injected into the next round of discussion

## Key Constraints
- Streamlit's rerun model: script re-executes top-to-bottom on any state change
- Must use st.session_state for all persistent state across reruns
- Backend is fully built: run_debate() -> DebateResult, but needs orchestrator modifications for streaming
- DebateOrchestrator._step() runs all agents sequentially in a blocking loop -- can't update UI mid-step without threading
- GPT-5.2 with reasoning_effort=xhigh -- each agent call is slow (~15-30s)
- Full 6-persona, 4-phase debate = ~24 LLM calls, estimated 3-5+ minutes total
- No speed/quality toggle -- persona count (2-6) is the user's speed lever
- Proxy gateway requires stream=True on all OpenAI calls

## Technical Foundation (from Phases 1-4)
- `run_debate(ticker, persona_names, data_package)` -> DebateResult with scorecard, transcript, phase history
- `DebateOrchestrator`: TinyWorld subclass, 4 phases (OPENING, CROSS_EXAM, REBUTTAL, VERDICT), sequential agent execution
- `DebateOrchestrator._step()`: broadcasts phase prompt, then each agent.act() in stable order
- `DebateOrchestrator.inject_context()`: broadcasts DataPackage to all agents
- `TinyWorld.broadcast(msg)`: sends message to all agents (used for moderator/user messages)
- `extract_votes(orchestrator)` -> list[Vote] via ResultsExtractor with fuzzy parsing + fallback
- `build_scorecard(votes, ticker, company_name)` -> Scorecard with consensus detection
- `Scorecard.to_markdown()`: renders scorecard as markdown table (reusable for download)
- `list_personas()` -> list of 6 names; `load_persona(name)` -> InvestorPersona
- `resolve_ticker(symbol)` -> TickerResult with company_name, valid flag
- `build_data_package(ticker)` -> DataPackage with financials, filings, news, social
- `agent.pop_latest_actions()`: reliable way to get agent output after act()
- 115 unit tests + 7 live API tests passing
- `src/tinyic/ui/__init__.py` exists (stub: `"""Phase 5: Streamlit UI."""`)

<decisions>
## Implementation Decisions

### 1. Real-time Debate Streaming

**Per-agent streaming via background thread + polling.**

- Run the DebateOrchestrator in a background thread. Store each agent's output in `st.session_state.debate_log` as it completes.
- Add a callback hook to the orchestrator: after each `agent.act()` + `pop_latest_actions()`, call `callback(agent_name, phase, actions)` which writes to shared state.
- Use `st.fragment` or a polling loop (`st.empty()` + short sleep) in the main thread to refresh the debate display as new entries appear.
- Each agent's statement appears individually as it finishes -- live group chat feel.
- Phase headers as prominent section dividers: `=== OPENING STATEMENTS ===`, `=== CROSS-EXAMINATION ===`, etc.
- Show waiting indicators: completed agents get green checkmark, current agent shows "is analyzing...", remaining agents show as "waiting".
- Auto-scroll to follow new messages. If user scrolls up to re-read, auto-scroll pauses until they return to bottom (standard chat UX).

**Architecture sketch:**
```
[Main thread]              [Background thread]
st.session_state    <--    orchestrator._step()
  .debate_log[]              for agent in agents:
  .current_agent                agent.act()
  .current_phase                callback(agent, phase, actions)
  .message_queue                # ^ writes to session_state
  .status

[Streamlit fragment/poll]
  checks session_state every ~2s
  renders new debate_log entries via st.chat_message
```

### 2. Persona Selection Experience

**Sidebar layout with checkbox grid + taglines. All 6 selected by default.**

- Sidebar contains: ticker input (top), company resolution display, persona checkboxes with 1-line taglines, Start Debate button (bottom).
- Main area reserved entirely for debate stream and scorecard.
- All 6 personas checked by default. User unchecks to remove. Minimum 2 enforced (show warning if <2).
- Taglines only (no expandable bios): "Warren Buffett -- Moats & compounding", "Charlie Munger -- Mental models & quality", etc.
- Sidebar controls lock (disabled) once debate starts. Show "New Debate" button to reset.

**Persona taglines:**
- Warren Buffett: Moats & compounding
- Charlie Munger: Mental models & quality
- Benjamin Graham: Deep value & margin of safety
- Peter Lynch: Growth at reasonable price
- Howard Marks: Cycles & risk management
- Li Lu: Value + emerging markets

### 3. Ticker Resolution

**Auto-resolve on input.** As soon as user enters a ticker, call `resolve_ticker()` and display confirmation ("Apple Inc. (AAPL)") or error ("Invalid ticker"). User sees the resolved name before clicking Start Debate. Catches typos early. The resolve call is fast (single yfinance lookup).

### 4. Mid-Debate Steering

**Persistent chat input with @mention targeting, queued at agent boundaries.**

- A chat input (st.chat_input) is always visible at the bottom during the debate.
- Default behavior: message broadcasts to all personas as a "Moderator" message via `TinyWorld.broadcast()`.
- @mention syntax for targeting: `@Buffett what about the moat?` directs the question to a specific persona. Other personas receive it as context/observation ("The moderator asked Buffett about...").
- Autocomplete on @ to help with persona names.
- **Message queuing:** When user sends a message while an agent is mid-generation, the message queues immediately and gets injected before the next agent starts acting. Current agent finishes uninterrupted. User sees their message in the stream with a "queued" indicator, then it gets delivered.
- Between phases: show a brief pause with "Continue" button or wait for user input. No auto-timeout -- wait indefinitely for user action. Respects user's reading pace.

**Orchestrator modifications needed:**
- Add a `message_queue` that the background thread checks before each agent.act()
- Parse @mentions to determine broadcast vs targeted delivery
- For targeted delivery: use agent.listen() for the target, broadcast observation to others

### 5. Failure Handling

**Show partial results + warning banner.**

- If an API call fails mid-debate, display whatever phases/agents completed so far.
- Attempt vote extraction on available data (backend already supports fallback HOLD/LOW votes).
- Show scorecard with a warning banner: "Debate incomplete (N of 4 phases). Scorecard based on available discussion."
- Warning styling on the scorecard (amber border or similar).
- No auto-retry for v1. User can start a new debate if they want.

### 6. Scorecard and Results Layout

**Summary consensus banner + color-coded vote table + collapsed transcript.**

- Top: prominent consensus banner with color coding -- green for BUY, amber for HOLD, red for SELL. Shows "CONSENSUS: BUY (4/6 bullish)" or "NO CONSENSUS (split vote)".
- Below: vote table with columns: Investor, Vote (color-coded), Confidence, Key Reasoning. Use st.columns or st.dataframe with styling.
- Color coding: BUY = green, HOLD = yellow/amber, SELL = red. Applied to consensus banner and vote indicators.
- Below table: `st.expander("View full debate transcript")` -- collapsed by default.
- Below transcript: `st.expander("View raw data package")` -- collapsed by default.

### 7. Post-Debate Actions

Three actions available after debate completes:
1. **New Debate** (sidebar): resets everything -- new ticker, new persona selection.
2. **Download scorecard as Markdown**: `st.download_button` using `Scorecard.to_markdown()`. Low effort, method already exists.
3. **Re-run same ticker**: shortcut to start new debate with same ticker but unlocks persona selection for changes.

### 8. Controls During Debate

- Sidebar ticker and persona checkboxes are **locked/disabled** during active debate.
- Only "New Debate" button available in sidebar during/after debate.
- "Re-run" button appears in main area after debate completes.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Scorecard.to_markdown()` (src/tinyic/debate/models.py:72) -- direct reuse for download button
- `run_debate()` (src/tinyic/debate/__init__.py:14) -- needs refactoring from blocking to callback-based for streaming
- `resolve_ticker()` (src/tinyic/data/ticker_resolver.py) -- direct reuse for ticker input validation
- `list_personas()` (src/tinyic/personas/registry.py) -- direct reuse for checkbox generation
- `load_persona()` (src/tinyic/personas/registry.py) -- used by orchestrator
- `build_data_package()` (src/tinyic/data/pipeline.py) -- used during debate setup
- `TinyWorld.broadcast()` (src/tinytroupe/environment/tiny_world.py) -- for user message injection
- `DebateOrchestrator._step()` (src/tinyic/debate/orchestrator.py:68) -- needs callback hook for per-agent streaming
- `DebateOrchestrator.inject_context()` -- called at debate start, no changes needed

### Modifications Needed
- **DebateOrchestrator._step()**: Add optional callback parameter called after each agent.act(). Callback receives (agent_name, phase, actions). Existing behavior preserved when no callback provided.
- **DebateOrchestrator.run_debate()**: Accept optional callback and message_queue parameters. Check message_queue before each agent to inject user steering messages.
- **run_debate()**: May need a streaming variant or the Streamlit app calls orchestrator directly instead of using the convenience function.

### Established Patterns
- Lazy imports in function bodies (used in run_debate(), fetchers) -- follow this pattern in UI code
- Pydantic models for all data structures -- continue for any new UI state models
- Graceful degradation with fallback values -- apply to UI error handling
- st.session_state for Streamlit state management -- standard pattern

### Integration Points
- UI app entry point: `src/tinyic/ui/app.py` (new file)
- UI imports from: tinyic.debate (orchestrator, models, extraction), tinyic.data (pipeline, ticker_resolver, models), tinyic.personas (registry)
- Background thread writes to st.session_state, main thread reads from it
- Message queue bridges user input (main thread) to orchestrator (background thread)

</code_context>

<deferred>
## Deferred Ideas

None -- all discussion stayed within phase scope.

</deferred>

---

*Phase: 05-streamlit-ui*
*Context gathered: 2026-03-22*
