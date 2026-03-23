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

    cost_stats = orchestrator.get_cost_stats()

    votes = extract_votes(orchestrator)
    scorecard = build_scorecard(votes, ticker, data_package.company_name)
    transcript = orchestrator.pretty_current_interactions()

    return DebateResult(
        ticker=ticker,
        company_name=data_package.company_name,
        scorecard=scorecard,
        phases_completed=orchestrator._phase_history,
        transcript=transcript,
        cost_stats=cost_stats,
    )


def get_debate_cost_stats(result: DebateResult) -> dict:
    """Get formatted cost statistics from a completed debate.

    Returns dict with token counts and estimated USD cost.
    Returns empty stats dict if cost_stats not available.
    """
    if not result.cost_stats:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "model_calls": 0,
            "cached_calls": 0,
            "estimated_cost_usd": 0.0,
        }

    stats = result.cost_stats
    base = stats.get("base_stats", stats)  # handle both TinyWorld and raw formats

    # Estimate cost (GPT-5.2 pricing approximation)
    input_cost = base.get("input_tokens", 0) / 1_000_000 * 2.50
    output_cost = base.get("output_tokens", 0) / 1_000_000 * 10.00

    return {
        "input_tokens": base.get("input_tokens", 0),
        "output_tokens": base.get("output_tokens", 0),
        "total_tokens": base.get("total_tokens", 0),
        "model_calls": base.get("model_calls", 0),
        "cached_calls": base.get("cached_calls", 0),
        "estimated_cost_usd": round(input_cost + output_cost, 4),
    }


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
    "get_debate_cost_stats",
]
