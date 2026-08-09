"""Per-run scoring: L1 execution, L2 geometry, L3 site-scene conformance.

L4 (human rubric) is ingested at report level, not per run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.loop import BUILDING_ROLE_GROUPS, SITE_ROLE_GROUPS, scene_role_set
from app.evals.corpus import EvalCase
from app.evals.geometry import GeometryReport, check_artifacts


@dataclass
class RunScore:
    l1_ok: bool
    l2_ok: bool | None
    l3_ok: bool | None
    attempts: int
    retries: int
    duration_s: float
    error: str | None
    details: dict = field(default_factory=dict)


def _load_scene(run_dir: Path) -> dict | None:
    path = run_dir / "artifacts" / "viewer_scene.json"
    if not path.exists():
        return None
    try:
        scene = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return scene if isinstance(scene, dict) else None


def _score_l3(case: EvalCase, run_dir: Path, details: dict) -> bool | None:
    if case.domain not in {"site_layout", "building"}:
        return None
    scene = _load_scene(run_dir)
    if scene is None:
        details["l3_missing_roles"] = list(case.required_roles or case.required_elements)
        details["l3_reason"] = "no viewer_scene artifact"
        return False
    objects = scene.get("objects") if isinstance(scene.get("objects"), list) else []
    roles = scene_role_set(scene)
    required = case.required_roles if case.domain == "site_layout" else case.required_elements
    role_groups = SITE_ROLE_GROUPS if case.domain == "site_layout" else BUILDING_ROLE_GROUPS
    missing = [
        group
        for group in required
        if not (roles & role_groups.get(group, {group}))
    ]
    details["l3_object_count"] = len(objects)
    details["l3_roles_found"] = sorted(roles)
    details["l3_missing_roles"] = missing
    if case.min_objects is not None and len(objects) < case.min_objects:
        details["l3_reason"] = f"sparse scene: {len(objects)} < {case.min_objects}"
        return False
    return not missing


def score_run(
    case: EvalCase,
    events: list[dict],
    run_dir: Path,
    *,
    duration_s: float,
    geometry: GeometryReport | None = None,
) -> RunScore:
    done = next((e for e in reversed(events) if e.get("type") == "done"), {})
    l1_ok = bool(done.get("ok"))
    attempts = sum(1 for e in events if e.get("type") == "script")
    retries = sum(1 for e in events if e.get("type") == "retry")
    error = next(
        (e.get("message") for e in reversed(events) if e.get("type") == "error"), None
    )

    details: dict = {"engine": done.get("engine")}
    l2_ok: bool | None = None
    l3_ok: bool | None = None
    if l1_ok:
        geometry = geometry if geometry is not None else check_artifacts(run_dir / "artifacts")
        l2_ok = geometry.ok
        details["geometry"] = {
            "step_valid": geometry.step_valid,
            "stl_watertight": geometry.stl_watertight,
            "stl_volume_mm3": geometry.stl_volume_mm3,
            "fcstd_loadable": geometry.fcstd_loadable,
            "issues": geometry.issues,
        }
        l3_ok = _score_l3(case, run_dir, details)

    return RunScore(
        l1_ok=l1_ok,
        l2_ok=l2_ok,
        l3_ok=l3_ok,
        attempts=attempts,
        retries=retries,
        duration_s=duration_s,
        error=None if l1_ok else error,
        details=details,
    )
