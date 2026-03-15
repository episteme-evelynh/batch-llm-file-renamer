"""Entry point for the PDF/EPUB Counter & AI Renamer application.

Requires:
- PySide6 for the GUI
- PyMuPDF (fitz) for PDF text extraction
- EbookLib + lxml for EPUB text extraction
- openai SDK for Ollama's OpenAI-compatible API
- Ollama running locally with gemma3:4b-it pulled

Usage:
    python main.py

Ensure Ollama is running:
    ollama serve
    ollama pull gemma3:4b-it
"""

import logging
import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("PDF/EPUB Counter & AI Renamer")
    app.setOrganizationName("BatchLLMRenamer")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
