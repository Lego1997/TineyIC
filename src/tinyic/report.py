"""Static report rendering (self-contained HTML + Markdown) from an event log.

``tinyic export <id|path> --html|--md`` — a shareable static rendering of any
recorded debate (FR-3.2 / FR-6.1). Both renderers are **pure functions over the
parsed event stream** (the same :class:`~tinyic.tui.events.Event` objects the TUI
and ``tinyic result`` consume), so an export renders the *same* debate the Town
Hall does, from the same log, with zero engine imports. This is the load-bearing
rule from the PRD made concrete: the HTML export is a cheap variant of one
product, not a second renderer.

Two reducers, both already tested, do the parsing so this module only lays out
markup:

* :class:`~tinyic.tui.state.TownHallState` folds events into the transcript
  (phase banners, turns, steering notes, data/error cards) and the live committee
  snapshot — exactly what the TUI draws.
* :func:`~tinyic.result.assemble_result` yields the structured tail (scorecard,
  memo, disagreements + collapse metrics, usage rollup) — the ``tinyic result``
  document.

Safety: the event log is secret-free by construction (schema §Envelope forbids
keys/tokens in any payload). The HTML renderer additionally escapes *every*
dynamic value (:func:`html.escape`), so even a hostile payload can only ever
become inert text — a report can contain no external asset, script, or live
markup. The Markdown bundle reuses the models' own ``to_markdown`` where a
complete section exists, falling back to direct rendering for partial logs.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .result import assemble_result, resolve_run_path
from .tui.events import read_events
from .tui.state import TownHallState, humanize_duration

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping, Sequence

    from .tui.events import Event

__all__ = [
    "render_html",
    "render_markdown",
    "load_and_render",
]

# --------------------------------------------------------------------------- #
# Presentation constants (mirror the Town Hall renderer's palette / labels).
# --------------------------------------------------------------------------- #

# The six canonical committee hues, mirrored from ``tinyic.tui.widgets`` so the
# HTML colorizes a persona identically to the TUI. Kept here (not imported) so
# the export never pulls Textual for a color lookup; the set is a stable product
# fact. Unknown personas fall through the same rotation.
_PERSONA_COLORS: dict[str, str] = {
    "Warren Buffett": "#4fc3f7",
    "Charlie Munger": "#ba68c8",
    "Benjamin Graham": "#4db6ac",
    "Peter Lynch": "#ffb74d",
    "Howard Marks": "#e57373",
    "Li Lu": "#aed581",
}
_FALLBACK_COLORS = ("#4fc3f7", "#ba68c8", "#4db6ac", "#ffb74d", "#e57373", "#aed581")

_PHASE_LABELS: dict[str, str] = {
    "opening": "Opening Statements",
    "cross_exam": "Cross-Examination",
    "rebuttal": "Rebuttal",
    "verdict": "Final Verdict",
}

_MEMO_TITLES: dict[str, str] = {
    "executive_summary": "Executive Summary",
    "investment_thesis": "Investment Thesis",
    "key_risks": "Key Risks",
    "valuation_discussion": "Valuation Discussion",
    "final_verdict": "Final Verdict",
}

_VOTE_CLASS = {"BUY": "buy", "SELL": "sell", "HOLD": "hold"}
_STANCE_CLASS = {"bullish": "buy", "bearish": "sell", "neutral": "hold"}

# Artifact cards the structured sections below own; skipped in the inline
# transcript so scorecard/memo/disagreements are never rendered twice.
_DEDICATED_ARTIFACT_KINDS = frozenset({"scorecard", "memo_section", "disagreement"})

_STATUS_LABEL = {
    "complete": "Complete",
    "partial": "Partial (some phases failed)",
    "error": "Failed at setup",
    "incomplete": "Incomplete (log truncated)",
}


def _persona_color(name: str) -> str:
    """A stable display color for a persona (mirrors ``widgets.persona_color``)."""
    if name in _PERSONA_COLORS:
        return _PERSONA_COLORS[name]
    import hashlib

    digest = int(hashlib.sha1((name or "").encode("utf-8")).hexdigest(), 16)
    return _FALLBACK_COLORS[digest % len(_FALLBACK_COLORS)]


def _fmt_tokens(n: object) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_cost(cost: object) -> str:
    if isinstance(cost, (int, float)):
        return f"${cost:.4f}"
    return "n/a (subscription)"


def _title_line(document: "Mapping[str, object]") -> str:
    ticker = str(document.get("ticker") or "").strip()
    company = str(document.get("company_name") or "").strip()
    if ticker and company:
        return f"{ticker} · {company}"
    return ticker or company or str(document.get("debate_id") or "TinyIC debate")


# ==========================================================================
# HTML
# ==========================================================================

def render_html(events: "Sequence[Event]") -> str:
    """Render a recorded debate as one self-contained static HTML page.

    Everything — CSS, layout, content — is inlined; the page references no
    external asset, so it opens and shares as a single file. Every dynamic value
    is HTML-escaped.
    """
    events = list(events)
    state = TownHallState()
    for event in events:
        state.dispatch(event)
    document = assemble_result(events)

    title = _title_line(document)
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape('TinyIC · ' + title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        _html_header(state, document),
        '<main class="th-main">',
        _html_transcript(state),
        _html_committee(state),
        "</main>",
        _html_scorecard(document),
        _html_memo(document),
        _html_disagreements(document),
        _html_usage(document),
        _html_footer(document),
        "</body>",
        "</html>",
    ]
    return "\n".join(p for p in parts if p) + "\n"


def _html_header(state: TownHallState, document: "Mapping[str, object]") -> str:
    status = str(document.get("status") or "incomplete")
    status_label = _STATUS_LABEL.get(status, status)
    meta: list[str] = []
    if state.preset:
        meta.append(f"preset <b>{html.escape(state.preset)}</b>")
    meta.append(f"status <b class=\"status-{html.escape(status)}\">{html.escape(status_label)}</b>")
    duration = document.get("duration_s")
    if isinstance(duration, (int, float)) and duration > 0:
        meta.append(f"duration <b>{html.escape(humanize_duration(float(duration)))}</b>")
    phases = document.get("phases_completed") or []
    if isinstance(phases, list) and phases:
        meta.append(f"phases <b>{len(phases)}/4</b>")

    total = (document.get("usage") or {}).get("total") or {}  # type: ignore[union-attr]
    usage_bits: list[str] = []
    if total:
        usage_bits.append(
            f"{_fmt_tokens(total.get('input_tokens'))} in · "
            f"{_fmt_tokens(total.get('output_tokens'))} out"
        )
        usage_bits.append(_fmt_cost(total.get("cost_usd")))

    return (
        '<header class="th-header">'
        f"<h1>{html.escape(_title_line(document))}</h1>"
        f'<div class="th-meta">{" · ".join(meta)}</div>'
        + (
            f'<div class="th-usage-chip">{" · ".join(html.escape(b) for b in usage_bits)}</div>'
            if usage_bits
            else ""
        )
        + "</header>"
    )


def _html_transcript(state: TownHallState) -> str:
    rows: list[str] = ['<section class="th-transcript" aria-label="Transcript">']
    for item in state.transcript:
        kind = getattr(item, "kind", "")
        if kind == "phase":
            rows.append(_html_phase(item))
        elif kind == "turn":
            rows.append(_html_turn(item))
        elif kind == "steering":
            rows.append(_html_steering(item))
        elif kind in _DEDICATED_ARTIFACT_KINDS:
            continue  # rendered in its own section below
        else:  # data_ready, debate_error, and any forward-compatible artifact
            rows.append(_html_artifact(item))
    rows.append("</section>")
    return "\n".join(rows)


def _html_phase(phase) -> str:
    label = _PHASE_LABELS.get(phase.phase, (phase.phase or "phase").replace("_", " ").title())
    cls = "th-phase completed" if phase.completed else "th-phase"
    tail = ""
    if phase.da_persona:
        tail += f' <span class="th-phase-da">devil\'s advocate: {html.escape(str(phase.da_persona))}</span>'
    if phase.turn_count is not None:
        tail += f' <span class="th-phase-turns">{html.escape(str(phase.turn_count))} turns</span>'
    return f'<h2 class="{cls}"><span class="th-phase-num">{phase.index + 1}</span>{html.escape(label)}{tail}</h2>'


def _html_turn(turn) -> str:
    color = _persona_color(turn.persona)
    classes = ["th-turn"]
    if turn.interrupted:
        classes.append("interrupted")
    head_bits: list[str] = [
        '<span class="th-dot"></span>',
        f'<span class="th-name">{html.escape(turn.persona or "?")}</span>',
        f'<span class="th-badge">{html.escape(turn.phase or "?")} · {html.escape(turn.role or "?")}</span>',
    ]
    if turn.stance:
        scls = _VOTE_CLASS.get(turn.stance) or _STANCE_CLASS.get(turn.stance) or "hold"
        head_bits.append(f'<span class="th-tag th-{scls}">{html.escape(turn.stance)}</span>')
    if turn.target_persona:
        head_bits.append(f'<span class="th-target">→ {html.escape(str(turn.target_persona))}</span>')
    if turn.interrupted:
        suffix = " (esc)" if turn.interrupted_by == "user" else (
            f" ({turn.interrupted_by})" if turn.interrupted_by else ""
        )
        disp = f" · {turn.interrupt_disposition}" if turn.interrupt_disposition else ""
        head_bits.append(
            f'<span class="th-interrupt">⚡ interrupted{html.escape(suffix + disp)}</span>'
        )

    speech = html.escape(turn.speech) if turn.speech else '<span class="th-dim">(no speech)</span>'
    body = [
        f'<article class="{" ".join(classes)}" style="--accent:{color}">',
        f'<div class="th-turn-head">{"".join(head_bits)}</div>',
        f'<div class="th-speech">{speech}</div>',
    ]
    if turn.thinking:
        body.append(
            '<details class="th-think"><summary>thinking</summary>'
            f'<div class="th-think-body">{html.escape(turn.thinking)}</div></details>'
        )
    body.append("</article>")
    return "\n".join(body)


def _html_steering(steer) -> str:
    status_cls = {"delivered": "delivered", "dropped": "dropped"}.get(steer.status, "queued")
    bits = [f'<span class="th-steer-mode">✎ {html.escape((steer.mode or "steer").upper())}</span>']
    if steer.target_persona:
        bits.append(f'<span class="th-steer-target">@{html.escape(str(steer.target_persona))}</span>')
    if steer.source:
        bits.append(f'<span class="th-dim">({html.escape(steer.source)})</span>')
    tail = ""
    if steer.status == "delivered":
        before = f" before {html.escape(str(steer.delivered_before_turn_id))}" if steer.delivered_before_turn_id else ""
        tail = f'<div class="th-steer-tail delivered">→ delivered{before}</div>'
    elif steer.status == "dropped":
        tail = f'<div class="th-steer-tail dropped">✗ dropped — {html.escape(steer.reason or "")}</div>'
    else:
        tail = '<div class="th-steer-tail queued">queued…</div>'
    return (
        f'<div class="th-steer {status_cls}">'
        f'<div class="th-steer-head">{" ".join(bits)}</div>'
        f'<div class="th-steer-text">{html.escape(steer.text or "")}</div>'
        f"{tail}</div>"
    )


def _html_artifact(item) -> str:
    kind = getattr(item, "kind", "artifact")
    if kind == "data_ready":
        payload = getattr(item, "payload", {}) or {}
        sources = payload.get("sources") or []
        chips = "".join(
            f'<span class="th-source th-src-{html.escape(str(s.get("status", "?")))}">'
            f'{html.escape(str(s.get("name", "?")))}: {html.escape(str(s.get("status", "?")))}</span>'
            for s in sources
            if isinstance(s, dict)
        )
        desc = html.escape(str(payload.get("description") or ""))
        return (
            f'<div class="th-card th-data"><div class="th-card-title">{html.escape(item.title)}</div>'
            + (f'<div class="th-data-desc">{desc}</div>' if desc else "")
            + f'<div class="th-sources">{chips}</div></div>'
        )
    cls = "th-card th-error" if kind == "debate_error" else "th-card"
    return (
        f'<div class="{cls}"><div class="th-card-title">{html.escape(getattr(item, "title", kind))}</div>'
        f'<div class="th-card-body">{html.escape(getattr(item, "body", "") or "")}</div></div>'
    )


def _html_committee(state: TownHallState) -> str:
    if not state.personas:
        return '<aside class="th-committee"><h2>Committee</h2></aside>'
    cards: list[str] = ['<aside class="th-committee"><h2>Committee</h2>']
    for member in state.personas.values():
        color = _persona_color(member.name)
        meta = " · ".join(
            html.escape(x)
            for x in (member.model_ref, member.thinking_level, member.temperament)
            if x
        )
        verdict: list[str] = []
        if member.stance:
            scls = _VOTE_CLASS.get(member.stance) or _STANCE_CLASS.get(member.stance) or "hold"
            verdict.append(f'<span class="th-tag th-{scls}">{html.escape(member.stance)}</span>')
        if member.vote:
            vcls = _VOTE_CLASS.get(member.vote, "hold")
            conf = f' <span class="th-dim">{html.escape(member.confidence)}</span>' if member.confidence else ""
            verdict.append(f'<span class="th-tag th-{vcls}">{html.escape(member.vote)}</span>{conf}')
        if member.caved:
            verdict.append('<span class="th-caved">⚠ caved</span>')
        cards.append(
            f'<div class="th-member" style="--accent:{color}">'
            f'<div class="th-member-head"><span class="th-dot"></span>'
            f'<span class="th-name">{html.escape(member.name)}</span></div>'
            + (f'<div class="th-member-meta">{meta}</div>' if meta else "")
            + (f'<div class="th-member-verdict">{" ".join(verdict)}</div>' if verdict else "")
            + "</div>"
        )
    cards.append("</aside>")
    return "\n".join(cards)


def _html_scorecard(document: "Mapping[str, object]") -> str:
    scorecard = document.get("scorecard")
    votes = document.get("votes") or []
    if not isinstance(scorecard, dict) and not votes:
        return ""
    scorecard = scorecard if isinstance(scorecard, dict) else {}
    consensus = scorecard.get("consensus") or "No consensus"
    ccls = _VOTE_CLASS.get(str(consensus), "hold")
    summary = (
        f'<span class="th-tag th-{ccls} th-consensus">{html.escape(str(consensus))}</span>'
        f'<span class="th-counts">BUY {scorecard.get("bull_count", 0)} · '
        f'HOLD {scorecard.get("hold_count", 0)} · SELL {scorecard.get("bear_count", 0)}</span>'
    )
    rows = "".join(
        "<tr>"
        f'<td class="th-vcell"><span class="th-dot" style="--accent:{_persona_color(str(v.get("persona", "")))}"></span>'
        f'{html.escape(str(v.get("persona", "?")))}</td>'
        f'<td><span class="th-tag th-{_VOTE_CLASS.get(str(v.get("vote", "")), "hold")}">{html.escape(str(v.get("vote", "?")))}</span></td>'
        f'<td>{html.escape(str(v.get("confidence", "?")))}</td>'
        f'<td>{html.escape("; ".join(str(r) for r in (v.get("reasoning") or [])[:3]) or "—")}</td>'
        f'<td>{html.escape("; ".join(str(r) for r in (v.get("key_risks") or [])[:2]) or "—")}</td>'
        f'<td>{"yes" if v.get("changed_mind") else "—"}</td>'
        "</tr>"
        for v in votes
    )
    return (
        '<section class="th-section th-scorecard"><h2>Scorecard</h2>'
        f'<div class="th-scorecard-summary">{summary}</div>'
        '<div class="th-tablewrap"><table><thead><tr>'
        "<th>Investor</th><th>Vote</th><th>Confidence</th><th>Key reasoning</th>"
        "<th>Key risks</th><th>Changed mind</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _html_memo(document: "Mapping[str, object]") -> str:
    memo = document.get("memo") or {}
    if not isinstance(memo, dict) or not memo:
        return ""
    blocks: list[str] = ['<section class="th-section th-memo"><h2>Investment Memo</h2>']
    for key, title in _MEMO_TITLES.items():
        section = memo.get(key)
        if not isinstance(section, dict):
            continue
        content = html.escape(str(section.get("content") or ""))
        contributors = section.get("contributing_personas") or []
        data = section.get("supporting_data") or []
        foot = []
        if contributors:
            foot.append(f'Contributors: {html.escape(", ".join(str(c) for c in contributors))}')
        if data:
            foot.append(f'Data: {html.escape(", ".join(str(d) for d in data))}')
        blocks.append(
            f'<article class="th-memo-section"><h3>{html.escape(title)}</h3>'
            f'<div class="th-memo-body">{content}</div>'
            + (f'<div class="th-memo-foot">{" · ".join(foot)}</div>' if foot else "")
            + "</article>"
        )
    blocks.append("</section>")
    return "\n".join(blocks)


def _html_disagreements(document: "Mapping[str, object]") -> str:
    disagreements = document.get("disagreements") or []
    collapse = document.get("collapse_metrics") or []
    if not disagreements and not collapse:
        return ""
    blocks: list[str] = ['<section class="th-section th-disagreements"><h2>Disagreement Analysis</h2>']
    for d in disagreements:
        if not isinstance(d, dict):
            continue
        sides = "".join(
            f'<div class="th-side"><b>{html.escape(str(s.get("persona", "?")))}:</b> '
            f'{html.escape(str(s.get("position", "")))}'
            + (
                f'<blockquote>“{html.escape(str(s.get("evidence_quote", "")))}”</blockquote>'
                if s.get("evidence_quote")
                else ""
            )
            + "</div>"
            for s in (d.get("sides") or [])
            if isinstance(s, dict)
        )
        resolution = (
            f'<div class="th-resolution"><b>Resolution:</b> {html.escape(str(d.get("resolution")))}</div>'
            if d.get("resolution")
            else ""
        )
        blocks.append(
            f'<article class="th-disagreement"><h3>{html.escape(str(d.get("dimension", "?")))}</h3>'
            f'<p>{html.escape(str(d.get("description", "")))}</p>{sides}{resolution}</article>'
        )
    blocks.append(_html_collapse(collapse))
    blocks.append("</section>")
    return "\n".join(b for b in blocks if b)


def _html_collapse(collapse: "Sequence[Mapping[str, object]]") -> str:
    metrics = [m for m in collapse if isinstance(m, dict)]
    if not metrics:
        return ""
    caved = [str(m.get("persona", "?")) for m in metrics if m.get("caved")]
    rate = round(100 * len(caved) / len(metrics), 1) if metrics else 0.0
    rows = "".join(
        "<tr>"
        f'<td>{html.escape(str(m.get("persona", "?")))}</td>'
        f'<td>{html.escape(str(m.get("stance_before", "?")))} → {html.escape(str(m.get("stance_after", "?")))}</td>'
        f'<td>{"<span class=\"th-caved\">⚠ CAVED</span>" if m.get("caved") else "held"}</td>'
        f'<td>{html.escape(str(m.get("note", "")))}</td>'
        "</tr>"
        for m in metrics
    )
    caved_line = (
        f'<div class="th-collapse-caved">Caved under pressure: {html.escape(", ".join(caved))}</div>'
        if caved
        else '<div class="th-collapse-caved held">No collapse: every assessed member held.</div>'
    )
    return (
        '<div class="th-collapse"><h3>Disagreement Collapse (DCR)</h3>'
        f'<div class="th-collapse-rate">Collapse rate <b>{rate}%</b> '
        f'({len(caved)} of {len(metrics)} assessed members caved)</div>'
        f"{caved_line}"
        '<div class="th-tablewrap"><table><thead><tr>'
        "<th>Member</th><th>Opening → Verdict</th><th>Outcome</th><th>Note</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div></div>"
    )


def _html_usage(document: "Mapping[str, object]") -> str:
    usage = document.get("usage")
    if not isinstance(usage, dict):
        return ""
    total = usage.get("total") or {}
    by_model = usage.get("by_model") or {}
    by_purpose = usage.get("by_purpose") or {}
    if not total and not by_model and not by_purpose:
        return ""

    def _bucket_rows(buckets: "Mapping[str, Mapping[str, object]]") -> str:
        return "".join(
            "<tr>"
            f"<td>{html.escape(str(name))}</td>"
            f'<td>{_fmt_tokens(b.get("input_tokens"))}</td>'
            f'<td>{_fmt_tokens(b.get("output_tokens"))}</td>'
            f'<td>{_fmt_tokens(b.get("cached_tokens"))}</td>'
            f'<td>{_fmt_cost(b.get("cost_usd"))}</td>'
            "</tr>"
            for name, b in buckets.items()
            if isinstance(b, dict)
        )

    total_line = (
        f'<div class="th-usage-total">Total: <b>{_fmt_tokens(total.get("input_tokens"))}</b> in · '
        f'<b>{_fmt_tokens(total.get("output_tokens"))}</b> out · '
        f'<b>{_fmt_tokens(total.get("cached_tokens"))}</b> cached · cost <b>{_fmt_cost(total.get("cost_usd"))}</b></div>'
    )
    tables = ""
    if by_model:
        tables += (
            '<h3>By model</h3><div class="th-tablewrap"><table><thead><tr>'
            "<th>Model</th><th>Input</th><th>Output</th><th>Cached</th><th>Cost</th></tr></thead>"
            f"<tbody>{_bucket_rows(by_model)}</tbody></table></div>"
        )
    if by_purpose:
        tables += (
            '<h3>By purpose</h3><div class="th-tablewrap"><table><thead><tr>'
            "<th>Purpose</th><th>Input</th><th>Output</th><th>Cached</th><th>Cost</th></tr></thead>"
            f"<tbody>{_bucket_rows(by_purpose)}</tbody></table></div>"
        )
    return f'<section class="th-section th-usage"><h2>Usage &amp; Cost</h2>{total_line}{tables}</section>'


def _html_footer(document: "Mapping[str, object]") -> str:
    debate_id = html.escape(str(document.get("debate_id") or ""))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        '<footer class="th-footer">'
        f"Generated by TinyIC · debate {debate_id} · event-schema v1 · {generated}<br>"
        "For research and entertainment only — not investment advice."
        "</footer>"
    )


# The whole stylesheet, inlined. Dark "town hall" palette with per-persona accent
# borders; a two-column transcript/committee grid that collapses on narrow
# screens. No external font, image, or ``url()`` — the page is one file.
_CSS = """
:root{
  --bg:#0f1115; --panel:#171a21; --panel2:#1e222b; --line:#2a2f3a;
  --text:#e6e8ee; --muted:#9aa3b2; --amber:#ffb74d;
  --buy:#66bb6a; --sell:#ef5350; --hold:#ffca28;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  line-height:1.55;font-size:15px}
h1,h2,h3{line-height:1.25;margin:0}
b{color:var(--text)}
a{color:var(--amber)}
.th-header{padding:20px 24px;background:linear-gradient(180deg,#181c24,#12141a);
  border-bottom:2px solid var(--amber);position:sticky;top:0;z-index:2}
.th-header h1{font-size:22px;letter-spacing:.2px}
.th-meta,.th-usage-chip{color:var(--muted);font-size:13px;margin-top:6px}
.th-usage-chip{margin-top:2px}
.th-meta .status-complete{color:var(--buy)}
.th-meta .status-partial{color:var(--hold)}
.th-meta .status-error,.th-meta .status-incomplete{color:var(--sell)}
.th-main{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:24px;
  max-width:1200px;margin:0 auto;padding:24px}
.th-transcript{min-width:0}
.th-phase{margin:26px 0 6px;color:var(--amber);font-size:16px;
  display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  border-bottom:1px solid var(--line);padding-bottom:6px}
.th-phase.completed{color:var(--buy)}
.th-phase-num{display:inline-flex;align-items:center;justify-content:center;
  width:22px;height:22px;border-radius:50%;background:var(--panel2);
  color:var(--muted);font-size:12px;font-weight:700}
.th-phase-da,.th-phase-turns{font-size:12px;color:var(--muted);font-weight:400}
.th-turn{background:var(--panel);border:1px solid var(--line);
  border-left:4px solid var(--accent,#888);border-radius:8px;
  padding:12px 14px;margin:12px 0}
.th-turn.interrupted{opacity:.85}
.th-turn-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;
  font-weight:600;margin-bottom:8px}
.th-dot{width:10px;height:10px;border-radius:50%;background:var(--accent,#888);
  display:inline-block;flex:none}
.th-name{color:var(--accent,var(--text))}
.th-badge{font-size:12px;color:var(--muted);font-weight:400}
.th-target{font-size:12px;color:var(--muted);font-style:italic}
.th-tag{font-size:12px;font-weight:700;padding:1px 7px;border-radius:999px;
  background:var(--panel2);border:1px solid var(--line)}
.th-buy{color:var(--buy);border-color:#2e5b33} .th-sell{color:var(--sell);border-color:#5b2e2e}
.th-hold{color:var(--hold);border-color:#5b512e}
.th-interrupt{color:var(--sell);font-size:12px;font-weight:700}
.th-speech{white-space:pre-wrap;word-wrap:break-word}
.th-dim{color:var(--muted);font-style:italic}
.th-think{margin-top:10px;border-top:1px dashed var(--line);padding-top:8px}
.th-think summary{cursor:pointer;color:var(--muted);font-size:13px;font-style:italic}
.th-think summary:hover{color:var(--amber)}
.th-think-body{white-space:pre-wrap;color:var(--muted);font-style:italic;
  margin-top:8px;padding-left:10px;border-left:2px solid var(--line)}
.th-steer{background:var(--panel2);border:1px solid var(--hold);border-radius:8px;
  padding:8px 12px;margin:12px 0;font-size:14px}
.th-steer.delivered{border-color:var(--buy)} .th-steer.dropped{border-color:var(--sell)}
.th-steer-mode{font-weight:700;color:var(--hold)}
.th-steer-target{font-weight:700}
.th-steer-text{font-style:italic;margin:4px 0}
.th-steer-tail{font-size:12px}
.th-steer-tail.delivered{color:var(--buy)} .th-steer-tail.dropped{color:var(--sell)}
.th-steer-tail.queued{color:var(--hold)}
.th-card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:10px 14px;margin:12px 0}
.th-card.th-error{border-color:var(--sell)}
.th-card-title{font-weight:700;margin-bottom:4px}
.th-card-body,.th-data-desc{color:var(--muted);white-space:pre-wrap}
.th-sources{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.th-source{font-size:12px;padding:1px 7px;border-radius:999px;background:var(--panel2);
  border:1px solid var(--line);color:var(--muted)}
.th-src-ok{color:var(--buy);border-color:#2e5b33}
.th-src-degraded{color:var(--hold)} .th-src-unavailable,.th-src-disabled_no_credential{color:var(--muted)}
.th-committee{align-self:start;position:sticky;top:96px}
.th-committee h2,.th-section h2{font-size:16px;color:var(--amber);margin-bottom:10px}
.th-member{background:var(--panel);border:1px solid var(--line);
  border-left:4px solid var(--accent,#888);border-radius:8px;padding:10px 12px;margin-bottom:10px}
.th-member-head{display:flex;align-items:center;gap:8px;font-weight:700}
.th-member-meta{font-size:12px;color:var(--muted);margin-top:4px}
.th-member-verdict{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px}
.th-caved{color:var(--sell);font-weight:700;font-size:12px}
.th-section{max-width:1200px;margin:0 auto;padding:8px 24px 24px}
.th-scorecard-summary{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.th-consensus{font-size:14px;padding:2px 10px}
.th-counts{color:var(--muted)}
.th-tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:520px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:var(--panel2);color:var(--muted);font-weight:600;position:sticky;top:0}
tbody tr:last-child td{border-bottom:none}
.th-vcell{display:flex;align-items:center;gap:8px;font-weight:600;white-space:nowrap}
.th-memo-section{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:14px 16px;margin-bottom:12px}
.th-memo-section h3{color:var(--text);font-size:15px;margin-bottom:8px}
.th-memo-body{white-space:pre-wrap}
.th-memo-foot{color:var(--muted);font-size:12px;margin-top:8px;font-style:italic}
.th-disagreement{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:14px 16px;margin-bottom:12px}
.th-disagreement h3{color:var(--text);font-size:15px;margin-bottom:6px}
.th-side{margin:8px 0}
.th-side blockquote{margin:4px 0 0;padding-left:10px;border-left:2px solid var(--amber);
  color:var(--muted);font-style:italic}
.th-resolution{margin-top:8px;color:var(--muted)}
.th-collapse{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:14px 16px;margin-top:12px}
.th-collapse h3{color:var(--text);font-size:15px;margin-bottom:8px}
.th-collapse-rate{margin-bottom:4px}
.th-collapse-caved{color:var(--sell);font-weight:600;margin-bottom:10px}
.th-collapse-caved.held{color:var(--buy)}
.th-usage-total{margin-bottom:12px}
.th-usage h3{font-size:14px;color:var(--muted);margin:14px 0 6px}
.th-footer{max-width:1200px;margin:0 auto;padding:24px;color:var(--muted);
  font-size:12px;border-top:1px solid var(--line);text-align:center}
@media (max-width:860px){
  .th-main{grid-template-columns:1fr}
  .th-committee{position:static}
}
"""


# ==========================================================================
# Markdown bundle
# ==========================================================================

def render_markdown(events: "Sequence[Event]") -> str:
    """Render the Markdown bundle: scorecard, memo, transcript, disagreements.

    Reuses the debate models' own ``to_markdown`` for any complete section (the
    same output the removed Streamlit downloads produced), falling back to direct
    rendering for a partial / truncated log.
    """
    events = list(events)
    state = TownHallState()
    for event in events:
        state.dispatch(event)
    document = assemble_result(events)

    title = _title_line(document)
    status = str(document.get("status") or "incomplete")
    header = [
        f"# TinyIC — {title}",
        "",
        f"*Status: {_STATUS_LABEL.get(status, status)}"
        + (f" · preset {document.get('preset')}" if document.get("preset") else "")
        + (
            f" · duration {humanize_duration(float(document['duration_s']))}"
            if isinstance(document.get("duration_s"), (int, float)) and document["duration_s"]
            else ""
        )
        + "*",
        "",
    ]
    sections: list[str] = ["\n".join(header)]

    scorecard_md = _markdown_scorecard(document)
    if scorecard_md:
        sections.append(scorecard_md)
    memo_md = _markdown_memo(document)
    if memo_md:
        sections.append(memo_md)
    sections.append(_markdown_transcript(state))
    disagreement_md = _markdown_disagreements(document)
    if disagreement_md:
        sections.append(disagreement_md)

    return "\n\n---\n\n".join(s.strip() for s in sections if s.strip()) + "\n"


def _markdown_scorecard(document: "Mapping[str, object]") -> str:
    model = _scorecard_model(document)
    if model is not None:
        return model.to_markdown()
    # Fallback: a bare consensus line when no full scorecard was recorded.
    scorecard = document.get("scorecard")
    if not isinstance(scorecard, dict):
        return ""
    return (
        f"## Investment Scorecard\n\n**Consensus:** {scorecard.get('consensus', 'No consensus')} "
        f"| Bulls: {scorecard.get('bull_count', 0)} | Bears: {scorecard.get('bear_count', 0)} "
        f"| Hold: {scorecard.get('hold_count', 0)}"
    )


def _markdown_memo(document: "Mapping[str, object]") -> str:
    model = _memo_model(document)
    if model is not None:
        return model.to_markdown()
    # Fallback: render whatever memo sections a partial log captured.
    memo = document.get("memo") or {}
    if not isinstance(memo, dict) or not memo:
        return ""
    lines = [f"# Investment Memo: {_title_line(document)}", ""]
    for key, title in _MEMO_TITLES.items():
        section = memo.get(key)
        if not isinstance(section, dict):
            continue
        lines += [f"## {title}", "", str(section.get("content") or ""), ""]
    return "\n".join(lines)


def _markdown_transcript(state: TownHallState) -> str:
    lines = ["## Transcript", ""]
    for item in state.transcript:
        kind = getattr(item, "kind", "")
        if kind == "phase":
            label = _PHASE_LABELS.get(item.phase, (item.phase or "phase").replace("_", " ").title())
            lines += ["", f"### {label}", ""]
        elif kind == "turn":
            stance = f" [{item.stance}]" if item.stance else ""
            flag = " ⚡ interrupted" if item.interrupted else ""
            lines.append(f"**{item.persona or '?'}** ⟨{item.phase or '?'} · {item.role or '?'}⟩{stance}{flag}")
            lines.append("")
            lines.append(item.speech.strip() if item.speech else "_(no speech)_")
            if item.thinking:
                lines += ["", f"> _thinking —_ {' '.join(item.thinking.split())}"]
            lines.append("")
        elif kind == "steering":
            mode = (item.mode or "steer").upper()
            target = f" @{item.target_persona}" if item.target_persona else ""
            lines += [f"> ✎ **{mode}**{target} — {item.text.strip()} _({item.status})_", ""]
    return "\n".join(lines).strip()


def _markdown_disagreements(document: "Mapping[str, object]") -> str:
    model = _disagreement_model(document)
    if model is not None:
        return model.to_markdown()
    return ""


# --------------------------------------------------------------------------- #
# Model reconstruction from the assembled document (reuse ``to_markdown``).
# Each guarded so an adversarial / partial log degrades to a fallback, never a
# crash: a validation failure simply yields ``None`` and the direct renderer runs.
# --------------------------------------------------------------------------- #

def _scorecard_model(document: "Mapping[str, object]"):
    scorecard = document.get("scorecard")
    if not isinstance(scorecard, dict):
        return None
    raw_votes = document.get("votes") or scorecard.get("votes") or []
    if not raw_votes:
        return None
    try:
        from .debate.models import Scorecard, Vote

        votes = [
            Vote(
                investor=str(v.get("persona", "") or ""),
                vote=str(v.get("vote", "HOLD") or "HOLD"),
                confidence=str(v.get("confidence", "MEDIUM") or "MEDIUM"),
                reasoning=list(v.get("reasoning") or []),
                key_risks=list(v.get("key_risks") or []),
                changed_mind=bool(v.get("changed_mind")),
                source=str(v.get("source", "extracted") or "extracted"),
            )
            for v in raw_votes
            if isinstance(v, dict)
        ]
        return Scorecard(
            ticker=str(document.get("ticker") or ""),
            company_name=str(document.get("company_name") or ""),
            votes=votes,
            consensus=scorecard.get("consensus"),
            bull_count=int(scorecard.get("bull_count") or 0),
            bear_count=int(scorecard.get("bear_count") or 0),
            hold_count=int(scorecard.get("hold_count") or 0),
        )
    except Exception:
        return None


def _memo_model(document: "Mapping[str, object]"):
    memo = document.get("memo")
    if not isinstance(memo, dict):
        return None
    if not all(key in memo and isinstance(memo[key], dict) for key in _MEMO_TITLES):
        return None  # InvestmentMemo requires all five sections; fall back otherwise
    try:
        from .debate.models import InvestmentMemo, MemoSection

        def _section(key: str) -> "MemoSection":
            data = memo[key]
            return MemoSection(
                title=_MEMO_TITLES[key],
                content=str(data.get("content") or ""),
                contributing_personas=list(data.get("contributing_personas") or []),
                supporting_data=list(data.get("supporting_data") or []),
            )

        return InvestmentMemo(
            ticker=str(document.get("ticker") or ""),
            company_name=str(document.get("company_name") or ""),
            executive_summary=_section("executive_summary"),
            investment_thesis=_section("investment_thesis"),
            key_risks=_section("key_risks"),
            valuation_discussion=_section("valuation_discussion"),
            final_verdict=_section("final_verdict"),
        )
    except Exception:
        return None


def _disagreement_model(document: "Mapping[str, object]"):
    disagreements = document.get("disagreements") or []
    collapse = _collapse_summary(document)
    if not disagreements and collapse is None:
        return None
    try:
        from .debate.models import Disagreement, DisagreementAnalysis

        items = [
            Disagreement(
                dimension=str(d.get("dimension", "") or ""),
                description=str(d.get("description", "") or ""),
                sides=list(d.get("sides") or []),
                resolution=str(d.get("resolution", "") or ""),
            )
            for d in disagreements
            if isinstance(d, dict)
        ]
        return DisagreementAnalysis(
            ticker=str(document.get("ticker") or ""),
            company_name=str(document.get("company_name") or ""),
            disagreements=items,
            collapse_summary=collapse,
        )
    except Exception:
        return None


def _collapse_summary(document: "Mapping[str, object]"):
    metrics = [m for m in (document.get("collapse_metrics") or []) if isinstance(m, dict)]
    if not metrics:
        return None
    try:
        from .debate.models import CollapseSummary, StanceShift

        shifts = [
            StanceShift(
                persona=str(m.get("persona", "") or ""),
                stance_before=str(m.get("stance_before", "") or ""),
                stance_after=str(m.get("stance_after", "") or ""),
                caved=bool(m.get("caved")),
                note=str(m.get("note", "") or ""),
            )
            for m in metrics
        ]
        caved = [s.persona for s in shifts if s.caved]
        after_counts: dict[str, int] = {}
        for shift in shifts:
            if shift.stance_after:
                after_counts[shift.stance_after] = after_counts.get(shift.stance_after, 0) + 1
        majority = max(after_counts, key=after_counts.get) if after_counts else None
        return CollapseSummary(
            disagreement_collapse_rate=len(caved) / len(shifts) if shifts else 0.0,
            assessed_count=len(shifts),
            caved_count=len(caved),
            caved_personas=caved,
            majority_stance=majority,
            shifts=shifts,
        )
    except Exception:
        return None


# ==========================================================================
# CLI entry
# ==========================================================================

def load_and_render(id_or_path: str, fmt: str) -> str:
    """Resolve a debate id/path, read its log, and render ``fmt`` (``html``/``md``).

    Raises ``FileNotFoundError`` when no matching run exists (the CLI maps that to
    the setup-error exit code, mirroring ``tinyic result``).
    """
    path = resolve_run_path(id_or_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"no debate run found for {id_or_path!r} (looked at {path})"
        )
    events = read_events(path)
    if fmt == "html":
        return render_html(events)
    return render_markdown(events)
