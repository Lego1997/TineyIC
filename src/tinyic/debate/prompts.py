"""Phase-specific prompt templates for the debate engine."""

from .models import DebatePhase

# ---------------------------------------------------------------------------
# Structured trailing blocks (FR-4.4)
# ---------------------------------------------------------------------------
#
# The opening and verdict phases mandate a fenced trailing block so the engine
# can lift a structured record (stance/claims/confidence; vote/confidence/etc.)
# out of the persona's prose deterministically -- no second LLM call. Cross-exam
# and rebuttal stay free-form NL (the anti-"telephone effect" split). These
# blocks use angle-bracket placeholders, never ``{...}``, so they survive the
# ``PHASE_PROMPTS[...].format(company=...)`` substitution untouched. The
# ``===THESIS===`` / ``===VERDICT===`` / ``===END===`` markers are the contract
# parsed by :mod:`tinyic.debate.structured`.

THESIS_BLOCK: str = (
    "\n\n"
    "AFTER your prose, append a structured summary as the LAST thing you write, "
    "in EXACTLY this format with the marker lines verbatim:\n"
    "===THESIS===\n"
    "STANCE: <bullish|bearish|neutral>\n"
    "CONFIDENCE: <high|medium|low>\n"
    "CLAIMS:\n"
    "- <your first key claim>\n"
    "- <your second key claim>\n"
    "- <your third key claim>\n"
    "===END==="
)

VERDICT_BLOCK: str = (
    "\n\n"
    "AFTER your prose, append a structured verdict as the LAST thing you write, "
    "in EXACTLY this format with the marker lines verbatim:\n"
    "===VERDICT===\n"
    "VOTE: <BUY|HOLD|SELL>\n"
    "CONFIDENCE: <HIGH|MEDIUM|LOW>\n"
    "REASONS:\n"
    "- <your first reason>\n"
    "- <your second reason>\n"
    "- <your third reason>\n"
    "RISKS:\n"
    "- <top risk>\n"
    "CHANGED_MIND: <yes|no>\n"
    "===END==="
)

PHASE_PROMPTS: dict[DebatePhase, str] = {
    DebatePhase.OPENING: (
        "Present your opening investment thesis on {company}. "
        "State your preliminary assessment (bullish, bearish, or neutral) "
        "and your top 3 reasons, drawing on the financial data provided. "
        "Speak from YOUR unique investment philosophy. "
        "Be specific about which data points support your view."
        + THESIS_BLOCK
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
        + VERDICT_BLOCK
    ),
}

CONTEXT_PREAMBLE: str = (
    "You are participating in an investment committee debate about "
    "{company_name} ({ticker}). Here is the financial data package for "
    "your analysis:\n\n{context_data}"
)

# Per-turn anti-convergence reinforcement, tightened to ONE line (FR-4.3): the
# persona file's philosophy hook plus a single temperament-specific clause. The
# "IMPORTANT REMINDER" prefix and the verbatim philosophy hook are load-bearing
# for the injection assertions in the debate tests.
REINFORCEMENT_TEMPLATE: str = (
    "IMPORTANT REMINDER: You are {name} -- {philosophy_hook} {temperament_line}"
)

#: Temperament vocabulary for the persona ``temperament`` field (FR-4.3). The
#: default committee mixes these so the six voices do not drift uniformly
#: sycophantic; ``contrarian`` is the prompted hard dissenter.
VALID_TEMPERAMENTS: frozenset[str] = frozenset(
    {"conciliatory", "balanced", "contrarian"}
)
DEFAULT_TEMPERAMENT: str = "balanced"

#: The one temperament clause folded into each reinforcement line. The
#: contrarian clause is the accuracy-over-agreement mandate that keeps at least
#: one hard dissenter honest under group pressure.
TEMPERAMENT_REINFORCEMENT: dict[str, str] = {
    "contrarian": (
        "Prioritize accuracy over agreement: press your dissent and do NOT "
        "soften your position to match the committee."
    ),
    "conciliatory": (
        "Seek common ground only where the evidence earns it, and hold any view "
        "you still believe is correct."
    ),
    "balanced": (
        "Hold your own view; concede only where the evidence genuinely "
        "persuades you, and say so plainly when it does not."
    ),
}


def temperament_clause(temperament: str | None) -> str:
    """Return the reinforcement clause for ``temperament`` (balanced fallback)."""
    if not isinstance(temperament, str):
        return TEMPERAMENT_REINFORCEMENT[DEFAULT_TEMPERAMENT]
    return TEMPERAMENT_REINFORCEMENT.get(
        temperament.strip().casefold(),
        TEMPERAMENT_REINFORCEMENT[DEFAULT_TEMPERAMENT],
    )

# ---------------------------------------------------------------------------
# Rotating devil's advocate
# ---------------------------------------------------------------------------

DEVILS_ADVOCATE_PROMPT: str = (
    "SPECIAL ROLE FOR THIS PHASE: You have been designated as the Devil's "
    "Advocate. Regardless of your personal view, you MUST argue the "
    "STRONGEST possible counter-position to the emerging consensus. "
    "Challenge every assumption. Find the weakest points in the majority "
    "view. Present the best case for the opposite conclusion. This role "
    "applies ONLY to this cross-examination phase -- your final vote in "
    "the verdict phase should reflect your TRUE opinion."
)

ROLE_RELEASE_PROMPT: str = (
    "Your Devil's Advocate role has ended. From now on, argue and vote "
    "based on your TRUE investment conviction. Do not feel bound by the "
    "counter-arguments you made as Devil's Advocate -- return to your "
    "genuine perspective."
)
