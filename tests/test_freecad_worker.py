import base64
import json
import sys
from pathlib import Path

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
from app.cad.site_layout_templates import site_layout_plot_frame, site_layout_repair_script

PY = sys.executable
WORKER_SOURCE = Path(__file__).resolve().parents[1] / "app" / "cad" / "freecad_worker.py"
SITE_LAYOUT_REFERENCE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "freecad" / "high_end_community_100m.py"
)
SITE_LAYOUT_FREECAD_SMOKE_SCRIPT = SITE_LAYOUT_REFERENCE_SCRIPT.read_text(encoding="utf-8")


def test_site_layout_repair_template_uses_plot_bbox_frame():
    audit = {
        "plot_bbox": {
            "min": [1000, 2000, -100],
            "max": [51000, 122000, 0],
            "size": [50000, 120000, 100],
        },
        "issues": [{"code": "missing_entrance_system"}],
    }

    frame = site_layout_plot_frame(audit)
    script = site_layout_repair_script(audit)

    assert frame == {
        "origin_x": 1000.0,
        "origin_y": 2000.0,
        "origin_z": 0.0,
        "scale_x": 0.5,
        "scale_y": 1.2,
        "scale_z": 0.5,
    }
    assert "FRAME =" in script
    assert "'origin_x': 1000.0" in script
    assert "'scale_y': 1.2" in script
    assert "template_box" in script


def test_site_layout_template_repair_maps_under_budget_to_program_detail():
    audit = {"issues": [{"code": "site_layout_object_budget_below_reference"}]}
    repair_script = site_layout_repair_script(audit)

    assert "'program_detail': True" in repair_script
    assert "add_program_detail" in repair_script
    assert "add_tower_detail" in repair_script
    assert "Floor_Bands" in repair_script


def test_worker_defines_site_layout_object_budget_audit():
    source = WORKER_SOURCE.read_text(encoding="utf-8")

    assert "append_site_layout_object_budget_issues" in source
    assert "site_layout_object_budget_below_reference" in source
    assert "site_layout_object_budget_above_reference" in source
    assert "append_site_layout_object_budget_issues(issues, components, geometry)" in source


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


def test_worker_defines_stable_subelement_ref_v2_semantics():
    source = WORKER_SOURCE.read_text(encoding="utf-8")

    for marker in (
        'stable_id = "{}:v2:{}"',
        '"stable_id": stable_id',
        '"legacy_stable_id"',
        '"signature_version": 2',
        '"stability": "geometric_signature_v2"',
        '"schema": "freecad.subelement_provenance.v1"',
        '"ref_history"',
        "document_topological_lineage",
        "topological_lineage_migration",
        "apply_topological_ref_migration",
        "apply_native_topological_ref_migration",
        "freecad.native_topological_ref_repair.v1",
        "AttachmentSupport",
        "References3D",
        "FourYiTopologyRepairReport",
        "freecad.topological_ref_repair_report.v1",
        '"schema": "freecad.patch_topological_lineage.v1"',
        '"schema": "freecad.operation_history.v1"',
        "stable_signature_score",
        "stable_signature_remapped",
        "stable_signature_ambiguous",
        "match_method",
        "stable_references",
        "stable_signatures",
    ):
        assert marker in source


def test_worker_exports_viewer_scene_style_metadata():
    source = WORKER_SOURCE.read_text(encoding="utf-8")

    for marker in (
        "freecad.viewer_object_style.v1",
        "viewer_object_style",
        "view_object_style",
        "semantic_viewer_style",
        "ViewObject",
        "ShapeColor",
        "LineColor",
        "Transparency",
        "semantic_role",
        "water",
        "building",
        '"style": viewer_object_style(obj)',
        "freecad.viewer_scene_presentation.v1",
        "viewer_scene_presentation",
        "viewer_scene_role_counts",
        '"default_view": "top" if site_layout else "iso"',
        '"distance_multiplier": 1.92 if site_layout else 1.65',
    ):
        assert marker in source


def test_worker_defines_site_layout_missing_first_audit():
    source = WORKER_SOURCE.read_text(encoding="utf-8")

    for marker in (
        "freecad.site_layout_audit.v1",
        "SITE_LAYOUT_REQUIREMENTS",
        "site_layout_audit",
        "site_layout_requirements_report",
        "site_layout_estimated_metrics",
        "plot_control",
        "planning_controls",
        "fire_access",
        "parking_underground",
        "planning_metrics",
        "outside_plot_boundary",
        "floating_site_components",
        "building_spacing_below_minimum",
        '"site_layout": site_layout',
    ):
        assert marker in source


def test_worker_defines_assembly_lcs_and_runtime_capability_diagnostics():
    source = WORKER_SOURCE.read_text(encoding="utf-8")

    for marker in (
        "connector_lcs_axes",
        "connector_lcs_summary",
        '"connector_lcs_missing"',
        '"connector_lcs_origin_only"',
        "assembly_runtime_capabilities",
        '"native_solver_available"',
        '"persistent_native_available"',
        '"persistent_native_reload_safe"',
        '"native_persistent_blocker"',
        '"reload_regression_required"',
        "native_assembly_reload_regression",
        "freecad.native_assembly_reload_regression.v1",
        "FOURYI_FREECAD_RUN_ASSEMBLY_RELOAD_REGRESSION",
        '"min_reload_safe_version": "1.2.0"',
        "native_assembly_reload_safe",
        "FOURYI_FREECAD_FORCE_NATIVE_ASSEMBLY",
        "ensure_assembly_connector_lcs",
        "FourYiConnectorFrame",
        '"skipped_joints"',
        '"created_joints"',
        "freecad.assembly_solver_geometry_verification.v1",
        "assembly_joint_geometry_result",
        "assembly_solver_geometry_verification",
        '"verification_state"',
        '"geometry_residual_count"',
        '"failed_mates"',
        '"needs_review_mates"',
        '"native_solved_joints"',
        '"assembly_geometry_residual_failed"',
        '"assembly_geometry_residual_needs_review"',
    ):
        assert marker in source


def test_worker_defines_conservative_native_topology_repair_paths():
    source = WORKER_SOURCE.read_text(encoding="utf-8")

    for marker in (
        "topological_ref_object_lookup",
        "migrate_native_object_link_property_value",
        "apply_native_ref_property_update",
        '"Source"',
        '"Sources"',
        '"updated_needs_review"',
        '"needs_review_count"',
        '"external_geometry_order_preserved"',
        '"external_geometry_migration_strategy"',
        '"direct_set_preserve_order"',
    ):
        assert marker in source


def test_worker_defines_sketch_edit_mode_conflict_lists_and_techdraw_capabilities():
    source = WORKER_SOURCE.read_text(encoding="utf-8")

    for marker in (
        "sketch_constraint_indexes",
        "sketch_constraint_ref_summaries",
        "sketch_constraint_glyph",
        '"geometry_refs"',
        '"geometry_indexes"',
        '"point_role"',
        '"external_or_axis"',
        "set_sketch_geometry_point",
        "set_sketch_geometry_construction",
        "set_sketch_constraint_state",
        "add_sketch_endpoint_coincidence",
        "annotate_sketch_geometry",
        "sketch_constraint_geometry_refs",
        '"related_constraint_count"',
        '"constraint_diagnostics"',
        "SKETCH_GEOMETRY_POINT_POSITIONS",
        "toggleDriving",
        "toggleActive",
        "add_auto_sketch_constraints",
        "auto_constraints",
        "FourYiSketchExternalGeometryMeta",
        "freecad.sketch_external_geometry_refs.v1",
        '"conflicting_constraints"',
        '"redundant_constraints"',
        '"malformed_constraints"',
        "annotate_sketch_constraints",
        "techdraw_runtime_capabilities",
        "FOURYI_FREECAD_NATIVE_TECHDRAW",
        "FOURYI_FREECAD_DISABLE_NATIVE_TECHDRAW",
        "native_techdraw_detail",
        "merge_techdraw_page_summaries",
        "FourYiTechDrawDimensionMeta",
        "techdraw_export_validation",
        "freecad.techdraw_export_validation.v1",
        "techdraw_autolayout_page",
        "freecad.techdraw_layout_engine.v1",
        '"native_first"',
        '"fallback_reason"',
    ):
        assert marker in source


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


def test_run_freecad_document_patch_dry_run_skips_file_exports(tmp_path):
    fake = tmp_path / "fake_freecadcmd_patch_dry_run.py"
    fake.write_text(
        f"""
import json
import os
import pathlib

doc_path = pathlib.Path(os.environ["FOURYI_FREECAD_DOCUMENT_PATH"])
patches_path = pathlib.Path(os.environ["FOURYI_FREECAD_PATCHES_PATH"])
assert os.environ["FOURYI_FREECAD_MODE"] == "patch"
assert os.environ["FOURYI_FREECAD_DRY_RUN"] == "1"
assert doc_path.read_bytes() == b"FCStd dry run"
assert json.loads(patches_path.read_text(encoding="utf-8")) == [
    {{"op": "validate_sketch", "selector": {{"name": "Sketch"}}, "solve": True}}
]
print("{FREECAD_RESULT_PREFIX}" + json.dumps({{
    "ok": True,
    "dry_run": True,
    "document_summary": {{
        "document": {{"name": "Doc"}},
        "objects": [{{"name": "Sketch", "type_id": "Sketcher::SketchObject"}}],
        "geometry": {{"object_count": 1, "valid": True}},
        "sketches": [],
        "assemblies": [],
        "techdraw": [],
    }},
    "patch_results": [
        {{"index": 0, "op": "validate_sketch", "valid": True, "degrees_of_freedom": 0}}
    ],
    "freecad_version": "1.0.4",
}}))
""",
        encoding="utf-8",
    )
    wrapper = tmp_path / "fake_freecadcmd_patch_dry_run"
    wrapper.write_text(f"#!/bin/sh\nexec {PY} {fake} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)

    result = run_freecad_document_patch(
        [{"op": "validate_sketch", "selector": {"name": "Sketch"}, "solve": True}],
        base64.b64encode(b"FCStd dry run").decode("ascii"),
        dry_run=True,
        freecadcmd=str(wrapper),
        workdir=str(tmp_path / "patch-dry-run-work"),
        timeout=5,
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["exports"] == {}
    assert result["preview_png_b64"] is None
    assert result["document_summary"]["document"]["name"] == "Doc"
    assert result["patch_results"][0]["op"] == "validate_sketch"
    assert result["freecad_version"] == "1.0.4"


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
        assert scene["objects"][0]["faces"][0]["stable_id"].startswith("face:v2:")
        assert scene["objects"][0]["edges"][0]["stable_id"].startswith("edge:v2:")
        assert scene["objects"][0]["vertices"][0]["stable_id"].startswith("vertex:v2:")
        assert scene["objects"][0]["faces"][0]["legacy_stable_id"].startswith("face:")
        assert scene["objects"][0]["faces"][0]["signature"]
        assert scene["objects"][0]["faces"][0]["signature_version"] == 2
        assert scene["objects"][0]["faces"][0]["stability"] == "geometric_signature_v2"
        assert scene["objects"][0]["edges"][0]["points"]
        assert scene["objects"][0]["vertices"][0]["point"]
    assert result["freecad_version"]


@pytest.mark.skipif(
    resolve_freecadcmd() is None,
    reason="FreeCADCmd is not installed locally",
)
def test_local_freecadcmd_site_layout_smoke_exports_named_scene_objects():
    result = run_freecad_script(SITE_LAYOUT_FREECAD_SMOKE_SCRIPT, timeout=90)

    assert result["ok"] is True
    assert {"step", "stl", "fcstd", "viewer_scene"}.issubset(result["exports"])
    scene = json.loads(base64.b64decode(result["exports"]["viewer_scene"]))
    assert scene["schema"] == "freecad.viewer_scene.v1"
    assert scene["object_count"] >= 30
    presentation = scene["presentation"]
    assert presentation["schema"] == "freecad.viewer_scene_presentation.v1"
    assert presentation["site_layout"] is True
    assert presentation["default_view"] == "top"
    assert presentation["fit"] == "all"
    assert presentation["camera_hint"]["distance_multiplier"] > 1.65
    assert presentation["role_counts"]["plot"] >= 1
    assert presentation["role_counts"]["building"] >= 1
    assert presentation["role_counts"]["road"] >= 1

    object_text = {
        f"{item.get('name', '')} {item.get('label', '')}".lower()
        for item in scene["objects"]
    }
    for marker in (
        "plot",
        "tower",
        "water",
        "playground",
        "clubhouse",
        "fire",
        "garage",
        "gate",
    ):
        assert any(marker in text for text in object_text)

    styles = {item["name"]: item.get("style", {}) for item in scene["objects"]}
    assert styles["Plot_Boundary_100x100m"]["semantic_role"] == "plot"
    assert styles["HighRise_Tower_1_Body"]["semantic_role"] == "building"
    assert styles["Water_Artificial_Lake"]["semantic_role"] == "water"
    assert styles["Children_Playground"]["semantic_role"] == "play"
    assert styles["Central_Green_Lawn"]["semantic_role"] == "green"
    assert styles["Clubhouse_Amenity_Body"]["semantic_role"] == "amenity"
    assert styles["Main_Road_N_S"]["semantic_role"] == "road"
    assert len({
        styles[name]["color"]
        for name in (
            "Plot_Boundary_100x100m",
            "HighRise_Tower_1_Body",
            "Water_Artificial_Lake",
            "Children_Playground",
            "Central_Green_Lawn",
            "Main_Road_N_S",
        )
    }) >= 5
    assert 0 < styles["Water_Artificial_Lake"]["opacity"] < styles["HighRise_Tower_1_Body"]["opacity"] <= 1

    inspected = run_freecad_document_inspect(result["exports"]["fcstd"], timeout=90)
    assert inspected["ok"] is True
    audit = inspected["document_summary"]["site_layout"]
    assert audit["schema"] == "freecad.site_layout_audit.v1"
    assert audit["applicable"] is True
    assert audit["status"] == "pass"
    assert audit["coverage_score"] == 1
    assert audit["issues"] == []
    assert all(item["status"] == "pass" for item in audit["requirements"])
    counts = audit["component_counts"]
    assert counts["plot_boundary"] >= 1
    assert counts["setback_control"] >= 1
    assert counts["north_axis"] >= 1
    assert counts["elevation_benchmark"] >= 1
    assert counts["boundary_wall"] >= 5
    assert counts["entrance_system"] >= 1
    assert counts["traffic_network"] >= 6
    assert counts["fire_access"] >= 1
    assert counts["parking_underground"] >= 1
    assert counts["residential_building"] >= 6
    assert counts["public_amenity"] >= 1
    assert counts["landscape_open_space"] >= 3
    assert counts["planning_metrics"] >= 1
    metrics = audit["estimated_metrics"]
    assert metrics["plot_area"] == 10000000000
    assert 0.1 < metrics["estimated_building_density"] < 0.25
    assert 0.2 < metrics["estimated_landscape_ratio"] < 0.5

    imported = run_freecad_import_model(
        "fcstd",
        result["exports"]["fcstd"],
        filename="high_end_community_100m.FCStd",
        timeout=90,
    )
    assert imported["ok"] is True
    imported_inspected = run_freecad_document_inspect(imported["exports"]["fcstd"], timeout=90)
    assert imported_inspected["document_summary"]["site_layout"]["status"] == "pass"
    assert imported_inspected["document_summary"]["site_layout"]["issues"] == []


@pytest.mark.skipif(
    resolve_freecadcmd() is None,
    reason="FreeCADCmd is not installed locally",
)
def test_local_freecadcmd_site_layout_template_repair_fills_missing_roles():
    source = run_freecad_script(
        """
import FreeCAD
import Part

doc = FreeCAD.newDocument("RoughSiteLayout")

def add_box(name, label, x, y, z, length, width, height):
    obj = doc.addObject("Part::Box", name)
    obj.Label = label
    obj.Length = length
    obj.Width = width
    obj.Height = height
    obj.Placement.Base = FreeCAD.Vector(x, y, z)
    return obj

plot = add_box("Plot", "Plot", 0, 0, -80, 100000, 100000, 80)
road = add_box("RoadLoop", "Road loop", 8000, 8000, 0, 84000, 6000, 120)
building_a = add_box("BuildingA", "Building A", 18000, 18000, 0, 14000, 18000, 18000)
building_b = add_box("BuildingB", "Building B", 44000, 18000, 0, 14000, 18000, 15000)
building_c = add_box("BuildingC", "Building C", 70000, 18000, 0, 14000, 18000, 12000)
water = add_box("WaterGarden", "Water garden", 18000, 65000, 0, 24000, 15000, 120)
playground = add_box("Playground", "Playground", 62000, 62000, 0, 18000, 16000, 120)
green = add_box("GreenPark", "Green park", 47000, 61000, 0, 9000, 25000, 100)

doc.recompute()
result = [plot, road, building_a, building_b, building_c, water, playground, green]
""",
        timeout=90,
    )
    assert source["ok"] is True
    inspected = run_freecad_document_inspect(source["exports"]["fcstd"], timeout=90)
    audit = inspected["document_summary"]["site_layout"]
    assert audit["status"] == "fail"
    assert "missing_enclosure_system" in {item["code"] for item in audit["issues"]}

    repaired = run_freecad_document_script(
        site_layout_repair_script(audit),
        source["exports"]["fcstd"],
        timeout=90,
    )
    assert repaired["ok"] is True
    repaired_inspected = run_freecad_document_inspect(repaired["exports"]["fcstd"], timeout=90)
    repaired_audit = repaired_inspected["document_summary"]["site_layout"]
    assert repaired_audit["status"] == "pass"
    assert repaired_audit["issues"] == []
    assert repaired_audit["component_counts"]["boundary_wall"] >= 5
    assert repaired_audit["component_counts"]["fire_access"] >= 1
    assert repaired_audit["component_counts"]["parking_underground"] >= 1
    assert repaired_audit["component_counts"]["planning_metrics"] >= 1


@pytest.mark.skipif(
    resolve_freecadcmd() is None,
    reason="FreeCADCmd is not installed locally",
)
def test_local_freecadcmd_site_layout_template_repair_fills_under_budget_detail():
    source = run_freecad_script(
        """
import FreeCAD

doc = FreeCAD.newDocument("UnderBudgetSiteLayout")

def add_box(name, label, x, y, z, length, width, height):
    obj = doc.addObject("Part::Box", name)
    obj.Label = label
    obj.Length = length
    obj.Width = width
    obj.Height = height
    obj.Placement.Base = FreeCAD.Vector(x, y, z)
    return obj

items = [
    add_box("Plot_Redline", "Plot redline boundary", 0, 0, -80, 100000, 100000, 80),
    add_box("Setback_Control", "Setback control line", 8000, 8000, 0, 84000, 200, 80),
    add_box("North_Axis", "NorthAxis marker", 92000, 74000, 0, 800, 15000, 120),
    add_box("Boundary_Wall_North", "Boundary wall north", 0, 99600, 0, 100000, 400, 3300),
    add_box("Boundary_Wall_West", "Boundary wall west", 0, 0, 0, 400, 100000, 3300),
    add_box("Boundary_Wall_East", "Boundary wall east", 99600, 0, 0, 400, 100000, 3300),
    add_box("Main_Entrance_Gate", "Entrance gate", 43000, 700, 0, 14000, 2200, 5600),
    add_box("Main_Road", "Road path circulation", 45500, 500, 0, 9000, 28500, 120),
    add_box("Fire_Road", "Fire lane ladder access", 10000, 22000, 0, 80000, 6000, 120),
    add_box("Garage_Ramp", "Underground garage ramp parking", 69000, 5200, 0, 9000, 15500, 320),
    add_box("Villa_Residential", "Villa residential building", 12000, 33000, 0, 8000, 8200, 4200),
    add_box("HighRise_Tower", "HighRise residential tower", 62000, 61500, 0, 13000, 15000, 72000),
    add_box("Clubhouse_Amenity", "Clubhouse amenity", 66500, 44500, 0, 15000, 11000, 6200),
    add_box("Water_Lake", "Water artificial lake", 30000, 42000, 0, 26000, 18000, 100),
    add_box("Children_Playground", "Children playground green", 73500, 27000, 0, 11000, 8800, 100),
    add_box("PlanningMetrics", "PlanningMetrics FAR density green ratio", 2500, 87000, 0, 18000, 9000, 120),
]

doc.recompute()
result = items
""",
        timeout=90,
    )
    assert source["ok"] is True
    inspected = run_freecad_document_inspect(source["exports"]["fcstd"], timeout=90)
    audit = inspected["document_summary"]["site_layout"]
    assert audit["status"] == "needs_review"
    assert "site_layout_object_budget_below_reference" in {item["code"] for item in audit["issues"]}

    repaired = run_freecad_document_script(
        site_layout_repair_script(audit),
        source["exports"]["fcstd"],
        timeout=90,
    )
    assert repaired["ok"] is True
    repaired_inspected = run_freecad_document_inspect(repaired["exports"]["fcstd"], timeout=90)
    repaired_audit = repaired_inspected["document_summary"]["site_layout"]
    assert repaired_audit["status"] == "pass"
    assert repaired_audit["issues"] == []
    assert repaired_audit["component_count"] >= 20
    assert repaired_audit["component_counts"]["landscape_open_space"] >= 4


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
                "op": "set_geometry_construction",
                "selector": {"name": "Sketch"},
                "geometry_index": 0,
                "construction": True,
            },
            {
                "op": "add_endpoint_coincidence",
                "selector": {"name": "Sketch"},
                "first": {"geometry_index": 0, "point_role": "start"},
                "second": {"geometry_index": 2, "point_role": "start"},
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
                "op": "set_constraint_state",
                "selector": {"name": "Sketch"},
                "constraint_name": "line_width",
                "new_name": "line_width_reference",
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
        "set_geometry_construction",
        "add_endpoint_coincidence",
        "add_constraint",
        "remove_constraint",
        "set_constraint_state",
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
    assert objects["BaseBox"]["shape"]["subelements"]["faces"][0]["stable_id"].startswith("face:v2:")
    assert objects["BaseBox"]["shape"]["subelements"]["faces"][0]["legacy_stable_id"].startswith("face:")
    assert objects["BaseBox"]["shape"]["subelements"]["faces"][0]["signature_version"] == 2
    assert objects["BaseBox"]["shape"]["subelements"]["faces"][0]["stability"] == "geometric_signature_v2"
    assert objects["BaseBox"]["shape"]["subelements"]["edges"][0]["signature"]
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
    assert sketch["edit_mode"]["constraint_count"] == sketch["constraint_count"]
    assert "diagnostics" in sketch["edit_mode"]
    assert typed_state["sketches"]["Sketch"]["external_geometry"][0]["object"]["name"] == "BaseBox"
    assert face_sketch["map_mode"] == "FlatFace"
    assert face_sketch["attachment_support"][0]["object"]["name"] == "BaseBox"
    assert sketch["geometry_count"] == 8
    assert sketch["geometry"][0]["construction"] is True
    assert sketch["geometry"][1]["construction"] is True
    assert sketch["constraint_count"] == 2
    assert any(item["type"] == "Coincident" for item in sketch["constraints"])
    distance_constraint = next(item for item in sketch["constraints"] if item["type"] == "DistanceX")
    assert distance_constraint["name"] == "line_width_reference"
    assert distance_constraint["value"] == 20.0
    assembly = next(item for item in summary["assemblies"] if item["name"] == "Assembly")
    assert assembly["fallback"] is True
    assert assembly["solver_backend"] == "native_transient"
    assert assembly["solver_diagnostics"]["severity"] in {"ok", "info", "warning", "error"}
    assert assembly["part_count"] >= 2
    assert assembly["joint_count"] == 1
    parts = {item["name"]: item for item in assembly["parts"]}
    assert parts["BaseBox"]["grounded"] is True
    assert parts["Boss"]["placement"]["base"] == [60.0, 0.0, 0.0]
    assert assembly["joints"][0]["kind"] == "grounded"
    assert assembly["joints"][0]["object_to_ground"]["name"] == "BaseBox"
    page = next(item for item in summary["techdraw"] if item["name"] == "Page")
    assert page["layout_diagnostics"]["export_quality"]
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
        assert joint["reference1"]["connector_frame"]["frame_quality"] in {
            "orientation_complete",
            "origin_only",
            "missing_reference",
            "object_only",
        }
        assert "lcs" in joint["reference1"]["connector_frame"]
        assert assembly["solver_diagnostics"]["severity"] in {"ok", "info", "warning", "error"}
    assert assemblies["AsmDistance"]["joints"][1]["distance"] == 12.0


@pytest.mark.skipif(
    resolve_freecadcmd() is None,
    reason="FreeCADCmd is not installed locally",
)
def test_local_freecadcmd_sketch_geometry_point_patch_round_trip():
    source = run_freecad_script(
        """
doc = FreeCAD.newDocument("SketchGeometryPointPatch")
box = doc.addObject("Part::Box", "BaseBox")
box.Length = 10
box.Width = 8
box.Height = 6
sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(10, 0, 0)), False)
doc.recompute()
result = [box, sketch]
""",
        timeout=90,
    )
    assert source["ok"] is True

    patched = run_freecad_document_patch(
        [
            {
                "op": "set_geometry_point",
                "selector": {"name": "Sketch"},
                "geometry_index": 0,
                "point_role": "end",
                "value": [20, 0, 0],
                "solve": True,
            }
        ],
        source["exports"]["fcstd"],
        timeout=90,
    )
    assert patched["ok"] is True
    result = patched["patch_results"][0]
    assert result["new_point"] == [20.0, 0.0, 0.0]
    assert result["topological_lineage"]["repair"]["schema"] == "freecad.topological_ref_repair_report.v1"

    inspected = run_freecad_document_inspect(patched["exports"]["fcstd"], timeout=90)
    assert inspected["ok"] is True
    sketch = next(item for item in inspected["document_summary"]["sketches"] if item["name"] == "Sketch")
    line = next(item for item in sketch["geometry"] if item["index"] == 0)
    assert line["end"] == [20.0, 0.0, 0.0]


@pytest.mark.skipif(
    resolve_freecadcmd() is None,
    reason="FreeCADCmd is not installed locally",
)
def test_local_freecadcmd_sketch_add_geometry_auto_constraint():
    source = run_freecad_script(
        """
doc = FreeCAD.newDocument("SketchAutoConstraint")
box = doc.addObject("Part::Box", "BaseBox")
box.Length = 1
box.Width = 1
box.Height = 1
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
                "op": "add_geometry",
                "selector": {"name": "Sketch"},
                "geometry": {"type": "line", "start": [0, 0, 0], "end": [20, 0, 0]},
                "auto_constraints": True,
                "solve": True,
            }
        ],
        source["exports"]["fcstd"],
        timeout=90,
    )
    assert patched["ok"] is True
    result = patched["patch_results"][0]
    assert result["auto_constraints"][0]["type"] == "Horizontal"

    inspected = run_freecad_document_inspect(patched["exports"]["fcstd"], timeout=90)
    assert inspected["ok"] is True
    sketch = next(item for item in inspected["document_summary"]["sketches"] if item["name"] == "Sketch")
    assert any(item["type"] == "Horizontal" for item in sketch["constraints"])
