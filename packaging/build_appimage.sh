#!/usr/bin/env bash
# Build on Ubuntu 22.04 x86_64 (or a compatible environment), after installing .[gui,build].
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
task_python="${PYTHON:-python}"
if [[ "$(uname -m)" != x86_64 ]]; then
    echo 'This recipe currently builds x86_64 AppImages only.' >&2
    exit 1
fi
"$task_python" -m PyInstaller --noconfirm --clean packaging/ocrizer.spec
"$task_python" packaging/assemble_appdir.py
task_version="$("$task_python" -c 'from ocrizer import __version__; print(__version__)')"
task_tool="${APPIMAGETOOL:-$task_root/build/appimagetool-x86_64.AppImage}"
if [[ ! -f "$task_tool" ]]; then
    mkdir -p "$(dirname -- "$task_tool")"
    curl --fail --location --retry 3 \
        'https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage' \
        --output "$task_tool"
fi
chmod +x "$task_tool"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$task_tool" \
    "$task_root/build/OCRizer.AppDir" "$task_root/dist/OCRizer-$task_version-linux-x86_64.AppImage"
chmod +x "$task_root/dist/OCRizer-$task_version-linux-x86_64.AppImage"
# Exercise the actual AppImage launcher without requiring FUSE in CI.
APPIMAGE_EXTRACT_AND_RUN=1 "$task_root/dist/OCRizer-$task_version-linux-x86_64.AppImage" --cli --check
"$task_python" packaging/smoke_test.py --appimage "$task_root/dist/OCRizer-$task_version-linux-x86_64.AppImage"
