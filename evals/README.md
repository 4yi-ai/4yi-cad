# 4yi-cad Eval Harness (M0)

四层打分:L1 执行成功 / L2 几何有效(STEP header、STL watertight、FCStd 可加载)/
L3 site 场景符合度(role groups + 对象数)/ L4 人工评分(≥4/5)。
商用阈值见 `app/evals/report.py::THRESHOLDS`,与 spec
`docs/superpowers/specs/2026-08-03-ai-commercial-readiness-design.md` 一致。

## 跑一次真实基线(仅 x86_64 容器,手动)

```bash
docker build -f Dockerfile.freecad-gui -t 4yi-cad-eval .
docker run --rm -it \
  -e OPENAI_BASE_URL=<gateway>/api/v1 -e OPENAI_API_KEY=<token> -e TEXT_MODEL=<model> \
  -v "$PWD/evals:/srv/app/evals" 4yi-cad-eval bash -lc '
    pip install -r requirements-dev.txt &&
    python scripts/eval/run_eval.py --smoke              # 先 10-case smoke
    # python scripts/eval/run_eval.py --repeats 3        # 全量 84×3
  '
```

## 人工评分

```bash
python scripts/eval/review_sheet.py generate --report-dir evals/reports/<stamp>
# 填 review_sheet.csv 的 score(1-5)/notes 列
python scripts/eval/review_sheet.py ingest --report-dir evals/reports/<stamp>
```

## 发布基线

commit `evals/reports/latest.json` + `<stamp>/report.{json,md}`(不要 commit runs/ 产物)。
readiness 端点(`GET /api/production/readiness`)读 latest.json:
`ai_quality_baseline`(private_beta+)看 total_runs>0,`ai_quality_thresholds`
(public_beta/ga)看 thresholds_met。可用 `CAD_EVAL_REPORT_PATH` 覆盖路径。
部署镜像需在 baseline 落库后给 `Dockerfile` 增加 `COPY evals/reports/ /srv/app/evals/reports/`(或用 `CAD_EVAL_REPORT_PATH` 指向挂载的报告),否则容器内 readiness 的 `ai_quality_baseline` 永远 fail。
