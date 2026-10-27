# Building and releasing OCRizer

The release pipeline builds Windows x86_64 executables, a Linux x86_64 AppImage,
and a platform-independent Python wheel plus source distribution. Desktop packages
include Tesseract and the `eng`, `pol`, and `osd` language models. The Python package
uses a separately installed Tesseract, so `pip install` remains suitable for headless
systems. Nothing is published by running a local build or manually running the
**Build distributions** workflow.

## First release setup

1. Put this directory in a GitHub repository and enable Actions. This source copy
   does not include Git history or a configured remote.
2. OCRizer is MIT-licensed; the root `LICENSE` and package metadata are included
   in source, wheel and desktop distributions. Add author/contact details and real
   repository/documentation URLs if appropriate; repository ownership was not
   supplied with the original source.
3. Confirm that you own the PyPI project name `ocrizer`, or reserve it as a new
   project. If the name is unavailable, change `project.name`, the installation
   hints, and the PyPI URLs before publishing. The import package can stay `ocrizer`.
4. Configure a PyPI **Trusted Publisher** for your GitHub owner/repository, workflow
   filename `release.yml`, and GitHub environment `pypi`. For a new project use
   PyPI's pending publisher setup; for an existing project use its Publishing page.
   Create the matching GitHub `pypi` environment. No long-lived PyPI API token is
   needed. See [PyPI's setup guide](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
   and [publishing from GitHub Actions](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).
5. Run the **Tests** and **Build distributions** workflows on the intended release
   commit. Download and try both desktop packages on clean target machines before
   the first public release. Linux support starts at a compatible glibc 2.35 system
   (the build runner is Ubuntu 22.04); desktop integration also needs the normal
   system display/graphics libraries. macOS desktop packaging and ARM builds are
   not provided by these recipes.

## Release a version

Set `__version__` in `src/ocrizer/__init__.py`; package metadata reads this value
automatically. Commit all source changes, then create and push the matching tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The **Release** workflow rejects mismatched tags, runs checks/tests on Windows
and Linux with Python 3.11, 3.13, and 3.14, and builds the three distribution types.
Only after every build succeeds does it create a GitHub release containing:

* `OCRizer-VERSION-windows-x86_64.zip`
* `OCRizer-VERSION-linux-x86_64.AppImage`
* `ocrizer-VERSION-py3-none-any.whl`
* `ocrizer-VERSION.tar.gz`
* `SHA256SUMS.txt`

The separate `pypi` job then publishes the already built Python artifacts using
Trusted Publishing. If publisher setup fails, fix the configuration and re-run
the failed job. PyPI does not allow replacing a published version; source changes
after publication require a new version/tag.

## Build Python distributions locally

```bash
python -m pip install ".[build]"
python -m build
python -m twine check --strict dist/*.whl dist/*.tar.gz
```

`python -m build` first creates the source distribution and builds the wheel from
it, checking that the source archive contains what is needed. Both CLI entry
points and the `py.typed` marker are shipped. Install `ocrizer[gui]` for the GUI;
the base package omits Qt. The source archive also includes tests, release
scripts, documentation, and workflows.

## Build Windows executables locally

Use 64-bit Windows, Python 3.11+, and a complete Tesseract installation:

```powershell
python -m pip install -e ".[gui,build]"
./packaging/build_windows.ps1
# Non-default installation:
./packaging/build_windows.ps1 -TesseractDirectory 'D:\Tools\Tesseract-OCR'
```

The script builds both executables, copies Tesseract and its DLLs/models, retains
dependency license notices, runs real OCR with two spawned workers, checks text
export, checks GUI startup, and creates the versioned zip. Missing `eng`, `pol`,
or `osd` models are downloaded from Tesseract's official `tessdata_fast/4.1.0`
release. Other build/download steps need internet; the resulting app processes
documents offline. Use `-Python .venv\Scripts\python.exe` to select a virtualenv.

Extract the **whole zip**, then run `OCRizer/OCRizer.exe` for the GUI or
`OCRizer/pdfocr.exe` for the CLI. Keep `_internal` and `Tesseract-OCR` beside the
executables; copying only an `.exe` will not work. The executables are unsigned;
code signing requires the publisher's own certificate and is not configured here.

The raw `pyinstaller packaging/ocrizer.spec --noconfirm` command remains available,
but it produces an application folder without the separate Tesseract bundle,
license inventory, archive, or distribution smoke checks.

## Build the Linux AppImage locally

Build on x86_64 Ubuntu 22.04 for the same baseline as CI:

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-pol \
  tesseract-ocr-osd fonts-dejavu-core libegl1 libopengl0 libxkbcommon-x11-0 \
  libxcb-cursor0 desktop-file-utils
python -m pip install -e ".[gui,build]"
bash packaging/build_appimage.sh
```

The recipe freezes the app, builds an AppDir with Tesseract, native dependencies,
models and desktop resources, and invokes the official pinned `appimagetool`
release 1.9.1. Set `PYTHON` to choose an interpreter and `APPIMAGETOOL` to use a
pre-downloaded tool. See the [AppImage packaging guide](https://docs.appimage.org/packaging-guide/introduction.html).

```bash
chmod +x OCRizer-1.0.0-linux-x86_64.AppImage
./OCRizer-1.0.0-linux-x86_64.AppImage
./OCRizer-1.0.0-linux-x86_64.AppImage --cli --check
./OCRizer-1.0.0-linux-x86_64.AppImage --cli input.pdf
```

On systems without FUSE, set `APPIMAGE_EXTRACT_AND_RUN=1` before the command. CI
uses this mode to smoke-test the actual AppImage, including real parallel OCR and
GUI startup. The Linux artifact must be built/tested on Linux; PyInstaller does
not cross-compile from Windows.

## Redistribution notices

Desktop recipes copy installed Python license metadata into `licenses/python`
and record package names/versions. Windows retains the Tesseract installation's
notices and adds the model license. The Linux recipe copies Debian copyright
notices for bundled native libraries and models. These inventories support a
publisher's license review; the publisher must also meet applicable source and
notice requirements, including those of Qt/PySide6 (LGPL) and OCRmyPDF (MPL).
