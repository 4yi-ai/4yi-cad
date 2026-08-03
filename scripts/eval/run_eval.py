#!/usr/bin/env python3
"""Real-gateway eval runner (manual, x86_64 container only — never default CI).

Usage (inside the app container, gateway env injected):
  python scripts/eval/run_eval.py --smoke
  python scripts/eval/run_eval.py --repeats 3 --max-total-tokens 2000000
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.agent.loop import run_generation  # noqa: E402
from app.evals.corpus import EvalCase, load_corpus  # noqa: E402
from app.evals.report import aggregate, render_markdown, save_report  # noqa: E402
from app.evals.scoring import score_run  # noqa: E402

_ARTIFACT_FILENAMES = {
    "step": "model.step",
    "stl": "model.stl",
    "fcstd": "model.fcstd",
    "viewer_scene": "viewer_scene.json",
}


class EvalBudgetExceeded(RuntimeError):
    pass


class MeteredGateway:
    """Wraps a gateway; accumulates usage tokens and enforces a hard budget."""

    def __init__(self, inner, *, max_total_tokens: int | None = None):
        self._inner = inner
        self._max = max_total_tokens
        self.total_tokens = 0
        self.calls = 0

    async def chat_completion(self, messages, *, tools=None, tool_choice=None):
        if self._max is not None and self.total_tokens > self._max:
            raise EvalBudgetExceeded(
                f"token budget exhausted: {self.total_tokens} > {self._max}"
            )
        completion = await self._inner.chat_completion(
            messages, tools=tools, tool_choice=tool_choice
        )
        usage = (completion.raw or {}).get("usage") or {}
        used = int(usage.get("total_tokens") or 0)
        if self._max is not None and self.total_tokens + used > self._max:
            raise EvalBudgetExceeded(
                f"token budget exceeded: {self.total_tokens + used} > {self._max}"
            )
        self.total_tokens += used
        self.calls += 1
        return completion


def _write_artifact(art_dir: Path, fmt: str, data_b64: str) -> str:
    name = _ARTIFACT_FILENAMES.get(fmt, f"{fmt}.bin")
    path = art_dir / name
    try:
        path.write_bytes(base64.b64decode(data_b64))
    except Exception:  # noqa: BLE001 - keep the run alive on a corrupt artifact
        path.write_bytes(b"")
    return name


async def run_case(
    case: EvalCase,
    *,
    gateway,
    execute,
    execute_freecad,
    run_dir: Path,
    fcstd_check: bool = True,
) -> dict:
    art_dir = run_dir / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []
    started = time.monotonic()

    async def _consume() -> None:
        async for event in run_generation(
            case.prompt,
            gateway=gateway,
            execute=execute,
            execute_freecad=execute_freecad,
        ):
            slim = dict(event)
            if event.get("type") == "artifact":
                slim["file"] = _write_artifact(
                    art_dir, event.get("format") or "unknown", event.get("data_b64") or ""
                )
                slim.pop("data_b64", None)
            elif event.get("type") == "preview" and event.get("png_b64"):
                (art_dir / "preview.png").write_bytes(base64.b64decode(event["png_b64"]))
                slim.pop("png_b64", None)
            events.append(slim)

    try:
        await asyncio.wait_for(_consume(), timeout=case.timeout_s)
    except Exception as exc:  # noqa: BLE001 - a crashed run is a scored failure
        events.append({"type": "error", "message": f"runner: {exc}"})
        events.append({"type": "done", "ok": False})

    duration = time.monotonic() - started
    from app.evals.geometry import check_artifacts

    geometry = None
    done_ok = any(e.get("type") == "done" and e.get("ok") for e in events)
    if done_ok:
        geometry = check_artifacts(art_dir, fcstd_check=fcstd_check)
    score = score_run(case, events, run_dir, duration_s=duration, geometry=geometry)

    (run_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2)
    )
    tokens = {
        "total_tokens": getattr(gateway, "total_tokens", 0),
        "calls": getattr(gateway, "calls", 0),
    }
    return {
        "case_id": case.id,
        "domain": case.domain,
        "tier": case.tier,
        "prompt": case.prompt,
        "smoke": case.smoke,
        "rep": None,  # filled by main()
        "tokens": tokens,
        **asdict(score),
    }


def _select_cases(cases: list[EvalCase], args) -> list[EvalCase]:
    if args.case_id:
        cases = [c for c in cases if c.id == args.case_id]
    if args.tier:
        cases = [c for c in cases if c.tier == args.tier]
    if args.smoke:
        cases = [c for c in cases if c.smoke]
    return cases


async def _amain(args) -> int:
    from app.config import load_config
    from app.gateway import GatewayClient
    from app.main import default_execute, default_freecad_execute

    cfg = load_config()
    gateway = MeteredGateway(
        GatewayClient.from_config(cfg), max_total_tokens=args.max_total_tokens
    )
    cases = _select_cases(load_corpus(Path(args.corpus)), args)
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2

    reports_root = Path(args.out)
    stamp_dir = reports_root / "runs" / time.strftime("%Y%m%d-%H%M%S")
    records: list[dict] = []
    for case in cases:
        for rep in range(1, args.repeats + 1):
            run_dir = stamp_dir / case.id / f"rep{rep}"
            run_dir.mkdir(parents=True, exist_ok=True)
            tokens_before = gateway.total_tokens
            try:
                record = await run_case(
                    case,
                    gateway=gateway,
                    execute=default_execute,
                    execute_freecad=default_freecad_execute,
                    run_dir=run_dir,
                    fcstd_check=not args.no_fcstd_check,
                )
            except EvalBudgetExceeded as exc:
                print(f"STOP: {exc}", file=sys.stderr)
                break
            record["rep"] = rep
            record["tokens"] = {
                "total_tokens": gateway.total_tokens - tokens_before,
                "calls": gateway.calls,
            }
            records.append(record)
            status = "PASS" if record["l1_ok"] else "FAIL"
            print(f"[{status}] {case.id} rep{rep} attempts={record['attempts']} "
                  f"dur={record['duration_s']:.0f}s")
        else:
            continue
        break  # budget exhausted: stop outer loop too

    if not records:
        return 2
    report = aggregate(records)
    out = save_report(report, reports_root, records)
    print(render_markdown(report))
    print(f"report: {out / 'report.json'}\nlatest: {reports_root / 'latest.json'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(REPO_ROOT / "evals" / "cases"))
    parser.add_argument("--out", default=str(REPO_ROOT / "evals" / "reports"))
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--tier", choices=["t1", "t2", "t3"])
    parser.add_argument("--case-id")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-total-tokens", type=int, default=2_000_000)
    parser.add_argument("--no-fcstd-check", action="store_true")
    return asyncio.run(_amain(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
