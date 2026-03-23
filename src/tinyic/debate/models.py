"""Pydantic data models for the debate engine."""

from datetime import datetime
from enum import Enum
from typing import Optional

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


class Vote(BaseModel):
    """An individual investor's final vote after debate."""

    investor: str
    vote: VoteChoice
    confidence: Confidence
    reasoning: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    changed_mind: bool = False

    @field_validator("vote", mode="before")
    @classmethod
    def fuzzy_match_vote(cls, v: object) -> object:
        """Fuzzy-match vote strings like 'STRONG BUY' -> BUY."""
        if isinstance(v, str):
            upper = v.upper()
            if "BUY" in upper:
                return VoteChoice.BUY
            if "SELL" in upper:
                return VoteChoice.SELL
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


class DisagreementAnalysis(BaseModel):
    """Top disagreements extracted from the debate."""
    ticker: str
    company_name: str
    disagreements: list[Disagreement] = Field(default_factory=list)
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
