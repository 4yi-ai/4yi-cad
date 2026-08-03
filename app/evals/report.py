"""Eval report aggregation + persistence + readiness-gate integration.

STDLIB-ONLY IMPORTS: app/main.py imports this module at runtime for the
ai_quality readiness checks; it must not pull dev-only deps (yaml/trimesh).
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "4yi-cad.eval_report.v1"
THRESHOLDS: dict[str, float] = {
    "success_rate": 0.90,
    "geometry_valid_rate": 0.95,
    "fcstd_loadable_rate": 0.95,
    "site_quality_pass_rate": 0.85,
    "human_review_mean": 4.0,
}
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _rate(passes: int, total: int) -> float | None:
    return None if total == 0 else passes / total


def aggregate(records: list[dict]) -> dict:
    total = len(records)
    successes = [r for r in records if r.get("l1_ok")]
    l2_scored = [r for r in successes if r.get("l2_ok") is not None]
    fcstd_checked = [
        r for r in records
        if ((r.get("details") or {}).get("geometry") or {}).get("fcstd_loadable") is not None
    ]
    site_scored = [
        r for r in successes
        if r.get("domain") == "site_layout" and r.get("l3_ok") is not None
    ]
    failures = Counter(
        (r.get("error") or "unknown").splitlines()[0]
        for r in records
        if not r.get("l1_ok")
    )
    by_tier: dict[str, dict] = {}
    for tier in sorted({r.get("tier") for r in records if r.get("tier")}):
        tier_records = [r for r in records if r.get("tier") == tier]
        by_tier[tier] = {
            "runs": len(tier_records),
            "success_rate": _rate(
                sum(1 for r in tier_records if r.get("l1_ok")), len(tier_records)
            ),
        }
    metrics = {
        "success_rate": _rate(len(successes), total),
        "geometry_valid_rate": _rate(
            sum(1 for r in l2_scored if r.get("l2_ok")), len(l2_scored)
        ),
        "fcstd_loadable_rate": _rate(
            sum(
                1 for r in fcstd_checked
                if ((r.get("details") or {}).get("geometry") or {}).get("fcstd_loadable")
            ),
            len(fcstd_checked),
        ),
        "site_quality_pass_rate": _rate(
            sum(1 for r in site_scored if r.get("l3_ok")), len(site_scored)
        ),
        "human_review_mean": None,
    }
    return {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_runs": total,
        "metrics": metrics,
        "thresholds": dict(THRESHOLDS),
        "thresholds_met": evaluate_thresholds(metrics),
        "by_tier": by_tier,
        "top_failures": [list(item) for item in failures.most_common(10)],
        "fcstd_checks_skipped": len(successes) - len(fcstd_checked),
    }


def evaluate_thresholds(metrics: dict, thresholds: dict[str, float] | None = None) -> bool:
    thresholds = THRESHOLDS if thresholds is None else thresholds
    for key, floor in thresholds.items():
        value = metrics.get(key)
        if value is None or value < floor:
            return False
    return True


def render_markdown(report: dict) -> str:
    lines = [
        "# 4yi-cad Eval Report",
        f"- generated_at: {report['generated_at']}",
        f"- total_runs: {report['total_runs']}",
        f"- thresholds_met: **{report['thresholds_met']}**",
        "",
        "| metric | value | threshold |",
        "|---|---|---|",
    ]
    for key, floor in report["thresholds"].items():
        value = report["metrics"].get(key)
        shown = "n/a" if value is None else f"{value:.3f}"
        lines.append(f"| {key} | {shown} | ≥ {floor} |")
    lines.append("")
    lines.append("## Top failures")
    for message, count in report.get("top_failures", []):
        lines.append(f"- {count}× {message}")
    return "\n".join(lines) + "\n"


def save_report(report: dict, reports_root: Path, records: list[dict]) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = reports_root / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (out / "records.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    latest = reports_root / "latest.json"
    tmp = reports_root / "latest.json.tmp"
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, latest)
    return out


def default_report_path() -> Path:
    env = os.environ.get("CAD_EVAL_REPORT_PATH", "").strip()
    return Path(env) if env else _REPO_ROOT / "evals" / "reports" / "latest.json"


def load_latest_eval_report(path: str | Path | None = None) -> dict | None:
    target = Path(path) if path is not None else default_report_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def build_ai_quality_checks(report: dict | None, *, report_path: str) -> list[dict[str, Any]]:
    total_runs = int((report or {}).get("total_runs") or 0)
    baseline_recorded = total_runs > 0
    thresholds_met = bool((report or {}).get("thresholds_met"))
    return [
        {
            "key": "ai_quality_baseline",
            "status": "pass" if baseline_recorded else "fail",
            "message": (
                f"AI eval baseline recorded ({total_runs} runs)."
                if baseline_recorded
                else "No AI eval baseline: run scripts/eval/run_eval.py and ship evals/reports/latest.json."
            ),
            "required_for": ["private_beta", "public_beta", "ga"],
            "details": {"report_path": report_path, "total_runs": total_runs},
        },
        {
            "key": "ai_quality_thresholds",
            "status": "pass" if thresholds_met else "fail",
            "message": (
                "AI quality metrics meet commercial thresholds."
                if thresholds_met
                else "AI quality metrics below commercial thresholds (see details.metrics vs details.thresholds)."
            ),
            "required_for": ["public_beta", "ga"],
            "details": {
                "metrics": (report or {}).get("metrics") or {},
                "thresholds": (report or {}).get("thresholds") or dict(THRESHOLDS),
            },
        },
    ]
