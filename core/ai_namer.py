"""AI-powered filename generation using Gemma 3 4B via Ollama.

Implements a MapReduce summarization pipeline:
1. Chunk extracted text into overlapping segments
2. Map: Summarize each chunk independently
3. Reduce: Combine chunk summaries into a single abstract
4. Extract: Parse bibliographic metadata from the abstract
5. Format: Build a Zotero-friendly filename

Optimized for local inference on Mac Mini M4 Pro (48GB unified memory)
using Gemma 3 4B-Instruction via Ollama's OpenAI-compatible API.
"""

import json
import logging
import re
import time

from openai import OpenAI

logger = logging.getLogger(__name__)

# Default Ollama local endpoint (OpenAI-compatible)
DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "gemma3:4b-it"

# Chunking parameters (in characters, ~4 chars per token)
CHUNK_SIZE = 12_000       # ~3000 tokens per chunk
CHUNK_OVERLAP = 1_800     # ~15% overlap to preserve context flow
MIN_CHUNK_SIZE = 500      # Don't create tiny trailing chunks

# LLM generation parameters optimized for deterministic summarization
SUMMARY_TEMPERATURE = 0.2
SUMMARY_MAX_TOKENS = 400
METADATA_MAX_TOKENS = 300

# Retry configuration for LLM calls
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds

# --- Prompt Templates ---

MAP_SYSTEM_PROMPT = (
    "You are a research document summarizer specializing in academic papers, "
    "books, theses, and manuscripts. You produce precise, factual summaries "
    "that preserve author names, publication years, key findings, and "
    "methodological details. Never fabricate information."
)

MAP_USER_PROMPT = (
    "Summarize the following text excerpt from a document in 2-3 concise "
    "sentences. Preserve any author names, dates, institutional affiliations, "
    "journal names, DOIs, ISBNs, and key findings you find:\n\n{chunk_text}"
)

REDUCE_SYSTEM_PROMPT = (
    "You are a research document summarizer. You combine partial summaries "
    "into a single coherent abstract. Preserve all bibliographic details "
    "(authors, year, journal, DOI, ISBN) and key findings."
)

REDUCE_USER_PROMPT = (
    "Below are partial summaries from different sections of the same document. "
    "Combine them into a single coherent abstract of 3-5 sentences that "
    "captures the document's main topic, authors, year, and key findings:\n\n"
    "{combined_summaries}"
)

METADATA_SYSTEM_PROMPT = (
    "You are a bibliographic metadata extractor. You analyze document "
    "abstracts and extract structured metadata for library cataloging. "
    "You must respond with valid JSON only, no explanation or markdown."
)

METADATA_USER_PROMPT = (
    "Given this abstract of a document, extract bibliographic metadata.\n\n"
    "Abstract: {abstract}\n\n"
    "Respond ONLY with valid JSON (no markdown fencing, no explanation):\n"
    '{{\n'
    '  "doc_type": "<one of: journal_article, book, book_chapter, thesis, '
    'manuscript, conference_paper, report, unknown>",\n'
    '  "authors": ["<LastName1>", "<LastName2>"],\n'
    '  "year": "<YYYY or empty string if unknown>",\n'
    '  "title": "<concise document title, max 80 characters>"\n'
    '}}'
)


class GemmaNamer:
    """Generates bibliographic metadata from document text using Gemma 3 4B.

    Uses a MapReduce chunking strategy for robust multi-page summarization:
    - Documents are split into overlapping chunks
    - Each chunk is summarized independently (Map)
    - Summaries are combined into a single abstract (Reduce)
    - Metadata is extracted from the abstract for filename generation
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
    ):
        self.model = model
        self.client = OpenAI(
            base_url=base_url,
            api_key="ollama",  # Ollama doesn't require a real key
        )

    def generate_metadata(self, text: str) -> dict:
        """Full pipeline: chunk → map → reduce → extract metadata.

        Args:
            text: Raw text extracted from a PDF or EPUB (up to ~48K chars).

        Returns:
            Dict with keys: doc_type, authors, year, title
            Returns fallback metadata on any failure.
        """
        if not text or not text.strip():
            return _fallback_metadata()

        try:
            # Step 1: Chunk the text
            chunks = self._chunk_text(text)
            logger.info("Split text into %d chunks", len(chunks))

            # Step 2: Map — summarize each chunk
            chunk_summaries = []
            for i, chunk in enumerate(chunks):
                logger.info("Summarizing chunk %d/%d", i + 1, len(chunks))
                summary = self._summarize_chunk(chunk)
                if summary:
                    chunk_summaries.append(summary)

            if not chunk_summaries:
                logger.warning("No chunk summaries produced, using raw text")
                # Fall back to using truncated raw text directly
                abstract = text[:4000]
            elif len(chunk_summaries) == 1:
                # Single chunk — no need to reduce
                abstract = chunk_summaries[0]
            else:
                # Step 3: Reduce — combine summaries
                abstract = self._combine_summaries(chunk_summaries)

            if not abstract:
                abstract = text[:4000]

            # Step 4: Extract metadata from abstract
            metadata = self._extract_metadata(abstract)
            return metadata

        except Exception:
            logger.exception("Metadata generation failed")
            return _fallback_metadata()

    def _chunk_text(
        self,
        text: str,
        chunk_size: int = CHUNK_SIZE,
        overlap: int = CHUNK_OVERLAP,
    ) -> list[str]:
        """Split text into overlapping chunks at sentence boundaries.

        Uses a sliding window with overlap to preserve context between
        chunks. Tries to break at sentence boundaries (periods followed
        by whitespace) to avoid splitting mid-sentence.
        """
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = start + chunk_size

            if end >= len(text):
                # Last chunk — take everything remaining
                chunk = text[start:]
                if len(chunk) >= MIN_CHUNK_SIZE or not chunks:
                    chunks.append(chunk)
                else:
                    # Too small — append to previous chunk
                    chunks[-1] += " " + chunk
                break

            # Try to break at a sentence boundary (look backwards from end)
            # Search for ". " or ".\n" in the last 20% of the chunk
            search_start = end - int(chunk_size * 0.2)
            segment = text[search_start:end]

            # Find the last sentence-ending punctuation
            best_break = -1
            for pattern in [". ", ".\n", "? ", "?\n", "! ", "!\n"]:
                pos = segment.rfind(pattern)
                if pos > best_break:
                    best_break = pos

            if best_break > 0:
                end = search_start + best_break + 1  # Include the punctuation

            chunks.append(text[start:end].strip())

            # Advance with overlap
            start = end - overlap
            if start <= (end - chunk_size):
                # Safety: ensure forward progress
                start = end

        return chunks

    def _summarize_chunk(self, chunk: str) -> str:
        """Map step: summarize a single text chunk via Gemma 3 4B."""
        return self._llm_call(
            system_prompt=MAP_SYSTEM_PROMPT,
            user_prompt=MAP_USER_PROMPT.format(chunk_text=chunk),
            max_tokens=SUMMARY_MAX_TOKENS,
        )

    def _combine_summaries(self, summaries: list[str]) -> str:
        """Reduce step: combine chunk summaries into a single abstract."""
        numbered = "\n\n".join(
            f"[Section {i + 1}]: {s}" for i, s in enumerate(summaries)
        )
        return self._llm_call(
            system_prompt=REDUCE_SYSTEM_PROMPT,
            user_prompt=REDUCE_USER_PROMPT.format(combined_summaries=numbered),
            max_tokens=SUMMARY_MAX_TOKENS,
        )

    def _extract_metadata(self, abstract: str) -> dict:
        """Extract structured bibliographic metadata from an abstract."""
        response = self._llm_call(
            system_prompt=METADATA_SYSTEM_PROMPT,
            user_prompt=METADATA_USER_PROMPT.format(abstract=abstract),
            max_tokens=METADATA_MAX_TOKENS,
        )

        if not response:
            return _fallback_metadata()

        return _parse_metadata_json(response)

    def _llm_call(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> str:
        """Make a single LLM call with retry logic.

        Uses exponential backoff on failures (network errors,
        Ollama not responding, etc.). Returns empty string on
        exhausting all retries.
        """
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=SUMMARY_TEMPERATURE,
                    max_tokens=max_tokens,
                )
                content = response.choices[0].message.content
                return content.strip() if content else ""

            except Exception as e:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s. Retrying in %.1fs",
                    attempt + 1, MAX_RETRIES, e, delay,
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)

        logger.error("All %d LLM call attempts failed", MAX_RETRIES)
        return ""


def _parse_metadata_json(response: str) -> dict:
    """Parse JSON metadata from LLM response, handling common formatting issues.

    The LLM may wrap JSON in markdown fences, add explanatory text, or
    produce slightly malformed JSON. This function handles all those cases.
    """
    # Strip markdown code fences if present
    cleaned = response.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    # Try to extract JSON object if there's surrounding text
    json_match = re.search(r"\{[^{}]*\}", cleaned, re.DOTALL)
    if json_match:
        cleaned = json_match.group(0)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Failed to parse metadata JSON: %s", cleaned[:200])
        return _fallback_metadata()

    # Validate and normalize fields
    return {
        "doc_type": _validate_doc_type(data.get("doc_type", "unknown")),
        "authors": _validate_authors(data.get("authors", [])),
        "year": _validate_year(data.get("year", "")),
        "title": str(data.get("title", "Untitled")).strip()[:80],
    }


def _validate_doc_type(doc_type: str) -> str:
    """Ensure doc_type is one of the expected values."""
    valid_types = {
        "journal_article", "book", "book_chapter", "thesis",
        "manuscript", "conference_paper", "report", "unknown",
    }
    doc_type = str(doc_type).strip().lower()
    return doc_type if doc_type in valid_types else "unknown"


def _validate_authors(authors) -> list[str]:
    """Ensure authors is a list of non-empty strings."""
    if not isinstance(authors, list):
        return []
    cleaned = []
    for a in authors:
        name = str(a).strip()
        if name and name.lower() not in ("unknown", "n/a", "none", ""):
            cleaned.append(name)
    return cleaned


def _validate_year(year) -> str:
    """Ensure year is a plausible 4-digit year string."""
    year_str = str(year).strip()
    match = re.search(r"\b(1[5-9]\d{2}|20[0-3]\d)\b", year_str)
    return match.group(1) if match else ""


def _fallback_metadata() -> dict:
    """Return default metadata when extraction fails."""
    return {
        "doc_type": "unknown",
        "authors": [],
        "year": "",
        "title": "Untitled",
    }
