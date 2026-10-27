from __future__ import annotations

import pytest

import factory
from ocrizer.errors import ErrorCode, InvalidPdfError
from ocrizer.pdfinfo import analyze_pdf, extract_text, render_page, validate_pdf

needs_font = pytest.mark.skipif(not factory.has_font(), reason="no TrueType font available")


def code_of(path) -> ErrorCode:
    with pytest.raises(InvalidPdfError) as excinfo:
        validate_pdf(path)
    return excinfo.value.code


def test_valid_pdf(docs):
    pdf = factory.text_pdf(docs / "ok.pdf", [["Hello"], ["World"]])
    assert validate_pdf(pdf) == 2


def test_missing_file(docs):
    assert code_of(docs / "missing.pdf") is ErrorCode.NOT_FOUND


def test_directory_is_not_a_pdf(docs):
    assert code_of(docs) is ErrorCode.NOT_A_PDF


def test_empty_file(docs):
    assert code_of(factory.empty_pdf(docs / "empty.pdf")) is ErrorCode.EMPTY


def test_zero_page_pdf(docs):
    assert code_of(factory.zero_page_pdf(docs / "zero.pdf")) is ErrorCode.EMPTY


def test_not_a_pdf(docs):
    path = docs / "notes.pdf"
    path.write_text("this is plain text, not a pdf")
    assert code_of(path) is ErrorCode.NOT_A_PDF


def test_corrupted_pdf(docs):
    source = factory.text_pdf(docs / "source.pdf", [["Hello"]] * 5)
    assert code_of(factory.corrupt_pdf(docs / "corrupt.pdf", source)) is ErrorCode.CORRUPT


def test_encrypted_pdf(docs):
    source = factory.text_pdf(docs / "source.pdf", [["Hello"]])
    error = code_of(factory.encrypted_pdf(docs / "locked.pdf", source))
    assert error is ErrorCode.ENCRYPTED


def test_text_detection_digital_pdf(docs):
    report = analyze_pdf(factory.text_pdf(docs / "digital.pdf", [["Hello world"], ["Second page"]]))
    assert report.page_count == 2
    assert report.text_pages == (0, 1)
    assert report.fully_searchable and report.pages_needing_ocr == ()
    assert "Hello world" in extract_text(report.path)


@needs_font
def test_text_detection_image_only_pdf(docs):
    report = analyze_pdf(factory.scanned_pdf(docs / "scan.pdf", [["Scan"], ["Scan 2"]]))
    assert report.text_pages == ()
    assert report.pages_needing_ocr == (0, 1)
    assert not report.fully_searchable


@needs_font
def test_text_detection_mixed_pdf(docs):
    report = analyze_pdf(factory.mixed_pdf(docs / "mixed.pdf", [["Scanned"]], ["Digital page"]))
    assert report.page_count == 2
    assert report.text_pages == (1,)
    assert report.pages_needing_ocr == (0,)


@needs_font
def test_text_page_with_big_image_needs_image_ocr(docs):
    pdf = factory.image_text_pdf(docs / "figure.pdf", "Typeset body text", ["Text inside a chart"])
    report = analyze_pdf(pdf)
    assert report.text_pages == (0,) and report.image_pages == (0,)
    assert report.pages_needing_ocr == (0,) and not report.fully_searchable


@needs_font
def test_small_images_are_ignored(docs):
    pdf = factory.image_text_pdf(docs / "logo.pdf", "Body", ["Logo"], image_width_pt=30)
    report = analyze_pdf(pdf)
    assert report.image_pages == () and report.fully_searchable


@needs_font
def test_page_with_ocr_layer_is_not_reported_again(docs):
    """A scan that already has an invisible text layer must not be processed a second time."""
    import ocrmypdf

    scan = factory.scanned_pdf(docs / "scan.pdf", [factory.ENGLISH_TEXT])
    try:
        ocrmypdf.ocr(scan, docs / "ocr.pdf", language=["eng"], progress_bar=False, jobs=1)
    except Exception as exc:  # Tesseract unavailable: nothing to check
        pytest.skip(f"OCR not available: {exc}")
    report = analyze_pdf(docs / "ocr.pdf")
    assert report.text_pages == (0,) and report.image_pages == () and report.fully_searchable


@needs_font
def test_render_page_returns_grayscale_image(docs):
    pdf = factory.scanned_pdf(docs / "scan.pdf", [["Scan"]])
    image = render_page(pdf, 0, dpi=100)
    assert image.mode == "L" and image.width > 100


def test_unicode_path(docs):
    path = factory.text_pdf(docs / "zażółć gęślą — dokument.pdf", [["Hello"]])
    assert analyze_pdf(path).fully_searchable
