"""Deterministic prompts and artifact rendering for the persona factory.

Keeping these templates separate from provider orchestration makes them easy to
snapshot-test offline.  Callers pass already-normalized evidence text and
verified dossier sections; this module performs no network or model work.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from html import escape as html_escape
from urllib.parse import quote

from .types import Evidence


DISCLAIMER = (
    "Educational simulation based only on the public record and limited to "
    "investment philosophy and methodology. It may contain errors, and is not "
    "affiliated with or endorsed by the person represented."
)

LOW_SOURCE_WARNING = (
    "**LOW-SOURCE WARNING:** This profile rests on a thin independent-source "
    "base. Treat every characterization as provisional and consult the cited "
    "public record directly."
)

DOSSIER_SECTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "philosophy",
        "Philosophy",
        "Distill enduring investment principles and distinguish direct statements from interpretation.",
    ),
    (
        "methodology",
        "Methodology & decision process",
        "Describe repeatable analysis, valuation, portfolio, and decision habits.",
    ),
    (
        "risk",
        "Risk discipline & red flags",
        "Describe downside discipline, disqualifiers, uncertainty, and known limitations.",
    ),
    (
        "track_record",
        "Track record highlights (public)",
        "Use only documented public positions or records; avoid causal performance claims.",
    ),
    (
        "voice",
        "Voice",
        "Characterize public investment communication and use only excerpt-verbatim quotations.",
    ),
)


def _markdown_source_label(value: str) -> str:
    visible = "".join(
        (
            " "
            if unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            else character
        )
        for character in str(value)
    )
    visible = " ".join(visible.split())
    visible = html_escape(visible, quote=False)
    return (
        visible.replace("\\", r"\\")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace("(", r"\(")
        .replace(")", r"\)")
    )


def _markdown_destination(value: str) -> str:
    # Angle destinations have an unambiguous closing delimiter. Percent-encode
    # that delimiter, backslashes, controls, whitespace, and non-ASCII bytes;
    # retain normal RFC 3986 URL syntax and existing percent escapes.
    return quote(
        str(value).strip(),
        safe="/:?#[]@!$&'()*+,;=%~-._",
    )


def render_dossier_prompt(
    investor_name: str,
    section_title: str,
    instructions: str,
    evidence_ledger: str,
) -> str:
    """Render one evidence-confined dossier-section synthesis prompt."""
    return (
        f"Write the {section_title!r} section of a public-record dossier about "
        f"{investor_name}. {instructions}\n\n"
        "Use ONLY the numbered evidence below. Every prose paragraph must end "
        "with one or more citations like [1]. Quote only text appearing "
        "verbatim in an evidence excerpt. If the evidence is thin, write 'the "
        "public record does not establish ...' instead of guessing. Return "
        "prose only, without a heading or sources list.\n\n"
        + evidence_ledger
    )


def render_persona_prompt(
    investor_name: str,
    sections: Mapping[str, tuple[str, ...]],
    evidence_ledger: str,
) -> str:
    """Render the schema-oriented persona synthesis prompt."""
    dossier = "\n\n".join(
        f"## {key}\n" + "\n\n".join(paragraphs)
        for key, paragraphs in sections.items()
    )
    return (
        f"Create a TinyTroupe TinyPerson JSON object for an educational public-"
        f"record simulation of {investor_name}. Use ONLY the verified-style "
        "dossier and numbered ledger below. Do not include age, family, health, "
        "residence, or other private-life material. Keep beliefs to at most 15; "
        "include style, personality.traits, behaviors.general, skills, "
        "preferences interests/likes/dislikes, and public other_facts. Include "
        "the complete tinyic schema_version 1 block. Famous quotes must copy an "
        "excerpt verbatim and use a 1-based source index.\n\n"
        f"DOSSIER\n{dossier}\n\nLEDGER\n{evidence_ledger}"
    )


def render_dossier(
    *,
    investor_name: str,
    epithet: str,
    sections: Mapping[str, tuple[str, ...]],
    evidence: Sequence[Evidence],
    quality: str,
    model_ref: str,
    generated_date: str,
    search_calls: int,
    cost_usd: float | None,
) -> str:
    """Render the final cited Markdown dossier from verified content."""
    title = f"# {investor_name} — {epithet}\n\n"
    output = [title, f"> **Disclaimer:** {DISCLAIMER}\n"]
    if quality == "thin":
        output.append(f"\n> {LOW_SOURCE_WARNING}\n")
    section_titles = {key: title for key, title, _ in DOSSIER_SECTIONS}
    for key, paragraphs in sections.items():
        output.append(f"\n## {section_titles[key]}\n\n")
        output.append("\n\n".join(paragraphs))
        output.append("\n")
    output.append("\n## Sources\n\n")
    for number, item in enumerate(evidence, 1):
        accessed = item.accessed or generated_date
        output.append(
            f"{number}. [{_markdown_source_label(item.title)}]"
            f"(<{_markdown_destination(item.url)}>) — {item.source_type}; "
            f"accessed {accessed}\n"
        )
    cost = (
        "unavailable (subscription or unpriced lane)"
        if cost_usd is None
        else f"${cost_usd:.6f}"
    )
    output.append(
        "\n---\n"
        f"Generated by TinyIC with `{model_ref}` on {generated_date}. "
        f"Search calls: {search_calls}. Sources: {len(evidence)}. Cost: {cost}.\n"
    )
    return "".join(output)


__all__ = [
    "DISCLAIMER",
    "DOSSIER_SECTIONS",
    "LOW_SOURCE_WARNING",
    "render_dossier",
    "render_dossier_prompt",
    "render_persona_prompt",
]
