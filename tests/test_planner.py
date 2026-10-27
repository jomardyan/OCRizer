from __future__ import annotations

from pathlib import Path

import pytest

from ocrizer.config import ConflictPolicy, OutputSettings
from ocrizer.errors import UsageError
from ocrizer.planner import discover_pdfs, expand_globs, plan_jobs


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n")
    return path


def outputs(plan) -> list[str]:
    return [j.output_path.name for j in plan.jobs]


def test_default_output_next_to_input(docs):
    a = touch(docs / "a.pdf")
    plan = plan_jobs([a], OutputSettings())
    assert plan.jobs[0].output_path == docs / "a_ocr.pdf"
    assert plan.jobs[0].input_path == a


def test_output_dir_and_custom_suffix(docs, tmp_path):
    a = touch(docs / "a.pdf")
    plan = plan_jobs([a], OutputSettings(output_dir=tmp_path / "out", suffix="_searchable"))
    assert plan.jobs[0].output_path == tmp_path / "out" / "a_searchable.pdf"


def test_existing_output_is_renamed_never_overwritten(docs):
    a = touch(docs / "a.pdf")
    touch(docs / "a_ocr.pdf")
    touch(docs / "a_ocr_1.pdf")
    assert outputs(plan_jobs([a], OutputSettings())) == ["a_ocr_2.pdf"]


def test_text_export_name_counts_as_conflict(docs):
    a = touch(docs / "a.pdf")
    (docs / "a_ocr.txt").write_text("old")
    assert outputs(plan_jobs([a], OutputSettings(), export_text=True)) == ["a_ocr_1.pdf"]
    assert outputs(plan_jobs([a], OutputSettings(), export_text=False)) == ["a_ocr.pdf"]


def test_overwrite_policy_keeps_existing_name(docs):
    a = touch(docs / "a.pdf")
    touch(docs / "a_ocr.pdf")
    plan = plan_jobs([a], OutputSettings(conflict=ConflictPolicy.OVERWRITE))
    assert outputs(plan) == ["a_ocr.pdf"] and not plan.jobs[0].skip_reason


def test_skip_policy_marks_job_skipped(docs):
    a = touch(docs / "a.pdf")
    touch(docs / "a_ocr.pdf")
    plan = plan_jobs([a], OutputSettings(conflict=ConflictPolicy.SKIP))
    assert "already exists" in plan.jobs[0].skip_reason


def test_same_name_in_different_folders_does_not_collide(docs, tmp_path):
    a = touch(docs / "one" / "doc.pdf")
    b = touch(docs / "two" / "doc.pdf")
    plan = plan_jobs([a, b], OutputSettings(output_dir=tmp_path / "out"))
    assert outputs(plan) == ["doc_ocr.pdf", "doc_ocr_1.pdf"]
    assert len({j.output_path for j in plan.jobs}) == 2


def test_overwrite_policy_still_avoids_clashes_inside_one_batch(docs, tmp_path):
    a = touch(docs / "one" / "doc.pdf")
    b = touch(docs / "two" / "doc.pdf")
    plan = plan_jobs([a, b], OutputSettings(output_dir=tmp_path / "out", conflict=ConflictPolicy.OVERWRITE))
    assert len({j.output_path for j in plan.jobs}) == 2


def test_duplicates_are_ignored(docs):
    a = touch(docs / "a.pdf")
    plan = plan_jobs([a, a, docs / "." / "a.pdf"], OutputSettings())
    assert len(plan.jobs) == 1
    assert [reason for _, reason in plan.ignored] == ["duplicate input"] * 2


def test_directory_requires_batch(docs):
    touch(docs / "a.pdf")
    with pytest.raises(UsageError, match="directory"):
        plan_jobs([docs], OutputSettings())


def test_batch_directory_scan_and_recursion(docs):
    touch(docs / "a.pdf")
    touch(docs / "B.PDF")
    touch(docs / "sub" / "c.pdf")
    touch(docs / "readme.txt")
    flat = plan_jobs([docs], OutputSettings(), batch=True)
    assert sorted(j.input_path.name for j in flat.jobs) == ["B.PDF", "a.pdf"]
    deep = plan_jobs([docs], OutputSettings(), batch=True, recursive=True)
    assert sorted(j.input_path.name for j in deep.jobs) == ["B.PDF", "a.pdf", "c.pdf"]


def test_recursive_output_dir_mirrors_structure(docs, tmp_path):
    touch(docs / "a.pdf")
    touch(docs / "sub" / "deeper" / "c.pdf")
    out = tmp_path / "out"
    plan = plan_jobs([docs], OutputSettings(output_dir=out), batch=True, recursive=True)
    assert {j.output_path for j in plan.jobs} == {
        out / "a_ocr.pdf", out / "sub" / "deeper" / "c_ocr.pdf"
    }


def test_directory_scan_ignores_earlier_results_and_temp_files(docs):
    touch(docs / "a.pdf")
    touch(docs / "a_ocr.pdf")
    touch(docs / "a_ocr_3.pdf")
    touch(docs / ".ocrizer-abc.partial.pdf")
    found, ignored = discover_pdfs(docs, recursive=False)
    assert [p.name for p in found] == ["a.pdf"]
    assert len(ignored) == 3


def test_explicit_files_named_like_results_are_still_processed(docs):
    f = touch(docs / "a_ocr.pdf")
    assert len(plan_jobs([f], OutputSettings()).jobs) == 1


def test_output_cannot_overwrite_input(docs):
    a = touch(docs / "a.pdf")
    with pytest.raises(UsageError, match="overwrite the input"):
        plan_jobs([a], OutputSettings(suffix=""))
    with pytest.raises(UsageError, match="overwrite the input"):
        plan_jobs([a], OutputSettings(), explicit_output=a)


def test_output_cannot_overwrite_another_input(docs):
    a = touch(docs / "a.pdf")
    b = touch(docs / "a_ocr.pdf")
    with pytest.raises(UsageError, match="another input"):
        plan_jobs([a, b], OutputSettings(conflict=ConflictPolicy.OVERWRITE))


def test_in_place_is_explicit(docs):
    a = touch(docs / "a.pdf")
    plan = plan_jobs([a], OutputSettings(in_place=True, conflict=ConflictPolicy.OVERWRITE))
    assert plan.jobs[0].output_path == a
    with pytest.raises(UsageError):
        plan_jobs([a], OutputSettings(in_place=True, output_dir=docs / "o"))


def test_in_place_with_text_export_never_replaces_an_existing_txt(docs):
    a = touch(docs / "a.pdf")
    plan_jobs([a], OutputSettings(in_place=True, conflict=ConflictPolicy.OVERWRITE), export_text=True)
    (docs / "a.txt").write_text("my notes")
    with pytest.raises(UsageError, match="already exists"):
        plan_jobs([a], OutputSettings(in_place=True, conflict=ConflictPolicy.OVERWRITE), export_text=True)


def test_explicit_output_rules(docs, tmp_path):
    a = touch(docs / "a.pdf")
    b = touch(docs / "b.pdf")
    target = tmp_path / "result.pdf"
    assert plan_jobs([a], OutputSettings(), explicit_output=target).jobs[0].output_path == target
    with pytest.raises(UsageError, match="exactly one"):
        plan_jobs([a, b], OutputSettings(), explicit_output=target)
    with pytest.raises(UsageError, match="together"):
        plan_jobs([a], OutputSettings(output_dir=tmp_path), explicit_output=target)
    with pytest.raises(UsageError, match=r"\.pdf"):
        plan_jobs([a], OutputSettings(), explicit_output=tmp_path / "result.txt")
    touch(target)
    with pytest.raises(UsageError, match="already exists"):
        plan_jobs([a], OutputSettings(), explicit_output=target)
    ok = plan_jobs([a], OutputSettings(conflict=ConflictPolicy.OVERWRITE), explicit_output=target)
    assert ok.jobs[0].output_path == target


def test_unicode_and_space_paths(docs):
    a = touch(docs / "zażółć gęślą" / "dokument końcowy ąę.pdf")
    plan = plan_jobs([docs], OutputSettings(), batch=True, recursive=True)
    assert plan.jobs[0].output_path == a.with_name("dokument końcowy ąę_ocr.pdf")


def test_missing_input_still_becomes_a_job(docs):
    """Validation happens in the worker so that one bad path never aborts a batch."""
    plan = plan_jobs([docs / "nope.pdf"], OutputSettings())
    assert len(plan.jobs) == 1


def test_expand_globs(docs):
    touch(docs / "a.pdf")
    touch(docs / "b.pdf")
    expanded = expand_globs([str(docs / "*.pdf"), str(docs / "nothing*.pdf"), str(docs / "a.pdf")])
    assert [Path(p).name for p in expanded] == ["a.pdf", "b.pdf", "nothing*.pdf", "a.pdf"]


@pytest.mark.parametrize("suffix", ["/escape", "\\escape", "\x00", "\n", None])
def test_invalid_suffix_is_rejected_before_planning(docs, suffix):
    with pytest.raises(UsageError, match="suffix"):
        plan_jobs([docs / "a.pdf"], OutputSettings(suffix=suffix))


def test_output_settings_accept_paths_and_enum_strings(docs, tmp_path):
    source = touch(docs / "a.pdf")
    out = tmp_path / "out"
    out.mkdir()
    touch(out / "a_ocr.pdf")
    plan = plan_jobs([source], OutputSettings(output_dir=str(out), conflict="skip"))
    assert plan.jobs[0].skip_reason
