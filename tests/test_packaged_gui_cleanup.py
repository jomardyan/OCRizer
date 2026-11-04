from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "posix", reason="AppImage process group contract is POSIX-only")
def test_smoke_cleanup_stops_wrapper_and_gui_which_inherit_pipes():
    spec = importlib.util.spec_from_file_location(
        "desktop_smoke", Path(__file__).resolve().parents[1] / "packaging/smoke_test.py"
    )
    assert spec is not None and spec.loader is not None
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print('ready',flush=True); time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == b"ready"
        smoke.stop_gui(process)
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            smoke.stop_gui(process)
