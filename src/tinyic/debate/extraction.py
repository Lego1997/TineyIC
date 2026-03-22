"""Vote extraction and scorecard building from debate results."""

import logging
from typing import Union

from tinytroupe.extraction import ResultsExtractor

from .models import Confidence, Scorecard, Vote, VoteChoice

logger = logging.getLogger(__name__)


def _parse_list_field(raw: object) -> list[str]:
    """Parse a field that might be a string, list, or None into a list of strings."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        # Handle "- point 1\n- point 2" format
        lines = raw.split("\n")
        result = []
        for line in lines:
            cleaned = line.strip().lstrip("-\u2022*").strip()
            if cleaned:
                result.append(cleaned)
        return result if result else [raw.strip()] if raw.strip() else []
    return [str(raw)]


def _parse_bool(raw: object) -> bool:
    """Parse a boolean-ish value."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.lower() in ("true", "yes", "1")
    return False


def extract_votes(orchestrator) -> list[Vote]:
    """Extract structured votes from each agent after debate completion.

    Uses ResultsExtractor to send each agent's interaction history to the LLM
    for structured extraction of vote, confidence, reasoning, and risks.

    Args:
        orchestrator: A completed DebateOrchestrator instance.

    Returns:
        List of Vote objects, one per participating agent.
    """
    extractor = ResultsExtractor(
        extraction_objective=(
            "Extract the investor's FINAL investment verdict from the debate, "
            "including their vote, confidence level, and key reasoning."
        ),
        situation=(
            f"An investment committee debate about {orchestrator.data_package.company_name} "
            f"({orchestrator.data_package.ticker}) has concluded. "
            f"Extract each investor's final position."
        ),
        fields=["vote", "confidence", "reasoning", "key_risks", "changed_mind"],
        fields_hints={
            "vote": "Must be exactly one of: BUY, HOLD, or SELL",
            "confidence": "Must be exactly one of: HIGH, MEDIUM, or LOW",
            "reasoning": "List of 2-4 bullet points summarizing the investor's key arguments",
            "key_risks": "The top 1-2 risks the investor identified",
            "changed_mind": "Boolean: did the investor change their position during the debate?",
        },
    )

    votes: list[Vote] = []

    try:
        results = extractor.extract_results_from_agents(
            agents=orchestrator.agents,
            verbose=False,
        )
    except Exception as e:
        logger.warning("Extraction failed for all agents: %s", e)
        # Return fallback votes for all agents
        return [
            Vote(
                investor=agent.name,
                vote=VoteChoice.HOLD,
                confidence=Confidence.LOW,
                reasoning=["Extraction failed"],
                changed_mind=False,
            )
            for agent in orchestrator.agents
        ]

    for agent, raw_result in zip(orchestrator.agents, results):
        try:
            if raw_result is None:
                raw_result = {}

            vote = Vote(
                investor=agent.name,
                vote=raw_result.get("vote", "HOLD"),
                confidence=raw_result.get("confidence", "MEDIUM"),
                reasoning=_parse_list_field(raw_result.get("reasoning")),
                key_risks=_parse_list_field(raw_result.get("key_risks")),
                changed_mind=_parse_bool(raw_result.get("changed_mind", False)),
            )
            votes.append(vote)
        except Exception as e:
            logger.warning("Failed to parse vote for %s: %s", agent.name, e)
            votes.append(
                Vote(
                    investor=agent.name,
                    vote=VoteChoice.HOLD,
                    confidence=Confidence.LOW,
                    reasoning=["Extraction failed"],
                    changed_mind=False,
                )
            )

    return votes


def build_scorecard(votes: list[Vote], ticker: str, company_name: str) -> Scorecard:
    """Aggregate votes into a scorecard with consensus detection.

    Args:
        votes: List of Vote objects from extract_votes().
        ticker: Stock ticker symbol.
        company_name: Company display name.

    Returns:
        Scorecard with vote counts and consensus (if clear majority).
    """
    bull_count = sum(1 for v in votes if v.vote == VoteChoice.BUY)
    bear_count = sum(1 for v in votes if v.vote == VoteChoice.SELL)
    hold_count = sum(1 for v in votes if v.vote == VoteChoice.HOLD)

    # Consensus requires strict majority (> half)
    total = len(votes)
    consensus = None
    if total > 0:
        if bull_count > total / 2:
            consensus = VoteChoice.BUY
        elif bear_count > total / 2:
            consensus = VoteChoice.SELL
        elif hold_count > total / 2:
            consensus = VoteChoice.HOLD

    return Scorecard(
        ticker=ticker,
        company_name=company_name,
        votes=votes,
        consensus=consensus,
        bull_count=bull_count,
        bear_count=bear_count,
        hold_count=hold_count,
    )
