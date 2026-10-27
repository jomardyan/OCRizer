"""GUI smoke tests, run headless (QT_QPA_PLATFORM=offscreen)."""

from __future__ import annotations

import functools
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import factory  # noqa: E402
import fake_workers  # noqa: E402
from ocrizer import batch  # noqa: E402
from ocrizer.config import AppConfig  # noqa: E402
from ocrizer.gui import main_window  # noqa: E402
from ocrizer.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialogs(monkeypatch):
    """Record message boxes instead of blocking the test on a modal dialog."""
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text))
    return shown


@pytest.fixture
def window(qapp, dialogs):
    win = MainWindow(AppConfig(), check_on_start=False)
    win.show()
    yield win
    win.close()
    assert dialogs == [], f"unexpected dialog(s): {dialogs}"


def pump(app, condition, timeout=180.0):
    deadline = time.time() + timeout
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert condition(), "timed out"


def statuses(win):
    return [win.table.item(r, 1).text() for r in range(win.table.rowCount())]


def test_initial_state_and_controls(window):
    assert [window.language_box.itemData(i) for i in range(window.language_box.count())] == [
        "auto", "pol", "eng", "pol+eng", "eng+pol",
    ]
    assert window.language_box.currentData() == "auto"  # automatic detection is the default
    assert not window.start_button.isEnabled() and not window.cancel_button.isEnabled()
    assert window.output_edit.text() == ""


def test_adding_removing_and_clearing_files(window, docs):
    a = factory.text_pdf(docs / "a.pdf", [["x"]])
    b = factory.text_pdf(docs / "sub" / "b.pdf", [["x"]])
    (docs / "notes.txt").write_text("not a pdf")

    assert window.add_paths([a, docs / "notes.txt"]) == 1
    assert window.add_paths([a]) == 0  # duplicates are ignored
    assert window.add_paths([docs]) == 1  # folders are scanned recursively; a.pdf already queued
    assert window.table.rowCount() == 2
    assert "not PDF" in window.messages.toPlainText()
    assert window.start_button.isEnabled()

    window.table.selectRow(0)
    window._remove_selected()
    assert window.table.rowCount() == 1
    window._clear()
    assert window.table.rowCount() == 0 and not window.start_button.isEnabled()
    assert b.exists()


def test_drag_and_drop_adds_files(window, docs):
    a = factory.text_pdf(docs / "a.pdf", [["x"]])
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(a)), QUrl.fromLocalFile(str(docs / "readme.md"))])
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(event)
    assert window.table.rowCount() == 1


def test_folder_scan_does_not_queue_previous_results(window, docs):
    factory.text_pdf(docs / "a.pdf", [["x"]])
    factory.text_pdf(docs / "a_ocr.pdf", [["x"]])
    factory.text_pdf(docs / "sub" / "b_ocr_2.pdf", [["x"]])
    assert window.add_paths([docs]) == 1
    assert window.table.item(0, 0).text() == "a.pdf"


@pytest.mark.parametrize("language", ["pol+eng", "eng+pol"])
def test_bilingual_preference_is_restored(qapp, dialogs, language):
    win = MainWindow(AppConfig(gui_language=language), check_on_start=False)
    assert win.language_box.currentData() == language
    win.close()


def test_retry_clears_old_row_details(window, docs):
    from ocrizer.models import JobStatus
    from ocrizer.planner import path_key

    path = factory.text_pdf(docs / "a.pdf", [["x"]])
    window.add_paths([path])
    window._set_row(path_key(path), JobStatus.FAILED, "Previous error")
    window._set_row(path_key(path), JobStatus.PENDING, "")
    assert window.table.item(0, 2).text() == ""
    assert window.table.item(0, 2).toolTip() == ""


def test_gui_entry_point_explains_missing_optional_dependency(monkeypatch, capsys):
    from ocrizer.gui import app

    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", None)
    monkeypatch.setattr(app, "_hide_console_windows", lambda: None)
    assert app.main([]) == 3
    assert 'pip install "ocrizer[gui]"' in capsys.readouterr().err


@pytest.mark.parametrize("override, expected", [(None, "saved.exe"), ("explicit.exe", "explicit.exe")])
def test_gui_entry_point_tesseract_override(qapp, monkeypatch, override, expected):
    from ocrizer.config import save_config
    from ocrizer.gui import app

    configs = []

    class Window:
        def __init__(self, config):
            configs.append(config)

        def show(self):
            pass

    save_config(AppConfig(tesseract_path="saved.exe"))
    monkeypatch.setattr(main_window, "MainWindow", Window)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(app, "_hide_console_windows", lambda: None)
    assert app.main([], tesseract_path=override) == 0
    assert configs[0].tesseract_path == expected


def test_gui_entry_point_warns_and_recovers_from_invalid_config(qapp, dialogs, monkeypatch):
    from ocrizer.config import config_path
    from ocrizer.gui import app

    configs = []

    class Window:
        def __init__(self, config):
            configs.append(config)

        def show(self):
            pass

    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("not valid JSON", encoding="utf-8")
    monkeypatch.setattr(main_window, "MainWindow", Window)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(app, "_hide_console_windows", lambda: None)
    assert app.main([], tesseract_path="explicit.exe") == 0
    assert configs[0].tesseract_path == "explicit.exe"
    assert configs[0].gui_language == "auto"
    assert len(dialogs) == 1 and "default settings" in dialogs[0]


def test_settings_are_remembered(window, docs, qapp, monkeypatch):
    window.add_paths([factory.text_pdf(docs / "a.pdf", [["x"]])])
    window.language_box.setCurrentIndex(1)
    window.output_edit.setText(str(docs / "out"))
    window.text_checkbox.setChecked(True)
    monkeypatch.setattr(main_window, "BatchRunner", functools.partial(
        batch.BatchRunner, verify_dependencies=False, worker_target=fake_workers.ok_worker))
    window.start()
    pump(qapp, lambda: not window.is_running())
    from ocrizer.config import load_config

    saved = load_config()
    assert (saved.gui_language, saved.gui_output_dir, saved.gui_export_text) == (
        "pol", str(docs / "out"), True)
    assert saved.language == "auto"  # GUI choices never leak into CLI defaults


def test_settings_are_remembered_on_close_without_starting(window, docs):
    from ocrizer.config import load_config

    window.language_box.setCurrentIndex(window.language_box.findData("pol+eng"))
    window.output_edit.setText(str(docs / "out"))
    window.text_checkbox.setChecked(True)
    window.close()
    saved = load_config()
    assert (saved.gui_language, saved.gui_output_dir, saved.gui_export_text) == (
        "pol+eng", str(docs / "out"), True,
    )


def test_cancel_stops_running_documents(window, docs, qapp, monkeypatch, tmp_path):
    files = [factory.text_pdf(docs / f"{n}.pdf", [["x"]]) for n in "abc"]
    monkeypatch.setenv("FAKE_PID_FILE", str(tmp_path / "pids"))
    monkeypatch.setattr(main_window, "BatchRunner", functools.partial(
        batch.BatchRunner, verify_dependencies=False, worker_target=fake_workers.sleep_worker))
    window.add_paths(files)
    window.start()
    assert window.is_running() and window.cancel_button.isEnabled()
    assert not window.start_button.isEnabled() and not window.add_button.isEnabled()
    extra = factory.text_pdf(docs / "extra.pdf", [["x"]])
    assert window.add_paths([extra]) == 0  # programmatic additions also obey the busy state
    assert window.table.rowCount() == 3
    pump(qapp, lambda: "Processing" in statuses(window), timeout=60)
    window.cancel()
    pump(qapp, lambda: not window.is_running(), timeout=60)
    assert statuses(window) == ["Cancelled"] * 3
    assert window.status_label.text().startswith("Cancelled")
    assert window.start_button.isEnabled()
    assert not list(docs.glob("*_ocr.pdf"))


def test_close_during_processing_waits_for_worker_without_blocking(
    window, docs, qapp, monkeypatch,
):
    monkeypatch.setattr(main_window, "BatchRunner", functools.partial(
        batch.BatchRunner, verify_dependencies=False, worker_target=fake_workers.sleep_worker))
    questions = []

    def confirm(*args, **kwargs):
        questions.append(True)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    window.add_paths([factory.text_pdf(docs / "a.pdf", [["x"]])])
    window.start()
    pump(qapp, lambda: "Processing" in statuses(window), timeout=60)
    thread = window._thread
    started = time.monotonic()
    window.close()
    assert time.monotonic() - started < 1
    assert window.isVisible() and window._thread is thread
    assert window._close_requested and window._cancelling
    window.close()
    assert len(questions) == 1
    pump(qapp, lambda: not window.is_running() and not window.isVisible(), timeout=60)
    assert statuses(window) == ["Cancelled"]


@pytest.mark.integration
def test_processing_documents_end_to_end(window, docs, qapp):
    polish = factory.scanned_pdf(docs / "pl.pdf", [factory.POLISH_TEXT])
    english = factory.scanned_pdf(docs / "en.pdf", [factory.ENGLISH_TEXT])
    broken = factory.corrupt_pdf(docs / "broken.pdf", polish)
    digital = factory.text_pdf(docs / "digital.pdf", [["Already text"]])
    out = docs / "results"
    window.add_paths([polish, english, broken, digital])
    window.output_edit.setText(str(out))
    window.start()
    pump(qapp, lambda: not window.is_running(), timeout=300)

    by_name = {window.table.item(r, 0).text(): window.table.item(r, 1).text()
               for r in range(window.table.rowCount())}
    assert by_name == {"pl.pdf": "Done", "en.pdf": "Done", "broken.pdf": "Failed",
                       "digital.pdf": "Skipped"}
    assert (out / "pl_ocr.pdf").is_file() and (out / "en_ocr.pdf").is_file()
    assert window.progress.value() == window.progress.maximum() == 4
    assert "broken.pdf" in window.messages.toPlainText()
    assert "1 failed" in window.status_label.text()

    # A second run only retries what has not been done: nothing is processed twice.
    window.start()
    pump(qapp, lambda: not window.is_running(), timeout=300)
    assert sorted(p.name for p in out.glob("*.pdf")) == ["en_ocr.pdf", "pl_ocr.pdf"]
