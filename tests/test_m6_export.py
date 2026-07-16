"""M6 Stage 2 acceptance: HTML + Markdown export (FR-3.2 / FR-6.1).

Both renderers are pure functions over a recorded event log, so these tests run
entirely offline from the **byte-pinned** M4 golden fixture (its sha256 is
asserted so the golden input can never drift silently) plus a few small,
inline-fixed synthetic logs for the escaping / interrupt / truncation edges.

Per the milestone brief the *output* is checked structurally (so the styling can
evolve without churning tests), never byte-pinned; only the event-log *inputs*
are frozen.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tinyic.cli import build_parser, main
from tinyic.report import load_and_render, render_html, render_markdown
from tinyic.tui.events import parse_event, read_events

FIXTURE = Path(__file__).parent / "fixtures" / "m4_full_debate.jsonl"
# Byte-pin the golden event-log input: if the fixture is regenerated this hash
# must be updated deliberately, which forces a re-review of the export goldens.
FIXTURE_SHA256 = "4f650b4bd720136058a8fe7047365d2ba3dce6a5421dedb511fde76378653100"

# The six committee members and the four phase labels the report must surface.
_PERSONAS = [
    "Warren Buffett",
    "Charlie Munger",
    "Benjamin Graham",
    "Peter Lynch",
    "Howard Marks",
    "Li Lu",
]
_PHASE_LABELS = ["Opening Statements", "Cross-Examination", "Rebuttal", "Final Verdict"]
_MEMO_TITLES = [
    "Executive Summary",
    "Investment Thesis",
    "Key Risks",
    "Valuation Discussion",
    "Final Verdict",
]

# Markup that would load an external asset — a self-contained report has none of
# it. (Escaped text such as ``&lt;script&gt;`` is inert and does not match.)
_EXTERNAL_ASSET_MARKERS = ["<script", "<link", "<img", "src=", "@import", "url("]


def _golden_events():
    return read_events(FIXTURE)


def _synthetic(pairs):
    """Build a frozen event log from ``(type, payload)`` pairs (seq = order)."""
    events = []
    for i, (etype, payload) in enumerate(pairs, start=1):
        line = json.dumps(
            {
                "v": 1,
                "seq": i,
                "ts": "2026-07-13T01:02:03.000Z",
                "debate_id": "tst-20260713-syn",
                "type": etype,
                "payload": payload,
            }
        )
        events.append(parse_event(line, line_index=i))
    return events


def _assert_self_contained(html: str) -> None:
    for marker in _EXTERNAL_ASSET_MARKERS:
        assert marker not in html, f"external-asset marker {marker!r} leaked into HTML"


# ==========================================================================
# The golden input is byte-pinned
# ==========================================================================


def test_golden_fixture_is_byte_pinned():
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert digest == FIXTURE_SHA256, (
        "the M4 golden event log changed; update FIXTURE_SHA256 and re-review the "
        "export structure assertions"
    )


# ==========================================================================
# HTML export — structure, self-containment, expandable thinking
# ==========================================================================


def test_html_export_is_a_self_contained_document():
    html = render_html(_golden_events())
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    # Exactly one inlined stylesheet, and no external asset of any kind.
    assert html.count("<style>") == 1 and html.count("</style>") == 1
    _assert_self_contained(html)
    # No absolute URL is fetched — the golden content carries no URLs either.
    assert "http://" not in html and "https://" not in html


def test_html_export_titles_and_status():
    html = render_html(_golden_events())
    assert "<title>" in html and "AAPL" in html
    # Header rollup: complete status + the memo-call usage figures.
    assert "Complete" in html
    assert "$0.0136" in html  # cost rollup
    assert "1,600" in html and "960" in html  # input / output tokens


def test_html_export_renders_phases_personas_and_thinking():
    html = render_html(_golden_events())
    for label in _PHASE_LABELS:
        assert label in html, f"missing phase banner {label!r}"
    for persona in _PERSONAS:
        assert persona in html, f"missing persona {persona!r}"
    # Expandable thinking is a <details>/<summary> disclosure.
    assert "<details" in html
    assert "<summary>thinking</summary>" in html
    assert html.count("<details") >= 6  # at least the six opening turns' thinking


def test_html_export_scorecard_memo_disagreements_usage_sections():
    html = render_html(_golden_events())
    # Scorecard: one dedicated section, consensus + a row per voter.
    assert html.count('class="th-section th-scorecard"') == 1
    assert "BUY" in html and "SELL" in html
    assert html.count('class="th-vcell"') == 6  # six voter rows
    # Memo: all five sections.
    assert "Investment Memo" in html
    for title in _MEMO_TITLES:
        assert title in html
    # Disagreements incl. collapse flags.
    assert "Disagreement Analysis" in html
    assert "Valuation" in html and "Cycle timing" in html
    assert "Disagreement Collapse" in html
    assert "CAVED" in html and "Benjamin Graham" in html
    # Usage rollup with a per-model breakdown.
    assert "Usage" in html and "By model" in html
    assert "openai/gpt-5.2" in html


def test_html_export_does_not_duplicate_structured_artifacts_in_transcript():
    # The scorecard/memo/disagreement artifacts live in their own sections, not
    # inline in the transcript, so nothing is rendered twice.
    html = render_html(_golden_events())
    start = html.index('class="th-transcript"')
    transcript = html[start : html.index("</section>", start)]
    assert "th-scorecard" not in transcript
    assert "th-memo-section" not in transcript
    assert "th-disagreement" not in transcript


def test_html_export_escapes_hostile_payload_content():
    # A payload can never inject live markup: every dynamic value is escaped.
    events = _synthetic(
        [
            (
                "debate_started",
                {
                    "ticker": "AAPL",
                    "company_name": "Apple <inc> & \"co\"",
                    "preset": "p",
                    "personas": [{"name": "Warren Buffett", "model_ref": "openai/gpt-5.2"}],
                    "moderator": "rules",
                    "aggregator": "openai/gpt-5.2",
                    "caps": {},
                    "config_hash": "sha256:0",
                    "tinyic_version": "0.1.0",
                },
            ),
            ("phase_started", {"phase": "opening", "index": 0}),
            (
                "turn_started",
                {"turn_id": "t1", "persona": "Warren Buffett", "phase": "opening", "role": "statement"},
            ),
            (
                "talk_completed",
                {"turn_id": "t1", "full_text": "<script>alert('xss')</script> & <b>bold</b>"},
            ),
            ("turn_completed", {"turn_id": "t1", "persona": "Warren Buffett", "phase": "opening", "interrupted": False, "usage_ref": None}),
            ("debate_completed", {"phases_completed": ["opening"], "duration_s": 1.0, "result_ref": "x"}),
        ]
    )
    html = render_html(events)
    assert "<script>alert" not in html  # never a live script tag
    assert "&lt;script&gt;alert" in html  # rendered as inert, escaped text
    assert "&amp;" in html  # bare ampersand escaped
    _assert_self_contained(html)


def test_html_export_renders_interrupt_badge():
    events = _synthetic(
        [
            (
                "debate_started",
                {"ticker": "AAPL", "company_name": "Apple Inc.", "preset": "p",
                 "personas": [{"name": "Warren Buffett"}], "moderator": "rules",
                 "aggregator": "m", "caps": {}, "config_hash": "h", "tinyic_version": "0.1.0"},
            ),
            ("phase_started", {"phase": "opening", "index": 0}),
            ("turn_started", {"turn_id": "t1", "persona": "Warren Buffett", "phase": "opening", "role": "statement"}),
            ("talk_completed", {"turn_id": "t1", "full_text": "half a thought"}),
            ("turn_interrupted", {"turn_id": "t1", "persona": "Warren Buffett", "by": "user", "disposition": "discarded_on_arrival"}),
            ("debate_completed", {"phases_completed": ["opening"], "duration_s": 1.0, "result_ref": "x"}),
        ]
    )
    html = render_html(events)
    assert "interrupted" in html and "(esc)" in html


# ==========================================================================
# Markdown export — bundle contents, order, model reuse
# ==========================================================================


def test_markdown_export_bundles_all_sections_in_order():
    md = render_markdown(_golden_events())
    # Scorecard reuses Scorecard.to_markdown() (the removed Streamlit download).
    assert "Investment Scorecard" in md
    assert "| Investor | Vote | Confidence | Key Reasoning |" in md
    for persona in _PERSONAS:
        assert persona in md
    # Memo reuses InvestmentMemo.to_markdown().
    assert "# Investment Memo" in md
    # Transcript is built from the turns.
    assert "## Transcript" in md
    for label in _PHASE_LABELS:
        assert label in md
    # Disagreements reuse DisagreementAnalysis.to_markdown() incl. the DCR block.
    assert "# Disagreement Analysis" in md
    assert "Disagreement Collapse (DCR)" in md
    assert "[CAVED]" in md

    # Bundle order: scorecard → memo → transcript → disagreements.
    order = [
        md.index("Investment Scorecard"),
        md.index("# Investment Memo"),
        md.index("## Transcript"),
        md.index("# Disagreement Analysis"),
    ]
    assert order == sorted(order)


def test_markdown_transcript_carries_speeches_and_stances():
    md = render_markdown(_golden_events())
    assert "Apple is a wonderful business at a fair price." in md
    assert "[bullish]" in md and "[bearish]" in md


# ==========================================================================
# Truncated / partial logs degrade, never crash
# ==========================================================================


def test_export_tolerates_a_truncated_log():
    events = _synthetic(
        [
            (
                "debate_started",
                {"ticker": "AAPL", "company_name": "Apple Inc.", "preset": "p",
                 "personas": [{"name": "Warren Buffett"}], "moderator": "m",
                 "aggregator": "m", "caps": {}, "config_hash": "h", "tinyic_version": "0.1.0"},
            ),
            ("phase_started", {"phase": "opening", "index": 0}),
            ("turn_started", {"turn_id": "t1", "persona": "Warren Buffett", "phase": "opening", "role": "statement"}),
            ("talk_completed", {"turn_id": "t1", "full_text": "an unfinished debate"}),
            # ...crash: no scorecard, memo, or terminal event.
        ]
    )
    html = render_html(events)
    md = render_markdown(events)
    assert html.startswith("<!doctype html>")
    assert "Incomplete" in html  # header reports the truncation
    assert 'class="th-section th-scorecard"' not in html  # no scorecard section
    _assert_self_contained(html)
    # Markdown still renders the transcript it *did* capture.
    assert "an unfinished debate" in md
    assert "Investment Scorecard" not in md


# ==========================================================================
# CLI wiring (FR-6.1 surface)
# ==========================================================================


def test_export_parser_requires_exactly_one_format():
    args = build_parser().parse_args(["export", "run-1", "--html"])
    assert args.command == "export" and args.fmt == "html"
    args = build_parser().parse_args(["export", "run-1", "--md"])
    assert args.fmt == "md"
    # Neither format is a usage error (argparse exits 2).
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["export", "run-1"])
    assert exc.value.code == 2


def test_export_help_lists_the_command(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "export" in capsys.readouterr().out


def test_cmd_export_writes_report_to_stdout(monkeypatch, capsys):
    import tinyic.report as report_module

    captured = {}

    def fake_render(id_or_path, fmt):
        captured["id"] = id_or_path
        captured["fmt"] = fmt
        return "RENDERED-REPORT-BODY"

    monkeypatch.setattr(report_module, "load_and_render", fake_render)
    assert main(["export", "aapl-123", "--md"]) == 0
    out = capsys.readouterr()
    assert out.out == "RENDERED-REPORT-BODY"  # verbatim artifact, nothing else
    assert captured == {"id": "aapl-123", "fmt": "md"}


def test_cmd_export_writes_to_file_and_keeps_stdout_clean(monkeypatch, capsys, tmp_path):
    import tinyic.report as report_module

    monkeypatch.setattr(report_module, "load_and_render", lambda i, f: "<!doctype html>\n<html></html>\n")
    target = tmp_path / "nested" / "report.html"
    assert main(["export", "aapl-123", "--html", "-o", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("<!doctype html>")
    out = capsys.readouterr()
    assert out.out == ""  # artifact went to the file, not stdout
    assert "wrote html report" in out.err  # human progress on stderr


def test_cmd_export_missing_run_is_exit_3(capsys):
    assert main(["export", "no-such-debate-xyz", "--html"]) == 3
    assert "no debate run found" in capsys.readouterr().err


def test_load_and_render_resolves_a_path(tmp_path):
    # A direct .jsonl path is honored verbatim (mirrors ``tinyic result``).
    html = load_and_render(str(FIXTURE), "html")
    assert html.startswith("<!doctype html>")
    with pytest.raises(FileNotFoundError):
        load_and_render(str(tmp_path / "missing.jsonl"), "md")


# ==========================================================================
# Fresh-process hygiene: the exported artifact is the only thing on STDOUT
# ==========================================================================


def test_fresh_process_export_md_keeps_stdout_clean(tmp_path):
    """``tinyic export --md`` imports the debate models (→ TinyTroupe, which
    prints an AI disclaimer + config dump on import); none of it may reach the
    STDOUT artifact channel."""
    import os

    env = dict(os.environ)
    env["TINYIC_RUNS_DIR"] = str(tmp_path / "runs")
    program = (
        "import sys; from tinyic.cli import main; "
        f"sys.exit(main(['export', {str(FIXTURE)!r}, '--md']))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", program],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0
    # The one-time import banner + config dump are OFF stdout entirely.
    assert "DISCLAIMER" not in proc.stdout
    assert "TinyTroupe configuration" not in proc.stdout
    # STDOUT is exactly the Markdown bundle.
    assert proc.stdout.startswith("# TinyIC — AAPL")
    assert "Investment Scorecard" in proc.stdout
    assert "# Disagreement Analysis" in proc.stdout
