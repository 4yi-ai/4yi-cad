"""Tests for worker preview isolation.

Preview rendering (VTK) can crash NATIVELY (segfault) in a headless container —
a Python try/except cannot catch that, so if preview ran in-process a crash would
destroy the already-exported STEP/STL. `render_preview_isolated` runs preview in a
child process so any failure/crash/timeout degrades to preview=None while exports
survive. Tested with injected commands — no cadquery/VTK needed.
"""

import sys

from app.cad.worker import render_preview_isolated

PY = sys.executable


def test_returns_base64_from_successful_child():
    argv = [PY, "-c", "import sys; sys.stdout.write('QUJD')"]
    assert render_preview_isolated("/ignored.stl", preview_argv=argv) == "QUJD"


def test_crashing_child_yields_none_not_exception():
    argv = [PY, "-c", "import sys; sys.exit(1)"]
    assert render_preview_isolated("/ignored.stl", preview_argv=argv) is None


def test_timeout_yields_none():
    argv = [PY, "-c", "import time; time.sleep(30)"]
    assert render_preview_isolated("/ignored.stl", preview_argv=argv, timeout=0.3) is None


def test_empty_output_yields_none():
    argv = [PY, "-c", "pass"]
    assert render_preview_isolated("/ignored.stl", preview_argv=argv) is None
