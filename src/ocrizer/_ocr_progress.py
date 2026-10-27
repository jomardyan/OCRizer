"""OCRmyPDF plugin that forwards progress to a callback instead of drawing a progress bar.

It is loaded by module name (``plugins=["ocrizer._ocr_progress"]``) in the worker process. A
module-level sink is used because OCRmyPDF instantiates the progress-bar class itself.
"""

from __future__ import annotations

from collections.abc import Callable

from ocrmypdf import hookimpl

ProgressSink = Callable[[str, int, int], None]  # (stage, completed, total)

_sink: ProgressSink | None = None


def set_sink(sink: ProgressSink | None) -> None:
    global _sink
    _sink = sink


class _CallbackProgressBar:
    def __init__(self, *, total=None, desc=None, unit=None, disable=False, **kwargs):
        self._total = int(total) if total else 0
        self._desc = desc or "Working"
        self._done = 0

    def __enter__(self):
        self._emit()
        return self

    def __exit__(self, *args):
        return False

    def update(self, n=1, *, completed=None):
        self._done = int(completed) if completed is not None else self._done + int(n)
        self._emit()

    def _emit(self) -> None:
        if _sink is not None:
            _sink(self._desc, self._done, self._total)


@hookimpl
def get_progressbar_class():
    return _CallbackProgressBar
