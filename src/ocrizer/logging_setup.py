"""Structured logging.

Every record may carry a ``fields`` mapping (see :func:`log_event`). The optional log file is
JSON Lines, one object per record, so it can be grepped or ingested by log tooling. The console
gets a compact human-readable rendering of the same data.

Privacy: the application never logs recognised text or document content, only event names,
file paths, page counts, timings and error codes.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER_NAME = "ocrizer"


def get_logger(suffix: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}" if suffix else LOGGER_NAME)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit a structured record: ``event`` is the message, ``fields`` the payload."""
    if logger.isEnabledFor(level):
        logger.log(level, event, extra={"fields": fields})


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(_jsonable(getattr(record, "fields", {})))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {})
        extra = " ".join(f"{k}={v}" for k, v in _jsonable(fields).items())
        line = f"{record.levelname.lower():7} {record.getMessage()}"
        if extra:
            line += f" ({extra})"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(
    *, verbosity: int = 0, quiet: bool = False, log_file: Path | None = None
) -> None:
    """Configure the ``ocrizer`` logger tree. Safe to call more than once."""
    root = logging.getLogger(LOGGER_NAME)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.DEBUG)
    root.propagate = False

    if quiet:
        console_level = logging.ERROR
    else:
        console_level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG if verbosity >= 2 else logging.INFO)
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

    # OCRmyPDF is chatty; only surface its warnings/errors (never document text).
    logging.getLogger("ocrmypdf").setLevel(logging.DEBUG if verbosity >= 2 else logging.WARNING)
