# OCRizer

Turn scanned, image-only PDFs into **searchable PDFs** with an invisible, selectable text layer.
Polish and English, GUI and command line, single files and large batches. Everything runs
locally and offline: no document or text ever leaves the machine.

OCRizer is a thin, carefully engineered layer around two proven open-source tools:
[OCRmyPDF](https://ocrmypdf.readthedocs.io/) (PDF handling, text-layer grafting) and
[Tesseract](https://github.com/tesseract-ocr/tesseract) (the OCR engine). It adds automatic
Polish/English detection, safe batch processing, a desktop GUI and a scriptable CLI that share
one engine.

* Python 3.11+ · Windows and Linux (macOS should work, see [Platform notes](#platform-notes))
* GUI: PySide6 · CLI: `pdfocr`
* License: [MIT](LICENSE). Bundled dependencies retain their own licenses.

---

## Installation

### Desktop downloads

Download a versioned desktop artifact from the project's GitHub release. Desktop packages
include Python, Qt, Tesseract and the Polish, English and orientation models; a separate
Python or Tesseract installation is unnecessary.

* **Windows x86_64:** extract the entire `OCRizer-VERSION-windows-x86_64.zip`, then run
  `OCRizer/OCRizer.exe` for the GUI or `OCRizer/pdfocr.exe` for the command line. Keep the
  `_internal` and `Tesseract-OCR` folders beside the executables.
* **Linux x86_64:** make the AppImage executable, then launch it:

  ```bash
  chmod +x OCRizer-1.0.0-linux-x86_64.AppImage
  ./OCRizer-1.0.0-linux-x86_64.AppImage
  ./OCRizer-1.0.0-linux-x86_64.AppImage --cli --check
  ./OCRizer-1.0.0-linux-x86_64.AppImage --cli input.pdf
  ```

  Use the version you downloaded in these commands. The AppImage targets systems compatible
  with Ubuntu 22.04's glibc 2.35 baseline and needs the usual desktop graphics libraries.
  If FUSE is unavailable, prefix the command with `APPIMAGE_EXTRACT_AND_RUN=1`.

For Python installations, install Tesseract first, then install the package as described below.

### 1. Install Tesseract with the Polish and English language packs

Tesseract is the only external program OCRizer needs. (Ghostscript is **not** required: page
rasterisation uses pypdfium2 and the output is a regular PDF, not PDF/A.)

| Platform | Command |
|---|---|
| **Windows** | `winget install UB-Mannheim.TesseractOCR` – then add Polish: re-run the installer and tick **Polish** under *Additional language data*, or download [`pol.traineddata`](https://github.com/tesseract-ocr/tessdata_fast) into the `tessdata` folder next to `tesseract.exe`. |
| **Debian / Ubuntu** | `sudo apt install tesseract-ocr tesseract-ocr-pol tesseract-ocr-eng` |
| **Fedora** | `sudo dnf install tesseract tesseract-langpack-pol tesseract-langpack-eng` |
| **Arch** | `sudo pacman -S tesseract tesseract-data-pol tesseract-data-eng` |
| **macOS** | `brew install tesseract tesseract-lang` |

Automatic language detection needs `pol` and `eng`; an explicit `--language eng` or
`--language pol` needs only its selected model. `osd` enables optional automatic page rotation.
For the highest accuracy you can use the larger *tessdata_best* models instead of the default
*tessdata_fast* ones (slower).

If `tesseract` is not on your `PATH`, OCRizer also looks in the default install folders and
accepts `--tesseract PATH`, the `OCRIZER_TESSERACT` environment variable or `tesseract_path`
in the config file.

### 2. Install OCRizer

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
python -m pip install ocrizer          # CLI and Python library
python -m pip install "ocrizer[gui]"   # also install the desktop GUI
```

The PyPI commands apply once the package has been published. To install this source checkout:

```bash
python -m pip install .
python -m pip install ".[gui]"
python -m pip install -e ".[gui,dev]"   # development tools, tests and packaging
```

### 3. Verify the installation

```bash
pdfocr --check
```

prints one line per dependency (Tesseract and its version, `pol`/`eng`/`osd` data, the Python
packages, PySide6) and exits with `0`, or with `3` plus installation instructions when something
required is missing. The GUI and every OCR run perform the same check and show the same
instructions.

---

## Using the GUI

```bash
pdfocr-gui            # or: pdfocr --gui      or: python -m ocrizer --gui
```

* **Add PDFs…** or drag and drop files **or folders** onto the window (folders are scanned
  recursively). Non-PDF items are ignored, duplicates are not queued twice.
* The list shows each document with its own status: *Queued → Processing (stage/page) → Done /
  Skipped / Failed / Cancelled* and details (output name, language used, error).
* **Remove selected** / **Clear all** manage the queue.
* **Language:** *Auto detect* (default), *Polish*, *English*, *Polish + English* or
  *English + Polish*. With both models selected, the first language is primary.
* **Output folder:** empty means "next to each original". Results are always new files
  (`name_ocr.pdf`); an existing file is never overwritten, a numbered name is used instead.
* **Also save the recognised text as a .txt file** exports the text next to the PDF.
* **Start processing** / **Cancel**: processing runs in background worker processes, so the
  window stays responsive. Cancelling stops running documents immediately (including their
  Tesseract processes) and removes all temporary files.
  Closing during processing offers cancellation and closes after workers have stopped.
* The progress bar shows finished documents; errors collect in the box at the bottom.
  Pressing Start again only retries what is not done yet, so nothing is processed twice.

Language, output folder and the text-export choice are remembered between sessions (GUI-only
settings; they never change CLI behaviour).

---

## Using the command line

```bash
pdfocr input.pdf                                  # -> input_ocr.pdf next to the original
pdfocr input.pdf --output output.pdf              # explicit output name
pdfocr input.pdf --language auto                  # default: detect Polish / English / both
pdfocr input.pdf --language pol                   # force Polish   (also: eng, pol+eng)
pdfocr a.pdf b.pdf "C:\Docs\zażółć gęślą.pdf"     # several files; spaces/Unicode are fine
pdfocr ./documents/ --batch --output-dir ./processed/
pdfocr ./documents/ --batch --recursive           # include sub-folders
pdfocr ./documents/ --batch --workers 4           # four documents in parallel
pdfocr "scans/*.pdf"                              # wildcards work on Windows too
pdfocr --check                                    # verify dependencies
pdfocr --help
pdfocr --version
```

| Option | Meaning |
|---|---|
| `-o, --output FILE` | Output file (exactly one input file). Fails if it exists, unless `--overwrite`. |
| `--output-dir DIR` | Write results here. With `--recursive` the sub-folder structure is mirrored. |
| `-l, --language` | `auto` (default), `pol`, `eng`, `pol+eng` (also `pl`, `en`, `polish`, `english`). |
| `-b, --batch` | Process the PDFs inside the given directories. Directories without `--batch` are rejected so that a whole folder is never processed by accident. |
| `-r, --recursive` | Include sub-directories (implies `--batch`). |
| `-w, --workers N` | Documents processed in parallel (default: half the CPU cores, max 4). |
| `--existing-text skip\|redo\|force` | See [Existing text](#existing-text-layers). Default `skip`. |
| `--overwrite` / `--skip-existing` | Replace an existing output file / skip documents whose output exists. Default: pick a new name. |
| `--in-place` | Replace the **original** with the OCR'd file. Must be requested explicitly. |
| `--suffix TEXT` | Output suffix (default `_ocr`). |
| `--export-text` | Also write the recognised text to `name_ocr.txt`. |
| `--no-ocr-images` | Do not read text inside images on pages that already have text. |
| `--deskew`, `--no-rotate`, `--optimize 0-3` | See [Preprocessing](#preprocessing-and-the-integrity-trade-off). |
| `-q, --quiet` / `-v, -vv` | Only errors / more detail (`-v`: language decisions, `-vv`: debug). |
| `--log-file FILE` | Structured JSON-lines log. |
| `--tesseract PATH` | Tesseract executable or folder. |

Progress is shown as a live line on a terminal and as one result line per document otherwise:

```
[1/3] OK       docs\a.pdf -> processed\a_ocr.pdf  (language pol [auto], 12 pages, 8.4s)
[2/3] SKIPPED  docs\b.pdf: Already searchable: all 4 page(s) contain text.
[3/3] FAILED   docs\c.pdf: The PDF is encrypted or password-protected.
               hint: Remove the password first (e.g. `qpdf --decrypt ...`).
Done: 1 succeeded, 1 failed, 1 skipped, 0 cancelled.
```

**Exit codes:** `0` all documents succeeded or were skipped · `1` at least one document failed ·
`2` invalid arguments/inputs (nothing processed) · `3` missing Tesseract / language pack ·
`130` interrupted with Ctrl+C (running documents are cancelled and cleaned up).

**Configuration** (optional): `config.json` in the per-user config folder
(`%LOCALAPPDATA%\ocrizer` on Windows, `~/.config/ocrizer` on Linux, location override:
`OCRIZER_CONFIG`) with `language`, `workers`, `suffix`, `tesseract_path`. Environment overrides:
`OCRIZER_LANGUAGE`, `OCRIZER_WORKERS`, `OCRIZER_TESSERACT`. Command-line options win over all of
them.

---

## Using the Python library

The base package exposes the same planner and batch runner used by both interfaces:

```python
import multiprocessing
from pathlib import Path

from ocrizer.batch import BatchRunner
from ocrizer.config import OcrSettings, OutputSettings
from ocrizer.errors import OcrizerError
from ocrizer.planner import plan_jobs


def main():
    settings = OcrSettings(language="pol+eng", export_text=True)
    try:
        plan = plan_jobs(
            [Path("input.pdf")], OutputSettings(output_dir=Path("results")),
            export_text=settings.export_text,
        )
        summary = BatchRunner(plan.jobs, settings, workers=2).run()
    except OcrizerError as exc:
        raise SystemExit(f"{exc.message} {exc.hint or ''}".strip()) from exc
    for result in summary.results:
        print(result.status.value, result.job.input_path, result.output_path or result.message)
    raise SystemExit(1 if summary.failed else 0)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
```

Save this as a script and keep the `if __name__ == "__main__"` guard: workers use
multiprocessing's spawn mode. `BatchRunner.run()` blocks until the batch finishes.
`summary.results` contains each document's status, output paths, message and error code;
one failed document does not stop the rest. Missing Tesseract or language models raise
`MissingDependencyError` (a subclass of `OcrizerError`) before OCR starts. Planning and
configuration errors also raise `OcrizerError` subclasses. Automatic language detection
requires both `pol` and `eng` models, including when using the library.

For progress, pass `on_event=callback` to `BatchRunner`; call `runner.cancel()` from another
thread to stop work. If you explicitly select overwrite or in-place output planning, also
pass `overwrite=True` to the runner. The example uses the default safe, numbered output names.
See the [Python API guide](docs/python-api.md) for callbacks, cancellation and error handling.

---

## How it works

```
 GUI (PySide6) ─┐                                   ┌─ validate (pikepdf) ─ text-layer scan (pypdfium2)
                ├─► planner ─► BatchRunner ─► worker process per document
 CLI (argparse)─┘  (paths)    (queue, cancel,       ├─ language detection (Tesseract probe + py3langid)
                               commit, cleanup)      └─ OCRmyPDF/Tesseract ─► integrity check ─► text export
```

| Module | Responsibility |
|---|---|
| `ocrizer.cli`, `ocrizer.gui.*` | Presentation only. No OCR logic; both call `plan_jobs()` + `BatchRunner`. |
| `ocrizer.engine` | The OCR engine for **one** document. |
| `ocrizer.language` | Automatic Polish/English detection. |
| `ocrizer.batch`, `ocrizer.worker` | Job management: process isolation, parallelism, cancellation, atomic commit, cleanup. |
| `ocrizer.planner` | Input discovery and collision-free, overwrite-safe output paths. |
| `ocrizer.pdfinfo` | Validation (corrupt / encrypted / empty) and per-page text detection. |
| `ocrizer.deps` | Locating Tesseract, checking language packs, install instructions. |
| `ocrizer.config`, `ocrizer.logging_setup`, `ocrizer.errors` | Settings, structured logging, error taxonomy. |

Each document is processed in its **own worker process**. That gives hard guarantees: cancelling
kills the whole process tree (worker, OCRmyPDF pool, Tesseract), a crash or out-of-memory in one
document fails only that document, and memory is returned to the OS after each file. The worker
writes to a hidden temporary file in the output folder and the runner moves it into place
atomically; scratch directories and partial files are removed after success, failure, crash and
cancellation alike.

### Existing text layers

Every page is checked for extractable text (pypdfium2) before any OCR:

* `--existing-text skip` (default): a document in which **every page already has text is left
  alone** and reported as *skipped* (no output file, no wasted CPU). In a mixed document only the
  pages without text are OCR'd. A page with a text layer **and** a sizeable raster image (at least
  3 % of the page, 0.5 in per side) but no OCR layer yet is not "fully searchable": the text
  inside the image (a chart, screenshot or scanned table) is read and added as an invisible layer
  while typeset text and vector graphics stay as they are. Pages that already carry an invisible
  OCR layer, such as this tool's own results, are not processed again. `--no-ocr-images` turns
  this off. Limits: the page's structure tags (Tagged PDF) are dropped on pages that are
  re-OCR'd, and photographs without text may add a few stray characters.
* `redo`: replace a previous OCR layer and OCR images (visible text is kept). Cannot be
  combined with `--deskew`.
* `force`: rasterise **every** page and OCR it. This is the only mode that discards vector text
  and fonts; use it only for documents whose text layer is garbage.

Files named like earlier results (`*_ocr.pdf`, `*_ocr_1.pdf`) are ignored during directory scans,
so re-running a batch over the same folder does not OCR its own output.

### Automatic language detection

Documented in detail in [`ocrizer/language.py`](src/ocrizer/language.py). In short:

1. Up to 4 pages that need OCR are sampled evenly across the document and read with Tesseract's
   combined `pol+eng` model (200 DPI).
2. The recognised paragraphs are merged into chunks of ~60+ letters; each chunk is classified
   Polish/English by [py3langid](https://github.com/adbar/py3langid) (offline, deterministic,
   restricted to the two candidates) and weighted by length. This yields the shares of Polish,
   English and *uncertain* text, which is what makes mixed documents and mixed pages visible.
3. Decision: mostly Polish → `pol`; mostly English → `eng` (unless ≥1 % Polish diacritics, which
   the English model cannot produce); a second language ≥5 % → `pol+eng`.
4. **Fallback to `pol+eng`** when there is too little text (<20 words / <80 letters), Tesseract's
   mean word confidence is below 50 %, or more than 35 % of the text is classified with low
   confidence. `pol+eng` is the superset, so every uncertain case degrades to the safe choice.

What was verified (automated tests, `tests/test_language.py` and `tests/test_ocr_integration.py`):
16/16 hand-written Polish and English business sentences classified correctly; real OCR of
rendered scans: Polish page → `pol`, English page → `eng`, Polish+English pages and pages mixing
both languages → `pol+eng`, an unreadable noise scan → fallback; OCR text of Polish scans contains
`ą ć ę ł ń ó ś ź ż` correctly.

Known limits, stated plainly: only the sampled pages are inspected, so a single page in the other
language inside a long single-language document can be missed (use `--language pol+eng` if that
matters); the test corpus is small and synthetic (clean rendered scans), so accuracy on
real-world, low-quality scans is not claimed – that is exactly what the fallback is for; only
Polish and English are supported.

### Preprocessing and the integrity trade-off

Original appearance wins over marginal recognition gains:

* The output is a regular PDF (`--output-type pdf`, no PDF/A conversion). OCRmyPDF preserves
  page order; page count and dimensions at each page position are verified after every run
  (a mismatch fails the job). Originals are never modified (except with `--in-place`).
* **Auto-rotation is on** (`--no-rotate` to disable): upside-down or sideways scans cannot be
  recognised otherwise. It only sets the page's `/Rotate` flag and only when Tesseract's
  orientation detection is confident; very sparse pages may not reach that confidence.
* **Deskew is off by default** (`--deskew` to enable) because it rewrites the page image. Enable it
  for noticeably crooked scans. It cannot be combined with `--existing-text redo` or with
  image-text recognition beside visible text in `skip` mode. For those documents, disable
  deskew, use `--no-ocr-images`, or explicitly select `--existing-text force`.
* Lossless output optimisation is on (`--optimize 1`); `2`/`3` are lossy.
* Pages above 64 megapixels are downsampled **for OCR only** (the visible page is untouched) to
  bound memory use.

### Resource use

`--workers` documents run in parallel (default: half the cores, at most 4). Each document gets
`cores / concurrent documents` OCR threads (at most 8) so the machine is not oversubscribed.
Pages are processed individually; memory and runtime depend on image sizes, selected models,
preprocessing and the number of simultaneous documents.

### Security and privacy

* Document processing uses no network access; installation and build recipes download their
  dependencies, while the installed app works offline.
* External programs are started with argument lists, never through a shell; no executable path is
  hard-coded.
* Document content is never logged. Logs contain event names, file names, page counts, language
  codes and error codes only (`--log-file` writes them as JSON lines).
* Input files are validated before processing (header, structure, encryption, page count).
  Encrypted, corrupt, empty and digitally signed PDFs are rejected with an explanation.

---

## Packaging a standalone desktop application

Build each desktop package on its target operating system. These recipes bundle Tesseract,
its runtime libraries, and the `eng`, `pol` and `osd` models, then smoke-test real parallel OCR
and GUI startup.

On Windows, install a complete Tesseract distribution, then run:

```powershell
python -m pip install -e ".[gui,build]"
./packaging/build_windows.ps1
# Custom Tesseract installation:
./packaging/build_windows.ps1 -TesseractDirectory 'D:\Tools\Tesseract-OCR'
```

This creates `dist/OCRizer-VERSION-windows-x86_64.zip`, containing both `.exe` files and their
support folders. It is a portable application folder; extract the whole archive to use it.

On x86_64 Ubuntu 22.04, install the system build requirements listed in
[the release guide](docs/releasing.md#build-the-linux-appimage-locally), then run:

```bash
python -m pip install -e ".[gui,build]"
bash packaging/build_appimage.sh
```

This creates `dist/OCRizer-VERSION-linux-x86_64.AppImage`. PyInstaller does not cross-compile;
the Linux recipe must run on Linux. The lower-level
`pyinstaller packaging/ocrizer.spec --noconfirm` command builds just the application folder
without the additional Tesseract bundle or release archive.

For PyPI artifacts:

```bash
python -m pip install ".[build]"
python -m build
python -m twine check --strict dist/*.whl dist/*.tar.gz
```

GitHub Actions provides three workflows:

* [Tests](.github/workflows/ci.yml) runs lint/type checks and tests on Windows and Linux.
* [Build distributions](.github/workflows/build.yml) builds the Windows zip, Linux AppImage,
  Python wheel and source archive, and uploads them as workflow artifacts. Run it manually
  from Actions to prepare downloads without publishing.
* [Release](.github/workflows/release.yml) runs for `v*` tags, checks that the tag matches
  `ocrizer.__version__`, runs tests/builds, attaches artifacts and SHA-256 checksums to a
  GitHub release, and publishes the Python artifacts to PyPI through Trusted Publishing.

Before publishing, complete the repository and PyPI publisher setup in
[Building and releasing OCRizer](docs/releasing.md). A local build does not publish anything.

---

## Development and tests

```bash
pip install -e ".[gui,dev]"
pytest                    # everything; real-OCR tests are skipped if Tesseract/pol/eng are missing
pytest -m "not integration"   # fast tests that need no Tesseract
ruff check src tests ; mypy src
```

The suite builds its test PDFs on the fly (scanned pages are text rendered with a
TrueType font into an image-only PDF; set `OCRIZER_TEST_FONT` if no suitable font is found) and
covers: single and batch OCR, Polish / English / mixed-language documents and pages, language
detection (unit-level decisions and real scans), searchable PDFs and image-only PDFs, mixed
documents, corrupt/encrypted/empty/zero-page files, worker crashes and failures, cancellation
(including killing grandchild processes and checking Tesseract is gone), output-name conflicts,
atomic commit races, CLI argument validation and exit codes, Unicode file names and paths with
spaces, ASCII-only consoles, and the GUI (headless, with a real end-to-end run and cancel).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Tesseract: executable not found` | Install Tesseract (see above) or pass `--tesseract C:\path\to\tesseract.exe` / set `OCRIZER_TESSERACT`. After installing, open a new terminal so `PATH` updates. |
| `language pack 'pol' not installed` | Install the Polish data (see table above); `tesseract --list-langs` must list `pol` and `eng`. |
| `automatic page rotation will be disabled` | `osd.traineddata` is missing; only rotation is affected. |
| Directory argument rejected | Add `--batch` (and `--recursive` for sub-folders). |
| File reported *Skipped – already searchable* | It has a text layer. Use `--existing-text redo` or `force` if that layer is wrong. |
| *Failed – encrypted* | Remove the password first, e.g. `qpdf --decrypt --password=… in.pdf out.pdf`. |
| *Failed – digitally signed* | OCR would invalidate the signature; work on an unsigned copy. |
| Worker *crashed (exit code …)* | Usually out of memory on very large pages; lower `--workers`. |
| Output has a `_1` suffix | The target name already existed; nothing was overwritten. Use `--overwrite` to replace it. |
| Upside-down page not rotated | Install `osd.traineddata`; orientation detection also needs enough readable text. Rotate the source manually when detection is uncertain. |
| Deskew rejected | `redo` cannot be deskewed; image-text OCR beside visible text in `skip` mode also requires deskew off. Use `--no-ocr-images` or explicitly select `--existing-text force` if appropriate. |
| Cannot safely create output on a Linux filesystem | Atomic non-overwriting saves need hard-link support. Choose another output filesystem, or explicitly permit replacement with `--overwrite`. |
| AppImage fails because FUSE is unavailable | Run `APPIMAGE_EXTRACT_AND_RUN=1 ./OCRizer-VERSION-linux-x86_64.AppImage`. |
| Garbled characters in console | Use a UTF-8 terminal (Windows Terminal); OCRizer never crashes on unprintable names but may show `?`. |
| Stray files `.ocrizer-*.partial.pdf` | Only left behind if the whole machine/process was killed abruptly; safe to delete. |

---

## Platform notes

* CI is configured to test Windows and Linux with Python 3.11, 3.13 and 3.14, including real
  OCR and headless GUI tests. Desktop release artifacts target x86_64 Windows and Linux;
  Linux builds use Ubuntu 22.04 as their compatibility baseline.
* On macOS Apple-silicon, install Tesseract via Homebrew; its tool locations are auto-detected.
  macOS is not included in the CI matrix or desktop build recipes.
* Hard links provide atomic saves that refuse existing files. On Windows, filesystems without
  hard links use Windows' non-overwriting rename. On Linux/POSIX, a filesystem without hard-link
  support produces an actionable error instead of risking replacement; choose a different
  output filesystem or explicitly request `--overwrite`.
* A document's own metadata, bookmarks and links are retained by OCRmyPDF; digitally signed
  files are refused.
