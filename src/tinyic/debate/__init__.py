"""Debate engine for structured multi-agent investment debates."""

import logging

from .models import DebatePhase, VoteChoice, Confidence, Vote, Scorecard, DebateResult
from .orchestrator import DebateOrchestrator
from .extraction import extract_votes, build_scorecard

from tinyic.constants import MIN_PERSONAS

logger = logging.getLogger(__name__)


def run_debate(
    ticker: str,
    persona_names: list[str],
    data_package=None,
) -> DebateResult:
    """Run a complete investment committee debate.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        persona_names: List of persona registry names (min 2).
        data_package: Pre-built DataPackage, or None to fetch live data.

    Returns:
        DebateResult with scorecard, transcript, and phase history.

    Raises:
        ValueError: If fewer than MIN_PERSONAS persona names provided.
    """
    if len(persona_names) < MIN_PERSONAS:
        raise ValueError(
            f"At least {MIN_PERSONAS} persona names required, got {len(persona_names)}"
        )

    from tinyic.personas.registry import load_persona
    from tinyic.data.pipeline import build_data_package as _build_data_package

    personas = [load_persona(name) for name in persona_names]

    if data_package is None:
        data_package = _build_data_package(ticker)

    orchestrator = DebateOrchestrator(
        name=f"IC-{ticker}",
        personas=personas,
        data_package=data_package,
    )
    orchestrator.run_debate()

    votes = extract_votes(orchestrator)
    scorecard = build_scorecard(votes, ticker, data_package.company_name)
    transcript = orchestrator.pretty_current_interactions()

    return DebateResult(
        ticker=ticker,
        company_name=data_package.company_name,
        scorecard=scorecard,
        phases_completed=orchestrator._phase_history,
        transcript=transcript,
    )


__all__ = [
    "DebateOrchestrator",
    "DebatePhase",
    "VoteChoice",
    "Confidence",
    "Vote",
    "Scorecard",
    "DebateResult",
    "extract_votes",
    "build_scorecard",
    "run_debate",
]
