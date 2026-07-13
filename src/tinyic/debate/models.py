"""Pydantic data models for the debate engine."""

import re
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class DebatePhase(str, Enum):
    """Phases of a structured investment debate."""

    SETUP = "setup"
    OPENING = "opening_statements"
    CROSS_EXAM = "cross_examination"
    REBUTTAL = "rebuttal"
    VERDICT = "final_verdict"
    COMPLETE = "complete"


class VoteChoice(str, Enum):
    """Final investment recommendation."""

    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


class Confidence(str, Enum):
    """Confidence level for a vote."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @classmethod
    def _missing_(cls, value: object):
        """Resolve confidence strings without making callers match enum case."""
        if isinstance(value, str):
            normalized = value.strip().upper()
            return cls.__members__.get(normalized)
        return None


_VOTE_PREFIX_PATTERN = re.compile(
    r"^\s*"
    r"(?:(?:FINAL\s+)?(?:VOTE|VERDICT|RECOMMENDATION)\s*[:=\-]\s*)?"
    r"(?:(?:STRONG|CONDITIONAL)\s+)?"
    r"(?P<vote>BUY|HOLD|SELL)\b",
    re.IGNORECASE,
)


class Vote(BaseModel):
    """An individual investor's final vote after debate."""

    investor: str
    vote: VoteChoice
    confidence: Confidence
    reasoning: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    changed_mind: bool = False
    #: Where the vote came from (FR-4.4): ``structured`` when parsed from the
    #: persona's mandated verdict block, ``extracted`` when derived by the
    #: LLM extraction fallback. Surfaced verbatim in ``vote_recorded.source``.
    source: Literal["structured", "extracted"] = "extracted"

    @field_validator("vote", mode="before")
    @classmethod
    def fuzzy_match_vote(cls, v: object) -> object:
        """Parse an explicit leading verdict without scanning explanatory prose."""
        if v is None:
            return VoteChoice.HOLD
        if isinstance(v, str):
            match = _VOTE_PREFIX_PATTERN.match(v)
            if match:
                return VoteChoice(match.group("vote").upper())
            return VoteChoice.HOLD
        return v


class Scorecard(BaseModel):
    """Aggregated voting results from the debate."""

    ticker: str
    company_name: str
    votes: list[Vote]
    consensus: Optional[VoteChoice] = None
    bull_count: int = 0
    bear_count: int = 0
    hold_count: int = 0

    def to_markdown(self) -> str:
        """Render the scorecard as a markdown table."""
        lines: list[str] = []

        # Header
        consensus_label = self.consensus.value if self.consensus else "No consensus"
        lines.append(f"## {self.company_name} ({self.ticker}) -- Investment Scorecard")
        lines.append("")
        lines.append(
            f"**Consensus:** {consensus_label} "
            f"| Bulls: {self.bull_count} | Bears: {self.bear_count} | Hold: {self.hold_count}"
        )
        lines.append("")

        # Table
        lines.append("| Investor | Vote | Confidence | Key Reasoning |")
        lines.append("|----------|------|------------|---------------|")
        for vote in self.votes:
            reasoning_str = "; ".join(vote.reasoning[:3]) if vote.reasoning else "--"
            lines.append(
                f"| {vote.investor} | {vote.vote.value} | {vote.confidence.value} | {reasoning_str} |"
            )

        return "\n".join(lines)


class MemoSection(BaseModel):
    """A single section of the investment memo with grounding metadata."""
    title: str
    content: str
    contributing_personas: list[str] = Field(default_factory=list)
    supporting_data: list[str] = Field(default_factory=list)


class InvestmentMemo(BaseModel):
    """Full narrative investment memo synthesized from debate."""
    ticker: str
    company_name: str
    executive_summary: MemoSection
    investment_thesis: MemoSection
    key_risks: MemoSection
    valuation_discussion: MemoSection
    final_verdict: MemoSection
    generated_at: datetime = Field(default_factory=datetime.now)

    def to_markdown(self) -> str:
        """Render the memo as a Markdown document."""
        sections = [
            self.executive_summary,
            self.investment_thesis,
            self.key_risks,
            self.valuation_discussion,
            self.final_verdict,
        ]
        lines = [f"# Investment Memo: {self.company_name} ({self.ticker})", ""]
        for section in sections:
            lines.append(f"## {section.title}")
            lines.append("")
            lines.append(section.content)
            lines.append("")
            if section.contributing_personas:
                lines.append(
                    f"*Contributors: {', '.join(section.contributing_personas)}*"
                )
            if section.supporting_data:
                lines.append(
                    f"*Data references: {', '.join(section.supporting_data)}*"
                )
            lines.append("")
        return "\n".join(lines)


class Disagreement(BaseModel):
    """A single dimension of disagreement between personas."""
    dimension: str
    description: str
    sides: list[dict] = Field(default_factory=list)
    # Each dict: {persona: str, position: str, evidence_quote: str}
    resolution: str = ""


class StanceShift(BaseModel):
    """One persona's opening->verdict stance trajectory (FR-4.5).

    ``caved`` flags a disagreement collapse: a contrarian opening stance dropped
    to join the majority at the verdict without citing new evidence. Surfaced
    verbatim in the ``collapse_metric`` event and the DCR summary.
    """

    persona: str
    stance_before: str
    stance_after: str
    caved: bool = False
    note: str = ""


class CollapseSummary(BaseModel):
    """DCR-style summary of disagreement collapse across the committee (FR-4.5).

    The Disagreement-Collapse-Rate is the fraction of *assessed* personas (those
    with both an opening thesis and a final vote) flagged as having caved.
    """

    disagreement_collapse_rate: float = 0.0
    assessed_count: int = 0
    caved_count: int = 0
    caved_personas: list[str] = Field(default_factory=list)
    majority_stance: Optional[str] = None
    shifts: list[StanceShift] = Field(default_factory=list)

    def to_markdown(self) -> str:
        """Render the collapse summary as a Markdown block."""
        pct = round(self.disagreement_collapse_rate * 100, 1)
        majority = self.majority_stance or "no clear majority"
        lines = [
            "## Disagreement Collapse (DCR)",
            "",
            f"**Collapse rate:** {pct}% "
            f"({self.caved_count} of {self.assessed_count} assessed personas caved) "
            f"| Majority stance: {majority}",
            "",
        ]
        if self.caved_personas:
            lines.append(f"**Caved under pressure:** {', '.join(self.caved_personas)}")
            lines.append("")
        for shift in self.shifts:
            flag = "CAVED" if shift.caved else "held"
            lines.append(
                f"- {shift.persona}: {shift.stance_before} -> "
                f"{shift.stance_after} [{flag}] — {shift.note}"
            )
        return "\n".join(lines)


class DisagreementAnalysis(BaseModel):
    """Top disagreements extracted from the debate."""
    ticker: str
    company_name: str
    disagreements: list[Disagreement] = Field(default_factory=list)
    #: Optional DCR summary (FR-4.5) attached when the writer is given the
    #: structured records; ``None`` for the legacy transcript-only path.
    collapse_summary: Optional["CollapseSummary"] = None
    generated_at: datetime = Field(default_factory=datetime.now)

    def to_markdown(self) -> str:
        """Render disagreement analysis as Markdown."""
        lines = [
            f"# Disagreement Analysis: {self.company_name} ({self.ticker})",
            "",
        ]
        for i, d in enumerate(self.disagreements, 1):
            lines.append(f"## {i}. {d.dimension}")
            lines.append("")
            lines.append(d.description)
            lines.append("")
            for side in d.sides:
                lines.append(f"**{side.get('persona', 'Unknown')}:** {side.get('position', '')}")
                quote = side.get("evidence_quote", "")
                if quote:
                    lines.append(f'> "{quote}"')
                lines.append("")
            if d.resolution:
                lines.append(f"**Resolution:** {d.resolution}")
                lines.append("")
        if self.collapse_summary is not None:
            lines.append(self.collapse_summary.to_markdown())
            lines.append("")
        return "\n".join(lines)


class DebateResult(BaseModel):
    """Complete result from a debate run."""

    ticker: str
    company_name: str
    scorecard: Scorecard
    phases_completed: list[str]
    transcript: Optional[str] = None
    cost_stats: Optional[dict] = Field(
        default=None,
        description="Token usage and cost statistics from the debate",
    )
    memo: Optional["InvestmentMemo"] = None
    disagreement_analysis: Optional["DisagreementAnalysis"] = None
    created_at: datetime = Field(default_factory=datetime.now)
