"""Renamer worker: orchestrates text extraction, AI summarization, and filename generation.

Runs on a background QThread. Renames happen automatically for successful files
(no user interaction needed). On errors, the worker first tries to troubleshoot
(retry with different parameters) before reporting the failure so the GUI can
show an error dialog for manual naming.

For each file the pipeline also:
- Generates a .bib sidecar file using bibtex-gen (DOI lookup) and bibtex-cleaner
- Combines the service-provided title (from DOI) with the LLM-distilled title
  in the final filename, following Zotero naming conventions
"""

import logging
import os
import time

from PySide6.QtCore import QThread, Signal

from core.ai_namer import GemmaNamer
from core.bib_generator import combine_titles, generate_bib_file
from core.filename_sanitizer import build_zotero_filename
from core.text_extractor import extract_text

logger = logging.getLogger(__name__)

# Troubleshooting: max_pages variants to try when extraction seems too short
_RETRY_MAX_PAGES = [30, 50]
# Minimum characters of extracted text to consider "usable"
_MIN_TEXT_LENGTH = 200


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
    """Processes files through the AI pipeline, auto-renaming on success.

    Pipeline per file:
    1. Extract text (with troubleshooting retries on failure)
    2. Chunk text → Map summarize → Reduce → Extract metadata
    3. Generate .bib sidecar via bibtex-gen + bibtex-cleaner
    4. Combine service title (from DOI) with LLM title
    5. Build Zotero-friendly filename
    6. Emit proposal (success or error after exhausting retries)

    The GUI auto-renames successful proposals and shows an error dialog
    only for failures.
    """

    # Signals for GUI
    progress = Signal(int, int)          # (current_index, total_count)
    current_file = Signal(str)           # path of file being processed
    status_update = Signal(str)          # real-time status text
    proposal_ready = Signal(object)      # RenameProposal (success or error)
    finished_all = Signal(int, int)      # (success_count, error_count)

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
        """Process all files, emitting proposals one at a time."""
        namer = GemmaNamer(base_url=self.ollama_url, model=self.model)
        total = len(self.files)
        success_count = 0
        error_count = 0

        for i, filepath in enumerate(self.files):
            if self.isInterruptionRequested():
                break

            self.current_file.emit(filepath)
            self.progress.emit(i + 1, total)

            proposal = self._process_with_troubleshoot(filepath, namer)
            self.proposal_ready.emit(proposal)

            if proposal.error:
                error_count += 1
            else:
                success_count += 1

        self.finished_all.emit(success_count, error_count)

    def _process_with_troubleshoot(
        self, filepath: str, namer: GemmaNamer,
    ) -> RenameProposal:
        """Try the full pipeline, troubleshooting failures in real-time.

        Troubleshooting sequence:
        1. Normal extraction (15 pages/items)
        2. If text too short or empty → retry with 30 pages, then 50
        3. If text extraction throws → retry once after a brief pause
        4. If AI metadata extraction returns fallback → retry the AI call
        5. If all retries exhausted → return error proposal
        """
        basename = os.path.basename(filepath)

        # --- Step 1: Text extraction with retries ---
        text = ""
        extraction_error = ""

        self.status_update.emit(f"Extracting text: {basename}")
        try:
            text = extract_text(filepath)
        except Exception as e:
            extraction_error = str(e)
            logger.warning("Initial extraction failed for %s: %s", basename, e)

        # Troubleshoot: too short or empty text
        if not text or len(text.strip()) < _MIN_TEXT_LENGTH:
            for max_pages in _RETRY_MAX_PAGES:
                if self.isInterruptionRequested():
                    break
                self.status_update.emit(
                    f"Troubleshooting: retrying with {max_pages} pages: {basename}"
                )
                try:
                    text = extract_text(filepath, max_pages=max_pages)
                    if text and len(text.strip()) >= _MIN_TEXT_LENGTH:
                        extraction_error = ""
                        break
                except Exception as e:
                    extraction_error = str(e)
                    logger.warning(
                        "Retry extraction (%d pages) failed for %s: %s",
                        max_pages, basename, e,
                    )

        # Troubleshoot: extraction threw but we haven't retried yet
        if extraction_error and (not text or not text.strip()):
            self.status_update.emit(
                f"Troubleshooting: retrying extraction after pause: {basename}"
            )
            time.sleep(0.5)
            try:
                text = extract_text(filepath)
                if text and text.strip():
                    extraction_error = ""
            except Exception as e:
                extraction_error = str(e)

        # Final check — no text at all
        if not text or not text.strip():
            return RenameProposal(
                original_path=filepath,
                error=extraction_error or "No text could be extracted from file",
            )

        # --- Step 2-3: AI metadata extraction with retry ---
        metadata = None
        ai_error = ""

        self.status_update.emit(f"AI processing: {basename}")
        try:
            metadata = namer.generate_metadata(text)
        except Exception as e:
            ai_error = str(e)
            logger.warning("AI metadata failed for %s: %s", basename, e)

        # Troubleshoot: AI returned fallback metadata (empty authors + "Untitled")
        if metadata and self._is_fallback_metadata(metadata):
            self.status_update.emit(
                f"Troubleshooting: retrying AI with more text: {basename}"
            )
            try:
                # Retry with the full extracted text (not truncated)
                metadata = namer.generate_metadata(text)
            except Exception as e:
                ai_error = str(e)

        # Troubleshoot: AI call itself failed
        if ai_error and not metadata:
            self.status_update.emit(
                f"Troubleshooting: retrying AI call: {basename}"
            )
            time.sleep(1.0)
            try:
                metadata = namer.generate_metadata(text)
                ai_error = ""
            except Exception as e:
                ai_error = str(e)

        if not metadata:
            return RenameProposal(
                original_path=filepath,
                error=ai_error or "AI metadata extraction failed",
            )

        # --- Step 4: Build proposal ---
        try:
            return self._build_proposal(filepath, metadata)
        except Exception as e:
            logger.exception("Failed to build proposal for %s", filepath)
            return RenameProposal(
                original_path=filepath,
                metadata=metadata,
                error=f"Filename generation failed: {e}",
            )

    def _build_proposal(
        self, filepath: str, metadata: dict,
    ) -> RenameProposal:
        """Build the rename proposal: .bib sidecar + combined title + filename."""
        llm_title = metadata.get("title", "Untitled")
        directory = os.path.dirname(filepath)
        stem = os.path.splitext(os.path.basename(filepath))[0]

        self.status_update.emit(
            f"Generating .bib: {os.path.basename(filepath)}"
        )

        bib_path, service_title = generate_bib_file(
            metadata=metadata,
            output_dir=directory,
            base_filename=stem,
            mendeley_client_id=self.mendeley_client_id,
            mendeley_client_secret=self.mendeley_client_secret,
        )

        # Combine service title with LLM title
        combined_title = combine_titles(service_title, llm_title)
        metadata["title"] = combined_title

        # Build Zotero filename
        _, ext = os.path.splitext(filepath)
        proposed_name = build_zotero_filename(metadata, ext)

        return RenameProposal(
            original_path=filepath,
            proposed_name=proposed_name,
            metadata=metadata,
            bib_path=bib_path,
        )

    @staticmethod
    def _is_fallback_metadata(metadata: dict) -> bool:
        """Check if metadata looks like the fallback/empty result."""
        authors = metadata.get("authors", [])
        title = metadata.get("title", "")
        return (
            not authors
            and title in ("Untitled", "")
            and not metadata.get("year", "")
        )
