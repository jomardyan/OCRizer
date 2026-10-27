"""Smoke-test real OCR, spawned workers and GUI startup in a desktop distribution."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont


def _scan(path: Path) -> None:
    candidates = (
        os.environ.get("OCRIZER_TEST_FONT", ""),
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    font_path = next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)
    if font_path is None:
        raise SystemExit("Set OCRIZER_TEST_FONT to a TrueType font before running the smoke test.")
    image = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 32)
    for line in range(10):
        draw.text(
            (90, 100 + line * 75), "OCRizer makes scanned documents searchable.", font=font, fill="black"
        )
    image.save(path, "PDF", resolution=150)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--gui", type=Path)
    parser.add_argument("--appimage", type=Path)
    parser.add_argument("--tesseract", type=Path)
    args = parser.parse_args()
    if args.appimage:
        cli = [str(args.appimage.resolve()), "--cli"]
        gui = [str(args.appimage.resolve())]
    elif args.cli and args.gui:
        cli = [str(args.cli.resolve())]
        gui = [str(args.gui.resolve())]
    else:
        parser.error("pass --appimage, or both --cli and --gui")
    environment = os.environ.copy()
    environment.update(QT_QPA_PLATFORM="offscreen", APPIMAGE_EXTRACT_AND_RUN="1")
    if args.tesseract:
        environment["OCRIZER_TESSERACT"] = str(args.tesseract.resolve())
    else:
        environment.pop("OCRIZER_TESSERACT", None)
        if os.name == "nt":
            environment["PATH"] = os.pathsep.join(
                entry for entry in environment.get("PATH", "").split(os.pathsep)
                if not (Path(entry) / "tesseract.exe").is_file()
            )
    with tempfile.TemporaryDirectory(prefix="ocrizer-smoke-") as temporary:
        folder = Path(temporary)
        environment["OCRIZER_CONFIG"] = str(folder / "config.json")
        subprocess.run([*cli, "--version"], check=True, env=environment, timeout=60)
        subprocess.run([*cli, "--check"], check=True, env=environment, timeout=90)
        inputs = [folder / "scan one.pdf", folder / "zażółć.pdf"]
        for source in inputs:
            _scan(source)
        output = folder / "results"
        subprocess.run(
            [*cli, *map(str, inputs), "--output-dir", str(output), "--workers", "2",
             "--language", "auto", "--no-rotate", "--optimize", "0", "--export-text"],
            check=True, env=environment, timeout=240,
        )
        for source in inputs:
            result = output / f"{source.stem}_ocr.pdf"
            with pdfium.PdfDocument(result) as document:
                assert len(document) == 1, result
                with closing(document[0]) as page, closing(page.get_textpage()) as text_page:
                    assert "searchable" in text_page.get_text_range().lower(), result
            assert "searchable" in result.with_suffix(".txt").read_text(encoding="utf-8").lower()
        process = subprocess.Popen(gui, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.communicate(timeout=15)
        else:
            raise RuntimeError(
                f"GUI exited before its event loop was verified ({process.returncode}):\n"
                + (stdout + stderr).decode("utf-8", errors="replace")
            )
    print("Desktop smoke test passed: parallel OCR, searchable output, text export, GUI startup.")


if __name__ == "__main__":
    main()
