# Phase 5: Streamlit UI - Research

**Researched:** 2026-03-22
**Domain:** Streamlit real-time chat UI with background threading for multi-agent LLM debate
**Confidence:** MEDIUM-HIGH

## Summary

Building a real-time debate viewer in Streamlit requires careful navigation of Streamlit's rerun-everything execution model. The core challenge is running a 3-5 minute blocking debate (24+ sequential LLM calls) in a background thread while keeping the UI responsive for both display updates and user input. Streamlit does not officially support multithreading in app code, but provides documented patterns for it. The recommended architecture uses `threading.Thread` for the debate orchestrator, `queue.Queue` for thread-safe communication, and `st.fragment(run_every=...)` for polling updates from the main thread.

The chat display pattern is well-established in Streamlit: store messages in `st.session_state.messages` as a list of dicts, iterate to render on each rerun. The critical innovation for our case is that messages arrive from a background thread rather than from user input, requiring a queue-based bridge. `st.chat_message` supports custom names and avatars, making it natural to display each investor persona's statements distinctly.

Key risks: (1) `st.session_state` is NOT thread-safe -- the background thread must write to a `queue.Queue`, not directly to session_state; (2) `st.chat_input` has documented issues with the `disabled` parameter across versions -- keep input always enabled rather than fighting this; (3) fragments cannot render widgets to external containers or the sidebar -- the fragment must live in the main content area.

**Primary recommendation:** Use `queue.Queue` as the sole communication channel between the background debate thread and the Streamlit main thread. Poll the queue from an `st.fragment(run_every=2)` that drains new messages and appends them to `st.session_state.debate_log`. Never write to `st.session_state` from the background thread.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
1. **Real-time Debate Streaming**: Per-agent streaming via background thread + polling. Run DebateOrchestrator in background thread, store each agent's output in `st.session_state.debate_log`. Add callback hook to orchestrator after each `agent.act()`. Use `st.fragment` or polling loop for UI refresh. Phase headers as section dividers. Show waiting indicators per agent.
2. **Persona Selection**: Sidebar layout with checkbox grid + taglines. All 6 selected by default. Minimum 2 enforced. Sidebar locks during debate.
3. **Ticker Resolution**: Auto-resolve on input via `resolve_ticker()`. Display confirmation or error before debate starts.
4. **Mid-Debate Steering**: Persistent `st.chat_input` with @mention targeting, queued at agent boundaries. Message queuing pattern -- current agent finishes uninterrupted. Between phases: wait indefinitely for user "Continue" action.
5. **Failure Handling**: Show partial results + warning banner. Attempt vote extraction on available data. No auto-retry for v1.
6. **Scorecard Layout**: Summary consensus banner (color-coded) + vote table + collapsed transcript + collapsed raw data.
7. **Post-Debate Actions**: New Debate (sidebar), Download scorecard as Markdown, Re-run same ticker.
8. **Controls During Debate**: Sidebar ticker and persona checkboxes locked/disabled during active debate.

### Claude's Discretion
None explicitly listed -- all decisions were locked in CONTEXT.md.

### Deferred Ideas (OUT OF SCOPE)
None -- all discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| UI-01 | Streamlit app with ticker input field and company resolution | `resolve_ticker()` direct reuse; sidebar text_input + auto-resolve pattern; st.session_state for validation display |
| UI-02 | Real-time debate display showing each persona's statements via st.chat_message | Background thread + queue.Queue + st.fragment(run_every=2) polling; st.chat_message with persona avatars; growing message list pattern |
| UI-03 | Scorecard view displaying all votes and reasoning after debate completes | `Scorecard.to_markdown()` reuse; st.metric/st.columns for consensus banner; color-coded vote table; st.download_button for export |
| UI-04 | User can inject questions or steer the debate mid-session via chat input | st.chat_input always visible; message_queue bridges to background thread; @mention parsing; orchestrator modification for queue checking |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| streamlit | >=1.55.0 | Web UI framework | Latest stable (March 2026); has st.fragment, st.chat_message, AppTest |
| Python threading | stdlib | Background debate execution | Streamlit's documented pattern for IO-heavy background tasks |
| queue.Queue | stdlib | Thread-safe message passing | Thread-safe by design; recommended by Streamlit community for cross-thread communication |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | (existing) | Test runner | Already in project; AppTest integrates with pytest |
| streamlit.testing.v1 | bundled | Headless UI testing | Part of streamlit; use AppTest for automated tests |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| queue.Queue | Direct session_state writes from thread | session_state is NOT thread-safe; will cause race conditions and NoSessionContext errors |
| st.fragment(run_every=2) | while loop with time.sleep in main thread | Blocks the entire script; prevents user interaction during polling |
| threading.Thread | asyncio | Streamlit's own docs recommend threading for IO-heavy ops; asyncio adds complexity without benefit here |
| streamlit-server-state | queue.Queue | External dependency; designed for cross-session state, not single-session background tasks |

**Installation:**
```bash
uv add streamlit --project src/tinyic
```

## Architecture Patterns

### Recommended Project Structure
```
src/tinyic/ui/
    __init__.py          # Already exists (stub)
    app.py               # Main Streamlit app entry point
    state.py             # Session state initialization and state machine
    components.py        # Reusable UI components (chat display, scorecard, sidebar)
    debate_runner.py     # Background thread wrapper around DebateOrchestrator
```

### Pattern 1: Queue-Based Background Thread Communication (CRITICAL)
**What:** The background debate thread writes to a `queue.Queue`. The main Streamlit thread drains the queue into `st.session_state` during fragment reruns.
**When to use:** Always. This is the ONLY safe pattern for background-to-UI communication.
**Example:**
```python
# Source: Streamlit multithreading docs + community patterns
import queue
import threading
import streamlit as st

# Initialize once per session
if "message_queue" not in st.session_state:
    st.session_state.message_queue = queue.Queue()
    st.session_state.debate_log = []
    st.session_state.status = "setup"

def debate_worker(msg_queue, ticker, persona_names, user_input_queue):
    """Runs in background thread. NEVER touches st.session_state."""
    from tinyic.personas.registry import load_persona
    from tinyic.data.pipeline import build_data_package
    from tinyic.debate.orchestrator import DebateOrchestrator
    from tinyic.debate.extraction import extract_votes, build_scorecard

    try:
        msg_queue.put({"type": "status", "status": "data_fetching"})
        data_package = build_data_package(ticker)
        msg_queue.put({"type": "status", "status": "debating"})

        personas = [load_persona(name) for name in persona_names]
        orchestrator = DebateOrchestrator(
            name=f"IC-{ticker}", personas=personas, data_package=data_package,
        )
        orchestrator.inject_context()

        for phase in orchestrator.PHASE_ORDER:
            orchestrator.current_phase = phase
            prompt = PHASE_PROMPTS[phase].format(company=data_package.company_name)
            orchestrator.broadcast_internal_goal(prompt)
            msg_queue.put({"type": "phase", "phase": phase.value})

            for agent in orchestrator.agents:
                # Check for user messages before each agent
                while not user_input_queue.empty():
                    user_msg = user_input_queue.get_nowait()
                    orchestrator.broadcast(user_msg, source=None)
                    msg_queue.put({"type": "user_injected", "content": user_msg})

                msg_queue.put({"type": "agent_start", "agent": agent.name, "phase": phase.value})
                actions = agent.act(return_actions=True)
                agent_output = agent.pop_latest_actions()
                orchestrator._handle_actions(agent, agent_output)
                msg_queue.put({
                    "type": "agent_done",
                    "agent": agent.name,
                    "phase": phase.value,
                    "content": str(actions),
                })

            orchestrator._phase_history.append(phase.value)
            orchestrator._phase_index += 1
            # Wait for user continue between phases
            msg_queue.put({"type": "phase_complete", "phase": phase.value})

        orchestrator.current_phase = DebatePhase.COMPLETE
        msg_queue.put({"type": "status", "status": "scoring"})
        votes = extract_votes(orchestrator)
        scorecard = build_scorecard(votes, ticker, data_package.company_name)
        transcript = orchestrator.pretty_current_interactions()

        msg_queue.put({
            "type": "complete",
            "scorecard": scorecard,
            "transcript": transcript,
        })
    except Exception as e:
        msg_queue.put({"type": "error", "error": str(e)})
```

### Pattern 2: Fragment-Based Polling Loop
**What:** An `st.fragment(run_every=2)` that drains the message queue and updates the display.
**When to use:** During active debate to poll for new agent outputs.
**Example:**
```python
# Source: Streamlit st.fragment docs (run_every parameter)
@st.fragment(run_every=2 if st.session_state.get("status") == "debating" else None)
def debate_display():
    """Poll queue and render debate messages. Reruns every 2s during debate."""
    msg_queue = st.session_state.message_queue
    # Drain all pending messages
    while not msg_queue.empty():
        try:
            msg = msg_queue.get_nowait()
            if msg["type"] == "agent_done":
                st.session_state.debate_log.append(msg)
            elif msg["type"] == "phase":
                st.session_state.debate_log.append(msg)
            elif msg["type"] == "status":
                st.session_state.status = msg["status"]
            elif msg["type"] == "complete":
                st.session_state.status = "complete"
                st.session_state.scorecard = msg["scorecard"]
                st.session_state.transcript = msg["transcript"]
            elif msg["type"] == "error":
                st.session_state.status = "error"
                st.session_state.error = msg["error"]
        except queue.Empty:
            break

    # Render all messages
    for entry in st.session_state.debate_log:
        if entry["type"] == "phase":
            st.divider()
            st.subheader(f"Phase: {entry['phase'].replace('_', ' ').title()}")
        elif entry["type"] == "agent_done":
            with st.chat_message(entry["agent"], avatar=get_avatar(entry["agent"])):
                st.markdown(entry["content"])
        elif entry["type"] == "agent_start":
            with st.chat_message(entry["agent"], avatar="spinner"):
                st.markdown(f"*{entry['agent']} is analyzing...*")
```

### Pattern 3: State Machine via Session State
**What:** Use a string status field to manage app state transitions.
**When to use:** To control which UI components render and which are disabled.
**Example:**
```python
# State machine: setup -> data_fetching -> debating -> scoring -> complete | error
VALID_TRANSITIONS = {
    "setup": ["data_fetching"],
    "data_fetching": ["debating", "error"],
    "debating": ["scoring", "error"],
    "scoring": ["complete", "error"],
    "complete": ["setup"],
    "error": ["setup"],
}

def init_session_state():
    defaults = {
        "status": "setup",
        "ticker": "",
        "company_name": "",
        "selected_personas": list(PERSONA_REGISTRY.keys()),
        "debate_log": [],
        "message_queue": queue.Queue(),
        "user_input_queue": queue.Queue(),
        "scorecard": None,
        "transcript": None,
        "debate_thread": None,
        "error": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
```

### Pattern 4: Sidebar with Disabled State
**What:** Use `disabled` parameter on all sidebar widgets, tied to session_state status.
**When to use:** To lock sidebar controls during debate.
**Example:**
```python
# Source: Streamlit docs + community pattern for widget locking
is_locked = st.session_state.status not in ("setup", "complete", "error")

with st.sidebar:
    ticker = st.text_input(
        "Stock Ticker",
        value=st.session_state.ticker,
        disabled=is_locked,
        key="ticker_input",
    )

    # Auto-resolve on change
    if ticker and ticker != st.session_state.ticker:
        valid, name = resolve_ticker(ticker)
        st.session_state.ticker = ticker
        st.session_state.company_name = name if valid else ""

    if st.session_state.company_name:
        st.success(f"{st.session_state.company_name} ({ticker.upper()})")
    elif ticker:
        st.error("Invalid ticker")

    st.divider()
    st.subheader("Investment Committee")
    for name in list_personas():
        display = name.replace("_", " ").title()
        tagline = PERSONA_TAGLINES.get(name, "")
        st.checkbox(
            f"{display} -- {tagline}",
            value=name in st.session_state.selected_personas,
            disabled=is_locked,
            key=f"persona_{name}",
        )

    if is_locked:
        st.button("New Debate", on_click=reset_to_setup)
    else:
        can_start = (
            st.session_state.company_name
            and len(st.session_state.selected_personas) >= 2
        )
        st.button("Start Debate", disabled=not can_start, on_click=start_debate)
```

### Pattern 5: Chat Message Display with Persona Avatars
**What:** Use first-letter or emoji avatars for each investor persona in `st.chat_message`.
**When to use:** For every debate message displayed.
**Example:**
```python
# Source: Streamlit st.chat_message docs -- avatar accepts single emoji or first letter
PERSONA_AVATARS = {
    "Warren Buffett": ":material/account_balance:",
    "Charlie Munger": ":material/psychology:",
    "Benjamin Graham": ":material/calculate:",
    "Peter Lynch": ":material/trending_up:",
    "Howard Marks": ":material/cycle:",
    "Li Lu": ":material/public:",
}

# Render a debate message
with st.chat_message(name=agent_name, avatar=PERSONA_AVATARS.get(agent_name)):
    st.markdown(content)

# For user/moderator messages
with st.chat_message("user"):
    st.markdown(f"**Moderator:** {user_message}")
```

### Anti-Patterns to Avoid
- **Writing to st.session_state from a background thread:** Will cause NoSessionContext errors or race conditions. ALWAYS use queue.Queue as intermediary.
- **Using time.sleep() polling in the main script body:** Blocks the entire Streamlit script, preventing any user interaction. Use st.fragment(run_every=...) instead.
- **Calling st.rerun() from a background thread:** Will crash. Only the main script thread can trigger reruns.
- **Using add_script_run_ctx to share context with background thread:** Officially unsupported, may break in future versions, risks security vulnerabilities from leaked ScriptRunContext. Avoid this entirely.
- **Nesting st.chat_message containers:** Streamlit docs explicitly warn against this -- breaks layout.
- **Trying to disable/enable st.chat_input dynamically:** Known buggy across Streamlit versions (documented issues #8323, #8757). Keep it always enabled instead.
- **Putting widgets inside a fragment that render to external containers:** Fragments can only contain widgets in their main body, not render to externally created containers.
- **Calling st.sidebar from within a fragment:** Not supported. Instead, call the fragment function inside a `with st.sidebar:` context.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Thread-safe queue | Custom lock-based list | `queue.Queue` (stdlib) | Already thread-safe, handles edge cases (empty, full, timeout) |
| Chat message history replay | Manual HTML rendering | `st.chat_message` + session_state list pattern | Streamlit's built-in pattern handles reruns, avatars, markdown |
| Scorecard markdown | Custom markdown generator | `Scorecard.to_markdown()` | Already built in Phase 4, tested |
| Progress indication | Custom spinner logic | `st.status` context manager | Auto-transitions from "running" to "complete", handles collapse |
| File download | Custom HTTP endpoint | `st.download_button(data=string)` | Built-in, handles MIME types, no server-side file management |
| Ticker validation debouncing | Custom timer logic | Simple if-changed check in session_state | Streamlit reruns on every widget change anyway; yfinance call is fast enough |
| Widget locking | Custom CSS/JS hacks | `disabled=st.session_state.status != "setup"` parameter | Every Streamlit widget supports `disabled` parameter natively |

**Key insight:** Streamlit's rerun-everything model means you don't need manual DOM manipulation or event listeners. State changes trigger full reruns naturally. The only tricky part is the background thread, and queue.Queue solves it cleanly.

## Common Pitfalls

### Pitfall 1: Background Thread Outliving the Session
**What goes wrong:** User closes browser tab, but the background debate thread keeps running (making LLM calls and spending money).
**Why it happens:** Python threads are not automatically terminated when the Streamlit session ends.
**How to avoid:** Store a `threading.Event` as a stop signal. The background thread checks `stop_event.is_set()` before each agent.act() call. On session end or "New Debate" click, set the event.
**Warning signs:** Orphan threads in process list; unexpected API charges.
```python
stop_event = threading.Event()
st.session_state.stop_event = stop_event

# In worker thread, before each expensive call:
if stop_event.is_set():
    msg_queue.put({"type": "cancelled"})
    return
```

### Pitfall 2: Fragment run_every Not Stopping After Debate Completes
**What goes wrong:** The fragment keeps polling every 2 seconds forever, even after the debate is done.
**Why it happens:** `run_every` is set at fragment decoration time. If you use a fixed value, it never stops.
**How to avoid:** Conditionally set `run_every` based on session_state status. When status changes to "complete" or "error", pass `run_every=None`.
**Warning signs:** Browser tab keeps sending requests; CPU usage stays elevated.
```python
# CORRECT: Conditional run_every
is_active = st.session_state.get("status") in ("data_fetching", "debating", "scoring")
run_every = 2 if is_active else None

@st.fragment(run_every=run_every)
def debate_display():
    ...
```

### Pitfall 3: Elements Accumulating in External Containers
**What goes wrong:** Each fragment rerun adds duplicate messages to containers created outside the fragment.
**Why it happens:** Streamlit docs state: "Elements drawn to containers outside the main body of fragment will not be cleared with each fragment rerun. Instead, Streamlit will draw them additively."
**How to avoid:** Render ALL debate messages inside the fragment's main body, not into external containers. The fragment clears and redraws its own body on each rerun.
**Warning signs:** Messages appearing multiple times; UI getting longer with each poll.

### Pitfall 4: Queue Messages Lost on Full App Rerun
**What goes wrong:** User interacts with a widget outside the fragment, triggering a full app rerun. Messages that were in the queue but not yet drained are processed, but previously rendered messages need re-rendering from debate_log.
**Why it happens:** Full reruns re-execute the entire script. If debate_log in session_state is the source of truth, this works fine. But if you rely on the queue alone, you lose track.
**How to avoid:** Always append messages to `st.session_state.debate_log` as the canonical record. The queue is just a transport mechanism. On any rerun (fragment or full), re-render from debate_log.
**Warning signs:** Messages disappearing after clicking a button; partial debate history.

### Pitfall 5: Phase Pause Not Working with Fragment Auto-Rerun
**What goes wrong:** User decision specifies "wait indefinitely for user action between phases." But the fragment keeps re-running and might auto-advance.
**Why it happens:** The background thread doesn't know the user hasn't clicked "Continue" yet.
**How to avoid:** The background thread blocks on a `threading.Event` (phase_continue_event) after each phase. The "Continue" button in the UI sets this event. The background thread only proceeds when the event is set.
**Warning signs:** Phases auto-advancing without user input.
```python
# In background thread, after phase completes:
msg_queue.put({"type": "phase_complete", "phase": phase.value})
phase_continue_event.wait()  # Blocks until UI sets this
phase_continue_event.clear()  # Reset for next phase

# In UI, when user clicks Continue:
def on_continue():
    st.session_state.phase_continue_event.set()
```

### Pitfall 6: session_state Keys Reset by Widget Parameter Changes
**What goes wrong:** Changing widget parameters (like label, options) causes Streamlit to treat it as a new widget and reset its value.
**Why it happens:** Streamlit identifies widgets by their position and key. In 2025-2026, widgets are transitioning to key-only identity, but not all are migrated.
**How to avoid:** Always provide explicit `key=` parameters for all widgets. Never change widget keys dynamically.
**Warning signs:** Checkbox selections resetting; text input clearing unexpectedly.

### Pitfall 7: st.chat_input Placement Issues
**What goes wrong:** `st.chat_input` placed at the bottom of the page conflicts with dynamic content above it.
**Why it happens:** When used in main body, `st.chat_input` is pinned to the bottom. If also placed inside a fragment, it cannot be a widget in an external container.
**How to avoid:** Place `st.chat_input` in the main script body (outside the fragment). Handle the input value in the main script, put user messages into user_input_queue. The fragment only handles display.
**Warning signs:** DuplicateWidgetID errors; chat input disappearing.

## Code Examples

### Complete App Skeleton
```python
# Source: Synthesized from Streamlit docs patterns
# src/tinyic/ui/app.py
import queue
import threading
import streamlit as st

st.set_page_config(page_title="openIC - Investment Committee", layout="wide")

def init_state():
    defaults = {
        "status": "setup",
        "ticker": "",
        "company_name": "",
        "selected_personas": [],
        "debate_log": [],
        "message_queue": queue.Queue(),
        "user_input_queue": queue.Queue(),
        "scorecard": None,
        "transcript": None,
        "debate_thread": None,
        "stop_event": threading.Event(),
        "phase_continue_event": threading.Event(),
        "error": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# --- Sidebar ---
render_sidebar()

# --- Main Area ---
if st.session_state.status == "setup":
    render_welcome()
elif st.session_state.status in ("data_fetching", "debating", "scoring"):
    debate_display()  # The fragment
elif st.session_state.status == "complete":
    debate_display()  # Final render (run_every=None)
    render_scorecard()
elif st.session_state.status == "error":
    render_error()

# --- Chat Input (always in main body, outside fragment) ---
if st.session_state.status == "debating":
    if user_msg := st.chat_input("Ask a question or steer the debate..."):
        st.session_state.user_input_queue.put(user_msg)
        st.session_state.debate_log.append({
            "type": "user_message",
            "content": user_msg,
        })
```

### Scorecard Rendering
```python
# Source: Decision from CONTEXT.md + Streamlit docs
def render_scorecard():
    scorecard = st.session_state.scorecard
    if not scorecard:
        return

    # Consensus banner
    consensus = scorecard.consensus
    if consensus:
        color_map = {"BUY": "green", "HOLD": "orange", "SELL": "red"}
        label = consensus.value
        ratio = f"{scorecard.bull_count}/{len(scorecard.votes)} bullish"
        st.success(f"CONSENSUS: {label} ({ratio})")  # or st.warning/st.error
    else:
        st.warning("NO CONSENSUS (split vote)")

    # Vote table
    cols = st.columns([2, 1, 1, 4])
    cols[0].markdown("**Investor**")
    cols[1].markdown("**Vote**")
    cols[2].markdown("**Confidence**")
    cols[3].markdown("**Key Reasoning**")
    for vote in scorecard.votes:
        cols = st.columns([2, 1, 1, 4])
        cols[0].write(vote.investor)
        vote_color = {"BUY": ":green[BUY]", "HOLD": ":orange[HOLD]", "SELL": ":red[SELL]"}
        cols[1].markdown(vote_color.get(vote.vote.value, vote.vote.value))
        cols[2].write(vote.confidence.value)
        cols[3].write("; ".join(vote.reasoning[:3]))

    # Collapsed sections
    with st.expander("View full debate transcript"):
        st.markdown(st.session_state.transcript or "No transcript available.")

    # Download button
    st.download_button(
        label="Download Scorecard",
        data=scorecard.to_markdown(),
        file_name=f"{scorecard.ticker}_scorecard.md",
        mime="text/markdown",
        on_click="ignore",
        icon=":material/download:",
    )

    # Re-run button
    if st.button("Re-run with same ticker"):
        st.session_state.status = "setup"
        st.session_state.debate_log = []
        st.session_state.scorecard = None
        st.rerun()
```

### @Mention Parsing for Targeted Messages
```python
# Source: CONTEXT.md decision on mid-debate steering
import re

PERSONA_ALIASES = {
    "buffett": "Warren Buffett",
    "munger": "Charlie Munger",
    "graham": "Benjamin Graham",
    "lynch": "Peter Lynch",
    "marks": "Howard Marks",
    "li": "Li Lu",
    "lu": "Li Lu",
}

def parse_mention(text: str) -> tuple[str | None, str]:
    """Parse @mention from user input. Returns (target_name, clean_message)."""
    match = re.match(r"@(\w+)\s+(.*)", text, re.DOTALL)
    if match:
        alias = match.group(1).lower()
        message = match.group(2)
        target = PERSONA_ALIASES.get(alias)
        return target, message
    return None, text
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `st.experimental_rerun()` | `st.rerun()` | Streamlit 1.27 | Stable API; no more experimental prefix |
| Custom chat components | `st.chat_message` + `st.chat_input` | Streamlit 1.26 | Native chat UI; proper accessibility and avatars |
| Manual partial updates | `st.fragment(run_every=...)` | Streamlit 1.37 | Proper partial rerun support; ideal for polling |
| `st.cache` | `st.cache_data` / `st.cache_resource` | Streamlit 1.18 | Clearer semantics; cache_resource for singletons |
| `st.experimental_testing` | `st.testing.v1.AppTest` | Streamlit 1.28 | Stable headless testing API |
| Widget identity by position | Widget identity by key | 2025-2026 rollout | Must always provide explicit keys; prevents reset bugs |
| `st.chat_message(avatar="emoji")` | `st.chat_message(avatar="spinner")` | Streamlit 1.52 | Built-in spinner avatar for "thinking" state |

**Deprecated/outdated:**
- `st.experimental_rerun()`: Use `st.rerun()` (with optional `scope="fragment"`)
- `st.cache`: Use `st.cache_data` or `st.cache_resource`
- `add_script_run_ctx` for background threads: Officially unsupported; prefer queue-based pattern

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + streamlit.testing.v1.AppTest |
| Config file | pyproject.toml (`[tool.pytest.ini_options]`) |
| Quick run command | `uv run pytest tests/test_ui.py -x` |
| Full suite command | `uv run pytest tests/ -x --timeout=120` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| UI-01 | Ticker input resolves company name | unit | `uv run pytest tests/test_ui.py::test_ticker_resolution -x` | No -- Wave 0 |
| UI-01 | Invalid ticker shows error | unit | `uv run pytest tests/test_ui.py::test_invalid_ticker -x` | No -- Wave 0 |
| UI-02 | Debate log grows as messages arrive | unit | `uv run pytest tests/test_ui.py::test_debate_log_rendering -x` | No -- Wave 0 |
| UI-02 | Phase headers display correctly | unit | `uv run pytest tests/test_ui.py::test_phase_headers -x` | No -- Wave 0 |
| UI-03 | Scorecard displays after debate | unit | `uv run pytest tests/test_ui.py::test_scorecard_display -x` | No -- Wave 0 |
| UI-03 | Download button exports markdown | unit | `uv run pytest tests/test_ui.py::test_download_button -x` | No -- Wave 0 |
| UI-04 | User message queued during debate | unit | `uv run pytest tests/test_ui.py::test_user_input_queuing -x` | No -- Wave 0 |
| UI-04 | @mention parsing routes correctly | unit | `uv run pytest tests/test_ui.py::test_mention_parsing -x` | No -- Wave 0 |

### Testing Strategy Notes

**AppTest Capabilities (verified from Streamlit docs):**
- `AppTest.from_file("src/tinyic/ui/app.py")` -- test the app directly
- `at.chat_input[0]` -- access chat input widget
- `at.chat_message[0]` -- access chat message elements
- `at.sidebar` -- access sidebar elements
- `at.session_state["key"] = value` -- pre-set state for testing specific views
- `at.button[0].click()` then `at.run()` -- simulate button clicks

**Testing Limitations:**
- AppTest runs synchronously -- background thread testing needs mocking
- Cannot test real-time polling behavior (fragment run_every)
- Best used for: state machine transitions, component rendering, static scorecard display
- For threading/queue tests: use plain pytest with mocked orchestrator (no Streamlit needed)

**Recommended Test Split:**
1. Pure Python unit tests (no Streamlit): @mention parsing, state machine transitions, queue message processing logic
2. AppTest integration tests: sidebar rendering, scorecard display, ticker resolution UI, chat message rendering from pre-populated session_state
3. Manual tests: real-time debate flow, background thread behavior, polling updates

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_ui.py -x`
- **Per wave merge:** `uv run pytest tests/ -x --timeout=120`
- **Phase gate:** Full suite green before verify-work

### Wave 0 Gaps
- [ ] `tests/test_ui.py` -- covers all UI-XX requirements
- [ ] `tests/test_mention_parsing.py` -- pure Python test for @mention logic (no Streamlit dependency)
- [ ] Streamlit added to tinyic dependencies: `uv add streamlit --project src/tinyic`

## Open Questions

1. **Phase pause UX: "Continue" button vs auto-continue**
   - What we know: CONTEXT.md specifies "wait indefinitely for user action between phases"
   - What's unclear: Should the "Continue" button be inside the fragment or in the main body? Fragment widgets cannot render to external containers.
   - Recommendation: Place a "Continue to next phase" button inside the fragment's main body. When clicked, it sets the `phase_continue_event` threading.Event. The background thread unblocks and proceeds.

2. **Auto-scroll behavior**
   - What we know: CONTEXT.md requests "auto-scroll to follow new messages" with pause on manual scroll-up
   - What's unclear: Streamlit does not provide native auto-scroll control. Chat messages naturally appear at the bottom, but there is no built-in "scroll to bottom" API.
   - Recommendation: Rely on Streamlit's natural behavior where new elements appear at the bottom of the page. For v1, skip custom scroll management -- the fragment rerun will naturally show latest content. This is a cosmetic limitation, not a functional one.

3. **How fast can resolve_ticker be called?**
   - What we know: yfinance lookup is used; CONTEXT.md says "fast enough"
   - What's unclear: If user types quickly, each keystroke triggers a rerun and potentially a yfinance call
   - Recommendation: Only call resolve_ticker when the text_input value changes AND has at least 1 character. Streamlit's text_input already debounces (fires on Enter or blur by default). Use `on_change` callback to avoid calling on every rerun.

4. **Orchestrator modification scope**
   - What we know: CONTEXT.md specifies the orchestrator needs callback hooks and message_queue support
   - What's unclear: Should this modify the existing `DebateOrchestrator._step()` in-place or create a subclass?
   - Recommendation: Modify `DebateOrchestrator._step()` to accept an optional `on_agent_done` callback and `message_queue` parameter. This keeps backward compatibility (no callback = existing behavior) while enabling the streaming UI. This is a Phase 5 task since it only matters for the UI.

## Sources

### Primary (HIGH confidence)
- [Streamlit Threading Docs](https://docs.streamlit.io/develop/concepts/design/multithreading) - Complete multithreading guide with code examples, ScriptRunContext warnings, and recommended patterns
- [st.fragment API Reference](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment) - Fragment function signature, run_every parameter, limitations
- [Working with Fragments](https://docs.streamlit.io/develop/concepts/architecture/fragments) - Fragment behavior: element clearing, external container accumulation, widget restrictions
- [st.chat_message API](https://docs.streamlit.io/develop/api-reference/chat/st.chat_message) - Chat message signature, avatar options, container return value
- [st.chat_input API](https://docs.streamlit.io/develop/api-reference/chat/st.chat_input) - Chat input signature, disabled parameter, placement behavior
- [st.status API](https://docs.streamlit.io/develop/api-reference/status/st.status) - Status container with running/complete/error states
- [st.download_button API](https://docs.streamlit.io/develop/api-reference/widgets/st.download_button) - Download button for text/binary files, on_click="ignore" pattern
- [AppTest API](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest) - from_file, from_function, element access, session_state injection
- [Build LLM Chat App Tutorial](https://docs.streamlit.io/develop/tutorials/chat-and-llm-apps/build-conversational-apps) - Canonical chat app pattern with session_state message list
- [Start/Stop Streaming Fragments Tutorial](https://docs.streamlit.io/develop/tutorials/execution-flow/start-and-stop-fragment-auto-reruns) - Conditional run_every pattern for controlled polling
- [Advanced AppTest Patterns](https://docs.streamlit.io/develop/concepts/app-testing/beyond-the-basics) - Testing secrets, session state pre-seeding, page simulation

### Secondary (MEDIUM confidence)
- [Streamlit 2026 Release Notes](https://docs.streamlit.io/develop/quick-reference/release-notes/2026) - Version 1.55.0 as latest; widget binding, dynamic containers
- [Streamlit 2025 Release Notes](https://docs.streamlit.io/develop/quick-reference/release-notes/2025) - chat_input audio support, spinner avatar, widget identity transition
- [Streamlit Forum: Background Tasks](https://discuss.streamlit.io/t/how-to-run-a-background-task-in-streamlit-and-notify-the-ui-when-it-finishes/95033) - Community patterns for fragment + session_state polling
- [Streamlit Forum: Disable chat_input](https://discuss.streamlit.io/t/disable-streamlit-chat-input-during-prompt-processing/82202) - Known issues with disabled parameter

### Tertiary (LOW confidence)
- [GitHub Issue #8323: chat_input disabled](https://github.com/streamlit/streamlit/issues/8323) - Documented bug with disabling chat_input during processing
- [GitHub Issue #6756: st.lock request](https://github.com/streamlit/streamlit/issues/6756) - Feature request for global widget locking (not implemented)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Streamlit is the only option (project constraint); queue.Queue is stdlib and well-documented
- Architecture: MEDIUM-HIGH - Queue + fragment polling is the recommended community pattern, but Streamlit officially "does not support multithreading in app code"
- Pitfalls: HIGH - All pitfalls verified from official docs and community reports with specific issue numbers
- Testing: MEDIUM - AppTest is stable but has limitations for testing async/threaded behavior; manual testing needed for real-time features

**Research date:** 2026-03-22
**Valid until:** 2026-04-22 (Streamlit releases monthly; core patterns are stable)
