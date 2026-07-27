import sys

import pytest

from app.cad.freecad import MINIMAL_FREECAD_SMOKE_SCRIPT
from app.cad.freecad_worker import FREECAD_RESULT_PREFIX, resolve_freecadcmd, run_freecad_script

PY = sys.executable


def test_run_freecad_script_reports_missing_binary(monkeypatch):
    monkeypatch.delenv("FREECADCMD_BINARY", raising=False)
    monkeypatch.setattr("app.cad.freecad_worker.shutil.which", lambda name: None)
    monkeypatch.setattr("app.cad.freecad_worker.FREECADCMD_MACOS_CANDIDATES", ())

    result = run_freecad_script("result = None")

    assert result["ok"] is False
    assert "FreeCADCmd unavailable" in result["error"]


def test_run_freecad_script_parses_fake_binary_with_wrapper(tmp_path, monkeypatch):
    monkeypatch.setattr("app.cad.freecad_worker.render_preview_isolated", lambda path: None)
    fake = tmp_path / "fake_freecadcmd.py"
    fake.write_text(
        f"""
import json
import os
import pathlib

out = pathlib.Path(os.environ["FOURYI_FREECAD_OUT"])
(out / "model.step").write_text("ISO-10303-21;", encoding="utf-8")
(out / "model.stl").write_text("solid smoke", encoding="utf-8")
print("{FREECAD_RESULT_PREFIX}" + json.dumps({{
    "ok": True,
    "step_path": str(out / "model.step"),
    "stl_path": str(out / "model.stl"),
    "freecad_version": "1.0.0",
}}))
""",
        encoding="utf-8",
    )
    wrapper = tmp_path / "fake_freecadcmd"
    wrapper.write_text(f"#!/bin/sh\nexec {PY} {fake} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)

    result = run_freecad_script(
        "result = None",
        freecadcmd=str(wrapper),
        workdir=str(tmp_path / "work"),
        timeout=5,
    )

    assert result["ok"] is True
    assert result["freecad_version"] == "1.0.0"
    assert set(result["exports"]) == {"step", "stl"}
    assert result["preview_png_b64"] is None


def test_resolve_freecadcmd_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("FREECADCMD_BINARY", "/custom/FreeCADCmd")

    assert resolve_freecadcmd() == "/custom/FreeCADCmd"


@pytest.mark.skipif(
    resolve_freecadcmd() is None,
    reason="FreeCADCmd is not installed locally",
)
def test_local_freecadcmd_smoke_exports_step_and_stl():
    result = run_freecad_script(MINIMAL_FREECAD_SMOKE_SCRIPT, timeout=90)

    assert result["ok"] is True
    assert set(result["exports"]) == {"step", "stl"}
    assert result["freecad_version"]
