"""Helpers that build test PDFs on the fly (no binary fixtures are checked in).

Scanned documents are simulated by rendering text with a real TrueType font into a bitmap and
saving it as an image-only PDF, i.e. a PDF without any text layer.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import pikepdf
from PIL import Image, ImageDraw, ImageFont

POLISH_PANGRAM = "Zażółć gęślą jaźń"

POLISH_TEXT = [
    "Umowa o kredyt konsumencki została zawarta pomiędzy stronami.",
    "Kredytobiorca zobowiązuje się do terminowej spłaty rat kapitałowych.",
    "Zażółć gęślą jaźń oraz pchnąć w tę łódź jeża lub ośm skrzyń fig.",
    "Dane osobowe są przetwarzane zgodnie z obowiązującymi przepisami.",
]
ENGLISH_TEXT = [
    "The agreement was made between the parties on the date below.",
    "The borrower undertakes to repay every instalment on time.",
    "The quick brown fox jumps over the lazy dog near the river bank.",
    "Personal data is processed in accordance with applicable law.",
]

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def find_font() -> Path | None:
    override = os.environ.get("OCRIZER_TEST_FONT")
    for candidate in ([override] if override else []) + _FONT_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def text_image(
    lines: list[str], *, size: tuple[int, int] = (2480, 1754), font_px: int = 52, angle: float = 0
) -> Image.Image:
    """Render ``lines`` black-on-white at 300 DPI (A4 landscape by default)."""
    font_path = find_font()
    if font_path is None:
        raise RuntimeError("no TrueType font with Polish glyphs available")
    font = ImageFont.truetype(str(font_path), font_px)
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    y = 160
    for line in lines:
        draw.text((140, y), line, fill=0, font=font)
        y += int(font_px * 2.0)
    if angle:
        image = image.rotate(angle, expand=False, fillcolor=255, resample=Image.BICUBIC)
    return image


def scanned_pdf(path: Path, pages: list[list[str]], **image_kwargs) -> Path:
    """Write an image-only PDF with one page per list of lines."""
    images = [text_image(lines, **image_kwargs) for lines in pages]
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, "PDF", resolution=300.0, save_all=True, append_images=images[1:])
    return path


def text_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Write a PDF with a real text layer (born-digital)."""
    from fpdf import FPDF

    pdf = FPDF(unit="pt", format="A4")
    font_path = find_font()
    if font_path is not None:
        pdf.add_font("Test", "", str(font_path))
        family = "Test"
    else:  # core fonts cannot render Polish, fine for English-only fallbacks
        family = "Helvetica"
    for lines in pages:
        pdf.add_page()
        pdf.set_font(family, size=14)
        for line in lines:
            pdf.cell(0, 24, line, new_x="LMARGIN", new_y="NEXT")
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path


def image_text_pdf(
    path: Path, body: str, image_lines: list[str], *, image_width_pt: float = 450.0
) -> Path:
    """A born-digital page whose figure is a raster image with text inside it."""
    import io

    from fpdf import FPDF

    figure = text_image(image_lines, size=(1200, 120 + 104 * len(image_lines)))
    buffer = io.BytesIO()
    figure.save(buffer, "PNG")
    buffer.seek(0)

    pdf = FPDF(unit="pt", format="A4")
    font_path = find_font()
    if font_path is not None:
        pdf.add_font("Test", "", str(font_path))
    pdf.add_page()
    pdf.set_font("Test" if font_path else "Helvetica", size=14)
    pdf.cell(0, 24, body, new_x="LMARGIN", new_y="NEXT")
    pdf.image(buffer, x=70, y=100, w=image_width_pt)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path


def mixed_pdf(path: Path, scanned: list[list[str]], digital: list[str]) -> Path:
    """Scanned page(s) followed by a page that already has text, merged into one PDF."""
    tmp_scan = scanned_pdf(path.with_name(path.stem + "_a.tmp.pdf"), scanned)
    tmp_text = text_pdf(path.with_name(path.stem + "_b.tmp.pdf"), [digital])
    with pikepdf.open(tmp_scan) as a, pikepdf.open(tmp_text) as b:
        a.pages.extend(b.pages)
        a.save(path)
    tmp_scan.unlink()
    tmp_text.unlink()
    return path


def encrypted_pdf(path: Path, source: Path) -> Path:
    with pikepdf.open(source) as pdf:
        pdf.save(path, encryption=pikepdf.Encryption(user="secret", owner="secret"))
    return path


def corrupt_pdf(path: Path, source: Path) -> Path:
    """A file that starts like a PDF but whose structure is destroyed beyond repair."""
    data = bytearray(source.read_bytes())
    rng = random.Random(1234)
    for i in range(16, len(data)):
        data[i] = rng.randrange(256)
    path.write_bytes(bytes(data))
    return path


def signed_pdf(path: Path, source: Path) -> Path:
    """Flag the document as digitally signed (AcroForm /SigFlags), which is what OCRmyPDF checks."""
    with pikepdf.open(source) as pdf:
        pdf.Root.AcroForm = pdf.make_indirect(pikepdf.Dictionary(Fields=pikepdf.Array([]), SigFlags=3))
        pdf.save(path)
    return path


def empty_pdf(path: Path) -> Path:
    path.write_bytes(b"")
    return path


def zero_page_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.save(path)
    return path


def has_font() -> bool:
    return find_font() is not None


def platform_note() -> str:
    return sys.platform
