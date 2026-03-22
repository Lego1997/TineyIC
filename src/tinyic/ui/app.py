"""openIC -- AI Investment Committee Simulator (Streamlit app)."""

import streamlit as st
import threading
import queue
from datetime import datetime
import re

_TESTING = False
try:
    st.set_page_config(
        page_title="openIC | Investment Committee",
        page_icon="📊",
        layout="wide",
    )
except Exception:
    _TESTING = True

# Persona metadata for sidebar display
PERSONA_INFO = {
    "warren_buffett": {"display": "Warren Buffett", "tagline": "Moats & compounding"},
    "charlie_munger": {"display": "Charlie Munger", "tagline": "Mental models & quality"},
    "benjamin_graham": {"display": "Benjamin Graham", "tagline": "Deep value & margin of safety"},
    "peter_lynch": {"display": "Peter Lynch", "tagline": "Growth at reasonable price"},
    "howard_marks": {"display": "Howard Marks", "tagline": "Cycles & risk management"},
    "li_lu": {"display": "Li Lu", "tagline": "Value + emerging markets"},
}

# Phase display labels
PHASE_LABELS = {
    "opening_statements": "Opening Statements",
    "cross_examination": "Cross-Examination",
    "rebuttal": "Rebuttal",
    "final_verdict": "Final Verdict",
}


def extract_talk_content(actions):
    """Extract TALK content from a list of agent actions.

    Args:
        actions: List of action dicts from pop_latest_actions(), or None.

    Returns:
        Concatenated TALK content, or "(No response)" if no TALK actions found.
    """
    if not actions:
        return "(No response)"
    parts = []
    for action in actions:
        if isinstance(action, dict) and action.get("type") == "TALK":
            content = action.get("content", "")
            if content:
                parts.append(content)
    return "\n\n".join(parts) if parts else "(No response)"


def parse_user_message(text):
    """Parse user message for @mention targeting.

    Args:
        text: Raw user input string.

    Returns:
        (clean_text, target_agent_name_or_None)
    """
    name_map = {
        "buffett": "Warren Buffett",
        "warren": "Warren Buffett",
        "munger": "Charlie Munger",
        "charlie": "Charlie Munger",
        "graham": "Benjamin Graham",
        "benjamin": "Benjamin Graham",
        "lynch": "Peter Lynch",
        "peter": "Peter Lynch",
        "marks": "Howard Marks",
        "howard": "Howard Marks",
        "li": "Li Lu",
        "lu": "Li Lu",
    }

    match = re.match(r"^@(\w+)\s+(.+)", text, re.DOTALL)
    if match:
        mention = match.group(1).lower()
        clean_text = match.group(2).strip()
        target = name_map.get(mention)
        return (clean_text, target)

    return (text, None)


def init_state():
    """Initialize session state with defaults. Idempotent -- only sets missing keys."""
    defaults = {
        "status": "idle",
        "ticker": "",
        "company_name": "",
        "selected_personas": list(PERSONA_INFO.keys()),
        "debate_log": [],
        "current_phase": "",
        "current_agent": "",
        "debate_result": None,
        "error_message": "",
        "ui_queue": None,
        "stop_event": None,
        "message_queue": None,        # queue.Queue for user steering messages -> orchestrator
        "phase_gate": None,           # threading.Event instance, shared with background thread
        "waiting_for_continue": False, # True when paused between phases
        "user_messages": [],           # User messages to display in chat log
        "incomplete_warning": False,   # True if debate ended with error (partial results)
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def render_sidebar():
    """Render the sidebar with ticker input, persona selection, and controls."""
    with st.sidebar:
        st.title("openIC")
        st.caption("AI Investment Committee Simulator")

        disabled = st.session_state.status in ("fetching", "debating", "extracting")

        ticker = st.text_input(
            "Stock Ticker",
            placeholder="e.g., AAPL, MSFT, TSLA",
            disabled=disabled,
            key="ticker_input",
        )

        if ticker and ticker.upper().strip() != st.session_state.ticker:
            st.session_state.ticker = ticker.upper().strip()
            with st.spinner("Resolving..."):
                _resolve_ticker(st.session_state.ticker)
        elif not ticker:
            st.session_state.ticker = ""
            st.session_state.company_name = ""
            if st.session_state.status == "ready":
                st.session_state.status = "idle"

        if st.session_state.company_name:
            st.success(f"{st.session_state.company_name} ({st.session_state.ticker})")
        elif st.session_state.ticker and st.session_state.status == "idle":
            st.error("Invalid ticker")

        st.divider()

        st.subheader("Committee Members")
        selected = []
        for key, info in PERSONA_INFO.items():
            checked = st.checkbox(
                f"**{info['display']}** -- {info['tagline']}",
                value=key in st.session_state.selected_personas,
                key=f"persona_{key}",
                disabled=disabled,
            )
            if checked:
                selected.append(key)
        st.session_state.selected_personas = selected

        if len(selected) < 2:
            st.warning("Select at least 2 committee members")

        st.divider()

        can_start = (
            bool(st.session_state.company_name)
            and len(selected) >= 2
            and st.session_state.status in ("idle", "ready", "complete")
        )

        if st.button("Start Debate", disabled=not can_start or disabled, type="primary", use_container_width=True):
            _start_debate()
            st.rerun()

        if st.session_state.status in ("debating", "extracting", "complete", "error"):
            if st.button("New Debate", use_container_width=True):
                _reset_state()
                st.rerun()


def _resolve_ticker(ticker):
    """Resolve ticker to company name. Updates session_state."""
    from tinyic.data.ticker_resolver import resolve_ticker
    is_valid, name = resolve_ticker(ticker)
    if is_valid:
        st.session_state.company_name = name
        st.session_state.status = "ready"
    else:
        st.session_state.company_name = ""
        st.session_state.status = "idle"


def _start_debate():
    """Launch the debate in a background thread."""
    st.session_state.status = "fetching"
    st.session_state.debate_log = []
    st.session_state.debate_result = None
    st.session_state.error_message = ""
    st.session_state.current_phase = ""
    st.session_state.current_agent = ""
    st.session_state.user_messages = []
    st.session_state.incomplete_warning = False
    st.session_state.waiting_for_continue = False

    # Create shared concurrency primitives
    st.session_state.ui_queue = queue.Queue()
    st.session_state.stop_event = threading.Event()
    st.session_state.message_queue = queue.Queue()
    st.session_state.phase_gate = threading.Event()

    ticker = st.session_state.ticker
    persona_names = list(st.session_state.selected_personas)
    ui_queue = st.session_state.ui_queue
    stop_event = st.session_state.stop_event
    message_queue = st.session_state.message_queue
    phase_gate = st.session_state.phase_gate

    thread = threading.Thread(
        target=_debate_worker,
        args=(ticker, persona_names, ui_queue, stop_event, message_queue, phase_gate),
        daemon=True,
    )
    thread.start()


def _debate_worker(ticker, persona_names, ui_queue, stop_event, message_queue=None, phase_gate=None):
    """Background thread: fetch data -> run debate -> extract votes -> scorecard.

    IMPORTANT: This function NEVER reads or writes st.session_state.
    All communication with the UI thread is via ui_queue.put().
    """
    try:
        from tinyic.data.pipeline import build_data_package
        from tinyic.personas.registry import load_persona
        from tinyic.debate.orchestrator import DebateOrchestrator
        from tinyic.debate.extraction import extract_votes, build_scorecard
        from tinyic.debate.models import DebateResult

        data_package = build_data_package(ticker)

        personas = [load_persona(name) for name in persona_names]

        orchestrator = DebateOrchestrator(
            name=f"IC-{ticker}",
            personas=personas,
            data_package=data_package,
        )

        # Set message queue and phase gate for steering
        orchestrator.message_queue = message_queue
        orchestrator.phase_gate = phase_gate

        # Track agents done per phase for phase_complete events
        agent_count_in_phase = [0]
        num_personas = len(persona_names)

        def on_phase_start(phase_value):
            ui_queue.put({
                "type": "phase",
                "phase": phase_value,
                "timestamp": datetime.now().isoformat(),
            })

        def on_agent_start(agent_name, phase_value):
            ui_queue.put({
                "type": "agent_start",
                "agent": agent_name,
                "phase": phase_value,
            })

        def on_agent_done(agent_name, phase_value, actions):
            content = extract_talk_content(actions)
            ui_queue.put({
                "type": "message",
                "agent": agent_name,
                "phase": phase_value,
                "content": content,
                "timestamp": datetime.now().isoformat(),
            })
            agent_count_in_phase[0] += 1
            if agent_count_in_phase[0] >= num_personas:
                agent_count_in_phase[0] = 0
                # Don't pause after VERDICT (last phase)
                if phase_value != "final_verdict":
                    ui_queue.put({"type": "phase_complete", "phase": phase_value})

        orchestrator.on_phase_start = on_phase_start
        orchestrator.on_agent_start = on_agent_start
        orchestrator.on_agent_done = on_agent_done

        ui_queue.put({"type": "status", "status": "debating"})
        orchestrator.run_debate()

        if stop_event.is_set():
            ui_queue.put({"type": "status", "status": "error"})
            ui_queue.put({"type": "error", "message": "Debate cancelled"})
            return

        ui_queue.put({"type": "status", "status": "extracting"})
        votes = extract_votes(orchestrator)
        scorecard = build_scorecard(votes, ticker, data_package.company_name)
        transcript = orchestrator.pretty_current_interactions()

        result = DebateResult(
            ticker=ticker,
            company_name=data_package.company_name,
            scorecard=scorecard,
            phases_completed=orchestrator._phase_history,
            transcript=transcript,
        )

        ui_queue.put({
            "type": "complete",
            "result": result,
            "data_package_text": data_package.to_context_string(),
        })

    except Exception as e:
        # Try to build partial scorecard from whatever we have
        try:
            if 'orchestrator' in locals() and orchestrator._phase_history:
                from tinyic.debate.extraction import extract_votes, build_scorecard
                from tinyic.debate.models import DebateResult

                votes = extract_votes(orchestrator)
                scorecard = build_scorecard(votes, ticker, data_package.company_name)
                transcript = orchestrator.pretty_current_interactions()

                result = DebateResult(
                    ticker=ticker,
                    company_name=data_package.company_name,
                    scorecard=scorecard,
                    phases_completed=orchestrator._phase_history,
                    transcript=transcript,
                )
                ui_queue.put({"type": "partial_complete", "result": result, "error": str(e)})
                return
        except Exception:
            pass  # Partial extraction also failed

        ui_queue.put({"type": "error", "message": str(e)})


def _drain_queue():
    """Drain the ui_queue into session_state. Called from the main thread only."""
    ui_queue = st.session_state.get("ui_queue")
    if ui_queue is None:
        return

    while True:
        try:
            msg = ui_queue.get_nowait()
        except queue.Empty:
            break

        msg_type = msg["type"]
        if msg_type == "status":
            st.session_state.status = msg["status"]
        elif msg_type == "phase":
            st.session_state.current_phase = msg["phase"]
            st.session_state.current_agent = ""
            st.session_state.debate_log.append(msg)
        elif msg_type == "agent_start":
            st.session_state.current_agent = msg["agent"]
        elif msg_type == "message":
            st.session_state.current_agent = ""
            st.session_state.debate_log.append(msg)
        elif msg_type == "complete":
            st.session_state.debate_result = msg["result"]
            st.session_state.data_package_text = msg.get("data_package_text", "")
            st.session_state.status = "complete"
        elif msg_type == "error":
            st.session_state.error_message = msg["message"]
            st.session_state.status = "error"
        elif msg_type == "phase_complete":
            st.session_state.waiting_for_continue = True
        elif msg_type == "partial_complete":
            st.session_state.debate_result = msg["result"]
            st.session_state.incomplete_warning = True
            st.session_state.error_message = msg["error"]
            st.session_state.status = "complete"


def render_debate_section():
    """Render the debate section. Uses st.fragment for auto-polling during debate."""
    is_active = st.session_state.status in ("fetching", "debating", "extracting")
    run_every = 2 if is_active else None

    @st.fragment(run_every=run_every)
    def debate_fragment():
        """Fragment that polls queue and renders debate log. Reruns independently."""
        _drain_queue()

        for entry in st.session_state.debate_log:
            if entry["type"] == "phase":
                label = PHASE_LABELS.get(entry["phase"], entry["phase"])
                st.markdown(f"---")
                st.markdown(f"### {label}")
            elif entry["type"] == "message":
                with st.chat_message(name=entry["agent"]):
                    st.markdown(entry["content"])
            elif entry["type"] == "user":
                target = entry.get("target")
                prefix = f"*To {target}:* " if target else ""
                with st.chat_message(name="Moderator", avatar="🎙️"):
                    st.markdown(f"{prefix}{entry['content']}")

        if st.session_state.status == "debating" and st.session_state.current_agent:
            with st.chat_message(name=st.session_state.current_agent):
                st.markdown(f"*{st.session_state.current_agent} is analyzing...*")

        if st.session_state.status == "fetching":
            st.info("Fetching financial data, SEC filings, and market sentiment...")
        elif st.session_state.status == "extracting":
            st.info("Extracting final votes and building scorecard...")

    debate_fragment()


def render_scorecard():
    """Render the scorecard with consensus banner, vote table, transcript, and download."""
    result = st.session_state.debate_result
    if not result:
        return

    sc = result.scorecard

    st.divider()
    st.header("Investment Scorecard")

    # Consensus banner
    if sc.consensus:
        color_map = {"BUY": "#2e7d32", "HOLD": "#f57f17", "SELL": "#c62828"}
        bg = color_map.get(sc.consensus.value, "#616161")
        count_map = {"BUY": sc.bull_count, "HOLD": sc.hold_count, "SELL": sc.bear_count}
        count = count_map[sc.consensus.value]
        total = len(sc.votes)
        st.markdown(
            f'<div style="background-color:{bg};color:white;padding:1.5rem;'
            f'border-radius:0.5rem;text-align:center;margin:1rem 0;">'
            f'<h2 style="margin:0;color:white;">CONSENSUS: {sc.consensus.value}</h2>'
            f'<p style="margin:0.5rem 0 0 0;color:white;">{count}/{total} committee members</p>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="background-color:#616161;color:white;padding:1.5rem;'
            'border-radius:0.5rem;text-align:center;margin:1rem 0;">'
            '<h2 style="margin:0;color:white;">NO CONSENSUS</h2>'
            '<p style="margin:0.5rem 0 0 0;color:white;">Split vote among committee members</p>'
            '</div>',
            unsafe_allow_html=True,
        )

    # Vote table
    st.subheader("Individual Votes")
    vote_emoji = {"BUY": "🟢", "HOLD": "🟡", "SELL": "🔴"}

    for vote in sc.votes:
        cols = st.columns([2, 1, 1, 4])
        with cols[0]:
            st.markdown(f"**{vote.investor}**")
        with cols[1]:
            emoji = vote_emoji.get(vote.vote.value, "⚪")
            st.markdown(f"{emoji} **{vote.vote.value}**")
        with cols[2]:
            st.markdown(vote.confidence.value)
        with cols[3]:
            if vote.reasoning:
                st.markdown("; ".join(vote.reasoning[:3]))
            else:
                st.markdown("--")

    # Transcript expander
    with st.expander("View full debate transcript"):
        st.markdown(result.transcript or "No transcript available")

    # Raw data package expander
    data_text = st.session_state.get("data_package_text", "")
    if data_text:
        with st.expander("View raw data package"):
            st.text(data_text)

    # Download scorecard
    st.download_button(
        label="Download Scorecard (Markdown)",
        data=sc.to_markdown(),
        file_name=f"{result.ticker}_scorecard.md",
        mime="text/markdown",
    )

    # Re-run button
    if st.button("Re-run with same ticker"):
        st.session_state.status = "ready"
        st.session_state.debate_log = []
        st.session_state.debate_result = None
        st.rerun()


def _reset_state():
    """Reset all session state for a fresh debate."""
    stop_event = st.session_state.get("stop_event")
    if stop_event is not None:
        stop_event.set()

    for key in list(st.session_state.keys()):
        if key.startswith("persona_") or key == "ticker_input":
            continue
        del st.session_state[key]


def main():
    init_state()
    render_sidebar()

    if st.session_state.status == "idle":
        st.title("openIC")
        st.markdown(
            "Enter a stock ticker in the sidebar to start an investment committee debate. "
            "Six legendary investors will analyze the company and deliver their verdicts."
        )

    elif st.session_state.status == "ready":
        st.title(f"{st.session_state.company_name}")
        st.markdown(
            f"Ready to debate **{st.session_state.company_name} ({st.session_state.ticker})**. "
            "Select your committee members and click **Start Debate**."
        )

    elif st.session_state.status in ("fetching", "debating", "extracting"):
        st.title(f"Investment Committee: {st.session_state.company_name}")
        render_debate_section()  # st.fragment handles polling + rendering

        # Phase pause: show Continue button between phases (outside fragment)
        if st.session_state.waiting_for_continue:
            st.info(f"**{PHASE_LABELS.get(st.session_state.current_phase, '')}** complete. Review the discussion, then continue.")
            cols = st.columns([1, 3])
            with cols[0]:
                if st.button("Continue to next phase", type="primary"):
                    st.session_state.waiting_for_continue = False
                    st.session_state.phase_gate.set()
                    st.rerun()

        # Chat input for user steering (outside fragment, in main body)
        if st.session_state.status == "debating":
            if user_input := st.chat_input("Ask a question or steer the debate (@name to target)"):
                clean_text, target = parse_user_message(user_input)

                # Add to display log
                st.session_state.debate_log.append({
                    "type": "user",
                    "content": clean_text,
                    "target": target,
                    "timestamp": datetime.now().isoformat(),
                })

                # Enqueue for orchestrator
                st.session_state.message_queue.put((clean_text, target))

                # If waiting for continue, also signal the gate
                if st.session_state.waiting_for_continue:
                    st.session_state.waiting_for_continue = False
                    st.session_state.phase_gate.set()

                st.rerun()

    elif st.session_state.status == "complete":
        st.title(f"Investment Committee: {st.session_state.company_name}")
        if st.session_state.incomplete_warning:
            phases_done = len(st.session_state.debate_result.phases_completed) if st.session_state.debate_result else 0
            st.warning(
                f"Debate incomplete ({phases_done} of 4 phases). "
                "Scorecard based on available discussion. "
                f"Error: {st.session_state.error_message}"
            )
        _drain_queue()
        render_debate_section()
        render_scorecard()

    elif st.session_state.status == "error":
        st.title("Something went wrong")
        _drain_queue()
        st.error(st.session_state.error_message)
        if st.session_state.debate_log:
            st.markdown("### Partial debate transcript:")
            render_debate_section()


if not _TESTING:
    main()
