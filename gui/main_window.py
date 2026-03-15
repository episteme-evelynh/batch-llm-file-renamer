"""Main application window for the PDF/EPUB Counter & AI Renamer.

Files are renamed automatically as each one is processed. No batch preview
dialog is shown. If a file fails (after the worker auto-troubleshoots), a
blocking error dialog appears with partial metadata and a custom name input.
"""

import logging
import os

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.renamer import RenamerWorker
from core.scanner import ScannerWorker
from gui.rename_dialog import ErrorRenameDialog, apply_rename

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Primary GUI window for scanning, counting, and AI-renaming files.

    Layout:
    ┌─────────────────────────────────────────────┐
    │  Directory: [_______________] [Browse]       │
    │  Ollama URL: [localhost:11434/v1]            │
    │  Model: [gemma3:4b-it]                      │
    │  Mendeley Client ID: [...]                  │
    │  Mendeley Secret: [...]                     │
    ├─────────────────────────────────────────────┤
    │  [Scan for Files]    [Cancel Scan]           │
    │  ████████████████░░░░░░░░  (progress bar)   │
    │  Current: /path/to/current/file.pdf          │
    │  Found: 42 PDF/EPUB files                    │
    ├─────────────────────────────────────────────┤
    │  [Rename Files]  [Cancel]                    │
    │  ████████████████░░░░░░░░  (progress bar)   │
    │  Status: AI processing file.pdf              │
    │  Renamed: 12 / 42 | Errors: 0               │
    └─────────────────────────────────────────────┘
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF/EPUB Counter & AI Renamer")
        self.setMinimumSize(700, 520)

        self._settings = QSettings("BatchLLMRenamer", "BatchLLMRenamer")
        self._scanner_worker = None
        self._renamer_worker = None
        self._found_files: list[str] = []
        self._rename_success = 0
        self._rename_errors = 0
        self._rename_skipped = 0

        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # --- Configuration Section ---
        config_group = QGroupBox("Configuration")
        config_layout = QVBoxLayout(config_group)

        # Directory picker
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Directory:"))
        self.dir_input = QLineEdit()
        self.dir_input.setPlaceholderText("Select a folder to scan...")
        dir_row.addWidget(self.dir_input)
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.clicked.connect(self._browse_directory)
        dir_row.addWidget(self.browse_btn)
        config_layout.addLayout(dir_row)

        # Ollama URL
        ollama_row = QHBoxLayout()
        ollama_row.addWidget(QLabel("Ollama URL:"))
        self.ollama_url_input = QLineEdit()
        self.ollama_url_input.setPlaceholderText("http://localhost:11434/v1")
        ollama_row.addWidget(self.ollama_url_input)
        config_layout.addLayout(ollama_row)

        # Model selector
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("gemma3:4b-it")
        model_row.addWidget(self.model_input)
        config_layout.addLayout(model_row)

        # Mendeley credentials (for bibtex-gen DOI lookup)
        mendeley_id_row = QHBoxLayout()
        mendeley_id_row.addWidget(QLabel("Mendeley Client ID:"))
        self.mendeley_id_input = QLineEdit()
        self.mendeley_id_input.setPlaceholderText("(optional) for DOI → BibTeX lookup")
        mendeley_id_row.addWidget(self.mendeley_id_input)
        config_layout.addLayout(mendeley_id_row)

        mendeley_secret_row = QHBoxLayout()
        mendeley_secret_row.addWidget(QLabel("Mendeley Secret:"))
        self.mendeley_secret_input = QLineEdit()
        self.mendeley_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.mendeley_secret_input.setPlaceholderText("(optional) for DOI → BibTeX lookup")
        mendeley_secret_row.addWidget(self.mendeley_secret_input)
        config_layout.addLayout(mendeley_secret_row)

        main_layout.addWidget(config_group)

        # --- Scan Section ---
        scan_group = QGroupBox("File Scanner")
        scan_layout = QVBoxLayout(scan_group)

        scan_btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan for Files")
        self.scan_btn.clicked.connect(self._start_scan)
        scan_btn_row.addWidget(self.scan_btn)

        self.cancel_scan_btn = QPushButton("Cancel Scan")
        self.cancel_scan_btn.setEnabled(False)
        self.cancel_scan_btn.clicked.connect(self._cancel_scan)
        scan_btn_row.addWidget(self.cancel_scan_btn)

        scan_btn_row.addStretch()
        scan_layout.addLayout(scan_btn_row)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)  # Indeterminate by default
        self.scan_progress.setVisible(False)
        scan_layout.addWidget(self.scan_progress)

        self.scan_file_label = QLabel("")
        self.scan_file_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.scan_file_label.setWordWrap(False)
        scan_layout.addWidget(self.scan_file_label)

        self.scan_count_label = QLabel("Found: 0 PDF/EPUB files")
        self.scan_count_label.setStyleSheet("font-weight: bold;")
        scan_layout.addWidget(self.scan_count_label)

        main_layout.addWidget(scan_group)

        # --- AI Rename Section ---
        rename_group = QGroupBox("AI Rename (Gemma 3 4B via Ollama)")
        rename_layout = QVBoxLayout(rename_group)

        rename_btn_row = QHBoxLayout()
        self.rename_btn = QPushButton("Rename Files")
        self.rename_btn.setEnabled(False)
        self.rename_btn.clicked.connect(self._start_rename)
        rename_btn_row.addWidget(self.rename_btn)

        self.cancel_rename_btn = QPushButton("Cancel")
        self.cancel_rename_btn.setEnabled(False)
        self.cancel_rename_btn.clicked.connect(self._cancel_rename)
        rename_btn_row.addWidget(self.cancel_rename_btn)

        rename_btn_row.addStretch()
        rename_layout.addLayout(rename_btn_row)

        self.rename_progress = QProgressBar()
        self.rename_progress.setRange(0, 100)
        self.rename_progress.setVisible(False)
        rename_layout.addWidget(self.rename_progress)

        self.rename_status_label = QLabel("")
        self.rename_status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.rename_status_label.setWordWrap(False)
        rename_layout.addWidget(self.rename_status_label)

        self.rename_count_label = QLabel("")
        self.rename_count_label.setStyleSheet("font-weight: bold;")
        rename_layout.addWidget(self.rename_count_label)

        main_layout.addWidget(rename_group)

        # Stretch at bottom
        main_layout.addStretch()

    def _load_settings(self):
        """Load persisted settings."""
        url = self._settings.value("ollama_url", "http://localhost:11434/v1")
        model = self._settings.value("model", "gemma3:4b-it")
        self.ollama_url_input.setText(url)
        self.model_input.setText(model)
        self.mendeley_id_input.setText(
            self._settings.value("mendeley_client_id", "")
        )
        self.mendeley_secret_input.setText(
            self._settings.value("mendeley_client_secret", "")
        )

    def _save_settings(self):
        """Persist current settings."""
        self._settings.setValue("ollama_url", self._get_ollama_url())
        self._settings.setValue("model", self._get_model())
        self._settings.setValue(
            "mendeley_client_id", self.mendeley_id_input.text().strip()
        )
        self._settings.setValue(
            "mendeley_client_secret", self.mendeley_secret_input.text().strip()
        )

    def _get_ollama_url(self) -> str:
        url = self.ollama_url_input.text().strip()
        return url or "http://localhost:11434/v1"

    def _get_model(self) -> str:
        model = self.model_input.text().strip()
        return model or "gemma3:4b-it"

    # --- Directory Browsing ---

    def _browse_directory(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Directory to Scan"
        )
        if path:
            self.dir_input.setText(path)

    # --- Scan Controls ---

    def _start_scan(self):
        scan_dir = self.dir_input.text().strip()
        if not scan_dir or not os.path.isdir(scan_dir):
            QMessageBox.warning(
                self, "Invalid Directory",
                "Please select a valid directory to scan.",
            )
            return

        # Reset state
        self._found_files = []
        self.scan_count_label.setText("Found: 0 PDF/EPUB files")
        self.scan_file_label.setText("Scanning...")
        self.scan_progress.setRange(0, 0)  # Indeterminate
        self.scan_progress.setVisible(True)
        self.scan_btn.setEnabled(False)
        self.cancel_scan_btn.setEnabled(True)
        self.rename_btn.setEnabled(False)

        self._scanner_worker = ScannerWorker(scan_dir)
        self._scanner_worker.file_found.connect(self._on_file_found)
        self._scanner_worker.count_updated.connect(self._on_scan_count)
        self._scanner_worker.finished_scan.connect(self._on_scan_finished)
        self._scanner_worker.error.connect(self._on_scan_error)
        self._scanner_worker.start()

    def _cancel_scan(self):
        if self._scanner_worker and self._scanner_worker.isRunning():
            self._scanner_worker.requestInterruption()

    def _on_file_found(self, filepath: str):
        display = filepath
        if len(display) > 80:
            display = "..." + display[-77:]
        self.scan_file_label.setText(f"Current: {display}")

    def _on_scan_count(self, count: int):
        self.scan_count_label.setText(f"Found: {count} PDF/EPUB files")

    def _on_scan_finished(self, files: list):
        self._found_files = files
        self.scan_progress.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.cancel_scan_btn.setEnabled(False)
        self.scan_file_label.setText("Scan complete.")
        self.scan_count_label.setText(
            f"Found: {len(files)} PDF/EPUB files"
        )
        self.rename_btn.setEnabled(len(files) > 0)

    def _on_scan_error(self, error_msg: str):
        QMessageBox.warning(self, "Scan Error", error_msg)

    # --- AI Rename Controls ---

    def _start_rename(self):
        if not self._found_files:
            QMessageBox.information(
                self, "No Files", "No files to process. Run a scan first."
            )
            return

        self._save_settings()

        # Reset counters
        self._rename_success = 0
        self._rename_errors = 0
        self._rename_skipped = 0

        # Reset rename UI
        total = len(self._found_files)
        self.rename_progress.setRange(0, total)
        self.rename_progress.setValue(0)
        self.rename_progress.setVisible(True)
        self.rename_status_label.setText("Starting AI processing...")
        self.rename_count_label.setText(f"Processed: 0 / {total}")
        self.rename_btn.setEnabled(False)
        self.cancel_rename_btn.setEnabled(True)
        self.scan_btn.setEnabled(False)

        self._renamer_worker = RenamerWorker(
            files=self._found_files,
            ollama_url=self._get_ollama_url(),
            model=self._get_model(),
            mendeley_client_id=self.mendeley_id_input.text().strip(),
            mendeley_client_secret=self.mendeley_secret_input.text().strip(),
        )
        self._renamer_worker.progress.connect(self._on_rename_progress)
        self._renamer_worker.status_update.connect(self._on_status_update)
        self._renamer_worker.proposal_ready.connect(self._on_proposal_ready)
        self._renamer_worker.finished_all.connect(self._on_all_finished)
        self._renamer_worker.start()

    def _cancel_rename(self):
        if self._renamer_worker and self._renamer_worker.isRunning():
            self._renamer_worker.requestInterruption()

    def _on_rename_progress(self, current: int, total: int):
        self.rename_progress.setValue(current)

    def _on_status_update(self, status: str):
        """Real-time status from the worker (extraction, troubleshooting, AI)."""
        display = status
        if len(display) > 90:
            display = display[:87] + "..."
        self.rename_status_label.setText(display)

    def _on_proposal_ready(self, proposal):
        """Handle each file as it completes — auto-rename or show error dialog.

        Called once per file from the worker thread's signal. Successful
        proposals are renamed immediately. Errors trigger a blocking dialog.
        """
        total = len(self._found_files)

        if not proposal.error:
            # --- Success: auto-rename, no dialog ---
            try:
                apply_rename(proposal.original_path, proposal.proposed_name)
                self._rename_success += 1
            except OSError as e:
                # Rename itself failed (permissions, disk full, etc.)
                logger.error(
                    "Auto-rename failed for %s: %s", proposal.original_path, e
                )
                self._handle_error_proposal(proposal, f"Rename failed: {e}")
                return
        else:
            # --- Error: show blocking dialog ---
            self._handle_error_proposal(proposal, proposal.error)

        self._update_rename_counts(total)

    def _handle_error_proposal(self, proposal, error_msg: str):
        """Show the error dialog with metadata and a custom name input."""
        dialog = ErrorRenameDialog(
            original_path=proposal.original_path,
            error_msg=error_msg,
            metadata=proposal.metadata,
            parent=self,
        )

        if dialog.exec() == ErrorRenameDialog.DialogCode.Accepted:
            # User provided a custom name
            try:
                apply_rename(proposal.original_path, dialog.custom_name)
                self._rename_success += 1
            except OSError as e:
                logger.error(
                    "Custom rename failed for %s: %s",
                    proposal.original_path, e,
                )
                QMessageBox.critical(
                    self, "Rename Failed",
                    f"Could not rename file:\n{e}",
                )
                self._rename_errors += 1
        else:
            # User clicked Skip
            self._rename_skipped += 1

    def _update_rename_counts(self, total: int):
        """Update the summary label below the progress bar."""
        processed = self._rename_success + self._rename_errors + self._rename_skipped
        parts = [f"Processed: {processed} / {total}"]
        if self._rename_success:
            parts.append(f"Renamed: {self._rename_success}")
        if self._rename_errors:
            parts.append(f"Errors: {self._rename_errors}")
        if self._rename_skipped:
            parts.append(f"Skipped: {self._rename_skipped}")
        self.rename_count_label.setText(" | ".join(parts))

    def _on_all_finished(self, success_count: int, error_count: int):
        """Called when the worker has finished processing all files."""
        self.rename_progress.setVisible(False)
        self.rename_btn.setEnabled(True)
        self.cancel_rename_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)

        total = self._rename_success + self._rename_errors + self._rename_skipped
        self.rename_status_label.setText("Complete.")
        self._update_rename_counts(len(self._found_files))

        # Summary message
        msg_parts = [f"Renamed: {self._rename_success}"]
        if self._rename_skipped:
            msg_parts.append(f"Skipped: {self._rename_skipped}")
        if self._rename_errors:
            msg_parts.append(f"Errors: {self._rename_errors}")

        QMessageBox.information(
            self, "Rename Complete",
            f"Finished processing {total} file(s).\n" + "\n".join(msg_parts),
        )

    def closeEvent(self, event):
        """Clean up workers on window close."""
        if self._scanner_worker and self._scanner_worker.isRunning():
            self._scanner_worker.requestInterruption()
            self._scanner_worker.wait(3000)
        if self._renamer_worker and self._renamer_worker.isRunning():
            self._renamer_worker.requestInterruption()
            self._renamer_worker.wait(3000)
        self._save_settings()
        super().closeEvent(event)
