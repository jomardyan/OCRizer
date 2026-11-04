from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
spec = importlib.util.spec_from_file_location("assemble_appdir", PACKAGING / "assemble_appdir.py")
assert spec is not None and spec.loader is not None
assembler = importlib.util.module_from_spec(spec)
# assemble_appdir is normally executed directly alongside collect_licenses.py.
sys.path.insert(0, str(PACKAGING))
try:
    spec.loader.exec_module(assembler)
finally:
    sys.path.pop(0)


@pytest.mark.parametrize("format", ["quoted-stdout", "quoted-stderr", "legacy", "environment"])
def test_installed_models_are_located_across_tesseract_versions(tmp_path, monkeypatch, format):
    models = tmp_path / "models with spaces"
    models.mkdir()
    for name in ("eng", "pol", "osd"):
        (models / f"{name}.traineddata").write_bytes(b"model")
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    if format == "environment":
        monkeypatch.setenv("TESSDATA_PREFIX", str(models))
    listing = f'List of available languages in "{models}" (3):\neng\nosd\npol\n'

    def run(command, **kwargs):
        if command[0] == "dpkg-query":
            inventory = str(models / "eng.traineddata").replace("\\", "/") + "\n" if format == "legacy" else ""
            return subprocess.CompletedProcess(command, 0 if format == "legacy" else 1, inventory, "")
        text = listing if format.startswith("quoted") else "List of available languages (3):\neng\nosd\npol\n"
        return subprocess.CompletedProcess(command, 0, "" if format == "quoted-stderr" else text, text if format == "quoted-stderr" else "")

    monkeypatch.setattr(assembler.subprocess, "run", run)
    assert assembler.find_tessdata("tesseract") == models


def test_missing_language_models_fail_before_packaging(tmp_path, monkeypatch):
    (tmp_path / "eng.traineddata").write_bytes(b"model")
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(assembler.subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, 0, f'List in "{tmp_path}" (1):\neng\n', ""))
    with pytest.raises(SystemExit, match="complete Tesseract models"):
        assembler.find_tessdata("tesseract")
