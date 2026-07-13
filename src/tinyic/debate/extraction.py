"""Vote extraction and scorecard building from debate results."""

import logging

from tinytroupe.extraction import ResultsExtractor

from .models import Confidence, Scorecard, Vote, VoteChoice
from .structured import StructuredVerdict

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


def _vote_from_structured(investor: str, verdict: StructuredVerdict) -> Vote:
    """Build a ``source="structured"`` vote from a parsed verdict block (FR-4.4)."""
    return Vote(
        investor=investor,
        vote=verdict.vote,
        confidence=verdict.confidence,
        reasoning=list(verdict.reasons),
        key_risks=list(verdict.risks),
        changed_mind=verdict.changed_mind,
        source="structured",
    )


def _fallback_vote(investor: str) -> Vote:
    """The HOLD/LOW vote used when LLM extraction fails or omits an agent."""
    return Vote(
        investor=investor,
        vote=VoteChoice.HOLD,
        confidence=Confidence.LOW,
        reasoning=["Extraction failed"],
        changed_mind=False,
        source="extracted",
    )


def _extract_votes_via_llm(orchestrator, agents: list) -> list[Vote]:
    """Extract ``source="extracted"`` votes for ``agents`` via ResultsExtractor.

    Sends each agent's interaction history to the LLM for structured extraction
    of vote, confidence, reasoning, and risks. Returns exactly one vote per agent
    in order, substituting a HOLD/LOW fallback wherever extraction fails.
    """
    if not agents:
        return []

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

    try:
        results = extractor.extract_results_from_agents(
            agents=agents,
            verbose=False,
        )
    except Exception as e:
        logger.warning("Extraction failed for all agents: %s", e)
        return [_fallback_vote(agent.name) for agent in agents]

    # The vendored extractor is expected to return one slot per agent, but
    # preserve committee cardinality if a provider/client regression returns
    # a short iterable. Extra results have no owning persona and are ignored.
    results = list(results or [])
    votes: list[Vote] = []
    for index, agent in enumerate(agents):
        raw_result = results[index] if index < len(results) else None
        if raw_result is None:
            votes.append(_fallback_vote(agent.name))
            continue

        try:
            votes.append(
                Vote(
                    investor=agent.name,
                    vote=raw_result.get("vote") or "HOLD",
                    confidence=raw_result.get("confidence") or "MEDIUM",
                    reasoning=_parse_list_field(raw_result.get("reasoning")),
                    key_risks=_parse_list_field(raw_result.get("key_risks")),
                    changed_mind=_parse_bool(raw_result.get("changed_mind", False)),
                    source="extracted",
                )
            )
        except Exception as e:
            logger.warning("Failed to parse vote for %s: %s", agent.name, e)
            votes.append(_fallback_vote(agent.name))

    return votes


def extract_votes(orchestrator) -> list[Vote]:
    """Extract each agent's final vote, structured records first (FR-4.4).

    Vote extraction consumes the moderator's recorded verdict blocks first: any
    persona whose mandated ``===VERDICT===`` block parsed yields a
    ``source="structured"`` vote with no model call. Only the remaining personas
    (block absent or malformed) go through the LLM extraction fallback, which
    yields ``source="extracted"`` votes. Agent order is preserved.

    Args:
        orchestrator: A completed DebateOrchestrator instance.

    Returns:
        List of Vote objects, one per participating agent.
    """
    agents = list(orchestrator.agents)
    moderator = getattr(orchestrator, "moderator", None)
    recorded = dict(getattr(moderator, "recorded_verdicts", None) or {})

    # Personas lacking a structured verdict fall back to LLM extraction; run it
    # once for just that subset (no call at all when every verdict was recorded).
    llm_agents = [agent for agent in agents if recorded.get(agent.name) is None]
    llm_votes = iter(_extract_votes_via_llm(orchestrator, llm_agents))

    votes: list[Vote] = []
    for agent in agents:
        verdict = recorded.get(agent.name)
        if verdict is not None:
            votes.append(_vote_from_structured(agent.name, verdict))
        else:
            votes.append(next(llm_votes))
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
