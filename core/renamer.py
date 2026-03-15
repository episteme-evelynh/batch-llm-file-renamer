"""Renamer worker: orchestrates text extraction, AI summarization, and filename generation.

Runs on a background QThread. Does NOT perform actual file renames — it only
proposes new filenames. The GUI presents these proposals in a preview dialog
for user confirmation before any files are touched.

For each file the pipeline also:
- Generates a .bib sidecar file using bibtex-gen (DOI lookup) and bibtex-cleaner
- Combines the service-provided title (from DOI) with the LLM-distilled title
  in the final filename, following Zotero naming conventions
"""

import logging
import os

from PySide6.QtCore import QThread, Signal

from core.ai_namer import GemmaNamer
from core.bib_generator import combine_titles, generate_bib_file
from core.filename_sanitizer import build_zotero_filename
from core.text_extractor import extract_text

logger = logging.getLogger(__name__)


class RenameProposal:
    """A proposed file rename with metadata."""

    __slots__ = (
        "original_path", "proposed_name", "metadata", "error", "bib_path",
    )

    def __init__(
        self,
        original_path: str,
        proposed_name: str = "",
        metadata: dict | None = None,
        error: str = "",
        bib_path: str = "",
    ):
        self.original_path = original_path
        self.proposed_name = proposed_name
        self.metadata = metadata or {}
        self.error = error
        self.bib_path = bib_path


class RenamerWorker(QThread):
    """Processes files through the AI pipeline to generate rename proposals.

    Pipeline per file:
    1. Extract text from first 15 pages (PDF) or 15 sections (EPUB)
    2. Chunk text with overlap → Map: summarize each chunk → Reduce: combine
    3. Extract bibliographic metadata (authors, year, title, doc_type, doi, isbn)
    4. Generate .bib sidecar via bibtex-gen (DOI lookup) + bibtex-cleaner
    5. Combine service title (from DOI) with LLM title for the filename
    6. Build Zotero-friendly filename

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
        mendeley_client_id: str = "",
        mendeley_client_secret: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.files = files
        self.ollama_url = ollama_url
        self.model = model
        self.mendeley_client_id = mendeley_client_id
        self.mendeley_client_secret = mendeley_client_secret

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
            llm_title = metadata.get("title", "Untitled")

            # Step 4: Generate .bib sidecar and get service title
            directory = os.path.dirname(filepath)
            stem = os.path.splitext(os.path.basename(filepath))[0]

            bib_path, service_title = generate_bib_file(
                metadata=metadata,
                output_dir=directory,
                base_filename=stem,
                mendeley_client_id=self.mendeley_client_id,
                mendeley_client_secret=self.mendeley_client_secret,
            )

            # Step 5: Combine service title with LLM title
            combined_title = combine_titles(service_title, llm_title)
            metadata["title"] = combined_title

            # Step 6: Build Zotero filename with the combined title
            _, ext = os.path.splitext(filepath)
            proposed_name = build_zotero_filename(metadata, ext)

            return RenameProposal(
                original_path=filepath,
                proposed_name=proposed_name,
                metadata=metadata,
                bib_path=bib_path,
            )

        except Exception as e:
            logger.exception("Failed to process %s", filepath)
            return RenameProposal(
                original_path=filepath,
                error=str(e),
            )
