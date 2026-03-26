"""TineyIC -- AI Investment Committee Simulator (Streamlit app)."""

import streamlit as st
import threading
import queue
from datetime import datetime
import re

_TESTING = False
try:
    st.set_page_config(
        page_title="TineyIC | Investment Committee",
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


def _inject_custom_css():
    """Inject custom CSS for a premium financial editorial aesthetic.

    Typography: Newsreader (serif headings) + DM Sans (body) + JetBrains Mono (data).
    Palette: Deep navy sidebar, warm paper tones, copper/gold accents.
    """
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;0,6..72,700;1,6..72,400&family=DM+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400&family=JetBrains+Mono:wght@400;500&display=swap');

    /* ── Base typography ────────────────────────────────── */
    .stApp, .stApp p, .stApp li, .stApp span {
        font-family: 'DM Sans', -apple-system, sans-serif !important;
    }

    .stApp h1, .stApp h2, .stApp h3, .stApp h4 {
        font-family: 'Newsreader', Georgia, serif !important;
        letter-spacing: -0.025em;
        color: #0a1628 !important;
    }

    .stApp h1 { font-weight: 700; font-size: 2.2rem !important; }
    .stApp h2 { font-weight: 600; font-size: 1.6rem !important; }
    .stApp h3 { font-weight: 600; font-size: 1.25rem !important; }

    /* ── Sidebar ── dark financial terminal ──────────────── */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0a1628 0%, #111d35 50%, #0d1829 100%) !important;
    }

    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] h4 {
        color: #f0ece4 !important;
        font-family: 'Newsreader', Georgia, serif !important;
    }

    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] span {
        color: #c4bfb4 !important;
    }

    [data-testid="stSidebar"] hr {
        border-color: rgba(255,255,255,0.08) !important;
    }

    [data-testid="stSidebar"] input {
        background: rgba(255,255,255,0.06) !important;
        border-color: rgba(255,255,255,0.12) !important;
        color: #f0ece4 !important;
    }

    [data-testid="stSidebar"] [data-baseweb="select"] {
        background: rgba(255,255,255,0.06) !important;
    }

    [data-testid="stSidebar"] [data-baseweb="select"] * {
        color: #d4cfc4 !important;
    }

    [data-testid="stSidebar"] [data-testid="stAlertContainer"] p {
        color: inherit !important;
    }

    /* ── Primary button ── gold accent ──────────────────── */
    button[data-testid="stBaseButton-primary"] {
        background: linear-gradient(135deg, #b8860b 0%, #d4a843 100%) !important;
        color: #0a1628 !important;
        border: none !important;
        font-weight: 700 !important;
        font-family: 'DM Sans', sans-serif !important;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        transition: all 0.2s ease !important;
    }

    button[data-testid="stBaseButton-primary"]:hover {
        background: linear-gradient(135deg, #d4a843 0%, #e6c05c 100%) !important;
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(184, 134, 11, 0.3) !important;
    }

    /* ── Metric cards ──────────────────────────────────── */
    [data-testid="stMetric"] {
        background: #f8f7f4;
        border: 1px solid #e8e4da;
        border-radius: 8px;
        padding: 14px 16px !important;
        transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }

    [data-testid="stMetric"]:hover {
        border-color: #d4cfc4;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }

    [data-testid="stMetricLabel"] {
        font-family: 'DM Sans', sans-serif !important;
        font-size: 0.72rem !important;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #6b6560 !important;
        font-weight: 600 !important;
    }

    [data-testid="stMetricValue"] {
        font-family: 'JetBrains Mono', monospace !important;
        font-weight: 500 !important;
        color: #0a1628 !important;
    }

    /* ── Tabs ──────────────────────────────────────────── */
    button[data-baseweb="tab"] {
        font-family: 'DM Sans', sans-serif !important;
        font-weight: 600 !important;
        letter-spacing: 0.01em;
    }

    /* ── Download buttons ── dark premium ───────────────── */
    .stDownloadButton > button {
        background: #0a1628 !important;
        color: #f0ece4 !important;
        border: none !important;
        font-family: 'DM Sans', sans-serif !important;
        font-weight: 600 !important;
        letter-spacing: 0.02em;
        transition: all 0.2s ease !important;
    }

    .stDownloadButton > button:hover {
        background: #162a4a !important;
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(10, 22, 40, 0.2) !important;
    }

    /* ── Chat messages ── warm paper ───────────────────── */
    [data-testid="stChatMessage"] {
        border-radius: 8px !important;
        border: 1px solid #eae6de !important;
        background: #fdfcfa !important;
    }

    /* ── Expanders ─────────────────────────────────────── */
    [data-testid="stExpander"] {
        border: 1px solid #e8e4da !important;
        border-radius: 8px !important;
    }

    /* ── Dividers, alerts, captions ────────────────────── */
    hr { border-color: #e8e4da !important; }

    .stAlert { border-radius: 8px !important; }

    .stCaption p {
        font-size: 0.8rem !important;
        color: #8a857d !important;
    }
    </style>
    """, unsafe_allow_html=True)


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
        "_last_query": "",
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
        "sidebar_financials": None,
        "sidebar_description": None,
        "sidebar_fetched_at": None,
        "sidebar_warnings": [],
        "sidebar_price_history": [],
        "data_package": None,
        "selected_model": "",
        "deep_research": True,
        "research_available": False,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def render_sidebar():
    """Render the sidebar with ticker input, persona selection, and controls."""
    with st.sidebar:
        st.title("TineyIC")
        st.caption("AI Investment Committee Simulator")

        disabled = st.session_state.status in ("fetching", "debating", "extracting", "generating")

        query = st.text_input(
            "Company or Ticker",
            placeholder="e.g., AAPL, Apple, 0700.HK",
            disabled=disabled,
            key="ticker_input",
        )

        if query and query.strip() != st.session_state.get("_last_query", ""):
            st.session_state["_last_query"] = query.strip()
            with st.spinner("Resolving..."):
                _resolve_ticker(query.strip())
        elif not query:
            st.session_state["_last_query"] = ""
            st.session_state.ticker = ""
            st.session_state.company_name = ""
            if st.session_state.status == "ready":
                st.session_state.status = "idle"

        if st.session_state.company_name:
            st.success(f"{st.session_state.company_name} ({st.session_state.ticker})")
        elif st.session_state.get("_last_query") and st.session_state.status == "idle":
            st.error("Could not resolve. Try a ticker symbol or full company name.")

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

        st.subheader("Model")
        from tinyic.constants import MODEL_OPTIONS
        from tinytroupe import config_manager

        model_labels = [f"{m['display_name']} — {m['description']}" for m in MODEL_OPTIONS]
        model_ids = [m["id"] for m in MODEL_OPTIONS]

        current_model = config_manager.get("model")
        default_idx = model_ids.index(current_model) if current_model in model_ids else 0

        selected_idx = st.selectbox(
            "LLM Model",
            range(len(model_labels)),
            format_func=lambda i: model_labels[i],
            index=default_idx,
            disabled=disabled,
            key="model_select",
        )
        st.session_state.selected_model = model_ids[selected_idx]

        st.divider()

        st.subheader("Research")
        st.session_state.deep_research = st.checkbox(
            "Deep Research",
            value=st.session_state.deep_research,
            disabled=disabled,
            key="deep_research_toggle",
            help="When enabled, performs web search + AI synthesis to produce a comprehensive research brief before the debate. Adds 15-30 seconds but significantly enriches persona context.",
        )

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


def _resolve_ticker(query):
    """Resolve ticker or company name. Updates session_state."""
    from tinyic.data.ticker_resolver import resolve_ticker
    is_valid, symbol, name = resolve_ticker(query)
    if is_valid:
        st.session_state.ticker = symbol
        st.session_state.company_name = name
        st.session_state.status = "ready"
    else:
        st.session_state.ticker = ""
        st.session_state.company_name = ""
        st.session_state.status = "idle"


def render_data_sidebar():
    """Render the company data sidebar panel with financials, chart, and warnings.

    Shows key metrics, price chart, data freshness, and any data source warnings.
    Only renders when data is available (after data_ready event).
    """
    financials = st.session_state.get("sidebar_financials")
    if financials is None:
        return  # No data yet

    st.markdown(
        '<p style="font-family:\'DM Sans\',sans-serif;font-size:0.7rem;font-weight:700;'
        'text-transform:uppercase;letter-spacing:0.1em;color:#6b6560;margin:0 0 4px 0;">'
        'Company Data</p>',
        unsafe_allow_html=True,
    )

    # Description
    desc = st.session_state.get("sidebar_description")
    if desc:
        st.caption(desc)

    # Key metrics in 2-column grid
    col1, col2 = st.columns(2)

    def _fmt_large(val):
        """Format large numbers (market cap, revenue) with B/M suffix."""
        if val is None:
            return "N/A"
        if abs(val) >= 1e12:
            return f"${val / 1e12:.1f}T"
        if abs(val) >= 1e9:
            return f"${val / 1e9:.1f}B"
        if abs(val) >= 1e6:
            return f"${val / 1e6:.1f}M"
        return f"${val:,.0f}"

    def _fmt_ratio(val):
        if val is None:
            return "N/A"
        return f"{val:.1f}x"

    def _fmt_pct(val):
        if val is None:
            return "N/A"
        return f"{val * 100:.1f}%"

    with col1:
        st.metric("P/E Ratio", _fmt_ratio(financials.get("pe_ratio")))
        st.metric("Revenue", _fmt_large(financials.get("revenue")))
        st.metric("ROE", _fmt_pct(financials.get("roe")))

    with col2:
        st.metric("Market Cap", _fmt_large(financials.get("market_cap")))
        st.metric("Profit Margin", _fmt_pct(financials.get("profit_margin")))
        st.metric("D/E Ratio", _fmt_ratio(financials.get("debt_to_equity")))

    # Price chart
    price_history = st.session_state.get("sidebar_price_history", [])
    if price_history:
        import pandas as pd
        chart_df = pd.DataFrame(price_history)
        chart_df["date"] = pd.to_datetime(chart_df["date"])
        chart_df = chart_df.set_index("date")
        st.line_chart(chart_df["close"], use_container_width=True)

    # Data freshness
    fetched_at = st.session_state.get("sidebar_fetched_at")
    if fetched_at:
        st.caption(f"Data fetched: {fetched_at[:19].replace('T', ' ')}")

    # Research enrichment indicator
    if st.session_state.get("research_available", False):
        st.caption("Enhanced with deep research")

    # Warnings
    warnings = st.session_state.get("sidebar_warnings", [])
    if warnings:
        for w in warnings:
            st.warning(w, icon="\u26a0\ufe0f")


def _render_cost_display():
    """Display per-debate token usage and estimated cost.

    Cost stats flow: orchestrator.get_cost_stats() -> DebateResult.cost_stats
    -> get_debate_cost_stats() formats into display-ready dict -> st.metric renders.
    """
    result = st.session_state.get("debate_result")
    if not result or not result.cost_stats:
        return

    from tinyic.debate import get_debate_cost_stats
    stats = get_debate_cost_stats(result)

    st.divider()
    st.markdown(
        '<p style="font-family:\'DM Sans\',sans-serif;font-size:0.7rem;font-weight:700;'
        'text-transform:uppercase;letter-spacing:0.1em;color:#6b6560;margin:0 0 12px 0;">'
        'Debate Cost</p>',
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Input Tokens", f"{stats['input_tokens']:,}")
    with c2:
        st.metric("Output Tokens", f"{stats['output_tokens']:,}")
    with c3:
        st.metric("Estimated Cost", f"${stats['estimated_cost_usd']:.4f}")


def render_memo_tab():
    """Render the investment memo with section-by-section display and grounding metadata."""
    result = st.session_state.get("debate_result")
    if not result or not result.memo:
        st.info("Memo not available. Generation may have been skipped or failed.")
        return

    memo = result.memo

    sections = [
        memo.executive_summary,
        memo.investment_thesis,
        memo.key_risks,
        memo.valuation_discussion,
        memo.final_verdict,
    ]

    for section in sections:
        st.markdown(f"### {section.title}")
        st.markdown(section.content)

        if section.contributing_personas:
            st.caption(f"Contributors: {', '.join(section.contributing_personas)}")
        if section.supporting_data:
            st.caption(f"Data references: {', '.join(section.supporting_data)}")

        st.divider()


def render_disagreements_tab():
    """Render the disagreement analysis with dimensions, sides, and evidence quotes."""
    result = st.session_state.get("debate_result")
    if not result or not result.disagreement_analysis:
        st.info("Disagreement analysis not available.")
        return

    analysis = result.disagreement_analysis

    if not analysis.disagreements:
        st.info("No significant disagreements identified.")
        return

    for i, d in enumerate(analysis.disagreements, 1):
        st.markdown(f"### {i}. {d.dimension}")
        st.markdown(d.description)

        for side in d.sides:
            persona = side.get("persona", "Unknown")
            position = side.get("position", "")
            quote = side.get("evidence_quote", "")

            st.markdown(f"**{persona}:** {position}")
            if quote:
                st.markdown(f'> "{quote}"')

        if d.resolution:
            st.markdown(f"**Resolution:** {d.resolution}")

        st.divider()


def _render_download_buttons():
    """Render download buttons for all debate artifacts."""
    result = st.session_state.get("debate_result")
    if not result:
        return

    st.divider()
    st.markdown(
        '<p style="font-family:\'DM Sans\',sans-serif;font-size:0.7rem;font-weight:700;'
        'text-transform:uppercase;letter-spacing:0.1em;color:#6b6560;margin:0 0 12px 0;">'
        'Downloads</p>',
        unsafe_allow_html=True,
    )

    ticker = result.ticker

    # Scorecard (Markdown) -- always available
    st.download_button(
        label="Scorecard (Markdown)",
        data=result.scorecard.to_markdown(),
        file_name=f"{ticker}_scorecard.md",
        mime="text/markdown",
        key="dl_scorecard",
    )

    # Investment Memo (Markdown) -- available if memo was generated
    if result.memo:
        memo_md = result.memo.to_markdown()
        st.download_button(
            label="Investment Memo (Markdown)",
            data=memo_md,
            file_name=f"{ticker}_memo.md",
            mime="text/markdown",
            key="dl_memo_md",
        )

        # Investment Memo (DOCX) -- only if pandoc available
        from tinyic.export import has_pandoc
        if has_pandoc():
            import tempfile
            from tinyic.export import ExportManager
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = ExportManager(tmpdir)
                docx_path = mgr.export_docx(memo_md, f"{ticker}_memo")
                if docx_path and docx_path.exists():
                    st.download_button(
                        label="Investment Memo (DOCX)",
                        data=docx_path.read_bytes(),
                        file_name=f"{ticker}_memo.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key="dl_memo_docx",
                    )

    # Transcript (Markdown) -- available if transcript exists
    if result.transcript:
        transcript_md = f"# Debate Transcript: {result.company_name} ({ticker})\n\n{result.transcript}"
        st.download_button(
            label="Transcript (Markdown)",
            data=transcript_md,
            file_name=f"{ticker}_transcript.md",
            mime="text/markdown",
            key="dl_transcript",
        )


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
    selected_model = st.session_state.selected_model
    deep_research = st.session_state.deep_research

    thread = threading.Thread(
        target=_debate_worker,
        args=(ticker, persona_names, ui_queue, stop_event, message_queue, phase_gate),
        kwargs={"selected_model": selected_model, "deep_research": deep_research},
        daemon=True,
    )
    thread.start()


def _debate_worker(ticker, persona_names, ui_queue, stop_event, message_queue=None, phase_gate=None, selected_model=None, deep_research=True):
    """Background thread: fetch data -> run debate -> extract votes -> scorecard.

    IMPORTANT: This function NEVER reads or writes st.session_state.
    All communication with the UI thread is via ui_queue.put().
    """
    try:
        from tinytroupe import config_manager

        original_model = config_manager.get("model")
        if selected_model:
            config_manager.update("model", selected_model)

        try:
            from tinyic.data.pipeline import build_data_package
            from tinyic.personas.registry import load_persona
            from tinyic.debate.orchestrator import DebateOrchestrator
            from tinyic.debate.extraction import extract_votes, build_scorecard
            from tinyic.debate.models import DebateResult

            data_package = build_data_package(ticker, deep_research=deep_research)

            # Fetch price history for sidebar chart (UI-only, not for LLM context)
            from tinyic.data.financials import fetch_price_history
            price_history = fetch_price_history(ticker)

            # Signal UI with data package fields for sidebar (before debate starts)
            ui_queue.put({
                "type": "data_ready",
                "financials": data_package.financials.model_dump() if data_package.financials else None,
                "description": data_package.description,
                "fetched_at": data_package.fetched_at.isoformat(),
                "warnings": data_package.warnings,
                "price_history": price_history,
                "research_available": data_package.research_brief is not None,
            })

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
                cost_stats=orchestrator.get_cost_stats(),
            )

            # Generate memo and disagreement analysis (post-debate LLM calls)
            ui_queue.put({"type": "status", "status": "generating"})
            try:
                from tinyic.debate.memo import generate_memo, extract_disagreements
                result.memo = generate_memo(result, data_package)
                result.disagreement_analysis = extract_disagreements(result)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Post-debate generation failed: %s", e)
                # Memo/disagreement stay None -- UI handles gracefully

            ui_queue.put({
                "type": "complete",
                "result": result,
                "data_package": data_package,
                "data_package_text": data_package.to_context_string(),
            })
        finally:
            config_manager.update("model", original_model)

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
        elif msg_type == "data_ready":
            st.session_state.sidebar_financials = msg.get("financials")
            st.session_state.sidebar_description = msg.get("description")
            st.session_state.sidebar_fetched_at = msg.get("fetched_at")
            st.session_state.sidebar_warnings = msg.get("warnings", [])
            st.session_state.sidebar_price_history = msg.get("price_history", [])
            st.session_state.research_available = msg.get("research_available", False)
        elif msg_type == "message":
            st.session_state.current_agent = ""
            st.session_state.debate_log.append(msg)
        elif msg_type == "complete":
            st.session_state.debate_result = msg["result"]
            st.session_state.data_package = msg.get("data_package")
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
    is_active = st.session_state.status in ("fetching", "debating", "extracting", "generating")
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
            if st.session_state.get("deep_research", False):
                st.info("Fetching financial data, SEC filings, market sentiment, and performing deep research...")
            else:
                st.info("Fetching financial data, SEC filings, and market sentiment...")
        elif st.session_state.status == "extracting":
            st.info("Extracting final votes and building scorecard...")
        elif st.session_state.status == "generating":
            st.info("Generating investment memo and disagreement analysis...")

    debate_fragment()


def render_scorecard():
    """Render the scorecard with consensus banner, vote table, transcript, and download."""
    result = st.session_state.debate_result
    if not result:
        return

    sc = result.scorecard

    st.divider()
    st.header("Investment Scorecard")

    # Consensus banner -- premium gradient with vote breakdown
    _gradient_map = {
        "BUY":  ("#15803d", "#166534", "rgba(21,128,61,0.25)"),
        "HOLD": ("#b45309", "#92400e", "rgba(180,83,9,0.25)"),
        "SELL": ("#dc2626", "#991b1b", "rgba(220,38,38,0.25)"),
    }
    _fallback_gradient = ("#4b5563", "#374151", "rgba(75,85,99,0.25)")

    total = len(sc.votes)
    # Build vote pill HTML for banner
    _pill_data = []
    if sc.bull_count:
        _pill_data.append(("BUY", sc.bull_count, "#dcfce7", "#15803d"))
    if getattr(sc, "hold_count", 0):
        _pill_data.append(("HOLD", getattr(sc, "hold_count", 0), "#fef3c7", "#92400e"))
    if getattr(sc, "bear_count", 0):
        _pill_data.append(("SELL", getattr(sc, "bear_count", 0), "#fee2e2", "#991b1b"))
    pills_html = "".join(
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'padding:3px 14px;border-radius:100px;font-family:\'DM Sans\',sans-serif;'
        f'font-weight:700;font-size:0.72rem;letter-spacing:0.04em;">'
        f'{count} {label}</span>'
        for label, count, bg, fg in _pill_data
    )

    if sc.consensus:
        g1, g2, shadow = _gradient_map.get(sc.consensus.value, _fallback_gradient)
        verdict_text = sc.consensus.value
    else:
        g1, g2, shadow = _fallback_gradient
        verdict_text = "NO CONSENSUS"

    st.markdown(f'''
    <div style="
        background:linear-gradient(135deg,{g1} 0%,{g2} 100%);
        color:white;padding:2rem 2.5rem;border-radius:12px;text-align:center;
        margin:1.5rem 0;box-shadow:0 8px 32px {shadow};position:relative;overflow:hidden;
    ">
        <div style="position:absolute;top:0;left:0;right:0;bottom:0;
            background:radial-gradient(circle at 30% 50%,rgba(255,255,255,0.1) 0%,transparent 70%);
            pointer-events:none;"></div>
        <p style="margin:0;font-family:'DM Sans',sans-serif;font-size:0.7rem;
            text-transform:uppercase;letter-spacing:0.2em;opacity:0.85;color:white;">
            Committee Verdict</p>
        <h2 style="margin:0.3rem 0 0.6rem;font-family:'Newsreader',serif;font-size:2.4rem;
            font-weight:700;color:white;letter-spacing:-0.02em;">
            {verdict_text}</h2>
        <div style="display:flex;justify-content:center;gap:12px;margin-top:0.5rem;">
            {pills_html}
        </div>
    </div>
    ''', unsafe_allow_html=True)

    # Vote cards -- accent-bordered cards with badge + reasoning
    st.subheader("Individual Votes")
    _vote_style = {
        "BUY":  {"border": "#15803d", "badge_bg": "#dcfce7", "badge_fg": "#15803d"},
        "HOLD": {"border": "#d97706", "badge_bg": "#fef3c7", "badge_fg": "#92400e"},
        "SELL": {"border": "#dc2626", "badge_bg": "#fee2e2", "badge_fg": "#991b1b"},
    }
    _default_style = {"border": "#6b7280", "badge_bg": "#f3f4f6", "badge_fg": "#374151"}

    for vote in sc.votes:
        vs = _vote_style.get(vote.vote.value, _default_style)
        reasoning = "; ".join(vote.reasoning[:3]) if vote.reasoning else "\u2014"
        st.markdown(f'''
        <div style="display:flex;align-items:center;gap:16px;
            border:1px solid #e8e4da;border-left:4px solid {vs["border"]};
            border-radius:8px;padding:14px 18px;margin:8px 0;background:#fdfcfa;">
            <div style="flex:0 0 160px;">
                <div style="font-family:'Newsreader',serif;font-weight:600;font-size:1rem;color:#0a1628;">
                    {vote.investor}</div>
            </div>
            <div style="flex:0 0 80px;text-align:center;">
                <span style="display:inline-block;background:{vs["badge_bg"]};color:{vs["badge_fg"]};
                    padding:2px 12px;border-radius:100px;font-family:'DM Sans',sans-serif;
                    font-weight:700;font-size:0.72rem;letter-spacing:0.05em;">
                    {vote.vote.value}</span>
                <div style="font-size:0.68rem;color:#8a857d;margin-top:3px;
                    font-family:'DM Sans',sans-serif;">{vote.confidence.value}</div>
            </div>
            <div style="flex:1;font-size:0.85rem;color:#4a4540;line-height:1.5;
                font-family:'DM Sans',sans-serif;">{reasoning}</div>
        </div>
        ''', unsafe_allow_html=True)

    # Transcript expander
    with st.expander("View full debate transcript"):
        st.markdown(result.transcript or "No transcript available")

    # Raw data package expander
    data_text = st.session_state.get("data_package_text", "")
    if data_text:
        with st.expander("View raw data package"):
            st.text(data_text)

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
    _inject_custom_css()
    render_sidebar()

    if st.session_state.status == "idle":
        st.markdown(
            '<div style="margin:3rem 0 1.5rem;">'
            '<h1 style="font-family:\'Newsreader\',serif;font-size:3rem;font-weight:700;'
            'color:#0a1628;letter-spacing:-0.03em;margin:0;">open<span style="color:#b8860b;">IC</span></h1>'
            '<p style="font-family:\'DM Sans\',sans-serif;font-size:1rem;color:#6b6560;'
            'margin:0.5rem 0 0;max-width:520px;line-height:1.6;">'
            'Enter a stock ticker to convene your investment committee. '
            'Six legendary investors will analyze the company and deliver their verdicts.</p>'
            '</div>',
            unsafe_allow_html=True,
        )

    elif st.session_state.status == "ready":
        st.title(f"{st.session_state.company_name}")
        st.markdown(
            f"Ready to debate **{st.session_state.company_name} ({st.session_state.ticker})**. "
            "Select your committee members and click **Start Debate**."
        )

    elif st.session_state.status in ("fetching", "debating", "extracting", "generating"):
        st.title(f"Investment Committee: {st.session_state.company_name}")

        # Two-column layout: debate (left, wider) + data sidebar (right)
        has_data = st.session_state.get("sidebar_financials") is not None
        if has_data:
            debate_col, data_col = st.columns([2, 1])
        else:
            debate_col = st.container()
            data_col = None

        with debate_col:
            render_debate_section()

            if st.session_state.waiting_for_continue:
                st.info(f"**{PHASE_LABELS.get(st.session_state.current_phase, '')}** complete. Review the discussion, then continue.")
                cols = st.columns([1, 3])
                with cols[0]:
                    if st.button("Continue to next phase", type="primary"):
                        st.session_state.waiting_for_continue = False
                        st.session_state.phase_gate.set()
                        st.rerun()

            if st.session_state.status == "debating":
                if user_input := st.chat_input("Ask a question or steer the debate (@name to target)"):
                    clean_text, target = parse_user_message(user_input)
                    st.session_state.debate_log.append({
                        "type": "user",
                        "content": clean_text,
                        "target": target,
                        "timestamp": datetime.now().isoformat(),
                    })
                    st.session_state.message_queue.put((clean_text, target))
                    if st.session_state.waiting_for_continue:
                        st.session_state.waiting_for_continue = False
                        st.session_state.phase_gate.set()
                    st.rerun()

        if data_col is not None:
            with data_col:
                render_data_sidebar()

    elif st.session_state.status == "complete":
        st.title(f"Investment Committee: {st.session_state.company_name}")
        if st.session_state.incomplete_warning:
            phases_done = len(st.session_state.debate_result.phases_completed) if st.session_state.debate_result else 0
            st.warning(
                f"Debate incomplete ({phases_done} of 4 phases). "
                "Scorecard based on available discussion. "
                f"Error: {st.session_state.error_message}"
            )

        has_data = st.session_state.get("sidebar_financials") is not None
        if has_data:
            results_col, data_col = st.columns([2, 1])
        else:
            results_col = st.container()
            data_col = None

        with results_col:
            _drain_queue()
            render_debate_section()

            # Tabbed results view
            tab_scorecard, tab_memo, tab_disagreements = st.tabs(
                ["Scorecard", "Investment Memo", "Disagreements"]
            )

            with tab_scorecard:
                render_scorecard()
            with tab_memo:
                render_memo_tab()
            with tab_disagreements:
                render_disagreements_tab()

            # Downloads and cost (below tabs)
            _render_download_buttons()
            _render_cost_display()

        if data_col is not None:
            with data_col:
                render_data_sidebar()

    elif st.session_state.status == "error":
        st.title("Something went wrong")
        _drain_queue()
        st.error(st.session_state.error_message)
        if st.session_state.debate_log:
            st.markdown("### Partial debate transcript:")
            render_debate_section()


if not _TESTING:
    main()
