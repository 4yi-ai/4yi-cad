import json
from pathlib import Path

from app.evals.report import (
    THRESHOLDS,
    aggregate,
    build_ai_quality_checks,
    evaluate_thresholds,
    load_latest_eval_report,
    save_report,
)


def _rec(case_id="t1-001", domain="site_layout", tier="t1", l1=True, l2=True, l3=True,
         fcstd=None, error=None):
    return {
        "case_id": case_id, "domain": domain, "tier": tier, "rep": 1,
        "l1_ok": l1, "l2_ok": l2 if l1 else None, "l3_ok": l3 if l1 else None,
        "attempts": 1, "retries": 0, "duration_s": 10.0, "error": error,
        "details": {"geometry": {"fcstd_loadable": fcstd}},
    }


def test_aggregate_rates_and_denominators():
    records = [
        _rec(),                                              # full pass
        _rec(case_id="t1-002", l2=False, l3=False),          # geometry + scene fail
        _rec(case_id="t1-003", l1=False, error="boom"),      # exec fail
        _rec(case_id="m1-001", domain="mechanical", l3=True, fcstd=True),
        _rec(case_id="m2-001", domain="mechanical", fcstd=False),
    ]
    report = aggregate(records)
    m = report["metrics"]
    assert report["total_runs"] == 5
    assert m["success_rate"] == 0.8                       # 4/5
    assert m["geometry_valid_rate"] == 0.75               # 3/4 successful with l2 scored
    assert m["fcstd_loadable_rate"] == 0.5                # 1/2 checked
    assert m["site_quality_pass_rate"] == 0.5             # t1-001 pass, t1-002... l3 True? see below
    assert m["human_review_mean"] is None
    assert report["thresholds_met"] is False
    assert report["top_failures"] == [["boom", 1]]


def test_site_quality_denominator_is_site_successes():
    records = [_rec(), _rec(case_id="t1-004", l3=False)]
    m = aggregate(records)["metrics"]
    assert m["site_quality_pass_rate"] == 0.5


def test_evaluate_thresholds_requires_all_and_human():
    good = {"success_rate": 0.95, "geometry_valid_rate": 1.0,
            "fcstd_loadable_rate": 1.0, "site_quality_pass_rate": 0.9,
            "human_review_mean": 4.2}
    assert evaluate_thresholds(good) is True
    assert evaluate_thresholds({**good, "human_review_mean": None}) is False
    assert evaluate_thresholds({**good, "success_rate": 0.89}) is False
    assert THRESHOLDS["success_rate"] == 0.90


def test_save_and_load_latest(tmp_path, monkeypatch):
    records = [_rec()]
    report = aggregate(records)
    out = save_report(report, tmp_path, records)
    assert (out / "report.json").exists()
    assert (out / "records.jsonl").exists()
    assert (out / "report.md").exists()
    latest = tmp_path / "latest.json"
    assert json.loads(latest.read_text())["schema"] == "4yi-cad.eval_report.v1"
    monkeypatch.setenv("CAD_EVAL_REPORT_PATH", str(latest))
    assert load_latest_eval_report()["total_runs"] == 1


def test_load_latest_missing_or_bad(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD_EVAL_REPORT_PATH", str(tmp_path / "nope.json"))
    assert load_latest_eval_report() is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_latest_eval_report(bad) is None


def test_build_ai_quality_checks_shapes():
    no_report = build_ai_quality_checks(None, report_path="/x/latest.json")
    assert [c["key"] for c in no_report] == ["ai_quality_baseline", "ai_quality_thresholds"]
    assert all(c["status"] == "fail" for c in no_report)
    assert no_report[0]["required_for"] == ["private_beta", "public_beta", "ga"]
    assert no_report[1]["required_for"] == ["public_beta", "ga"]

    baseline_only = build_ai_quality_checks(
        {"total_runs": 84, "thresholds_met": False, "metrics": {}, "thresholds": THRESHOLDS},
        report_path="/x/latest.json",
    )
    assert baseline_only[0]["status"] == "pass"
    assert baseline_only[1]["status"] == "fail"

    ready = build_ai_quality_checks(
        {"total_runs": 84, "thresholds_met": True, "metrics": {"success_rate": 0.95},
         "thresholds": THRESHOLDS},
        report_path="/x/latest.json",
    )
    assert all(c["status"] == "pass" for c in ready)
    assert ready[1]["details"]["metrics"] == {"success_rate": 0.95}
