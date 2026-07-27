"""Headless FreeCAD worker invoked from the existing sandbox.

Protocol: reads {"script": "..."} on stdin and writes one JSON object on stdout:
{"ok", "preview_png_b64"?, "exports": {"step": b64, "stl": b64}, "error"?}.

The generated FreeCAD Python runs under FreeCADCmd in this worker's scrubbed
environment. P2.0 keeps this in the same container as the FastAPI app; splitting
it into a separate service is a later production hardening step.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.cad.worker import _b64_file, render_preview_isolated

FREECAD_RESULT_PREFIX = "__4YI_FREECAD_RESULT__"
FREECADCMD_CANDIDATES = ("FreeCADCmd", "freecadcmd")


HARNESS = r'''
import json
import os
import traceback

import FreeCAD
import Mesh
import Part

PREFIX = "__4YI_FREECAD_RESULT__"


def emit(payload):
    print(PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def shape_volume(shape):
    try:
        return float(shape.Volume)
    except Exception:
        try:
            return float(shape.Volume())
        except Exception:
            return None


def objects_from_namespace(namespace):
    result = namespace.get("result")
    doc = namespace.get("doc") or FreeCAD.ActiveDocument

    if doc is not None:
        try:
            doc.recompute()
        except Exception:
            pass

    if result is not None:
        if isinstance(result, (list, tuple)):
            objects = list(result)
            if objects:
                return objects
        if hasattr(result, "Shape"):
            return [result]
        if hasattr(result, "exportStep"):
            return [result]

    if doc is not None:
        objects = [obj for obj in getattr(doc, "Objects", []) if hasattr(obj, "Shape")]
        if objects:
            return objects

    return []


try:
    out_dir = os.environ["FOURYI_FREECAD_OUT"]
    user_script = os.environ["FOURYI_FREECAD_SCRIPT"]
    namespace = {
        "FreeCAD": FreeCAD,
        "App": FreeCAD,
        "Part": Part,
        "Mesh": Mesh,
    }
    with open(user_script, "r", encoding="utf-8") as fh:
        exec(compile(fh.read(), user_script, "exec"), namespace)

    objects = objects_from_namespace(namespace)
    if not objects:
        emit({"ok": False, "error": "script did not create a result shape or document object"})
        raise SystemExit(0)

    volume = 0.0
    saw_volume = False
    for obj in objects:
        shape = getattr(obj, "Shape", obj)
        value = shape_volume(shape)
        if value is not None:
            volume += value
            saw_volume = True
    if saw_volume and volume <= 1e-9:
        emit({"ok": False, "error": "resulting FreeCAD model has ~zero volume"})
        raise SystemExit(0)

    step_path = os.path.join(out_dir, "model.step")
    stl_path = os.path.join(out_dir, "model.stl")
    if len(objects) == 1 and hasattr(objects[0], "exportStep"):
        objects[0].exportStep(step_path)
        objects[0].exportStl(stl_path)
    else:
        Part.export(objects, step_path)
        Mesh.export(objects, stl_path)

    emit({
        "ok": True,
        "step_path": step_path,
        "stl_path": stl_path,
        "freecad_version": ".".join(str(part) for part in FreeCAD.Version()[:3]),
    })
except Exception:
    emit({"ok": False, "error": "freecad script error:\n" + traceback.format_exc(limit=4)})
'''


def resolve_freecadcmd() -> str | None:
    configured = os.environ.get("FREECADCMD_BINARY")
    if configured:
        return configured
    for candidate in FREECADCMD_CANDIDATES:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def run_freecad_script(
    script: str,
    *,
    freecadcmd: str | None = None,
    timeout: float = 90.0,
    workdir: str | None = None,
) -> dict:
    binary = freecadcmd or resolve_freecadcmd()
    if not binary:
        return {
            "ok": False,
            "error": "FreeCADCmd unavailable; install FreeCAD or set FREECADCMD_BINARY",
        }

    run_dir = Path(workdir or tempfile.mkdtemp())
    run_dir.mkdir(parents=True, exist_ok=True)
    user_script = run_dir / "user_freecad_script.py"
    harness_script = run_dir / "freecad_harness.py"
    user_script.write_text(script, encoding="utf-8")
    harness_script.write_text(HARNESS, encoding="utf-8")

    env = dict(os.environ)
    env["FOURYI_FREECAD_OUT"] = str(run_dir)
    env["FOURYI_FREECAD_SCRIPT"] = str(user_script)
    try:
        proc = subprocess.run(
            [binary, str(harness_script)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(run_dir),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"FreeCADCmd exceeded {timeout}s wall-clock limit"}
    except OSError as exc:
        return {"ok": False, "error": f"FreeCADCmd failed to start: {exc}"}

    payload = _parse_freecad_result(proc.stdout)
    if payload is None:
        return {
            "ok": False,
            "error": (
                f"FreeCADCmd exited with code {proc.returncode} without a result frame\n"
                f"stdout:\n{_tail(proc.stdout)}\nstderr:\n{_tail(proc.stderr)}"
            ),
        }
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": (
                f"FreeCADCmd exited with code {proc.returncode}\n"
                f"stdout:\n{_tail(proc.stdout)}\nstderr:\n{_tail(proc.stderr)}"
            ),
        }
    if not payload.get("ok"):
        return {"ok": False, "error": payload.get("error") or "FreeCADCmd execution failed"}

    step_path = payload.get("step_path")
    stl_path = payload.get("stl_path")
    if not step_path or not stl_path:
        return {"ok": False, "error": "FreeCADCmd did not report STEP/STL paths"}
    if not Path(step_path).is_file() or not Path(stl_path).is_file():
        return {"ok": False, "error": "FreeCADCmd did not produce STEP/STL exports"}

    exports = {"step": _b64_file(step_path), "stl": _b64_file(stl_path)}
    return {
        "ok": True,
        "preview_png_b64": render_preview_isolated(stl_path),
        "exports": exports,
        "freecad_version": payload.get("freecad_version"),
    }


def _parse_freecad_result(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        if not line.startswith(FREECAD_RESULT_PREFIX):
            continue
        try:
            return json.loads(line[len(FREECAD_RESULT_PREFIX) :])
        except json.JSONDecodeError:
            return None
    return None


def _tail(value: str, limit: int = 4000) -> str:
    value = value or ""
    return value[-limit:]


def main() -> None:
    try:
        request = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        json.dump({"ok": False, "error": f"invalid request: {exc}"}, sys.stdout)
        return
    json.dump(run_freecad_script(request.get("script", "")), sys.stdout)


if __name__ == "__main__":
    main()
