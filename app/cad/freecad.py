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


def run_freecad_import_sandboxed(
    import_format: str,
    data_b64: str,
    *,
    filename: str | None = None,
    timeout_s: float = 180,
    cpu_seconds: int | None = 120,
    address_space_mb: int | None = 4096,
) -> SandboxResult:
    return run_sandboxed(
        {
            "operation": "import_model",
            "format": import_format,
            "data_b64": data_b64,
            "filename": filename,
        },
        timeout_s=timeout_s,
        cpu_seconds=cpu_seconds,
        address_space_mb=address_space_mb,
        worker_argv=FREECAD_WORKER_ARGV,
    )


def run_freecad_document_edit_sandboxed(
    script: str,
    fcstd_b64: str,
    *,
    timeout_s: float = 180,
    cpu_seconds: int | None = 120,
    address_space_mb: int | None = 4096,
) -> SandboxResult:
    return run_sandboxed(
        {
            "operation": "edit_document",
            "script": script,
            "fcstd_b64": fcstd_b64,
        },
        timeout_s=timeout_s,
        cpu_seconds=cpu_seconds,
        address_space_mb=address_space_mb,
        worker_argv=FREECAD_WORKER_ARGV,
    )


def run_freecad_document_inspect_sandboxed(
    fcstd_b64: str,
    *,
    timeout_s: float = 180,
    cpu_seconds: int | None = 120,
    address_space_mb: int | None = 4096,
) -> SandboxResult:
    return run_sandboxed(
        {
            "operation": "inspect_document",
            "fcstd_b64": fcstd_b64,
        },
        timeout_s=timeout_s,
        cpu_seconds=cpu_seconds,
        address_space_mb=address_space_mb,
        worker_argv=FREECAD_WORKER_ARGV,
    )


def run_freecad_document_patch_sandboxed(
    patches: list[dict],
    fcstd_b64: str,
    *,
    dry_run: bool = False,
    timeout_s: float = 180,
    cpu_seconds: int | None = 120,
    address_space_mb: int | None = 4096,
) -> SandboxResult:
    return run_sandboxed(
        {
            "operation": "patch_document",
            "patches": patches,
            "fcstd_b64": fcstd_b64,
            "dry_run": dry_run,
        },
        timeout_s=timeout_s,
        cpu_seconds=cpu_seconds,
        address_space_mb=address_space_mb,
        worker_argv=FREECAD_WORKER_ARGV,
    )
