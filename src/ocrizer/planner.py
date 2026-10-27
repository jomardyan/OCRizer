"""Input discovery and output-path planning.

Planning happens up front, in the calling process, so that two jobs can never be assigned the
same output file and no input can be overwritten by accident. The rules:

* Inputs are de-duplicated by resolved path.
* Directories are only expanded in batch mode; hidden files, our own temporary files and files
  that look like earlier results (``*_ocr.pdf``, ``*_ocr_1.pdf``) are ignored during directory scans.
* ``name.pdf`` -> ``name_ocr.pdf`` next to the input (or in ``--output-dir``; with a recursive
  scan the sub-directory structure is mirrored there).
* An existing output is never silently replaced: by default the new file gets ``_1``, ``_2`` ...
  appended; ``skip`` and ``overwrite`` are explicit opt-ins.
* An output may only equal an input when ``in_place`` was requested explicitly.
"""

from __future__ import annotations

import glob
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ocrizer.config import ConflictPolicy, OutputSettings
from ocrizer.errors import UsageError
from ocrizer.models import Job

TEMP_PREFIX = ".ocrizer-"


@dataclass(slots=True)
class Plan:
    jobs: list[Job] = field(default_factory=list)
    ignored: list[tuple[Path, str]] = field(default_factory=list)  # (path, reason)


def path_key(path: Path) -> str:
    """Case/alias-insensitive identity of a path (on Windows too), without requiring existence."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    return os.path.normcase(str(resolved))


def expand_globs(arguments: Iterable[str]) -> list[str]:
    """Expand wildcard arguments ourselves (Windows shells do not)."""
    expanded: list[str] = []
    for arg in arguments:
        if not Path(arg).exists() and any(c in arg for c in "*?["):
            matches = sorted(glob.glob(arg, recursive=True))
            expanded.extend(matches or [arg])
        else:
            expanded.append(arg)
    return expanded


def discover_pdfs(
    root: Path, *, recursive: bool, suffix: str = "_ocr"
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Return ``(pdfs, ignored)`` found under ``root`` in a stable (sorted) order."""
    found: list[Path] = []
    ignored: list[tuple[Path, str]] = []
    pattern = "**/*" if recursive else "*"
    for path in sorted(root.glob(pattern), key=lambda p: str(p).lower()):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        if path.name.startswith("."):
            ignored.append((path, "hidden or temporary file"))
        elif suffix and re.search(rf"{re.escape(suffix)}(_\d+)?$", path.stem, re.IGNORECASE):
            ignored.append((path, f"looks like an earlier OCR result (*{suffix}.pdf)"))
        else:
            found.append(path)
    return found, ignored


def _candidate(base: Path, stem: str, suffix: str, counter: int) -> Path:
    tail = f"_{counter}" if counter else ""
    return base / f"{stem}{suffix}{tail}.pdf"


def plan_jobs(
    inputs: Sequence[str | os.PathLike[str]],
    output: OutputSettings,
    *,
    batch: bool = False,
    recursive: bool = False,
    explicit_output: Path | None = None,
    export_text: bool = False,
) -> Plan:
    """Turn user inputs into a list of :class:`Job` with unique, safe output paths."""
    if not isinstance(output.suffix, str) or any(
        c in output.suffix for c in '\\/:*?"<>|\x00\r\n'
    ):
        raise UsageError(f"Invalid output suffix {output.suffix!r}.")
    try:
        output = replace(
            output, conflict=ConflictPolicy(output.conflict),
            output_dir=Path(output.output_dir) if output.output_dir is not None else None,
        )
        explicit_output = Path(explicit_output) if explicit_output is not None else None
    except (ValueError, TypeError) as exc:
        raise UsageError(f"Invalid output settings: {exc}") from exc
    if explicit_output is not None and output.output_dir is not None:
        raise UsageError("--output and --output-dir cannot be used together.")
    if output.in_place and (explicit_output is not None or output.output_dir is not None):
        raise UsageError("--in-place cannot be combined with --output or --output-dir.")
    if explicit_output is not None and explicit_output.suffix.lower() != ".pdf":
        raise UsageError(f"The output file must have a .pdf extension: {explicit_output}")

    plan = Plan()
    entries: list[tuple[Path, Path | None]] = []  # (pdf, root directory it was found under)
    seen: set[str] = set()

    def add(path: Path, root: Path | None) -> None:
        key = path_key(path)
        if key in seen:
            plan.ignored.append((path, "duplicate input"))
        else:
            seen.add(key)
            entries.append((path, root))

    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            if not batch:
                raise UsageError(
                    f"{path} is a directory.", hint="Use --batch to process every PDF inside it."
                )
            pdfs, ignored = discover_pdfs(path, recursive=recursive, suffix=output.suffix)
            plan.ignored.extend(ignored)
            for pdf in pdfs:
                add(pdf, path)
        else:
            add(path, None)

    if explicit_output is not None and len(entries) != 1:
        raise UsageError("--output can only be used with exactly one input file.")

    input_keys = {path_key(p) for p, _ in entries}
    reserved: set[str] = set()

    def taken(pdf: Path) -> bool:
        if path_key(pdf) in reserved or pdf.exists():
            return True
        if export_text:
            txt = pdf.with_suffix(".txt")
            return path_key(txt) in reserved or txt.exists()
        return False

    for index, (src, root) in enumerate(entries):
        skip_reason = ""
        if output.in_place:
            target = src
            if export_text and target.with_suffix(".txt").exists():
                raise UsageError(
                    f"{target.with_suffix('.txt')} already exists and would be overwritten by --export-text.",
                    hint="Move or delete it, or do not combine --in-place with --export-text.",
                )
        elif explicit_output is not None:
            target = explicit_output
        else:
            base = output.output_dir if output.output_dir is not None else src.parent
            if output.output_dir is not None and root is not None:
                base = base / src.parent.relative_to(root)
            target = _candidate(base, src.stem, output.suffix, 0)

        if not output.in_place:
            if path_key(target) == path_key(src):
                raise UsageError(
                    f"The output path would overwrite the input file: {src}",
                    hint="Use a different suffix/output directory, or --in-place to replace it.",
                )
            if path_key(target) in input_keys:
                raise UsageError(f"The output path would overwrite another input file: {target}")
            if taken(target):
                if output.conflict is ConflictPolicy.OVERWRITE and path_key(target) not in reserved:
                    pass  # explicit permission to replace an existing result
                elif explicit_output is not None and output.conflict is ConflictPolicy.RENAME:
                    raise UsageError(
                        f"Output file already exists: {target}",
                        hint="Use --overwrite to replace it, or choose another name.",
                    )
                elif output.conflict is ConflictPolicy.SKIP:
                    skip_reason = f"Output already exists: {target.name}"
                else:  # RENAME (also resolves clashes between jobs of the same batch)
                    counter = 1
                    candidate = _candidate(target.parent, src.stem, output.suffix, counter)
                    while taken(candidate):
                        counter += 1
                        candidate = _candidate(target.parent, src.stem, output.suffix, counter)
                    target = candidate
        reserved.add(path_key(target))
        if export_text:
            reserved.add(path_key(target.with_suffix(".txt")))
        plan.jobs.append(Job(index, src, target, skip_reason))
    return plan
