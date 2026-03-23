"""Investment memo generation and disagreement extraction via LLM synthesis."""

import json
import logging
from typing import Optional

from tinytroupe.clients import client
from tinytroupe.utils import extract_json

from .models import (
    DebateResult,
    Disagreement,
    DisagreementAnalysis,
    InvestmentMemo,
    MemoSection,
)

logger = logging.getLogger(__name__)

# Maximum transcript length (characters) to include in synthesis prompts.
# Prevents exceeding context window with large 6-persona debates.
MAX_TRANSCRIPT_LENGTH = 8000

MEMO_SYSTEM_PROMPT = (
    "You are an expert investment analyst synthesizing a structured investment memo "
    "from a committee debate.\n\n"
    "You will receive the full debate transcript, the scorecard with each investor's "
    "vote and reasoning, and the financial data package.\n\n"
    "Produce a JSON object with exactly these 5 sections:\n"
    "{\n"
    '    "executive_summary": {\n'
    '        "content": "2-3 paragraph executive summary of the committee findings",\n'
    '        "contributing_personas": ["names of personas whose arguments shaped this section"],\n'
    '        "supporting_data": ["specific data points from the financial package referenced"]\n'
    "    },\n"
    '    "investment_thesis": { same structure },\n'
    '    "key_risks": { same structure },\n'
    '    "valuation_discussion": { same structure },\n'
    '    "final_verdict": { same structure }\n'
    "}\n\n"
    "RULES:\n"
    "- Every claim must be grounded in the debate transcript or financial data\n"
    "- Do NOT introduce facts not discussed in the debate\n"
    "- Name specific personas when attributing arguments\n"
    "- Reference specific financial metrics from the data package\n"
    "- The final_verdict section must reflect the actual vote distribution\n"
    "- Each section's content should be 1-3 paragraphs\n"
    "- Return ONLY valid JSON, no other text"
)

DISAGREEMENT_SYSTEM_PROMPT = (
    "You are analyzing an investment committee debate to identify the key areas "
    "where investors disagreed.\n\n"
    "Produce a JSON object:\n"
    "{\n"
    '    "disagreements": [\n'
    "        {\n"
    '            "dimension": "Name of the disagreement (e.g., Valuation Methodology)",\n'
    '            "description": "1-2 sentence description of what they disagreed about",\n'
    '            "sides": [\n'
    "                {\n"
    '                    "persona": "Investor Name",\n'
    '                    "position": "Brief summary of their position",\n'
    '                    "evidence_quote": "Direct quote from the transcript"\n'
    "                }\n"
    "            ],\n"
    '            "resolution": "How or whether this was resolved in the final votes"\n'
    "        }\n"
    "    ]\n"
    "}\n\n"
    "RULES:\n"
    "- Return exactly 3 disagreements, ranked by significance\n"
    "- Each disagreement must have at least 2 sides\n"
    "- evidence_quote must be copied from the transcript (as close to verbatim as possible)\n"
    "- Focus on substantive investment disagreements, not stylistic differences\n"
    "- Return ONLY valid JSON, no other text"
)


def _truncate_transcript(transcript: str) -> str:
    """Truncate transcript to fit within prompt budget."""
    if not transcript or len(transcript) <= MAX_TRANSCRIPT_LENGTH:
        return transcript or ""
    # Keep the end (cross-exam, rebuttal, verdict) which is most informative
    return "..." + transcript[-(MAX_TRANSCRIPT_LENGTH - 3) :]


def _make_fallback_section(title: str, message: str) -> MemoSection:
    """Create a fallback memo section when generation fails."""
    return MemoSection(title=title, content=message)


def _make_fallback_memo(ticker: str, company_name: str, reason: str) -> InvestmentMemo:
    """Create a fallback memo when LLM synthesis fails."""
    fallback = _make_fallback_section
    msg = f"Memo generation failed: {reason}"
    return InvestmentMemo(
        ticker=ticker,
        company_name=company_name,
        executive_summary=fallback("Executive Summary", msg),
        investment_thesis=fallback("Investment Thesis", msg),
        key_risks=fallback("Key Risks", msg),
        valuation_discussion=fallback("Valuation Discussion", msg),
        final_verdict=fallback("Final Verdict", msg),
    )


def _parse_memo_section(raw: dict, title: str) -> MemoSection:
    """Parse a raw dict into a MemoSection, with defaults for missing fields."""
    if not isinstance(raw, dict):
        return _make_fallback_section(title, str(raw) if raw else "")
    return MemoSection(
        title=title,
        content=raw.get("content", ""),
        contributing_personas=raw.get("contributing_personas", []),
        supporting_data=raw.get("supporting_data", []),
    )


def generate_memo(
    debate_result: DebateResult,
    data_package,
) -> InvestmentMemo:
    """Generate an investment memo from debate results via LLM synthesis.

    Args:
        debate_result: Completed debate with transcript and scorecard.
        data_package: Financial data package used in the debate.

    Returns:
        InvestmentMemo with 5 structured sections. Returns a fallback memo
        if LLM synthesis fails.
    """
    transcript = _truncate_transcript(debate_result.transcript)
    scorecard_md = debate_result.scorecard.to_markdown()
    context_str = data_package.to_context_string()

    user_prompt = (
        f"## Debate Transcript\n{transcript}\n\n"
        f"## Scorecard\n{scorecard_md}\n\n"
        f"## Financial Data\n{context_str}"
    )

    messages = [
        {"role": "system", "content": MEMO_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = client().send_message(messages, temperature=0.7)
        data = extract_json(response["content"])
    except Exception as e:
        logger.warning("Memo generation LLM call failed: %s", e)
        return _make_fallback_memo(
            debate_result.ticker, debate_result.company_name, str(e)
        )

    if not data:
        logger.warning("Memo generation returned empty/invalid JSON")
        return _make_fallback_memo(
            debate_result.ticker,
            debate_result.company_name,
            "Invalid JSON response",
        )

    section_keys = [
        ("executive_summary", "Executive Summary"),
        ("investment_thesis", "Investment Thesis"),
        ("key_risks", "Key Risks"),
        ("valuation_discussion", "Valuation Discussion"),
        ("final_verdict", "Final Verdict"),
    ]

    sections = {}
    for key, title in section_keys:
        sections[key] = _parse_memo_section(data.get(key, {}), title)

    return InvestmentMemo(
        ticker=debate_result.ticker,
        company_name=debate_result.company_name,
        **sections,
    )


def extract_disagreements(
    debate_result: DebateResult,
) -> DisagreementAnalysis:
    """Extract top disagreements from a debate transcript via LLM analysis.

    Args:
        debate_result: Completed debate with transcript.

    Returns:
        DisagreementAnalysis with up to 3 disagreements. Returns a fallback
        with empty disagreements list if extraction fails.
    """
    transcript = _truncate_transcript(debate_result.transcript)
    scorecard_md = debate_result.scorecard.to_markdown()

    user_prompt = (
        f"## Debate Transcript\n{transcript}\n\n"
        f"## Scorecard\n{scorecard_md}"
    )

    messages = [
        {"role": "system", "content": DISAGREEMENT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = client().send_message(messages, temperature=0.7)
        data = extract_json(response["content"])
    except Exception as e:
        logger.warning("Disagreement extraction LLM call failed: %s", e)
        return DisagreementAnalysis(
            ticker=debate_result.ticker,
            company_name=debate_result.company_name,
        )

    if not data or "disagreements" not in data:
        logger.warning("Disagreement extraction returned empty/invalid JSON")
        return DisagreementAnalysis(
            ticker=debate_result.ticker,
            company_name=debate_result.company_name,
        )

    disagreements = []
    for raw_d in data["disagreements"][:3]:  # Cap at 3
        if not isinstance(raw_d, dict):
            continue
        disagreements.append(
            Disagreement(
                dimension=raw_d.get("dimension", "Unknown"),
                description=raw_d.get("description", ""),
                sides=raw_d.get("sides", []),
                resolution=raw_d.get("resolution", ""),
            )
        )

    return DisagreementAnalysis(
        ticker=debate_result.ticker,
        company_name=debate_result.company_name,
        disagreements=disagreements,
    )
