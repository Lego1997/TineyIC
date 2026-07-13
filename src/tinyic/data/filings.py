"""edgartools SEC filings fetcher.

Uses edgar.documents.HTMLParser for structured section extraction,
avoiding deprecated edgar.files.html / edgar.files.htmltools imports.
"""

import logging
import re
from typing import Optional

from .models import FilingSummary

logger = logging.getLogger(__name__)

# edgartools requires identity for SEC EDGAR access
_identity_set = False

# Section headings to extract per filing type
_10K_SECTIONS = ["Item 1", "Item 1A", "Item 7"]
_10Q_SECTIONS = ["Item 1", "Item 2"]

_GENERIC_FILING_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:part\s+[ivx]+\b|item\s+\d+[a-z]?\b)[^\n]*$",
    re.IGNORECASE | re.MULTILINE,
)
_TOC_LEADER = re.compile(r"\.{4,}|\s\.{2,}\s*\d+\s*$")


def _ensure_identity():
    """Set EDGAR identity once per process."""
    global _identity_set
    if not _identity_set:
        from edgar import set_identity
        set_identity("TineyIC research@example.com")
        _identity_set = True


def _extract_sections(markdown: str, section_keys: list[str], max_per_section: int) -> list[str]:
    """Extract named sections from filing markdown text.

    Looks for headings matching section_keys (case-insensitive) and extracts
    the content under each heading up to the next heading or max_per_section chars.

    Args:
        markdown: Full filing text in markdown format.
        section_keys: List of section heading prefixes to match (e.g. "Item 1A").
        max_per_section: Max characters to extract per section.

    Returns:
        List of formatted section strings like "## Item 1\\n<content>".
    """
    parts = []
    all_headings = list(_GENERIC_FILING_HEADING.finditer(markdown))

    # EDGAR markdown often contains each Item twice: once in the table of
    # contents and once as the real section heading.  Evaluate every candidate
    # and select a substantive body instead of accepting the first match.
    for key in section_keys:
        pattern = re.compile(
            rf"^(?:#{{1,6}}\s*)?{re.escape(key)}(?=[\s.:—–-])[^\n]*$",
            re.IGNORECASE | re.MULTILINE,
        )
        candidates: list[tuple[int, str]] = []
        for match in pattern.finditer(markdown):
            start = match.end()
            end = next(
                (
                    heading.start()
                    for heading in all_headings
                    if heading.start() > match.start()
                ),
                len(markdown),
            )
            section_text = markdown[start:end].strip()
            heading_text = match.group(0)
            toc_like = bool(
                _TOC_LEADER.search(heading_text)
                or _TOC_LEADER.search(section_text[:200])
            )

            # Real filing sections are prose, while TOC spans are normally one
            # short line ending in a page number.  Prefer substantive prose;
            # retain a short-body fallback for unusually terse filings.
            score = len(section_text) - (10_000 if toc_like else 0)
            if section_text:
                candidates.append((score, section_text))

        if candidates:
            _, section_text = max(candidates, key=lambda candidate: candidate[0])
            parts.append(f"## {key}\n{section_text[:max_per_section]}")
    return parts


def _part_one(markdown: str) -> str:
    """Return the substantive Part I span of a 10-Q when it is identifiable."""
    part_one_pattern = re.compile(
        r"^(?:#{1,6}\s*)?Part\s+I(?=[\s.:—–-])[^\n]*$",
        re.IGNORECASE | re.MULTILINE,
    )
    part_two_pattern = re.compile(
        r"^(?:#{1,6}\s*)?Part\s+II(?=[\s.:—–-])[^\n]*$",
        re.IGNORECASE | re.MULTILINE,
    )
    part_ones = list(part_one_pattern.finditer(markdown))
    part_twos = list(part_two_pattern.finditer(markdown))

    candidates: list[tuple[int, str]] = []
    for start_match in part_ones:
        end_match = next(
            (
                match
                for match in part_twos
                if match.start() > start_match.end()
            ),
            None,
        )
        if end_match is None:
            continue
        span = markdown[start_match.end() : end_match.start()]
        toc_like = _TOC_LEADER.search(start_match.group(0)) is not None
        score = len(span) - (10_000 if toc_like else 0)
        candidates.append((score, span))

    if not candidates:
        return markdown
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _join_with_budget(parts: list[str], max_chars: int) -> str:
    """Join sections without letting a final blind slice erase the last one."""
    if not parts:
        return ""
    joined = "\n\n".join(parts)
    if len(joined) <= max_chars:
        return joined

    per_part = max(1, (max_chars - 2 * (len(parts) - 1)) // len(parts))
    return "\n\n".join(part[:per_part] for part in parts)[:max_chars]


def fetch_filings(ticker: str, form_type: str = "10-K") -> Optional[FilingSummary]:
    """Fetch latest SEC filing summary via edgartools.

    Uses HTMLParser for structured extraction (non-deprecated API), with
    markdown fallback for plain text.

    Args:
        ticker: Stock ticker symbol.
        form_type: Filing type, "10-K" or "10-Q".

    Returns:
        FilingSummary with truncated text, or None on failure.
        10-K text is truncated to ~3000 chars; 10-Q to ~2000 chars.
    """
    max_chars = 3000 if form_type == "10-K" else 2000

    try:
        from edgar import Company
        _ensure_identity()

        company = Company(ticker.upper().strip())
        filings = company.get_filings(form=form_type)
        if not filings:
            logger.warning("No %s filings found for %s", form_type, ticker)
            return None

        latest = filings.latest()
        if not latest:
            logger.warning("No latest %s filing for %s", form_type, ticker)
            return None

        # Determine section keys and per-section limit
        if form_type == "10-K":
            section_keys = _10K_SECTIONS
            max_per_section = 1000
        else:
            section_keys = _10Q_SECTIONS
            max_per_section = 700

        text_parts: list[str] = []

        # Primary: HTMLParser-based structured extraction
        try:
            html = latest.html()
            if html:
                from edgar.documents import HTMLParser
                parser = HTMLParser.create_for_ai()
                doc = parser.parse(html)
                if doc:
                    md = doc.to_markdown()
                    if md:
                        section_markdown = _part_one(md) if form_type == "10-Q" else md
                        text_parts = _extract_sections(
                            section_markdown, section_keys, max_per_section
                        )
        except Exception as e:
            logger.debug("HTMLParser extraction failed for %s %s: %s", ticker, form_type, e)

        # Fallback: get full markdown and extract sections or truncate
        if not text_parts:
            try:
                md = latest.markdown()
                if md:
                    section_markdown = _part_one(md) if form_type == "10-Q" else md
                    text_parts = _extract_sections(
                        section_markdown, section_keys, max_per_section
                    )
                    # If section extraction found nothing, use raw markdown
                    if not text_parts:
                        text_parts = [md[:max_chars]]
            except Exception as e:
                logger.warning("Could not get markdown for %s %s: %s", ticker, form_type, e)

        if not text_parts:
            logger.warning("No text extracted from %s %s filing", ticker, form_type)
            return None

        # Extract filing date
        filing_date = None
        try:
            filing_date = str(latest.filing_date) if hasattr(latest, "filing_date") else None
        except Exception:
            pass

        return FilingSummary(
            form_type=form_type,
            filing_date=filing_date,
            text_summary=_join_with_budget(text_parts, max_chars),
        )

    except Exception as e:
        logger.warning("Failed to fetch %s for %s: %s", form_type, ticker, e)
        return None
