from __future__ import annotations

import functools
import os
import subprocess
import sys

import pytest

import factory
import fake_workers
from ocrizer import __version__, batch, cli
from ocrizer.pdfinfo import analyze_pdf


def run_cli(*args):
    return cli.main([str(a) for a in args])


# ------------------------------------------------------------------ argument validation (no OCR)
def test_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        run_cli("--version")
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_documents_exit_codes(capsys):
    with pytest.raises(SystemExit) as excinfo:
        run_cli("--help")
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--language" in out and "--batch" in out and "Exit codes" in out


def test_no_input_is_a_usage_error(capsys):
    assert run_cli() == 2
    assert "no input" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["x.pdf", "--language", "german"],
        ["x.pdf", "--language", "pol+deu"],
        ["x.pdf", "--workers", "0"],
        ["x.pdf", "--workers", "many"],
        ["x.pdf", "--optimize", "7"],
        ["x.pdf", "--existing-text", "maybe"],
        ["x.pdf", "--overwrite", "--skip-existing"],
        ["x.pdf", "--in-place", "--overwrite"],
        ["x.pdf", "--bogus"],
    ],
)
def test_invalid_arguments_exit_with_2(args, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run_cli(*args)
    assert excinfo.value.code == 2
    assert capsys.readouterr().err


def test_directory_without_batch_is_rejected(docs, capsys):
    factory.text_pdf(docs / "a.pdf", [["x"]])
    assert run_cli(docs) == 2
    assert "--batch" in capsys.readouterr().err


def test_empty_directory_reports_no_files(docs, capsys):
    assert run_cli(docs, "--batch") == 2
    assert "no PDF files" in capsys.readouterr().err


def test_output_option_rules(docs, capsys):
    a = factory.text_pdf(docs / "a.pdf", [["x"]])
    b = factory.text_pdf(docs / "b.pdf", [["x"]])
    assert run_cli(a, b, "--output", docs / "out.pdf") == 2
    assert "exactly one" in capsys.readouterr().err
    assert run_cli(a, "--output", docs / "out.txt") == 2
    assert run_cli(a, "--output", docs / "o.pdf", "--output-dir", docs) == 2
    assert run_cli(a, "--suffix", "") == 2
    assert "must not be empty" in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--output", "--output-dir"])
def test_in_place_rejects_an_output_location(docs, capsys, option):
    source = factory.text_pdf(docs / "a.pdf", [["x"]])
    assert run_cli(source, "--in-place", option, docs / "out.pdf") == 2
    assert "--in-place cannot be combined" in capsys.readouterr().err
    assert source.is_file()


def test_skipped_document_reports_exported_text_path(capsys, docs):
    from ocrizer.models import BatchEvent, Job, JobResult, JobStatus

    job = Job(0, docs / "a.pdf", docs / "a_ocr.pdf")
    result = JobResult(job, JobStatus.SKIPPED, text_path=job.text_path, message="Already searchable")
    reporter = cli.ConsoleReporter(1, quiet=False, verbose=0)
    reporter.on_event(BatchEvent("finished", job, result=result))
    output = capsys.readouterr().out
    assert "SKIPPED" in output and str(job.text_path) in output


def test_gui_dispatches_with_explicit_tesseract_and_owns_config_recovery(monkeypatch):
    captured = {}

    def launch(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "_launch_gui", launch)
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("GUI must load its own config"))
    assert run_cli("--gui", "--tesseract", "custom-tesseract") == 0
    assert captured == {"tesseract_path": "custom-tesseract"}


def test_missing_tesseract_exits_3_with_install_instructions(docs, capsys):
    a = factory.text_pdf(docs / "a.pdf", [["x"]])
    assert run_cli(a, "--tesseract", docs / "no-tesseract") == 3
    err = capsys.readouterr().err
    assert "Tesseract" in err and ("winget" in err or "apt" in err or "brew" in err)


def test_check_reports_missing_tesseract(docs, capsys):
    assert run_cli("--check", "--tesseract", docs / "no-tesseract") == 3
    out = capsys.readouterr().out
    assert "MISSING" in out and "Tesseract" in out


def test_ctrl_c_cancels_running_documents_and_exits_130(docs, tmp_path, monkeypatch, capsys):
    files = [factory.text_pdf(docs / f"{n}.pdf", [["x"]]) for n in "abc"]
    monkeypatch.setattr(
        cli, "BatchRunner",
        functools.partial(batch.BatchRunner, verify_dependencies=False,
                          worker_target=fake_workers.sleep_worker),
    )
    pid_file = tmp_path / "pids"
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    real_wait = batch.wait
    state = {"raised": False}

    def interrupting_wait(conns, timeout=None):
        if not state["raised"] and pid_file.exists() and pid_file.read_text().split():
            state["raised"] = True
            raise KeyboardInterrupt
        return real_wait(conns, timeout)

    monkeypatch.setattr(batch, "wait", interrupting_wait)
    assert run_cli(*files, "-w", "2") == 130
    assert state["raised"]
    assert not list(docs.glob("*_ocr.pdf")) and not [p for p in docs.iterdir() if p.name.startswith(".")]
    assert "CANCELLED" in capsys.readouterr().out


def test_keyboard_interrupt_outside_the_runner_exits_130(docs, monkeypatch, capsys):
    a = factory.text_pdf(docs / "a.pdf", [["x"]])
    monkeypatch.setattr(cli, "plan_jobs", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert run_cli(a) == 130


# ------------------------------------------------------------------ real runs
@pytest.mark.integration
def test_single_file_run(docs, capsys):
    src = factory.scanned_pdf(docs / "a.pdf", [factory.ENGLISH_TEXT])
    assert run_cli(src, "-l", "eng") == 0
    out = capsys.readouterr().out
    assert "OK" in out and "a_ocr.pdf" in out and "1 succeeded" in out
    assert analyze_pdf(docs / "a_ocr.pdf").fully_searchable


@pytest.mark.integration
def test_explicit_output_and_conflict_handling(docs, capsys):
    src = factory.scanned_pdf(docs / "a.pdf", [factory.ENGLISH_TEXT])
    target = docs / "custom name.pdf"
    assert run_cli(src, "--output", target, "-l", "eng") == 0
    assert target.is_file()
    capsys.readouterr()
    assert run_cli(src, "--output", target) == 2  # exists, no --overwrite
    assert "already exists" in capsys.readouterr().err
    assert run_cli(src, "--output", target, "--skip-existing") == 0
    assert "SKIPPED" in capsys.readouterr().out
    before = target.stat().st_mtime_ns
    assert run_cli(src, "--output", target, "--overwrite", "-l", "eng") == 0
    assert target.stat().st_mtime_ns != before


@pytest.mark.integration
def test_batch_recursive_with_output_dir_and_workers(docs, tmp_path, capsys):
    factory.scanned_pdf(docs / "pl.pdf", [factory.POLISH_TEXT])
    factory.scanned_pdf(docs / "sub" / "en.pdf", [factory.ENGLISH_TEXT])
    factory.corrupt_pdf(docs / "sub" / "broken.pdf", docs / "pl.pdf")
    out = tmp_path / "processed"
    code = run_cli(docs, "--batch", "--recursive", "--output-dir", out, "--workers", "2", "-v")
    captured = capsys.readouterr()
    assert code == 1  # one document failed
    assert (out / "pl_ocr.pdf").is_file() and (out / "sub" / "en_ocr.pdf").is_file()
    assert "FAILED" in captured.err and "broken.pdf" in captured.err
    assert "2 succeeded, 1 failed" in captured.out
    assert not (docs / "pl_ocr.pdf").exists()  # --output-dir keeps the source folder clean


@pytest.mark.integration
def test_quiet_mode_prints_only_problems(docs, capsys):
    src = factory.scanned_pdf(docs / "a.pdf", [factory.ENGLISH_TEXT])
    assert run_cli(src, "-q", "-l", "eng") == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert run_cli(docs / "missing.pdf", "-q") == 1
    assert "FAILED" in capsys.readouterr().err


@pytest.mark.integration
def test_missing_input_file_fails_with_exit_1(docs, capsys):
    assert run_cli(docs / "nope.pdf") == 1
    assert "File not found" in capsys.readouterr().err


@pytest.mark.integration
def test_in_place_requires_explicit_flag_and_replaces_original(docs):
    src = factory.scanned_pdf(docs / "a.pdf", [factory.ENGLISH_TEXT])
    assert run_cli(src, "-l", "eng", "--in-place") == 0
    assert analyze_pdf(src).fully_searchable
    assert sorted(p.name for p in docs.iterdir()) == ["a.pdf"]


@pytest.mark.integration
def test_check_passes_on_a_configured_machine(capsys):
    assert run_cli("--check") == 0
    assert "Tesseract language 'pol'" in capsys.readouterr().out


@pytest.mark.integration
def test_unicode_names_survive_a_non_unicode_console(docs):
    """Printing Polish file names on an ASCII-only console must not crash the run."""
    src = factory.scanned_pdf(docs / "zażółć gęślą jaźń.pdf", [factory.POLISH_TEXT])
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}
    proc = subprocess.run(
        [sys.executable, "-m", "ocrizer", str(src), "-l", "pol"],
        capture_output=True, env=env, timeout=300, check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("ascii", "replace")
    assert (docs / "zażółć gęślą jaźń_ocr.pdf").is_file()
