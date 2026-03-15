"""Recursive PDF/EPUB file scanner running on a background QThread."""

import os

from PySide6.QtCore import QThread, Signal


SUPPORTED_EXTENSIONS = {".pdf", ".epub"}


class ScannerWorker(QThread):
    """Recursively discovers PDF and EPUB files in a directory tree.

    Runs on a background thread to keep the GUI responsive.
    Emits signals for each file found (for live counter/label updates)
    and a final signal with the complete file list.

    The progress bar should be set to indeterminate/busy mode during
    scanning since the total file count is unknown upfront.
    """

    file_found = Signal(str)     # Emitted for each file: full path
    count_updated = Signal(int)  # Emitted with running total count
    finished_scan = Signal(list) # Emitted when done: full list of paths
    error = Signal(str)          # Emitted on unrecoverable errors

    def __init__(self, root_dir: str, parent=None):
        super().__init__(parent)
        self.root_dir = root_dir

    def run(self):
        """Walk the directory tree and emit signals for each PDF/EPUB found."""
        found_files = []
        count = 0

        try:
            for dirpath, dirnames, filenames in os.walk(self.root_dir):
                if self.isInterruptionRequested():
                    break

                # Skip hidden directories (e.g., .git, .Spotlight-V100)
                dirnames[:] = [
                    d for d in dirnames if not d.startswith(".")
                ]

                for filename in filenames:
                    if self.isInterruptionRequested():
                        break

                    _, ext = os.path.splitext(filename)
                    if ext.lower() in SUPPORTED_EXTENSIONS:
                        full_path = os.path.join(dirpath, filename)
                        found_files.append(full_path)
                        count += 1
                        self.file_found.emit(full_path)
                        self.count_updated.emit(count)

        except PermissionError as e:
            self.error.emit(f"Permission denied: {e}")
        except Exception as e:
            self.error.emit(f"Scan error: {e}")

        self.finished_scan.emit(found_files)
