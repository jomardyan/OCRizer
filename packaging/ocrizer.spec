# PyInstaller spec: builds dist/OCRizer/ containing OCRizer(.exe) (GUI) and pdfocr(.exe) (CLI).
#
#   pip install -e ".[gui,dev]"
#   pyinstaller packaging/ocrizer.spec --noconfirm
#
# Tesseract is NOT bundled. Either install it system-wide (see README) or copy a Tesseract
# folder (tesseract.exe + tessdata with pol/eng/osd) to dist/OCRizer/Tesseract-OCR/ to ship a
# self-contained build; the application looks there first when running frozen.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
src = str(root / "src")

hiddenimports = (
    ["ocrizer._ocr_progress", "ocrizer.worker", "ocrizer.engine"]  # loaded by name at run time
    + collect_submodules("ocrmypdf", filter=lambda name: not name.startswith("ocrmypdf._pyinstaller"))
    + collect_submodules("ocrizer")
)
datas = (
    collect_data_files("py3langid")
    + collect_data_files("ocrmypdf")
    + collect_data_files("pikepdf")
    + copy_metadata("ocrmypdf", recursive=True)
    + copy_metadata("ocrizer")
)
binaries = collect_dynamic_libs("pypdfium2_raw") + collect_dynamic_libs("pypdfium2")
excludes = ["tkinter", "pytest", "mypy", "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.Qt3DCore"]


def analysis(script):
    return Analysis(
        [str(root / "packaging" / script)], pathex=[src], hiddenimports=hiddenimports,
        datas=datas, binaries=binaries, excludes=excludes,
    )


cli = analysis("entry_cli.py")
gui = analysis("entry_gui.py")
# Both executables are collected into the same directory. MERGE is intended for separate
# application directories; its relative dependency references break this shared layout.
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="pdfocr", console=True)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="OCRizer", console=False)
COLLECT(
    cli_exe, cli.binaries, cli.datas,
    gui_exe, gui.binaries, gui.datas,
    name="OCRizer",
)
