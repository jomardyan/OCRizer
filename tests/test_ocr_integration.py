"""End-to-end tests with real Tesseract/OCRmyPDF on generated documents.

They are skipped automatically when Tesseract or the pol/eng language packs are missing.
"""

from __future__ import annotations

import hashlib

import pikepdf
import pytest

import factory
from conftest import normalize, similarity
from ocrizer.batch import BatchRunner
from ocrizer.config import ConflictPolicy, ExistingText, OcrSettings, OutputSettings
from ocrizer.errors import ErrorCode, MissingDependencyError
from ocrizer.models import JobStatus
from ocrizer.pdfinfo import analyze_pdf, extract_text
from ocrizer.planner import plan_jobs

pytestmark = pytest.mark.integration


def run(files, settings=None, output=None, *, workers=1, overwrite=False, **plan_kwargs):
    settings = settings or OcrSettings()
    plan = plan_jobs(
        files, output or OutputSettings(), export_text=settings.export_text, **plan_kwargs
    )
    return BatchRunner(plan.jobs, settings, workers=workers, overwrite=overwrite).run()


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def page_sizes(path):
    with pikepdf.open(path) as pdf:
        return [tuple(round(float(v), 1) for v in page.mediabox) for page in pdf.pages]


def test_single_polish_scan_becomes_searchable(docs):
    source = factory.scanned_pdf(docs / "umowa.pdf", [factory.POLISH_TEXT])
    before = digest(source)
    assert not analyze_pdf(source).text_pages  # really image-only

    (result,) = run([source]).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert result.output_path == docs / "umowa_ocr.pdf"
    assert result.language == "pol" and result.detection.confident
    text = extract_text(result.output_path)
    assert similarity(" ".join(factory.POLISH_TEXT), text) > 0.95
    for diacritics_phrase in ("zażółć gęślą jaźń", "pchnąć w tę łódź jeża", "ośm skrzyń"):
        assert diacritics_phrase in normalize(text)
    assert analyze_pdf(result.output_path).fully_searchable
    assert digest(source) == before, "the original must never be modified"


def test_single_english_scan_becomes_searchable(docs):
    source = factory.scanned_pdf(docs / "agreement.pdf", [factory.ENGLISH_TEXT])
    (result,) = run([source]).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert result.language == "eng"
    assert similarity(" ".join(factory.ENGLISH_TEXT), extract_text(result.output_path)) > 0.95


def test_explicit_language_skips_detection(docs):
    source = factory.scanned_pdf(docs / "a.pdf", [factory.POLISH_TEXT])
    (result,) = run([source], OcrSettings(language="pol")).results
    assert result.language == "pol" and result.detection is None
    assert "gęślą" in normalize(extract_text(result.output_path))


IMAGE_LINES = ["Potential versus current density", "Langmuir adsorption isotherm"]


def test_text_inside_images_is_recognised_and_typeset_text_kept(docs):
    source = factory.image_text_pdf(docs / "figure.pdf", "Typeset body text", IMAGE_LINES)
    before = digest(source)
    (result,) = run([source], OcrSettings(language="eng")).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert result.pages_ocr == 1
    text = normalize(extract_text(result.output_path))
    assert "typeset body text" in text
    assert "langmuir adsorption isotherm" in text
    assert digest(source) == before
    assert page_sizes(result.output_path) == page_sizes(source)

    # The result already has an OCR layer, so a second pass leaves it alone.
    again = run([result.output_path], OcrSettings(language="eng")).results[0]
    assert again.status is JobStatus.SKIPPED


def test_image_ocr_can_be_disabled(docs):
    source = factory.image_text_pdf(docs / "figure.pdf", "Typeset body text", IMAGE_LINES)
    (result,) = run([source], OcrSettings(language="eng", ocr_images=False)).results
    assert result.status is JobStatus.SKIPPED


def test_image_ocr_preserves_existing_hidden_text_on_other_pages(docs):
    scan = factory.scanned_pdf(docs / "scan.pdf", [factory.ENGLISH_TEXT])
    layer = factory.text_pdf(docs / "layer.pdf", [["Archived searchable layer must survive"]])
    figure = factory.image_text_pdf(docs / "figure.pdf", "Typeset body text", IMAGE_LINES)
    source = docs / "combined.pdf"
    with pikepdf.open(scan) as document, pikepdf.open(layer) as overlay, pikepdf.open(figure) as addition:
        overlay.pages[0].contents_coalesce()
        contents = overlay.pages[0].obj.Contents
        contents.write(contents.read_bytes().replace(b"BT", b"BT 3 Tr"))
        document.pages[0].add_overlay(overlay.pages[0])
        document.pages.extend(addition.pages)
        document.save(source)
    assert analyze_pdf(source).pages_needing_ocr == (1,)

    (result,) = run([source], OcrSettings(language="eng")).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert result.pages_ocr == 1
    pages = extract_text(result.output_path).split("\f")
    assert "Archived searchable layer must survive" in pages[0]
    assert "langmuir adsorption isotherm" in normalize(pages[1])


def test_mixed_language_document_uses_both_languages(docs):
    source = factory.scanned_pdf(
        docs / "mixed.pdf", [factory.POLISH_TEXT, factory.ENGLISH_TEXT]
    )
    (result,) = run([source]).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert result.language == "pol+eng"
    pages = [normalize(t) for t in extract_text(result.output_path).split("\f")]
    assert "zażółć gęślą jaźń" in pages[0]
    assert "quick brown fox" in pages[1]
    assert similarity(" ".join(factory.POLISH_TEXT), pages[0]) > 0.93
    assert similarity(" ".join(factory.ENGLISH_TEXT), pages[1]) > 0.93


def test_page_mixing_both_languages(docs):
    lines = factory.POLISH_TEXT[:2] + factory.ENGLISH_TEXT[:2]
    source = factory.scanned_pdf(docs / "page.pdf", [lines])
    (result,) = run([source]).results
    assert result.language == "pol+eng"
    assert similarity(" ".join(lines), extract_text(result.output_path)) > 0.93


def test_searchable_pdf_is_not_reprocessed(docs):
    source = factory.text_pdf(docs / "digital.pdf", [["Born digital document"], ["Second page"]])
    (result,) = run([source]).results
    assert result.status is JobStatus.SKIPPED
    assert "Already searchable" in result.message
    assert not (docs / "digital_ocr.pdf").exists()


def test_result_of_a_previous_run_is_not_reprocessed(docs):
    source = factory.scanned_pdf(docs / "a.pdf", [factory.ENGLISH_TEXT])
    (first,) = run([source]).results
    (second,) = run([first.output_path], output=OutputSettings(suffix="_again")).results
    assert second.status is JobStatus.SKIPPED
    assert not (docs / "a_ocr_again.pdf").exists()


def test_mixed_pdf_only_scanned_pages_are_ocred(docs):
    source = factory.mixed_pdf(docs / "mixed.pdf", [factory.ENGLISH_TEXT], ["Digital page text"])
    (result,) = run([source]).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert (result.pages, result.pages_ocr) == (2, 1)
    pages = extract_text(result.output_path).split("\f")
    assert "quick brown fox" in normalize(pages[0])
    assert "Digital page text" in pages[1]


def test_force_mode_ocrs_pages_that_have_text(docs):
    source = factory.text_pdf(docs / "digital.pdf", [["Searchable already"]])
    settings = OcrSettings(existing_text=ExistingText.FORCE, language="eng")
    (result,) = run([source], settings).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert "searchable already" in normalize(extract_text(result.output_path))


def test_redo_mode_replaces_old_ocr_layer(docs):
    source = factory.scanned_pdf(docs / "a.pdf", [factory.ENGLISH_TEXT])
    (first,) = run([source]).results
    settings = OcrSettings(existing_text=ExistingText.REDO, language="eng")
    (second,) = run([first.output_path], settings, OutputSettings(suffix="_redo")).results
    assert second.status is JobStatus.SUCCEEDED, second.message
    assert "quick brown fox" in normalize(extract_text(second.output_path))


def test_page_count_order_and_dimensions_are_preserved(docs):
    source = factory.scanned_pdf(
        docs / "three.pdf",
        [factory.ENGLISH_TEXT, factory.POLISH_TEXT, factory.ENGLISH_TEXT[:2]],
    )
    (result,) = run([source]).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert page_sizes(result.output_path) == page_sizes(source)
    pages = [normalize(t) for t in extract_text(result.output_path).split("\f")]
    assert len(pages) == 3
    assert "quick brown fox" in pages[0] and "gęślą" in pages[1] and "agreement" in pages[2]


def test_upside_down_scan_is_rotated_for_recognition(docs):
    # Tesseract's orientation detection needs a reasonably full page to be confident.
    lines = factory.ENGLISH_TEXT * 4
    source = factory.scanned_pdf(docs / "flipped.pdf", [lines], angle=180, font_px=40)
    (result,) = run([source], OcrSettings(language="eng")).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert similarity(" ".join(lines), extract_text(result.output_path)) > 0.9
    with pikepdf.open(result.output_path) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) % 360 == 180  # fixed via metadata, no re-render


def test_text_export(docs):
    source = factory.scanned_pdf(docs / "a.pdf", [factory.POLISH_TEXT])
    (result,) = run([source], OcrSettings(export_text=True)).results
    assert result.text_path == docs / "a_ocr.txt"
    content = result.text_path.read_text(encoding="utf-8")
    assert "zażółć gęślą jaźń" in normalize(content)


def test_unicode_filenames_and_paths_with_spaces(docs):
    folder = docs / "ścieżka z spacjami ąęłń"
    source = factory.scanned_pdf(folder / "dokument źdźbło — wersja 2.pdf", [factory.POLISH_TEXT])
    out = docs / "wyniki łódź"
    (result,) = run([source], OcrSettings(export_text=True), OutputSettings(output_dir=out)).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert result.output_path == out / "dokument źdźbło — wersja 2_ocr.pdf"
    assert result.output_path.is_file() and result.text_path.is_file()
    assert "gęślą" in normalize(extract_text(result.output_path))


def test_output_conflict_creates_new_name_and_keeps_old_file(docs):
    source = factory.scanned_pdf(docs / "a.pdf", [factory.ENGLISH_TEXT])
    (docs / "a_ocr.pdf").write_bytes(b"precious existing file")
    (result,) = run([source]).results
    assert result.output_path == docs / "a_ocr_1.pdf"
    assert (docs / "a_ocr.pdf").read_bytes() == b"precious existing file"


def test_in_place_replaces_the_original_only_when_requested(docs):
    source = factory.scanned_pdf(docs / "a.pdf", [factory.ENGLISH_TEXT])
    output = OutputSettings(in_place=True, conflict=ConflictPolicy.OVERWRITE)
    (result,) = run([source], output=output, overwrite=True).results
    assert result.status is JobStatus.SUCCEEDED, result.message
    assert result.output_path == source
    assert analyze_pdf(source).fully_searchable
    assert [p.name for p in docs.iterdir()] == ["a.pdf"]  # no leftovers next to it


def test_batch_with_good_and_bad_documents(docs):
    good1 = factory.scanned_pdf(docs / "good1.pdf", [factory.POLISH_TEXT])
    good2 = factory.scanned_pdf(docs / "good2.pdf", [factory.ENGLISH_TEXT])
    digital = factory.text_pdf(docs / "digital.pdf", [["Already text"]])
    corrupt = factory.corrupt_pdf(docs / "corrupt.pdf", good1)
    locked = factory.encrypted_pdf(docs / "locked.pdf", good2)
    empty = factory.empty_pdf(docs / "empty.pdf")
    zero = factory.zero_page_pdf(docs / "zero.pdf")
    plan = plan_jobs([docs], OutputSettings(), batch=True)
    summary = BatchRunner(plan.jobs, OcrSettings(), workers=3).run()

    by_name = {r.job.input_path.name: r for r in summary.results}
    assert by_name["good1.pdf"].status is JobStatus.SUCCEEDED
    assert by_name["good2.pdf"].status is JobStatus.SUCCEEDED
    assert by_name["digital.pdf"].status is JobStatus.SKIPPED
    assert by_name["corrupt.pdf"].error_code is ErrorCode.CORRUPT
    assert by_name["locked.pdf"].error_code is ErrorCode.ENCRYPTED
    assert by_name["empty.pdf"].error_code is ErrorCode.EMPTY
    assert by_name["zero.pdf"].error_code is ErrorCode.EMPTY
    assert summary.describe() == "2 succeeded, 4 failed, 1 skipped, 0 cancelled"
    assert (docs / "good1_ocr.pdf").is_file() and (docs / "good2_ocr.pdf").is_file()
    assert not [p for p in docs.iterdir() if p.name.startswith(".ocrizer-")]
    for original in (corrupt, locked, empty, zero):
        assert original.exists()


def test_digitally_signed_pdf_is_refused_untouched(docs):
    scan = factory.scanned_pdf(docs / "scan.pdf", [factory.ENGLISH_TEXT])
    signed = factory.signed_pdf(docs / "signed.pdf", scan)
    before = digest(signed)
    (result,) = run([signed], OcrSettings(language="eng")).results
    assert result.status is JobStatus.FAILED and result.error_code is ErrorCode.SIGNED
    assert "signature" in result.message and result.hint
    assert digest(signed) == before and not (docs / "signed_ocr.pdf").exists()


def test_batch_runs_documents_in_parallel(docs):
    files = [
        factory.scanned_pdf(docs / f"doc{i}.pdf", [factory.ENGLISH_TEXT if i % 2 else factory.POLISH_TEXT])
        for i in range(4)
    ]
    summary = run(files, workers=2)
    assert summary.succeeded == 4
    languages = [r.language for r in summary.results]
    assert languages == ["pol", "eng", "pol", "eng"]
    assert len({r.output_path for r in summary.results}) == 4


def test_missing_tesseract_raises_actionable_error(docs):
    source = factory.scanned_pdf(docs / "a.pdf", [factory.ENGLISH_TEXT])
    plan = plan_jobs([source], OutputSettings())
    runner = BatchRunner(plan.jobs, OcrSettings(), tesseract_path=str(docs / "no-such-tesseract"))
    with pytest.raises(MissingDependencyError) as excinfo:
        runner.run()
    assert "Tesseract" in excinfo.value.message and excinfo.value.hint
    assert not (docs / "a_ocr.pdf").exists()


def test_cancelling_a_real_ocr_run_stops_tesseract_and_cleans_up(docs, tmp_path, monkeypatch):
    import tempfile
    import threading
    import time

    import psutil

    scratch_root = tmp_path / "tmp"
    scratch_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch_root))

    source = factory.scanned_pdf(docs / "long.pdf", [factory.ENGLISH_TEXT] * 40)
    plan = plan_jobs([source], OutputSettings())
    seen_ocr = threading.Event()

    def on_event(event):
        if event.progress is not None and event.progress.stage == "OCR":
            seen_ocr.set()

    runner = BatchRunner(plan.jobs, OcrSettings(language="eng"), workers=1, on_event=on_event)
    holder = {}
    thread = threading.Thread(target=lambda: holder.setdefault("summary", runner.run()))
    started = time.time()
    thread.start()
    assert seen_ocr.wait(120), "OCR never started"
    runner.cancel()
    thread.join(60)
    assert not thread.is_alive()

    (result,) = holder["summary"].results
    assert result.status is JobStatus.CANCELLED
    assert not (docs / "long_ocr.pdf").exists()
    assert not [p for p in docs.iterdir() if p.name.startswith(".")]
    assert list(scratch_root.iterdir()) == [], "scratch directories must be removed"
    stray = [
        p for p in psutil.process_iter(["name", "create_time"])
        if (p.info["name"] or "").lower().startswith("tesseract") and p.info["create_time"] >= started
    ]
    assert stray == [], "Tesseract processes must not outlive a cancelled job"
