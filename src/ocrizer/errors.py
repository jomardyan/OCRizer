"""Error taxonomy shared by the engine, the CLI and the GUI.

Every failure that can be reported to a user carries a stable :class:`ErrorCode`, a
human-readable message and, where one exists, an actionable hint.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    NOT_A_PDF = "not_a_pdf"
    EMPTY = "empty_pdf"
    CORRUPT = "corrupt_pdf"
    ENCRYPTED = "encrypted_pdf"
    SIGNED = "signed_pdf"
    MISSING_DEPENDENCY = "missing_dependency"
    OUTPUT_EXISTS = "output_exists"
    OUTPUT_ACCESS = "output_access"
    OCR_FAILED = "ocr_failed"
    WORKER_CRASHED = "worker_crashed"
    INTERNAL = "internal_error"


class OcrizerError(Exception):
    """Base class for expected, user-reportable failures."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(self, message: str, *, hint: str | None = None, code: ErrorCode | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code is not None:
            self.code = code

    def __str__(self) -> str:
        return self.message


class InvalidPdfError(OcrizerError):
    """The input is not a usable PDF (not a PDF, empty, corrupt, encrypted, ...)."""

    code = ErrorCode.CORRUPT


class MissingDependencyError(OcrizerError):
    """Tesseract or a required language pack is not available."""

    code = ErrorCode.MISSING_DEPENDENCY


class OutputError(OcrizerError):
    """The output location is invalid, not writable, or would overwrite a file."""

    code = ErrorCode.OUTPUT_ACCESS


class OcrFailedError(OcrizerError):
    """OCRmyPDF/Tesseract failed on an otherwise valid PDF."""

    code = ErrorCode.OCR_FAILED


class ConfigError(OcrizerError):
    """Invalid configuration value or file."""

    code = ErrorCode.INTERNAL


class UsageError(OcrizerError):
    """Invalid combination of inputs/options detected before any work starts."""

    code = ErrorCode.INTERNAL
