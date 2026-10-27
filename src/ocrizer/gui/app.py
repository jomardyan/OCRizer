"""GUI entry point (``pdfocr-gui``)."""

from __future__ import annotations

import multiprocessing
import os
import sys
from collections.abc import Sequence


def _hide_console_windows() -> None:
    """Windows GUI builds have no console, so every Tesseract/Ghostscript child process that
    OCRmyPDF starts would flash its own console window. Giving the process one hidden console,
    which the children inherit, avoids that."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    if not kernel32.GetConsoleWindow() and kernel32.AllocConsole():
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, 0)  # SW_HIDE


def main(argv: Sequence[str] | None = None, *, tesseract_path: str | None = None) -> int:
    multiprocessing.freeze_support()  # needed for PyInstaller-built executables on Windows
    _hide_console_windows()
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        if sys.stderr is not None:
            print(
                f"OCRizer: cannot load the desktop interface: {exc}\n"
                'Install GUI support with: pip install "ocrizer[gui]"',
                file=sys.stderr,
            )
        return 3

    from ocrizer.config import load_config
    from ocrizer.errors import ConfigError
    from ocrizer.gui.main_window import MainWindow
    from ocrizer.logging_setup import configure_logging

    configure_logging(verbosity=0)
    app = QApplication.instance() or QApplication(["pdfocr-gui", *argv] if argv is not None else sys.argv)
    app.setApplicationName("OCRizer")
    config_error: str | None = None
    try:
        config = load_config()
    except ConfigError as exc:
        from ocrizer.config import AppConfig

        config = AppConfig()
        config_error = str(exc)
    if tesseract_path is not None:
        config.tesseract_path = tesseract_path
    window = MainWindow(config)
    window.show()
    if config_error:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(
            window, "Configuration could not be loaded",
            f"OCRizer is using its default settings.\n\n{config_error}",
        )
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
