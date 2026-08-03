#!/usr/bin/env python3
"""L4 human-review sheet: sample successful runs into a CSV, ingest scores back."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.evals.report import evaluate_thresholds, render_markdown  # noqa: E402

SHEET_FIELDS = ["case_id", "rep", "tier", "prompt", "artifacts_dir", "score", "notes"]


def _load_records(report_dir: Path) -> list[dict]:
    with (report_dir / "records.jsonl").open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def generate_review_sheet(report_dir: Path, runs_root: Path, sample_size: int = 15) -> Path:
    records = [r for r in _load_records(report_dir) if r.get("l1_ok")]
    rng = random.Random(0)
    by_tier: dict[str, list[dict]] = {}
    for record in records:
        by_tier.setdefault(record.get("tier") or "?", []).append(record)
    sample: list[dict] = []
    tiers = sorted(by_tier)
    while len(sample) < min(sample_size, len(records)):
        for tier in tiers:
            bucket = by_tier[tier]
            if bucket and len(sample) < sample_size:
                sample.append(bucket.pop(rng.randrange(len(bucket))))
    sheet = report_dir / "review_sheet.csv"
    with sheet.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        for record in sample:
            run_dir = record.get("run_dir", "")
            if run_dir:
                artifacts_dir = str(Path(run_dir) / "artifacts")
            else:
                # Fallback for old records without run_dir
                artifacts_dir = str(
                    runs_root / "runs" / "*" / str(record.get("case_id"))
                    / f"rep{record.get('rep')}" / "artifacts"
                )
            writer.writerow({
                "case_id": record.get("case_id"),
                "rep": record.get("rep"),
                "tier": record.get("tier"),
                "prompt": record.get("prompt", ""),
                "artifacts_dir": artifacts_dir,
                "score": "",
                "notes": "",
            })
    return sheet


def ingest_reviews(report_dir: Path, reports_root: Path) -> dict:
    scores: list[int] = []
    with (report_dir / "review_sheet.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("score") or "").strip()
            if not raw:
                continue
            value = int(raw)
            if not 1 <= value <= 5:
                raise ValueError(f"score out of range 1-5: {raw!r} ({row.get('case_id')})")
            scores.append(value)
    if not scores:
        raise ValueError("no scores filled in review_sheet.csv")

    report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    report["metrics"]["human_review_mean"] = sum(scores) / len(scores)
    report["thresholds_met"] = evaluate_thresholds(report["metrics"], report.get("thresholds"))
    (report_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (report_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    latest = reports_root / "latest.json"
    tmp = reports_root / "latest.json.tmp"
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, latest)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["generate", "ingest"])
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--reports-root", default=str(REPO_ROOT / "evals" / "reports"))
    parser.add_argument("--sample-size", type=int, default=15)
    args = parser.parse_args()
    report_dir = Path(args.report_dir)
    reports_root = Path(args.reports_root)
    if args.action == "generate":
        sheet = generate_review_sheet(report_dir, reports_root, args.sample_size)
        print(f"review sheet: {sheet}")
    else:
        report = ingest_reviews(report_dir, reports_root)
        print(f"human_review_mean={report['metrics']['human_review_mean']:.2f} "
              f"thresholds_met={report['thresholds_met']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
