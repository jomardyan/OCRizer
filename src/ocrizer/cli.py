"""Command-line interface (``pdfocr``).

This module must not import the GUI (or Qt) at import time: the CLI has to work on headless
machines where PySide6 is not installed.

Exit codes
----------
0    every document succeeded or was skipped
1    at least one document failed
2    invalid arguments, inputs or configuration (nothing was processed)
3    a required OCR dependency (Tesseract / language pack) is missing
130  interrupted (Ctrl+C); running documents were cancelled and cleaned up
"""

from __future__ import annotations

import argparse
import multiprocessing
import shutil
import sys
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import TextIO

from ocrizer import __version__
from ocrizer.batch import BatchRunner
from ocrizer.config import (
    ConflictPolicy,
    ExistingText,
    OcrSettings,
    OutputSettings,
    load_config,
    normalize_language,
)
from ocrizer.deps import check_dependencies, prepare_environment
from ocrizer.errors import ConfigError, MissingDependencyError, OcrizerError, UsageError
from ocrizer.logging_setup import configure_logging
from ocrizer.models import BatchEvent, BatchSummary, JobResult, JobStatus
from ocrizer.planner import Plan, expand_globs, plan_jobs

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_DEPENDENCY = 3
EXIT_INTERRUPTED = 130


def _language(value: str) -> str:
    try:
        return normalize_language(value)
    except ConfigError as exc:
        raise argparse.ArgumentTypeError(f"{exc.message} {exc.hint or ''}".strip()) from exc


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _version_string() -> str:
    try:
        ocrmypdf_version = metadata.version("ocrmypdf")
    except metadata.PackageNotFoundError:
        ocrmypdf_version = "not installed"
    return f"pdfocr {__version__} (OCRmyPDF {ocrmypdf_version})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdfocr",
        description="Convert scanned PDFs into searchable PDFs (Polish and English), locally and offline.",
        epilog="Exit codes: 0 ok, 1 some documents failed, 2 usage error, 3 missing dependency, "
        "130 interrupted. Run 'pdfocr --check' to verify the installation.",
    )
    parser.add_argument("inputs", nargs="*", metavar="INPUT",
                        help="PDF files, directories (with --batch) or wildcard patterns")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="output file (only with a single input file)")
    parser.add_argument("--output-dir", metavar="DIR", help="write results into this directory")
    parser.add_argument("-l", "--language", type=_language, metavar="LANG",
                        help="auto (default), pol, eng or pol+eng")
    parser.add_argument("-b", "--batch", action="store_true",
                        help="process the PDFs inside the given directories")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="include sub-directories (implies --batch)")
    parser.add_argument("-w", "--workers", type=_positive_int, metavar="N",
                        help="documents processed in parallel (default: half the CPU cores, max 4)")
    parser.add_argument("--existing-text", choices=[e.value for e in ExistingText], default="skip",
                        help="skip (default): OCR only pages without text and leave fully searchable "
                        "files alone; redo: replace an old OCR layer; force: rasterise and OCR "
                        "everything (loses vector text)")
    conflict = parser.add_mutually_exclusive_group()
    conflict.add_argument("--overwrite", action="store_true",
                          help="replace existing output files (default: pick a new name)")
    conflict.add_argument("--skip-existing", action="store_true",
                          help="skip documents whose output file already exists")
    conflict.add_argument("--in-place", action="store_true",
                          help="replace the ORIGINAL file with the OCR'd version")
    parser.add_argument("--no-ocr-images", action="store_true",
                        help="with --existing-text skip: do not read text inside images on pages "
                        "that already have a text layer")
    parser.add_argument("--suffix", metavar="TEXT",
                        help="suffix added to output names (default: _ocr)")
    parser.add_argument("--export-text", action="store_true",
                        help="also write the recognised text to a .txt file")
    parser.add_argument("--deskew", action="store_true",
                        help="straighten crooked scans (rewrites page images)")
    parser.add_argument("--no-rotate", action="store_true",
                        help="do not auto-rotate pages whose text is sideways or upside down")
    parser.add_argument("--optimize", type=int, choices=range(4), default=1, metavar="0-3",
                        help="output optimisation: 0 off, 1 lossless (default), 2-3 lossy")
    parser.add_argument("--tesseract", metavar="PATH",
                        help="tesseract executable or its directory (default: PATH lookup)")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="more detail (-v: language decisions, -vv: debug logging)")
    parser.add_argument("--log-file", metavar="FILE", help="write structured JSON logs here")
    parser.add_argument("--check", action="store_true",
                        help="verify Tesseract, language packs and Python packages, then exit")
    parser.add_argument("--gui", action="store_true", help="start the graphical interface")
    parser.add_argument("--version", action="version", version=_version_string())
    return parser


class ConsoleReporter:
    """Prints one line per finished document and, on a terminal, a live progress line."""

    def __init__(self, total: int, *, quiet: bool, verbose: int,
                 out: TextIO | None = None, err: TextIO | None = None):
        self._total = total
        self._quiet = quiet
        self._verbose = verbose
        self._out = out or sys.stdout
        self._err = err or sys.stderr
        self._live = (not quiet) and hasattr(self._err, "isatty") and self._err.isatty()
        self._done = 0
        self._live_len = 0

    def _clear_live(self) -> None:
        if self._live and self._live_len:
            self._err.write("\r" + " " * self._live_len + "\r")
            self._err.flush()
            self._live_len = 0

    def on_event(self, event: BatchEvent) -> None:
        if event.kind == "progress" and self._live and event.progress is not None:
            width = max(shutil.get_terminal_size((80, 20)).columns - 1, 20)
            line = f"[{self._done}/{self._total}] {event.job.input_path.name}: {event.progress.text()}"
            line = line[:width]
            self._err.write("\r" + line.ljust(self._live_len))
            self._err.flush()
            self._live_len = max(self._live_len, len(line))
        elif event.kind == "finished" and event.result is not None:
            self._done += 1
            self._clear_live()
            self._print_result(event.result)

    def _print_result(self, result: JobResult) -> None:
        name = str(result.job.input_path)
        position = f"[{self._done:>{len(str(self._total))}}/{self._total}]"
        status = result.status
        if status is JobStatus.SUCCEEDED:
            if self._quiet:
                return
            detail = describe_success(result)
            line = f"{position} OK       {name} -> {result.output_path}  ({detail})"
            if result.message:
                line += f"\n{' ' * len(position)}          note: {result.message}"
            print(line, file=self._out)
            if result.text_path:
                print(f"{' ' * len(position)}          text: {result.text_path}", file=self._out)
        elif status is JobStatus.SKIPPED:
            if not self._quiet:
                print(f"{position} SKIPPED  {name}: {result.message}", file=self._out)
                if result.text_path:
                    print(f"{' ' * len(position)}          text: {result.text_path}", file=self._out)
        elif status is JobStatus.CANCELLED:
            if not self._quiet:
                print(f"{position} CANCELLED {name}", file=self._out)
        else:
            print(f"{position} FAILED   {name}: {result.message}", file=self._err)
            if result.hint:
                print(f"{' ' * len(position)}          hint: {result.hint}", file=self._err)
        if self._verbose and result.detection is not None and not self._quiet:
            d = result.detection
            print(
                f"{' ' * len(position)}          language: {d.language} "
                f"({'confident' if d.confident else 'fallback'}: {d.reason}; "
                f"polish {d.polish_share:.0%}, english {d.english_share:.0%}, "
                f"OCR confidence {d.mean_confidence:.0f}%)",
                file=self._out,
            )


def describe_success(result: JobResult) -> str:
    parts = []
    if result.detection is not None:
        tag = "auto" if result.detection.confident else "auto, fallback"
        parts.append(f"language {result.language} [{tag}]")
    elif result.language:
        parts.append(f"language {result.language}")
    parts.append(f"{result.pages} page{'s' if result.pages != 1 else ''}")
    if result.pages_ocr != result.pages:
        parts.append(f"{result.pages_ocr} OCR'd")
    parts.append(f"{result.duration:.1f}s")
    return ", ".join(parts)


def _print_error(exc: OcrizerError) -> None:
    print(f"pdfocr: error: {exc.message}", file=sys.stderr)
    if exc.hint:
        print(f"  {exc.hint}", file=sys.stderr)


def _launch_gui(tesseract_path: str | None = None) -> int:
    try:
        from ocrizer.gui.app import main as gui_main
    except ImportError:
        print("pdfocr: error: the GUI needs PySide6.\n  Install it with: pip install \"ocrizer[gui]\"",
              file=sys.stderr)
        return EXIT_DEPENDENCY
    return gui_main([], tesseract_path=tesseract_path)


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()  # needed for PyInstaller-built executables on Windows
    for stream in (sys.stdout, sys.stderr):  # never crash on unprintable file names
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args, parser)
    except KeyboardInterrupt:
        print("pdfocr: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.gui:
        return _launch_gui(tesseract_path=args.tesseract)

    try:
        config = load_config()
    except ConfigError as exc:
        _print_error(exc)
        return EXIT_USAGE

    log_file = Path(args.log_file) if args.log_file else None
    try:
        configure_logging(verbosity=args.verbose, quiet=args.quiet, log_file=log_file)
    except OSError as exc:
        print(f"pdfocr: error: cannot open log file: {exc.strerror or exc}", file=sys.stderr)
        return EXIT_USAGE

    tesseract_setting = args.tesseract or config.tesseract_path or None
    prepare_environment(tesseract_setting)

    if args.check:
        report = check_dependencies(configured_tesseract=tesseract_setting, include_gui=True)
        print(report.format())
        return EXIT_OK if report.ok else EXIT_DEPENDENCY
    if not args.inputs:
        parser.print_usage(sys.stderr)
        print("pdfocr: error: no input given (a PDF file, or a directory with --batch).",
              file=sys.stderr)
        return EXIT_USAGE

    language = args.language or config.language
    try:
        settings = OcrSettings(
            language=language,
            existing_text=ExistingText(args.existing_text),
            ocr_images=not args.no_ocr_images,
            rotate_pages=not args.no_rotate,
            deskew=args.deskew,
            optimize=args.optimize,
            export_text=args.export_text,
        ).validated()
        conflict = (
            ConflictPolicy.OVERWRITE if args.overwrite or args.in_place
            else ConflictPolicy.SKIP if args.skip_existing
            else ConflictPolicy.RENAME
        )
        output = OutputSettings(
            output_dir=Path(args.output_dir) if args.output_dir else None,
            suffix=args.suffix if args.suffix is not None else config.suffix,
            conflict=conflict,
            in_place=args.in_place,
        )
        if not args.in_place and not output.suffix and not args.output:
            raise UsageError("The output suffix must not be empty.",
                             hint="Use --in-place if you really want to replace the originals.")
        plan = plan_jobs(
            expand_globs(args.inputs),
            output,
            batch=args.batch or args.recursive,
            recursive=args.recursive,
            explicit_output=Path(args.output) if args.output else None,
            export_text=settings.export_text,
        )
    except OcrizerError as exc:
        _print_error(exc)
        return EXIT_USAGE

    if not plan.jobs:
        print("pdfocr: error: no PDF files found to process.", file=sys.stderr)
        return EXIT_USAGE
    _report_ignored(plan, args)

    reporter = ConsoleReporter(len(plan.jobs), quiet=args.quiet, verbose=args.verbose)
    try:
        runner = BatchRunner(
            plan.jobs, settings, workers=args.workers or config.workers,
            overwrite=conflict is ConflictPolicy.OVERWRITE, on_event=reporter.on_event,
            tesseract_path=tesseract_setting,
        )
        summary = runner.run()
    except MissingDependencyError as exc:
        _print_error(exc)
        return EXIT_DEPENDENCY
    except OcrizerError as exc:
        _print_error(exc)
        return EXIT_USAGE
    return _finish(summary, quiet=args.quiet)


def _report_ignored(plan: Plan, args: argparse.Namespace) -> None:
    if args.verbose and not args.quiet:
        for path, reason in plan.ignored:
            print(f"ignored {path}: {reason}", file=sys.stderr)


def _finish(summary: BatchSummary, *, quiet: bool) -> int:
    if not quiet or summary.failed:
        print(f"Done: {summary.describe()}.", file=sys.stderr if quiet else sys.stdout)
    if summary.cancelled:
        return EXIT_INTERRUPTED
    return EXIT_FAILED if summary.failed else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
