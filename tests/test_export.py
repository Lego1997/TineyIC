"""Tests for the export module -- Markdown and DOCX output.

Covers ExportManager.export_markdown() and export_docx(), plus
the has_pandoc() utility.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from tinyic.debate.models import (
    Confidence,
    Disagreement,
    DisagreementAnalysis,
    InvestmentMemo,
    MemoSection,
    Scorecard,
    Vote,
    VoteChoice,
)
from tinyic.export import ExportManager, has_pandoc


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def make_test_memo() -> InvestmentMemo:
    """Create a minimal InvestmentMemo for testing."""
    section = MemoSection(
        title="Final Verdict",
        content="Test content for this section.",
        contributing_personas=["Warren Buffett"],
        supporting_data=["P/E ratio of 28x"],
    )
    return InvestmentMemo(
        ticker="AAPL",
        company_name="Apple Inc.",
        executive_summary=MemoSection(title="Executive Summary", content="Summary of findings."),
        investment_thesis=MemoSection(title="Investment Thesis", content="Thesis details."),
        key_risks=MemoSection(title="Key Risks", content="Risk analysis."),
        valuation_discussion=MemoSection(title="Valuation Discussion", content="Valuation details."),
        final_verdict=section,
    )


def make_test_scorecard() -> Scorecard:
    """Create a minimal Scorecard for testing."""
    return Scorecard(
        ticker="AAPL",
        company_name="Apple Inc.",
        votes=[
            Vote(
                investor="Warren Buffett",
                vote=VoteChoice.BUY,
                confidence=Confidence.HIGH,
                reasoning=["Strong moat"],
            ),
        ],
        consensus=VoteChoice.BUY,
        bull_count=1,
    )


def make_test_disagreement_analysis() -> DisagreementAnalysis:
    """Create a minimal DisagreementAnalysis for testing."""
    return DisagreementAnalysis(
        ticker="AAPL",
        company_name="Apple Inc.",
        disagreements=[
            Disagreement(
                dimension="Valuation",
                description="Disagreement on valuation.",
                sides=[
                    {
                        "persona": "Buffett",
                        "position": "Fair price",
                        "evidence_quote": "Quote here",
                    }
                ],
                resolution="Unresolved",
            )
        ],
    )


# ---------------------------------------------------------------------------
# TestHasPandoc
# ---------------------------------------------------------------------------

class TestHasPandoc:
    """Tests for the has_pandoc() utility function."""

    def test_has_pandoc_when_available(self):
        """When shutil.which('pandoc') returns a path, has_pandoc() returns True."""
        with patch("tinyic.export.shutil.which", return_value="/usr/local/bin/pandoc"):
            assert has_pandoc() is True

    def test_has_pandoc_when_missing(self):
        """When shutil.which('pandoc') returns None, has_pandoc() returns False."""
        with patch("tinyic.export.shutil.which", return_value=None):
            assert has_pandoc() is False


# ---------------------------------------------------------------------------
# TestExportManager
# ---------------------------------------------------------------------------

class TestExportManager:
    """Tests for the ExportManager class."""

    def test_export_memo_markdown(self, tmp_path: Path):
        """export_markdown(memo.to_markdown(), 'memo') creates a .md file with correct content."""
        memo = make_test_memo()
        mgr = ExportManager(tmp_path / "output")
        md_content = memo.to_markdown()

        result = mgr.export_markdown(md_content, "memo")

        assert result.exists()
        assert result.suffix == ".md"
        written = result.read_text(encoding="utf-8")
        assert "Apple Inc." in written
        assert "Executive Summary" in written
        assert "Investment Thesis" in written

    def test_export_scorecard_markdown(self, tmp_path: Path):
        """export_markdown(scorecard.to_markdown(), 'scorecard') creates a .md file."""
        scorecard = make_test_scorecard()
        mgr = ExportManager(tmp_path / "output")
        md_content = scorecard.to_markdown()

        result = mgr.export_markdown(md_content, "scorecard")

        assert result.exists()
        written = result.read_text(encoding="utf-8")
        assert "Investment Scorecard" in written
        assert "Warren Buffett" in written

    def test_export_disagreement_markdown(self, tmp_path: Path):
        """export_markdown(analysis.to_markdown(), 'disagreements') creates a .md file."""
        analysis = make_test_disagreement_analysis()
        mgr = ExportManager(tmp_path / "output")
        md_content = analysis.to_markdown()

        result = mgr.export_markdown(md_content, "disagreements")

        assert result.exists()
        written = result.read_text(encoding="utf-8")
        assert "Disagreement Analysis" in written
        assert "Valuation" in written

    def test_export_transcript_markdown(self, tmp_path: Path):
        """export_markdown(transcript_text, 'transcript') creates a .md file with raw text."""
        transcript = "## Round 1\n\nBuffett: I think Apple is fairly valued.\n"
        mgr = ExportManager(tmp_path / "output")

        result = mgr.export_markdown(transcript, "transcript")

        assert result.exists()
        written = result.read_text(encoding="utf-8")
        assert "Buffett: I think Apple is fairly valued." in written

    def test_export_markdown_returns_path(self, tmp_path: Path):
        """export_markdown() returns the Path to the created file."""
        mgr = ExportManager(tmp_path / "output")

        result = mgr.export_markdown("some content", "test-file")

        assert isinstance(result, Path)
        assert result.name == "test-file.md"

    def test_export_markdown_creates_directory(self, tmp_path: Path):
        """export_markdown() creates the output directory if it doesn't exist."""
        nested_dir = tmp_path / "a" / "b" / "c"
        assert not nested_dir.exists()

        mgr = ExportManager(nested_dir)
        result = mgr.export_markdown("content", "deep-file")

        assert nested_dir.exists()
        assert result.exists()

    @pytest.mark.skipif(not has_pandoc(), reason="pandoc not installed")
    def test_export_memo_docx_with_pandoc(self, tmp_path: Path):
        """With pandoc available, export_docx() creates a .docx file and returns the Path."""
        memo = make_test_memo()
        mgr = ExportManager(tmp_path / "output")
        md_content = memo.to_markdown()

        result = mgr.export_docx(md_content, "memo")

        assert result is not None
        assert result.exists()
        assert result.suffix == ".docx"
        # DOCX files start with a PK zip signature
        with open(result, "rb") as f:
            assert f.read(2) == b"PK"

    def test_export_docx_without_pandoc(self, tmp_path: Path):
        """With pandoc NOT available (mocked), export_docx() returns None and does not crash."""
        mgr = ExportManager(tmp_path / "output")

        with patch("tinyic.export.has_pandoc", return_value=False):
            result = mgr.export_docx("# Test content", "memo")

        assert result is None

    def test_export_docx_returns_none_on_error(self, tmp_path: Path):
        """If ArtifactExporter raises during DOCX export, export_docx() returns None."""
        mgr = ExportManager(tmp_path / "output")

        with patch("tinyic.export.has_pandoc", return_value=True), \
             patch("tinyic.export.ArtifactExporter", side_effect=RuntimeError("pandoc broke")):
            result = mgr.export_docx("# Test content", "memo")

        assert result is None

    def test_export_custom_filename(self, tmp_path: Path):
        """export_markdown(content, 'my-custom-name') creates 'my-custom-name.md'."""
        mgr = ExportManager(tmp_path)

        result = mgr.export_markdown("content", "my-custom-name")

        assert result.name == "my-custom-name.md"
        assert result.exists()

    def test_export_sanitizes_filename(self, tmp_path: Path):
        """export_markdown(content, 'bad/name:here') sanitizes invalid characters in filename."""
        mgr = ExportManager(tmp_path)

        result = mgr.export_markdown("content", "bad/name:here")

        # Invalid characters should be replaced with hyphens
        assert "/" not in result.name
        assert ":" not in result.name
        assert result.name == "bad-name-here.md"
        assert result.exists()
