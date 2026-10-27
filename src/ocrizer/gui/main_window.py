"""Main window of the desktop application.

The window contains no OCR logic. It builds a plan with :func:`ocrizer.planner.plan_jobs` and
runs it with :class:`ocrizer.batch.BatchRunner` on a ``QThread`` - the very same code path as
the command-line interface - and only translates runner events into widget updates.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QDragEnterEvent, QDropEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ocrizer import __version__
from ocrizer.batch import BatchRunner
from ocrizer.config import AppConfig, ConflictPolicy, OcrSettings, OutputSettings, save_config
from ocrizer.deps import check_dependencies
from ocrizer.errors import OcrizerError
from ocrizer.models import BatchEvent, BatchSummary, JobStatus
from ocrizer.planner import discover_pdfs, path_key, plan_jobs

LANGUAGES = (
    ("Auto detect", "auto"),
    ("Polish", "pol"),
    ("English", "eng"),
    ("Polish + English", "pol+eng"),
    ("English + Polish", "eng+pol"),
)

COLUMN_DOCUMENT, COLUMN_STATUS, COLUMN_DETAILS = range(3)

_STATUS_TEXT = {
    JobStatus.PENDING: "Queued",
    JobStatus.RUNNING: "Processing",
    JobStatus.SUCCEEDED: "Done",
    JobStatus.FAILED: "Failed",
    JobStatus.CANCELLED: "Cancelled",
    JobStatus.SKIPPED: "Skipped",
}
_STATUS_COLOR = {
    JobStatus.SUCCEEDED: QColor("#2e7d32"),
    JobStatus.FAILED: QColor("#c62828"),
    JobStatus.CANCELLED: QColor("#ef6c00"),
    JobStatus.SKIPPED: QColor("#757575"),
}
_DONE = (JobStatus.SUCCEEDED, JobStatus.SKIPPED)


@dataclass(slots=True)
class _Row:
    path: Path
    status: JobStatus = JobStatus.PENDING


class BatchThread(QThread):
    """Runs a BatchRunner off the GUI thread and relays its events as Qt signals."""

    job_event = Signal(object)  # BatchEvent
    completed = Signal(object)  # BatchSummary
    error = Signal(str)

    def __init__(self, jobs, settings: OcrSettings, parent: QObject | None = None, **runner_kwargs):
        super().__init__(parent)
        self._runner = BatchRunner(jobs, settings, on_event=self.job_event.emit, **runner_kwargs)

    def run(self) -> None:  # executes in the worker thread
        try:
            summary = self._runner.run()
        except OcrizerError as exc:
            self.error.emit(f"{exc.message}\n{exc.hint or ''}".strip())
        except Exception as exc:  # noqa: BLE001 - never let a worker thread die silently
            self.error.emit(f"Unexpected error: {exc}")
        else:
            self.completed.emit(summary)

    def cancel(self) -> None:
        self._runner.cancel()


class QueueTable(QTableWidget):
    """The document list; draws a hint while it is empty (it doubles as the drop target)."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.rowCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().color(self.palette().ColorRole.PlaceholderText))
            painter.drawText(
                self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                "Drag and drop PDF files or folders here\nor use \"Add PDFs…\"",
            )


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, *, check_on_start: bool = True):
        super().__init__()
        self._config = config
        self._rows: dict[str, _Row] = {}
        self._thread: BatchThread | None = None
        self._total_jobs = 0
        self._finished_jobs = 0
        self._cancelling = False
        self._close_requested = False

        self.setWindowTitle(f"OCRizer {__version__} - searchable PDFs (Polish / English)")
        self.setAcceptDrops(True)
        self.resize(820, 620)
        self._build_ui()
        self._update_buttons()
        if check_on_start:
            QTimer.singleShot(0, self._startup_dependency_check)

    # ---------------------------------------------------------------- UI construction
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        top = QHBoxLayout()
        self.add_button = QPushButton("Add PDFs…")
        self.add_button.clicked.connect(self._choose_files)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self._remove_selected)
        self.clear_button = QPushButton("Clear all")
        self.clear_button.clicked.connect(self._clear)
        top.addWidget(self.add_button)
        top.addWidget(self.remove_button)
        top.addWidget(self.clear_button)
        top.addStretch(1)
        root.addLayout(top)

        self.table = QueueTable(0, 3)
        self.table.setHorizontalHeaderLabels(["Document", "Status", "Details"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAcceptDrops(False)  # the window handles drops
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COLUMN_DOCUMENT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COLUMN_DETAILS, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        options = QHBoxLayout()
        options.addWidget(QLabel("Language:"))
        self.language_box = QComboBox()
        for label, code in LANGUAGES:
            self.language_box.addItem(label, code)
        index = self.language_box.findData(self._config.gui_language)
        self.language_box.setCurrentIndex(max(index, 0))
        options.addWidget(self.language_box)
        options.addSpacing(16)
        options.addWidget(QLabel("Output folder:"))
        self.output_edit = QLineEdit(self._config.gui_output_dir)
        self.output_edit.setPlaceholderText("Same folder as each original")
        options.addWidget(self.output_edit, 1)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._choose_output_dir)
        options.addWidget(self.browse_button)
        root.addLayout(options)

        self.text_checkbox = QCheckBox("Also save the recognised text as a .txt file")
        self.text_checkbox.setChecked(self._config.gui_export_text)
        root.addWidget(self.text_checkbox)

        actions = QHBoxLayout()
        self.start_button = QPushButton("Start processing")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        actions.addWidget(self.start_button)
        actions.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m documents")
        self.progress.setTextVisible(True)
        actions.addWidget(self.progress, 1)
        root.addLayout(actions)

        self.status_label = QLabel("Add PDF files to begin.")
        root.addWidget(self.status_label)
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setMaximumHeight(84)
        self.messages.setPlaceholderText("Errors and warnings appear here.")
        root.addWidget(self.messages)

        self.setCentralWidget(central)
        self.setMinimumSize(QSize(640, 480))

    # ---------------------------------------------------------------- queue management
    def add_paths(self, paths: list[str | Path]) -> int:
        """Add files (and the PDFs inside folders) to the queue; returns how many were new."""
        if self.is_running():
            return 0
        added = ignored = 0
        for raw in paths:
            path = Path(raw)
            candidates: list[Path]
            try:
                if path.is_dir():
                    candidates, _ = discover_pdfs(path, recursive=True, suffix=self._config.suffix)
                elif path.suffix.lower() == ".pdf":
                    candidates = [path]
                else:
                    ignored += 1
                    continue
            except OSError as exc:
                self._message(f"Cannot add {path}: {exc}")
                continue
            for pdf in candidates:
                key = path_key(pdf)
                if key in self._rows:
                    continue
                self._rows[key] = _Row(pdf)
                self._append_row(pdf)
                added += 1
        if ignored:
            self._message(f"Ignored {ignored} item(s) that are not PDF files or folders.")
        if added:
            self._set_status(f"{added} document(s) added. {self.table.rowCount()} in the queue.")
        self._update_buttons()
        return added

    def _append_row(self, path: Path) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        name = QTableWidgetItem(path.name)
        name.setToolTip(str(path))
        name.setData(Qt.ItemDataRole.UserRole, path_key(path))
        self.table.setItem(row, COLUMN_DOCUMENT, name)
        self.table.setItem(row, COLUMN_STATUS, QTableWidgetItem(_STATUS_TEXT[JobStatus.PENDING]))
        self.table.setItem(row, COLUMN_DETAILS, QTableWidgetItem(str(path.parent)))

    def _row_index(self, key: str) -> int:
        for row in range(self.table.rowCount()):
            if self.table.item(row, COLUMN_DOCUMENT).data(Qt.ItemDataRole.UserRole) == key:
                return row
        return -1

    def _remove_selected(self) -> None:
        if self.is_running():
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            key = self.table.item(row, COLUMN_DOCUMENT).data(Qt.ItemDataRole.UserRole)
            self._rows.pop(key, None)
            self.table.removeRow(row)
        self._update_buttons()

    def _clear(self) -> None:
        if self.is_running():
            return
        self._rows.clear()
        self.table.setRowCount(0)
        self.progress.setValue(0)
        self.progress.setMaximum(1)
        self.messages.clear()
        self._set_status("Queue cleared.")
        self._update_buttons()

    def _choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select PDF files", "", "PDF documents (*.pdf)")
        if files:
            self.add_paths(files)

    def _choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    # ---------------------------------------------------------------- drag and drop
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and not self.is_running():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        if self.is_running():
            event.ignore()
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()

    # ---------------------------------------------------------------- processing
    def is_running(self) -> bool:
        """True from Start until the worker thread's ``finished`` signal has been handled
        (which Qt delivers after every event the runner emitted)."""
        return self._thread is not None

    def _startup_dependency_check(self) -> None:
        report = check_dependencies(configured_tesseract=self._config.tesseract_path or None)
        if not report.ok:
            self._set_status("Tesseract OCR is not set up correctly.")
            self._message(report.format())
            QMessageBox.warning(
                self, "OCR components missing",
                "OCRizer needs Tesseract OCR with the Polish and English language packs.\n\n"
                + report.format(),
            )

    def start(self) -> None:
        if self.is_running():
            return
        pending = [r for r in self._rows.values() if r.status not in _DONE]
        if not pending:
            self._set_status("Nothing to process: add files or clear the list.")
            return
        language = self.language_box.currentData()
        self._remember_settings(language)

        output_dir = self.output_edit.text().strip()
        export_text = self.text_checkbox.isChecked()
        try:
            plan = plan_jobs(
                [r.path for r in pending],
                OutputSettings(Path(output_dir).expanduser() if output_dir else None, self._config.suffix,
                               ConflictPolicy.RENAME),
                export_text=export_text,
            )
            settings = OcrSettings(language=language, export_text=export_text).validated()
            # Events travel through Qt signals so widgets are only touched on the GUI thread.
            thread = BatchThread(
                plan.jobs, settings, self, workers=self._config.workers,
                tesseract_path=self._config.tesseract_path or None,
            )
        except OcrizerError as exc:
            self._message(f"{exc.message} {exc.hint or ''}".strip())
            self._set_status("Processing could not start.")
            return
        thread.job_event.connect(self._on_event)
        thread.completed.connect(self._on_completed)
        thread.error.connect(self._on_error)
        thread.finished.connect(self._on_thread_finished)

        self._thread = thread
        self._cancelling = False
        self._total_jobs = len(plan.jobs)
        self._finished_jobs = 0
        self.progress.setMaximum(self._total_jobs)
        self.progress.setValue(0)
        self.messages.clear()
        for job in plan.jobs:
            self._set_row(path_key(job.input_path), JobStatus.PENDING, "")
        self._set_status(f"Processing {self._total_jobs} document(s)…")
        self._update_buttons()
        thread.start()

    def cancel(self) -> None:
        if self.is_running() and not self._cancelling:
            self._cancelling = True
            self._thread.cancel()
            self._set_status("Cancelling… running documents are being stopped.")
            self._update_buttons()

    def _on_event(self, event: BatchEvent) -> None:
        key = path_key(event.job.input_path)
        if event.kind == "started":
            self._set_row(key, JobStatus.RUNNING, "Starting…")
        elif event.kind == "progress" and event.progress is not None:
            self._set_row(key, JobStatus.RUNNING, event.progress.text())
        elif event.kind == "finished" and event.result is not None:
            result = event.result
            self._finished_jobs += 1
            self.progress.setValue(self._finished_jobs)
            detail = result.message
            if result.status is JobStatus.SUCCEEDED:
                language = result.language or ""
                note = f" - {result.message}" if result.message else ""
                detail = f"{result.output_path.name if result.output_path else ''} ({language}, " \
                         f"{result.pages} page(s)){note}"
            tooltip = detail + (f"\n{result.hint}" if result.hint else "")
            self._set_row(key, result.status, detail, tooltip)
            if result.status is JobStatus.FAILED:
                self._message(f"{result.job.input_path.name}: {result.message} {result.hint}".strip())

    def _on_completed(self, summary: BatchSummary) -> None:
        self._set_status(
            ("Cancelled. " if summary.cancelled else "Finished. ") + summary.describe() + "."
        )

    def _on_error(self, text: str) -> None:
        self._set_status("Processing could not start.")
        self._message(text)
        if not self._close_requested:
            QMessageBox.warning(self, "Processing failed", text)

    def _on_thread_finished(self) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.deleteLater()
        self._cancelling = False
        self._update_buttons()
        if self._close_requested:
            self.close()

    # ---------------------------------------------------------------- helpers
    def _set_row(self, key: str, status: JobStatus, detail: str, tooltip: str = "") -> None:
        row_data = self._rows.get(key)
        row = self._row_index(key)
        if row_data is None or row < 0:
            return
        row_data.status = status
        status_item = self.table.item(row, COLUMN_STATUS)
        status_item.setText(_STATUS_TEXT[status])
        color = _STATUS_COLOR.get(status)
        status_item.setForeground(QBrush(color) if color else QBrush())
        details_item = self.table.item(row, COLUMN_DETAILS)
        details_item.setText(detail)
        details_item.setToolTip(tooltip or detail)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _message(self, text: str) -> None:
        self.messages.appendPlainText(text)

    def _remember_settings(self, language: str) -> None:
        self._config.gui_language = language
        self._config.gui_output_dir = self.output_edit.text().strip()
        self._config.gui_export_text = self.text_checkbox.isChecked()
        try:
            save_config(self._config)
        except OcrizerError:
            pass  # remembering preferences is a convenience, never a reason to stop

    def _update_buttons(self) -> None:
        running = self.is_running()
        has_rows = self.table.rowCount() > 0
        self.start_button.setEnabled(has_rows and not running)
        self.cancel_button.setEnabled(running and not self._cancelling)
        for widget in (self.add_button, self.remove_button, self.clear_button, self.language_box,
                       self.output_edit, self.browse_button, self.text_checkbox):
            widget.setEnabled(not running)
        self.clear_button.setEnabled(has_rows and not running)
        self.remove_button.setEnabled(has_rows and not running)

    def closeEvent(self, event) -> None:
        if self.is_running():
            if not self._close_requested:
                answer = QMessageBox.question(
                    self, "Processing in progress",
                    "Documents are still being processed. Cancel and quit?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self._close_requested = True
                self.cancel()
            # Keep Qt's event loop responsive while the runner stops its process trees.
            # Destroying a running QThread can terminate the whole application.
            event.ignore()
            return
        self._remember_settings(self.language_box.currentData())
        event.accept()
