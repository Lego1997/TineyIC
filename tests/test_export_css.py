"""The export stylesheet shares the web design tokens (v2.3 harmonization)."""

from tinyic.report import _CSS


def test_export_css_uses_shared_tokens():
    assert "--paper:#f7f3ea" in _CSS
    assert "--ink:#14120f" in _CSS
    assert "prefers-color-scheme: dark" in _CSS
    assert "@media print" in _CSS


def test_export_css_keeps_self_contained_discipline():
    assert "url(" not in _CSS
    assert "http://" not in _CSS and "https://" not in _CSS
    assert "@import" not in _CSS


def test_export_css_keeps_every_class_hook():
    for hook in (
        ".th-header", ".th-main", ".th-turn", ".th-phase", ".th-member",
        ".th-card", ".th-steer", ".th-memo-section", ".th-disagreement",
        ".th-collapse", ".th-usage", ".th-footer", ".th-buy", ".th-hold",
        ".th-sell", ".status-complete", ".th-src-ok", ".th-committee",
        ".th-scorecard-summary", ".th-tablewrap", ".th-vcell",
    ):
        assert hook in _CSS, hook
