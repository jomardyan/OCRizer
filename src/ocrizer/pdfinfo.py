"""PDF validation and per-page text-layer analysis.

Validation uses pikepdf (qpdf); text detection and rendering use pypdfium2. Both open the file
lazily and every page is processed and released one at a time, so memory use does not grow with
document size.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from PIL import Image

from ocrizer.errors import ErrorCode, InvalidPdfError

# A page counts as "already searchable" when it carries at least this many non-blank characters
# of extractable text. This mirrors OCRmyPDF's own skip-text behaviour (any text means skip).
MIN_TEXT_CHARS = 1
_TEXT_PROBE_CHARS = 2000  # never extract more than this per page just to decide

# A raster image can hide text (charts, screenshots, scanned tables) that the text layer lacks.
# Only images at least this big are worth a Tesseract pass; logos and icons are ignored.
MIN_IMAGE_PAGE_FRACTION = 0.03
MIN_IMAGE_SIDE_PT = 36.0
_TEXT_RENDER_INVISIBLE = 3  # PDF text render mode used for OCR layers


@dataclass(frozen=True, slots=True)
class PdfReport:
    path: Path
    page_count: int
    text_pages: tuple[int, ...]  # 0-based indices of pages that already contain text
    # Pages with visible text *and* a sizeable raster image but no OCR layer yet: the text
    # layer is complete for the typeset text, yet whatever is written inside the image is not.
    image_pages: tuple[int, ...] = ()

    @property
    def pages_needing_ocr(self) -> tuple[int, ...]:
        have = set(self.text_pages) - set(self.image_pages)
        return tuple(i for i in range(self.page_count) if i not in have)

    @property
    def fully_searchable(self) -> bool:
        return self.page_count > 0 and not self.pages_needing_ocr


def validate_pdf(path: Path) -> int:
    """Check that ``path`` is a usable, unencrypted, non-empty PDF and return its page count.

    Raises :class:`InvalidPdfError` with a specific :class:`ErrorCode` otherwise.
    """
    if not path.exists():
        raise InvalidPdfError(f"File not found: {path}", code=ErrorCode.NOT_FOUND)
    if not path.is_file():
        raise InvalidPdfError(f"Not a file: {path}", code=ErrorCode.NOT_A_PDF)
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(1024)
    except OSError as exc:
        raise InvalidPdfError(
            f"Cannot read file: {exc.strerror or exc}", code=ErrorCode.NOT_FOUND
        ) from exc
    if size == 0:
        raise InvalidPdfError("The file is empty (0 bytes).", code=ErrorCode.EMPTY)
    if b"%PDF-" not in head:
        raise InvalidPdfError(
            "Not a PDF file (missing %PDF header).",
            code=ErrorCode.NOT_A_PDF,
            hint="Only PDF documents are supported.",
        )
    try:
        with pikepdf.open(path) as pdf:
            if pdf.is_encrypted:
                raise InvalidPdfError(
                    "The PDF is encrypted or password-protected.",
                    code=ErrorCode.ENCRYPTED,
                    hint="Remove the password first (e.g. `qpdf --decrypt --password=... in.pdf out.pdf`).",
                )
            pages = len(pdf.pages)
    except pikepdf.PasswordError as exc:
        raise InvalidPdfError(
            "The PDF is encrypted or password-protected.",
            code=ErrorCode.ENCRYPTED,
            hint="Remove the password first (e.g. `qpdf --decrypt --password=... in.pdf out.pdf`).",
        ) from exc
    except (pikepdf.PdfError, RuntimeError, ValueError, OSError) as exc:
        raise InvalidPdfError(
            f"The PDF is corrupted or unreadable: {_first_line(exc, path)}", code=ErrorCode.CORRUPT
        ) from exc
    if pages == 0:
        raise InvalidPdfError("The PDF contains no pages.", code=ErrorCode.EMPTY)
    return pages


def analyze_pdf(path: Path) -> PdfReport:
    """Validate ``path`` and determine which pages already contain extractable text."""
    expected = validate_pdf(path)
    text_pages: list[int] = []
    image_pages: list[int] = []
    count = 0
    try:
        doc = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        raise InvalidPdfError(
            f"The PDF is corrupted or unreadable: {_first_line(exc, path)}", code=ErrorCode.CORRUPT
        ) from exc
    try:
        count = len(doc)
        for index in range(count):
            page = doc[index]
            textpage = page.get_textpage()
            try:
                n = textpage.count_chars()
                text = textpage.get_text_range(0, min(n, _TEXT_PROBE_CHARS)) if n else ""
                has_text = len("".join(text.split())) >= MIN_TEXT_CHARS
                if has_text:
                    text_pages.append(index)
                    if _has_unrecognised_image_text(page):
                        image_pages.append(index)
            finally:
                textpage.close()
                page.close()
    finally:
        doc.close()
    if count == 0 < expected:
        raise InvalidPdfError("The PDF pages could not be read.", code=ErrorCode.CORRUPT)
    return PdfReport(path, count, tuple(text_pages), tuple(image_pages))


def _has_unrecognised_image_text(page: pdfium.PdfPage) -> bool:
    """True for a page with visible text and a big image but no invisible (OCR) text.

    Pages that already carry an OCR layer (invisible text, e.g. a scan processed earlier or by
    this tool) are *not* reported, so results are never OCR'd a second time.
    """
    width, height = page.get_size()
    page_area = max(width * height, 1.0)
    has_big_image = False
    for obj in page.get_objects():
        if obj.type == pdfium_raw.FPDF_PAGEOBJ_TEXT:
            if pdfium_raw.FPDFTextObj_GetTextRenderMode(obj.raw) == _TEXT_RENDER_INVISIBLE:
                return False
        elif obj.type == pdfium_raw.FPDF_PAGEOBJ_IMAGE and not has_big_image:
            left, bottom, right, top = obj.get_bounds()
            w, h = right - left, top - bottom
            has_big_image = (
                min(w, h) >= MIN_IMAGE_SIDE_PT and w * h / page_area >= MIN_IMAGE_PAGE_FRACTION
            )
    return has_big_image


def render_page(path: Path, index: int, dpi: int = 200, max_side: int = 4000) -> Image.Image:
    """Render one page to a grayscale PIL image (used for language-detection probes)."""
    doc = pdfium.PdfDocument(str(path))
    try:
        page = doc[index]
        try:
            width, height = page.get_size()
            scale = min(dpi / 72.0, max_side / max(width, height, 1.0))
            bitmap = page.render(scale=scale)
            try:
                return bitmap.to_pil().convert("L")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()


def iter_page_text(path: Path, pages: Sequence[int] | None = None) -> Iterator[str]:
    """Yield the text layer of a PDF page by page (all pages, or the given 0-based ones)."""
    doc = pdfium.PdfDocument(str(path))
    try:
        for index in pages if pages is not None else range(len(doc)):
            page = doc[index]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
            finally:
                textpage.close()
                page.close()
            yield text.replace("\r\n", "\n")
    finally:
        doc.close()


def extract_text(path: Path, pages: Sequence[int] | None = None) -> str:
    """Extract the text layer as one string, pages separated by form feeds."""
    return "\f".join(iter_page_text(path, pages))


def _first_line(exc: BaseException, path: Path) -> str:
    """First line of an error message, without the (often very long) file path in it."""
    lines = [ln for ln in str(exc).replace(str(path), path.name).strip().splitlines() if ln.strip()]
    return lines[0].replace(f"{path.name}: ", "", 1) if lines else exc.__class__.__name__
