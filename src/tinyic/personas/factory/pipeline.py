"""Offline-first, cited investor-persona research pipeline.

All nondeterministic provider work is behind ``ResearchBackend``.  This module
owns the safety properties that must not be delegated to a model: URL
deduplication, citation checks, quote verification, source-quality gates,
schema validation, collision handling, and guarded last-stage writes.
"""

from __future__ import annotations

import copy
import errno
import ipaddress
import json
import os
import re
import stat
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date
from html import unescape as html_unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .schema import SchemaValidationError, validate_agent_spec
from .seeds import canonical_name_key, plan_queries
from .templates import (
    DISCLAIMER,
    DOSSIER_SECTIONS,
    LOW_SOURCE_WARNING,
    render_dossier,
    render_dossier_prompt,
    render_persona_prompt,
)
from .url_safety import normalize_public_host, url_contains_sensitive_material
from .types import (
    AtomicClaim,
    CallUsage,
    DossierSynthesisRequest,
    Evidence,
    PersonaSynthesisRequest,
    ResearchBackend,
    ResearchRequest,
    ResearchResult,
    SearchRequest,
    UsageSummary,
    VerificationRequest,
)

PROTECTED_BUILTIN_SLUGS = frozenset(
    {
        "benjamin_graham",
        "warren_buffett",
        "charlie_munger",
        "peter_lynch",
        "howard_marks",
        "li_lu",
    }
)

_TRACKING_QUERY_KEYS = frozenset(
    {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
)
_CITATION_RE = re.compile(r"\[(\d+)\]")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")

_DOUBLE_QUOTE_OPENERS = frozenset(
    {
        '"',
        "«",
        "»",
        "“",
        "”",
        "„",
        "‟",
        "❝",
        "❞",
        "「",
        "」",
        "『",
        "』",
        "〝",
        "〞",
        "〟",
        "﹁",
        "﹂",
        "﹃",
        "﹄",
        "＂",
        "｢",
        "｣",
    }
)
_DOUBLE_QUOTE_CLOSERS = _DOUBLE_QUOTE_OPENERS
_SINGLE_QUOTE_OPENERS = frozenset(
    {"'", "ʻ", "ʼ", "ʹ", "‘", "’", "‚", "‛", "′", "‹", "›", "＇"}
)
_SINGLE_QUOTE_CLOSERS = _SINGLE_QUOTE_OPENERS
_DIRECTIONAL_QUOTE_PAIRS = frozenset(
    {
        ("«", "»"),
        ("»", "«"),
        ("“", "”"),
        ("„", "”"),
        ("‟", "”"),
        ("❝", "❞"),
        ("「", "」"),
        ("『", "』"),
        ("〝", "〞"),
        ("〝", "〟"),
        ("﹁", "﹂"),
        ("﹃", "﹄"),
        ("｢", "｣"),
        ("‘", "’"),
        ("‚", "’"),
        ("‛", "’"),
        ("‹", "›"),
        ("›", "‹"),
    }
)
_QUOTE_STRIP_CHARACTERS = "".join(
    sorted(_DOUBLE_QUOTE_OPENERS | _SINGLE_QUOTE_OPENERS)
)
_QUOTE_CHARACTERS = _DOUBLE_QUOTE_OPENERS | _SINGLE_QUOTE_OPENERS
_QUOTE_LIKE_UNICODE_NAMES = (
    "APOSTROPHE",
    "DASIA",
    "COMMA ABOVE",
    "COMMA BELOW",
    "GERESH",
    "GERSHAYIM",
    "HALF RING",
    "KORONIS",
    "PRIME",
    "PSILI",
    "QUOTATION MARK",
    "REVERSED COMMA",
    "SALTILLO",
    "TURNED COMMA",
)
_QUOTE_LIKE_MODIFIER_NAMES = (
    "ACUTE ACCENT",
    "GRAVE ACCENT",
)
_MARKDOWN_BLOCKQUOTE_RE = re.compile(
    r"^[ \t]*(?:(?:[-+*]|\d{1,9}[.)])[ \t]+)*(?:>[ \t]?)+(.*)$"
)
_MARKDOWN_FENCE_RE = re.compile(
    r"^[ \t]*(?:(?:(?:[-+*]|\d{1,9}[.)])[ \t]+)|(?:>[ \t]?))*"
    r"(?:`{3,}|~{3,})",
    flags=re.MULTILINE,
)
_RAW_HTML_OR_AUTOLINK_RE = re.compile(
    r"<(?:/?[A-Za-z][^<>]*|!--[\s\S]*?--|![A-Z][^<>]*|\?[^<>]*)>",
    flags=re.IGNORECASE,
)
_MARKDOWN_LINK_END_RE = re.compile(r"]\s*(?:\(|\[)")
_MARKDOWN_IMAGE_START_RE = re.compile(r"!\s*\[")
_MARKDOWN_REFERENCE_DEFINITION_RE = re.compile(
    r"^[ \t]{0,3}\[[^\]\r\n]+\]:", flags=re.MULTILINE
)

_PUBLIC_RECORD_SCOPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "age",
        re.compile(
            r"\bdate of birth\b|"
            r"\b(?:he|she|they)\s+(?:is|are|was|were)\s+(?:now\s+)?"
            r"\d{1,3}(?:\s|-)+years?(?:\s|-)+old\b|"
            r"\b\d{1,3}(?:\s|-)+years?(?:\s|-)+old\b|"
            r"\bborn\s+(?:in\s+\d{4}|on\s+[a-z]+\s+\d{1,2})\b"
        ),
    ),
    (
        "family",
        re.compile(
            r"\b(?:his|her|their)\s+(?:"
            r"family(?!\s+(?:office|business|company|firm|fund|investment|enterprise))|"
            r"wife|husband|spouse|son|daughter|children?|mother|father|parents?|"
            r"brother|sister)\b|"
            r"\bfamily\s+(?:life|background|history|members?)\b|"
            r"(?:^|[.!?]\s+)(?:married|divorced|widowed)\b"
        ),
    ),
    (
        "health",
        re.compile(
            r"\b(?:his|her|their)\s+(?:health|illness|disease|diagnosis|"
            r"medical\s+(?:history|condition|treatment))\b|"
            r"\b(?:he|she|they)\s+(?:(?:is|are|was|were|has been|have been)\s+)?"
            r"(?:diagnosed\s+with|hospitalized\s+for|treated\s+for|suffers?\s+from)\b|"
            r"^(?:diagnosed\s+with|hospitalized\s+for|treated\s+for)\b"
        ),
    ),
    (
        "residence",
        re.compile(
            r"\b(?:his|her|their)\s+(?:residence|home address|street address|"
            r"residential address|phone number|email address)\b|"
            r"\b(?:he|she|they)\s+(?:currently\s+)?(?:lives|resides)\s+(?:at|in)\b|"
            r"^(?:currently\s+)?(?:lives|resides)\s+(?:at|in)\b|"
            r"\b(?:home|street|residential|email)\s+address\b|"
            r"\bpersonal\s+(?:phone|email)\b"
        ),
    ),
    (
        "affiliation or endorsement",
        re.compile(
            r"\b(?:tinyic|(?:this|the)\s+(?:persona|profile|simulation|dossier))\b"
            r".{0,48}\b(?:endorsed|approved|authorized|affiliated|associated)\b|"
            r"\b(?:endorsed|approved|authorized|affiliated|associated)\b"
            r".{0,48}\b(?:tinyic|(?:this|the)\s+(?:persona|profile|simulation|dossier))\b|"
            r"\bofficial(?:ly\s+authorized)?\s+(?:persona|profile|simulation|dossier)\b"
        ),
    ),
)
_LEADING_APOSTROPHE_WORDS = frozenset(
    {"bout", "cause", "em", "n", "round", "til", "tis", "twas"}
)
_FIXED_INTERNAL_APOSTROPHE_STEMS = {
    "all": frozenset({"y"}),
    "am": frozenset({"ma"}),
    "d": frozenset(
        {
            "he",
            "how",
            "i",
            "it",
            "she",
            "that",
            "there",
            "they",
            "this",
            "we",
            "what",
            "when",
            "where",
            "who",
            "why",
            "you",
        }
    ),
    "ll": frozenset(
        {
            "he",
            "how",
            "i",
            "it",
            "she",
            "that",
            "there",
            "they",
            "this",
            "we",
            "what",
            "when",
            "where",
            "who",
            "why",
            "you",
        }
    ),
    "m": frozenset({"i"}),
    "re": frozenset(
        {
            "how",
            "there",
            "they",
            "we",
            "what",
            "when",
            "where",
            "who",
            "why",
            "you",
        }
    ),
    "ve": frozenset(
        {
            "could",
            "how",
            "i",
            "might",
            "must",
            "should",
            "there",
            "they",
            "we",
            "what",
            "when",
            "where",
            "who",
            "would",
            "why",
            "you",
        }
    ),
}
_NEGATIVE_CONTRACTION_STEMS = frozenset(
    {
        "ain",
        "aren",
        "can",
        "couldn",
        "daren",
        "didn",
        "doesn",
        "don",
        "hadn",
        "hasn",
        "haven",
        "isn",
        "mightn",
        "mustn",
        "needn",
        "oughtn",
        "shan",
        "shouldn",
        "wasn",
        "weren",
        "won",
        "wouldn",
    }
)
_CHAINED_APOSTROPHE_SUFFIXES = frozenset({"d", "ll", "re", "s", "ve"})
_SAFE_PLAIN_INLINE_CODE = frozenset(
    {
        "false",
        "model_ref",
        "none",
        "null",
        "slug",
        "symbol",
        "ticker",
        "tickers",
        "true",
    }
)
_INLINE_CODE_RATIO_COMPONENTS = frozenset(
    {
        "assets",
        "book",
        "b",
        "debt",
        "ebit",
        "ebitda",
        "earnings",
        "e",
        "equity",
        "ev",
        "fcf",
        "price",
        "p",
        "revenue",
        "sales",
    }
)
_SAFE_S_CONTRACTION_STEMS = frozenset(
    {"he", "here", "how", "it", "let", "she", "that", "there", "what", "where", "who"}
)
_SAFE_S_POSSESSIVE_STEMS = frozenset(
    {"analysis", "business", "cafe", "consensus", "investor", "status"}
)
_POSSESSIVE_DETERMINERS = frozenset(
    {
        "a",
        "all",
        "an",
        "both",
        "each",
        "either",
        "every",
        "few",
        "her",
        "his",
        "its",
        "many",
        "most",
        "my",
        "neither",
        "no",
        "our",
        "several",
        "some",
        "that",
        "the",
        "their",
        "these",
        "this",
        "those",
        "your",
    }
)
_AFFIRMATIVE_COMPOUND_PLURAL_POSSESSIVES = {
    ("business", "risks"): "impact",
    ("company", "reports"): "findings",
    ("market", "signals"): "value",
}
_SEQUENTIAL_QUOTE_PUNCTUATION = frozenset(
    {"/", "—", "–", "|", "&", "+", ",", ":", ";"}
)
_DECADE_APOSTROPHE_GOVERNORS = frozenset(
    {
        "after",
        "and",
        "april",
        "around",
        "august",
        "before",
        "by",
        "calendar",
        "circa",
        "cy",
        "december",
        "early",
        "february",
        "fiscal",
        "fq",
        "fy",
        "during",
        "from",
        "h1",
        "h2",
        "in",
        "january",
        "july",
        "june",
        "late",
        "march",
        "may",
        "mid",
        "november",
        "october",
        "of",
        "or",
        "september",
        "since",
        "spring",
        "summer",
        "fall",
        "autumn",
        "the",
        "through",
        "throughout",
        "to",
        "until",
        "versus",
        "vs",
        "winter",
        "year",
    }
)
_POSSESSIVE_GOVERNORS = frozenset(
    {
        "about",
        "and",
        "among",
        "at",
        "between",
        "but",
        "by",
        "for",
        "from",
        "into",
        "of",
        "nor",
        "or",
        "over",
        "through",
        "to",
        "toward",
        "under",
        "with",
        "without",
    }
)
_NON_POSSESSIVE_PHRASE_STARTS = frozenset(
    {
        "a",
        "aboard",
        "about",
        "above",
        "abroad",
        "across",
        "after",
        "again",
        "ahead",
        "always",
        "against",
        "along",
        "already",
        "although",
        "am",
        "amid",
        "among",
        "and",
        "an",
        "apart",
        "around",
        "as",
        "aside",
        "at",
        "away",
        "back",
        "before",
        "behind",
        "below",
        "beneath",
        "beside",
        "between",
        "beyond",
        "but",
        "by",
        "because",
        "be",
        "been",
        "being",
        "can",
        "could",
        "despite",
        "did",
        "do",
        "does",
        "down",
        "during",
        "except",
        "fast",
        "for",
        "from",
        "he",
        "had",
        "has",
        "have",
        "here",
        "home",
        "i",
        "if",
        "in",
        "inside",
        "into",
        "it",
        "is",
        "just",
        "like",
        "later",
        "near",
        "nearby",
        "never",
        "nor",
        "may",
        "might",
        "must",
        "now",
        "of",
        "off",
        "on",
        "onto",
        "opposite",
        "or",
        "often",
        "once",
        "outside",
        "over",
        "past",
        "regarding",
        "round",
        "she",
        "shall",
        "should",
        "since",
        "so",
        "sometimes",
        "soon",
        "still",
        "than",
        "that",
        "the",
        "then",
        "there",
        "they",
        "this",
        "those",
        "through",
        "throughout",
        "till",
        "to",
        "today",
        "together",
        "tomorrow",
        "toward",
        "under",
        "underneath",
        "unless",
        "unlike",
        "until",
        "up",
        "upon",
        "twice",
        "via",
        "we",
        "was",
        "were",
        "when",
        "whereas",
        "whether",
        "while",
        "with",
        "will",
        "within",
        "without",
        "would",
        "yesterday",
        "yet",
        "you",
    }
)
_LY_POSSESSIVE_HEADS = frozenset(
    {"assembly", "family", "monopoly", "quarterly", "rally", "supply"}
)
_NOMINAL_MODIFIER_STARTS = frozenset({"early", "later", "only"})
_AUXILIARY_WORDS = frozenset(
    {
        "am",
        "are",
        "be",
        "been",
        "being",
        "can",
        "could",
        "did",
        "do",
        "does",
        "had",
        "has",
        "have",
        "is",
        "may",
        "might",
        "must",
        "shall",
        "should",
        "was",
        "were",
        "will",
        "would",
    }
)
_IRREGULAR_REPORTING_VERBS = frozenset(
    {
        "added",
        "adds",
        "argues",
        "asks",
        "believes",
        "claims",
        "comments",
        "declares",
        "explains",
        "insists",
        "maintains",
        "notes",
        "observes",
        "recalls",
        "replies",
        "reply",
        "responds",
        "respond",
        "said",
        "says",
        "spoke",
        "states",
        "suggests",
        "thinks",
        "told",
        "warns",
        "wrote",
        "writes",
    }
)
_PROPER_PLURAL_NOMINAL_HEADS = frozenset(
    {
        "assets",
        "bonds",
        "businesses",
        "costs",
        "earnings",
        "equities",
        "estimates",
        "goals",
        "holdings",
        "interests",
        "liabilities",
        "margins",
        "methods",
        "operations",
        "options",
        "positions",
        "prices",
        "principles",
        "profits",
        "returns",
        "revenues",
        "rights",
        "risks",
        "sales",
        "shares",
        "stocks",
        "subsidiaries",
        "valuations",
        "weights",
    }
)

_PRIMARY_HOSTS = frozenset(
    {
        "berkshirehathaway.com",
        "brianlangis.wordpress.com",
        "efts.sec.gov",
        "fs.blog",
        "grahamanddoddsville.net",
        "oaktreecapital.com",
        "sec.gov",
        "worldlypartners.com",
    }
)

_COUNTRY_SECOND_LEVEL_SUFFIXES = frozenset(
    {
        "ac.uk",
        "co.jp",
        "co.uk",
        "com.au",
        "com.br",
        "com.cn",
        "com.hk",
        "com.sg",
        "gov.uk",
        "net.au",
        "org.au",
        "org.uk",
    }
)

class PersonaFactoryError(RuntimeError):
    reason_code = "persona_factory_error"


class InsufficientSourcesError(PersonaFactoryError):
    reason_code = "insufficient_sources"

    def __init__(self, domain_count: int):
        self.domain_count = domain_count
        super().__init__(
            f"insufficient_sources: found {domain_count} independent domains; at least 3 required"
        )


class PersonaCollisionError(PersonaFactoryError):
    reason_code = "persona_exists"


class BuiltinPersonaCollisionError(PersonaCollisionError):
    reason_code = "builtin_persona_collision"


class InvalidSlugError(PersonaFactoryError):
    reason_code = "invalid_persona_slug"


class BackendContractError(PersonaFactoryError):
    reason_code = "invalid_research_backend_response"


def slugify(value: str) -> str:
    """Create a stable snake-case artifact slug from a public name."""
    slug = canonical_name_key(value)
    if not slug or not _SLUG_RE.fullmatch(slug):
        raise InvalidSlugError(f"cannot derive a valid slug from {value!r}")
    return slug


def resolve_personas_dir() -> Path:
    """Resolve the user factory directory, honoring the offline-test override."""
    override = os.environ.get("TINYIC_PERSONAS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".tinyic" / "personas"


def canonicalize_url(url: str) -> str:
    """Canonicalize a public HTTP URL for ledger identity and reject secrets."""
    if not isinstance(url, str):
        raise ValueError("evidence URL must be a string")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        raise ValueError("evidence URL is malformed") from None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("evidence URL must be public http(s)")
    if parsed.username or parsed.password:
        raise ValueError("evidence URL must not contain credentials")
    if url_contains_sensitive_material(url):
        raise ValueError("evidence URL must not contain credential-like query parameters")
    scheme = parsed.scheme.casefold()
    host = normalize_public_host(parsed.hostname)
    if host is None:
        raise ValueError("evidence URL host must be public")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    rendered_host = f"[{host}]" if address is not None and address.version == 6 else host
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        netloc = rendered_host
    else:
        netloc = f"{rendered_host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        filtered_query.append((key, value))
    query = urlencode(sorted(filtered_query))
    return urlunsplit((scheme, netloc, path, query, ""))


def independent_domain(url: str) -> str:
    """Return a conservative registrable-domain identity for quality gates.

    A full public-suffix dependency would violate the zero-dependency design.
    The common compound suffixes relevant to cited English-language sources
    are handled explicitly; ordinary hosts collapse to their last two labels.
    """
    raw_host = urlsplit(url).hostname or ""
    host = normalize_public_host(raw_host) or ""
    host = host.removeprefix("www.")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    suffix = ".".join(labels[-2:])
    if suffix in _COUNTRY_SECOND_LEVEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return suffix


def _is_curated_primary(url: str) -> bool:
    host = normalize_public_host(urlsplit(url).hostname or "") or ""
    host = host.removeprefix("www.")
    return any(host == primary or host.endswith(f".{primary}") for primary in _PRIMARY_HOSTS)


class EvidenceLedger:
    """Insertion-ordered, URL-deduplicated evidence with stable 1-based ids."""

    def __init__(self) -> None:
        self._items: list[Evidence] = []
        self._by_url: dict[str, int] = {}

    def add(self, item: Evidence) -> int:
        url = canonicalize_url(item.url)
        title = " ".join(str(item.title).split())
        excerpt = " ".join(str(item.excerpt).split())
        if not title or not excerpt:
            raise ValueError("evidence title and excerpt must not be blank")
        source_type = str(item.source_type).casefold()
        if source_type not in {"primary", "secondary"}:
            raise ValueError("evidence source_type must be primary or secondary")
        if _is_curated_primary(url):
            source_type = "primary"
        normalized = replace(
            item,
            url=url,
            title=title,
            excerpt=excerpt,
            source_type=source_type,
        )
        existing = self._by_url.get(url)
        if existing is None:
            self._items.append(normalized)
            number = len(self._items)
            self._by_url[url] = number
            return number

        # Preserve its stable citation id while retaining the richer excerpt and
        # strongest source classification returned by later search angles.
        index = existing - 1
        old = self._items[index]
        candidates = (old, normalized)
        quote_eligible = tuple(item for item in candidates if item.quote_eligible)
        richer = max(
            quote_eligible or candidates,
            key=lambda item: len(item.excerpt),
        )
        self._items[index] = replace(
            richer,
            source_type=(
                "primary"
                if "primary" in {old.source_type, normalized.source_type}
                else "secondary"
            ),
        )
        return existing

    def extend(self, items: Iterable[Evidence]) -> None:
        for item in items:
            self.add(item)

    @property
    def items(self) -> tuple[Evidence, ...]:
        return tuple(self._items)

    @property
    def domains(self) -> frozenset[str]:
        return frozenset(independent_domain(item.url) for item in self._items)

    @property
    def has_primary(self) -> bool:
        return any(item.source_type == "primary" for item in self._items)

    def numbered_text(self) -> str:
        return "\n\n".join(
            f"[{number}] {item.title}\nURL: {item.url}\n"
            f"Type: {item.source_type}\nExcerpt: {item.excerpt}"
            for number, item in enumerate(self._items, 1)
        )

    def source_records(self, accessed: str) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "title": item.title,
                "url": item.url,
                "type": item.source_type,
                "accessed": item.accessed or accessed,
            }
            for item in self._items
        )


def quality_for(ledger: EvidenceLedger) -> str:
    """Apply the frozen independent-domain and primary-source thresholds."""
    domains = len(ledger.domains)
    if domains < 3:
        raise InsufficientSourcesError(domains)
    if domains >= 5 and ledger.has_primary:
        return "normal"
    return "thin"


def _citation_numbers(text: str, source_count: int) -> tuple[int, ...]:
    return tuple(
        dict.fromkeys(
            number
            for raw in _CITATION_RE.findall(text)
            if 1 <= (number := int(raw)) <= source_count
        )
    )


def _draft_paragraphs(text: str, source_count: int) -> tuple[str, ...]:
    """Keep only model paragraphs carrying at least one valid ledger citation."""
    # Preserve horizontal indentation until the CommonMark safety gate runs;
    # ``str.strip()`` would turn a four-space code block into ordinary prose.
    blocks = re.split(r"\n\s*\n", str(text).strip("\r\n"))
    kept: list[str] = []
    for block in blocks:
        paragraph = "\n".join(
            line.rstrip()
            for line in block.splitlines()
            if not line.lstrip().startswith("#")
        ).strip("\r\n")
        if (
            paragraph
            and _citation_numbers(paragraph, source_count)
            and not _paragraph_has_unsafe_markup(paragraph)
        ):
            kept.append(paragraph)
    return tuple(kept)


def _normalize_public_record_text(value: str) -> str:
    """Normalize model prose for conservative deterministic scope checks."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    cleaned: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category == "Cf":
            # Format controls must not split a high-signal private-life phrase.
            continue
        cleaned.append(" " if category in {"Cc", "Cs"} else character)
    return " ".join("".join(cleaned).split())


def _subject_scope_patterns(
    investor_name: str,
) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Build high-confidence private-life patterns for named subjects."""

    normalized_name = _normalize_public_record_text(investor_name)
    subjects = {"the investor", "the represented investor"}
    if normalized_name:
        subjects.add(normalized_name)
        surname = normalized_name.rsplit(" ", 1)[-1]
        if surname:
            subjects.add(surname)
    subject_alternatives = "|".join(
        re.escape(value)
        for value in sorted(subjects, key=len, reverse=True)
    )
    subject = rf"(?<!\w)(?:{subject_alternatives})(?!\w)"
    safe_family_uses = (
        r"office|business|company|firm|fund|investment|enterprise"
    )
    family_relation = (
        rf"family(?!\s+(?:{safe_family_uses}))|wife|husband|spouse|son|"
        r"daughter|children?|mother|father|parents?|brother|sister"
    )
    return (
        (
            "age",
            re.compile(
                rf"{subject}\s+(?:is|are|was|were)\s+(?:now\s+)?"
                r"\d{1,3}(?:\s|-)+years?(?:\s|-)+old\b|"
                rf"{subject}\s+was\s+born\s+(?:in\s+\d{{4}}|"
                r"on\s+[a-z]+\s+\d{1,2})\b"
            ),
        ),
        (
            "family",
            re.compile(
                rf"{subject}['’]s\s+(?:{family_relation})\b|"
                rf"{subject}\s+(?:has|had)\s+(?:an?\s+)?(?:{family_relation})\b|"
                rf"{subject}\s+(?:is|was|became)\s+(?:married|divorced|widowed)\b"
            ),
        ),
        (
            "health",
            re.compile(
                rf"{subject}['’]s\s+(?:health|illness|disease|diagnosis|"
                r"medical\s+(?:history|condition|treatment))\b|"
                rf"{subject}\s+(?:(?:is|are|was|were|has been|have been)\s+)?"
                r"(?:diagnosed\s+with|hospitalized\s+for|treated\s+for|suffers?\s+from)\b"
            ),
        ),
        (
            "residence",
            re.compile(
                rf"{subject}['’]s\s+(?:residence|home address|street address|"
                r"residential address|phone number|email address)\b|"
                rf"{subject}\s+(?:currently\s+)?(?:lives|resides)\s+(?:at|in)\b"
            ),
        ),
    )


def _assert_public_record_scope(
    entries: Iterable[tuple[str, str]], *, investor_name: str
) -> None:
    """Reject high-confidence personal-life or false-affiliation claims.

    This is intentionally narrower than a general keyword filter. Professional
    phrases such as ``family office``, ``portfolio health``, and a manager who
    endorsed an investment method remain in scope; biographical age, relatives,
    health, residence/contact details, and claims that this artifact is official
    do not.
    """

    issues: list[str] = []
    patterns = _PUBLIC_RECORD_SCOPE_PATTERNS + _subject_scope_patterns(
        investor_name
    )
    for path, value in entries:
        normalized = _normalize_public_record_text(value)
        for category, pattern in patterns:
            if pattern.search(normalized) is not None:
                issues.append(f"{path} contains excluded {category} content")
                break
    if issues:
        # Reuse the existing safe, path-only validation diagnostic. Never echo
        # the provider-generated personal content itself.
        raise SchemaValidationError(issues)


def _format_claim_path(path: Sequence[str | int]) -> str:
    rendered = ""
    for part in path:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += ("." if rendered else "") + part
    return rendered


def _persona_claims(specification: Mapping[str, Any]) -> list[AtomicClaim]:
    claims: list[AtomicClaim] = []

    def scalar(path: tuple[str | int, ...], value: Any, *, quote: bool = False) -> None:
        if isinstance(value, str) and value.strip():
            claims.append(
                AtomicClaim(
                    "persona:" + ".".join(map(str, path)),
                    value.strip(),
                    path=path,
                    quote=quote,
                )
            )

    def string_list(path: tuple[str | int, ...], values: Any) -> None:
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            for index, value in enumerate(values):
                scalar((*path, index), value)

    persona = specification["persona"]
    tinyic = specification["tinyic"]
    scalar(("persona", "occupation", "title"), persona.get("occupation", {}).get("title"))
    scalar(
        ("persona", "occupation", "organization"),
        persona.get("occupation", {}).get("organization"),
    )
    scalar(("persona", "style"), persona.get("style"))
    string_list(("persona", "beliefs"), persona.get("beliefs"))
    string_list(("persona", "skills"), persona.get("skills"))
    string_list(("persona", "other_facts"), persona.get("other_facts"))
    string_list(
        ("persona", "personality", "traits"),
        persona.get("personality", {}).get("traits"),
    )
    string_list(
        ("persona", "behaviors", "general"),
        persona.get("behaviors", {}).get("general"),
    )
    for key in ("interests", "likes", "dislikes"):
        string_list(
            ("persona", "preferences", key), persona.get("preferences", {}).get(key)
        )
    scalar(("tinyic", "epithet"), tinyic.get("epithet"))
    scalar(("tinyic", "philosophy_hook"), tinyic.get("philosophy_hook"))
    for key in ("decision_checklist", "signal_rules", "red_flags"):
        string_list(("tinyic", key), tinyic.get(key))
    for index, quote in enumerate(tinyic.get("famous_quotes", ())):
        if isinstance(quote, Mapping):
            source = quote.get("source")
            citations = (source,) if isinstance(source, int) else ()
            text = quote.get("text")
            if isinstance(text, str) and text.strip():
                claims.append(
                    AtomicClaim(
                        f"persona:tinyic.famous_quotes.{index}",
                        text.strip(),
                        citations=citations,
                        path=("tinyic", "famous_quotes", index),
                        quote=True,
                    )
                )
    return claims


def _get_path(root: Any, path: Sequence[str | int]) -> Any:
    value = root
    for part in path:
        value = value[part]
    return value


def _quote_is_verbatim(
    text: str, citations: Sequence[int], evidence: Sequence[Evidence]
) -> bool:
    quote = " ".join(text.strip().strip(_QUOTE_STRIP_CHARACTERS).split())
    if not quote:
        return False
    for number in citations:
        if not 1 <= number <= len(evidence):
            continue
        item = evidence[number - 1]
        if not item.quote_eligible:
            continue
        excerpt = item.excerpt
        for match in re.finditer(re.escape(quote), excerpt):
            start, end = match.span()
            left_ok = (
                not _is_word_character(quote[0])
                or start == 0
                or not _is_word_character(excerpt[start - 1])
            )
            right_ok = (
                not _is_word_character(quote[-1])
                or end == len(excerpt)
                or not _is_word_character(excerpt[end])
            )
            if left_ok and right_ok:
                return True
    return False


def _is_word_character(character: str) -> bool:
    return character.isalnum() or unicodedata.category(character) in {
        "Cf",
        "Mc",
        "Me",
        "Mn",
        "Pc",
    }


def _is_gated_quote_delimiter(character: str) -> bool:
    if character == "`" or character in _QUOTE_CHARACTERS:
        return True
    name = unicodedata.name(character, "")
    category = unicodedata.category(character)
    return (
        category in {"Pi", "Pf"}
        or any(fragment in name for fragment in _QUOTE_LIKE_UNICODE_NAMES)
        or (
            category in {"Lm", "Sk"}
            and any(fragment in name for fragment in _QUOTE_LIKE_MODIFIER_NAMES)
        )
    )


def _is_unsupported_quote_character(character: str) -> bool:
    if character in _QUOTE_CHARACTERS:
        return False
    category = unicodedata.category(character)
    if category.startswith("M"):
        # Context decides whether a combining mark is an attempted delimiter.
        # Attached marks are legitimate decomposed text; free-standing marks
        # are rejected by ``_is_standalone_combining_mark``.
        return False
    if _is_gated_quote_delimiter(character):
        return True
    normalized = unicodedata.normalize("NFKC", character)
    return normalized != character and any(
        _is_gated_quote_delimiter(mapped) for mapped in normalized
    )


def _is_standalone_combining_mark(text: str, index: int) -> bool:
    """Reject combining marks used as free-standing quote delimiters."""

    if not unicodedata.category(text[index]).startswith("M"):
        return False
    cursor = index - 1
    while cursor >= 0 and (
        unicodedata.category(text[cursor]).startswith("M")
        or unicodedata.category(text[cursor]) == "Cf"
    ):
        cursor -= 1
    return cursor < 0 or not text[cursor].isalnum()


def _is_markdown_escaped(text: str, index: int) -> bool:
    slashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        slashes += 1
        cursor -= 1
    return slashes % 2 == 1


def _markdown_inline_code_end(text: str, start: int) -> int | None:
    """Return the last closer index for a valid CommonMark backtick span."""

    if _is_markdown_escaped(text, start):
        return None
    run = 1
    while start + run < len(text) and text[start + run] == "`":
        run += 1
    delimiter = "`" * run
    cursor = start + run
    while cursor < len(text):
        end = text.find(delimiter, cursor)
        if end < 0:
            return None
        before_is_tick = end > 0 and text[end - 1] == "`"
        after = end + run
        after_is_tick = after < len(text) and text[after] == "`"
        if not before_is_tick and not after_is_tick:
            content = text[start + run : end]
            return after - 1 if content else None
        cursor = end + run
    return None


def _inline_code_content_is_technical(content: str) -> bool:
    """Accept only a small, affirmative grammar of technical tokens."""

    stripped = content.strip()
    if not stripped or "\n" in stripped or "\r" in stripped:
        return False
    folded = " ".join(stripped.split()).casefold()
    if folded in _SAFE_PLAIN_INLINE_CODE:
        return True
    if any(character in _QUOTE_CHARACTERS for character in stripped):
        # The whole displayed value must be exactly one quotation. The caller
        # then parses that span and checks it verbatim; a verified prefix
        # cannot authorize an unchecked suffix inside the same code span.
        outer_delimiters_match = (
            stripped[0] in _DOUBLE_QUOTE_OPENERS
            and stripped[-1] in _DOUBLE_QUOTE_CLOSERS
        ) or (
            stripped[0] in _SINGLE_QUOTE_OPENERS
            and stripped[-1] in _SINGLE_QUOTE_CLOSERS
        )
        if not outer_delimiters_match:
            return False
        parsed_spans = _quoted_spans(stripped)
        return parsed_spans == (stripped[1:-1],)
    if re.fullmatch(r"price\s*`\s*book", folded) is not None:
        return True
    ratio = re.fullmatch(
        r"([a-z]+)\s*/\s*([a-z]+)(?:\s+ratio)?", folded
    )
    if ratio is not None:
        return all(
            component in _INLINE_CODE_RATIO_COMPONENTS
            for component in ratio.groups()
        )
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+", stripped):
        return True
    if re.fullmatch(r"\d{1,2}[A-Z]", stripped) is not None:
        return True
    return re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", stripped) is not None


def _inline_code_content_is_unambiguously_structured_technical(
    content: str,
) -> bool:
    """Accept the narrower grammar safe for source-reporting frames."""

    stripped = content.strip()
    if not stripped or "\n" in stripped or "\r" in stripped:
        return False
    folded = " ".join(stripped.split()).casefold()
    if folded in {"false", "none", "null", "true"}:
        return True
    if re.fullmatch(r"price\s*`\s*book", folded) is not None:
        return True
    ratio = re.fullmatch(
        r"([a-z]+)\s*/\s*([a-z]+)(?:\s+ratio)?", folded
    )
    if ratio is not None:
        return all(
            component in _INLINE_CODE_RATIO_COMPONENTS
            for component in ratio.groups()
        )
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+", stripped):
        return True
    return re.fullmatch(r"\d{1,2}[A-Z]", stripped) is not None


def _inline_code_context_allows_technical_use(
    text: str, start: int, end: int, content: str
) -> bool:
    """Recognize bounded sentence frames that conventionally typeset code."""

    prefix = text[:start]
    suffix = text[end + 1 :]
    clause = re.split(r"[.!?;\n]", prefix)[-1]
    neutral_suffix = (
        r"^\s*(?:(?:at\s+\d+(?:\.\d+)?x)|"
        r"(?:is\s+(?:present|stable|valid))|"
        r"(?:data|discipline|metadata))?"
        r"\s*[.!?]?\s*(?:\[\d+\]\s*)*$"
    )
    if re.fullmatch(
        neutral_suffix,
        suffix,
        flags=re.IGNORECASE,
    ) is None:
        return False
    clause_start = r"^\s*(?:(?:[-+*]|\d{1,9}[.)])\s+)*"
    neutral_use = (
        clause_start
        + r"(?:the\s+)?"
        r"(?:(?:(?:current|famous|primary|reported|valuation)\s+){0,2}"
        r"(?:metric|ratio)|api(?:\s+field)?|code|field|formula|function|"
        r"investor|model|parser|schema|syntax|system)\s+"
        r"(?:accepts|calculates|compares|computes|contains|expects|is|reads|"
        r"references|returns|sets|tracks|uses|was|writes)\s*$"
    )
    if re.search(neutral_use, clause, flags=re.IGNORECASE) is not None:
        return True
    field_label = (
        clause_start
        + r"(?:the\s+)?(?:api\s+)?field"
        r"(?:\s+(?:called|is|named))?\s*$"
    )
    if re.search(field_label, clause, flags=re.IGNORECASE) is not None:
        return True
    technical_reporting = (
        clause_start
        + r"(?:the\s+)?(?:api|dataset|filing|record|report|schema|table)\s+"
        r"(?:contained|contains|disclosed|discloses|listed|lists|presented|"
        r"presents|reported|reports|showed|shows|stated|states)\s*$"
    )
    return (
        re.search(technical_reporting, clause, flags=re.IGNORECASE)
        is not None
        and _inline_code_content_is_unambiguously_structured_technical(
            content
        )
    )


def _inline_code_is_quote_like(
    text: str, start: int, end: int, content: str
) -> bool:
    """Fail closed unless content and surrounding prose are technical."""

    return not (
        _inline_code_content_is_technical(content)
        and _inline_code_context_allows_technical_use(
            text, start, end, content
        )
    )


def _literal_backtick_spans(content: str) -> tuple[str, ...] | None:
    """Extract paired literal backticks from a wider inline-code span."""

    indexes = tuple(
        index for index, character in enumerate(content) if character == "`"
    )
    if len(indexes) <= 1:
        return ()
    if len(indexes) % 2:
        return None
    spans: list[str] = []
    for opener, closer in zip(indexes[::2], indexes[1::2], strict=True):
        if closer == opener + 1:
            return None
        spans.append(content[opener + 1 : closer])
    return tuple(spans)


def _word_after(text: str, start: int) -> str | None:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    match = re.match(r"[A-Za-z0-9_]+", text[index:])
    return match.group(0).casefold() if match is not None else None


def _phrase_words_after(text: str, start: int) -> tuple[tuple[str, ...], bool]:
    tail = text[start:].lstrip()
    if tail.startswith("["):
        return (), False
    segment = re.split(
        r"[!?;,:]|(?:\.(?=\s*(?:\[|$)))", tail, maxsplit=1
    )[0]
    words = tuple(re.findall(r"[A-Za-z0-9_]+", segment))
    if not words:
        return (), False
    first = re.match(r"[A-Za-z0-9_]+", tail)
    hyphenated = first is not None and tail[first.end() :].startswith("-")
    return words, hyphenated


def _is_internal_word_apostrophe(text: str, index: int) -> bool:
    if not (
        index > 0
        and index + 1 < len(text)
        and _is_word_character(text[index - 1])
        and _is_word_character(text[index + 1])
    ):
        return False

    left_start = index
    while left_start > 0 and _is_word_character(text[left_start - 1]):
        left_start -= 1
    right_end = index + 1
    while right_end < len(text) and _is_word_character(text[right_end]):
        right_end += 1
    left = text[left_start:index]
    right = text[index + 1 : right_end]
    left_folded = left.casefold()
    right_folded = right.casefold()
    chained = (
        left_start > 0
        and text[left_start - 1] in _SINGLE_QUOTE_OPENERS
        and _is_internal_word_apostrophe(text, left_start - 1)
    )
    if chained and right_folded in _CHAINED_APOSTROPHE_SUFFIXES:
        return True
    fixed_stems = _FIXED_INTERNAL_APOSTROPHE_STEMS.get(right_folded)
    if fixed_stems is not None:
        return left_folded in fixed_stems
    if right_folded == "t":
        return left_folded in _NEGATIVE_CONTRACTION_STEMS
    if right_folded == "n":
        return (
            right_end + 1 < len(text)
            and text[right_end] in _SINGLE_QUOTE_OPENERS
            and _is_word_character(text[right_end + 1])
        )
    if right_folded == "s":
        normalized_left = "".join(
            character
            for character in unicodedata.normalize("NFKD", left)
            if not unicodedata.category(character).startswith("M")
            and unicodedata.category(character) != "Cf"
        ).casefold()
        if (
            left[:1].isupper()
            or normalized_left in _SAFE_S_CONTRACTION_STEMS
            or normalized_left in _SAFE_S_POSSESSIVE_STEMS
        ):
            return True
        # An otherwise ordinary isolated ``'s`` remains a possessive. If a
        # real later closer exists, however, the pair is an ambiguous quote
        # span and must be verified rather than silently consumed.
        return not _has_later_single_quote_closer(text, right_end)
    if len(left) == 1 and left.isalpha():
        if left_folded in {"d", "l", "o"}:
            return True
        return (
            left_folded == "n"
            and left_start > 0
            and text[left_start - 1] in _SINGLE_QUOTE_OPENERS
        )
    return False


def _is_plural_possessive_mark(text: str, index: int) -> bool:
    """Return whether a boundary apostrophe follows a word ending in s.

    At top level this form is an apostrophe, not an opening quote. Closing
    quotes after an s-ending word are handled while an already-open span is
    scanned, so they never reach this predicate as a new opener.
    """

    return (
        index > 0
        and text[index - 1].casefold() == "s"
        and (
            index + 1 == len(text)
            or not _is_word_character(text[index + 1])
        )
    )


def _plural_boundary_clause_words(text: str, index: int) -> tuple[str, ...]:
    clause = re.split(r"[.!?;:,\n]", text[:index])[-1]
    return tuple(re.findall(r"[A-Za-z]+", clause))


def _is_affirmative_simple_plural_possessive(
    text: str, index: int
) -> bool:
    """Recognize only one-word and determiner-led possessors positively."""

    if not _is_plural_possessive_mark(text, index):
        return False
    words = _plural_boundary_clause_words(text, index)
    return len(words) == 1 or (
        len(words) == 2
        and words[0].casefold() in _POSSESSIVE_DETERMINERS
    )


def _looks_like_reporting_shaped_plural_boundary(text: str, index: int) -> bool:
    """Fail closed on a multi-token subject/finite s-boundary shape.

    This is deliberately the conservative default rather than a vocabulary of
    possible subjects or reporting verbs. Explicit simple and compound
    possessive grammars are handled before this ambiguity rule; other
    multi-token shapes require quote verification when a later s-ending mark
    can act as their closer.
    """

    if (
        not _is_plural_possessive_mark(text, index)
        or _is_affirmative_simple_plural_possessive(text, index)
    ):
        return False
    words = _plural_boundary_clause_words(text, index)
    return (
        len(words) >= 2
        and words[-1].islower()
        and words[-1].casefold().endswith("s")
    )


def _is_affirmative_compound_plural_possessive(
    text: str, index: int
) -> bool:
    """Preserve a bounded set of unambiguous noun-compound possessives.

    Outside this positive grammar, a determiner-led compound ending in ``s``
    is irreducibly ambiguous with a finite clause and intentionally fails
    closed when a later possessive-looking delimiter could close a quote.
    """

    if not _is_plural_possessive_mark(text, index):
        return False
    clause_words = tuple(
        word.casefold() for word in _plural_boundary_clause_words(text, index)
    )
    if len(clause_words) != 3 or clause_words[0] != "the":
        return False
    expected_possessed_head = _AFFIRMATIVE_COMPOUND_PLURAL_POSSESSIVES.get(
        clause_words[1:]
    )
    if expected_possessed_head is None:
        return False

    tail = text[index + 1 :]
    continuation = re.match(
        r"\s*([A-Za-z]+)\s+and\s+([A-Za-z]+s)", tail,
        flags=re.IGNORECASE,
    )
    if (
        continuation is None
        or continuation.group(1).casefold() != expected_possessed_head
    ):
        return False
    second_mark = index + 1 + continuation.end()
    return (
        second_mark < len(text)
        and text[second_mark] in _SINGLE_QUOTE_OPENERS
        and _is_plausible_plural_possessive(text, second_mark)
    )


def _plural_possessive_has_governor(text: str, index: int) -> bool:
    words = tuple(re.findall(r"[A-Za-z0-9_]+", text[:index]))
    return len(words) >= 2 and words[-2].casefold() in _POSSESSIVE_GOVERNORS


def _has_strong_clause_boundary(text: str, start: int, end: int) -> bool:
    return re.search(r"[,;:!?]|\.(?=\s)", text[start:end]) is not None


def _looks_like_single_opener(text: str, index: int) -> bool:
    opening_boundary = index == 0 or text[index - 1].isspace() or text[index - 1] in "([{—–-:=,;"
    return (
        opening_boundary
        and index + 1 < len(text)
        and not text[index + 1].isspace()
    )


def _looks_like_clear_quote_closer(text: str, index: int) -> bool:
    """Return whether a delimiter has an affirmative local closing boundary."""

    if index == 0 or text[index - 1].isspace():
        return False
    if index + 1 == len(text):
        return True
    tail = text[index + 1 :]
    return (
        tail[0].isspace()
        or tail[0] in ",.;:!?"
        or tail[0] in _SEQUENTIAL_QUOTE_PUNCTUATION
        or _CITATION_RE.match(tail) is not None
    )


def _looks_like_clear_sequential_opener(text: str, index: int) -> bool:
    return _looks_like_single_opener(text, index) or (
        index > 0
        and text[index - 1] in _SEQUENTIAL_QUOTE_PUNCTUATION
        and index + 1 < len(text)
        and not text[index + 1].isspace()
    )


def _quote_delimiters_are_coherent(opening: str, closing: str) -> bool:
    return opening == closing or (opening, closing) in _DIRECTIONAL_QUOTE_PAIRS


def _looks_like_nested_same_family_opener(
    text: str,
    index: int,
    delimiters: frozenset[str],
    *,
    opening_delimiter: str,
    single: bool,
) -> bool:
    """Fail closed when a possible closer structurally looks like nesting."""

    if single and _is_internal_word_apostrophe(text, index):
        return False
    later: list[int] = []
    for cursor in range(index + 1, len(text)):
        if text[cursor] not in delimiters:
            continue
        if single and (
            _is_internal_word_apostrophe(text, cursor)
            or _is_trailing_elision_apostrophe(text, cursor)
            or _is_plausible_plural_possessive(text, cursor)
        ):
            continue
        later.append(cursor)
        if len(later) == 2:
            break
    if len(later) < 2:
        return False
    between = _CITATION_RE.sub(" ", text[index + 1 : later[0]])
    punctuation_only = bool(between) and all(
        character in _SEQUENTIAL_QUOTE_PUNCTUATION for character in between
    )
    if (
        _looks_like_clear_quote_closer(text, index)
        and _looks_like_clear_sequential_opener(text, later[0])
        and _quote_delimiters_are_coherent(opening_delimiter, text[index])
        and _quote_delimiters_are_coherent(text[later[0]], text[later[1]])
        and (re.search(r"\s", between) is not None or punctuation_only)
    ):
        # A local closer, intervening whitespace/prose, and a local opener are
        # sufficient to recognize sequential quotations. The prose itself is
        # deliberately open-vocabulary; each resulting quote is still checked
        # independently. Adjacent and punctuation-wrapped candidates lack one
        # of these boundary signals and remain ambiguous nesting.
        return False
    return True


def _leading_apostrophe_match(text: str, index: int) -> re.Match[str] | None:
    tail = text[index + 1 :]
    decade = re.match(r"\d{2}(?:s)?\b", tail, flags=re.IGNORECASE)
    if decade is not None:
        return decade
    word = re.match(r"[A-Za-z]+\b", tail)
    if word is not None and word.group(0).casefold() in _LEADING_APOSTROPHE_WORDS:
        return word
    return None


def _is_trailing_elision_apostrophe(text: str, index: int) -> bool:
    if index == 0 or (
        index + 1 < len(text) and _is_word_character(text[index + 1])
    ):
        return False
    match = re.search(r"([A-Za-z]+)$", text[:index])
    return (
        match is not None
        and len(match.group(1)) >= 3
        and match.group(1).casefold().endswith("in")
    )


def _is_plausible_plural_possessive(text: str, index: int) -> bool:
    if index == 0 or text[index - 1].casefold() != "s":
        return False
    words, hyphenated = _phrase_words_after(text, index + 1)
    if not words:
        return False
    first_raw = words[0]
    following_word = first_raw.casefold()
    if first_raw[0].isupper():
        nominal_head = False
        for raw in words[1:]:
            if not raw or not raw[0].islower():
                continue
            candidate = raw.casefold()
            # An auxiliary before any noun head makes the phrase a clause
            # (``Buffett has said``), not a proper-name possessive modifier.
            if candidate in _AUXILIARY_WORDS:
                return False
            if (
                candidate in _IRREGULAR_REPORTING_VERBS
                or candidate.endswith("ed")
            ):
                return False
            if (
                candidate in _NON_POSSESSIVE_PHRASE_STARTS
                or candidate in _NOMINAL_MODIFIER_STARTS
                or (
                    candidate.endswith("ly")
                    and candidate not in _LY_POSSESSIVE_HEADS
                )
            ):
                continue
            if (
                candidate.endswith("s")
                and candidate not in _PROPER_PLURAL_NOMINAL_HEADS
                and not candidate.endswith("ings")
            ):
                # Before a noun head, an unknown third-person ``-s`` token is
                # a finite clause boundary (``Buffett predicts``), not evidence
                # that the proper name modifies a possessive noun phrase.
                return False
            nominal_head = True
            break
        if not nominal_head:
            return False
    if (
        following_word in _NON_POSSESSIVE_PHRASE_STARTS
        and not hyphenated
        and not (
            following_word in _NOMINAL_MODIFIER_STARTS
            and len(words) > 1
            and words[1].casefold() not in _NON_POSSESSIVE_PHRASE_STARTS
        )
    ):
        return False
    return not (
        following_word.endswith("ly")
        and following_word not in _LY_POSSESSIVE_HEADS
        and (
            len(words) == 1
            or words[1].casefold() in _NON_POSSESSIVE_PHRASE_STARTS
        )
    )


def _has_likely_single_quote_closer(text: str, start: int) -> bool:
    for index in range(start, len(text)):
        if text[index] not in _SINGLE_QUOTE_CLOSERS:
            continue
        if _is_internal_word_apostrophe(
            text, index
        ) or _is_trailing_elision_apostrophe(text, index):
            continue
        if text[index] != "‘" and _leading_apostrophe_match(text, index) is not None:
            # A second decade/elision is its own apostrophe, not the closer for
            # the first one: ``the '80s ... the '90s`` / ``’Tis ..., ’cause``.
            return False
        if _looks_like_single_opener(text, index):
            return False
        # A leading elision or abbreviated year followed by an s-apostrophe
        # boundary is indistinguishable from a quotation ending in s. Treat it
        # as a closer so exact-quote verification fails closed.
        return True
    return False


def _has_later_single_quote_closer(
    text: str,
    start: int,
    *,
    accept_plausible_possessive: bool = False,
) -> bool:
    """Find a later closer without consuming another quote or a possessive."""

    for index in range(start, len(text)):
        if text[index] not in _SINGLE_QUOTE_CLOSERS:
            continue
        if _is_internal_word_apostrophe(
            text, index
        ) or _is_trailing_elision_apostrophe(text, index):
            continue
        if text[index] != "‘" and _leading_apostrophe_match(text, index) is not None:
            return False
        if _looks_like_single_opener(text, index):
            return False
        if (
            _is_plausible_plural_possessive(text, index)
            and (
                not accept_plausible_possessive
                or _plural_possessive_has_governor(text, index)
            )
        ):
            continue
        return True
    return False


def _is_leading_apostrophe(text: str, index: int) -> bool:
    """Return whether a boundary apostrophe starts a decade or common elision."""

    # A left-curly mark is unambiguously typographic opening punctuation.
    if text[index] == "‘":
        return False

    match = _leading_apostrophe_match(text, index)
    if match is None:
        return False

    prefix_words = tuple(
        word.casefold()
        for word in re.findall(
            r"[A-Za-z]+", re.split(r"[.!?;\n]", text[:index])[-1]
        )
    )
    quarter_context = re.search(
        r"\b(?:[1-4]Q|CY|FQ[1-4]?|FY\d{0,4}|H[12]|Q[1-4])\s*$",
        text[:index],
        flags=re.IGNORECASE,
    ) is not None
    if prefix_words and (
        not match.group(0)[0].isdigit()
        or (
            prefix_words[-1] not in _DECADE_APOSTROPHE_GOVERNORS
            and not quarter_context
        )
    ):
        # Mid-clause elisions are quote openers. Abbreviated years remain prose
        # only after their closed-class grammatical governors (for example,
        # "in '08" or "the '80s"). This avoids an open-ended verb allowlist and
        # makes unmatched provider output fail closed.
        return False

    end = index + 1 + match.end()
    # ``'80s'`` and ``'cause'`` are explicitly delimited quotations.  Longer
    # ambiguous phrases are quotes whenever a structural closer remains after
    # contractions, possessives, and independent elisions are excluded.
    return not (
        (end < len(text) and text[end] in _SINGLE_QUOTE_CLOSERS)
        or _has_likely_single_quote_closer(text, end)
    )


def _is_unquoted_apostrophe(
    text: str, index: int, *, allow_plain_plural_possessive: bool
) -> bool:
    if _is_internal_word_apostrophe(
        text, index
    ) or _is_trailing_elision_apostrophe(text, index):
        return True
    if _is_plural_possessive_mark(text, index):
        if _is_affirmative_compound_plural_possessive(text, index):
            return True
        return (
            allow_plain_plural_possessive
            and not _has_later_single_quote_closer(
                text,
                index + 1,
                accept_plausible_possessive=(
                    _looks_like_reporting_shaped_plural_boundary(text, index)
                ),
            )
        )
    return _is_leading_apostrophe(text, index)


def _should_skip_single_closer(text: str, index: int) -> bool:
    if _is_internal_word_apostrophe(text, index):
        return True
    if _is_trailing_elision_apostrophe(text, index):
        return _has_later_single_quote_closer(text, index + 1)
    return (
        _is_plausible_plural_possessive(text, index)
        and _has_later_single_quote_closer(text, index + 1)
    )


def _quoted_spans(text: str) -> tuple[str, ...] | None:
    """Extract prose quotations, returning ``None`` for malformed delimiters.

    Common Western and CJK quote glyphs are accepted in either orientation
    because provider output sometimes normalizes only one side. Apostrophes in
    words, plural possessives, decades, and common leading elisions remain
    ordinary prose. Ambiguous possessives around a single-quoted span are
    parsed conservatively so fabricated suffixes cannot escape exact-quote
    verification.
    """

    spans: list[str] = []
    last_closing_index: int | None = None
    index = 0
    while index < len(text):
        opener = text[index]
        if opener == "`":
            run = 1
            while index + run < len(text) and text[index + run] == "`":
                run += 1
            if run >= 3:
                # Generated dossier prose has no reason to contain a fenced
                # code block. Reject it rather than treating the fence as an
                # unchecked quotation channel.
                return None
            code_end = _markdown_inline_code_end(text, index)
            if code_end is None:
                return None
            code_content = text[index + run : code_end - run + 1]
            if _inline_code_is_quote_like(text, index, code_end, code_content):
                return None
            literal_backtick_spans = _literal_backtick_spans(code_content)
            if literal_backtick_spans is None:
                return None
            # Paired literal backticks inside a wider CommonMark span are
            # visible delimiters and therefore need exact-quote verification.
            # Replace them only after their spans have been captured, then
            # continue scanning all other quote glyphs in the code content.
            code_spans = _quoted_spans(code_content.replace("`", " "))
            if code_spans is None:
                return None
            spans.extend(literal_backtick_spans)
            spans.extend(code_spans)
            index = code_end + 1
            continue
        if _is_standalone_combining_mark(text, index):
            return None
        if _is_unsupported_quote_character(opener):
            return None
        if opener in _DOUBLE_QUOTE_OPENERS:
            closers = _DOUBLE_QUOTE_CLOSERS
            single = False
        elif opener in _SINGLE_QUOTE_OPENERS and not _is_unquoted_apostrophe(
            text,
            index,
            allow_plain_plural_possessive=(
                last_closing_index is None
                or _has_strong_clause_boundary(
                    text, last_closing_index + 1, index
                )
                or _plural_possessive_has_governor(text, index)
            ),
        ):
            closers = _SINGLE_QUOTE_CLOSERS
            single = True
        else:
            index += 1
            continue

        closing_index = index + 1
        while closing_index < len(text):
            candidate = text[closing_index]
            if _is_standalone_combining_mark(text, closing_index):
                return None
            if _is_unsupported_quote_character(candidate):
                return None
            if candidate in closers:
                if (
                    closing_index > 0
                    and closing_index + 1 < len(text)
                    and _is_word_character(text[closing_index - 1])
                    and _is_word_character(text[closing_index + 1])
                    and (
                        not single
                        or not _is_internal_word_apostrophe(
                            text, closing_index
                        )
                    )
                ):
                    # A delimiter embedded between two words is either a
                    # no-space quote boundary or same-family nesting. Both are
                    # malformed unless the mark is a recognized apostrophe.
                    return None
                if _looks_like_nested_same_family_opener(
                    text,
                    closing_index,
                    closers,
                    opening_delimiter=opener,
                    single=single,
                ):
                    return None
                if (
                    _looks_like_single_opener(text, closing_index)
                ):
                    # Same-delimiter nesting is ambiguous by construction.  Do
                    # not greedily split around an unchecked inner quotation.
                    return None
                if single and _should_skip_single_closer(text, closing_index):
                    closing_index += 1
                    continue
                break
            closing_index += 1

        if closing_index >= len(text):
            return None
        if closing_index == index + 1:
            return None
        spans.append(text[index + 1 : closing_index])
        last_closing_index = closing_index
        index = closing_index + 1

    return tuple(spans)


def _advance_columns(text: str, start_column: int = 0) -> int:
    columns = start_column
    for character in text:
        if character == " ":
            columns += 1
        elif character == "\t":
            columns += 4 - (columns % 4)
        else:
            break
    return columns


def _leading_indentation_columns(line: str) -> int:
    indentation = re.match(r"[ \t]*", line)
    return _advance_columns(indentation.group(0) if indentation else "")


def _list_item_has_code_padding(line: str) -> bool:
    """Consume nested list markers and detect a five-column code item."""

    indentation = re.match(r"[ \t]*", line)
    raw_indentation = indentation.group(0) if indentation else ""
    cursor = len(raw_indentation)
    column = _advance_columns(raw_indentation)
    if column > 3:
        return False
    while cursor < len(line):
        marker = re.match(r"(?:[-+*]|\d{1,9}[.)])", line[cursor:])
        if marker is None:
            return False
        column += len(marker.group(0))
        cursor += marker.end()
        padding = re.match(r"[ \t]+", line[cursor:])
        if padding is None:
            return False
        raw_padding = padding.group(0)
        next_column = _advance_columns(raw_padding, column)
        if next_column - column >= 5:
            return True
        column = next_column
        cursor += padding.end()
    return False


def _paragraph_has_unsafe_markup(paragraph: str) -> bool:
    """Reject active Markdown/HTML while preserving plain ``[1]`` citations."""

    # Decode character references before inspection so an alternate Markdown
    # renderer cannot turn entity-obfuscated markup into an active construct.
    text = html_unescape(paragraph)
    for match in _RAW_HTML_OR_AUTOLINK_RE.finditer(text):
        if not _is_markdown_escaped(text, match.start()):
            return True
    for pattern in (_MARKDOWN_LINK_END_RE, _MARKDOWN_IMAGE_START_RE):
        for match in pattern.finditer(text):
            if not _is_markdown_escaped(text, match.start()):
                return True
    for match in _MARKDOWN_REFERENCE_DEFINITION_RE.finditer(text):
        label_start = text.find("[", match.start(), match.end())
        if label_start >= 0 and not _is_markdown_escaped(text, label_start):
            return True
    return False


def _paragraph_has_commonmark_code(paragraph: str) -> bool:
    """Reject fenced and standalone indented code in generated prose."""

    if _MARKDOWN_FENCE_RE.search(paragraph) is not None:
        return True
    first_content_line = next(
        (line for line in paragraph.splitlines() if line.strip()), None
    )
    if first_content_line is None:
        return False
    if _list_item_has_code_padding(first_content_line):
        # Five columns after a list marker form an indented code item, not an
        # ordinary one-to-four-column list continuation.
        return True
    return (
        _leading_indentation_columns(first_content_line) >= 4
    )


def _paragraph_quotes_are_verbatim(
    paragraph: str, citations: Sequence[int], evidence: Sequence[Evidence]
) -> bool:
    if (
        _paragraph_has_commonmark_code(paragraph)
        or _paragraph_has_unsafe_markup(paragraph)
    ):
        return False
    blockquotes: list[str] = []
    current_blockquote: list[str] = []
    for line in paragraph.splitlines():
        match = _MARKDOWN_BLOCKQUOTE_RE.match(line)
        if match is not None:
            content = match.group(1).strip()
            if not content:
                if current_blockquote:
                    blockquotes.append(" ".join(current_blockquote))
                    current_blockquote = []
                continue
            current_blockquote.append(content)
            continue
        if current_blockquote and line.strip():
            # CommonMark permits an unmarked lazy continuation line inside a
            # blockquote paragraph. Include it in the exact-verbatim gate.
            current_blockquote.append(line.strip())
    if current_blockquote:
        blockquotes.append(" ".join(current_blockquote))
    for blockquote in blockquotes:
        blockquote = " ".join(_CITATION_RE.sub(" ", blockquote).split())
        if not _quote_is_verbatim(blockquote, citations, evidence):
            return False

    # Markdown renderers decode named and numeric character references before
    # displaying punctuation; scan the decoded form so ``&ldquo;`` cannot
    # disguise a quotation.
    spans = _quoted_spans(html_unescape(paragraph))
    if spans is None:
        return False
    return all(
        _quote_is_verbatim(quote, citations, evidence)
        for quote in spans
    )


def _verification_prompt(claims: Sequence[AtomicClaim], ledger: EvidenceLedger) -> str:
    claim_text = "\n".join(
        f"{claim.claim_id}: {claim.text}" for claim in claims
    )
    return (
        "FActScore-lite verification. Decompose only if needed, then return a "
        "mapping for claims fully supported by the numbered ledger. Omit every "
        "unsupported claim. Map supported ids to the supporting 1-based source "
        "numbers. A quote is supported only if its exact words appear in the "
        "cited excerpt.\n\nCLAIMS\n"
        f"{claim_text}\n\nLEDGER\n{ledger.numbered_text()}"
    )


def _supported_claim_ids(
    response: Any,
    claims: Sequence[AtomicClaim],
    evidence: Sequence[Evidence],
) -> set[str]:
    supported_raw = getattr(response, "supported", None)
    if not isinstance(supported_raw, Mapping):
        raise BackendContractError("verify() must return VerificationResponse.supported")
    by_id = {claim.claim_id: claim for claim in claims}
    supported: set[str] = set()
    for claim_id, raw_numbers in supported_raw.items():
        claim = by_id.get(str(claim_id))
        if claim is None or not isinstance(raw_numbers, Sequence) or isinstance(raw_numbers, (str, bytes)):
            continue
        numbers = tuple(
            number
            for number in raw_numbers
            if isinstance(number, int)
            and not isinstance(number, bool)
            and 1 <= number <= len(evidence)
        )
        if not numbers:
            continue
        if claim.citations and not set(numbers).intersection(claim.citations):
            continue
        quote_citations = claim.citations or numbers
        if claim.quote and not _quote_is_verbatim(claim.text, quote_citations, evidence):
            continue
        supported.add(claim.claim_id)
    return supported


def _filter_persona(
    specification: Mapping[str, Any],
    claims: Sequence[AtomicClaim],
    supported: set[str],
) -> dict[str, Any]:
    filtered = copy.deepcopy(dict(specification))
    # Remove list elements from the back so recorded indexes stay valid.
    rejected = [claim for claim in claims if claim.claim_id not in supported]
    for claim in sorted(
        rejected,
        key=lambda value: (
            len(value.path),
            tuple(str(part) for part in value.path[:-1]),
            value.path[-1] if isinstance(value.path[-1], int) else -1,
        ),
        reverse=True,
    ):
        parent_path, leaf = claim.path[:-1], claim.path[-1]
        try:
            parent = _get_path(filtered, parent_path)
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(leaf, int) and isinstance(parent, list) and leaf < len(parent):
            parent.pop(leaf)
        elif isinstance(parent, dict) and isinstance(leaf, str):
            parent[leaf] = ""

    persona = filtered["persona"]
    tinyic = filtered["tinyic"]
    # Schema-preserving, non-biographical fallbacks do not assert facts about
    # the represented person and therefore need no evidence mapping.
    if not persona.get("style"):
        persona["style"] = "Measured, evidence-led, and explicit about uncertainty."
    persona.setdefault("beliefs", [])
    persona.setdefault("skills", [])
    persona.setdefault("other_facts", [])
    persona.setdefault("personality", {}).setdefault("traits", [])
    persona.setdefault("behaviors", {}).setdefault("general", [])
    preferences = persona.setdefault("preferences", {})
    for key in ("interests", "likes", "dislikes"):
        preferences.setdefault(key, [])
    if not tinyic.get("epithet"):
        tinyic["epithet"] = "Public-record investment philosophy simulation"
    if not tinyic.get("philosophy_hook"):
        tinyic["philosophy_hook"] = (
            "Apply only the investment principles supported by the cited public record."
        )
    checklist = tinyic.setdefault("decision_checklist", [])
    generic_steps = (
        "State what the cited evidence establishes.",
        "Assess downside and uncertainty before upside.",
        "Separate documented method from inference.",
    )
    for step in generic_steps:
        if len(checklist) >= 3:
            break
        if step not in checklist:
            checklist.append(step)
    tinyic.setdefault("signal_rules", [])
    tinyic.setdefault("red_flags", [])
    tinyic.setdefault("famous_quotes", [])
    occupation = persona.setdefault("occupation", {})
    for key in ("organization", "title"):
        if not occupation.get(key):
            occupation.pop(key, None)
    occupation["description"] = tinyic["epithet"]
    return filtered


def _source_records(ledger: EvidenceLedger, accessed: str) -> tuple[dict[str, str], ...]:
    return ledger.source_records(accessed)


class _ArtifactThreadLock:
    """Reference-counted same-process guard for one artifact-pair lock."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0


_ARTIFACT_THREAD_LOCKS_GUARD = threading.Lock()
_ARTIFACT_THREAD_LOCKS: dict[str, _ArtifactThreadLock] = {}


@contextmanager
def _in_process_artifact_lock(key: str):
    with _ARTIFACT_THREAD_LOCKS_GUARD:
        entry = _ARTIFACT_THREAD_LOCKS.get(key)
        if entry is None:
            entry = _ArtifactThreadLock()
            _ARTIFACT_THREAD_LOCKS[key] = entry
        entry.users += 1
    entry.lock.acquire()
    try:
        yield
    finally:
        entry.lock.release()
        with _ARTIFACT_THREAD_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0:
                _ARTIFACT_THREAD_LOCKS.pop(key, None)


def _artifact_lock_path(agent_path: Path) -> Path:
    name = agent_path.name
    slug = name.removesuffix(".agent.json")
    if slug == name:
        slug = agent_path.stem
    return agent_path.with_name(f".{slug}.lock")


def _open_artifact_lock(path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR
    for optional_flag in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, optional_flag, 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f"persona artifact lock is not a regular file: {path}")
        os.fchmod(descriptor, 0o600)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _lock_artifact_descriptor(descriptor: int) -> None:
    if os.name == "posix":
        import fcntl

        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                return
            except InterruptedError:
                continue
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        import msvcrt

        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                return
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                time.sleep(0.05)
    raise OSError(f"unsupported platform for persona artifact locking: {os.name}")


def _unlock_artifact_descriptor(descriptor: int) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    raise OSError(f"unsupported platform for persona artifact locking: {os.name}")


@contextmanager
def _artifact_pair_lock(agent_path: Path):
    lock_path = _artifact_lock_path(agent_path)
    key = os.path.normcase(os.path.realpath(lock_path))
    with _in_process_artifact_lock(key):
        descriptor = _open_artifact_lock(lock_path)
        locked = False
        try:
            _lock_artifact_descriptor(descriptor)
            locked = True
            yield
        finally:
            try:
                if locked:
                    _unlock_artifact_descriptor(descriptor)
            finally:
                os.close(descriptor)


def _stage_file(path: Path, content: bytes) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _restore(path: Path, old: bytes | None, *, replace_fn: Callable[[Path, Path], Any]) -> None:
    if old is None:
        path.unlink(missing_ok=True)
        return
    staged = _stage_file(path, old)
    try:
        replace_fn(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _restore_error(
    path: Path,
    old: bytes | None,
    *,
    replace_fn: Callable[[Path, Path], Any],
) -> BaseException | None:
    """Best-effort restore, tolerating a move-then-raise implementation."""

    try:
        current = path.read_bytes() if path.exists() else None
    except BaseException as error:
        return error
    if current == old:
        return None
    try:
        _restore(path, old, replace_fn=replace_fn)
    except BaseException as error:
        try:
            current = path.read_bytes() if path.exists() else None
        except BaseException:
            return error
        return None if current == old else error
    try:
        current = path.read_bytes() if path.exists() else None
    except BaseException as error:
        return error
    if current != old:
        return OSError("artifact restore returned without restoring the destination")
    return None


def write_artifact_pair(
    agent_path: Path,
    agent_bytes: bytes,
    dossier_path: Path,
    dossier_bytes: bytes,
    *,
    force: bool,
    replace_fn: Callable[[Path, Path], Any] = os.replace,
) -> None:
    """Lock, stage, and install an artifact pair, rolling back on failure."""
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    with _artifact_pair_lock(agent_path):
        if not force and (agent_path.exists() or dossier_path.exists()):
            raise PersonaCollisionError(
                "persona artifacts already exist for "
                f"{agent_path.stem.removesuffix('.agent')}"
            )
        old_agent = agent_path.read_bytes() if agent_path.exists() else None
        old_dossier = dossier_path.read_bytes() if dossier_path.exists() else None
        staged_agent: Path | None = None
        staged_dossier: Path | None = None
        try:
            staged_agent = _stage_file(agent_path, agent_bytes)
            staged_dossier = _stage_file(dossier_path, dossier_bytes)
            replace_fn(staged_agent, agent_path)
            replace_fn(staged_dossier, dossier_path)
        except BaseException:
            # Restore both destinations independently. An injected or exotic
            # replacement can move a file and then raise during installation or
            # rollback, so destination bytes—not call return values—are decisive.
            rollback_errors = tuple(
                error
                for error in (
                    _restore_error(agent_path, old_agent, replace_fn=replace_fn),
                    _restore_error(dossier_path, old_dossier, replace_fn=replace_fn),
                )
                if error is not None
            )
            if rollback_errors:
                raise RuntimeError("persona artifact rollback failed") from rollback_errors[0]
            raise
        finally:
            if staged_agent is not None:
                staged_agent.unlink(missing_ok=True)
            if staged_dossier is not None:
                staged_dossier.unlink(missing_ok=True)


class PersonaFactory:
    """Run the complete cited research pipeline through an injected backend."""

    def __init__(
        self,
        backend: ResearchBackend,
        *,
        protected_slugs: Iterable[str] = PROTECTED_BUILTIN_SLUGS,
        clock: Callable[[], date] = date.today,
        progress: Callable[[str], None] | None = None,
        writer: Callable[..., None] = write_artifact_pair,
    ) -> None:
        self.backend = backend
        self.protected_slugs = frozenset(protected_slugs)
        self.clock = clock
        self.progress = progress or (lambda _message: None)
        self.writer = writer

    def run(self, request: ResearchRequest) -> ResearchResult:
        investor_name = " ".join(str(request.investor_name).split())
        if not investor_name:
            raise ValueError("investor_name must not be blank")
        slug = slugify(request.slug if request.slug is not None else investor_name)
        if slug in self.protected_slugs:
            raise BuiltinPersonaCollisionError(
                f"{slug!r} is a protected built-in persona and cannot be overwritten"
            )
        output_dir = (
            resolve_personas_dir()
            if request.output_dir is None
            else Path(request.output_dir).expanduser()
        )
        agent_path = output_dir / f"{slug}.agent.json"
        dossier_path = output_dir / f"{slug}.dossier.md"
        if not request.force and (agent_path.exists() or dossier_path.exists()):
            raise PersonaCollisionError(
                f"persona {slug!r} already exists; use force=True to replace both artifacts"
            )

        plan = plan_queries(investor_name, max_searches=request.max_searches)
        ledger = EvidenceLedger()
        usage_records: list[CallUsage] = []
        search_calls = 0
        self.progress("planning")
        for query in plan.queries:
            remaining_searches = request.max_searches - search_calls
            if remaining_searches <= 0:
                self.progress("search:budget_exhausted")
                break
            self.progress(f"search:{query.angle}")
            response = self.backend.search(
                SearchRequest(
                    investor_name,
                    query,
                    remaining_searches=remaining_searches,
                )
            )
            evidence = getattr(response, "evidence", None)
            if not isinstance(evidence, Sequence):
                raise BackendContractError("search() must return SearchResponse.evidence")
            budget_exhausted = getattr(response, "budget_exhausted", False)
            if not isinstance(budget_exhausted, bool):
                raise BackendContractError(
                    "search() must return a boolean SearchResponse.budget_exhausted"
                )
            usage = getattr(response, "usage", None)
            if usage is not None:
                usage_records.append(usage)
                # Provider adapters report the actual priced unit here:
                # server-side invocations for OpenAI/Grok, grounded prompts
                # for Gemini 2.5, and client/server echo rounds for Kimi.
                # Older/custom backends may omit it, in which case their
                # operation-call count is the only safe accounting fallback.
                reported_search_calls = getattr(usage, "search_calls", None)
                if reported_search_calls is None:
                    reported_search_calls = getattr(usage, "calls", 1)
                if (
                    not isinstance(reported_search_calls, int)
                    or isinstance(reported_search_calls, bool)
                    or reported_search_calls < 0
                ):
                    raise BackendContractError(
                        "search usage.search_calls must be a non-negative integer or None"
                    )
                search_calls += reported_search_calls
            elif not budget_exhausted:
                # Backends may omit token usage, but a completed ordinary
                # search still consumed one provider call.
                search_calls += 1
            for item in evidence:
                if not isinstance(item, Evidence):
                    raise BackendContractError("search evidence entries must be Evidence")
                if url_contains_sensitive_material(item.url):
                    self.progress("search:unsafe_url_dropped")
                    continue
                ledger.add(
                    replace(
                        item,
                        query=item.query or query.text,
                        provider=item.provider or str(getattr(self.backend, "provider", "")),
                    )
                )
            if budget_exhausted or search_calls >= request.max_searches:
                self.progress("search:budget_exhausted")
                break

        quality = quality_for(ledger)  # refusal occurs before any artifact write
        generated_date = self.clock().isoformat()
        source_records = _source_records(ledger, generated_date)
        _assert_public_record_scope(
            (
                (f"tinyic.sources[{index}].title", record["title"])
                for index, record in enumerate(source_records)
            ),
            investor_name=investor_name,
        )
        generation = {
            "generated_by": "tinyic persona research",
            "model_ref": str(self.backend.model_ref),
            "date": generated_date,
            "search_calls": search_calls,
            "quality": quality,
            "disclaimer": DISCLAIMER,
        }

        self.progress("distilling_dossier")
        draft_sections: dict[str, tuple[str, ...]] = {}
        for key, title, instructions in DOSSIER_SECTIONS:
            response = self.backend.synthesize_dossier(
                DossierSynthesisRequest(
                    investor_name,
                    key,
                    title,
                    render_dossier_prompt(
                        investor_name,
                        title,
                        instructions,
                        ledger.numbered_text(),
                    ),
                    ledger.items,
                )
            )
            if not isinstance(getattr(response, "text", None), str):
                raise BackendContractError("synthesize_dossier() must return text")
            if getattr(response, "usage", None) is not None:
                usage_records.append(response.usage)
            paragraphs = _draft_paragraphs(response.text, len(ledger.items))
            if not paragraphs:
                paragraphs = (
                    f"The public record does not establish a sufficiently specific {title.casefold()} profile. [1]",
            )
            _assert_public_record_scope(
                (
                    (f"dossier.{key}[{index}]", paragraph)
                    for index, paragraph in enumerate(paragraphs)
                ),
                investor_name=investor_name,
            )
            draft_sections[key] = paragraphs

        self.progress("distilling_persona")
        persona_response = self.backend.synthesize_persona(
            PersonaSynthesisRequest(
                investor_name,
                slug,
                render_persona_prompt(
                    investor_name,
                    draft_sections,
                    ledger.numbered_text(),
                ),
                {key: tuple(value) for key, value in draft_sections.items()},
                ledger.items,
                tuple(dict(item) for item in source_records),
                dict(generation),
            )
        )
        raw_specification = getattr(persona_response, "specification", None)
        if not isinstance(raw_specification, Mapping):
            raise BackendContractError("synthesize_persona() must return a mapping")
        if getattr(persona_response, "usage", None) is not None:
            usage_records.append(persona_response.usage)
        specification = copy.deepcopy(dict(raw_specification))
        specification["type"] = "TinyPerson"
        if isinstance(specification.get("persona"), dict):
            specification["persona"]["name"] = investor_name
        if isinstance(specification.get("tinyic"), dict):
            specification["tinyic"]["schema_version"] = 1
            # Generated temperament is a neutral simulation control, not a
            # biographical claim inferred from public evidence.
            specification["tinyic"]["temperament"] = "balanced"
        # Generation metadata and sources are deterministic factory-owned fields;
        # a provider cannot suppress or forge them.
        if isinstance(specification.get("tinyic"), dict):
            specification["tinyic"]["sources"] = [dict(item) for item in source_records]
            specification["tinyic"]["generation"] = dict(generation)
        validate_agent_spec(specification)
        persona_claims = _persona_claims(specification)
        _assert_public_record_scope(
            (
                (_format_claim_path(claim.path), claim.text)
                for claim in persona_claims
            ),
            investor_name=investor_name,
        )

        dossier_claims: list[AtomicClaim] = []
        for key, paragraphs in draft_sections.items():
            for index, paragraph in enumerate(paragraphs):
                citations = _citation_numbers(paragraph, len(ledger.items))
                dossier_claims.append(
                    AtomicClaim(
                        f"dossier:{key}:{index}",
                        paragraph,
                        citations=citations,
                        path=(key, index),
                        # A dossier claim is the whole paragraph; its embedded
                        # quotations are checked individually below. Marking the
                        # whole paragraph as a quote would compare prose framing
                        # against the excerpt and incorrectly drop valid quotes.
                        quote=False,
                    )
                )
        all_claims = tuple(dossier_claims + persona_claims)
        self.progress("verifying")
        verification = self.backend.verify(
            VerificationRequest(
                investor_name,
                _verification_prompt(all_claims, ledger),
                all_claims,
                ledger.items,
            )
        )
        if getattr(verification, "usage", None) is not None:
            usage_records.append(verification.usage)
        supported = _supported_claim_ids(verification, all_claims, ledger.items)

        verified_sections: dict[str, tuple[str, ...]] = {}
        for key, paragraphs in draft_sections.items():
            kept: list[str] = []
            for index, paragraph in enumerate(paragraphs):
                claim_id = f"dossier:{key}:{index}"
                citations = _citation_numbers(paragraph, len(ledger.items))
                if claim_id in supported and _paragraph_quotes_are_verbatim(
                    paragraph, citations, ledger.items
                ):
                    kept.append(paragraph)
            if not kept:
                title = next(
                    title
                    for section_key, title, _ in DOSSIER_SECTIONS
                    if section_key == key
                )
                kept.append(
                    f"The public record does not establish a sufficiently specific {title.casefold()} profile. [1]"
                )
            verified_sections[key] = tuple(kept)

        specification = _filter_persona(specification, persona_claims, supported)
        # Factory-controlled values survive filtering and cannot be model claims.
        specification["tinyic"]["sources"] = [dict(item) for item in source_records]
        specification["tinyic"]["generation"] = dict(generation)
        specification["persona"]["occupation"]["description"] = specification["tinyic"]["epithet"]
        validate_agent_spec(specification)

        usage = UsageSummary.from_records(usage_records, search_calls=search_calls)
        dossier = render_dossier(
            investor_name=investor_name,
            epithet=specification["tinyic"]["epithet"],
            sections=verified_sections,
            evidence=ledger.items,
            quality=quality,
            model_ref=str(self.backend.model_ref),
            generated_date=generated_date,
            search_calls=search_calls,
            cost_usd=usage.cost_usd,
        )
        agent_bytes = (
            json.dumps(specification, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
        ).encode("utf-8")
        dossier_bytes = dossier.encode("utf-8")
        self.progress("writing")
        self.writer(
            agent_path,
            agent_bytes,
            dossier_path,
            dossier_bytes,
            force=request.force,
        )
        return ResearchResult(
            investor_name,
            slug,
            agent_path,
            dossier_path,
            len(ledger.items),
            len(ledger.domains),
            quality,
            usage,
            specification,
        )


__all__ = [
    "BackendContractError",
    "BuiltinPersonaCollisionError",
    "DISCLAIMER",
    "EvidenceLedger",
    "InsufficientSourcesError",
    "InvalidSlugError",
    "LOW_SOURCE_WARNING",
    "PROTECTED_BUILTIN_SLUGS",
    "PersonaCollisionError",
    "PersonaFactory",
    "PersonaFactoryError",
    "canonicalize_url",
    "independent_domain",
    "quality_for",
    "resolve_personas_dir",
    "slugify",
    "write_artifact_pair",
]
