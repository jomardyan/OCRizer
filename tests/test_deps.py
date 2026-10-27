from __future__ import annotations

from pathlib import Path

import pytest

from ocrizer import deps
from ocrizer.errors import MissingDependencyError

TESSERACT_VERSION = "tesseract 5.4.0\n leptonica-1.84.1"
LANG_LIST = 'List of available languages in "/x/tessdata/" (3):\neng\nosd\npol\n'


def fake_run_factory(langs: str = LANG_LIST, version: str = TESSERACT_VERSION):
    def fake_run(cmd, timeout=30.0):
        return langs if "--list-langs" in cmd else version

    return fake_run


@pytest.fixture
def fake_tesseract(tmp_path, monkeypatch):
    exe = tmp_path / "bin" / ("tesseract.exe" if deps.os.name == "nt" else "tesseract")
    exe.parent.mkdir()
    exe.write_text("")
    exe.chmod(0o755)
    monkeypatch.setattr(deps, "find_tesseract", lambda configured=None: exe)
    return exe


def test_all_present(fake_tesseract, monkeypatch):
    monkeypatch.setattr(deps, "_run", fake_run_factory())
    report = deps.check_dependencies()
    assert report.ok and report.has_osd
    assert report.tesseract == fake_tesseract
    assert "5.4.0" in report.format()


def test_missing_polish_pack_is_reported_with_instructions(fake_tesseract, monkeypatch):
    monkeypatch.setattr(deps, "_run", fake_run_factory("List:\neng\nosd\n"))
    report = deps.check_dependencies()
    assert not report.ok
    assert [p.name for p in report.problems] == ["Tesseract language 'pol'"]
    assert "pol" in report.format() and deps.install_hint() in report.format()
    with pytest.raises(MissingDependencyError) as excinfo:
        report.raise_if_missing()
    assert excinfo.value.hint == deps.install_hint()


def test_only_requested_languages_are_required(fake_tesseract, monkeypatch):
    monkeypatch.setattr(deps, "_run", fake_run_factory("List:\neng\n"))
    assert deps.check_dependencies(("eng",)).ok
    assert not deps.check_dependencies(("eng", "pol")).ok


def test_missing_osd_is_optional(fake_tesseract, monkeypatch):
    monkeypatch.setattr(deps, "_run", fake_run_factory("List:\neng\npol\n"))
    report = deps.check_dependencies()
    assert report.ok and not report.has_osd


@pytest.mark.parametrize("version", ["3.05.02", "4.1.0"])
def test_old_tesseract_is_rejected(fake_tesseract, monkeypatch, version):
    monkeypatch.setattr(deps, "_run", fake_run_factory(version=f"tesseract {version}"))
    assert not deps.check_dependencies().ok


def test_ocrmypdf_minimum_tesseract_version_is_accepted(fake_tesseract, monkeypatch):
    monkeypatch.setattr(deps, "_run", fake_run_factory(version="tesseract 4.1.1"))
    assert deps.check_dependencies().ok


def test_tesseract_not_found(monkeypatch):
    monkeypatch.setattr(deps, "find_tesseract", lambda configured=None: None)
    report = deps.check_dependencies()
    assert not report.ok
    assert any(p.name == "Tesseract" for p in report.problems)


def test_find_tesseract_explicit_path_and_directory(tmp_path):
    exe = tmp_path / deps._EXE
    exe.write_text("")
    assert deps.find_tesseract(str(exe)) == exe
    assert deps.find_tesseract(str(tmp_path)) == exe
    assert deps.find_tesseract(str(tmp_path / "nope")) is None


def test_frozen_app_prefers_its_bundled_tesseract_but_allows_explicit_override(tmp_path, monkeypatch):
    bundled = tmp_path / "Tesseract-OCR" / deps._EXE
    bundled.parent.mkdir()
    bundled.write_text("")
    system = tmp_path / "system" / deps._EXE
    system.parent.mkdir()
    system.write_text("")
    monkeypatch.setattr(deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(deps.sys, "executable", str(tmp_path / "OCRizer.exe"))
    monkeypatch.setattr(deps.shutil, "which", lambda name: str(system))
    assert deps.find_tesseract() == bundled
    assert deps.find_tesseract(str(system)) == system


def test_prepare_environment_adds_directory_to_path(tmp_path, monkeypatch):
    exe = tmp_path / "tools" / deps._EXE
    exe.parent.mkdir()
    exe.write_text("")
    monkeypatch.setenv("PATH", "/nonexistent")
    assert deps.prepare_environment(str(exe)) == exe
    assert deps.os.environ["PATH"].split(deps.os.pathsep)[0] == str(exe.parent)


def test_explicit_tesseract_takes_priority_over_existing_path_entry(tmp_path, monkeypatch):
    first, selected = tmp_path / "first", tmp_path / "selected"
    selected.mkdir()
    executable = selected / deps._EXE
    executable.write_text("")
    monkeypatch.setenv("PATH", deps.os.pathsep.join(map(str, [first, selected, selected])))
    deps.prepare_environment(str(executable))
    assert deps.os.environ["PATH"].split(deps.os.pathsep) == [str(selected), str(first)]


def test_unknown_tesseract_version_is_not_accepted(fake_tesseract, monkeypatch):
    monkeypatch.setattr(deps, "_run", fake_run_factory(version="some other executable"))
    assert not deps.check_dependencies().ok


def test_failed_tesseract_command_is_reported(fake_tesseract, monkeypatch):
    import subprocess

    def fail(cmd, timeout=30):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(deps, "_run", fail)
    report = deps.check_dependencies()
    assert not report.ok
    assert "cannot execute" in report.problems[0].detail


def test_parse_version():
    assert deps._parse_version("tesseract v5.3.1\n") == (5, 3, 1)
    assert deps._parse_version("garbage") is None


def test_install_hints_exist_for_every_platform(monkeypatch):
    for platform, needle in (("win32", "winget"), ("darwin", "brew"), ("linux", "apt")):
        monkeypatch.setattr(deps.sys, "platform", platform)
        assert needle in deps.install_hint()
    assert isinstance(deps.Path, type(Path))
