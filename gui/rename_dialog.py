"""Error dialog shown only when a file fails AI renaming.

Successful renames happen automatically with no dialog. When a file
fails (after the worker has already retried/troubleshot), this blocking
dialog appears so the user can provide a custom filename. The extracted
metadata (however partial) is displayed alongside the input field.
"""

import logging
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class ErrorRenameDialog(QDialog):
    """Blocking dialog for files that failed the AI pipeline.

    Shows the error, displays whatever metadata was extracted,
    and lets the user type a custom filename or skip the file.

    Layout:
    ┌──────────────────────────────────────────────┐
    │  ⚠ Error renaming file                       │
    │                                              │
    │  File: /path/to/original.pdf                 │
    │  Error: No text could be extracted from file │
    │                                              │
    │  ── Extracted Metadata ──                    │
    │  Title:   Some Partial Title                 │
    │  Authors: Smith, Jones                       │
    │  Year:    2024                               │
    │  Type:    journal_article                    │
    │  DOI:     10.1234/example                    │
    │                                              │
    │  Custom filename:                            │
    │  [___________________________________.pdf]   │
    │                                              │
    │           [Skip]  [Rename]                   │
    └──────────────────────────────────────────────┘
    """

    def __init__(self, original_path: str, error_msg: str,
                 metadata: dict, parent=None):
        super().__init__(parent)
        self.original_path = original_path
        self.error_msg = error_msg
        self.metadata = metadata or {}
        self.custom_name = ""  # Set after dialog accepted

        self.setWindowTitle("Rename Error")
        self.setMinimumWidth(600)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Error header
        error_header = QLabel(
            '<span style="color: #cc4400; font-size: 14px; font-weight: bold;">'
            "⚠ Error renaming file</span>"
        )
        layout.addWidget(error_header)

        # File path
        basename = os.path.basename(self.original_path)
        file_label = QLabel(f"<b>File:</b> {basename}")
        file_label.setToolTip(self.original_path)
        file_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(file_label)

        # Error message
        err_label = QLabel(f"<b>Error:</b> {self.error_msg}")
        err_label.setWordWrap(True)
        err_label.setStyleSheet("color: #cc0000;")
        err_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(err_label)

        # Separator
        sep = QLabel("── Extracted Metadata ──")
        sep.setStyleSheet("color: #888; margin-top: 6px;")
        layout.addWidget(sep)

        # Metadata fields — show whatever was extracted (may be partial/empty)
        meta_fields = [
            ("Title", self.metadata.get("title", "")),
            ("Authors", ", ".join(self.metadata.get("authors", []))),
            ("Year", self.metadata.get("year", "")),
            ("Type", self.metadata.get("doc_type", "").replace("_", " ")),
            ("DOI", self.metadata.get("doi", "")),
            ("ISBN", self.metadata.get("isbn", "")),
        ]

        for label_text, value in meta_fields:
            display = value if value else "(not found)"
            style = "" if value else "color: #999; font-style: italic;"
            meta_label = QLabel(f"<b>{label_text}:</b> "
                                f'<span style="{style}">{display}</span>')
            meta_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(meta_label)

        # Separator before input
        layout.addSpacing(8)

        # Custom filename input
        input_label = QLabel("<b>Custom filename:</b>")
        layout.addWidget(input_label)

        _, ext = os.path.splitext(self.original_path)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(f"Type a filename (extension {ext} added automatically)")

        # Pre-fill with whatever partial name can be built from metadata
        prefill = self._build_prefill(ext)
        if prefill:
            self.name_input.setText(prefill)
            self.name_input.selectAll()

        layout.addWidget(self.name_input)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        skip_btn = QPushButton("Skip")
        skip_btn.clicked.connect(self.reject)
        btn_layout.addWidget(skip_btn)

        rename_btn = QPushButton("Rename")
        rename_btn.setDefault(True)
        rename_btn.clicked.connect(self._accept_rename)
        btn_layout.addWidget(rename_btn)

        layout.addLayout(btn_layout)

        # Focus the input
        self.name_input.setFocus()

    def _build_prefill(self, ext: str) -> str:
        """Try to build a partial Zotero-style name from whatever metadata exists."""
        parts = []

        authors = self.metadata.get("authors", [])
        if authors:
            if len(authors) == 1:
                parts.append(authors[0])
            elif len(authors) == 2:
                parts.append(f"{authors[0]}, {authors[1]}")
            else:
                parts.append(f"{authors[0]} et al.")

        year = self.metadata.get("year", "")
        if year:
            parts.append(year)

        title = self.metadata.get("title", "")
        if title and title != "Untitled":
            parts.append(title)

        if parts:
            return " - ".join(parts)
        return ""

    def _accept_rename(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(
                self, "Empty Name",
                "Please enter a filename, or click Skip to skip this file.",
            )
            return

        _, ext = os.path.splitext(self.original_path)
        # Add extension if user didn't include one
        if not name.lower().endswith(ext.lower()):
            name += ext

        self.custom_name = name
        self.accept()


def resolve_collision(path: str) -> str:
    """If path already exists, append (2), (3), etc. until unique."""
    if not os.path.exists(path):
        return path

    base, ext = os.path.splitext(path)
    counter = 2
    while True:
        candidate = f"{base} ({counter}){ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def apply_rename(original_path: str, new_name: str) -> str:
    """Rename a file and its .bib sidecar. Returns the new path.

    Handles collisions by appending (2), (3), etc.
    Raises OSError on failure.
    """
    directory = os.path.dirname(original_path)
    new_path = os.path.join(directory, new_name)
    new_path = resolve_collision(new_path)

    os.rename(original_path, new_path)
    logger.info("Renamed: %s -> %s", original_path, new_path)

    # Also rename .bib sidecar if it exists
    old_stem = os.path.splitext(os.path.basename(original_path))[0]
    new_stem = os.path.splitext(os.path.basename(new_path))[0]
    old_bib = os.path.join(directory, old_stem + ".bib")
    if os.path.exists(old_bib):
        new_bib = os.path.join(directory, new_stem + ".bib")
        new_bib = resolve_collision(new_bib)
        os.rename(old_bib, new_bib)
        logger.info("Renamed .bib: %s -> %s", old_bib, new_bib)

    return new_path
