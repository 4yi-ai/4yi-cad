"""Quality gate for deterministic single-building FreeCAD deliveries."""

from __future__ import annotations

import json

REQUIRED_LOD200_ELEMENTS = ("slab", "wall", "window", "door", "core", "stair", "roof")


def _property_value(item: dict, name: str):
    for prop in list(item.get("properties") or []):
        if isinstance(prop, dict) and prop.get("name") == name:
            return prop.get("value")
    return None


def _building_spec_from_objects(objects: list[dict]) -> dict | None:
    for item in objects:
        raw = _property_value(item, "BuildingSpecJson")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None


def building_audit_from_summary(document_summary: dict | None) -> dict | None:
    if not isinstance(document_summary, dict):
        return None
    objects = [
        item for item in list(document_summary.get("objects") or []) if isinstance(item, dict)
    ]
    spec = _building_spec_from_objects(objects)
    if not spec or spec.get("schema_version") != "4yi-cad.building/v1":
        return None

    geometry = document_summary.get("geometry") if isinstance(document_summary.get("geometry"), dict) else {}
    element_counts: dict[str, int] = {}
    storeys = []
    unassigned = []
    for item in objects:
        role = _property_value(item, "FourYiElementType")
        if isinstance(role, str) and role:
            element_counts[role] = element_counts.get(role, 0) + 1
        if role == "storey":
            storeys.append(item.get("name"))
        if item.get("shape") and role in REQUIRED_LOD200_ELEMENTS:
            assigned_storey = _property_value(item, "Storey")
            if not assigned_storey:
                unassigned.append(item.get("name"))

    issues = []

    def add_issue(code: str, message: str, **details):
        issues.append({"severity": "error", "code": code, "message": message, **details})

    if geometry.get("valid") is not True:
        add_issue("invalid_geometry", "Building leaf geometry is not valid.")
    invalid_count = int(geometry.get("invalid_object_count") or 0)
    check_errors = int(geometry.get("check_error_count") or 0)
    if invalid_count or check_errors:
        add_issue(
            "occ_check_failed",
            "Building must have zero invalid leaf shapes and zero OCC check errors.",
            invalid_object_count=invalid_count,
            check_error_count=check_errors,
        )
    for role in REQUIRED_LOD200_ELEMENTS:
        if element_counts.get(role, 0) < 1:
            add_issue("missing_" + role, f"Building is missing required {role} elements.")

    expected_storeys = int(((spec.get("storeys") or {}).get("count")) or 0)
    if len(storeys) != expected_storeys:
        add_issue(
            "storey_count_mismatch",
            "Building storey hierarchy does not match BuildingSpec.",
            expected=expected_storeys,
            actual=len(storeys),
        )
    if unassigned:
        add_issue(
            "elements_without_storey",
            "Primary BIM elements must be assigned to a storey.",
            objects=unassigned[:20],
        )

    return {
        "schema": "4yi-cad.building_audit/v1",
        "applicable": True,
        "status": "pass" if not issues else "fail",
        "target_lod": spec.get("target_lod"),
        "typology": spec.get("typology"),
        "expected_storeys": expected_storeys,
        "actual_storeys": len(storeys),
        "element_counts": dict(sorted(element_counts.items())),
        "geometry": {
            "valid": geometry.get("valid"),
            "shape_object_count": geometry.get("shape_object_count"),
            "invalid_object_count": invalid_count,
            "check_error_count": check_errors,
        },
        "issues": issues,
    }


def building_failure_message(audit: dict | None) -> str:
    if not isinstance(audit, dict):
        return "building audit failed: missing building audit"
    issues = [
        f"{item.get('code')}: {item.get('message')}"
        for item in list(audit.get("issues") or [])[:8]
        if isinstance(item, dict)
    ]
    return "building audit status={}; {}".format(
        audit.get("status"),
        " | ".join(issues) if issues else "unknown failure",
    )
