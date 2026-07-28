from app.freecad_state import storage_status, typed_state_diff


def test_typed_state_diff_reports_object_and_geometry_changes():
    before = {
        "geometry": {"volume": 10.0, "valid": True, "face_count": 6},
        "typed_state": {
            "objects": {"Box": {"id": "Box", "properties": {"Length": {"value": 10}}}},
            "feature_tree": {"nodes": {"Box": {"id": "Box", "kind": "part_primitive"}}},
            "sketches": {},
            "assemblies": {},
            "techdraw": {"pages": {}},
        },
    }
    after = {
        "geometry": {"volume": 20.0, "valid": False, "face_count": 7, "failure_class": "invalid_shape"},
        "typed_state": {
            "objects": {
                "Box": {"id": "Box", "properties": {"Length": {"value": 20}}},
                "Boss": {"id": "Boss", "properties": {}},
            },
            "feature_tree": {
                "nodes": {
                    "Box": {"id": "Box", "kind": "part_primitive"},
                    "Boss": {"id": "Boss", "kind": "part_primitive"},
                }
            },
            "sketches": {},
            "assemblies": {},
            "techdraw": {"pages": {}},
        },
    }

    diff = typed_state_diff(before, after)

    assert diff["changed"] is True
    assert diff["objects"]["added"] == ["Boss"]
    assert diff["objects"]["changed"] == ["Box"]
    assert diff["geometry_delta"]["volume"] == {"from": 10.0, "to": 20.0, "delta": 10.0}
    assert diff["geometry_delta"]["valid"] == {"from": True, "to": False}
    assert diff["geometry_delta"]["failure_class"] == {"from": None, "to": "invalid_shape"}


def test_storage_status_marks_tmp_as_not_durable(tmp_path):
    status = storage_status("/tmp/4yi-cad/sessions.sqlite3", str(tmp_path / "artifacts"))

    assert status["session_db"]["durable_configured"] is False
    assert status["artifact_root"]["writable"] is True
