"""Main application window for the PDF/EPUB Counter & AI Renamer.

Uses the QtConcurrent-style ``TaskRunner`` and ``MapRunner`` from
``core.concurrent`` instead of manual QThread subclasses.  Files are
renamed automatically as each one completes; a blocking error dialog
appears only on failure.
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

from core.concurrent import MapRunner, TaskRunner
from core.renamer import RenameConfig, process_file
from core.scanner import scan_directory
from gui.rename_dialog import ErrorRenameDialog, apply_rename

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Primary GUI window for scanning, counting, and AI-renaming files.

    Threading model (QtConcurrent-style):
    - Scanning uses ``TaskRunner`` (single function on the global pool).
    - Renaming uses ``MapRunner`` (``process_file`` mapped over each file,
      sequential by default so error dialogs don't overlap).
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF/EPUB Counter & AI Renamer")
        self.setMinimumSize(700, 520)

        self._settings = QSettings("BatchLLMRenamer", "BatchLLMRenamer")
        self._scan_runner: TaskRunner | None = None
        self._rename_runner: MapRunner | None = None
        self._found_files: list[str] = []
        self._rename_success = 0
        self._rename_errors = 0
        self._rename_skipped = 0

        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # --- Configuration ---
        config_group = QGroupBox("Configuration")
        config_layout = QVBoxLayout(config_group)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Directory:"))
        self.dir_input = QLineEdit()
        self.dir_input.setPlaceholderText("Select a folder to scan...")
        dir_row.addWidget(self.dir_input)
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.clicked.connect(self._browse_directory)
        dir_row.addWidget(self.browse_btn)
        config_layout.addLayout(dir_row)

        ollama_row = QHBoxLayout()
        ollama_row.addWidget(QLabel("Ollama URL:"))
        self.ollama_url_input = QLineEdit()
        self.ollama_url_input.setPlaceholderText("http://localhost:11434/v1")
        ollama_row.addWidget(self.ollama_url_input)
        config_layout.addLayout(ollama_row)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("gemma3:4b-it")
        model_row.addWidget(self.model_input)
        config_layout.addLayout(model_row)

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

        # --- Scanner ---
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
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setVisible(False)
        scan_layout.addWidget(self.scan_progress)

        self.scan_file_label = QLabel("")
        self.scan_file_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        self.scan_file_label.setWordWrap(False)
        scan_layout.addWidget(self.scan_file_label)

        self.scan_count_label = QLabel("Found: 0 PDF/EPUB files")
        self.scan_count_label.setStyleSheet("font-weight: bold;")
        scan_layout.addWidget(self.scan_count_label)

        main_layout.addWidget(scan_group)

        # --- AI Rename ---
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
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        self.rename_status_label.setWordWrap(False)
        rename_layout.addWidget(self.rename_status_label)

        self.rename_count_label = QLabel("")
        self.rename_count_label.setStyleSheet("font-weight: bold;")
        rename_layout.addWidget(self.rename_count_label)

        main_layout.addWidget(rename_group)
        main_layout.addStretch()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _load_settings(self):
        self.ollama_url_input.setText(
            self._settings.value("ollama_url", "http://localhost:11434/v1"),
        )
        self.model_input.setText(
            self._settings.value("model", "gemma3:4b-it"),
        )
        self.mendeley_id_input.setText(
            self._settings.value("mendeley_client_id", ""),
        )
        self.mendeley_secret_input.setText(
            self._settings.value("mendeley_client_secret", ""),
        )

    def _save_settings(self):
        self._settings.setValue("ollama_url", self._get_ollama_url())
        self._settings.setValue("model", self._get_model())
        self._settings.setValue(
            "mendeley_client_id", self.mendeley_id_input.text().strip(),
        )
        self._settings.setValue(
            "mendeley_client_secret", self.mendeley_secret_input.text().strip(),
        )

    def _get_ollama_url(self) -> str:
        return self.ollama_url_input.text().strip() or "http://localhost:11434/v1"

    def _get_model(self) -> str:
        return self.model_input.text().strip() or "gemma3:4b-it"

    # ------------------------------------------------------------------
    # Directory browse
    # ------------------------------------------------------------------

    def _browse_directory(self):
        path = QFileDialog.getExistingDirectory(self, "Select Directory to Scan")
        if path:
            self.dir_input.setText(path)

    # ------------------------------------------------------------------
    # Scan — TaskRunner (single background function)
    # ------------------------------------------------------------------

    def _start_scan(self):
        scan_dir = self.dir_input.text().strip()
        if not scan_dir or not os.path.isdir(scan_dir):
            QMessageBox.warning(
                self, "Invalid Directory",
                "Please select a valid directory to scan.",
            )
            return

        self._found_files = []
        self.scan_count_label.setText("Found: 0 PDF/EPUB files")
        self.scan_file_label.setText("Scanning...")
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setVisible(True)
        self.scan_btn.setEnabled(False)
        self.cancel_scan_btn.setEnabled(True)
        self.rename_btn.setEnabled(False)

        self._scan_runner = TaskRunner(parent=self)
        self._scan_runner.signals.progress.connect(self._on_scan_progress)
        self._scan_runner.signals.result.connect(self._on_scan_finished)
        self._scan_runner.signals.error.connect(self._on_scan_error)
        self._scan_runner.run(scan_directory, scan_dir)

    def _cancel_scan(self):
        if self._scan_runner:
            self._scan_runner.cancel()

    def _on_scan_progress(self, data):
        """Live progress from ``scan_directory`` (dict with file_found, count)."""
        filepath = data.get("file_found", "")
        count = data.get("count", 0)

        display = filepath
        if len(display) > 80:
            display = "..." + display[-77:]
        self.scan_file_label.setText(f"Current: {display}")
        self.scan_count_label.setText(f"Found: {count} PDF/EPUB files")

    def _on_scan_finished(self, files):
        """Scan complete — ``files`` is the returned list of paths."""
        self._found_files = files
        self.scan_progress.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.cancel_scan_btn.setEnabled(False)
        self.scan_file_label.setText("Scan complete.")
        self.scan_count_label.setText(f"Found: {len(files)} PDF/EPUB files")
        self.rename_btn.setEnabled(len(files) > 0)

    def _on_scan_error(self, error_msg):
        self.scan_progress.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.cancel_scan_btn.setEnabled(False)
        QMessageBox.warning(self, "Scan Error", error_msg)

    # ------------------------------------------------------------------
    # Rename — MapRunner (process_file mapped over each file)
    # ------------------------------------------------------------------

    def _start_rename(self):
        if not self._found_files:
            QMessageBox.information(
                self, "No Files", "No files to process. Run a scan first.",
            )
            return

        self._save_settings()

        self._rename_success = 0
        self._rename_errors = 0
        self._rename_skipped = 0

        total = len(self._found_files)
        self.rename_progress.setRange(0, total)
        self.rename_progress.setValue(0)
        self.rename_progress.setVisible(True)
        self.rename_status_label.setText("Starting AI processing...")
        self.rename_count_label.setText(f"Processed: 0 / {total}")
        self.rename_btn.setEnabled(False)
        self.cancel_rename_btn.setEnabled(True)
        self.scan_btn.setEnabled(False)

        config = RenameConfig(
            ollama_url=self._get_ollama_url(),
            model=self._get_model(),
            mendeley_client_id=self.mendeley_id_input.text().strip(),
            mendeley_client_secret=self.mendeley_secret_input.text().strip(),
        )

        # Sequential (max_threads=1) so error dialogs don't overlap
        self._rename_runner = MapRunner(max_threads=1, parent=self)
        self._rename_runner.signals.item_result.connect(self._on_file_result)
        self._rename_runner.signals.item_error.connect(self._on_file_error)
        self._rename_runner.signals.progress.connect(self._on_status_update)
        self._rename_runner.signals.all_finished.connect(self._on_all_finished)
        self._rename_runner.map(process_file, self._found_files, config=config)

    def _cancel_rename(self):
        if self._rename_runner:
            self._rename_runner.cancel()

    def _on_status_update(self, status):
        """Real-time status from the pipeline (string forwarded via progress)."""
        display = status if len(status) <= 90 else status[:87] + "..."
        self.rename_status_label.setText(display)

    def _on_file_result(self, index: int, proposal):
        """One file completed successfully or with an error proposal."""
        total = len(self._found_files)
        self.rename_progress.setValue(index + 1)

        if not proposal.error:
            # Auto-rename — no dialog
            try:
                apply_rename(proposal.original_path, proposal.proposed_name)
                self._rename_success += 1
            except OSError as e:
                logger.error(
                    "Auto-rename failed for %s: %s", proposal.original_path, e,
                )
                self._handle_error(proposal, f"Rename failed: {e}")
                self._update_counts(total)
                return
        else:
            self._handle_error(proposal, proposal.error)

        self._update_counts(total)

    def _on_file_error(self, index: int, error_msg: str):
        """Unhandled exception in the pipeline (shouldn't normally happen)."""
        total = len(self._found_files)
        self.rename_progress.setValue(index + 1)
        self._rename_errors += 1
        logger.error("Unhandled pipeline error for file %d: %s", index, error_msg)
        self._update_counts(total)

    def _handle_error(self, proposal, error_msg: str):
        """Show the blocking error dialog for manual naming or skip."""
        dialog = ErrorRenameDialog(
            original_path=proposal.original_path,
            error_msg=error_msg,
            metadata=proposal.metadata,
            parent=self,
        )

        if dialog.exec() == ErrorRenameDialog.DialogCode.Accepted:
            try:
                apply_rename(proposal.original_path, dialog.custom_name)
                self._rename_success += 1
            except OSError as e:
                logger.error(
                    "Custom rename failed for %s: %s",
                    proposal.original_path, e,
                )
                QMessageBox.critical(
                    self, "Rename Failed", f"Could not rename file:\n{e}",
                )
                self._rename_errors += 1
        else:
            self._rename_skipped += 1

    def _update_counts(self, total: int):
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
        self.rename_progress.setVisible(False)
        self.rename_btn.setEnabled(True)
        self.cancel_rename_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)
        self.rename_status_label.setText("Complete.")
        self._update_counts(len(self._found_files))

        total = self._rename_success + self._rename_errors + self._rename_skipped
        parts = [f"Renamed: {self._rename_success}"]
        if self._rename_skipped:
            parts.append(f"Skipped: {self._rename_skipped}")
        if self._rename_errors:
            parts.append(f"Errors: {self._rename_errors}")

        QMessageBox.information(
            self, "Rename Complete",
            f"Finished processing {total} file(s).\n" + "\n".join(parts),
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self._scan_runner:
            self._scan_runner.cancel()
        if self._rename_runner:
            self._rename_runner.cancel()
            self._rename_runner.wait(3000)
        self._save_settings()
        super().closeEvent(event)
