"""FreeCAD document state diff and diagnostics helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def typed_state_diff(before_summary: dict[str, Any] | None, after_summary: dict[str, Any] | None) -> dict[str, Any]:
    before = _typed_state(before_summary)
    after = _typed_state(after_summary)
    diff = {
        "schema": "freecad.typed_state.diff.v1",
        "objects": _mapping_diff(before.get("objects"), after.get("objects")),
        "feature_tree": _mapping_diff(
            (before.get("feature_tree") or {}).get("nodes"),
            (after.get("feature_tree") or {}).get("nodes"),
        ),
        "sketches": _mapping_diff(before.get("sketches"), after.get("sketches")),
        "assemblies": _mapping_diff(before.get("assemblies"), after.get("assemblies")),
        "techdraw_pages": _mapping_diff(
            ((before.get("techdraw") or {}).get("pages")),
            ((after.get("techdraw") or {}).get("pages")),
        ),
        "geometry_delta": _geometry_delta(
            (before_summary or {}).get("geometry"),
            (after_summary or {}).get("geometry"),
        ),
    }
    diff["changed"] = any(
        diff[key]["added"] or diff[key]["removed"] or diff[key]["changed"]
        for key in ["objects", "feature_tree", "sketches", "assemblies", "techdraw_pages"]
    ) or bool(diff["geometry_delta"])
    return diff


def storage_status(db_path: str, artifact_root: str) -> dict[str, Any]:
    return {
        "session_db": _path_status(db_path, expected_file=True),
        "artifact_root": _path_status(artifact_root, expected_file=False),
    }


def _typed_state(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not summary:
        return {}
    typed = summary.get("typed_state")
    return typed if isinstance(typed, dict) else {}


def _mapping_diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    before = before or {}
    after = after or {}
    before_keys = set(before)
    after_keys = set(after)
    shared = before_keys & after_keys
    return {
        "added": sorted(after_keys - before_keys),
        "removed": sorted(before_keys - after_keys),
        "changed": sorted(key for key in shared if _fingerprint(before[key]) != _fingerprint(after[key])),
    }


def _geometry_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    before = before or {}
    after = after or {}
    delta: dict[str, Any] = {}
    for key in [
        "object_count",
        "shape_object_count",
        "solid_count",
        "shell_count",
        "face_count",
        "edge_count",
        "vertex_count",
        "volume",
        "invalid_object_count",
        "check_error_count",
    ]:
        old = before.get(key)
        new = after.get(key)
        if _is_number(old) and _is_number(new) and float(old) != float(new):
            delta[key] = {"from": old, "to": new, "delta": float(new) - float(old)}
        elif old != new and (old is not None or new is not None):
            delta[key] = {"from": old, "to": new}
    if before.get("valid") != after.get("valid"):
        delta["valid"] = {"from": before.get("valid"), "to": after.get("valid")}
    if before.get("failure_class") != after.get("failure_class"):
        delta["failure_class"] = {"from": before.get("failure_class"), "to": after.get("failure_class")}
    return delta


def _fingerprint(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _path_status(path: str, *, expected_file: bool) -> dict[str, Any]:
    p = Path(path)
    parent = p.parent if expected_file else p
    under_tmp = _is_under_tmp(p)
    writable = os.access(parent if parent.exists() else parent.parent, os.W_OK)
    return {
        "path": str(p),
        "exists": p.exists(),
        "writable": bool(writable),
        "durable_configured": not under_tmp,
        "warning": "default tmp storage is not durable" if under_tmp else None,
    }


def _is_under_tmp(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path.absolute()
    value = str(resolved)
    return value.startswith("/tmp/") or value.startswith("/private/tmp/")
