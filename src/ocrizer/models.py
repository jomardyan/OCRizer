"""Plain data types shared by the engine, the batch runner and the front ends.

All types are picklable: they are sent between the worker processes and the runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ocrizer.config import OcrSettings
from ocrizer.errors import ErrorCode
from ocrizer.language import DetectionResult


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"

    @property
    def finished(self) -> bool:
        return self not in (JobStatus.PENDING, JobStatus.RUNNING)


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    stage: str          # e.g. "Detecting language", "OCR"
    current: int = 0
    total: int = 0      # 0 = unknown

    def text(self) -> str:
        if self.total == 100:  # OCRmyPDF reports some stages as a percentage
            return f"{self.stage} {self.current}%"
        if self.total:
            return f"{self.stage} {self.current}/{self.total}"
        return self.stage


@dataclass(frozen=True, slots=True)
class Job:
    """One planned unit of work: an input document and where its result will go."""

    index: int
    input_path: Path
    output_path: Path
    skip_reason: str = ""  # non-empty: the planner already decided to skip this job

    @property
    def text_path(self) -> Path:
        return self.output_path.with_suffix(".txt")


@dataclass(frozen=True, slots=True)
class JobRequest:
    """Everything a worker process needs to process one document."""

    index: int
    input_path: Path
    scratch_dir: Path     # private temp directory (removed by the runner afterwards)
    partial_pdf: Path     # where the worker writes the result before the runner commits it
    settings: OcrSettings
    ocr_jobs: int
    has_osd: bool = True
    parent_pid: int = 0


@dataclass(slots=True)
class JobOutcome:
    """What the worker reports back for a document."""

    status: JobStatus
    pages: int = 0
    pages_ocr: int = 0
    pages_with_text: int = 0
    language: str | None = None
    detection: DetectionResult | None = None
    text_file: Path | None = None
    message: str = ""
    hint: str = ""
    error_code: ErrorCode | None = None


@dataclass(slots=True)
class JobResult:
    """Final, user-facing result for a job (after the runner committed or discarded files)."""

    job: Job
    status: JobStatus
    output_path: Path | None = None
    text_path: Path | None = None
    language: str | None = None
    detection: DetectionResult | None = None
    pages: int = 0
    pages_ocr: int = 0
    pages_with_text: int = 0
    message: str = ""
    hint: str = ""
    error_code: ErrorCode | None = None
    duration: float = 0.0


@dataclass(slots=True)
class BatchSummary:
    results: list[JobResult] = field(default_factory=list)
    cancelled: bool = False

    def count(self, status: JobStatus) -> int:
        return sum(1 for r in self.results if r.status is status)

    @property
    def succeeded(self) -> int:
        return self.count(JobStatus.SUCCEEDED)

    @property
    def failed(self) -> int:
        return self.count(JobStatus.FAILED)

    @property
    def skipped(self) -> int:
        return self.count(JobStatus.SKIPPED)

    @property
    def cancelled_jobs(self) -> int:
        return self.count(JobStatus.CANCELLED)

    def describe(self) -> str:
        return (
            f"{self.succeeded} succeeded, {self.failed} failed, "
            f"{self.skipped} skipped, {self.cancelled_jobs} cancelled"
        )


@dataclass(frozen=True, slots=True)
class BatchEvent:
    """Notification emitted by the runner for GUI/CLI consumption."""

    kind: str  # "started" | "progress" | "finished"
    job: Job
    progress: ProgressEvent | None = None
    result: JobResult | None = None
