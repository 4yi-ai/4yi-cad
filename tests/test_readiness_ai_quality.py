import json

from fastapi.testclient import TestClient

from app.main import create_app


def _readiness(monkeypatch, tmp_path, report: dict | None):
    path = tmp_path / "latest.json"
    if report is not None:
        path.write_text(json.dumps(report))
    monkeypatch.setenv("CAD_EVAL_REPORT_PATH", str(path))
    client = TestClient(create_app())
    resp = client.get("/api/production/readiness")
    assert resp.status_code == 200
    return resp.json()


def _check(body: dict, key: str) -> dict:
    return next(c for c in body["checks"] if c["key"] == key)


def test_no_report_fails_baseline(monkeypatch, tmp_path):
    body = _readiness(monkeypatch, tmp_path, None)
    assert _check(body, "ai_quality_baseline")["status"] == "fail"
    assert _check(body, "ai_quality_thresholds")["status"] == "fail"
    assert body["release_targets"]["private_beta_ready"] is False


def test_baseline_recorded_passes_private_beta_check(monkeypatch, tmp_path):
    body = _readiness(
        monkeypatch, tmp_path,
        {"schema": "4yi-cad.eval_report.v1", "total_runs": 84,
         "thresholds_met": False, "metrics": {}, "thresholds": {}},
    )
    assert _check(body, "ai_quality_baseline")["status"] == "pass"
    assert _check(body, "ai_quality_thresholds")["status"] == "fail"
    assert "ai_quality_baseline" not in body["summary"]["blockers"]
    assert "ai_quality_thresholds" in body["summary"]["blockers"]


def test_thresholds_met_passes_both(monkeypatch, tmp_path):
    body = _readiness(
        monkeypatch, tmp_path,
        {"schema": "4yi-cad.eval_report.v1", "total_runs": 84,
         "thresholds_met": True, "metrics": {"success_rate": 0.95}, "thresholds": {}},
    )
    assert _check(body, "ai_quality_baseline")["status"] == "pass"
    assert _check(body, "ai_quality_thresholds")["status"] == "pass"
