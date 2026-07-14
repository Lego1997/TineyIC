"""Offline-first, cited investor-persona research pipeline.

All nondeterministic provider work is behind ``ResearchBackend``.  This module
owns the safety properties that must not be delegated to a model: URL
deduplication, citation checks, quote verification, source-quality gates,
schema validation, collision handling, and guarded last-stage writes.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date
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

_DOUBLE_QUOTE_OPENERS = frozenset({'"', "“"})
_DOUBLE_QUOTE_CLOSERS = frozenset({'"', "”"})
_SINGLE_QUOTE_OPENERS = frozenset({"'", "‘"})
_SINGLE_QUOTE_CLOSERS = frozenset({"'", "’"})

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
    parsed = urlsplit(url.strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"evidence URL must be public http(s): {url!r}")
    if parsed.username or parsed.password:
        raise ValueError("evidence URL must not contain credentials")
    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold().rstrip(".")
    port = parsed.port
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        netloc = host
    else:
        netloc = f"{host}:{port}"
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
    host = urlsplit(url).hostname or ""
    host = host.casefold().rstrip(".").removeprefix("www.")
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
    host = (urlsplit(url).hostname or "").casefold().rstrip(".").removeprefix("www.")
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
        richer = normalized if len(normalized.excerpt) > len(old.excerpt) else old
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
    blocks = re.split(r"\n\s*\n", str(text).strip())
    kept: list[str] = []
    for block in blocks:
        paragraph = " ".join(
            line.strip() for line in block.splitlines() if not line.lstrip().startswith("#")
        ).strip()
        if paragraph and _citation_numbers(paragraph, source_count):
            kept.append(paragraph)
    return tuple(kept)


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


def _quote_is_verbatim(text: str, citations: Sequence[int], evidence: Sequence[Evidence]) -> bool:
    quote = text.strip().strip('"“”\'‘’')
    return bool(quote) and any(
        1 <= number <= len(evidence) and quote in evidence[number - 1].excerpt
        for number in citations
    )


def _quoted_spans(text: str) -> tuple[str, ...]:
    """Extract paired prose quotations without mistaking apostrophes for quotes."""

    spans: list[str] = []
    index = 0
    while index < len(text):
        opener = text[index]
        if opener in _DOUBLE_QUOTE_OPENERS:
            closers = _DOUBLE_QUOTE_CLOSERS
            single = False
        elif opener in _SINGLE_QUOTE_OPENERS and (
            index == 0 or not (text[index - 1].isalnum() or text[index - 1] == "_")
        ):
            closers = _SINGLE_QUOTE_CLOSERS
            single = True
        else:
            index += 1
            continue

        closing_index = index + 1
        while closing_index < len(text):
            candidate = text[closing_index]
            if candidate in closers and (
                not single
                or closing_index + 1 == len(text)
                or not (
                    text[closing_index + 1].isalnum()
                    or text[closing_index + 1] == "_"
                )
            ):
                break
            closing_index += 1

        if closing_index >= len(text):
            index += 1
            continue
        if closing_index - index > 2:
            spans.append(text[index + 1 : closing_index])
        index = closing_index + 1

    return tuple(spans)


def _paragraph_quotes_are_verbatim(
    paragraph: str, citations: Sequence[int], evidence: Sequence[Evidence]
) -> bool:
    return all(
        _quote_is_verbatim(quote, citations, evidence)
        for quote in _quoted_spans(paragraph)
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
    persona.setdefault("occupation", {})["description"] = tinyic["epithet"]
    return filtered


def _source_records(ledger: EvidenceLedger, accessed: str) -> tuple[dict[str, str], ...]:
    return ledger.source_records(accessed)


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


def write_artifact_pair(
    agent_path: Path,
    agent_bytes: bytes,
    dossier_path: Path,
    dossier_bytes: bytes,
    *,
    force: bool,
    replace_fn: Callable[[Path, Path], Any] = os.replace,
) -> None:
    """Stage both files before commit and roll back if the second rename fails."""
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    if not force and (agent_path.exists() or dossier_path.exists()):
        raise PersonaCollisionError(
            f"persona artifacts already exist for {agent_path.stem.removesuffix('.agent')}"
        )
    old_agent = agent_path.read_bytes() if agent_path.exists() else None
    old_dossier = dossier_path.read_bytes() if dossier_path.exists() else None
    staged_agent = _stage_file(agent_path, agent_bytes)
    staged_dossier = _stage_file(dossier_path, dossier_bytes)
    agent_committed = False
    try:
        replace_fn(staged_agent, agent_path)
        agent_committed = True
        replace_fn(staged_dossier, dossier_path)
    except BaseException:
        if agent_committed:
            _restore(agent_path, old_agent, replace_fn=replace_fn)
        # Normally the second rename failed before changing the destination. If
        # an exotic replace implementation changed then raised, restore it too.
        current = dossier_path.read_bytes() if dossier_path.exists() else None
        if current != old_dossier:
            _restore(dossier_path, old_dossier, replace_fn=replace_fn)
        raise
    finally:
        staged_agent.unlink(missing_ok=True)
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
                source_records,
                generation,
            )
        )
        raw_specification = getattr(persona_response, "specification", None)
        if not isinstance(raw_specification, Mapping):
            raise BackendContractError("synthesize_persona() must return a mapping")
        if getattr(persona_response, "usage", None) is not None:
            usage_records.append(persona_response.usage)
        specification = copy.deepcopy(dict(raw_specification))
        # Generation metadata and sources are deterministic factory-owned fields;
        # a provider cannot suppress or forge them.
        if isinstance(specification.get("tinyic"), dict):
            specification["tinyic"]["sources"] = [dict(item) for item in source_records]
            specification["tinyic"]["generation"] = dict(generation)
        validate_agent_spec(specification)

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
        persona_claims = _persona_claims(specification)
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
