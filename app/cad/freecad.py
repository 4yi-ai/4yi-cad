"""Headless FreeCAD execution entrypoint for P2.0 smoke."""

from __future__ import annotations

import sys

from app.cad.runner import SandboxResult, run_sandboxed

FREECAD_WORKER_ARGV = [sys.executable, "-m", "app.cad.freecad_worker"]

MINIMAL_FREECAD_SMOKE_SCRIPT = """
import FreeCAD
import Part

doc = FreeCAD.newDocument("Smoke")
result = doc.addObject("Part::Box", "SmokeBox")
result.Length = 10
result.Width = 8
result.Height = 6
doc.recompute()
"""


def run_freecad_sandboxed(
    script: str,
    *,
    timeout_s: float = 180,
    cpu_seconds: int | None = 120,
    address_space_mb: int | None = 4096,
) -> SandboxResult:
    return run_sandboxed(
        {"script": script},
        timeout_s=timeout_s,
        cpu_seconds=cpu_seconds,
        address_space_mb=address_space_mb,
        worker_argv=FREECAD_WORKER_ARGV,
    )
