"""Extract text from PDF and EPUB files for LLM summarization."""

import re

import fitz  # PyMuPDF
import ebooklib
from ebooklib import epub
from lxml import etree


MAX_CHARS = 48_000  # ~12K tokens, fits well within Gemma 3's 128K context


def extract_pdf_text(filepath: str, max_pages: int = 15) -> str:
    """Extract text from the first `max_pages` pages of a PDF.

    Uses PyMuPDF for fast, accurate text extraction including
    multi-column layouts and embedded fonts.
    """
    doc = fitz.open(filepath)
    pages_to_read = min(len(doc), max_pages)
    parts = []
    total_chars = 0

    for i in range(pages_to_read):
        page = doc[i]
        # Use "text" sort mode for reading-order extraction
        text = page.get_text("text")
        if text:
            parts.append(text)
            total_chars += len(text)
            if total_chars >= MAX_CHARS:
                break

    doc.close()
    full_text = "\n\n".join(parts)
    return full_text[:MAX_CHARS]


def extract_epub_text(filepath: str, max_items: int = 15) -> str:
    """Extract text from the first `max_items` document sections of an EPUB.

    Uses ebooklib to read the EPUB structure and lxml to strip HTML tags,
    producing clean plaintext for summarization.
    """
    book = epub.read_epub(filepath, options={"ignore_ncx": True})
    parts = []
    total_chars = 0
    items_read = 0

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        if items_read >= max_items:
            break

        html_content = item.get_content()
        try:
            tree = etree.fromstring(html_content, parser=etree.HTMLParser())
        except etree.XMLSyntaxError:
            continue

        # Extract all text nodes, stripping HTML
        text = etree.tostring(tree, method="text", encoding="unicode")
        text = _clean_whitespace(text)

        if text.strip():
            parts.append(text.strip())
            total_chars += len(text)
            items_read += 1
            if total_chars >= MAX_CHARS:
                break

    full_text = "\n\n".join(parts)
    return full_text[:MAX_CHARS]


def extract_text(filepath: str, max_pages: int = 15) -> str:
    """Auto-detect file type and extract text."""
    lower = filepath.lower()
    if lower.endswith(".pdf"):
        return extract_pdf_text(filepath, max_pages)
    elif lower.endswith(".epub"):
        return extract_epub_text(filepath, max_pages)
    else:
        raise ValueError(f"Unsupported file type: {filepath}")


def _clean_whitespace(text: str) -> str:
    """Collapse excessive whitespace while preserving paragraph breaks."""
    # Replace runs of 3+ newlines with double newline
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse runs of spaces/tabs within lines
    text = re.sub(r"[^\S\n]+", " ", text)
    return text.strip()
