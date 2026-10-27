"""PyInstaller entry point for the command-line executable (pdfocr)."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # worker processes re-execute this binary on Windows
    from ocrizer.cli import main

    sys.exit(main())
