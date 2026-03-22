"""Phase-specific prompt templates for the debate engine."""

from .models import DebatePhase

PHASE_PROMPTS: dict[DebatePhase, str] = {
    DebatePhase.OPENING: (
        "Present your opening investment thesis on {company}. "
        "State your preliminary assessment (bullish, bearish, or neutral) "
        "and your top 3 reasons, drawing on the financial data provided. "
        "Speak from YOUR unique investment philosophy. "
        "Be specific about which data points support your view."
    ),
    DebatePhase.CROSS_EXAM: (
        "Challenge the other committee members' arguments. "
        "Identify the WEAKEST point in each other investor's thesis. "
        "Ask probing questions. Point out data they may have overlooked or misinterpreted. "
        "Do NOT agree just to be polite -- genuine disagreement produces better analysis. "
        "Stay true to YOUR investment philosophy when critiquing others."
    ),
    DebatePhase.REBUTTAL: (
        "Respond to the challenges raised against your thesis. "
        "Defend your position where you believe you are right. "
        "Concede points where the criticism is valid, but explain what it changes. "
        "Refine your thesis based on the discussion -- you may adjust but must explain why. "
        "Do NOT simply agree with the majority."
    ),
    DebatePhase.VERDICT: (
        "Deliver your FINAL investment verdict on {company}. "
        "State clearly: BUY, HOLD, or SELL. "
        "State your confidence level: HIGH, MEDIUM, or LOW. "
        "Give your top 3 reasons for this verdict. "
        "If your view changed during the debate, explain what changed it."
    ),
}

CONTEXT_PREAMBLE: str = (
    "You are participating in an investment committee debate about "
    "{company_name} ({ticker}). Here is the financial data package for "
    "your analysis:\n\n{context_data}"
)
