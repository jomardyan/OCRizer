"""Discovery and verification of external OCR dependencies.

The only external program that is *required* is Tesseract (plus its ``pol`` and ``eng`` language
packs). Ghostscript is optional: OCRmyPDF 17 rasterises pages with pypdfium2 and builds plain
PDF output without it.

No executable path is hard-coded. Tesseract is located, in order, via an explicit setting
(``OCRIZER_TESSERACT`` / config ``tesseract_path``), a Tesseract bundled next to a frozen
(PyInstaller) build, the ``PATH``, and finally default per-platform install locations.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ocrizer.errors import MissingDependencyError

REQUIRED_LANGUAGE_PACKS = ("pol", "eng")
MIN_TESSERACT_VERSION = (4, 1, 1)  # matches OCRmyPDF 17's engine requirement
_EXE = "tesseract.exe" if os.name == "nt" else "tesseract"

_HINTS = {
    "win32": (
        "Install Tesseract with:  winget install UB-Mannheim.TesseractOCR\n"
        "  Polish is not included by default: re-run the installer and tick 'Polish' under\n"
        "  'Additional language data', or download pol.traineddata from\n"
        "  https://github.com/tesseract-ocr/tessdata_fast (or tessdata_best for higher accuracy)\n"
        "  into the 'tessdata' folder next to tesseract.exe.\n"
        "  If tesseract.exe is not on PATH, set OCRIZER_TESSERACT to its full path."
    ),
    "darwin": "Install with:  brew install tesseract tesseract-lang",
    "linux": (
        "Debian/Ubuntu:  sudo apt install tesseract-ocr tesseract-ocr-pol tesseract-ocr-eng\n"
        "  Fedora:         sudo dnf install tesseract tesseract-langpack-pol tesseract-langpack-eng\n"
        "  Arch:           sudo pacman -S tesseract tesseract-data-pol tesseract-data-eng"
    ),
}


def install_hint() -> str:
    key = "win32" if sys.platform == "win32" else "darwin" if sys.platform == "darwin" else "linux"
    return _HINTS[key]


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    name: str
    ok: bool
    detail: str
    required: bool = True
    hint: str = ""


@dataclass(frozen=True, slots=True)
class DependencyReport:
    items: tuple[DependencyStatus, ...]
    tesseract: Path | None = None
    languages: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return all(i.ok for i in self.items if i.required)

    @property
    def problems(self) -> tuple[DependencyStatus, ...]:
        return tuple(i for i in self.items if i.required and not i.ok)

    @property
    def has_osd(self) -> bool:
        return "osd" in self.languages

    def format(self) -> str:
        lines = []
        for item in self.items:
            mark = "OK     " if item.ok else ("MISSING" if item.required else "absent ")
            tag = "" if item.required else " (optional)"
            lines.append(f"  [{mark}] {item.name}{tag}: {item.detail}")
        if not self.ok:
            lines.append("")
            lines.append(install_hint())
        return "\n".join(lines)

    def raise_if_missing(self) -> None:
        if not self.ok:
            problems = "; ".join(f"{p.name}: {p.detail}" for p in self.problems)
            raise MissingDependencyError(
                f"Required OCR dependencies are missing ({problems}).", hint=install_hint()
            )


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):  # standalone build: a bundled Tesseract next to the app
        app_dir = Path(sys.executable).parent
        dirs += [app_dir / "Tesseract-OCR", app_dir / "tesseract"]
    if sys.platform == "win32":
        for var in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(var):
                dirs.append(Path(os.environ[var]) / "Tesseract-OCR")
        if os.environ.get("LOCALAPPDATA"):
            dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Tesseract-OCR")
    elif sys.platform == "darwin":
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    return dirs


def find_tesseract(configured: str | None = None) -> Path | None:
    """Locate the tesseract executable, or return None."""
    if configured:
        p = Path(configured).expanduser()
        if p.is_dir():
            p = p / _EXE
        if p.is_file():
            return p.resolve()
        found = shutil.which(configured)
        return Path(found).resolve() if found else None
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).parent
        for directory in (app_dir / "Tesseract-OCR", app_dir / "tesseract"):
            bundled = directory / _EXE
            if bundled.is_file():
                return bundled.resolve()
    found = shutil.which("tesseract")
    if found:
        return Path(found).resolve()
    for directory in _candidate_dirs():
        candidate = directory / _EXE
        if candidate.is_file():
            return candidate.resolve()
    return None


def prepare_environment(configured: str | None = None) -> Path | None:
    """Make the located Tesseract visible to this process and to every child process.

    OCRmyPDF finds Tesseract through ``PATH``, so the executable's directory is prepended when
    it was found outside of it.
    """
    exe = find_tesseract(configured)
    if exe is not None:
        directory = str(exe.parent)
        # An explicit selection must take precedence even when its directory already
        # occurs later on PATH, behind another Tesseract installation.
        key = os.path.normcase(os.path.abspath(directory))
        entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                   if entry and os.path.normcase(os.path.abspath(entry)) != key]
        os.environ["PATH"] = os.pathsep.join([directory, *entries])
    return exe


def _run(cmd: list[str], timeout: float = 30.0) -> str:
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=True, **kwargs,
    )
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _parse_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"tesseract\s+v?(\d+)\.(\d+)(?:\.(\d+))?", text, re.IGNORECASE)
    if not match:
        return None
    return tuple(int(g) for g in match.groups() if g is not None)


def _module_present(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def check_dependencies(
    languages: tuple[str, ...] = REQUIRED_LANGUAGE_PACKS,
    *,
    configured_tesseract: str | None = None,
    include_gui: bool = False,
) -> DependencyReport:
    """Verify Tesseract, its language packs and the Python packages the engine needs."""
    items: list[DependencyStatus] = []

    py_ok = sys.version_info >= (3, 11)
    items.append(DependencyStatus("Python", py_ok, sys.version.split()[0] + " (3.11+ required)"))

    tesseract = prepare_environment(configured_tesseract)
    installed: tuple[str, ...] = ()
    if tesseract is None:
        items.append(DependencyStatus("Tesseract", False, "executable not found"))
    else:
        try:
            version = _parse_version(_run([str(tesseract), "--version"]))
            listing = _run([str(tesseract), "--list-langs"])
        except (OSError, subprocess.SubprocessError) as exc:
            items.append(DependencyStatus("Tesseract", False, f"cannot execute {tesseract}: {exc}"))
        else:
            ver_text = ".".join(map(str, version)) if version else "unknown version"
            ver_ok = version is not None and version >= MIN_TESSERACT_VERSION
            items.append(
                DependencyStatus(
                    "Tesseract", ver_ok, f"{ver_text} at {tesseract}"
                    + ("" if ver_ok else " (4.1.1 or newer required)"),
                )
            )
            lines = [ln.strip() for ln in listing.splitlines() if ln.strip()]
            installed = tuple(ln for ln in lines if re.fullmatch(r"[A-Za-z0-9_]+", ln))

    for lang in languages:
        present = lang in installed
        detail = "installed" if present else f"language pack '{lang}' not installed"
        items.append(DependencyStatus(f"Tesseract language '{lang}'", present, detail))
    items.append(
        DependencyStatus(
            "Tesseract 'osd' data",
            "osd" in installed,
            "installed (automatic page rotation available)" if "osd" in installed
            else "not installed: automatic page rotation will be disabled",
            required=False,
        )
    )

    for module, label in (
        ("ocrmypdf", "ocrmypdf"), ("pikepdf", "pikepdf"), ("pypdfium2", "pypdfium2"),
        ("py3langid", "py3langid"), ("psutil", "psutil"), ("PIL", "pillow"),
    ):
        present = _module_present(module)
        items.append(DependencyStatus(f"Python package {label}", present,
                                      "installed" if present else "not installed: pip install ocrizer"))
    gs = shutil.which("gswin64c") or shutil.which("gs")
    items.append(
        DependencyStatus("Ghostscript", gs is not None, gs or "not found (not needed)", required=False)
    )
    if include_gui:
        present = _module_present("PySide6")
        items.append(
            DependencyStatus("PySide6 (GUI)", present,
                             "installed" if present else "not installed: pip install \"ocrizer[gui]\"",
                             required=False)
        )
    return DependencyReport(tuple(items), tesseract, installed)
