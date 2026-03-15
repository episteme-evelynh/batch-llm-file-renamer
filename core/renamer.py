"""Renamer worker: orchestrates text extraction, AI summarization, and filename generation.

Runs on a background QThread. Does NOT perform actual file renames — it only
proposes new filenames. The GUI presents these proposals in a preview dialog
for user confirmation before any files are touched.
"""

import logging
import os

from PySide6.QtCore import QThread, Signal

from core.ai_namer import GemmaNamer
from core.filename_sanitizer import build_zotero_filename
from core.text_extractor import extract_text

logger = logging.getLogger(__name__)


class RenameProposal:
    """A proposed file rename with metadata."""

    __slots__ = ("original_path", "proposed_name", "metadata", "error")

    def __init__(
        self,
        original_path: str,
        proposed_name: str = "",
        metadata: dict | None = None,
        error: str = "",
    ):
        self.original_path = original_path
        self.proposed_name = proposed_name
        self.metadata = metadata or {}
        self.error = error


class RenamerWorker(QThread):
    """Processes files through the AI pipeline to generate rename proposals.

    Pipeline per file:
    1. Extract text from first 15 pages (PDF) or 15 sections (EPUB)
    2. Chunk text with overlap → Map: summarize each chunk → Reduce: combine
    3. Extract bibliographic metadata (authors, year, title, doc_type)
    4. Build Zotero-friendly filename

    All results are proposals only — no files are renamed.
    """

    # Signals for GUI progress updates
    progress = Signal(int, int)          # (current_index, total_count)
    current_file = Signal(str)           # Full path of file being processed
    proposal_ready = Signal(object)      # RenameProposal for one file
    error_occurred = Signal(str, str)    # (filepath, error_message)
    finished_proposals = Signal(list)    # List of all RenameProposal objects

    def __init__(
        self,
        files: list[str],
        ollama_url: str = "http://localhost:11434/v1",
        model: str = "gemma3:4b-it",
        parent=None,
    ):
        super().__init__(parent)
        self.files = files
        self.ollama_url = ollama_url
        self.model = model

    def run(self):
        """Process all files and emit rename proposals."""
        namer = GemmaNamer(base_url=self.ollama_url, model=self.model)
        proposals = []
        total = len(self.files)

        for i, filepath in enumerate(self.files):
            if self.isInterruptionRequested():
                break

            self.current_file.emit(filepath)
            self.progress.emit(i + 1, total)

            proposal = self._process_file(filepath, namer)
            proposals.append(proposal)
            self.proposal_ready.emit(proposal)

            if proposal.error:
                self.error_occurred.emit(filepath, proposal.error)

        self.finished_proposals.emit(proposals)

    def _process_file(self, filepath: str, namer: GemmaNamer) -> RenameProposal:
        """Run the full pipeline on a single file."""
        try:
            # Step 1: Extract text
            text = extract_text(filepath)
            if not text or not text.strip():
                return RenameProposal(
                    original_path=filepath,
                    error="No text could be extracted from file",
                )

            # Step 2-3: AI pipeline (chunk → map → reduce → extract metadata)
            metadata = namer.generate_metadata(text)

            # Step 4: Build Zotero filename
            _, ext = os.path.splitext(filepath)
            proposed_name = build_zotero_filename(metadata, ext)

            return RenameProposal(
                original_path=filepath,
                proposed_name=proposed_name,
                metadata=metadata,
            )

        except Exception as e:
            logger.exception("Failed to process %s", filepath)
            return RenameProposal(
                original_path=filepath,
                error=str(e),
            )
