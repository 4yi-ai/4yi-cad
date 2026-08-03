import csv
import json
from pathlib import Path

import pytest

from scripts.eval.review_sheet import generate_review_sheet, ingest_reviews


def _report_dir(tmp_path: Path) -> Path:
    d = tmp_path / "20260803-120000"
    d.mkdir(parents=True)
    report = {
        "schema": "4yi-cad.eval_report.v1", "generated_at": "x", "total_runs": 2,
        "metrics": {"success_rate": 1.0, "geometry_valid_rate": 1.0,
                    "fcstd_loadable_rate": 1.0, "site_quality_pass_rate": 1.0,
                    "human_review_mean": None},
        "thresholds": {"success_rate": 0.90, "geometry_valid_rate": 0.95,
                       "fcstd_loadable_rate": 0.95, "site_quality_pass_rate": 0.85,
                       "human_review_mean": 4.0},
        "thresholds_met": False, "by_tier": {}, "top_failures": [],
        "fcstd_checks_skipped": 0,
    }
    (d / "report.json").write_text(json.dumps(report))
    (d / "report.md").write_text("x")
    with (d / "records.jsonl").open("w") as fh:
        for cid, tier in (("t1-001", "t1"), ("t2-001", "t2")):
            fh.write(json.dumps({
                "case_id": cid, "tier": tier, "rep": 1, "l1_ok": True,
                "domain": "site_layout", "error": None,
                "details": {}, "prompt": "p",
            }) + "\n")
    return d


def test_generate_then_ingest_updates_thresholds(tmp_path):
    d = _report_dir(tmp_path)
    sheet = generate_review_sheet(d, tmp_path, sample_size=2)
    rows = list(csv.DictReader(sheet.open()))
    assert {r["case_id"] for r in rows} == {"t1-001", "t2-001"}

    for row in rows:
        row["score"] = "5"
    with sheet.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    updated = ingest_reviews(d, tmp_path)
    assert updated["metrics"]["human_review_mean"] == 5.0
    assert updated["thresholds_met"] is True
    assert json.loads((tmp_path / "latest.json").read_text())["thresholds_met"] is True


def test_ingest_rejects_out_of_range(tmp_path):
    d = _report_dir(tmp_path)
    sheet = generate_review_sheet(d, tmp_path, sample_size=2)
    rows = list(csv.DictReader(sheet.open()))
    rows[0]["score"] = "9"
    with sheet.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError):
        ingest_reviews(d, tmp_path)
