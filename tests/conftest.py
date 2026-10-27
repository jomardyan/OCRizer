"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import difflib
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # lets spawned workers import tests/fake_workers.py

import factory  # noqa: E402

from ocrizer.deps import check_dependencies  # noqa: E402


def _ocr_unavailable_reason() -> str | None:
    report = check_dependencies()
    if not report.ok:
        return "Tesseract with pol+eng language packs is not available: " + "; ".join(
            f"{p.name} ({p.detail})" for p in report.problems
        )
    if not factory.has_font():
        return "no TrueType font with Polish glyphs found (set OCRIZER_TEST_FONT)"
    return None


_REASON = _ocr_unavailable_reason()


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    if _REASON is None:
        return
    skip = pytest.mark.skip(reason=_REASON)
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Never read or write the developer's real configuration file."""
    monkeypatch.setenv("OCRIZER_CONFIG", str(tmp_path / "config" / "config.json"))
    for var in ("OCRIZER_LANGUAGE", "OCRIZER_WORKERS", "OCRIZER_TESSERACT"):
        monkeypatch.delenv(var, raising=False)
    # prepare_environment() edits PATH; make sure no test leaks such a change into the next one
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    folder = tmp_path / "docs"
    folder.mkdir()
    return folder


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\f", " ")).strip().casefold()


def similarity(expected: str, actual: str) -> float:
    return difflib.SequenceMatcher(None, normalize(expected), normalize(actual)).ratio()


@pytest.fixture
def sim():  # type: ignore[no-untyped-def]
    return similarity
