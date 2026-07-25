"""Sandbox worker: executes an LLM-generated CadQuery script and returns artifacts.

Invoked as `python -m app.cad.worker` by app/cad/runner.py, one process per run,
with a scrubbed environment (no gateway token), CPU/mem rlimits and a wall-clock
deadline. Network egress, non-root and read-only rootfs are enforced by the
container. This process is UNTRUSTED-code-facing: it execs the model's script.

Protocol: reads {"script": "..."} as JSON on stdin; writes a single JSON object
on stdout: {"ok", "preview_png_b64"?, "exports": {"step": b64, "stl": b64}, "error"?}.
Exports travel inline as base64 — the browser client is the source of truth and
there is no server-side storage in the MVP.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import traceback


def _b64_file(path: str) -> str:
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def run(script: str) -> dict:
    try:
        import cadquery as cq
        from cadquery import exporters
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"cadquery unavailable: {exc}"}

    namespace: dict = {"cq": cq, "cadquery": cq}
    try:
        exec(script, namespace)  # noqa: S102 - untrusted; the container is the sandbox
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "script error:\n" + traceback.format_exc(limit=4)}

    result = namespace.get("result")
    if result is None:
        return {"ok": False, "error": "script did not assign a `result` object"}

    workdir = tempfile.mkdtemp()
    step_path = os.path.join(workdir, "model.step")
    stl_path = os.path.join(workdir, "model.stl")
    try:
        exporters.export(result, step_path)
        exporters.export(result, stl_path)
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "export failed:\n" + traceback.format_exc(limit=4)}

    exports = {"step": _b64_file(step_path), "stl": _b64_file(stl_path)}

    preview_png_b64 = None
    try:
        from app.cad.preview import render_png

        preview_png_b64 = base64.b64encode(render_png(stl_path)).decode("ascii")
    except Exception:  # noqa: BLE001 - preview is best-effort
        preview_png_b64 = None

    return {"ok": True, "preview_png_b64": preview_png_b64, "exports": exports}


def main() -> None:
    try:
        request = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        json.dump({"ok": False, "error": f"invalid request: {exc}"}, sys.stdout)
        return
    json.dump(run(request.get("script", "")), sys.stdout)


if __name__ == "__main__":
    main()
