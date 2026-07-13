"""Deterministic parsing of the mandated opening/verdict trailing blocks (FR-4.4).

Opening theses and final verdicts are emitted as *structured records* alongside
their free-form prose; free-form NL is reserved for the cross-exam and rebuttal
phases (the anti-"telephone effect" split from TradingAgents, docs/research §5).

**Elicitation choice (recorded per the M4 Stage-2 brief).** For both the opening
thesis and the final verdict we mandate a fenced trailing block in the phase
prompt and parse it *deterministically* here — rather than issuing a second
structured-elicitation LLM call per persona. This is the strictly more testable
of the two options the brief offered: the parser is a pure function over the
persona's already-emitted TALK prose, so robustness (well-formed, malformed,
partial, absent) is exercised entirely offline with no LLM or binding mocks, and
it adds zero extra model calls to a debate.

The two blocks the prompts mandate (see :mod:`tinyic.debate.prompts`)::

    ===THESIS===                     ===VERDICT===
    STANCE: bullish                  VOTE: BUY
    CONFIDENCE: high                 CONFIDENCE: HIGH
    CLAIMS:                          REASONS:
    - ...                            - ...
    ===END===                        RISKS:
                                     - ...
                                     CHANGED_MIND: no
                                     ===END===

Parsing is forgiving of case, surrounding whitespace, ``:`` vs ``=`` separators,
bullet glyphs (``-``/``*``/``•``/``1.``), and a missing ``===END===`` terminator
(the block is a *trailing* block, so it runs to end-of-text). A record is only
returned when its defining field parses — a stance for a thesis, a vote for a
verdict; anything else yields ``None`` so the caller can fall back (an absent
verdict defers to LLM vote extraction; an absent thesis simply records nothing).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "StructuredThesis",
    "StructuredVerdict",
    "parse_thesis",
    "parse_verdict",
]


@dataclass(frozen=True)
class StructuredThesis:
    """A parsed opening thesis: the persona's stance, claims, and confidence."""

    stance: str  # bullish | bearish | neutral
    claims: list[str] = field(default_factory=list)
    confidence: str = "medium"  # free-form per schema; normalized to high/medium/low


@dataclass(frozen=True)
class StructuredVerdict:
    """A parsed final verdict: the persona's vote, confidence, reasons, risks."""

    vote: str  # BUY | HOLD | SELL
    confidence: str = "MEDIUM"  # HIGH | MEDIUM | LOW
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    changed_mind: bool = False


# A block is delimited by ``===LABEL===`` (2+ '-'/'=' either side, any case) and
# runs to an optional ``===END===`` terminator or to end-of-text.
_BLOCK_END = re.compile(r"[-=]{2,}\s*END\s*[-=]{2,}", re.IGNORECASE)
_BULLET = re.compile(r"^[ \t>]*(?:[-*•·]|\d+[.)])[ \t]+(.*\S)[ \t]*$")


def _extract_block(text: str, label: str) -> str | None:
    """Return the body of the last ``===LABEL===`` block in ``text``, else None.

    The *last* start marker wins so an incidental mention of the marker earlier
    in the prose cannot shadow the real trailing block.
    """
    start = re.compile(rf"[-=]{{2,}}\s*{label}\s*[-=]{{2,}}", re.IGNORECASE)
    matches = list(start.finditer(text))
    if not matches:
        return None
    body = text[matches[-1].end() :]
    end = _BLOCK_END.search(body)
    if end is not None:
        body = body[: end.start()]
    return body


def _find_scalar(block: str, key: str) -> str | None:
    """Return the value of a ``KEY: value`` line within ``block`` (case-insensitive)."""
    match = re.search(
        rf"(?im)^[ \t>*_\-]*{key}[ \t]*[:=][ \t]*(.+?)[ \t]*$", block
    )
    return match.group(1).strip() if match else None


def _find_bullets(block: str, key: str) -> list[str]:
    """Return the bullet items listed under a ``KEY:`` header line within ``block``.

    Collects consecutive bullet lines (``-``/``*``/``•``/``1.``) following the
    header, stopping at the first non-bullet line (typically the next field or
    the ``===END===`` terminator). If the header carries an inline value instead
    of bullets, that value is split on ``;`` as a fallback.
    """
    header = re.search(
        rf"(?im)^[ \t>*_\-]*{key}[ \t]*[:=][ \t]*(.*)$", block
    )
    if header is None:
        return []
    items: list[str] = []
    for raw_line in block[header.end() :].splitlines():
        if not raw_line.strip():
            continue
        bullet = _BULLET.match(raw_line)
        if bullet is None:
            break
        item = bullet.group(1).strip()
        if item:
            items.append(item)
    if not items:
        inline = header.group(1).strip()
        if inline:
            items = [part.strip() for part in inline.split(";") if part.strip()]
    return items


def _match_stance(value: str | None) -> str | None:
    """Resolve a stance token to bullish/bearish/neutral, else None."""
    if not value:
        return None
    low = value.lower()
    for stance in ("bullish", "bearish", "neutral"):
        if re.search(rf"\b{stance}\b", low):
            return stance
    if re.search(r"\bbull\b", low):
        return "bullish"
    if re.search(r"\bbear\b", low):
        return "bearish"
    return None


def _match_vote(value: str | None) -> str | None:
    """Resolve a vote token to BUY/HOLD/SELL (e.g. 'STRONG BUY' -> BUY), else None."""
    if not value:
        return None
    match = re.search(r"\b(BUY|HOLD|SELL)\b", value, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _normalize_confidence(value: str | None, *, upper: bool) -> str:
    """Resolve a confidence token to high/medium/low (default medium)."""
    default = "MEDIUM" if upper else "medium"
    if not value:
        return default
    low = value.lower()
    for level in ("high", "medium", "low"):
        if re.search(rf"\b{level}\b", low):
            return level.upper() if upper else level
    return default


def _parse_changed_mind(value: str | None) -> bool:
    """Interpret a CHANGED_MIND token; only affirmative leads are True."""
    if not value:
        return False
    tokens = value.strip().lower().split()
    return bool(tokens) and tokens[0] in {"yes", "true", "y", "1", "changed"}


def parse_thesis(text: str | None) -> StructuredThesis | None:
    """Parse an opening ``===THESIS===`` block from ``text``.

    Returns ``None`` when the block is absent or carries no recognizable stance,
    so the caller records nothing rather than a misleading placeholder.
    """
    if not text:
        return None
    block = _extract_block(text, "THESIS")
    if block is None:
        return None
    stance = _match_stance(_find_scalar(block, "STANCE"))
    if stance is None:
        return None
    return StructuredThesis(
        stance=stance,
        claims=_find_bullets(block, "CLAIMS"),
        confidence=_normalize_confidence(
            _find_scalar(block, "CONFIDENCE"), upper=False
        ),
    )


def parse_verdict(text: str | None) -> StructuredVerdict | None:
    """Parse a final ``===VERDICT===`` block from ``text``.

    Returns ``None`` when the block is absent or carries no recognizable vote, so
    the caller falls back to LLM vote extraction (``vote_recorded.source`` then
    reports ``extracted`` rather than ``structured``).
    """
    if not text:
        return None
    block = _extract_block(text, "VERDICT")
    if block is None:
        return None
    vote = _match_vote(_find_scalar(block, "VOTE"))
    if vote is None:
        return None
    return StructuredVerdict(
        vote=vote,
        confidence=_normalize_confidence(
            _find_scalar(block, "CONFIDENCE"), upper=True
        ),
        reasons=_find_bullets(block, "REASONS"),
        risks=_find_bullets(block, "RISKS"),
        changed_mind=_parse_changed_mind(_find_scalar(block, "CHANGED_MIND")),
    )
