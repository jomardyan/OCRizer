"""Stand-in worker processes for runner tests (must be importable by spawned children)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

from ocrizer.models import JobOutcome, JobStatus, ProgressEvent


def ok_worker(conn, request):  # type: ignore[no-untyped-def]
    shutil.copyfile(request.input_path, request.partial_pdf)
    conn.send(("progress", ProgressEvent("fake", 1, 1)))
    conn.send(("result", JobOutcome(JobStatus.SUCCEEDED, pages=1, pages_ocr=1, language="eng")))
    conn.close()


def crash_worker(conn, request):  # type: ignore[no-untyped-def]
    request.partial_pdf.write_bytes(b"partial")
    os._exit(3)


def selective_worker(conn, request):  # type: ignore[no-untyped-def]
    """Crashes for files named *bad*, fails for *invalid*, succeeds otherwise."""
    name = request.input_path.name
    if "bad" in name:
        os._exit(7)
    if "invalid" in name:
        conn.send(("result", JobOutcome(JobStatus.FAILED, message="not a pdf")))
        conn.close()
        return
    ok_worker(conn, request)


def sleep_worker(conn, request):  # type: ignore[no-untyped-def]
    """Leaves scratch/partial files behind and a grandchild process, then blocks."""
    (request.scratch_dir / "work.tmp").write_text("x")
    request.partial_pdf.write_bytes(b"%PDF-partial")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    pid_file = os.environ.get("FAKE_PID_FILE")
    if pid_file:
        with open(pid_file, "a") as fh:
            fh.write(f"{child.pid}\n")
    time.sleep(300)
