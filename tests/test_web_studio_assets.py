"""Static contract checks for the persona studio assets (zero-build)."""

from __future__ import annotations

import re
from pathlib import Path

ASSET_DIR = Path(__file__).parents[1] / "src" / "tinyic" / "web" / "assets"
HTML = (ASSET_DIR / "studio.html").read_text(encoding="utf-8")
JS = (ASSET_DIR / "studio.js").read_text(encoding="utf-8")
CSS = (ASSET_DIR / "studio.css").read_text(encoding="utf-8")
BASE = (ASSET_DIR / "base.css").read_text(encoding="utf-8")

REQUIRED_IDS = [
    "studio-main",
    "view-library",
    "view-research",
    "view-editor",
    "view-committee",
    "library-grid",
    "library-empty",
    "confirm-dialog",
    "confirm-message",
    "confirm-accept",
    "confirm-cancel",
    "duplicate-dialog",
    "duplicate-slug",
    "duplicate-accept",
    "duplicate-cancel",
    "toast",
]

BANNED_JS = (
    "innerHTML",
    "insertAdjacentHTML",
    "eval(",
    ".replaceAll(",
    "Object.hasOwn(",
    "structuredClone(",
)


def test_required_element_ids():
    for element_id in REQUIRED_IDS:
        assert f'id="{element_id}"' in HTML, element_id


def test_shared_layers_load_first():
    assert HTML.index("/assets/base.css") < HTML.index("/assets/studio.css")
    assert HTML.index("/assets/md.js") < HTML.index("/assets/studio.js")


def test_no_external_references_or_inline_handlers():
    for text in (HTML, JS, CSS):
        assert "http://" not in text and "https://" not in text
        assert "@import" not in text and "url(" not in text
    assert not re.search(r"\son[a-z]+=\"", HTML)
    assert 'style="' not in HTML


def test_js_es2020_and_rendering_discipline():
    for banned in BANNED_JS:
        assert banned not in JS
    # No raw-HTML parsing anywhere; markdown rendering arrives with the editor.
    assert "createContextualFragment" not in JS


def test_persona_palette_synced_with_python_source():
    # Same convention as the Town Hall sync test: the 12 canonical hues from
    # persona_style.py must all live in the shared base.css palette.
    light_colors = {"#01579b", "#7b1fa2", "#00695c", "#bf360c", "#c62828", "#33691e"}
    dark_colors = {"#4fc3f7", "#ba68c8", "#4db6ac", "#ffb74d", "#e57373", "#aed581"}
    for hex_color in light_colors | dark_colors:
        assert hex_color in BASE, hex_color


def test_library_uses_real_api_paths():
    assert '"/api/personas"' in JS
    assert '/duplicate' in JS


RESEARCH_IDS = [
    "research-form", "research-name", "research-slug", "research-model",
    "research-max-searches", "research-force", "research-estimate",
    "research-estimate-card", "research-estimate-details", "research-confirm",
    "research-progress", "research-stages", "research-result", "research-error",
]


def test_research_wizard_markup_and_api_paths():
    for element_id in RESEARCH_IDS:
        assert f'id="{element_id}"' in HTML, element_id
    assert '"/api/research/estimate"' in JS
    assert '"/api/research"' in JS
    assert "new EventSource(" in JS and "/events" in JS
    assert '"job_completed"' in JS and '"job_error"' in JS


EDITOR_IDS = [
    "editor-monogram", "editor-name", "editor-meta", "editor-readonly",
    "tab-structured", "tab-dossier", "tab-raw",
    "editor-structured", "editor-dossier", "editor-dossier-text",
    "editor-dossier-preview", "editor-raw", "editor-raw-text",
    "editor-issues", "editor-save", "editor-duplicate", "editor-delete",
]


def test_editor_markup_and_modes():
    for element_id in EDITOR_IDS:
        assert f'id="{element_id}"' in HTML, element_id
    assert "TinyICMarkdown.setInto(" in JS  # dossier preview
    assert "decision_checklist" in JS and "famous_quotes" in JS  # field spec


COMMITTEE_IDS = [
    "committee-source", "committee-selected", "committee-available",
    "committee-error", "committee-save", "committee-reset",
]


def test_committee_markup_and_api_paths():
    for element_id in COMMITTEE_IDS:
        assert f'id="{element_id}"' in HTML, element_id
    assert '"/api/committee"' in JS
