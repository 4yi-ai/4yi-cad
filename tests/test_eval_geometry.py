from pathlib import Path

from app.evals.geometry import GeometryReport, check_artifacts

# Watertight tetrahedron, consistent outward winding.
TETRA_STL = """\
solid tetra
facet normal 0 0 -1
 outer loop
  vertex 0 0 0
  vertex 0 1 0
  vertex 1 0 0
 endloop
endfacet
facet normal 0 -1 0
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 0 1
 endloop
endfacet
facet normal -1 0 0
 outer loop
  vertex 0 0 0
  vertex 0 0 1
  vertex 0 1 0
 endloop
endfacet
facet normal 1 1 1
 outer loop
  vertex 1 0 0
  vertex 0 1 0
  vertex 0 0 1
 endloop
endfacet
endsolid tetra
"""

OPEN_STL = """\
solid open
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
endsolid open
"""


def test_watertight_stl_and_valid_step(tmp_path):
    (tmp_path / "model.stl").write_text(TETRA_STL)
    (tmp_path / "model.step").write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")
    report = check_artifacts(tmp_path, fcstd_check=False)
    assert report.step_valid is True
    assert report.stl_watertight is True
    assert report.stl_volume_mm3 is not None and report.stl_volume_mm3 > 1e-9
    assert report.fcstd_loadable is None
    assert report.ok is True


def test_open_mesh_fails(tmp_path):
    (tmp_path / "model.stl").write_text(OPEN_STL)
    report = check_artifacts(tmp_path, fcstd_check=False)
    assert report.stl_watertight is False
    assert report.ok is False
    assert report.issues


def test_bad_step_header_fails(tmp_path):
    (tmp_path / "model.step").write_text("not a step file")
    report = check_artifacts(tmp_path, fcstd_check=False)
    assert report.step_valid is False
    assert report.ok is False


def test_empty_dir_is_unscored(tmp_path):
    report = check_artifacts(tmp_path, fcstd_check=False)
    assert report == GeometryReport(issues=["no checkable artifacts found"])
    assert report.ok is None


def test_fcstd_check_skipped_when_no_freecadcmd(tmp_path, monkeypatch):
    (tmp_path / "model.fcstd").write_bytes(b"PK\x03\x04fake")
    monkeypatch.setenv("PATH", str(tmp_path))  # no FreeCADCmd on PATH
    report = check_artifacts(tmp_path)
    assert report.fcstd_loadable is None
    assert any("FreeCADCmd not available" in i for i in report.issues)
