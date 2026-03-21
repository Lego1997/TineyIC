"""edgartools SEC filings fetcher."""

import logging
from typing import Optional

from .models import FilingSummary

logger = logging.getLogger(__name__)

# edgartools requires identity for SEC EDGAR access
_identity_set = False


def _ensure_identity():
    """Set EDGAR identity once per process."""
    global _identity_set
    if not _identity_set:
        from edgar import set_identity
        set_identity("openIC research@example.com")
        _identity_set = True


def fetch_filings(ticker: str, form_type: str = "10-K") -> Optional[FilingSummary]:
    """Fetch latest SEC filing summary via edgartools.

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

        # Try structured access via filing object
        text_parts = []
        try:
            filing_obj = latest.obj()
            if filing_obj is not None:
                # For 10-K: extract key sections (Business, Risk Factors, MD&A)
                if form_type == "10-K":
                    for item_key in ["Item 1", "Item 1A", "Item 7"]:
                        try:
                            section = filing_obj[item_key]
                            if section:
                                section_text = str(section)[:1000]
                                text_parts.append(f"## {item_key}\n{section_text}")
                        except (KeyError, TypeError, IndexError):
                            pass
                # For 10-Q: extract key sections
                else:
                    for item_key in ["Part I", "Item 1", "Item 2"]:
                        try:
                            section = filing_obj[item_key]
                            if section:
                                section_text = str(section)[:700]
                                text_parts.append(f"## {item_key}\n{section_text}")
                        except (KeyError, TypeError, IndexError):
                            pass
        except Exception as e:
            logger.debug("Structured filing access failed for %s %s: %s", ticker, form_type, e)

        # Fallback: get plain text and truncate
        if not text_parts:
            try:
                text = latest.text()
                if text:
                    text_parts = [text[:max_chars]]
            except Exception as e:
                logger.warning("Could not get text for %s %s: %s", ticker, form_type, e)

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
