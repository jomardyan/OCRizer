"""Settings objects and persisted user configuration.

Resolution order for every user-tunable value (highest priority first):

1. explicit command-line options / GUI controls
2. environment variables (``OCRIZER_*``)
3. the per-user JSON config file (``config.json`` in the platform config directory)
4. built-in defaults
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import platformdirs

from ocrizer.errors import ConfigError

APP_NAME = "ocrizer"

AUTO = "auto"
SUPPORTED_LANGUAGES = ("pol", "eng")

_LANGUAGE_ALIASES = {
    "auto": AUTO,
    "autodetect": AUTO,
    "auto-detect": AUTO,
    "pol": "pol",
    "pl": "pol",
    "polish": "pol",
    "polski": "pol",
    "eng": "eng",
    "en": "eng",
    "english": "eng",
}


def normalize_language(value: str) -> str:
    """Return ``"auto"`` or a ``+``-joined Tesseract language spec (``pol``, ``eng``, ``pol+eng``).

    Accepts the Tesseract codes plus a few friendly aliases (``pl``, ``english``, ...).
    The order is preserved because Tesseract treats the first language as primary.
    """
    if not isinstance(value, str):
        raise ConfigError("Language must be a string.", hint="Use auto, pol, eng or pol+eng.")
    text = value.strip().lower()
    if not text:
        raise ConfigError("Language must not be empty.", hint="Use auto, pol, eng or pol+eng.")
    parts = [p.strip() for p in text.replace(",", "+").split("+") if p.strip()]
    if not parts:
        raise ConfigError("Language must not be empty.", hint="Use auto, pol, eng or pol+eng.")
    codes: list[str] = []
    for part in parts:
        code = _LANGUAGE_ALIASES.get(part)
        if code is None:
            raise ConfigError(
                f"Unsupported language {part!r}.",
                hint="Supported values: auto, pol (Polish), eng (English), pol+eng.",
            )
        if code not in codes:
            codes.append(code)
    if AUTO in codes and len(codes) > 1:
        raise ConfigError("'auto' cannot be combined with other languages.")
    return "+".join(codes)


def split_language(spec: str) -> tuple[str, ...]:
    """``"pol+eng"`` -> ``("pol", "eng")``; ``"auto"`` -> ``()``."""
    return () if spec == AUTO else tuple(spec.split("+"))


class ExistingText(StrEnum):
    """What to do with pages that already contain text."""

    SKIP = "skip"    # OCR only pages without text; skip the whole file if none need OCR
    REDO = "redo"    # replace an old hidden OCR layer, keep visible text, OCR images
    FORCE = "force"  # rasterise every page and OCR it (destructive to vector text)


class ConflictPolicy(StrEnum):
    """What to do when the output file already exists."""

    RENAME = "rename"        # document_ocr.pdf -> document_ocr_1.pdf
    SKIP = "skip"            # leave the existing file, report the input as skipped
    OVERWRITE = "overwrite"  # replace the existing output (never an input unless in-place)


@dataclass(frozen=True, slots=True)
class OcrSettings:
    """Everything that influences how one document is OCR'd."""

    language: str = AUTO
    existing_text: ExistingText = ExistingText.SKIP
    # With ExistingText.SKIP: also read text that sits inside images on pages that have a text
    # layer (charts, screenshots). The visible text and vector content are left untouched.
    ocr_images: bool = True
    # Page rotation is metadata-only (/Rotate) and applied only when Tesseract's orientation
    # detection is confident. Deskew rewrites page images, so it is opt-in (integrity first).
    rotate_pages: bool = True
    deskew: bool = False
    optimize: int = 1                 # 0 = off, 1 = lossless, 2/3 = lossy (smaller)
    export_text: bool = False
    detect_pages: int = 4             # pages sampled by automatic language detection
    ocr_jobs: int = 0                 # OCRmyPDF parallel pages per document; 0 = share the cores
    max_ocr_megapixels: float = 64.0  # downsample only the OCR input above this (bounds memory)
    tesseract_timeout: float = 300.0  # seconds per page

    def validated(self) -> OcrSettings:
        language = normalize_language(self.language)
        for name in ("optimize", "detect_pages", "ocr_jobs"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConfigError(f"{name} must be an integer.")
        if not 0 <= self.optimize <= 3:
            raise ConfigError("optimize must be between 0 and 3.")
        if self.detect_pages < 1:
            raise ConfigError("detect_pages must be at least 1.")
        if self.ocr_jobs < 0:
            raise ConfigError("ocr_jobs must not be negative.")
        for name in ("max_ocr_megapixels", "tesseract_timeout"):
            value = getattr(self, name)
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or value <= 0):
                raise ConfigError(f"{name} must be a finite positive number.")
        for name in ("ocr_images", "rotate_pages", "deskew", "export_text"):
            if not isinstance(getattr(self, name), bool):
                raise ConfigError(f"{name} must be a boolean.")
        try:
            existing_text = ExistingText(self.existing_text)
        except (TypeError, ValueError) as exc:
            raise ConfigError("existing_text must be skip, redo or force.") from exc
        if existing_text is ExistingText.REDO and self.deskew:
            raise ConfigError(
                "Deskew cannot be combined with redoing an existing OCR layer.",
                hint="Disable deskew, or select skip or force for existing text.",
            )
        return replace(self, language=language, existing_text=existing_text)


@dataclass(frozen=True, slots=True)
class OutputSettings:
    """Where and under which names results are written."""

    output_dir: Path | None = None
    suffix: str = "_ocr"
    conflict: ConflictPolicy = ConflictPolicy.RENAME
    in_place: bool = False  # explicitly replace the original file


def default_workers() -> int:
    """Conservative default: OCR is CPU- and memory-hungry, so use at most half the cores."""
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def config_path() -> Path:
    override = os.environ.get("OCRIZER_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)) / "config.json"


def log_dir() -> Path:
    return Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))


@dataclass(slots=True)
class AppConfig:
    """User-level defaults that persist between runs.

    ``language``, ``workers``, ``suffix`` and ``tesseract_path`` are defaults for the CLI (and
    ``workers``/``suffix``/``tesseract_path`` for the GUI). The ``gui_*`` fields are what the
    GUI remembers about its own controls; they never influence the CLI.
    """

    language: str = AUTO
    workers: int = field(default_factory=default_workers)
    suffix: str = "_ocr"
    tesseract_path: str = ""  # optional explicit tesseract executable or its directory
    gui_language: str = AUTO
    gui_output_dir: str = ""
    gui_export_text: bool = False

    def validate(self) -> AppConfig:
        self.language = normalize_language(self.language)
        self.gui_language = normalize_language(self.gui_language)
        if not isinstance(self.workers, int) or isinstance(self.workers, bool) or self.workers < 1:
            raise ConfigError("workers must be a positive integer.")
        if (not isinstance(self.suffix, str) or not self.suffix
                or any(c in self.suffix for c in '\\/:*?"<>|\x00\r\n')):
            raise ConfigError(f"Invalid output suffix {self.suffix!r}.")
        for name in ("tesseract_path", "gui_output_dir"):
            if not isinstance(getattr(self, name), str):
                raise ConfigError(f"{name} must be a string.")
        if not isinstance(self.gui_export_text, bool):
            raise ConfigError("gui_export_text must be a boolean.")
        return self


def load_config(path: Path | None = None) -> AppConfig:
    """Load the config file (if any), then apply environment overrides."""
    path = path or config_path()
    cfg = AppConfig()
    if path.is_file():
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("top-level JSON value must be an object")
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
        for key, value in raw.items():
            if key in AppConfig.__dataclass_fields__:  # unknown keys are ignored
                setattr(cfg, key, value)
    env = os.environ
    if env.get("OCRIZER_LANGUAGE"):
        cfg.language = env["OCRIZER_LANGUAGE"]
    if env.get("OCRIZER_WORKERS"):
        try:
            cfg.workers = int(env["OCRIZER_WORKERS"])
        except ValueError as exc:
            raise ConfigError("OCRIZER_WORKERS must be an integer.") from exc
    if env.get("OCRIZER_TESSERACT"):
        cfg.tesseract_path = env["OCRIZER_TESSERACT"]
    return cfg.validate()


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    path = path or config_path()
    cfg.validate()
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Separate writers must not share a temporary filename or truncate one another's data.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as stream:
            tmp = Path(stream.name)
            json.dump(asdict(cfg), stream, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as exc:
        raise ConfigError(f"Cannot write config file {path}: {exc}") from exc
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
