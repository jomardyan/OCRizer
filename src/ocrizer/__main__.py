"""``python -m ocrizer`` runs the command-line interface."""

import multiprocessing
import sys

from ocrizer.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
