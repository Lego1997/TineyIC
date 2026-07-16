"""Disagreement-collapse analytics over the structured records (FR-4.5).

The sycophancy evidence (docs/research 5) names *disagreement collapse* — a
persona abandoning its own contrarian stance to align with the majority — as the
headline failure mode of multi-agent debate, and recommends surfacing a
Disagreement-Collapse-Rate (DCR) style metric plus a "caved under pressure"
flag whenever a persona drops an earlier contrarian position.

This module derives those signals **purely and offline** from the structured
records M4 Stage 2 already records: each persona's opening thesis (stance +
claims) and its final verdict vote (choice + reasoning). No LLM call is made
here — the memo/disagreement *writer* is the aggregator binding (see
:mod:`tinyic.debate.memo`); the collapse analytics are deterministic bookkeeping
the orchestrator emits as ``collapse_metric`` events and the disagreement writer
folds into its output as a DCR summary.

**The "caved" heuristic (smallest honest interpretation, documented).** A
persona is flagged as having *caved* when **all** of:

1. its opening stance was *contrarian* — it differed from the eventual majority
   (plurality) final stance of the committee;
2. it *moved to that majority* at the verdict (its final stance changed from the
   opening stance and now equals the majority); and
3. it cited *no new evidence* for the shift — its final-vote reasoning introduces
   no substantive term absent from its own opening claims.

Every clause is conservative on purpose. With no clear plurality there is no
"majority pressure", so nothing is flagged. With no opening claims there is no
baseline to prove the reasoning is unchanged, so the shift is treated as
evidence-backed (never flag a collapse we cannot ground). Clause 3 is a keyword
novelty proxy, not a semantic judgment — it is deliberately the weakest honest
signal that distinguishes "aligned for a fresh reason" from "aligned for no
stated reason", and is documented as such so readers weigh the flag accordingly.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from .models import CollapseSummary, StanceShift, Vote, VoteChoice
from .structured import StructuredThesis

__all__ = [
    "analyze_collapse",
    "render_structured_grounding",
    "stance_for_vote",
]

# A final BUY/HOLD/SELL vote maps onto the same bullish/neutral/bearish stance
# vocabulary the opening thesis uses, so opening and verdict are comparable.
_VOTE_STANCE: dict[str, str] = {
    "BUY": "bullish",
    "HOLD": "neutral",
    "SELL": "bearish",
}

# Generic function words dropped before the keyword-novelty comparison, so
# "the/and/with" boilerplate cannot look like a fresh reason. Domain nouns are
# intentionally *kept* — a genuinely new business argument should register.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "have", "has",
        "will", "would", "could", "should", "into", "over", "under", "than",
        "then", "there", "their", "them", "they", "what", "when", "which",
        "while", "about", "above", "after", "again", "against", "because",
        "been", "being", "more", "most", "some", "such", "only", "very",
        "just", "also", "here", "does", "done", "were", "your", "ours",
    }
)


def stance_for_vote(vote: VoteChoice | str) -> str:
    """Map a final vote (``BUY``/``HOLD``/``SELL``) to a thesis-style stance."""
    key = vote.value if isinstance(vote, VoteChoice) else str(vote).strip().upper()
    return _VOTE_STANCE.get(key, "neutral")


def _content_tokens(texts: Iterable[str]) -> set[str]:
    """Substantive lowercase word tokens (len > 3, minus generic stopwords)."""
    tokens: set[str] = set()
    for text in texts:
        for word in re.findall(r"[a-z0-9]+", str(text).lower()):
            if len(word) > 3 and word not in _STOPWORDS:
                tokens.add(word)
    return tokens


def _cited_new_evidence(
    thesis_claims: Sequence[str], verdict_reasons: Sequence[str]
) -> bool:
    """Whether the verdict reasoning introduces a term absent from the claims.

    Keyword-novelty proxy (documented in the module docstring). With no opening
    claims there is no baseline, so the shift is treated as evidence-backed to
    avoid flagging a collapse we cannot ground.
    """
    prior = _content_tokens(thesis_claims)
    if not prior:
        return True
    return bool(_content_tokens(verdict_reasons) - prior)


def _plurality_stance(stances: Sequence[str]) -> str | None:
    """The single most common stance, or ``None`` when there is no clear winner."""
    counts = Counter(stances)
    if not counts:
        return None
    top_stance, top_count = counts.most_common(1)[0]
    if sum(1 for count in counts.values() if count == top_count) > 1:
        return None  # a tie is not "majority pressure"
    return top_stance


def _collapse_note(
    *,
    stance_before: str,
    stance_after: str,
    changed: bool,
    contrarian: bool,
    moved_to_majority: bool,
    cited_new_evidence: bool,
    caved: bool,
    majority: str | None,
) -> str:
    """One honest sentence describing the persona's opening→verdict trajectory."""
    if caved:
        return (
            f"Abandoned an opening {stance_before} stance to join the "
            f"{stance_after} majority without citing new evidence — a possible "
            "sycophantic collapse."
        )
    if not changed:
        if contrarian:
            return (
                f"Held a contrarian {stance_before} stance through to the "
                "verdict — did not converge under pressure."
            )
        return f"Held a {stance_before} stance consistent with the committee."
    if moved_to_majority:
        return (
            f"Shifted from {stance_before} to the {stance_after} majority, but "
            "cited new evidence — a reasoned update rather than a collapse."
        )
    return (
        f"Shifted from {stance_before} to {stance_after}, an independent move "
        f"away from the {majority or 'prevailing'} view."
    )


def analyze_collapse(
    theses: Mapping[str, StructuredThesis],
    votes: Sequence[Vote],
) -> CollapseSummary:
    """Compute per-persona stance trajectories and the DCR summary (FR-4.5).

    ``theses`` are the moderator's recorded opening theses (persona → thesis);
    ``votes`` are the committee's final votes. A trajectory is produced for every
    persona that has *both* an opening thesis and a final vote — opening stance
    (thesis) → final stance (vote) — with the "caved" flag applied per the
    heuristic documented in the module docstring. Personas lacking an opening
    thesis have no measurable trajectory and are skipped (no honest baseline).
    """
    vote_by_persona = {vote.investor: vote for vote in votes}
    majority = _plurality_stance([stance_for_vote(vote.vote) for vote in votes])

    shifts: list[StanceShift] = []
    caved_personas: list[str] = []
    for persona, thesis in theses.items():
        vote = vote_by_persona.get(persona)
        if vote is None:
            continue
        stance_before = thesis.stance
        stance_after = stance_for_vote(vote.vote)
        changed = stance_after != stance_before
        contrarian = majority is not None and stance_before != majority
        moved_to_majority = (
            majority is not None and changed and stance_after == majority
        )
        cited_new_evidence = _cited_new_evidence(thesis.claims, vote.reasoning)
        caved = contrarian and moved_to_majority and not cited_new_evidence
        if caved:
            caved_personas.append(persona)
        shifts.append(
            StanceShift(
                persona=persona,
                stance_before=stance_before,
                stance_after=stance_after,
                caved=caved,
                note=_collapse_note(
                    stance_before=stance_before,
                    stance_after=stance_after,
                    changed=changed,
                    contrarian=contrarian,
                    moved_to_majority=moved_to_majority,
                    cited_new_evidence=cited_new_evidence,
                    caved=caved,
                    majority=majority,
                ),
            )
        )

    assessed = len(shifts)
    dcr = round(len(caved_personas) / assessed, 4) if assessed else 0.0
    return CollapseSummary(
        disagreement_collapse_rate=dcr,
        assessed_count=assessed,
        caved_count=len(caved_personas),
        caved_personas=caved_personas,
        majority_stance=majority,
        shifts=shifts,
    )


def _thesis_line(persona: str, thesis: StructuredThesis) -> str:
    claims = "; ".join(thesis.claims) if thesis.claims else "(no claims stated)"
    return (
        f"- {persona}: {thesis.stance} (confidence {thesis.confidence}); "
        f"claims: {claims}"
    )


def _verdict_line(vote: Vote) -> str:
    reasons = "; ".join(vote.reasoning) if vote.reasoning else "(no reasons stated)"
    risks = "; ".join(vote.key_risks) if vote.key_risks else "(none stated)"
    return (
        f"- {vote.investor}: {vote.vote.value} (confidence {vote.confidence.value}); "
        f"reasons: {reasons}; risks: {risks}; "
        f"changed_mind: {'yes' if vote.changed_mind else 'no'}"
    )


def render_structured_grounding(
    theses: Mapping[str, StructuredThesis],
    votes: Sequence[Vote],
) -> str:
    """Render the structured records as the memo/disagreement primary grounding.

    Presents each persona's opening thesis (stance + claims) and final verdict
    (vote + reasoning + risks) as compact, attributable lines — the authoritative
    per-persona record the aggregator grounds on before the windowed prose.
    """
    lines: list[str] = ["Opening theses (stance -> claims):"]
    if theses:
        for persona, thesis in theses.items():
            lines.append(_thesis_line(persona, thesis))
    else:
        lines.append("- (no structured opening theses recorded)")
    lines.append("")
    lines.append("Final verdicts (vote -> reasoning):")
    if votes:
        for vote in votes:
            lines.append(_verdict_line(vote))
    else:
        lines.append("- (no votes recorded)")
    return "\n".join(lines)
