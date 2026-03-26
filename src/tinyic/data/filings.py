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
_10Q_SECTIONS = ["Part I", "Item 1", "Item 2"]


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
    # Build pattern: match headings that start with any of the section keys
    # Handles markdown headings like "# Item 1" or "## Item 1A" or plain "Item 1A"
    for key in section_keys:
        # Match heading line containing the key (case-insensitive)
        pattern = re.compile(
            rf"^(?:#+\s*)?{re.escape(key)}[^a-zA-Z0-9].*$",
            re.IGNORECASE | re.MULTILINE,
        )
        match = pattern.search(markdown)
        if match:
            start = match.end()
            # Find next heading (any markdown heading)
            next_heading = re.search(r"^#+\s", markdown[start:], re.MULTILINE)
            end = start + next_heading.start() if next_heading else len(markdown)
            section_text = markdown[start:end].strip()[:max_per_section]
            if section_text:
                parts.append(f"## {key}\n{section_text}")
    return parts


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
                        text_parts = _extract_sections(md, section_keys, max_per_section)
        except Exception as e:
            logger.debug("HTMLParser extraction failed for %s %s: %s", ticker, form_type, e)

        # Fallback: get full markdown and extract sections or truncate
        if not text_parts:
            try:
                md = latest.markdown()
                if md:
                    text_parts = _extract_sections(md, section_keys, max_per_section)
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
            text_summary="\n\n".join(text_parts)[:max_chars],
        )

    except Exception as e:
        logger.warning("Failed to fetch %s for %s: %s", form_type, ticker, e)
        return None
