import base64
import json

import pytest

from app.cad.building_spec import default_building_spec
from app.cad.building_templates import building_script, residential_tower_script
from app.cad.building_audit import building_audit_from_summary
from app.cad.freecad_worker import resolve_freecadcmd, run_freecad_document_inspect, run_freecad_script


def test_residential_tower_script_is_deterministic_and_semantic():
    spec = default_building_spec()
    first = residential_tower_script(spec)
    second = residential_tower_script(spec.model_dump())

    assert first == second
    assert 'FreeCAD.newDocument("FourYiResidentialTower")' in first
    assert '"IfcBuildingStorey"' in first
    assert '"IfcWall"' in first
    assert '"IfcWindow"' in first
    assert '"IfcDoor"' in first
    assert '"IfcStair"' in first
    assert "BuildingSpecJson" in first
    assert "result = created" in first


def test_building_script_rejects_unimplemented_typology():
    with pytest.raises(NotImplementedError, match="office_tower"):
        building_script(default_building_spec("office_tower"))


@pytest.mark.skipif(resolve_freecadcmd() is None, reason="FreeCADCmd is not installed locally")
def test_residential_tower_freecad_smoke_has_bim_elements_and_clean_geometry():
    payload = default_building_spec().model_dump()
    payload["storeys"]["count"] = 3
    result = run_freecad_script(residential_tower_script(payload), timeout=180)

    assert result["ok"] is True, result.get("error")
    assert {"step", "stl", "fcstd", "viewer_scene"}.issubset(result["exports"])
    scene = json.loads(base64.b64decode(result["exports"]["viewer_scene"]))
    names = {item["name"] for item in scene["objects"]}
    assert "Slab_01" in names
    assert "Wall_Front_01" in names
    assert "Window_Front_01" in names
    assert "Door_Main_Entrance" in names
    assert "Core_01" in names
    assert "Stair_01" in names
    assert "Roof_Slab_Parapet" in names

    inspected = run_freecad_document_inspect(result["exports"]["fcstd"], timeout=180)
    assert inspected["ok"] is True, inspected.get("error")
    summary = inspected["document_summary"]
    geometry = summary["geometry"]
    assert geometry["valid"] is True
    assert geometry["invalid_object_count"] == 0
    assert geometry["check_error_count"] == 0
    assert geometry["shape_object_count"] >= 30
    objects = {item["name"]: item for item in summary["objects"]}
    assert objects["Building"]["type_id"] == "App::DocumentObjectGroup"
    assert objects["Storey_01"]["type_id"] == "App::DocumentObjectGroup"
    audit = building_audit_from_summary(summary)
    assert audit["status"] == "pass"
    assert audit["actual_storeys"] == 3
    assert audit["element_counts"]["window"] == 12
