"""Batch processing and job management.

:class:`BatchRunner` runs the planned jobs, at most ``workers`` at a time, each in its own
worker process (see :mod:`ocrizer.worker`). It is the only component that

* creates and removes per-job scratch directories and partial output files,
* commits a finished result to its final path (atomically, never replacing an existing file
  unless overwriting was explicitly allowed),
* cancels work by terminating whole process trees, and
* turns every outcome - success, validation failure, crash, cancellation - into a
  :class:`~ocrizer.models.JobResult`, so that one bad document can never stop the batch.

The CLI and the GUI use exactly this class; ``run()`` blocks the calling thread (the GUI calls
it from a ``QThread``) and ``cancel()`` may be called from any thread.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from multiprocessing.connection import wait
from pathlib import Path
from typing import Any

import psutil

from ocrizer.config import AUTO, OcrSettings, default_workers, split_language
from ocrizer.deps import REQUIRED_LANGUAGE_PACKS, check_dependencies
from ocrizer.errors import ConfigError, ErrorCode, OcrizerError, OutputError
from ocrizer.logging_setup import get_logger, log_event
from ocrizer.models import (
    BatchEvent,
    BatchSummary,
    Job,
    JobOutcome,
    JobRequest,
    JobResult,
    JobStatus,
)
from ocrizer.planner import TEMP_PREFIX
from ocrizer.worker import run_job_process

log = get_logger("batch")

EventCallback = Callable[[BatchEvent], None]

MAX_OCR_JOBS = 8


@dataclass(slots=True)
class _Active:
    job: Job
    process: Any  # multiprocessing.Process (spawn context)
    conn: Any
    scratch: Path
    partial: Path
    started: float
    outcome: JobOutcome | None = None


def kill_process_tree(pid: int, timeout: float = 5.0) -> None:
    """Kill a process and all of its descendants (OCRmyPDF workers, Tesseract, ...)."""
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    killed: list[psutil.Process] = [root]
    for _ in range(3):  # sweep again in case something spawned while we were killing
        try:
            descendants = root.children(recursive=True)
        except psutil.Error:
            break
        if not descendants:
            break
        for proc in reversed(descendants):
            killed.append(proc)
            try:
                proc.kill()
            except psutil.Error:
                pass
    try:
        root.kill()
    except psutil.Error:
        pass
    psutil.wait_procs(killed, timeout=timeout)


def remove_tree(path: Path, attempts: int = 6) -> bool:
    """Remove a directory, retrying briefly (Windows releases file handles a moment late)."""
    for attempt in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(0.1 * (attempt + 1))
    log_event(log, logging.WARNING, "scratch_cleanup_failed", path=path)
    return False


def remove_file(path: Path | None) -> None:
    if path is None:
        return
    for attempt in range(5):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(0.1 * (attempt + 1))


def commit_file(source: Path, target: Path, *, overwrite: bool) -> None:
    """Move ``source`` to ``target`` atomically; refuse to replace a file unless ``overwrite``."""
    staged: Path | None = None
    try:
        # Text exports originate in the scratch directory, which may be on a different
        # drive from the destination. Stage beside the target before the atomic commit.
        if source.parent.resolve() != target.parent.resolve():
            fd, name = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".partial", dir=target.parent)
            os.close(fd)
            staged = Path(name)
            shutil.copyfile(source, staged)
            commit_file(staged, target, overwrite=overwrite)
            source.unlink(missing_ok=True)
            return
        if overwrite:
            os.replace(source, target)
            return
        try:
            os.link(source, target)  # fails if target exists: no check-then-write race
        except FileExistsError as exc:
            raise OutputError(
                f"Output file already exists: {target.name}", code=ErrorCode.OUTPUT_EXISTS
            ) from exc
        except OSError as exc:
            if os.name == "nt":
                # Windows rename refuses existing targets, including on FAT/exFAT where
                # hard links are unsupported. POSIX rename would silently replace them.
                try:
                    os.rename(source, target)
                except FileExistsError as exists:
                    raise OutputError(
                        f"Output file already exists: {target.name}", code=ErrorCode.OUTPUT_EXISTS
                    ) from exists
                return
            raise OutputError(
                f"Cannot safely create {target.name}: this filesystem does not support "
                "atomic non-overwriting saves.",
                hint="Choose another output filesystem, or explicitly allow overwriting.",
            ) from exc
        source.unlink(missing_ok=True)
    except PermissionError as exc:
        raise OutputError(
            f"Cannot write {target.name}: permission denied (is the file open in another program?)"
        ) from exc
    except OSError as exc:
        raise OutputError(f"Cannot write {target.name}: {exc.strerror or exc}") from exc
    finally:
        if staged is not None:
            remove_file(staged)


class BatchRunner:
    def __init__(
        self,
        jobs: Sequence[Job],
        settings: OcrSettings,
        *,
        workers: int | None = None,
        overwrite: bool = False,
        on_event: EventCallback | None = None,
        tesseract_path: str | None = None,
        verify_dependencies: bool = True,
        worker_target: Callable[..., None] = run_job_process,
    ):
        """
        ``overwrite`` must mirror the planner's decision: it allows a result to replace an
        existing file (``--overwrite`` / ``--in-place``). ``worker_target`` exists for tests.
        """
        self._jobs = list(jobs)
        self._settings = settings.validated()
        if workers is not None and (
            not isinstance(workers, int) or isinstance(workers, bool) or workers < 1
        ):
            raise ConfigError("workers must be a positive integer.")
        if len({job.index for job in self._jobs}) != len(self._jobs):
            raise ConfigError("Every batch job must have a unique index.")
        self._workers = workers if workers is not None else default_workers()
        self._overwrite = overwrite
        self._on_event = on_event
        self._tesseract_path = tesseract_path
        self._verify = verify_dependencies
        self._worker_target = worker_target
        self._cancel = threading.Event()
        self._ctx = multiprocessing.get_context("spawn")
        self._has_osd = True

    # ------------------------------------------------------------------ public API
    def cancel(self) -> None:
        """Request cancellation. Running documents are terminated, queued ones are cancelled."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> BatchSummary:
        if not self.cancelled and any(not job.skip_reason for job in self._jobs):
            self._check_environment()
        results: dict[int, JobResult] = {}
        pending: deque[Job] = deque(self._jobs)
        active: dict[Any, _Active] = {}
        log_event(log, logging.INFO, "batch_started", jobs=len(self._jobs), workers=self._workers)
        try:
            while pending or active:
                try:
                    self._step(pending, active, results)
                except KeyboardInterrupt:
                    self._cancel.set()
        finally:
            for entry in list(active.values()):  # only reached with leftovers on hard errors
                self._abort(entry, results, "Cancelled")
        summary = BatchSummary(
            [results.get(j.index) or JobResult(j, JobStatus.CANCELLED, message="Cancelled")
             for j in self._jobs],
            self._cancel.is_set(),
        )
        log_event(log, logging.INFO, "batch_finished", summary=summary.describe())
        return summary

    # ------------------------------------------------------------------ internals
    def _check_environment(self) -> None:
        languages: tuple[str, ...]
        if self._settings.language == AUTO:
            languages = REQUIRED_LANGUAGE_PACKS
        else:
            languages = split_language(self._settings.language)
        if not self._verify:
            return
        report = check_dependencies(languages, configured_tesseract=self._tesseract_path)
        report.raise_if_missing()
        self._has_osd = report.has_osd
        if self._settings.rotate_pages and not report.has_osd:
            log_event(log, logging.WARNING, "osd_missing_rotation_disabled")

    def _step(
        self, pending: deque[Job], active: dict[Any, _Active], results: dict[int, JobResult]
    ) -> None:
        if self._cancel.is_set():
            for entry in list(active.values()):
                self._abort(entry, results, "Cancelled")
            active.clear()
            while pending:
                self._record(results, JobResult(pending.popleft(), JobStatus.CANCELLED, message="Cancelled"))
            return

        while pending and len(active) < self._workers and not self.cancelled:
            job = pending.popleft()
            if job.skip_reason:
                self._record(results, JobResult(job, JobStatus.SKIPPED, message=job.skip_reason))
                continue
            started = self._start(job, results)
            if started is not None:
                active[started.conn] = started
        if not active:
            return

        for ready in wait(list(active), timeout=0.2):
            conn: Any = ready
            entry = active[conn]
            try:
                message: Any = conn.recv()
            except (EOFError, OSError):
                del active[conn]
                self._finish(entry, results, entry.outcome)
                continue
            kind = message[0]
            if kind == "progress":
                self._emit(BatchEvent("progress", entry.job, progress=message[1]))
            elif kind == "log":
                _, level, name, text, fields = message
                logging.getLogger(f"ocrizer.worker.{name}").log(
                    level, text, extra={"fields": fields or {"file": entry.job.input_path.name}}
                )
            elif kind == "result":
                entry.outcome = message[1]
                del active[conn]
                self._finish(entry, results, entry.outcome)

    def _default_ocr_jobs(self) -> int:
        """OCRmyPDF threads per document: share the cores between the documents that can really
        run at once, but never more than MAX_OCR_JOBS (each one holds a full page raster)."""
        concurrent = max(1, min(self._workers, len(self._jobs)))
        return max(1, min(MAX_OCR_JOBS, (os.cpu_count() or 1) // concurrent))

    def _start(self, job: Job, results: dict[int, JobResult]) -> _Active | None:
        scratch: Path | None = None
        partial = job.output_path.parent / f"{TEMP_PREFIX}{uuid.uuid4().hex[:12]}.partial.pdf"
        try:
            scratch = Path(tempfile.mkdtemp(prefix="ocrizer-"))
            job.output_path.parent.mkdir(parents=True, exist_ok=True)
            partial.touch(exist_ok=False)  # proves the directory is writable
            partial.unlink()
        except OSError as exc:
            if scratch is not None:
                remove_tree(scratch)
            self._record(
                results,
                JobResult(
                    job, JobStatus.FAILED, error_code=ErrorCode.OUTPUT_ACCESS,
                    message=f"Cannot prepare the output or temporary folder: {exc.strerror or exc}",
                    hint="Check free space and permissions for the output and temporary folders.",
                ),
            )
            return None

        ocr_jobs = self._settings.ocr_jobs or self._default_ocr_jobs()
        request = JobRequest(
            index=job.index, input_path=job.input_path, scratch_dir=scratch, partial_pdf=partial,
            settings=self._settings, ocr_jobs=ocr_jobs, has_osd=self._has_osd,
            parent_pid=os.getpid(),
        )
        recv, send = self._ctx.Pipe(duplex=False)
        process = self._ctx.Process(
            target=self._worker_target, args=(send, request), name=f"ocr-{job.index}", daemon=False
        )
        try:
            process.start()
        except OSError as exc:
            send.close()
            recv.close()
            remove_tree(scratch)
            self._record(
                results,
                JobResult(job, JobStatus.FAILED, error_code=ErrorCode.WORKER_CRASHED,
                          message=f"Could not start a worker process: {exc.strerror or exc}"),
            )
            return None
        send.close()  # keep only the child's copy so that EOF is seen when it exits
        entry = _Active(job, process, recv, scratch, partial, time.monotonic())
        self._emit(BatchEvent("started", job))
        return entry

    def _finish(
        self, entry: _Active, results: dict[int, JobResult], outcome: JobOutcome | None
    ) -> None:
        """Called once the worker has reported (or died): commit files, clean up, record."""
        entry.process.join(timeout=10)
        if entry.process.is_alive():
            kill_process_tree(entry.process.pid)
        entry.conn.close()
        job = entry.job
        duration = time.monotonic() - entry.started
        try:
            if outcome is None:
                code = entry.process.exitcode
                result = JobResult(
                    job, JobStatus.FAILED, error_code=ErrorCode.WORKER_CRASHED,
                    message=f"The OCR worker process crashed (exit code {code}).",
                    hint="This is often caused by running out of memory; try fewer --workers.",
                )
            else:
                result = self._commit_outcome(job, entry, outcome)
        finally:
            remove_file(entry.partial)
            remove_tree(entry.scratch)
        result.duration = duration
        self._record(results, result)

    def _commit_outcome(self, job: Job, entry: _Active, outcome: JobOutcome) -> JobResult:
        result = JobResult(
            job, outcome.status, language=outcome.language, detection=outcome.detection,
            pages=outcome.pages, pages_ocr=outcome.pages_ocr,
            pages_with_text=outcome.pages_with_text, message=outcome.message,
            hint=outcome.hint, error_code=outcome.error_code,
        )
        if outcome.status is JobStatus.FAILED:
            return result
        try:
            if outcome.status is JobStatus.SUCCEEDED:
                commit_file(entry.partial, job.output_path, overwrite=self._overwrite)
                result.output_path = job.output_path
            if outcome.text_file is not None and outcome.text_file.is_file():
                # An in-place run must never replace an unrelated <name>.txt next to the original.
                overwrite_text = self._overwrite and job.output_path != job.input_path
                commit_file(outcome.text_file, job.text_path, overwrite=overwrite_text)
                result.text_path = job.text_path
        except OcrizerError as exc:
            result.status = JobStatus.FAILED
            result.error_code, result.message, result.hint = exc.code, exc.message, exc.hint or ""
            if result.output_path is not None:  # PDF committed but the text file failed
                result.output_path = job.output_path
        return result

    def _abort(self, entry: _Active, results: dict[int, JobResult], message: str) -> None:
        kill_process_tree(entry.process.pid)
        entry.process.join(timeout=5)
        try:
            entry.conn.close()
        except OSError:
            pass
        remove_file(entry.partial)
        remove_tree(entry.scratch)
        self._record(results, JobResult(entry.job, JobStatus.CANCELLED, message=message,
                                        duration=time.monotonic() - entry.started))

    def _record(self, results: dict[int, JobResult], result: JobResult) -> None:
        results[result.job.index] = result
        log_event(
            log, logging.INFO, "job_finished", file=result.job.input_path.name,
            status=result.status.value, error_code=result.error_code.value if result.error_code else None,
        )
        self._emit(BatchEvent("finished", result.job, result=result))

    def _emit(self, event: BatchEvent) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a broken UI callback must not abort the batch
            log.exception("event callback failed")

