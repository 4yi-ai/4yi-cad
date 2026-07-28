import base64
import json
import sys

import pytest

from app.cad.freecad import MINIMAL_FREECAD_SMOKE_SCRIPT
from app.cad.freecad_worker import (
    FREECAD_RESULT_PREFIX,
    resolve_freecadcmd,
    run_freecad_document_inspect,
    run_freecad_document_patch,
    run_freecad_document_script,
    run_freecad_import_model,
    run_freecad_script,
)

PY = sys.executable


def test_run_freecad_script_reports_missing_binary(monkeypatch):
    monkeypatch.delenv("FREECADCMD_BINARY", raising=False)
    monkeypatch.setattr("app.cad.freecad_worker.shutil.which", lambda name: None)
    monkeypatch.setattr("app.cad.freecad_worker.FREECADCMD_MACOS_CANDIDATES", ())

    result = run_freecad_script("result = None")

    assert result["ok"] is False
    assert "FreeCADCmd unavailable" in result["error"]


def test_run_freecad_script_parses_fake_binary_with_wrapper(tmp_path, monkeypatch):
    monkeypatch.setattr("app.cad.freecad_worker.render_preview_isolated", lambda path: None)
    fake = tmp_path / "fake_freecadcmd.py"
    fake.write_text(
        f"""
import json
import os
import pathlib

out = pathlib.Path(os.environ["FOURYI_FREECAD_OUT"])
(out / "model.step").write_text("ISO-10303-21;", encoding="utf-8")
(out / "model.stl").write_text("solid smoke", encoding="utf-8")
(out / "model.FCStd").write_bytes(b"FCStd smoke")
(out / "viewer-scene.json").write_text('{{"schema":"freecad.viewer_scene.v1","objects":[]}}', encoding="utf-8")
print("{FREECAD_RESULT_PREFIX}" + json.dumps({{
    "ok": True,
    "step_path": str(out / "model.step"),
    "stl_path": str(out / "model.stl"),
    "fcstd_path": str(out / "model.FCStd"),
    "viewer_scene_path": str(out / "viewer-scene.json"),
    "freecad_version": "1.0.0",
}}))
""",
        encoding="utf-8",
    )
    wrapper = tmp_path / "fake_freecadcmd"
    wrapper.write_text(f"#!/bin/sh\nexec {PY} {fake} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)

    result = run_freecad_script(
        "result = None",
        freecadcmd=str(wrapper),
        workdir=str(tmp_path / "work"),
        timeout=5,
    )

    assert result["ok"] is True
    assert result["freecad_version"] == "1.0.0"
    assert set(result["exports"]) == {"step", "stl", "fcstd", "viewer_scene"}
    scene = json.loads(base64.b64decode(result["exports"]["viewer_scene"]))
    assert scene["schema"] == "freecad.viewer_scene.v1"
    assert result["preview_png_b64"] is None


def test_run_freecad_import_model_passes_decoded_import_to_wrapper(tmp_path, monkeypatch):
    monkeypatch.setattr("app.cad.freecad_worker.render_preview_isolated", lambda path: None)
    fake = tmp_path / "fake_freecadcmd_import.py"
    fake.write_text(
        f"""
import json
import os
import pathlib

out = pathlib.Path(os.environ["FOURYI_FREECAD_OUT"])
import_path = pathlib.Path(os.environ["FOURYI_FREECAD_IMPORT_PATH"])
assert os.environ["FOURYI_FREECAD_IMPORT_FORMAT"] == "step"
assert import_path.read_bytes() == b"STEPDATA"
(out / "model.step").write_text("ISO-10303-21;", encoding="utf-8")
(out / "model.stl").write_text("solid imported", encoding="utf-8")
(out / "model.FCStd").write_bytes(b"FCStd imported")
print("{FREECAD_RESULT_PREFIX}" + json.dumps({{
    "ok": True,
    "step_path": str(out / "model.step"),
    "stl_path": str(out / "model.stl"),
    "fcstd_path": str(out / "model.FCStd"),
    "freecad_version": "1.0.0",
}}))
""",
        encoding="utf-8",
    )
    wrapper = tmp_path / "fake_freecadcmd_import"
    wrapper.write_text(f"#!/bin/sh\nexec {PY} {fake} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)

    result = run_freecad_import_model(
        "step",
        base64.b64encode(b"STEPDATA").decode("ascii"),
        freecadcmd=str(wrapper),
        workdir=str(tmp_path / "import-work"),
        filename="part.step",
        timeout=5,
    )

    assert result["ok"] is True
    assert set(result["exports"]) == {"step", "stl", "fcstd"}


def test_run_freecad_document_script_loads_fcstd_before_user_script(tmp_path, monkeypatch):
    monkeypatch.setattr("app.cad.freecad_worker.render_preview_isolated", lambda path: None)
    fake = tmp_path / "fake_freecadcmd_edit.py"
    fake.write_text(
        f"""
import json
import os
import pathlib

out = pathlib.Path(os.environ["FOURYI_FREECAD_OUT"])
doc_path = pathlib.Path(os.environ["FOURYI_FREECAD_DOCUMENT_PATH"])
script_path = pathlib.Path(os.environ["FOURYI_FREECAD_SCRIPT"])
assert doc_path.read_bytes() == b"FCStd source"
assert "Length = 25" in script_path.read_text(encoding="utf-8")
(out / "model.step").write_text("ISO-10303-21; edited", encoding="utf-8")
(out / "model.stl").write_text("solid edited", encoding="utf-8")
(out / "model.FCStd").write_bytes(b"FCStd edited")
print("{FREECAD_RESULT_PREFIX}" + json.dumps({{
    "ok": True,
    "step_path": str(out / "model.step"),
    "stl_path": str(out / "model.stl"),
    "fcstd_path": str(out / "model.FCStd"),
    "freecad_version": "1.0.1",
}}))
""",
        encoding="utf-8",
    )
    wrapper = tmp_path / "fake_freecadcmd_edit"
    wrapper.write_text(f"#!/bin/sh\nexec {PY} {fake} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)

    result = run_freecad_document_script(
        "doc.Objects[0].Length = 25\n",
        base64.b64encode(b"FCStd source").decode("ascii"),
        freecadcmd=str(wrapper),
        workdir=str(tmp_path / "edit-work"),
        timeout=5,
    )

    assert result["ok"] is True
    assert result["freecad_version"] == "1.0.1"
    assert set(result["exports"]) == {"step", "stl", "fcstd"}


def test_run_freecad_document_inspect_loads_fcstd_and_returns_summary(tmp_path):
    fake = tmp_path / "fake_freecadcmd_inspect.py"
    fake.write_text(
        f"""
import json
import os
import pathlib

doc_path = pathlib.Path(os.environ["FOURYI_FREECAD_DOCUMENT_PATH"])
assert os.environ["FOURYI_FREECAD_MODE"] == "inspect"
assert doc_path.read_bytes() == b"FCStd inspect"
print("{FREECAD_RESULT_PREFIX}" + json.dumps({{
    "ok": True,
    "document_summary": {{
        "document": {{"name": "Doc"}},
        "objects": [{{"name": "Box", "type_id": "Part::Box"}}],
        "geometry": {{"object_count": 1, "valid": True}},
        "sketches": [],
        "assemblies": [],
        "techdraw": [],
    }},
    "freecad_version": "1.0.2",
}}))
""",
        encoding="utf-8",
    )
    wrapper = tmp_path / "fake_freecadcmd_inspect"
    wrapper.write_text(f"#!/bin/sh\nexec {PY} {fake} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)

    result = run_freecad_document_inspect(
        base64.b64encode(b"FCStd inspect").decode("ascii"),
        freecadcmd=str(wrapper),
        workdir=str(tmp_path / "inspect-work"),
        timeout=5,
    )

    assert result["ok"] is True
    assert result["freecad_version"] == "1.0.2"
    assert result["document_summary"]["document"]["name"] == "Doc"
    assert result["document_summary"]["objects"][0]["name"] == "Box"


def test_run_freecad_document_patch_loads_fcstd_and_patches_json(tmp_path, monkeypatch):
    monkeypatch.setattr("app.cad.freecad_worker.render_preview_isolated", lambda path: None)
    fake = tmp_path / "fake_freecadcmd_patch.py"
    fake.write_text(
        f"""
import json
import os
import pathlib

out = pathlib.Path(os.environ["FOURYI_FREECAD_OUT"])
doc_path = pathlib.Path(os.environ["FOURYI_FREECAD_DOCUMENT_PATH"])
patches_path = pathlib.Path(os.environ["FOURYI_FREECAD_PATCHES_PATH"])
assert os.environ["FOURYI_FREECAD_MODE"] == "patch"
assert doc_path.read_bytes() == b"FCStd patch"
patches = json.loads(patches_path.read_text(encoding="utf-8"))
assert patches == [
    {{
        "op": "set_property",
        "selector": {{"name": "Box"}},
        "property": "Length",
        "value": 25,
    }},
    {{
        "op": "set_constraint_value",
        "selector": {{"name": "Sketch"}},
        "constraint_index": 0,
        "value": 12,
    }},
    {{
        "op": "create_feature",
        "type_id": "Part::Cylinder",
        "name": "Boss",
        "properties": {{"Radius": 2, "Height": 5}},
        "placement": {{"base": [1, 2, 3]}},
    }},
    {{
        "op": "set_placement",
        "selector": {{"name": "Box"}},
        "base": [4, 5, 6],
        "axis": [0, 0, 1],
        "angle_degrees": 90,
    }},
    {{
        "op": "set_expression",
        "selector": {{"name": "Box"}},
        "property": "Length",
        "expression": "30 mm",
    }},
    {{
        "op": "set_body_tip",
        "selector": {{"name": "Body"}},
        "tip_selector": {{"name": "Pad"}},
    }},
    {{
        "op": "create_sketch",
        "name": "FaceSketch",
        "support_selector": {{"name": "Box"}},
        "reference": "Face1",
        "map_mode": "FlatFace",
    }},
    {{
        "op": "attach_sketch",
        "selector": {{"name": "Sketch"}},
        "support_selector": {{"name": "Box"}},
        "reference": "Face1",
        "map_mode": "FlatFace",
    }},
    {{
        "op": "add_external_geometry",
        "selector": {{"name": "Sketch"}},
        "source_selector": {{"name": "Box"}},
        "references": ["Edge1"],
    }},
    {{
        "op": "solver_status",
        "selector": {{"name": "Sketch"}},
    }},
    {{
        "op": "add_geometry",
        "selector": {{"name": "Sketch"}},
        "geometry": {{"type": "line_segment", "start": [0, 0, 0], "end": [20, 0, 0]}},
    }},
    {{
        "op": "add_constraint",
        "selector": {{"name": "Sketch"}},
        "constraint": {{"type": "Horizontal", "geometry_index": 0}},
    }},
    {{
        "op": "remove_constraint",
        "selector": {{"name": "Sketch"}},
        "constraint_index": 0,
    }},
    {{
        "op": "create_assembly",
        "name": "Assembly",
        "label": "Assembly",
    }},
    {{
        "op": "add_part_to_assembly",
        "selector": {{"name": "Assembly"}},
        "part_selector": {{"name": "Box"}},
        "placement": {{"base": [10, 0, 0]}},
    }},
    {{
        "op": "set_assembly_part_placement",
        "selector": {{"name": "Assembly"}},
        "part_selector": {{"name": "Box"}},
        "base": [20, 0, 0],
    }},
    {{
        "op": "ground_assembly_part",
        "selector": {{"name": "Assembly"}},
        "part_selector": {{"name": "Box"}},
        "name": "GroundBox",
    }},
    {{
        "op": "create_joint",
        "selector": {{"name": "Assembly"}},
        "joint_type": "fixed",
        "name": "FixedJoint",
        "connector1": {{"selector": {{"name": "Box"}}, "element": "Face6", "vertex": "Vertex7"}},
        "connector2": {{"selector": {{"name": "Boss"}}, "element": "Face6", "vertex": "Vertex7"}},
    }},
    {{
        "op": "update_joint",
        "selector": {{"name": "FixedJoint"}},
        "joint_type": "distance",
        "distance": 15,
    }},
    {{
        "op": "solve_assembly",
        "selector": {{"name": "Assembly"}},
    }},
    {{
        "op": "remove_part_from_assembly",
        "selector": {{"name": "Assembly"}},
        "part_selector": {{"name": "Box"}},
    }},
    {{
        "op": "create_techdraw_page",
        "name": "Page",
        "scale": 1,
    }},
    {{
        "op": "add_techdraw_view",
        "page_selector": {{"name": "Page"}},
        "source_selector": {{"name": "Box"}},
        "name": "FrontView",
        "direction": [0, -1, 0],
        "x": 100,
        "y": 100,
        "scale": 1,
    }},
    {{
        "op": "add_techdraw_projection_group",
        "page_selector": {{"name": "Page"}},
        "source_selector": {{"name": "Box"}},
        "name": "ProjectionGroup",
        "projection_names": ["Front", "Left", "Top"],
    }},
    {{
        "op": "add_techdraw_section_view",
        "page_selector": {{"name": "Page"}},
        "base_view_selector": {{"name": "FrontView"}},
        "name": "SectionView",
        "section_normal": [0, 1, 0],
        "section_origin": [5, 5, 5],
    }},
    {{
        "op": "add_techdraw_detail_view",
        "page_selector": {{"name": "Page"}},
        "base_view_selector": {{"name": "FrontView"}},
        "name": "DetailView",
        "anchor_point": [5, 5, 0],
        "radius": 5,
    }},
    {{
        "op": "add_techdraw_centerline",
        "view_selector": {{"name": "FrontView"}},
        "references": ["Edge1", "Edge2"],
    }},
    {{
        "op": "add_techdraw_cosmetic_vertex",
        "view_selector": {{"name": "FrontView"}},
        "point": [5, 5, 0],
    }},
    {{
        "op": "add_techdraw_cosmetic_line",
        "view_selector": {{"name": "FrontView"}},
        "start": [0, 0, 0],
        "end": [10, 0, 0],
    }},
    {{
        "op": "export_techdraw_pdf",
        "page_selector": {{"name": "Page"}},
    }},
    {{
        "op": "add_techdraw_dimension",
        "page_selector": {{"name": "Page"}},
        "view_selector": {{"name": "FrontView"}},
        "name": "WidthDim",
        "dimension_type": "Distance",
        "reference": "Edge1",
    }},
    {{
        "op": "delete_feature",
        "selector": {{"name": "Boss"}},
    }},
]
(out / "model.step").write_text("ISO-10303-21; patched", encoding="utf-8")
(out / "model.stl").write_text("solid patched", encoding="utf-8")
(out / "model.FCStd").write_bytes(b"FCStd patched")
(out / "drawing.svg").write_text("<svg><path /></svg>", encoding="utf-8")
(out / "drawing.dxf").write_text("0\\nSECTION\\n2\\nENTITIES\\n0\\nENDSEC\\n0\\nEOF\\n", encoding="utf-8")
(out / "drawing.pdf").write_bytes(b"%PDF-1.4\\n")
print("{FREECAD_RESULT_PREFIX}" + json.dumps({{
    "ok": True,
    "step_path": str(out / "model.step"),
    "stl_path": str(out / "model.stl"),
    "fcstd_path": str(out / "model.FCStd"),
    "techdraw_svg_path": str(out / "drawing.svg"),
    "techdraw_dxf_path": str(out / "drawing.dxf"),
    "techdraw_pdf_path": str(out / "drawing.pdf"),
    "techdraw_pdf_status": {{"ok": True, "exporter": "fake-rsvg-convert"}},
    "patch_results": [
        {{"index": 0, "op": "set_property", "property": "Length", "new_value": 25}},
        {{"index": 1, "op": "set_constraint_value", "constraint_index": 0, "new_value": 12}},
        {{"index": 2, "op": "create_feature", "created_type_id": "Part::Cylinder"}},
        {{"index": 3, "op": "set_placement"}},
        {{"index": 4, "op": "set_expression"}},
        {{"index": 5, "op": "set_body_tip"}},
        {{"index": 6, "op": "create_sketch"}},
        {{"index": 7, "op": "attach_sketch"}},
        {{"index": 8, "op": "add_external_geometry"}},
        {{"index": 9, "op": "solver_status"}},
        {{"index": 10, "op": "add_geometry"}},
        {{"index": 11, "op": "add_constraint"}},
        {{"index": 12, "op": "remove_constraint"}},
        {{"index": 13, "op": "create_assembly"}},
        {{"index": 14, "op": "add_part_to_assembly"}},
        {{"index": 15, "op": "set_assembly_part_placement"}},
        {{"index": 16, "op": "ground_assembly_part"}},
        {{"index": 17, "op": "create_joint"}},
        {{"index": 18, "op": "update_joint"}},
        {{"index": 19, "op": "solve_assembly"}},
        {{"index": 20, "op": "remove_part_from_assembly"}},
        {{"index": 21, "op": "create_techdraw_page"}},
        {{"index": 22, "op": "add_techdraw_view"}},
        {{"index": 23, "op": "add_techdraw_projection_group"}},
        {{"index": 24, "op": "add_techdraw_section_view"}},
        {{"index": 25, "op": "add_techdraw_detail_view"}},
        {{"index": 26, "op": "add_techdraw_centerline"}},
        {{"index": 27, "op": "add_techdraw_cosmetic_vertex"}},
        {{"index": 28, "op": "add_techdraw_cosmetic_line"}},
        {{"index": 29, "op": "export_techdraw_pdf"}},
        {{"index": 30, "op": "add_techdraw_dimension"}},
        {{"index": 31, "op": "delete_feature"}},
    ],
    "freecad_version": "1.0.3",
}}))
""",
        encoding="utf-8",
    )
    wrapper = tmp_path / "fake_freecadcmd_patch"
    wrapper.write_text(f"#!/bin/sh\nexec {PY} {fake} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)

    patches = [
        {
            "op": "set_property",
            "selector": {"name": "Box"},
            "property": "Length",
            "value": 25,
        },
        {
            "op": "set_constraint_value",
            "selector": {"name": "Sketch"},
            "constraint_index": 0,
            "value": 12,
        },
        {
            "op": "create_feature",
            "type_id": "Part::Cylinder",
            "name": "Boss",
            "properties": {"Radius": 2, "Height": 5},
            "placement": {"base": [1, 2, 3]},
        },
        {
            "op": "set_placement",
            "selector": {"name": "Box"},
            "base": [4, 5, 6],
            "axis": [0, 0, 1],
            "angle_degrees": 90,
        },
        {
            "op": "set_expression",
            "selector": {"name": "Box"},
            "property": "Length",
            "expression": "30 mm",
        },
        {
            "op": "set_body_tip",
            "selector": {"name": "Body"},
            "tip_selector": {"name": "Pad"},
        },
        {
            "op": "create_sketch",
            "name": "FaceSketch",
            "support_selector": {"name": "Box"},
            "reference": "Face1",
            "map_mode": "FlatFace",
        },
        {
            "op": "attach_sketch",
            "selector": {"name": "Sketch"},
            "support_selector": {"name": "Box"},
            "reference": "Face1",
            "map_mode": "FlatFace",
        },
        {
            "op": "add_external_geometry",
            "selector": {"name": "Sketch"},
            "source_selector": {"name": "Box"},
            "references": ["Edge1"],
        },
        {
            "op": "solver_status",
            "selector": {"name": "Sketch"},
        },
        {
            "op": "add_geometry",
            "selector": {"name": "Sketch"},
            "geometry": {"type": "line_segment", "start": [0, 0, 0], "end": [20, 0, 0]},
        },
        {
            "op": "add_constraint",
            "selector": {"name": "Sketch"},
            "constraint": {"type": "Horizontal", "geometry_index": 0},
        },
        {
            "op": "remove_constraint",
            "selector": {"name": "Sketch"},
            "constraint_index": 0,
        },
        {
            "op": "create_assembly",
            "name": "Assembly",
            "label": "Assembly",
        },
        {
            "op": "add_part_to_assembly",
            "selector": {"name": "Assembly"},
            "part_selector": {"name": "Box"},
            "placement": {"base": [10, 0, 0]},
        },
        {
            "op": "set_assembly_part_placement",
            "selector": {"name": "Assembly"},
            "part_selector": {"name": "Box"},
            "base": [20, 0, 0],
        },
        {
            "op": "ground_assembly_part",
            "selector": {"name": "Assembly"},
            "part_selector": {"name": "Box"},
            "name": "GroundBox",
        },
        {
            "op": "create_joint",
            "selector": {"name": "Assembly"},
            "joint_type": "fixed",
            "name": "FixedJoint",
            "connector1": {"selector": {"name": "Box"}, "element": "Face6", "vertex": "Vertex7"},
            "connector2": {"selector": {"name": "Boss"}, "element": "Face6", "vertex": "Vertex7"},
        },
        {
            "op": "update_joint",
            "selector": {"name": "FixedJoint"},
            "joint_type": "distance",
            "distance": 15,
        },
        {
            "op": "solve_assembly",
            "selector": {"name": "Assembly"},
        },
        {
            "op": "remove_part_from_assembly",
            "selector": {"name": "Assembly"},
            "part_selector": {"name": "Box"},
        },
        {
            "op": "create_techdraw_page",
            "name": "Page",
            "scale": 1,
        },
        {
            "op": "add_techdraw_view",
            "page_selector": {"name": "Page"},
            "source_selector": {"name": "Box"},
            "name": "FrontView",
            "direction": [0, -1, 0],
            "x": 100,
            "y": 100,
            "scale": 1,
        },
        {
            "op": "add_techdraw_projection_group",
            "page_selector": {"name": "Page"},
            "source_selector": {"name": "Box"},
            "name": "ProjectionGroup",
            "projection_names": ["Front", "Left", "Top"],
        },
        {
            "op": "add_techdraw_section_view",
            "page_selector": {"name": "Page"},
            "base_view_selector": {"name": "FrontView"},
            "name": "SectionView",
            "section_normal": [0, 1, 0],
            "section_origin": [5, 5, 5],
        },
        {
            "op": "add_techdraw_detail_view",
            "page_selector": {"name": "Page"},
            "base_view_selector": {"name": "FrontView"},
            "name": "DetailView",
            "anchor_point": [5, 5, 0],
            "radius": 5,
        },
        {
            "op": "add_techdraw_centerline",
            "view_selector": {"name": "FrontView"},
            "references": ["Edge1", "Edge2"],
        },
        {
            "op": "add_techdraw_cosmetic_vertex",
            "view_selector": {"name": "FrontView"},
            "point": [5, 5, 0],
        },
        {
            "op": "add_techdraw_cosmetic_line",
            "view_selector": {"name": "FrontView"},
            "start": [0, 0, 0],
            "end": [10, 0, 0],
        },
        {
            "op": "export_techdraw_pdf",
            "page_selector": {"name": "Page"},
        },
        {
            "op": "add_techdraw_dimension",
            "page_selector": {"name": "Page"},
            "view_selector": {"name": "FrontView"},
            "name": "WidthDim",
            "dimension_type": "Distance",
            "reference": "Edge1",
        },
        {
            "op": "delete_feature",
            "selector": {"name": "Boss"},
        },
    ]
    result = run_freecad_document_patch(
        patches,
        base64.b64encode(b"FCStd patch").decode("ascii"),
        freecadcmd=str(wrapper),
        workdir=str(tmp_path / "patch-work"),
        timeout=5,
    )

    assert result["ok"] is True
    assert result["freecad_version"] == "1.0.3"
    assert set(result["exports"]) == {
        "step",
        "stl",
        "fcstd",
        "techdraw_svg",
        "techdraw_dxf",
        "techdraw_pdf",
    }
    assert result["techdraw_pdf_status"]["ok"] is True
    assert [item["op"] for item in result["patch_results"]] == [
        "set_property",
        "set_constraint_value",
        "create_feature",
        "set_placement",
        "set_expression",
        "set_body_tip",
        "create_sketch",
        "attach_sketch",
        "add_external_geometry",
        "solver_status",
        "add_geometry",
        "add_constraint",
        "remove_constraint",
        "create_assembly",
        "add_part_to_assembly",
        "set_assembly_part_placement",
        "ground_assembly_part",
        "create_joint",
        "update_joint",
        "solve_assembly",
        "remove_part_from_assembly",
        "create_techdraw_page",
        "add_techdraw_view",
        "add_techdraw_projection_group",
        "add_techdraw_section_view",
        "add_techdraw_detail_view",
        "add_techdraw_centerline",
        "add_techdraw_cosmetic_vertex",
        "add_techdraw_cosmetic_line",
        "export_techdraw_pdf",
        "add_techdraw_dimension",
        "delete_feature",
    ]


def test_run_freecad_import_model_rejects_unsupported_format_before_runtime():
    result = run_freecad_import_model("dxf", base64.b64encode(b"0").decode("ascii"))

    assert result["ok"] is False
    assert "unsupported import format" in result["error"]


def test_run_freecad_document_inspect_rejects_invalid_base64_before_runtime():
    result = run_freecad_document_inspect("not-base64")

    assert result["ok"] is False
    assert "invalid base64" in result["error"]


def test_run_freecad_document_patch_rejects_empty_patches_before_runtime():
    result = run_freecad_document_patch([], base64.b64encode(b"FCStd").decode("ascii"))

    assert result["ok"] is False
    assert "non-empty list" in result["error"]


def test_resolve_freecadcmd_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("FREECADCMD_BINARY", "/custom/FreeCADCmd")

    assert resolve_freecadcmd() == "/custom/FreeCADCmd"


@pytest.mark.skipif(
    resolve_freecadcmd() is None,
    reason="FreeCADCmd is not installed locally",
)
def test_local_freecadcmd_smoke_exports_step_stl_and_fcstd():
    result = run_freecad_script(MINIMAL_FREECAD_SMOKE_SCRIPT, timeout=90)

    assert result["ok"] is True
    assert {"step", "stl", "fcstd"}.issubset(result["exports"])
    if "viewer_scene" in result["exports"]:
        scene = json.loads(base64.b64decode(result["exports"]["viewer_scene"]))
        assert scene["schema"] == "freecad.viewer_scene.v1"
        assert scene["objects"][0]["faces"]
        assert scene["objects"][0]["edges"]
        assert scene["objects"][0]["vertices"]
        assert scene["objects"][0]["edges"][0]["points"]
        assert scene["objects"][0]["vertices"][0]["point"]
    assert result["freecad_version"]


@pytest.mark.skipif(
    resolve_freecadcmd() is None,
    reason="FreeCADCmd is not installed locally",
)
def test_local_freecadcmd_document_patch_typed_feature_tree_ops():
    source = run_freecad_script(
        """
doc = FreeCAD.newDocument("TypedPatchSmoke")
box = doc.addObject("Part::Box", "BaseBox")
box.Length = 10
box.Width = 8
box.Height = 6
sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
doc.recompute()
result = [box, sketch]
""",
        timeout=90,
    )
    assert source["ok"] is True

    patched = run_freecad_document_patch(
        [
            {
                "op": "create_feature",
                "type_id": "Part::Cylinder",
                "name": "Boss",
                "properties": {"Radius": 2, "Height": 5},
                "placement": {"base": [12, 0, 0]},
            },
            {
                "op": "set_placement",
                "selector": {"name": "BaseBox"},
                "base": [1, 2, 3],
                "axis": [0, 0, 1],
                "angle_degrees": 0,
            },
            {
                "op": "set_expression",
                "selector": {"name": "BaseBox"},
                "property": "Length",
                "expression": "20 mm",
            },
            {
                "op": "create_feature",
                "type_id": "PartDesign::Body",
                "name": "Body",
            },
            {
                "op": "create_feature",
                "type_id": "PartDesign::AdditiveBox",
                "name": "AddBox",
                "body_selector": {"name": "Body"},
                "properties": {"Length": 3, "Width": 2, "Height": 1},
                "set_body_tip": True,
            },
            {
                "op": "set_body_tip",
                "selector": {"name": "Body"},
                "tip_selector": {"name": "AddBox"},
            },
            {
                "op": "create_sketch",
                "name": "FaceSketch",
                "support_selector": {"name": "BaseBox"},
                "reference": "Face1",
                "map_mode": "FlatFace",
            },
            {
                "op": "attach_sketch",
                "selector": {"name": "Sketch"},
                "support_selector": {"name": "BaseBox"},
                "reference": "Face1",
                "map_mode": "FlatFace",
            },
            {
                "op": "add_external_geometry",
                "selector": {"name": "Sketch"},
                "source_selector": {"name": "BaseBox"},
                "references": ["Edge1"],
            },
            {
                "op": "solver_status",
                "selector": {"name": "Sketch"},
            },
            {
                "op": "add_geometry",
                "selector": {"name": "Sketch"},
                "geometries": [
                    {"type": "line_segment", "start": [0, 0, 0], "end": [10, 0, 0]},
                    {
                        "type": "circle",
                        "center": [5, 5, 0],
                        "radius": 2,
                        "construction": True,
                    },
                    {"type": "rectangle", "points": [[0, 0, 0], [4, 3, 0]]},
                    {"type": "ellipse", "center": [12, 6, 0], "major_radius": 4, "minor_radius": 2},
                    {
                        "type": "arc_3_points",
                        "start": [0, 5, 0],
                        "mid": [3, 8, 0],
                        "end": [6, 5, 0],
                    },
                ],
            },
            {
                "op": "add_constraint",
                "selector": {"name": "Sketch"},
                "constraints": [
                    {"type": "Horizontal", "geometry_index": 0, "name": "line_horizontal"},
                    {
                        "type": "DistanceX",
                        "first": 0,
                        "first_pos": 1,
                        "second": 0,
                        "second_pos": 2,
                        "value": 20,
                        "name": "line_width",
                    },
                ],
            },
            {
                "op": "remove_constraint",
                "selector": {"name": "Sketch"},
                "constraint_name": "line_horizontal",
            },
            {
                "op": "validate_sketch",
                "selector": {"name": "Sketch"},
            },
            {
                "op": "create_assembly",
                "name": "Assembly",
            },
            {
                "op": "add_part_to_assembly",
                "selector": {"name": "Assembly"},
                "part_selector": {"name": "BaseBox"},
                "base": [30, 0, 0],
            },
            {
                "op": "add_part_to_assembly",
                "selector": {"name": "Assembly"},
                "part_selector": {"name": "Boss"},
                "base": [45, 0, 0],
            },
            {
                "op": "set_assembly_part_placement",
                "selector": {"name": "Assembly"},
                "part_selector": {"name": "Boss"},
                "base": [60, 0, 0],
            },
            {
                "op": "ground_assembly_part",
                "selector": {"name": "Assembly"},
                "part_selector": {"name": "BaseBox"},
                "name": "GroundBaseBox",
            },
            {
                "op": "solve_assembly",
                "selector": {"name": "Assembly"},
            },
            {
                "op": "create_techdraw_page",
                "name": "Page",
                "scale": 1,
            },
            {
                "op": "add_techdraw_view",
                "page_selector": {"name": "Page"},
                "source_selector": {"name": "BaseBox"},
                "name": "FrontView",
                "direction": [0, -1, 0],
                "x": 100,
                "y": 100,
                "scale": 1,
            },
            {
                "op": "add_techdraw_projection_group",
                "page_selector": {"name": "Page"},
                "source_selector": {"name": "BaseBox"},
                "name": "ProjectionGroup",
                "projection_names": ["Front", "Left", "Top"],
                "x": 140,
                "y": 120,
                "scale": 1,
            },
            {
                "op": "add_techdraw_section_view",
                "page_selector": {"name": "Page"},
                "base_view_selector": {"name": "FrontView"},
                "name": "SectionView",
                "direction": [0, 1, 0],
                "section_normal": [0, 1, 0],
                "section_origin": [5, 4, 3],
                "x": 160,
                "y": 100,
                "scale": 1,
            },
            {
                "op": "add_techdraw_detail_view",
                "page_selector": {"name": "Page"},
                "base_view_selector": {"name": "FrontView"},
                "name": "DetailView",
                "anchor_point": [5, 4, 0],
                "radius": 5,
                "x": 190,
                "y": 100,
                "scale": 2,
            },
            {
                "op": "add_techdraw_centerline",
                "view_selector": {"name": "FrontView"},
                "references": ["Edge1", "Edge2"],
            },
            {
                "op": "add_techdraw_cosmetic_vertex",
                "view_selector": {"name": "FrontView"},
                "point": [5, 5, 0],
            },
            {
                "op": "add_techdraw_cosmetic_line",
                "view_selector": {"name": "FrontView"},
                "start": [0, 0, 0],
                "end": [10, 0, 0],
            },
            {
                "op": "export_techdraw_pdf",
                "page_selector": {"name": "Page"},
            },
            {
                "op": "add_techdraw_dimension",
                "page_selector": {"name": "Page"},
                "view_selector": {"name": "FrontView"},
                "name": "WidthDim",
                "dimension_type": "Distance",
                "dimension_mode": "chain",
                "reference": "Edge1",
            },
        ],
        source["exports"]["fcstd"],
        timeout=90,
    )
    assert patched["ok"] is True
    assert "techdraw_svg" in patched["exports"]
    assert "techdraw_dxf" in patched["exports"]
    if patched.get("techdraw_pdf_status", {}).get("ok"):
        assert "techdraw_pdf" in patched["exports"]
    assert [item["op"] for item in patched["patch_results"]] == [
        "create_feature",
        "set_placement",
        "set_expression",
        "create_feature",
        "create_feature",
        "set_body_tip",
        "create_sketch",
        "attach_sketch",
        "add_external_geometry",
        "solver_status",
        "add_geometry",
        "add_constraint",
        "remove_constraint",
        "validate_sketch",
        "create_assembly",
        "add_part_to_assembly",
        "add_part_to_assembly",
        "set_assembly_part_placement",
        "ground_assembly_part",
        "solve_assembly",
        "create_techdraw_page",
        "add_techdraw_view",
        "add_techdraw_projection_group",
        "add_techdraw_section_view",
        "add_techdraw_detail_view",
        "add_techdraw_centerline",
        "add_techdraw_cosmetic_vertex",
        "add_techdraw_cosmetic_line",
        "export_techdraw_pdf",
        "add_techdraw_dimension",
    ]

    inspected = run_freecad_document_inspect(patched["exports"]["fcstd"], timeout=90)
    assert inspected["ok"] is True
    summary = inspected["document_summary"]
    assert summary["schema"] == "freecad.document_summary.v6"
    typed_state = summary["typed_state"]
    assert typed_state["schema"] == "freecad.typed_state.v1"
    objects = {item["name"]: item for item in summary["objects"]}
    assert {"BaseBox", "Boss", "Body", "AddBox", "Sketch", "FaceSketch"}.issubset(objects)
    assert "BaseBox" in typed_state["objects"]
    assert typed_state["feature_tree"]["nodes"]["Body"]["tip"] == "AddBox"
    assert objects["BaseBox"]["placement"]["base"] == [30.0, 0.0, 0.0]
    assert objects["BaseBox"]["shape"]["subelements"]["faces"][0]["reference"] == "Face1"
    assert objects["BaseBox"]["shape"]["subelements"]["edges"][0]["reference"] == "Edge1"
    length = next(prop for prop in objects["BaseBox"]["properties"] if prop["name"] == "Length")
    assert length["value"] == 20.0
    assert summary["feature_tree"]["nodes"]
    assert {node["kind"] for node in summary["feature_tree"]["nodes"]} >= {"part_primitive"}
    body_node = next(
        node for node in summary["feature_tree"]["nodes"] if node["object"]["name"] == "Body"
    )
    assert body_node["kind"] == "partdesign_body"
    assert body_node["tip"]["name"] == "AddBox"
    sketch = next(item for item in summary["sketches"] if item["name"] == "Sketch")
    face_sketch = next(item for item in summary["sketches"] if item["name"] == "FaceSketch")
    assert sketch["map_mode"] == "FlatFace"
    assert sketch["attachment_support"][0]["object"]["name"] == "BaseBox"
    assert sketch["external_geometry_count"] == 1
    assert sketch["external_geometry"][0]["object"]["name"] == "BaseBox"
    assert sketch["solver"]["solver_status"] is None
    assert typed_state["sketches"]["Sketch"]["external_geometry"][0]["object"]["name"] == "BaseBox"
    assert face_sketch["map_mode"] == "FlatFace"
    assert face_sketch["attachment_support"][0]["object"]["name"] == "BaseBox"
    assert sketch["geometry_count"] == 8
    assert sketch["geometry"][1]["construction"] is True
    assert sketch["constraint_count"] == 1
    assert sketch["constraints"][0]["type"] == "DistanceX"
    assert sketch["constraints"][0]["name"] == "line_width"
    assert sketch["constraints"][0]["value"] == 20.0
    assembly = next(item for item in summary["assemblies"] if item["name"] == "Assembly")
    assert assembly["fallback"] is True
    assert assembly["solver_backend"] == "native_transient"
    assert assembly["part_count"] >= 2
    assert assembly["joint_count"] == 1
    parts = {item["name"]: item for item in assembly["parts"]}
    assert parts["BaseBox"]["grounded"] is True
    assert parts["Boss"]["placement"]["base"] == [60.0, 0.0, 0.0]
    assert assembly["joints"][0]["kind"] == "grounded"
    assert assembly["joints"][0]["object_to_ground"]["name"] == "BaseBox"
    page = next(item for item in summary["techdraw"] if item["name"] == "Page")
    assert page["view_count"] >= 3
    assert page["dimension_count"] == 1
    views = {item["name"]: item for item in page["views"]}
    assert views["FrontView"]["source"][0]["name"] == "BaseBox"
    assert views["ProjectionGroup"]["kind"] == "techdraw_projection_group"
    assert len(views["ProjectionGroup"]["views"]) >= 3
    assert views["SectionView"]["kind"] == "techdraw_section_view"
    assert views["SectionView"]["baseView"]["name"] == "FrontView"
    assert views["DetailView"]["kind"] == "techdraw_detail_view"
    assert views["DetailView"]["baseView"]["name"] == "FrontView"
    assert views["FrontView"]["center_lines"]
    assert views["FrontView"]["cosmetic_vertexes"]
    assert views["FrontView"]["cosmetic_edges"]
    assert "Page" in typed_state["techdraw"]["pages"]
    assert "ProjectionGroup" in typed_state["techdraw"]["pages"]["Page"]["views"]
    assert page["dimensions"][0]["name"] == "WidthDim"
    assert page["dimensions"][0]["type"] == "Distance"
    assert page["dimensions"][0]["dimension_mode"] == "chain"


@pytest.mark.skipif(
    resolve_freecadcmd() is None,
    reason="FreeCADCmd is not installed locally",
)
def test_local_freecadcmd_assembly_joint_types_and_solver():
    joint_types = ["fixed", "revolute", "slider", "cylindrical", "distance", "angle"]
    source_lines = ["doc = FreeCAD.newDocument(\"AssemblyJointSmoke\")", "objects = []"]
    for joint_type in joint_types:
        suffix = joint_type.capitalize()
        source_lines.extend(
            [
                f'base_{joint_type} = doc.addObject("Part::Box", "Base{suffix}")',
                f"base_{joint_type}.Length = 20",
                f"base_{joint_type}.Width = 10",
                f"base_{joint_type}.Height = 5",
                f'moving_{joint_type} = doc.addObject("Part::Box", "Moving{suffix}")',
                f"moving_{joint_type}.Length = 10",
                f"moving_{joint_type}.Width = 10",
                f"moving_{joint_type}.Height = 10",
                f"moving_{joint_type}.Placement.Base = FreeCAD.Vector(40, 0, 0)",
                f"objects.extend([base_{joint_type}, moving_{joint_type}])",
            ]
        )
    source_lines.extend(["doc.recompute()", "result = objects"])
    source = run_freecad_script("\n".join(source_lines), timeout=90)
    assert source["ok"] is True

    patches = []
    for joint_type in joint_types:
        suffix = joint_type.capitalize()
        patches.extend(
            [
                {"op": "create_assembly", "name": f"Asm{suffix}"},
                {
                    "op": "add_part_to_assembly",
                    "selector": {"name": f"Asm{suffix}"},
                    "part_selector": {"name": f"Base{suffix}"},
                },
                {
                    "op": "add_part_to_assembly",
                    "selector": {"name": f"Asm{suffix}"},
                    "part_selector": {"name": f"Moving{suffix}"},
                },
                {
                    "op": "ground_assembly_part",
                    "selector": {"name": f"Asm{suffix}"},
                    "part_selector": {"name": f"Base{suffix}"},
                    "name": f"Ground{suffix}",
                },
                {
                    "op": "create_joint",
                    "selector": {"name": f"Asm{suffix}"},
                    "joint_type": joint_type,
                    "name": f"{suffix}Joint",
                    "connector1": {
                        "selector": {"name": f"Base{suffix}"},
                        "element": "Face6",
                        "vertex": "Vertex7",
                    },
                    "connector2": {
                        "selector": {"name": f"Moving{suffix}"},
                        "element": "Face6",
                        "vertex": "Vertex7",
                    },
                    **({"distance": 5} if joint_type == "distance" else {}),
                    **({"angle_degrees": 30} if joint_type == "angle" else {}),
                },
            ]
        )
    patches.extend(
        [
            {
                "op": "update_joint",
                "selector": {"name": "DistanceJoint"},
                "distance": 12,
            },
            {
                "op": "solve_assembly",
                "selector": {"name": "AsmDistance"},
            },
        ]
    )

    patched = run_freecad_document_patch(patches, source["exports"]["fcstd"], timeout=120)
    assert patched["ok"] is True
    ops = [item["op"] for item in patched["patch_results"]]
    assert ops.count("create_joint") == len(joint_types)
    assert "update_joint" in ops
    solve_result = next(item for item in patched["patch_results"] if item["op"] == "solve_assembly")
    assert solve_result["solver_status"] == 0

    inspected = run_freecad_document_inspect(patched["exports"]["fcstd"], timeout=120)
    assert inspected["ok"] is True
    assemblies = {item["name"]: item for item in inspected["document_summary"]["assemblies"]}
    for joint_type in ["Fixed", "Revolute", "Slider", "Cylindrical", "Distance", "Angle"]:
        assembly = assemblies[f"Asm{joint_type}"]
        joints = {item["name"]: item for item in assembly["joints"]}
        joint = joints[f"{joint_type}Joint"]
        assert joint["joint_type"] == joint_type
        assert joint["reference1"]["object"]["name"] == f"Base{joint_type}"
        assert joint["reference2"]["object"]["name"] == f"Moving{joint_type}"
    assert assemblies["AsmDistance"]["joints"][1]["distance"] == 12.0
