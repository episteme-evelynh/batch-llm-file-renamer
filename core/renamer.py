"""Per-file AI rename pipeline as a pure function for QThreadPool.

Replaces the old QThread subclass with ``process_file()`` — a plain
function that runs one file through extract → AI → bib → filename.
It is designed to be mapped over a file list via ``MapRunner.map()``.

The function accepts ``cancel_event`` and ``progress_callback`` from
the concurrency layer, enabling cooperative cancellation and live
status updates without any QObject / QThread coupling.
"""

import logging
import os
import time

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
    """Result of processing a single file through the AI pipeline."""

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


# ---------------------------------------------------------------------------
# Pipeline configuration (passed through MapRunner shared kwargs)
# ---------------------------------------------------------------------------

class RenameConfig:
    """Immutable config bundle passed to every ``process_file`` call."""

    __slots__ = (
        "ollama_url", "model", "mendeley_client_id", "mendeley_client_secret",
        "_namer",
    )

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434/v1",
        model: str = "gemma3:4b-it",
        mendeley_client_id: str = "",
        mendeley_client_secret: str = "",
    ):
        self.ollama_url = ollama_url
        self.model = model
        self.mendeley_client_id = mendeley_client_id
        self.mendeley_client_secret = mendeley_client_secret
        # Lazily created GemmaNamer (shared across sequential calls,
        # or one per thread when running in parallel)
        self._namer = None

    def get_namer(self) -> GemmaNamer:
        """Return a (cached) GemmaNamer for the current thread."""
        if self._namer is None:
            self._namer = GemmaNamer(
                base_url=self.ollama_url, model=self.model,
            )
        return self._namer


# ---------------------------------------------------------------------------
# The mapped function — one call per file
# ---------------------------------------------------------------------------

def process_file(
    filepath: str,
    *,
    config: RenameConfig,
    cancel_event=None,
    progress_callback=None,
) -> RenameProposal:
    """Run the full AI rename pipeline on a single file.

    This is the function passed to ``MapRunner.map(process_file, files,
    config=config)``.  It returns a ``RenameProposal`` (success or error
    after exhausting troubleshooting retries).

    Pipeline:
    1. Extract text (with troubleshooting retries)
    2. AI metadata extraction (with retries)
    3. Generate .bib sidecar
    4. Combine service + LLM titles
    5. Build Zotero filename
    """
    basename = os.path.basename(filepath)
    namer = config.get_namer()

    def _cancelled():
        return cancel_event and cancel_event.is_set()

    def _status(msg):
        if progress_callback:
            progress_callback(msg)

    # --- Step 1: Text extraction with troubleshooting ---
    text, extraction_error = _extract_with_troubleshoot(
        filepath, basename, _cancelled, _status,
    )

    if not text or not text.strip():
        return RenameProposal(
            original_path=filepath,
            error=extraction_error or "No text could be extracted from file",
        )

    # --- Step 2: AI metadata extraction with troubleshooting ---
    metadata, ai_error = _ai_metadata_with_troubleshoot(
        text, basename, namer, _cancelled, _status,
    )

    if not metadata:
        return RenameProposal(
            original_path=filepath,
            error=ai_error or "AI metadata extraction failed",
        )

    # --- Step 3-5: Bib sidecar + combined title + Zotero filename ---
    try:
        return _build_proposal(filepath, metadata, config, _status)
    except Exception as e:
        logger.exception("Failed to build proposal for %s", filepath)
        return RenameProposal(
            original_path=filepath,
            metadata=metadata,
            error=f"Filename generation failed: {e}",
        )


# ---------------------------------------------------------------------------
# Internal pipeline stages
# ---------------------------------------------------------------------------

def _extract_with_troubleshoot(filepath, basename, cancelled, status):
    """Extract text, retrying with more pages on short/empty results."""
    text = ""
    error = ""

    status(f"Extracting text: {basename}")
    try:
        text = extract_text(filepath)
    except Exception as e:
        error = str(e)
        logger.warning("Initial extraction failed for %s: %s", basename, e)

    # Retry with more pages if text is too short
    if not text or len(text.strip()) < _MIN_TEXT_LENGTH:
        for max_pages in _RETRY_MAX_PAGES:
            if cancelled():
                break
            status(f"Troubleshooting: retrying with {max_pages} pages: {basename}")
            try:
                text = extract_text(filepath, max_pages=max_pages)
                if text and len(text.strip()) >= _MIN_TEXT_LENGTH:
                    error = ""
                    break
            except Exception as e:
                error = str(e)
                logger.warning(
                    "Retry extraction (%d pages) failed for %s: %s",
                    max_pages, basename, e,
                )

    # Retry after a brief pause if extraction threw
    if error and (not text or not text.strip()):
        status(f"Troubleshooting: retrying extraction after pause: {basename}")
        time.sleep(0.5)
        try:
            text = extract_text(filepath)
            if text and text.strip():
                error = ""
        except Exception as e:
            error = str(e)

    return text, error


def _ai_metadata_with_troubleshoot(text, basename, namer, cancelled, status):
    """Call the AI namer, retrying on fallback/empty results."""
    metadata = None
    error = ""

    status(f"AI processing: {basename}")
    try:
        metadata = namer.generate_metadata(text)
    except Exception as e:
        error = str(e)
        logger.warning("AI metadata failed for %s: %s", basename, e)

    # Retry if AI returned obviously empty/fallback metadata
    if metadata and _is_fallback_metadata(metadata):
        if not cancelled():
            status(f"Troubleshooting: retrying AI with more text: {basename}")
            try:
                metadata = namer.generate_metadata(text)
            except Exception as e:
                error = str(e)

    # Retry if the call itself failed
    if error and not metadata:
        if not cancelled():
            status(f"Troubleshooting: retrying AI call: {basename}")
            time.sleep(1.0)
            try:
                metadata = namer.generate_metadata(text)
                error = ""
            except Exception as e:
                error = str(e)

    return metadata, error


def _build_proposal(filepath, metadata, config, status):
    """Generate .bib sidecar, combine titles, build Zotero filename."""
    llm_title = metadata.get("title", "Untitled")
    directory = os.path.dirname(filepath)
    stem = os.path.splitext(os.path.basename(filepath))[0]

    status(f"Generating .bib: {os.path.basename(filepath)}")

    bib_path, service_title = generate_bib_file(
        metadata=metadata,
        output_dir=directory,
        base_filename=stem,
        mendeley_client_id=config.mendeley_client_id,
        mendeley_client_secret=config.mendeley_client_secret,
    )

    combined_title = combine_titles(service_title, llm_title)
    metadata["title"] = combined_title

    _, ext = os.path.splitext(filepath)
    proposed_name = build_zotero_filename(metadata, ext)

    return RenameProposal(
        original_path=filepath,
        proposed_name=proposed_name,
        metadata=metadata,
        bib_path=bib_path,
    )


def _is_fallback_metadata(metadata: dict) -> bool:
    """Check if metadata looks like the fallback/empty result."""
    authors = metadata.get("authors", [])
    title = metadata.get("title", "")
    return (
        not authors
        and title in ("Untitled", "")
        and not metadata.get("year", "")
    )
