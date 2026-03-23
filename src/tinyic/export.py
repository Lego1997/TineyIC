"""Export manager for debate artifacts in Markdown and DOCX formats."""

import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Characters not allowed in filenames (cross-platform safe)
_INVALID_FILENAME_CHARS = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']


def has_pandoc() -> bool:
    """Check whether the pandoc binary is available on this system."""
    return shutil.which("pandoc") is not None


def _sanitize_filename(name: str) -> str:
    """Replace invalid filename characters with hyphens."""
    for char in _INVALID_FILENAME_CHARS:
        name = name.replace(char, "-")
    return name


class ArtifactExporter:
    """Thin wrapper around tinytroupe's ArtifactExporter.

    Imported lazily to avoid pulling in heavy dependencies (pandas,
    pypandoc, markdown) at module-import time.
    """

    def __new__(cls, base_output_folder: str):
        from tinytroupe.extraction import ArtifactExporter as _AE
        return _AE(base_output_folder)


class ExportManager:
    """Export debate artifacts as Markdown and DOCX files.

    Markdown export always works (no external dependencies).
    DOCX export requires pandoc; returns None if unavailable.
    """

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)

    def export_markdown(self, content: str, filename: str) -> Path:
        """Export content as a Markdown file.

        Args:
            content: Markdown-formatted string to write.
            filename: Base filename (without extension).

        Returns:
            Path to the created .md file.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _sanitize_filename(filename)
        path = self.output_dir / f"{safe_name}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def export_docx(self, content: str, filename: str) -> Optional[Path]:
        """Export content as a DOCX file via ArtifactExporter.

        Uses markdown -> HTML -> DOCX conversion via pypandoc.
        Returns None if pandoc is not available or if conversion fails.

        Args:
            content: Markdown-formatted string to convert.
            filename: Base filename (without extension).

        Returns:
            Path to the created .docx file, or None if export failed.
        """
        if not has_pandoc():
            logger.info(
                "pandoc not available -- skipping DOCX export for %s", filename
            )
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _sanitize_filename(filename)

        try:
            exporter = ArtifactExporter(str(self.output_dir))
            exporter.export(
                artifact_name=safe_name,
                artifact_data=content,
                content_type="",
                content_format="md",
                target_format="docx",
            )
            result_path = self.output_dir / f"{safe_name}.docx"
            if result_path.exists():
                return result_path
            logger.warning(
                "DOCX file not created at expected path: %s", result_path
            )
            return None
        except Exception as e:
            logger.warning("DOCX export failed for %s: %s", filename, e)
            return None
