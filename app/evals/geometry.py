"""L2 generic geometry checks on eval run artifacts.

Engine-agnostic: operates on exported files (STEP header, STL watertightness via
trimesh, FCStd loadability via a FreeCADCmd subprocess). Imported only by
scripts/tests — trimesh is a dev-only dependency.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

FCSTD_LOAD_TIMEOUT_S = 120
_MIN_VOLUME_MM3 = 1e-9


@dataclass
class GeometryReport:
    step_valid: bool | None = None
    stl_watertight: bool | None = None
    stl_volume_mm3: float | None = None
    fcstd_loadable: bool | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool | None:
        checks = [c for c in (self.step_valid, self.stl_watertight, self.fcstd_loadable) if c is not None]
        if not checks:
            return None
        if self.stl_volume_mm3 is not None and self.stl_volume_mm3 <= _MIN_VOLUME_MM3:
            return False
        return all(checks)


def _check_step(path: Path, report: GeometryReport) -> None:
    head = path.read_bytes()[:64].lstrip()
    report.step_valid = head.startswith(b"ISO-10303-21")
    if not report.step_valid:
        report.issues.append(f"{path.name}: missing ISO-10303-21 STEP header")


def _check_stl(path: Path, report: GeometryReport) -> None:
    import trimesh

    try:
        mesh = trimesh.load(str(path), force="mesh")
    except Exception as exc:  # noqa: BLE001 - any load failure is a finding
        report.stl_watertight = False
        report.issues.append(f"{path.name}: unloadable mesh: {exc}")
        return
    report.stl_watertight = bool(mesh.is_watertight)
    report.stl_volume_mm3 = abs(float(mesh.volume)) if mesh.is_watertight else None
    if not mesh.is_watertight:
        report.issues.append(f"{path.name}: mesh is not watertight")
    elif report.stl_volume_mm3 is not None and report.stl_volume_mm3 <= _MIN_VOLUME_MM3:
        report.issues.append(f"{path.name}: ~zero enclosed volume")


def _check_fcstd(path: Path, report: GeometryReport) -> None:
    freecadcmd = shutil.which("FreeCADCmd") or shutil.which("freecadcmd")
    if not freecadcmd:
        report.issues.append("FreeCADCmd not available; fcstd load check skipped")
        return
    script = (
        "import sys, FreeCAD\n"
        f"doc = FreeCAD.open({str(path)!r})\n"
        "sys.exit(0 if doc is not None else 1)\n"
    )
    try:
        proc = subprocess.run(
            [freecadcmd, "-c", script],
            capture_output=True,
            timeout=FCSTD_LOAD_TIMEOUT_S,
        )
        report.fcstd_loadable = proc.returncode == 0
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")[-500:]
            report.issues.append(f"{path.name}: FreeCAD.open failed: {tail}")
    except subprocess.TimeoutExpired:
        report.fcstd_loadable = False
        report.issues.append(f"{path.name}: FreeCAD.open timed out after {FCSTD_LOAD_TIMEOUT_S}s")
    except OSError as exc:
        report.fcstd_loadable = False
        report.issues.append(f"{path.name}: FreeCADCmd failed to run: {exc}")


def check_artifacts(artifacts_dir: Path, *, fcstd_check: bool = True) -> GeometryReport:
    report = GeometryReport()
    step = artifacts_dir / "model.step"
    stl = artifacts_dir / "model.stl"
    fcstd = artifacts_dir / "model.fcstd"
    if step.exists():
        _check_step(step, report)
    if stl.exists():
        _check_stl(stl, report)
    if fcstd.exists() and fcstd_check:
        _check_fcstd(fcstd, report)
    if report.step_valid is None and report.stl_watertight is None and report.fcstd_loadable is None:
        if not report.issues:
            report.issues.append("no checkable artifacts found")
    return report
