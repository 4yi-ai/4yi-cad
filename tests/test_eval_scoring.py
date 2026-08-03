import json
from pathlib import Path

from app.agent.loop import scene_role_set
from app.evals.corpus import EvalCase
from app.evals.geometry import GeometryReport
from app.evals.scoring import score_run

SITE_CASE = EvalCase(
    id="t2-x", domain="site_layout", tier="t2",
    prompt="社区+人工湖+儿童场", required_roles=("plot", "building", "water", "play"),
    min_objects=3,
)
MECH_CASE = EvalCase(id="m1-x", domain="mechanical", tier="t1", prompt="法兰")

OK_EVENTS = [
    {"type": "status", "message": "thinking"},
    {"type": "script", "script": "s", "engine": "freecad", "attempt": 1},
    {"type": "retry", "attempt": 1, "message": "boom"},
    {"type": "script", "script": "s2", "engine": "freecad", "attempt": 2},
    {"type": "done", "ok": True, "engine": "freecad"},
]
FAIL_EVENTS = [
    {"type": "status", "message": "thinking"},
    {"type": "script", "script": "s", "engine": "cadquery", "attempt": 1},
    {"type": "error", "message": "zero volume"},
    {"type": "done", "ok": False},
]


def _scene(tmp_path: Path, objects: list[dict]) -> None:
    art = tmp_path / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "viewer_scene.json").write_text(json.dumps({"objects": objects}))


def test_scene_role_set_uses_semantic_roles_and_text():
    scene = {"objects": [
        {"name": "PlotBase", "style": {"semantic_role": "plot"}},
        {"name": "Tower A"},
        {"name": "Central Lake"},
    ]}
    assert {"plot", "building", "water"} <= scene_role_set(scene)


def test_score_success_site_all_layers(tmp_path):
    _scene(tmp_path, [
        {"name": "plot base"}, {"name": "tower 1"},
        {"name": "lake"}, {"name": "playground"},
    ])
    score = score_run(
        SITE_CASE, OK_EVENTS, tmp_path, duration_s=42.0,
        geometry=GeometryReport(step_valid=True, stl_watertight=True, stl_volume_mm3=5.0),
    )
    assert score.l1_ok is True
    assert score.l2_ok is True
    assert score.l3_ok is True
    assert score.attempts == 2 and score.retries == 1
    assert score.error is None


def test_score_site_missing_role_fails_l3(tmp_path):
    _scene(tmp_path, [{"name": "plot base"}, {"name": "tower 1"}, {"name": "tower 2"}])
    score = score_run(
        SITE_CASE, OK_EVENTS, tmp_path, duration_s=1.0,
        geometry=GeometryReport(step_valid=True),
    )
    assert score.l3_ok is False
    assert "water" in score.details["l3_missing_roles"]


def test_score_site_sparse_scene_fails_l3(tmp_path):
    _scene(tmp_path, [{"name": "plot"}, {"name": "tower lake playground"}])
    score = score_run(
        SITE_CASE, OK_EVENTS, tmp_path, duration_s=1.0,
        geometry=GeometryReport(step_valid=True),
    )
    assert score.l3_ok is False


def test_score_failed_run(tmp_path):
    score = score_run(MECH_CASE, FAIL_EVENTS, tmp_path, duration_s=9.0)
    assert score.l1_ok is False
    assert score.l2_ok is None and score.l3_ok is None
    assert score.error == "zero volume"


def test_mech_case_has_no_l3(tmp_path):
    score = score_run(
        MECH_CASE, OK_EVENTS, tmp_path, duration_s=1.0,
        geometry=GeometryReport(step_valid=True, stl_watertight=True, stl_volume_mm3=2.0),
    )
    assert score.l3_ok is None
    assert score.l2_ok is True
