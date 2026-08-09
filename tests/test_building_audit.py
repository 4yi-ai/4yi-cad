from app.cad.building_audit import building_audit_from_summary, building_failure_message
from app.cad.building_spec import default_building_spec


def _prop(name, value):
    return {"name": name, "value": value}


def _summary(*, check_errors=0, include_roof=True):
    spec = default_building_spec().model_dump()
    spec["storeys"]["count"] = 1
    objects = [
        {
            "name": "Building",
            "properties": [
                _prop("FourYiElementType", "building"),
                _prop("BuildingSpecJson", __import__("json").dumps(spec)),
            ],
        },
        {"name": "Storey_01", "properties": [_prop("FourYiElementType", "storey")]},
    ]
    roles = ["slab", "wall", "window", "door", "core", "stair"]
    if include_roof:
        roles.append("roof")
    for role in roles:
        objects.append(
            {
                "name": role.title(),
                "shape": {"valid": True},
                "properties": [
                    _prop("FourYiElementType", role),
                    _prop("Storey", "Storey_01" if role != "roof" else "Roof"),
                ],
            }
        )
    return {
        "objects": objects,
        "geometry": {
            "valid": True,
            "shape_object_count": len(roles),
            "invalid_object_count": 0,
            "check_error_count": check_errors,
        },
    }


def test_building_audit_passes_complete_lod200_summary():
    audit = building_audit_from_summary(_summary())
    assert audit["status"] == "pass"
    assert audit["actual_storeys"] == 1
    assert audit["issues"] == []


def test_building_audit_rejects_occ_errors_and_missing_elements():
    audit = building_audit_from_summary(_summary(check_errors=2, include_roof=False))
    assert audit["status"] == "fail"
    codes = {issue["code"] for issue in audit["issues"]}
    assert {"occ_check_failed", "missing_roof"} <= codes
    assert "zero invalid leaf shapes" in building_failure_message(audit)


def test_building_audit_is_not_applicable_without_contract():
    assert building_audit_from_summary({"objects": [], "geometry": {}}) is None
