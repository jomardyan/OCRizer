"""PyInstaller entry point for the desktop application (OCRizer)."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from ocrizer.gui.app import main

    sys.exit(main())
