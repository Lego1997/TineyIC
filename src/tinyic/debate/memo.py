"""Investment memo generation and disagreement extraction via LLM synthesis.

FR-4.5: the memo/disagreement writer is the committee's **aggregator binding**.
It runs inside the aggregator's active routing scope (the orchestrator activates
it), so the vendored ``client()`` here resolves to the aggregator client.

The synthesis follows the Together Mixture-of-Agents pattern
(docs/research/2026-07-12-model-agnostic-brief.md section 2): the persona
speeches are advisor proposals, and this aggregator *critically evaluates* them —
Together's Apache-2.0 aggregator framing ("some of it may be biased or
incorrect") is vendored into the system prompts below. The structured records
(opening theses + final verdicts) are supplied as the **primary grounding**,
with the windowed prose as corroborating context.
"""

import json
import logging
from collections.abc import Mapping, Sequence

from tinytroupe.clients import client
from tinytroupe.utils import extract_json

from .analytics import analyze_collapse, render_structured_grounding
from .models import (
    DebateResult,
    Disagreement,
    DisagreementAnalysis,
    InvestmentMemo,
    MemoSection,
    Vote,
)
from .structured import StructuredThesis

logger = logging.getLogger(__name__)

# A transcript is processed losslessly in sequential windows. Each later call
# receives a bounded representation of the structured draft from earlier
# windows plus one new source slice. Fixed slots prevent an expanding synthesis
# or data package from crowding the next transcript window out of context.
TRANSCRIPT_WINDOW_LENGTH = 6000
RUNNING_SYNTHESIS_MAX_LENGTH = 4000
SCORECARD_CONTEXT_MAX_LENGTH = 2500
FINANCIAL_CONTEXT_MAX_LENGTH = 5000
# The structured records are compact (a handful of lines per persona); this slot
# only bounds a pathological committee so it can never crowd out the transcript.
STRUCTURED_RECORDS_MAX_LENGTH = 3000

# Together MoA aggregator framing (Apache-2.0), vendored per FR-4.5. Prepended to
# both writer prompts so the aggregator treats the personas' speech as advisory
# and potentially biased rather than authoritative.
_MOA_SKEPTICAL_FRAMING = (
    "You have been provided with a set of arguments from an investment "
    "committee of distinct investor personas. It is crucial to critically "
    "evaluate the information in these arguments, recognizing that some of it "
    "may be biased or incorrect. Do not simply replicate what a persona said; "
    "weigh it against the structured records and the financial data, and prefer "
    "the primary grounding (the personas' recorded theses and verdicts) when "
    "sources conflict.\n\n"
)

MEMO_SYSTEM_PROMPT = (
    _MOA_SKEPTICAL_FRAMING
    + "You are an expert investment analyst synthesizing a structured investment memo "
    "from a committee debate.\n\n"
    "You will receive the structured records (each persona's recorded opening "
    "thesis and final verdict) as the PRIMARY GROUNDING, plus one window of a "
    "debate transcript, the running memo draft from earlier windows, the "
    "scorecard with each investor's vote and reasoning, and the financial data "
    "package. Revise the complete draft to incorporate the new window without "
    "dropping well-grounded earlier findings.\n\n"
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
    "- Every claim must be grounded in the structured records, debate transcript, or financial data\n"
    "- Do NOT introduce facts not discussed in the debate\n"
    "- Name specific personas when attributing arguments\n"
    "- Reference specific financial metrics from the data package\n"
    "- The final_verdict section must reflect the actual vote distribution\n"
    "- Each section's content should be 1-3 paragraphs\n"
    "- Return ONLY valid JSON, no other text"
)

DISAGREEMENT_SYSTEM_PROMPT = (
    _MOA_SKEPTICAL_FRAMING
    + "You are analyzing an investment committee debate to identify the key areas "
    "where investors disagreed. You will receive the structured records (each "
    "persona's recorded opening thesis and final verdict) as the PRIMARY "
    "GROUNDING, one transcript window, and the running structured analysis from "
    "earlier windows. Revise the complete analysis without dropping well-grounded "
    "earlier evidence.\n\n"
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


def _transcript_windows(transcript: str | None) -> list[str]:
    """Split a transcript into ordered, lossless, bounded character windows."""
    text = transcript or ""
    if not text:
        return [""]
    return [
        text[start : start + TRANSCRIPT_WINDOW_LENGTH]
        for start in range(0, len(text), TRANSCRIPT_WINDOW_LENGTH)
    ]


def _bounded_slot(text: str, max_length: int) -> str:
    """Bound one prompt component while retaining both its start and end."""
    if len(text) <= max_length:
        return text
    marker = "\n...[content compacted to reserved prompt slot]...\n"
    remaining = max_length - len(marker)
    head_length = remaining * 2 // 3
    tail_length = remaining - head_length
    return f"{text[:head_length]}{marker}{text[-tail_length:]}"


def _running_draft_json(data: dict | None) -> str:
    """Render the prior structured synthesis for the next sequential window."""
    if data is None:
        return "(No prior draft; this is the first transcript window.)"
    return _bounded_slot(
        json.dumps(data, indent=2, ensure_ascii=False),
        RUNNING_SYNTHESIS_MAX_LENGTH,
    )


def _grounding_block(
    theses: Mapping[str, StructuredThesis] | None,
    votes: Sequence[Vote],
) -> str | None:
    """Render the structured-records primary-grounding slot, if records exist.

    Returns ``None`` when no structured theses were passed (the legacy
    transcript-only path), so the prompt is byte-identical to the pre-FR-4.5
    shape in that case.
    """
    if not theses:
        return None
    return _bounded_slot(
        render_structured_grounding(theses, votes), STRUCTURED_RECORDS_MAX_LENGTH
    )


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
    *,
    theses: Mapping[str, StructuredThesis] | None = None,
) -> InvestmentMemo:
    """Generate an investment memo from debate results via LLM synthesis.

    Args:
        debate_result: Completed debate with transcript and scorecard.
        data_package: Financial data package used in the debate.
        theses: The moderator's recorded opening theses (persona -> thesis). When
            given, the structured records (theses + the scorecard's final
            verdicts) are supplied to the aggregator as the primary grounding
            (FR-4.5). ``None`` keeps the legacy transcript-only prompt.

    Returns:
        InvestmentMemo with 5 structured sections. On a window failure the last
        good accumulated draft is retained and synthesis continues with the
        remaining windows; only a memo whose *every* window failed falls back.
    """
    transcript_windows = _transcript_windows(debate_result.transcript)
    scorecard_md = _bounded_slot(
        debate_result.scorecard.to_markdown(), SCORECARD_CONTEXT_MAX_LENGTH
    )
    context_str = _bounded_slot(
        data_package.to_context_string(), FINANCIAL_CONTEXT_MAX_LENGTH
    )
    grounding = _grounding_block(theses, debate_result.scorecard.votes)
    grounding_slot = (
        f"## Structured Records (PRIMARY GROUNDING)\n{grounding}\n\n"
        if grounding is not None
        else ""
    )

    # A failed window (a raised call or an unparseable response) must not discard
    # the accumulated draft: it is retained and synthesis continues with the next
    # window. ``last_error`` is only surfaced if no window ever succeeded.
    data = None
    last_error: Exception | None = None
    for index, transcript_window in enumerate(transcript_windows, start=1):
        user_prompt = (
            f"{grounding_slot}"
            f"## Transcript Window {index} of {len(transcript_windows)}\n"
            f"{transcript_window}\n\n"
            f"## Running Memo Draft From Earlier Windows\n"
            f"{_running_draft_json(data)}\n\n"
            f"## Scorecard\n{scorecard_md}\n\n"
            f"## Financial Data\n{context_str}"
        )
        messages = [
            {"role": "system", "content": MEMO_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        try:
            response = client().send_message(messages, temperature=0.7)
            parsed = extract_json(response["content"])
            if not isinstance(parsed, dict) or not parsed:
                raise ValueError(
                    f"Invalid JSON response for transcript window {index}"
                )
        except Exception as e:  # noqa: BLE001 -- degrade, do not discard the draft
            last_error = e
            logger.warning(
                "Memo window %d/%d failed; retaining last good draft: %s",
                index,
                len(transcript_windows),
                e,
            )
            continue
        data = parsed

    if data is None:
        logger.warning("Memo generation produced no usable draft: %s", last_error)
        return _make_fallback_memo(
            debate_result.ticker, debate_result.company_name, str(last_error)
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
    *,
    theses: Mapping[str, StructuredThesis] | None = None,
) -> DisagreementAnalysis:
    """Extract top disagreements from a debate transcript via LLM analysis.

    Args:
        debate_result: Completed debate with transcript.
        theses: The moderator's recorded opening theses (persona -> thesis). When
            given, they ground the analysis (FR-4.5) and a DCR ``collapse_summary``
            (per-persona stance trajectory + caved flags) is computed from the
            theses and the scorecard's final votes and attached to the output.

    Returns:
        DisagreementAnalysis with up to 3 disagreements plus, when ``theses`` are
        supplied, a DCR collapse summary. On a window failure the last good draft
        is retained; only a run whose every window failed yields empty
        disagreements (the collapse summary, being LLM-independent, is retained
        regardless).
    """
    transcript_windows = _transcript_windows(debate_result.transcript)
    scorecard_md = _bounded_slot(
        debate_result.scorecard.to_markdown(), SCORECARD_CONTEXT_MAX_LENGTH
    )
    grounding = _grounding_block(theses, debate_result.scorecard.votes)
    grounding_slot = (
        f"## Structured Records (PRIMARY GROUNDING)\n{grounding}\n\n"
        if grounding is not None
        else ""
    )
    # The collapse summary is derived from the structured records alone, so it is
    # available even if every LLM window fails.
    collapse_summary = (
        analyze_collapse(theses, debate_result.scorecard.votes)
        if theses
        else None
    )

    data = None
    last_error: Exception | None = None
    for index, transcript_window in enumerate(transcript_windows, start=1):
        user_prompt = (
            f"{grounding_slot}"
            f"## Transcript Window {index} of {len(transcript_windows)}\n"
            f"{transcript_window}\n\n"
            f"## Running Disagreement Analysis From Earlier Windows\n"
            f"{_running_draft_json(data)}\n\n"
            f"## Scorecard\n{scorecard_md}"
        )
        messages = [
            {"role": "system", "content": DISAGREEMENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        try:
            response = client().send_message(messages, temperature=0.7)
            parsed = extract_json(response["content"])
            if not isinstance(parsed, dict) or "disagreements" not in parsed:
                raise ValueError(
                    f"Invalid JSON response for transcript window {index}"
                )
        except Exception as e:  # noqa: BLE001 -- degrade, do not discard the draft
            last_error = e
            logger.warning(
                "Disagreement window %d/%d failed; retaining last good draft: %s",
                index,
                len(transcript_windows),
                e,
            )
            continue
        data = parsed

    if data is None:
        logger.warning(
            "Disagreement extraction produced no usable draft: %s", last_error
        )
        return DisagreementAnalysis(
            ticker=debate_result.ticker,
            company_name=debate_result.company_name,
            collapse_summary=collapse_summary,
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
        collapse_summary=collapse_summary,
    )
