"""Worker-process entry point.

Each document is processed in its own short-lived process. That gives the runner a hard
guarantee it could not get from threads: cancelling a job means killing the process tree (the
worker, OCRmyPDF's pool and any Tesseract children), and a crash (out-of-memory, segfault in a
native library) takes down only that one document.

Protocol: the worker sends tuples over a one-way pipe:
``("progress", ProgressEvent)``, ``("log", level, logger_name, message)`` and finally exactly
one ``("result", JobOutcome)``.
"""

from __future__ import annotations

import logging
import os
import signal
import tempfile
import threading
import time
import traceback
from multiprocessing.connection import Connection

import psutil

from ocrizer.errors import ErrorCode
from ocrizer.models import JobOutcome, JobRequest, JobStatus


class _Channel:
    """Thread-safe wrapper around the pipe (logging can come from several threads)."""

    def __init__(self, conn: Connection):
        self._conn = conn
        self._lock = threading.Lock()

    def send(self, message: tuple) -> None:
        with self._lock:
            try:
                self._conn.send(message)
            except (OSError, ValueError):
                pass  # the runner went away; the watchdog will end this process


class _PipeLogHandler(logging.Handler):
    def __init__(self, channel: _Channel):
        super().__init__(logging.INFO)
        self._channel = channel

    def emit(self, record: logging.LogRecord) -> None:
        fields = getattr(record, "fields", None)
        level = record.levelno
        if record.name.startswith("ocrmypdf") and level < logging.ERROR:
            level = logging.INFO  # OCRmyPDF's warnings are advisory (e.g. optimiser notes)
        self._channel.send(("log", level, record.name, record.getMessage(), fields))


def _watch_parent(parent_pid: int) -> None:
    """Exit (killing our own children) if the runner process disappears."""
    if not parent_pid:
        return

    def loop() -> None:
        while True:
            time.sleep(2.0)
            if not psutil.pid_exists(parent_pid):
                me = psutil.Process()
                for child in me.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
                os._exit(1)

    threading.Thread(target=loop, name="parent-watchdog", daemon=True).start()


def run_job_process(conn: Connection, request: JobRequest) -> None:
    """``multiprocessing.Process`` target. Imports are deferred to keep spawn start-up cheap."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is handled by the runner
    _watch_parent(request.parent_pid)
    tempfile.tempdir = str(request.scratch_dir)  # OCRmyPDF's work files live in our scratch dir

    channel = _Channel(conn)
    handler = _PipeLogHandler(channel)
    for name in ("ocrizer", "ocrmypdf"):
        logging.getLogger(name).addHandler(handler)
        logging.getLogger(name).setLevel(logging.INFO)

    try:
        from ocrizer.engine import process_document

        outcome = process_document(request, lambda ev: channel.send(("progress", ev)))
    except BaseException as exc:  # noqa: BLE001 - last line of defence, must always report
        outcome = JobOutcome(
            JobStatus.FAILED,
            message=f"Unexpected error: {exc.__class__.__name__}: {exc}",
            error_code=ErrorCode.INTERNAL,
        )
        channel.send(("log", logging.ERROR, "ocrizer.worker", traceback.format_exc(), None))
    channel.send(("result", outcome))
    conn.close()

