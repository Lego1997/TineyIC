"""Static contract checks for the zero-build Web Town Hall assets.

These tests intentionally need no browser, network, or JavaScript runtime. They pin
the security- and schema-sensitive parts of the packaged page while higher-level
server tests exercise delivery over HTTP.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest


ASSET_DIR = Path(__file__).parents[1] / "src" / "tinyic" / "web" / "assets"
HTML_PATH = ASSET_DIR / "index.html"
JS_PATH = ASSET_DIR / "app.js"
CSS_PATH = ASSET_DIR / "style.css"

SCHEMA_V1_EVENT_TYPES = {
    "debate_started",
    "data_ready",
    "phase_started",
    "phase_completed",
    "debate_completed",
    "debate_error",
    "turn_started",
    "think_delta",
    "think_completed",
    "talk_delta",
    "talk_completed",
    "cognitive_state",
    "turn_completed",
    "turn_interrupted",
    "steering_submitted",
    "steering_delivered",
    "steering_dropped",
    "thesis_recorded",
    "vote_recorded",
    "scorecard",
    "memo_section",
    "disagreement",
    "collapse_metric",
    "usage",
    "usage_window",
}

STALE_OR_INVENTED_EVENT_TYPES = {
    "vote_cast",
    "scorecard_ready",
    "memo_ready",
    "analysis_completed",
}


@pytest.fixture(scope="module")
def html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def javascript() -> str:
    return JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


class _MarkupInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.references: list[str] = []
        self.inline_handlers: list[str] = []
        self.inline_styles: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"] or "")
        for attribute in ("href", "src"):
            if attributes.get(attribute):
                self.references.append(attributes[attribute] or "")
        self.inline_handlers.extend(name for name, _ in attrs if name.startswith("on"))
        if "style" in attributes:
            self.inline_styles.append(attributes.get("style") or "")


def test_asset_bundle_exists_and_uses_only_local_references(html: str) -> None:
    assert {
        "index.html",
        "app.js",
        "style.css",
    } <= {path.name for path in ASSET_DIR.iterdir()}
    inventory = _MarkupInventory()
    inventory.feed(html)
    assert "/assets/app.js" in inventory.references
    assert "/assets/style.css" in inventory.references
    assert all(reference.startswith(("/", "#")) for reference in inventory.references)
    assert not inventory.inline_handlers
    assert not inventory.inline_styles


def test_assets_contain_no_external_resource_references(
    html: str, javascript: str, css: str
) -> None:
    external_reference = re.compile(
        r"(?i)(?:https?:)?//[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:[/?:#]|$)"
    )
    for source in (html, javascript, css):
        assert external_reference.search(source) is None
    assert "@import" not in css
    assert re.search(r"url\s*\(", css, flags=re.IGNORECASE) is None


def test_page_exposes_every_required_surface(html: str) -> None:
    inventory = _MarkupInventory()
    inventory.feed(html)
    required_ids = {
        "ticker",
        "company-name",
        "phase-chip",
        "elapsed",
        "connection-state",
        "transcript",
        "committee-roster",
        "scorecard-panel",
        "memo-panel",
        "disagreement-panel",
        "steering-panel",
        "thinking-toggle",
        "steering-form",
        "steering-target",
        "steering-text",
        "interrupt-button",
        "pause-button",
        "next-phase-button",
        "stop-button",
        "export-html",
        "export-md",
        "replay-chip",
        "verdict-summary",
        "error-banner",
        "truncated-banner",
        "jump-live",
        "confirm-dialog",
    }
    assert required_ids <= inventory.ids
    assert 'href="/api/result?fmt=html"' in html
    assert 'href="/api/result?fmt=md"' in html


def test_client_consumes_authoritative_schema_v1_names_only(javascript: str) -> None:
    declared_match = re.search(
        r"AUTHORITATIVE_EVENT_TYPES\s*=\s*Object\.freeze\(\[(.*?)\]\);",
        javascript,
        flags=re.DOTALL,
    )
    assert declared_match is not None
    declared = set(re.findall(r'"([a-z_]+)"', declared_match.group(1)))
    assert declared == SCHEMA_V1_EVENT_TYPES
    for event_type in SCHEMA_V1_EVENT_TYPES:
        assert f'case "{event_type}"' in javascript
    for invented in STALE_OR_INVENTED_EVENT_TYPES:
        assert invented not in javascript


def test_unknown_events_are_explicitly_ignored(javascript: str) -> None:
    dispatch = javascript[javascript.index("function dispatchEnvelope") :]
    assert "default:" in dispatch
    assert "unknown event types are deliberately ignored" in dispatch
    assert re.search(r"default:\s*\n\s*//[^\n]+\n\s*return;", dispatch)


def test_sse_full_replay_and_resume_contract_is_visible(javascript: str) -> None:
    assert 'fetch("/api/meta"' in javascript
    assert 'new EventSource("/events?from_seq=0")' in javascript
    assert "eventSource.onmessage = handleSseMessage" in javascript
    assert "eventSource.addEventListener(eventType, handleSseMessage)" in javascript
    assert "envelope.seq <= state.lastSeq" in javascript
    assert "state.lastSeq = envelope.seq" in javascript
    assert "state.meta.seqHigh" in javascript


def test_unexpected_live_stream_close_reconciles_worker_failure(
    javascript: str,
) -> None:
    close_handler = javascript[
        javascript.index("function reconcileUnexpectedClose") : javascript.index(
            "async function loadMeta"
        )
    ]
    assert "await loadMeta()" in close_handler
    assert 'state.meta.status === "error"' in close_handler
    assert 'setRunState("error")' in close_handler
    assert 'dom.errorTitle.textContent = "Run ended unexpectedly"' in close_handler
    assert "without a terminal record" in close_handler
    assert "dom.truncatedBanner.hidden = false" in close_handler
    assert "void reconcileUnexpectedClose()" in javascript
    assert "state.meta.status = normalizedStatus" in javascript


def test_markdown_is_escape_first_and_never_assigns_model_html(
    javascript: str,
) -> None:
    markdown_function = javascript[
        javascript.index("function renderMarkdown") : javascript.index(
            "function setMarkdown"
        )
    ]
    assert "escapeHtml(" in markdown_function
    assert markdown_function.index("escapeHtml(") < markdown_function.index(".split(")
    assert "<strong>" in javascript
    assert "<em>" in javascript
    assert "<pre><code>" in javascript
    assert "<blockquote>" in javascript
    assert 'const listTag = ordered ? "ol" : "ul"' in javascript
    assert "createContextualFragment(renderMarkdown(markdown))" in javascript
    for forbidden_sink in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
    ):
        assert forbidden_sink not in javascript


def test_markdown_links_are_http_only_and_hardened(javascript: str) -> None:
    assert 'parsed.protocol !== "http:"' in javascript
    assert 'parsed.protocol !== "https:"' in javascript
    assert 'rel="noopener noreferrer"' in javascript
    assert 'target="_blank"' in javascript
    assert "javascript:" not in javascript.lower()


def test_controls_use_json_post_protocol(javascript: str) -> None:
    assert '"Content-Type": "application/json"' in javascript
    assert 'postJson("/api/steering", payload)' in javascript
    assert 'postJson("/api/control", { type })' in javascript
    for control_type in ("pause", "resume", "next_phase", "stop"):
        assert f'"{control_type}"' in javascript
    for steering_type in ("steer", "queue", "interrupt"):
        assert f'"{steering_type}"' in javascript
    assert "event.shiftKey" in javascript
    assert "event.isComposing" in javascript
    assert "state.steeringPending" in javascript
    assert 'detail === "replay_read_only"' in javascript
    assert "!state.paused" in javascript
    assert '!["completed", "error"].includes(state.meta.status)' in javascript


def test_page_states_and_jump_to_live_are_implemented(
    javascript: str, css: str
) -> None:
    for state_name in ("connecting", "live", "replay", "completed", "error"):
        assert f'"{state_name}"' in javascript
    assert 'body[data-run-state="replay"] .composer' in css
    assert 'body[data-run-state="completed"] .export-action' in css
    assert 'body[data-run-state="error"] .composer' in css
    assert "function maybeMarkTruncatedReplay" in javascript
    assert "function updateJumpToLive" in javascript
    assert "window.scrollTo" in javascript


def test_persona_palette_is_hand_synced_for_both_color_schemes(css: str) -> None:
    assert "hand-synced from src/tinyic/persona_style.py" in css
    light_colors = {"#01579b", "#7b1fa2", "#00695c", "#bf360c", "#c62828", "#33691e"}
    dark_colors = {"#4fc3f7", "#ba68c8", "#4db6ac", "#ffb74d", "#e57373", "#aed581"}
    for color in light_colors | dark_colors:
        assert color in css
    assert "@media (prefers-color-scheme: dark)" in css
    assert 'Charter, Georgia, "Times New Roman", serif' in css


def test_layout_collapses_rail_and_respects_accessibility_preferences(css: str) -> None:
    assert "@media (max-width: 56.25rem)" in css
    assert ".right-rail" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_client_stays_within_es2020(javascript: str) -> None:
    assert ".replaceAll(" not in javascript
    assert "Object.hasOwn(" not in javascript
    assert "structuredClone(" not in javascript
