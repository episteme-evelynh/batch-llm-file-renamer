"""Build macOS-safe, Zotero-friendly filenames from AI-extracted metadata."""

import re
import unicodedata

# Maximum filename length on macOS (HFS+/APFS) in bytes
MAX_FILENAME_BYTES = 255

# Characters forbidden on macOS: / (path separator), : (legacy Mac separator), \0 (null)
FORBIDDEN_CHARS = re.compile(r"[/:\x00]")

# Unicode replacements for common typographic characters
UNICODE_REPLACEMENTS = {
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote
    "\u201c": '"',   # left double quote
    "\u201d": '"',   # right double quote
    "\u2013": "-",   # en dash
    "\u2014": "-",   # em dash
    "\u2026": "...", # ellipsis
    "\u00a0": " ",   # non-breaking space
    "\u200b": "",    # zero-width space
    "\u200c": "",    # zero-width non-joiner
    "\u200d": "",    # zero-width joiner
    "\ufeff": "",    # BOM / zero-width no-break space
    "\u00ad": "",    # soft hyphen
    "\u2012": "-",   # figure dash
    "\u2015": "-",   # horizontal bar
    "\uff0d": "-",   # fullwidth hyphen-minus
    "\u00b7": ".",   # middle dot
    "\u2022": "-",   # bullet
    "\u00d7": "x",   # multiplication sign
}


def sanitize_filename(name: str, extension: str) -> str:
    """Sanitize a proposed filename for macOS compatibility.

    - Replaces forbidden characters (/, :, null) with hyphens
    - Normalizes problematic Unicode to ASCII equivalents
    - Strips leading dots (hidden files on macOS)
    - Collapses redundant whitespace and dashes
    - Truncates to 255 bytes (UTF-8) including extension
    """
    # Apply Unicode replacements
    for char, replacement in UNICODE_REPLACEMENTS.items():
        name = name.replace(char, replacement)

    # Normalize Unicode to NFC (macOS standard)
    name = unicodedata.normalize("NFC", name)

    # Replace forbidden characters
    name = FORBIDDEN_CHARS.sub("-", name)

    # Strip leading dots
    name = name.lstrip(".")

    # Collapse multiple spaces into one
    name = re.sub(r"\s+", " ", name)

    # Collapse multiple consecutive dashes
    name = re.sub(r"-{2,}", "-", name)

    # Strip leading/trailing whitespace and dashes
    name = name.strip(" -")

    if not name:
        name = "Untitled"

    # Ensure extension starts with dot
    if extension and not extension.startswith("."):
        extension = "." + extension

    # Truncate name so total filename (name + extension) fits in 255 bytes
    ext_bytes = len(extension.encode("utf-8"))
    max_name_bytes = MAX_FILENAME_BYTES - ext_bytes

    encoded = name.encode("utf-8")
    if len(encoded) > max_name_bytes:
        # Truncate at character boundaries
        while len(name.encode("utf-8")) > max_name_bytes:
            name = name[:-1]
        name = name.rstrip(" -")

    return name + extension


def build_zotero_filename(metadata: dict, extension: str) -> str:
    """Build a Zotero-compatible filename from AI-extracted metadata.

    Follows Zotero's default naming convention:
      - Journal article (3+ authors): "LastName et al. - Year - Title.ext"
      - Journal article (2 authors):  "LastName, LastName - Year - Title.ext"
      - Journal article (1 author):   "LastName - Year - Title.ext"
      - Book:                         "LastName - Year - Title.ext"
      - Book chapter:                 "LastName - Year - Chapter Title.ext"
      - Thesis/manuscript/report:     "LastName - Year - Title.ext"
      - Unknown:                      "Title.ext"
    """
    doc_type = metadata.get("doc_type", "unknown")
    authors = metadata.get("authors", [])
    year = metadata.get("year", "")
    title = metadata.get("title", "Untitled")

    # Clean up title — remove trailing periods, excessive whitespace
    title = title.strip().rstrip(".")
    title = re.sub(r"\s+", " ", title)

    # Truncate overly long titles (keep under 80 chars for readability)
    if len(title) > 80:
        # Try to break at a word boundary
        truncated = title[:77]
        last_space = truncated.rfind(" ")
        if last_space > 40:
            title = truncated[:last_space]
        else:
            title = truncated

    # Build author string
    author_str = _format_authors(authors)

    # Assemble filename parts
    parts = []
    if author_str and doc_type != "unknown":
        parts.append(author_str)
    if year:
        parts.append(year)
    parts.append(title)

    name = " - ".join(parts)
    return sanitize_filename(name, extension)


def _format_authors(authors: list[str]) -> str:
    """Format author list for Zotero filename convention."""
    if not authors:
        return ""

    # Clean author names
    cleaned = [a.strip() for a in authors if a.strip()]
    if not cleaned:
        return ""

    if len(cleaned) == 1:
        return cleaned[0]
    elif len(cleaned) == 2:
        return f"{cleaned[0]}, {cleaned[1]}"
    else:
        return f"{cleaned[0]} et al."
