"""BibTeX generation and cleaning for each renamed file.

Uses bibtex-gen (DOI → BibTeX via Mendeley) and bibtex-cleaner (normalize/clean)
to produce a .bib sidecar file alongside each renamed document. The service-provided
title from the DOI lookup is combined with the LLM-distilled title for the final
filename, following Zotero naming conventions.
"""

import logging
import os
import re
import subprocess
import tempfile

logger = logging.getLogger(__name__)


def _try_bibtex_gen(doi: str, citation_key: str, client_id: str, client_secret: str) -> str:
    """Use bibtex-gen to generate a BibTeX entry from a DOI via Mendeley.

    Returns raw BibTeX string, or empty string on failure.
    """
    if not doi or not client_id or not client_secret:
        return ""

    try:
        import bibtex_gen

        btg = bibtex_gen.BibTexGenerator(client_id, client_secret)
        bib_obj = btg.generate(doi, citation_key)
        if bib_obj is not None:
            return str(bib_obj)
    except Exception as e:
        logger.warning("bibtex-gen lookup failed for DOI %s: %s", doi, e)

    return ""


def _try_bibtex_cleaner(bib_content: str) -> str:
    """Use bibtex-cleaner CLI to clean/normalize a BibTeX string.

    Runs the bibtex-cleaner command as a subprocess. Falls back to
    the original content if the tool is not available or fails.
    """
    if not bib_content.strip():
        return bib_content

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".bib", delete=False
        ) as infile:
            infile.write(bib_content)
            in_path = infile.name

        out_path = in_path + ".cleaned.bib"

        result = subprocess.run(
            ["bibtex-cleaner", in_path, out_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0 and os.path.exists(out_path):
            with open(out_path) as f:
                cleaned = f.read()
            if cleaned.strip():
                return cleaned

    except FileNotFoundError:
        logger.info("bibtex-cleaner CLI not found; skipping cleaning step")
    except subprocess.TimeoutExpired:
        logger.warning("bibtex-cleaner timed out")
    except Exception as e:
        logger.warning("bibtex-cleaner failed: %s", e)
    finally:
        # Clean up temp files
        for path in [in_path, out_path]:
            try:
                os.unlink(path)
            except OSError:
                pass

    return bib_content


def _extract_title_from_bibtex(bib_content: str) -> str:
    """Extract the title field from a BibTeX entry string.

    Handles both title = {Some Title} and title = "Some Title" forms.
    """
    if not bib_content:
        return ""

    # Match title = {content} or title = "content"
    match = re.search(
        r"title\s*=\s*[{\"](.+?)[}\"]",
        bib_content,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        title = match.group(1).strip()
        # Remove BibTeX braces used for capitalization preservation
        title = title.replace("{", "").replace("}", "")
        return title

    return ""


def _build_bibtex_from_metadata(metadata: dict, citation_key: str) -> str:
    """Build a minimal BibTeX entry from AI-extracted metadata.

    Used as a fallback when bibtex-gen cannot resolve the DOI.
    """
    doc_type = metadata.get("doc_type", "unknown")
    bib_type_map = {
        "journal_article": "article",
        "book": "book",
        "book_chapter": "inbook",
        "thesis": "phdthesis",
        "manuscript": "unpublished",
        "conference_paper": "inproceedings",
        "report": "techreport",
        "unknown": "misc",
    }
    bib_type = bib_type_map.get(doc_type, "misc")

    authors = metadata.get("authors", [])
    author_str = " and ".join(authors) if authors else "Unknown"
    year = metadata.get("year", "")
    title = metadata.get("title", "Untitled")
    doi = metadata.get("doi", "")
    isbn = metadata.get("isbn", "")

    lines = [f"@{bib_type}{{{citation_key},"]
    lines.append(f"  author = {{{author_str}}},")
    lines.append(f"  title = {{{title}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
    if isbn:
        lines.append(f"  isbn = {{{isbn}}},")
    lines.append("}")

    return "\n".join(lines)


def _make_citation_key(metadata: dict) -> str:
    """Generate a citation key from metadata: firstauthor_year."""
    authors = metadata.get("authors", [])
    first = authors[0] if authors else "unknown"
    # Keep only alphanumeric chars from the last name
    first = re.sub(r"[^a-zA-Z]", "", first).lower()
    year = metadata.get("year", "")
    return f"{first}_{year}" if year else first


def generate_bib_file(
    metadata: dict,
    output_dir: str,
    base_filename: str,
    mendeley_client_id: str = "",
    mendeley_client_secret: str = "",
) -> tuple[str, str]:
    """Generate a .bib sidecar file for a document.

    Pipeline:
    1. If DOI present, try bibtex-gen to get service-provided BibTeX
    2. Fall back to building BibTeX from AI metadata
    3. Clean the result with bibtex-cleaner
    4. Write .bib file alongside the document
    5. Return (bib_filepath, service_title)

    Args:
        metadata: AI-extracted metadata dict with doc_type, authors, year,
                  title, doi, isbn keys.
        output_dir: Directory where the .bib file will be written.
        base_filename: Filename stem (no extension) for the .bib file.
        mendeley_client_id: Mendeley API client ID for bibtex-gen.
        mendeley_client_secret: Mendeley API client secret for bibtex-gen.

    Returns:
        Tuple of (bib_filepath, service_title). service_title is the title
        retrieved from the DOI service, or empty string if unavailable.
    """
    citation_key = _make_citation_key(metadata)
    doi = metadata.get("doi", "")
    service_title = ""

    # Step 1: Try bibtex-gen for DOI-based lookup
    service_bib = _try_bibtex_gen(
        doi, citation_key, mendeley_client_id, mendeley_client_secret
    )

    if service_bib:
        service_title = _extract_title_from_bibtex(service_bib)
        bib_content = service_bib
    else:
        # Fall back to metadata-based BibTeX
        bib_content = _build_bibtex_from_metadata(metadata, citation_key)

    # Step 2: Clean with bibtex-cleaner
    bib_content = _try_bibtex_cleaner(bib_content)

    # Step 3: Write .bib file
    # Sanitize the base filename for the .bib file
    safe_base = re.sub(r"[^\w\s\-.]", "_", base_filename)
    bib_filename = safe_base + ".bib"
    bib_path = os.path.join(output_dir, bib_filename)

    try:
        with open(bib_path, "w", encoding="utf-8") as f:
            f.write(bib_content)
        logger.info("Wrote .bib file: %s", bib_path)
    except OSError as e:
        logger.error("Failed to write .bib file %s: %s", bib_path, e)
        bib_path = ""

    return bib_path, service_title


def combine_titles(service_title: str, llm_title: str) -> str:
    """Combine the service-provided title with the LLM-distilled title.

    If both titles are available and meaningfully different, appends the
    LLM title to the service title. If they are substantially the same
    (one contains the other), uses the longer one to avoid redundancy.

    The combined title is used in the final macOS filename string,
    following Zotero naming norms.
    """
    service_title = service_title.strip() if service_title else ""
    llm_title = llm_title.strip() if llm_title else ""

    if not service_title:
        return llm_title
    if not llm_title:
        return service_title

    # Check if one title substantially contains the other (case-insensitive)
    s_lower = service_title.lower()
    l_lower = llm_title.lower()

    if s_lower in l_lower:
        return llm_title
    if l_lower in s_lower:
        return service_title

    # Both titles are distinct — combine them with a separator
    # Format: "Service Title — LLM Title" (em-dash separator for Zotero readability)
    combined = f"{service_title} — {llm_title}"

    # Cap at 120 chars to leave room for author-year prefix in the filename
    if len(combined) > 120:
        # Truncate the LLM portion at a word boundary
        max_llm = 120 - len(service_title) - 3  # 3 for " — "
        if max_llm > 10:
            truncated = llm_title[:max_llm]
            last_space = truncated.rfind(" ")
            if last_space > 10:
                truncated = truncated[:last_space]
            combined = f"{service_title} — {truncated}"
        else:
            # Service title alone is already very long; just use it
            combined = service_title

    return combined
