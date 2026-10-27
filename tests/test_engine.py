"""Engine contracts that can be checked without launching Tesseract."""

from dataclasses import replace

import pytest

import factory
from ocrizer import engine
from ocrizer.config import OcrSettings
from ocrizer.errors import ErrorCode, OcrFailedError
from ocrizer.models import JobRequest, JobStatus
from ocrizer.pdfinfo import PdfReport, extract_text


@pytest.fixture
def request_for_pdf(docs, tmp_path):
    source = factory.text_pdf(docs / "source.pdf", [["Original searchable text"]])
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return JobRequest(0, source, scratch, docs / ".partial.pdf", OcrSettings(language="eng"), 1)


def test_searchable_document_exports_text_without_running_ocr(request_for_pdf, monkeypatch):
    request = replace(request_for_pdf, settings=OcrSettings(export_text=True))
    monkeypatch.setattr(engine, "_run_ocrmypdf", lambda *a: pytest.fail("OCR must be skipped"))
    outcome = engine.process_document(request, lambda event: None)
    assert outcome.status is JobStatus.SKIPPED
    assert outcome.text_file.read_text(encoding="utf-8") == extract_text(request.input_path)
    assert not request.partial_pdf.exists()


def test_image_ocr_only_reprocesses_pages_without_complete_text_layers(request_for_pdf, monkeypatch):
    import ocrmypdf

    report = PdfReport(request_for_pdf.input_path, 4, (0, 1, 3), (1,))
    captured = {}
    monkeypatch.setattr(ocrmypdf, "ocr", lambda *a, **kwargs: captured.update(kwargs))
    engine._run_ocrmypdf(request_for_pdf, "eng", report, lambda event: None)
    assert captured["redo_ocr"] is True
    assert captured["pages"] == "2,3"
    assert engine._ocr_progress._sink is None


def test_deskew_image_text_fails_with_actionable_hint(request_for_pdf, monkeypatch):
    request = replace(request_for_pdf, settings=OcrSettings(language="eng", deskew=True))
    report = PdfReport(request.input_path, 1, (0,), (0,))
    monkeypatch.setattr(engine, "analyze_pdf", lambda path: report)
    outcome = engine.process_document(request, lambda event: None)
    assert outcome.status is JobStatus.FAILED
    assert outcome.error_code is ErrorCode.OCR_FAILED
    assert "--no-ocr-images" in outcome.hint
    assert not request.partial_pdf.exists()


def test_ocr_option_errors_are_reported_and_progress_sink_is_cleared(request_for_pdf, monkeypatch):
    import ocrmypdf

    def invalid_options(*args, **kwargs):
        raise ValueError("incompatible settings")

    monkeypatch.setattr(ocrmypdf, "ocr", invalid_options)
    report = PdfReport(request_for_pdf.input_path, 1, ())
    with pytest.raises(OcrFailedError, match="incompatible settings"):
        engine._run_ocrmypdf(request_for_pdf, "eng", report, lambda event: None)
    assert engine._ocr_progress._sink is None


def test_integrity_check_rejects_changed_page_count(request_for_pdf):
    factory.text_pdf(request_for_pdf.partial_pdf, [["First"], ["Unexpected second page"]])
    with pytest.raises(OcrFailedError, match="page count changed"):
        engine._verify_output(request_for_pdf.input_path, request_for_pdf.partial_pdf, 1)


def test_integrity_check_rejects_changed_page_size(request_for_pdf):
    import pikepdf

    with pikepdf.open(request_for_pdf.input_path) as pdf:
        pdf.pages[0].mediabox = [0, 0, 100, 100]
        pdf.save(request_for_pdf.partial_pdf)
    with pytest.raises(OcrFailedError, match="changed size"):
        engine._verify_output(request_for_pdf.input_path, request_for_pdf.partial_pdf, 1)


@pytest.mark.parametrize("contents", [None, b"", b"not a PDF"])
def test_integrity_check_rejects_missing_or_invalid_output(request_for_pdf, contents):
    if contents is not None:
        request_for_pdf.partial_pdf.write_bytes(contents)
    with pytest.raises(OcrFailedError):
        engine._verify_output(request_for_pdf.input_path, request_for_pdf.partial_pdf, 1)
