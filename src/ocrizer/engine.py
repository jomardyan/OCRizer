"""The OCR engine: process exactly one document.

This module is the single place where documents are analysed and OCR'd; the CLI and the GUI
both reach it through :mod:`ocrizer.batch`, which runs :func:`process_document` in a worker
process. It never touches the final output location: the result is written to
``JobRequest.partial_pdf`` and committed (or discarded) by the runner.

Steps: validate -> detect existing text layer -> resolve language -> OCRmyPDF -> verify output
(page count, page sizes, text present) -> optional text export.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pikepdf

from ocrizer import _ocr_progress
from ocrizer.config import AUTO, ExistingText, OcrSettings, split_language
from ocrizer.errors import (
    ErrorCode,
    InvalidPdfError,
    MissingDependencyError,
    OcrFailedError,
    OcrizerError,
)
from ocrizer.language import DetectionResult, detect_language
from ocrizer.logging_setup import get_logger, log_event
from ocrizer.models import JobOutcome, JobRequest, JobStatus, ProgressEvent
from ocrizer.pdfinfo import PdfReport, analyze_pdf, iter_page_text

log = get_logger("engine")

PROGRESS_PLUGIN = "ocrizer._ocr_progress"
BOX_TOLERANCE_PT = 1.0

ProgressCallback = Callable[[ProgressEvent], None]


def process_document(request: JobRequest, progress: ProgressCallback) -> JobOutcome:
    """Process one document and describe the outcome. Never raises for expected failures."""
    started = time.monotonic()
    try:
        outcome = _process(request, progress)
    except OcrizerError as exc:
        outcome = JobOutcome(
            JobStatus.FAILED, message=exc.message, hint=exc.hint or "", error_code=exc.code
        )
    except MemoryError:
        outcome = JobOutcome(
            JobStatus.FAILED,
            message="Ran out of memory while processing this document.",
            hint="Reduce --workers, or split the document into smaller parts.",
            error_code=ErrorCode.OCR_FAILED,
        )
    log_event(
        log, logging.INFO, "document_processed",
        file=request.input_path.name, status=outcome.status.value, pages=outcome.pages,
        pages_ocr=outcome.pages_ocr, language=outcome.language,
        error_code=outcome.error_code.value if outcome.error_code else None,
        seconds=round(time.monotonic() - started, 2),
    )
    return outcome


def _process(request: JobRequest, progress: ProgressCallback) -> JobOutcome:
    settings = request.settings
    progress(ProgressEvent("Checking PDF"))
    report = analyze_pdf(request.input_path)
    if not settings.ocr_images:
        report = replace(report, image_pages=())

    if settings.existing_text is ExistingText.SKIP and report.fully_searchable:
        outcome = JobOutcome(
            JobStatus.SKIPPED,
            pages=report.page_count,
            pages_with_text=len(report.text_pages),
            message=f"Already searchable: all {report.page_count} page(s) contain text.",
        )
        if settings.export_text:
            outcome.text_file = _export_text(request.input_path, request.scratch_dir)
        return outcome

    if settings.deskew and settings.existing_text is ExistingText.SKIP and report.image_pages:
        raise OcrFailedError(
            "Deskew cannot preserve visible text while recognising text inside images.",
            hint="Disable --deskew, use --no-ocr-images, or explicitly choose --existing-text force.",
        )

    language, detection = _resolve_language(request, report, progress)
    pages_to_ocr = (
        len(report.pages_needing_ocr)
        if settings.existing_text is ExistingText.SKIP
        else report.page_count
    )

    _run_ocrmypdf(request, language, report, progress)
    _verify_output(request.input_path, request.partial_pdf, report.page_count)

    pages_with_text = _count_text_pages(request.partial_pdf)
    message = ""
    if pages_to_ocr and _count_chars(request.partial_pdf) <= _count_chars(request.input_path):
        message = (
            "No text was recognised (the pages may be blank or contain no text)."
            if not report.text_pages
            else "No text was found inside the images."
        )
    outcome = JobOutcome(
        JobStatus.SUCCEEDED,
        pages=report.page_count,
        pages_ocr=pages_to_ocr,
        pages_with_text=pages_with_text,
        language=language,
        detection=detection,
        message=message,
    )
    if settings.export_text:
        progress(ProgressEvent("Exporting text"))
        outcome.text_file = _export_text(request.partial_pdf, request.scratch_dir)
    return outcome


def _resolve_language(
    request: JobRequest, report: PdfReport, progress: ProgressCallback
) -> tuple[str, DetectionResult | None]:
    settings = request.settings
    if settings.language != AUTO:
        return settings.language, None
    progress(ProgressEvent("Detecting language"))
    candidates = report.pages_needing_ocr or tuple(range(report.page_count))
    detection = detect_language(
        request.input_path, candidates, sample_count=settings.detect_pages
    )
    log_event(
        log, logging.INFO, "language_detected", file=request.input_path.name,
        language=detection.language, confident=detection.confident, reason=detection.reason,
        polish=round(detection.polish_share, 3), english=round(detection.english_share, 3),
    )
    return detection.language, detection


def _run_ocrmypdf(
    request: JobRequest, language: str, report: PdfReport, progress: ProgressCallback
) -> None:
    import ocrmypdf
    from ocrmypdf import exceptions as ox

    settings = request.settings
    options = _ocrmypdf_options(settings, language, request, bool(report.image_pages))
    if settings.existing_text is ExistingText.SKIP and report.image_pages:
        # Redo is required to read images beside visible text. Limit it to pages that need
        # OCR so existing hidden text layers elsewhere in the document are preserved.
        options["pages"] = ",".join(str(index + 1) for index in report.pages_needing_ocr)
    _ocr_progress.set_sink(lambda stage, done, total: progress(ProgressEvent(stage, done, total)))
    try:
        ocrmypdf.ocr(request.input_path, request.partial_pdf, **options)
    except ox.EncryptedPdfError as exc:
        raise InvalidPdfError(
            "The PDF is encrypted or password-protected.", code=ErrorCode.ENCRYPTED
        ) from exc
    except ox.DigitalSignatureError as exc:
        raise InvalidPdfError(
            "The PDF is digitally signed; adding a text layer would invalidate the signature.",
            code=ErrorCode.SIGNED,
            hint="Process an unsigned copy of the document.",
        ) from exc
    except ox.PriorOcrFoundError as exc:
        raise OcrFailedError(
            "The PDF already contains text.",
            hint="Use --existing-text redo (replace an old OCR layer) or force.",
        ) from exc
    except ox.MissingDependencyError as exc:
        raise MissingDependencyError(str(exc) or "A required OCR dependency is missing.") from exc
    except ox.OutputFileAccessError as exc:
        raise OcrizerError(
            f"Cannot write the output file: {exc}", code=ErrorCode.OUTPUT_ACCESS
        ) from exc
    except ox.InputFileError as exc:
        raise InvalidPdfError(
            f"OCRmyPDF rejected the input: {_one_line(exc)}", code=ErrorCode.CORRUPT
        ) from exc
    except ox.ExitCodeException as exc:
        raise OcrFailedError(f"OCR failed: {_one_line(exc)}") from exc
    except ValueError as exc:
        raise OcrFailedError(
            f"OCR settings were rejected: {_one_line(exc)}",
            hint="Check the language, preprocessing and existing-text options.",
        ) from exc
    finally:
        _ocr_progress.set_sink(None)


def _ocrmypdf_options(
    settings: OcrSettings, language: str, request: JobRequest, image_text: bool = False
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "language": list(split_language(language)),
        "output_type": "pdf",  # keep the original structure; no PDF/A conversion
        "jobs": request.ocr_jobs,
        "rotate_pages": settings.rotate_pages and request.has_osd,
        "deskew": settings.deskew,
        "optimize": settings.optimize,
        "max_ocr_image_mpixels": settings.max_ocr_megapixels,
        "tesseract_timeout": settings.tesseract_timeout,
        "progress_bar": False,
        "plugins": [PROGRESS_PLUGIN],
    }
    if settings.existing_text is ExistingText.SKIP and not image_text:
        options["skip_text"] = True
    elif settings.existing_text is not ExistingText.FORCE:
        # REDO, or SKIP with text inside images: OCRmyPDF masks the visible text, reads what is
        # left (the images) and adds it as an invisible layer; vector text stays untouched.
        options["redo_ocr"] = True
    else:
        options["force_ocr"] = True
    return options


def _geometry(path: Path) -> list[tuple[float, float]]:
    sizes = []
    with pikepdf.open(path) as pdf:
        for page in pdf.pages:
            box = [float(v) for v in page.mediabox]
            sizes.append((abs(box[2] - box[0]), abs(box[3] - box[1])))
    return sizes


def _verify_output(source: Path, result: Path, expected_pages: int) -> None:
    """Make sure OCR kept the page count and dimensions at each page position."""
    if not result.is_file() or result.stat().st_size == 0:
        raise OcrFailedError("OCR finished but produced no output file.")
    try:
        before, after = _geometry(source), _geometry(result)
    except (pikepdf.PdfError, OSError) as exc:
        raise OcrFailedError(f"The OCR output is not a valid PDF: {_one_line(exc)}") from exc
    if len(after) != len(before) or len(after) != expected_pages:
        raise OcrFailedError(
            f"Integrity check failed: page count changed ({len(before)} -> {len(after)})."
        )
    for number, (a, b) in enumerate(zip(before, after, strict=True), start=1):
        if abs(a[0] - b[0]) > BOX_TOLERANCE_PT or abs(a[1] - b[1]) > BOX_TOLERANCE_PT:
            raise OcrFailedError(f"Integrity check failed: page {number} changed size.")


def _count_text_pages(path: Path) -> int:
    return sum(1 for text in iter_page_text(path) if text.strip())


def _count_chars(path: Path) -> int:
    return sum(len("".join(text.split())) for text in iter_page_text(path))


def _export_text(pdf: Path, scratch: Path) -> Path:
    target = scratch / "text.txt"
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        for number, text in enumerate(iter_page_text(pdf)):
            if number:
                fh.write("\f")
            fh.write(text)
    return target


def _one_line(exc: BaseException) -> str:
    lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
    return lines[0] if lines else exc.__class__.__name__

