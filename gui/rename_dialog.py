"""Preview dialog for reviewing and confirming AI-proposed file renames."""

import logging
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

# Table column indices
COL_CHECK = 0
COL_ORIGINAL = 1
COL_PROPOSED = 2
COL_TYPE = 3
COL_STATUS = 4


class RenamePreviewDialog(QDialog):
    """Dialog showing proposed renames in an editable table.

    Users can:
    - Review all proposed old → new filenames
    - Edit proposed names directly in the table
    - Select/deselect individual files via checkboxes
    - Use Select All / Deselect All buttons
    - Confirm to execute selected renames, or cancel

    No files are renamed until the user clicks "Rename Selected".
    Collision handling appends " (2)", " (3)", etc.
    """

    def __init__(self, proposals: list, parent=None):
        super().__init__(parent)
        self.proposals = proposals
        self.setWindowTitle("Review Proposed Renames")
        self.setMinimumSize(900, 500)
        self._build_ui()
        self._populate_table()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Header
        header = QLabel(
            f"<b>{len(self.proposals)}</b> files processed. "
            "Review proposed names below. You can edit names before confirming."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "", "Original Name", "Proposed Name", "Type", "Status",
        ])

        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(COL_ORIGINAL, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(COL_PROPOSED, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(COL_CHECK, 30)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)

        layout.addWidget(self.table)

        # Buttons row
        btn_layout = QHBoxLayout()

        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self._select_all)
        btn_layout.addWidget(self.select_all_btn)

        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.clicked.connect(self._deselect_all)
        btn_layout.addWidget(self.deselect_all_btn)

        btn_layout.addStretch()

        self.rename_btn = QPushButton("Rename Selected")
        self.rename_btn.setDefault(True)
        self.rename_btn.clicked.connect(self._execute_renames)
        btn_layout.addWidget(self.rename_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        layout.addLayout(btn_layout)

    def _populate_table(self):
        """Fill table with proposal data."""
        valid_proposals = [p for p in self.proposals if not p.error]
        error_proposals = [p for p in self.proposals if p.error]

        self.table.setRowCount(len(self.proposals))

        for row, proposal in enumerate(valid_proposals + error_proposals):
            # Checkbox
            checkbox = QCheckBox()
            checkbox.setChecked(not proposal.error)
            checkbox.setEnabled(not proposal.error)
            self.table.setCellWidget(row, COL_CHECK, checkbox)

            # Original name (read-only)
            original_name = os.path.basename(proposal.original_path)
            item = QTableWidgetItem(original_name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(proposal.original_path)
            self.table.setItem(row, COL_ORIGINAL, item)

            # Proposed name (editable for valid proposals)
            proposed = proposal.proposed_name if not proposal.error else ""
            proposed_item = QTableWidgetItem(proposed)
            if proposal.error:
                proposed_item.setFlags(
                    proposed_item.flags() & ~Qt.ItemFlag.ItemIsEditable
                )
            self.table.setItem(row, COL_PROPOSED, proposed_item)

            # Document type
            doc_type = proposal.metadata.get("doc_type", "") if not proposal.error else ""
            type_item = QTableWidgetItem(doc_type.replace("_", " ").title())
            type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, COL_TYPE, type_item)

            # Status
            status = "Ready" if not proposal.error else f"Error: {proposal.error}"
            status_item = QTableWidgetItem(status)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if proposal.error:
                status_item.setForeground(Qt.GlobalColor.red)
            self.table.setItem(row, COL_STATUS, status_item)

        # Store ordered proposals for rename execution
        self._ordered_proposals = valid_proposals + error_proposals

    def _select_all(self):
        for row in range(self.table.rowCount()):
            cb = self.table.cellWidget(row, COL_CHECK)
            if cb and cb.isEnabled():
                cb.setChecked(True)

    def _deselect_all(self):
        for row in range(self.table.rowCount()):
            cb = self.table.cellWidget(row, COL_CHECK)
            if cb:
                cb.setChecked(False)

    def _execute_renames(self):
        """Rename all selected files, handling collisions."""
        selected = []
        for row in range(self.table.rowCount()):
            cb = self.table.cellWidget(row, COL_CHECK)
            if cb and cb.isChecked():
                proposal = self._ordered_proposals[row]
                # Get the (possibly user-edited) proposed name from the table
                edited_name = self.table.item(row, COL_PROPOSED).text().strip()
                if edited_name:
                    selected.append((proposal.original_path, edited_name))

        if not selected:
            QMessageBox.information(self, "Nothing Selected", "No files selected for renaming.")
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Rename",
            f"Rename {len(selected)} file(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        success = 0
        errors = []

        for original_path, new_name in selected:
            try:
                directory = os.path.dirname(original_path)
                new_path = os.path.join(directory, new_name)

                # Handle collisions by appending (2), (3), etc.
                new_path = _resolve_collision(new_path)

                os.rename(original_path, new_path)
                success += 1
                logger.info("Renamed: %s -> %s", original_path, new_path)

                # Also rename the .bib sidecar if it exists
                old_stem = os.path.splitext(os.path.basename(original_path))[0]
                new_stem = os.path.splitext(os.path.basename(new_path))[0]
                old_bib = os.path.join(directory, old_stem + ".bib")
                if os.path.exists(old_bib):
                    new_bib = os.path.join(directory, new_stem + ".bib")
                    new_bib = _resolve_collision(new_bib)
                    os.rename(old_bib, new_bib)
                    logger.info("Renamed .bib: %s -> %s", old_bib, new_bib)

            except Exception as e:
                errors.append(f"{os.path.basename(original_path)}: {e}")
                logger.exception("Rename failed: %s", original_path)

        msg = f"Successfully renamed {success} file(s)."
        if errors:
            msg += f"\n\n{len(errors)} error(s):\n" + "\n".join(errors[:10])

        QMessageBox.information(self, "Rename Complete", msg)
        self.accept()


def _resolve_collision(path: str) -> str:
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
