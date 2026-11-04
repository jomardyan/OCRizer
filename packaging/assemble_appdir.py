"""Assemble an AppDir with frozen OCRizer, Tesseract, models and native dependencies."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from collect_licenses import main as collect_licenses

# Keep the target system's loader and glibc family, as required by AppImage portability rules.
_SYSTEM_LIBRARIES = re.compile(
    r"^(ld-linux.*|lib(c|m|pthread|dl|rt|resolv|util|nss_.*|anl)\.so(?:\..*)?)$"
)


def find_tessdata(binary: str) -> Path:
    """Locate installed models on both Tesseract 4 and 5 without guessing a version path."""
    listing = subprocess.run([binary, "--list-langs"], check=True, capture_output=True, text=True)
    candidates = []
    match = re.search(r'"([^"\n]+)"', listing.stdout + listing.stderr)
    if match:
        candidates.append(Path(match[1]))
    if os.environ.get("TESSDATA_PREFIX"):
        prefix = Path(os.environ["TESSDATA_PREFIX"])
        candidates.extend((prefix, prefix / "tessdata"))
    # Ubuntu 22.04's Tesseract 4 lists language names without the model path.
    # Ask the package database for the actual installed model, preserving distro layouts.
    inventory = subprocess.run(
        ["dpkg-query", "-L", "tesseract-ocr-eng"], capture_output=True, text=True, check=False
    )
    if inventory.returncode == 0:
        candidates.extend(
            Path(line).parent for line in inventory.stdout.splitlines() if line.endswith("/eng.traineddata")
        )
    for candidate in candidates:
        if all((candidate / f"{language}.traineddata").is_file() for language in ("eng", "pol", "osd")):
            return candidate
    raise SystemExit("Cannot locate complete Tesseract models; install tesseract-ocr-eng, -pol and -osd.")


def main() -> None:
    if sys.platform != "linux":
        raise SystemExit("AppDirs must be assembled on Linux.")
    root = Path(__file__).resolve().parent.parent
    destination = root / "build" / "OCRizer.AppDir"
    if destination.exists():
        if not destination.resolve().is_relative_to(root.resolve()) or destination.is_symlink():
            raise SystemExit(f"Refusing to replace an AppDir outside the checkout: {destination}")
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    application = destination / "usr" / "lib" / "ocrizer"
    shutil.copytree(root / "dist" / "OCRizer", application)
    for filename in ("AppRun", "ocrizer.desktop", "ocrizer.svg"):
        shutil.copy2(root / "packaging" / "linux" / filename, destination / filename)
    (destination / "AppRun").chmod(0o755)
    shutil.copy2(destination / "ocrizer.svg", destination / ".DirIcon")
    binary = shutil.which("tesseract")
    if binary is None:
        raise SystemExit("Install tesseract-ocr and its eng, pol, osd packages before building.")
    bin_dir = destination / "usr" / "bin"
    bin_dir.mkdir(parents=True)
    tesseract = bin_dir / "tesseract"
    shutil.copy2(binary, tesseract)
    tessdata = find_tessdata(binary)
    models = destination / "usr" / "share" / "tessdata"
    # Tesseract also needs configs/{hocr,pdf,tsv,txt}, tessconfigs and pdf.ttf.
    # Copy support files while limiting the much larger model files to supported languages.
    shutil.copytree(tessdata, models, ignore=shutil.ignore_patterns("*.traineddata"))
    for language in ("pol", "eng", "osd"):
        shutil.copy2(tessdata / f"{language}.traineddata", models)
    library_dir = destination / "usr" / "lib"
    # ldd lists the full transitive closure for Tesseract, including Leptonica and image codecs.
    result = subprocess.run(["ldd", binary], check=True, capture_output=True, text=True)
    if "not found" in result.stdout:
        raise SystemExit(f"Unresolved Tesseract libraries:\n{result.stdout}")
    native_files = [Path(binary)]
    for resolved in re.findall(r"=>\s+(/\S+)", result.stdout):
        library = Path(resolved)
        if not _SYSTEM_LIBRARIES.fullmatch(library.name):
            shutil.copy2(library, library_dir / library.name)
            native_files.append(library)
    # Preserve Debian copyright notices for bundled native code and trained models.
    notices = destination / "licenses" / "native"
    notices.mkdir(parents=True)
    packages = {"tesseract-ocr-eng", "tesseract-ocr-pol", "tesseract-ocr-osd"}
    for native_file in native_files:
        owner = subprocess.run(["dpkg-query", "-S", str(native_file)], capture_output=True, text=True)
        if owner.returncode == 0:
            packages.update(line.split(": ", 1)[0] for line in owner.stdout.splitlines())
    for package in packages:
        package_name = package.split(":", 1)[0]
        copyright_file = Path("/usr/share/doc") / package_name / "copyright"
        if copyright_file.is_file():
            shutil.copy2(copyright_file, notices / f"{package_name}.copyright")
    shutil.copy2(root / "README.md", destination / "README.md")
    shutil.copy2(root / "LICENSE", destination / "LICENSE")
    sys.argv = ["collect_licenses.py", os.fspath(destination)]
    collect_licenses()
    print(destination)


if __name__ == "__main__":
    main()
