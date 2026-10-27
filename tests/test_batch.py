"""Batch runner behaviour: isolation of failures, crashes, cancellation, commits, clean-up.

These tests use stand-in worker processes (tests/fake_workers.py) so that they exercise the
real process management without needing Tesseract.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

import psutil
import pytest

import factory
import fake_workers
from ocrizer.batch import BatchRunner, commit_file, kill_process_tree
from ocrizer.config import ConflictPolicy, OcrSettings, OutputSettings
from ocrizer.errors import ConfigError, ErrorCode, OutputError
from ocrizer.models import Job, JobStatus
from ocrizer.planner import plan_jobs


@pytest.fixture
def scratch_root(tmp_path, monkeypatch):
    """Isolated temp root so that leaked scratch directories are detectable."""
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def make_runner(plan, worker, **kwargs):
    kwargs.setdefault("workers", 2)
    return BatchRunner(
        plan.jobs, OcrSettings(), verify_dependencies=False, worker_target=worker, **kwargs
    )


def make_docs(docs: Path, *names: str) -> list[Path]:
    return [factory.text_pdf(docs / name, [["x"]]) for name in names]


def leftovers(*roots: Path) -> list[Path]:
    found = []
    for root in roots:
        found += [p for p in root.rglob("*") if p.name.startswith((".ocrizer-", "ocrizer-"))]
    return found


def test_successful_batch_commits_outputs(docs, scratch_root):
    files = make_docs(docs, "a.pdf", "b.pdf", "c.pdf")
    plan = plan_jobs(files, OutputSettings())
    events = []
    summary = make_runner(plan, fake_workers.ok_worker, on_event=events.append).run()
    assert summary.describe() == "3 succeeded, 0 failed, 0 skipped, 0 cancelled"
    assert [r.output_path.name for r in summary.results] == ["a_ocr.pdf", "b_ocr.pdf", "c_ocr.pdf"]
    assert all(r.output_path.is_file() for r in summary.results)
    assert {e.kind for e in events} == {"started", "progress", "finished"}
    assert leftovers(docs, scratch_root) == []


def test_originals_are_never_modified(docs, scratch_root):
    (original,) = make_docs(docs, "a.pdf")
    before = original.read_bytes()
    make_runner(plan_jobs([original], OutputSettings()), fake_workers.ok_worker).run()
    assert original.read_bytes() == before


def test_failures_and_crashes_do_not_stop_the_batch(docs, scratch_root):
    files = make_docs(docs, "first.pdf", "bad_crash.pdf", "invalid.pdf", "last.pdf")
    plan = plan_jobs(files, OutputSettings())
    summary = make_runner(plan, fake_workers.selective_worker, workers=1).run()
    by_name = {r.job.input_path.name: r for r in summary.results}
    assert by_name["first.pdf"].status is JobStatus.SUCCEEDED
    assert by_name["last.pdf"].status is JobStatus.SUCCEEDED
    crashed = by_name["bad_crash.pdf"]
    assert crashed.status is JobStatus.FAILED
    assert crashed.error_code is ErrorCode.WORKER_CRASHED and "exit code 7" in crashed.message
    assert by_name["invalid.pdf"].status is JobStatus.FAILED
    assert by_name["invalid.pdf"].message == "not a pdf"
    assert summary.describe() == "2 succeeded, 2 failed, 0 skipped, 0 cancelled"
    assert leftovers(docs, scratch_root) == []  # partial files of the crash were removed


def test_crash_leaves_no_output(docs, scratch_root):
    (pdf,) = make_docs(docs, "a.pdf")
    summary = make_runner(plan_jobs([pdf], OutputSettings()), fake_workers.crash_worker).run()
    assert summary.results[0].status is JobStatus.FAILED
    assert not (docs / "a_ocr.pdf").exists()
    assert leftovers(docs, scratch_root) == []


def test_planner_skips_are_reported_without_starting_workers(docs, scratch_root):
    (pdf,) = make_docs(docs, "a.pdf")
    (docs / "a_ocr.pdf").write_bytes(b"existing")
    plan = plan_jobs([pdf], OutputSettings(conflict=ConflictPolicy.SKIP))
    summary = make_runner(plan, fake_workers.crash_worker).run()
    assert summary.results[0].status is JobStatus.SKIPPED
    assert (docs / "a_ocr.pdf").read_bytes() == b"existing"


def test_existing_output_is_not_overwritten_at_commit_time(docs, scratch_root):
    """Race: the output appears after planning. The runner must refuse to replace it."""
    (pdf,) = make_docs(docs, "a.pdf")
    plan = plan_jobs([pdf], OutputSettings())
    (docs / "a_ocr.pdf").write_bytes(b"created by someone else")
    summary = make_runner(plan, fake_workers.ok_worker).run()
    result = summary.results[0]
    assert result.status is JobStatus.FAILED and result.error_code is ErrorCode.OUTPUT_EXISTS
    assert (docs / "a_ocr.pdf").read_bytes() == b"created by someone else"
    assert leftovers(docs, scratch_root) == []


def test_overwrite_replaces_existing_output(docs, scratch_root):
    (pdf,) = make_docs(docs, "a.pdf")
    (docs / "a_ocr.pdf").write_bytes(b"old")
    plan = plan_jobs([pdf], OutputSettings(conflict=ConflictPolicy.OVERWRITE))
    summary = make_runner(plan, fake_workers.ok_worker, overwrite=True).run()
    assert summary.results[0].status is JobStatus.SUCCEEDED
    assert (docs / "a_ocr.pdf").read_bytes() == pdf.read_bytes()


def test_unwritable_output_directory_fails_only_that_job(docs, scratch_root):
    ok, blocked = make_docs(docs, "ok.pdf", "blocked.pdf")
    (docs / "not_a_dir").write_text("a file where a directory is needed")
    plan = plan_jobs([ok], OutputSettings())
    bad_plan = plan_jobs([blocked], OutputSettings(output_dir=docs / "not_a_dir" / "sub"))
    jobs = plan.jobs + [bad_plan.jobs[0].__class__(1, blocked, docs / "not_a_dir" / "sub" / "x.pdf")]
    summary = BatchRunner(jobs, OcrSettings(), verify_dependencies=False,
                          worker_target=fake_workers.ok_worker, workers=1).run()
    assert [r.status for r in summary.results] == [JobStatus.SUCCEEDED, JobStatus.FAILED]
    assert summary.results[1].error_code is ErrorCode.OUTPUT_ACCESS


def test_cancel_kills_process_tree_and_cleans_up(docs, scratch_root, tmp_path, monkeypatch):
    files = make_docs(docs, "a.pdf", "b.pdf", "c.pdf", "d.pdf")
    pid_file = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    runner = make_runner(plan_jobs(files, OutputSettings()), fake_workers.sleep_worker, workers=2)
    holder = {}
    thread = threading.Thread(target=lambda: holder.setdefault("summary", runner.run()))
    thread.start()

    deadline = time.time() + 60
    while time.time() < deadline:  # wait until both workers have spawned their grandchild
        if pid_file.exists() and len(pid_file.read_text().split()) >= 2:
            break
        time.sleep(0.1)
    pids = [int(p) for p in pid_file.read_text().split()]
    assert len(pids) == 2 and all(psutil.pid_exists(p) for p in pids)

    started = time.time()
    runner.cancel()
    thread.join(timeout=60)
    assert not thread.is_alive()
    assert time.time() - started < 30

    summary = holder["summary"]
    assert summary.cancelled
    assert [r.status for r in summary.results] == [JobStatus.CANCELLED] * 4
    assert not any(psutil.pid_exists(p) for p in pids), "grandchild processes must be killed"
    assert leftovers(docs, scratch_root) == []
    assert not list(docs.glob("*_ocr.pdf"))


def test_cancel_before_start_cancels_everything(docs, scratch_root):
    files = make_docs(docs, "a.pdf", "b.pdf")
    runner = make_runner(plan_jobs(files, OutputSettings()), fake_workers.ok_worker)
    runner.cancel()
    summary = runner.run()
    assert summary.cancelled and summary.cancelled_jobs == 2
    assert leftovers(docs, scratch_root) == []


def test_event_callback_errors_do_not_abort_the_batch(docs, scratch_root):
    files = make_docs(docs, "a.pdf")

    def broken(event):
        raise RuntimeError("UI exploded")

    summary = make_runner(plan_jobs(files, OutputSettings()), fake_workers.ok_worker,
                          on_event=broken).run()
    assert summary.succeeded == 1


def test_at_most_n_workers_run_concurrently(docs, scratch_root, tmp_path, monkeypatch):
    files = make_docs(docs, *(f"{i}.pdf" for i in range(5)))
    pid_file = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    runner = make_runner(plan_jobs(files, OutputSettings()), fake_workers.sleep_worker, workers=2)
    thread = threading.Thread(target=runner.run)
    thread.start()

    def spawned_count() -> int:
        return len(pid_file.read_text().split()) if pid_file.exists() else 0

    deadline = time.time() + 90
    while spawned_count() < 2 and time.time() < deadline:
        time.sleep(0.1)
    time.sleep(4)  # long enough for any excess worker to have started
    spawned = spawned_count()
    runner.cancel()
    thread.join(timeout=60)
    assert spawned == 2


def test_commit_file_semantics(tmp_path):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"new")
    commit_file(src, dst, overwrite=False)
    assert dst.read_bytes() == b"new" and not src.exists()

    src.write_bytes(b"newer")
    with pytest.raises(OutputError):
        commit_file(src, dst, overwrite=False)
    assert dst.read_bytes() == b"new" and src.exists()
    commit_file(src, dst, overwrite=True)
    assert dst.read_bytes() == b"newer"


def test_kill_process_tree_ignores_dead_pid():
    kill_process_tree(2**22 + os.getpid() % 1000)  # must not raise


def test_no_overwrite_fallback_preserves_a_file_created_during_commit(tmp_path, monkeypatch):
    from ocrizer import batch

    source, target = tmp_path / "partial.pdf", tmp_path / "result.pdf"
    source.write_bytes(b"new")

    def unsupported_link(*args):
        target.write_bytes(b"someone else's document")
        raise OSError("hard links unsupported")

    monkeypatch.setattr(batch.os, "link", unsupported_link)
    with pytest.raises(OutputError):
        commit_file(source, target, overwrite=False)
    assert target.read_bytes() == b"someone else's document"
    assert source.read_bytes() == b"new"


def test_text_export_from_another_folder_is_staged_before_commit(tmp_path, monkeypatch):
    from ocrizer import batch

    scratch, output = tmp_path / "scratch", tmp_path / "output"
    scratch.mkdir()
    output.mkdir()
    source, target = scratch / "text.txt", output / "text.txt"
    source.write_text("exported", encoding="utf-8")
    replace = batch.os.replace

    def same_directory_replace(src, dst):
        assert Path(src).parent == Path(dst).parent
        replace(src, dst)

    monkeypatch.setattr(batch.os, "replace", same_directory_replace)
    commit_file(source, target, overwrite=True)
    assert target.read_text(encoding="utf-8") == "exported"
    assert not source.exists()
    assert list(output.iterdir()) == [target]


def test_temporary_directory_failure_is_reported_per_job(docs, monkeypatch):
    files = make_docs(docs, "a.pdf", "b.pdf")

    def no_space(*args, **kwargs):
        raise OSError("temporary directory is full")

    monkeypatch.setattr(tempfile, "mkdtemp", no_space)
    summary = make_runner(plan_jobs(files, OutputSettings()), fake_workers.ok_worker).run()
    assert summary.failed == 2
    assert all(result.error_code is ErrorCode.OUTPUT_ACCESS for result in summary.results)


def test_cancelled_and_skipped_batches_do_not_require_ocr_dependencies(docs, monkeypatch):
    def should_not_check(self):
        pytest.fail("OCR dependencies are unnecessary for cancelled/skipped jobs")

    monkeypatch.setattr(BatchRunner, "_check_environment", should_not_check)
    source = docs / "a.pdf"
    skipped = BatchRunner([Job(0, source, docs / "out.pdf", "Already exists")], OcrSettings())
    assert skipped.run().skipped == 1
    cancelled = BatchRunner([Job(0, source, docs / "out.pdf")], OcrSettings())
    cancelled.cancel()
    assert cancelled.run().cancelled_jobs == 1
    assert BatchRunner([], OcrSettings()).run().results == []


@pytest.mark.parametrize("workers", [0, -1, True, "2"])
def test_invalid_worker_counts_are_rejected(workers):
    with pytest.raises(ConfigError):
        BatchRunner([], OcrSettings(), workers=workers)


def test_duplicate_job_indices_are_rejected(tmp_path):
    job = Job(0, tmp_path / "input.pdf", tmp_path / "output.pdf")
    with pytest.raises(ConfigError, match="unique index"):
        BatchRunner([job, job], OcrSettings())
