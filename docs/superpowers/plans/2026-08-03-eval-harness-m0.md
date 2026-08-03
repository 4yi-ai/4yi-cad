# 4yi-cad M0 Eval Harness (Phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the eval corpus + runner + 4-layer scoring + report pipeline and wire an `ai_quality` gate into the production-readiness endpoint, so the AI→real-model pipeline has a measured commercial baseline (M0 of the approved spec `docs/superpowers/specs/2026-08-03-ai-commercial-readiness-design.md`).

**Architecture:** Eval library lives in `app/evals/` (corpus loader, geometry checks, scoring, report) so both the CLI runner (`scripts/eval/run_eval.py`) and the readiness endpoint can import it. Corpus data is YAML under `evals/cases/`. The runner drives the real `run_generation()` loop against the real gateway inside the x86_64 container; reports land in `evals/reports/` with a `latest.json` the readiness gate reads. Scoring layers: L1 execution success (loop events), L2 generic geometry validity (STEP header, STL watertight via trimesh, FCStd loadable via FreeCADCmd subprocess), L3 site-scene conformance (reuses the loop's role taxonomy), L4 human rubric (CSV sheet generate/ingest).

**Tech Stack:** Python 3.11+, FastAPI (existing), pytest, PyYAML + trimesh + numpy (dev/eval-only deps — NOT in the production image requirements).

## Global Constraints

- Follow the approved spec `docs/superpowers/specs/2026-08-03-ai-commercial-readiness-design.md`; thresholds verbatim: 生成成功率 ≥90%、几何有效率 ≥95%、FCStd 可加载 ≥95%、site 质量分达标率 ≥85%、人工评分 ≥4/5.
- Corpus size verbatim from spec: site-layout 60–80 (this plan: t1=24, t2=24, t3=16 → 64) + mechanical 20; smoke subset = 10 cases.
- `app/evals/report.py` must import **only stdlib** (it is imported by `app/main.py` at runtime; PyYAML/trimesh must never be imported by the production app). `app/evals/corpus.py` (yaml) and `app/evals/geometry.py` (trimesh) are imported only by scripts/tests.
- New deps go in `requirements-dev.txt` only: `pyyaml`, `trimesh`, `numpy`.
- Gateway env contract (read-only, existing): `OPENAI_BASE_URL`/`OPENAI_API_BASE`, `OPENAI_API_KEY`, `TEXT_MODEL` via `app.config.load_config()`. Never call api.openai.com.
- Real eval runs are manual, x86_64-container only; never wired into default CI/pytest (all pytest tests use fakes).
- Repo test command: `.venv/bin/python -m pytest tests/ -x -q` (adjust to plain `pytest` if no `.venv`). Run from repo root `/Users/yi.zhu/code/4yi-cad`.
- Commit after every task; work on branch `feat/eval-harness-m0` cut from `main`.

---

### Task 1: Eval case schema + corpus loader

**Files:**
- Create: `app/evals/__init__.py` (empty)
- Create: `app/evals/corpus.py`
- Create: `evals/cases/site_layout/t1/t1-001.yaml`, `evals/cases/site_layout/t2/t2-001.yaml`, `evals/cases/mechanical/m1-001.yaml` (samples; full corpus is Task 2)
- Modify: `requirements-dev.txt` (append `pyyaml`, `trimesh`, `numpy`)
- Test: `tests/test_eval_corpus.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces: `EvalCase` frozen dataclass with fields `id: str`, `domain: str` (`site_layout|mechanical`), `tier: str` (`t1|t2|t3`), `prompt: str`, `required_roles: tuple[str, ...]` (subset of `plot|building|water|play|amenity`), `min_objects: int | None`, `smoke: bool`, `timeout_s: int` (default 900); `load_case(path: Path) -> EvalCase`; `load_corpus(root: Path) -> list[EvalCase]` (sorted by id, raises `CorpusError` on duplicate ids/invalid fields).

- [ ] **Step 1: Add dev deps**

Append to `requirements-dev.txt`:

```
pyyaml
trimesh
numpy
```

Run: `.venv/bin/pip install -r requirements-dev.txt` → installs cleanly.

- [ ] **Step 2: Write the failing test**

`tests/test_eval_corpus.py`:

```python
from pathlib import Path

import pytest

from app.evals.corpus import CorpusError, EvalCase, load_case, load_corpus

CASE_YAML = """\
id: t1-999
domain: site_layout
tier: t1
smoke: true
prompt: "一个简单住宅组团:3栋6层板楼、一条环路、中心绿地"
required_roles: [plot, building]
min_objects: 10
timeout_s: 600
"""


def _write(tmp_path: Path, rel: str, text: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_load_case_parses_all_fields(tmp_path):
    path = _write(tmp_path, "t1/t1-999.yaml", CASE_YAML)
    case = load_case(path)
    assert case == EvalCase(
        id="t1-999",
        domain="site_layout",
        tier="t1",
        prompt="一个简单住宅组团:3栋6层板楼、一条环路、中心绿地",
        required_roles=("plot", "building"),
        min_objects=10,
        smoke=True,
        timeout_s=600,
    )


def test_load_case_defaults(tmp_path):
    path = _write(
        tmp_path,
        "m.yaml",
        'id: m1-999\ndomain: mechanical\ntier: t1\nprompt: "一个M8法兰"\n',
    )
    case = load_case(path)
    assert case.required_roles == ()
    assert case.min_objects is None
    assert case.smoke is False
    assert case.timeout_s == 900


@pytest.mark.parametrize(
    "bad",
    [
        "id: x\ndomain: nope\ntier: t1\nprompt: p\n",
        "id: x\ndomain: mechanical\ntier: t9\nprompt: p\n",
        "id: x\ndomain: site_layout\ntier: t1\nprompt: p\nrequired_roles: [castle]\n",
        "domain: mechanical\ntier: t1\nprompt: p\n",
        "id: x\ndomain: mechanical\ntier: t1\nprompt: ''\n",
    ],
)
def test_load_case_rejects_invalid(tmp_path, bad):
    path = _write(tmp_path, "bad.yaml", bad)
    with pytest.raises(CorpusError):
        load_case(path)


def test_load_corpus_sorted_and_unique(tmp_path):
    _write(tmp_path, "a/z.yaml", 'id: b-2\ndomain: mechanical\ntier: t1\nprompt: p\n')
    _write(tmp_path, "b/a.yaml", 'id: a-1\ndomain: mechanical\ntier: t1\nprompt: p\n')
    cases = load_corpus(tmp_path)
    assert [c.id for c in cases] == ["a-1", "b-2"]


def test_load_corpus_rejects_duplicate_ids(tmp_path):
    _write(tmp_path, "a.yaml", 'id: dup\ndomain: mechanical\ntier: t1\nprompt: p\n')
    _write(tmp_path, "b.yaml", 'id: dup\ndomain: mechanical\ntier: t1\nprompt: q\n')
    with pytest.raises(CorpusError):
        load_corpus(tmp_path)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_corpus.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.evals'`

- [ ] **Step 4: Write the implementation**

`app/evals/__init__.py`: empty file.

`app/evals/corpus.py`:

```python
"""Eval corpus loader.

Corpus cases are YAML files under evals/cases/. This module is imported only by
scripts and tests — never by the production app (it needs PyYAML, a dev-only dep).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

VALID_DOMAINS = {"site_layout", "mechanical"}
VALID_TIERS = {"t1", "t2", "t3"}
VALID_ROLES = {"plot", "building", "water", "play", "amenity"}
DEFAULT_TIMEOUT_S = 900


class CorpusError(ValueError):
    """Raised when a corpus case file is missing or malformed."""


@dataclass(frozen=True)
class EvalCase:
    id: str
    domain: str
    tier: str
    prompt: str
    required_roles: tuple[str, ...] = ()
    min_objects: int | None = None
    smoke: bool = False
    timeout_s: int = DEFAULT_TIMEOUT_S


def load_case(path: Path) -> EvalCase:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CorpusError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise CorpusError(f"{path}: case must be a YAML mapping")

    def _req_str(key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise CorpusError(f"{path}: missing or empty required field {key!r}")
        return value.strip()

    case_id = _req_str("id")
    domain = _req_str("domain")
    tier = _req_str("tier")
    prompt = _req_str("prompt")
    if domain not in VALID_DOMAINS:
        raise CorpusError(f"{path}: invalid domain {domain!r}")
    if tier not in VALID_TIERS:
        raise CorpusError(f"{path}: invalid tier {tier!r}")

    roles_raw = raw.get("required_roles") or []
    if not isinstance(roles_raw, list) or any(r not in VALID_ROLES for r in roles_raw):
        raise CorpusError(f"{path}: required_roles must be a list from {sorted(VALID_ROLES)}")

    min_objects = raw.get("min_objects")
    if min_objects is not None and (not isinstance(min_objects, int) or min_objects < 1):
        raise CorpusError(f"{path}: min_objects must be a positive integer")

    timeout_s = raw.get("timeout_s", DEFAULT_TIMEOUT_S)
    if not isinstance(timeout_s, int) or timeout_s < 1:
        raise CorpusError(f"{path}: timeout_s must be a positive integer")

    return EvalCase(
        id=case_id,
        domain=domain,
        tier=tier,
        prompt=prompt,
        required_roles=tuple(roles_raw),
        min_objects=min_objects,
        smoke=bool(raw.get("smoke", False)),
        timeout_s=timeout_s,
    )


def load_corpus(root: Path) -> list[EvalCase]:
    cases = [load_case(p) for p in sorted(root.rglob("*.yaml"))]
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise CorpusError(f"duplicate case id {case.id!r}")
        seen.add(case.id)
    return sorted(cases, key=lambda c: c.id)
```

Create the three sample YAML files:

`evals/cases/site_layout/t1/t1-001.yaml`:

```yaml
id: t1-001
domain: site_layout
tier: t1
smoke: true
prompt: "设计一个简单住宅组团:3栋6层板楼平行布置,一条环形车行道,中心集中绿地,场地地块60m×90m"
required_roles: [plot, building]
min_objects: 10
```

`evals/cases/site_layout/t2/t2-001.yaml`:

```yaml
id: t2-001
domain: site_layout
tier: t2
smoke: true
prompt: "设计一个混合社区总图:6栋18层高层住宅、南入口带门岗和落客区、中心人工湖水景、儿童游乐场、会所,环路+步道系统,地块140m×180m"
required_roles: [plot, building, water, play, amenity]
min_objects: 18
```

`evals/cases/mechanical/m1-001.yaml`:

```yaml
id: m1-001
domain: mechanical
tier: t1
smoke: true
prompt: "生成一个法兰盘:外径120mm,内孔直径40mm,厚度15mm,6个M10螺栓孔均布在直径90mm的圆周上"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_eval_corpus.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add app/evals/ evals/cases/ requirements-dev.txt tests/test_eval_corpus.py
git commit -m "feat(evals): eval case schema + corpus loader with sample cases"
```

---

### Task 2: Author the full 84-case corpus

**Files:**
- Create: `evals/cases/site_layout/t1/t1-001..t1-024.yaml` (24), `evals/cases/site_layout/t2/t2-001..t2-024.yaml` (24), `evals/cases/site_layout/t3/t3-001..t3-016.yaml` (16), `evals/cases/mechanical/m1-001..m1-014.yaml` (14 simple parts), `evals/cases/mechanical/m2-001..m2-006.yaml` (6 assemblies)
- Test: `tests/test_eval_corpus.py` (append completeness test)

**Interfaces:**
- Consumes: `load_corpus` from Task 1.
- Produces: the canonical corpus. Tier conventions: t1 site `min_objects: 10`, t2/t3 site `min_objects: 18` (matches the loop's sparse-scene gate); mechanical cases have `required_roles: []`, `min_objects: null`. Smoke subset = exactly 10 cases: `t1-001, t1-002, t1-003, t1-004, t2-001, t2-002, t3-001, m1-001, m1-002, m2-001`.

- [ ] **Step 1: Write the failing completeness test**

Append to `tests/test_eval_corpus.py`:

```python
CORPUS_ROOT = Path(__file__).resolve().parents[1] / "evals" / "cases"


def test_full_corpus_shape():
    cases = load_corpus(CORPUS_ROOT)
    by_tier_site = {
        t: [c for c in cases if c.domain == "site_layout" and c.tier == t]
        for t in ("t1", "t2", "t3")
    }
    mech = [c for c in cases if c.domain == "mechanical"]
    assert len(by_tier_site["t1"]) == 24
    assert len(by_tier_site["t2"]) == 24
    assert len(by_tier_site["t3"]) == 16
    assert len(mech) == 20
    smoke = [c.id for c in cases if c.smoke]
    assert sorted(smoke) == sorted(
        ["t1-001", "t1-002", "t1-003", "t1-004", "t2-001", "t2-002",
         "t3-001", "m1-001", "m1-002", "m2-001"]
    )
    for c in cases:
        if c.domain == "site_layout":
            assert "plot" in c.required_roles and "building" in c.required_roles
            assert c.min_objects == (10 if c.tier == "t1" else 18)
```

Run: `.venv/bin/python -m pytest tests/test_eval_corpus.py::test_full_corpus_shape -q`
Expected: FAIL (counts wrong — only samples exist)

- [ ] **Step 2: Author the cases from this coverage matrix**

Each row becomes one YAML file (format identical to Task 1 samples; site cases get `required_roles` = `[plot, building]` plus the roles listed; mechanical omit roles/min_objects). Prompts are the 中文 one-liners below, each expanded with concrete dimensions (地块尺寸/楼层数/直径等 — pick reasonable values, every prompt must be fully self-contained and unambiguous).

**T1(24)— 简单住宅组团,每案 3–5 楼栋 + 道路/绿化基础要素**(extra roles per case listed):
| id | 主题 | extra roles |
|---|---|---|
| t1-001 | 3栋6层板楼+环路+中心绿地 | — |
| t1-002 | 4栋点式小高层+十字路网 | — |
| t1-003 | 联排别墅两排+尽端路 | — |
| t1-004 | 合院组团+围墙+南大门 | amenity |
| t1-005 | 5栋板楼行列式+宅间绿化 | — |
| t1-006 | L形布局4栋+转角绿地 | — |
| t1-007 | 3栋板楼+地面停车场 | — |
| t1-008 | 点式+板式混合5栋 | — |
| t1-009 | 别墅6栋+环形车道 | — |
| t1-010 | 4栋6层+中心儿童活动场 | play |
| t1-011 | 3栋高层点式+入口广场 | amenity |
| t1-012 | 行列式5栋+消防登高面 | — |
| t1-013 | 板楼3栋+小型水景池 | water |
| t1-014 | 4栋+围墙+两个出入口 | amenity |
| t1-015 | 联排8户+步行巷道 | — |
| t1-016 | 3栋错列布置+斜向道路 | — |
| t1-017 | 5栋+集中草坪+树阵 | — |
| t1-018 | 4栋小高层+南北主路 | — |
| t1-019 | 别墅4栋+私家花园分块 | — |
| t1-020 | 3栋+自行车棚+垃圾房 | amenity |
| t1-021 | 6栋多层行列式+两条平行路 | — |
| t1-022 | 点式3栋+三角形地块 | — |
| t1-023 | 4栋+中心凉亭+环形步道 | amenity |
| t1-024 | 板楼3栋+东西向道路+北侧绿带 | — |

**T2(24)— 混合社区,每案 6–10 楼栋 + ≥3 类配套要素**:
| id | 主题 | extra roles |
|---|---|---|
| t2-001 | 高层6栋+人工湖+儿童场+会所(见 Task 1 样例) | water, play, amenity |
| t2-002 | 8栋高层+商业裙房+中心广场 | amenity |
| t2-003 | 高层+洋房混合+泳池+健身场 | water, play |
| t2-004 | 6栋+幼儿园+老年活动中心 | play, amenity |
| t2-005 | 围合式布局+内庭水景+门岗 | water, amenity |
| t2-006 | 7栋+溪流水系贯穿+木栈道 | water |
| t2-007 | 高层8栋+双入口+地库出入口 | amenity |
| t2-008 | 板式高层+风雨连廊+会所 | amenity |
| t2-009 | 6栋+篮球场+儿童沙坑 | play |
| t2-010 | 洋房+小高层+中央草坪+旱喷 | water, play |
| t2-011 | 8栋+沿街商业+公交港湾 | amenity |
| t2-012 | 高层6栋+雨水花园+慢跑道 | water |
| t2-013 | 混合7栋+宠物公园+休闲亭 | play, amenity |
| t2-014 | 6栋围合两院+连桥 | amenity |
| t2-015 | 高层+叠拼+中心湖+环湖步道 | water |
| t2-016 | 8栋+南北双会所 | amenity |
| t2-017 | 7栋+儿童三龄段活动区 | play |
| t2-018 | 6栋+屋顶花园标注+入口水景 | water, amenity |
| t2-019 | 高层8栋+消防环路+登高场地 | — |
| t2-020 | 6栋+架空层活动区+泳池 | water, play |
| t2-021 | 7栋+沿河绿带+亲水平台 | water |
| t2-022 | 混合8栋+集中商业+下沉广场 | amenity |
| t2-023 | 6栋+网球场+儿童攀爬区 | play |
| t2-024 | 高层7栋+双水景轴线 | water |

**T3(16)— 复杂总图:滨水/高差/超高层/多期/大配套**:
| id | 主题 | extra roles |
|---|---|---|
| t3-001 | 滨湖高档社区:10栋高层+湖岸线+游艇码头+会所群 | water, play, amenity |
| t3-002 | 坡地社区:三级台地+挡墙+错层楼栋 | amenity |
| t3-003 | 超高层2栋+高层6栋+裙房商业综合体 | amenity |
| t3-004 | 两期开发:一期洋房+二期高层+共享中央公园 | play, amenity |
| t3-005 | 学区社区:12栋+九年制学校+运动场 | play, amenity |
| t3-006 | 河道穿越地块:两岸楼栋+三座桥+滨河步道 | water |
| t3-007 | TOD社区:地铁站点+公交枢纽+高层群 | amenity |
| t3-008 | 山地别墅群:等高线台地+组团式布局 | — |
| t3-009 | 康养社区:医院+适老住宅+疗愈花园 | play, amenity |
| t3-010 | 10栋+环形中央湖+五类主题园 | water, play |
| t3-011 | 混合用地:住宅+办公塔楼+酒店 | amenity |
| t3-012 | 古镇风貌区:低层院落群+水街 | water, amenity |
| t3-013 | 12栋高层+三大组团+组团级会所×3 | amenity |
| t3-014 | 海绵城市示范:湿地+植草沟+调蓄塘 | water |
| t3-015 | 体育主题社区:标准田径场+泳池馆 | water, play, amenity |
| t3-016 | 超大盘:16栋+双商业中心+三入口 | amenity |

**Mechanical m1(14)— 单零件(CadQuery 路由)**: m1-001 法兰盘(样例)、m1-002 带键槽传动轴、m1-003 直角安装支架带加强筋、m1-004 六角螺栓M12、m1-005 深沟球轴承座、m1-006 齿轮泵端盖、m1-007 T型槽工作台板、m1-008 圆柱齿轮(模数2,20齿,简化齿形)、m1-009 管道三通接头、m1-010 散热片阵列块、m1-011 蜗轮箱盖板带观察孔、m1-012 V带轮双槽、m1-013 十字联轴器半体、m1-014 薄壁机箱外壳带安装耳。

**Mechanical m2(6)— 装配体(命中 mechanical-assembly hint → FreeCAD 路由)**: m2-001 铰链装配(两页+销轴)、m2-002 液压缸简化装配(缸体+活塞杆+耳环)、m2-003 脚轮装配(支架+轮+轴)、m2-004 连杆机构(曲柄+连杆+滑块)、m2-005 法兰对接装配(两法兰+垫片+4螺栓)、m2-006 起落架简化装配(支柱+轮+扭力臂)。

- [ ] **Step 3: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_eval_corpus.py -q`
Expected: PASS (incl. `test_full_corpus_shape`)

- [ ] **Step 4: Commit**

```bash
git add evals/cases/ tests/test_eval_corpus.py
git commit -m "feat(evals): full 84-case corpus (64 site-layout + 20 mechanical, 10 smoke)"
```

---

### Task 3: L2 generic geometry checks

**Files:**
- Create: `app/evals/geometry.py`
- Test: `tests/test_eval_geometry.py`

**Interfaces:**
- Consumes: nothing from other tasks (operates on files in a run's `artifacts/` dir).
- Produces: `GeometryReport` dataclass with `step_valid: bool | None`, `stl_watertight: bool | None`, `stl_volume_mm3: float | None`, `fcstd_loadable: bool | None`, `issues: list[str]`, and property `ok -> bool | None` (None = nothing checkable; True only if all non-None checks pass and volume > 1e-9 when present). Functions: `check_artifacts(artifacts_dir: Path, *, fcstd_check: bool = True) -> GeometryReport`; artifact filenames are the Task 6 runner convention: `model.step`, `model.stl`, `model.fcstd`.

- [ ] **Step 1: Write the failing test**

`tests/test_eval_geometry.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_geometry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.evals.geometry'`

- [ ] **Step 3: Write the implementation**

`app/evals/geometry.py`:

```python
"""L2 generic geometry checks on eval run artifacts.

Engine-agnostic: operates on exported files (STEP header, STL watertightness via
trimesh, FCStd loadability via a FreeCADCmd subprocess). Imported only by
scripts/tests — trimesh is a dev-only dependency.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

FCSTD_LOAD_TIMEOUT_S = 120
_MIN_VOLUME_MM3 = 1e-9


@dataclass
class GeometryReport:
    step_valid: bool | None = None
    stl_watertight: bool | None = None
    stl_volume_mm3: float | None = None
    fcstd_loadable: bool | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool | None:
        checks = [c for c in (self.step_valid, self.stl_watertight, self.fcstd_loadable) if c is not None]
        if not checks:
            return None
        if self.stl_volume_mm3 is not None and self.stl_volume_mm3 <= _MIN_VOLUME_MM3:
            return False
        return all(checks)


def _check_step(path: Path, report: GeometryReport) -> None:
    head = path.read_bytes()[:64].lstrip()
    report.step_valid = head.startswith(b"ISO-10303-21")
    if not report.step_valid:
        report.issues.append(f"{path.name}: missing ISO-10303-21 STEP header")


def _check_stl(path: Path, report: GeometryReport) -> None:
    import trimesh

    try:
        mesh = trimesh.load(str(path), force="mesh")
    except Exception as exc:  # noqa: BLE001 - any load failure is a finding
        report.stl_watertight = False
        report.issues.append(f"{path.name}: unloadable mesh: {exc}")
        return
    report.stl_watertight = bool(mesh.is_watertight)
    report.stl_volume_mm3 = abs(float(mesh.volume)) if mesh.is_watertight else None
    if not mesh.is_watertight:
        report.issues.append(f"{path.name}: mesh is not watertight")
    elif report.stl_volume_mm3 is not None and report.stl_volume_mm3 <= _MIN_VOLUME_MM3:
        report.issues.append(f"{path.name}: ~zero enclosed volume")


def _check_fcstd(path: Path, report: GeometryReport) -> None:
    freecadcmd = shutil.which("FreeCADCmd") or shutil.which("freecadcmd")
    if not freecadcmd:
        report.issues.append("FreeCADCmd not available; fcstd load check skipped")
        return
    script = (
        "import sys, FreeCAD\n"
        f"doc = FreeCAD.open({str(path)!r})\n"
        "sys.exit(0 if doc and len(doc.Objects) >= 0 else 1)\n"
    )
    try:
        proc = subprocess.run(
            [freecadcmd, "-c", script],
            capture_output=True,
            timeout=FCSTD_LOAD_TIMEOUT_S,
        )
        report.fcstd_loadable = proc.returncode == 0
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")[-500:]
            report.issues.append(f"{path.name}: FreeCAD.open failed: {tail}")
    except subprocess.TimeoutExpired:
        report.fcstd_loadable = False
        report.issues.append(f"{path.name}: FreeCAD.open timed out after {FCSTD_LOAD_TIMEOUT_S}s")


def check_artifacts(artifacts_dir: Path, *, fcstd_check: bool = True) -> GeometryReport:
    report = GeometryReport()
    step = artifacts_dir / "model.step"
    stl = artifacts_dir / "model.stl"
    fcstd = artifacts_dir / "model.fcstd"
    if step.exists():
        _check_step(step, report)
    if stl.exists():
        _check_stl(stl, report)
    if fcstd.exists() and fcstd_check:
        _check_fcstd(fcstd, report)
    if report.step_valid is None and report.stl_watertight is None and report.fcstd_loadable is None:
        if not report.issues:
            report.issues.append("no checkable artifacts found")
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_eval_geometry.py -q`
Expected: PASS (all 5)

- [ ] **Step 5: Commit**

```bash
git add app/evals/geometry.py tests/test_eval_geometry.py
git commit -m "feat(evals): L2 generic geometry checks (STEP/STL/FCStd)"
```

---

### Task 4: Public scene-role helpers + run scoring

**Files:**
- Modify: `app/agent/loop.py` (add two public helpers near `_site_requested_role_groups`, no behavior change)
- Create: `app/evals/scoring.py`
- Test: `tests/test_eval_scoring.py`

**Interfaces:**
- Consumes: `EvalCase` (Task 1), `GeometryReport`/`check_artifacts` (Task 3), loop event dicts (`type: status|script|retry|preview|artifact|error|done`).
- Produces: in `app/agent/loop.py`: `SITE_ROLE_GROUPS = _SITE_ROLE_GROUPS` and `def scene_role_set(scene: dict) -> set[str]`. In `app/evals/scoring.py`: `RunScore` dataclass (`l1_ok: bool`, `l2_ok: bool | None`, `l3_ok: bool | None`, `attempts: int`, `retries: int`, `duration_s: float`, `error: str | None`, `details: dict`) and `score_run(case, events, run_dir, *, duration_s, geometry=None) -> RunScore` (geometry injectable for tests; defaults to `check_artifacts(run_dir / "artifacts")`). L3 reads `run_dir / "artifacts" / "viewer_scene.json"`.

- [ ] **Step 1: Write the failing test**

`tests/test_eval_scoring.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_scoring.py -q`
Expected: FAIL — `ImportError: cannot import name 'scene_role_set' from 'app.agent.loop'`

- [ ] **Step 3: Write the implementation**

In `app/agent/loop.py`, directly after the `_SITE_ROLE_GROUPS = {...}` block, add:

```python
# Public aliases for eval scoring (app/evals/scoring.py) — same taxonomy the
# in-loop site-layout quality gate uses.
SITE_ROLE_GROUPS = _SITE_ROLE_GROUPS


def scene_role_set(scene: dict) -> set[str]:
    objects = scene.get("objects") if isinstance(scene.get("objects"), list) else []
    return {_role_for_scene_object(obj) for obj in objects if isinstance(obj, dict)}
```

Create `app/evals/scoring.py`:

```python
"""Per-run scoring: L1 execution, L2 geometry, L3 site-scene conformance.

L4 (human rubric) is ingested at report level, not per run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.loop import SITE_ROLE_GROUPS, scene_role_set
from app.evals.corpus import EvalCase
from app.evals.geometry import GeometryReport, check_artifacts


@dataclass
class RunScore:
    l1_ok: bool
    l2_ok: bool | None
    l3_ok: bool | None
    attempts: int
    retries: int
    duration_s: float
    error: str | None
    details: dict = field(default_factory=dict)


def _load_scene(run_dir: Path) -> dict | None:
    path = run_dir / "artifacts" / "viewer_scene.json"
    if not path.exists():
        return None
    try:
        scene = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return scene if isinstance(scene, dict) else None


def _score_l3(case: EvalCase, run_dir: Path, details: dict) -> bool | None:
    if case.domain != "site_layout":
        return None
    scene = _load_scene(run_dir)
    if scene is None:
        details["l3_missing_roles"] = list(case.required_roles)
        details["l3_reason"] = "no viewer_scene artifact"
        return False
    objects = scene.get("objects") if isinstance(scene.get("objects"), list) else []
    roles = scene_role_set(scene)
    missing = [
        group
        for group in case.required_roles
        if not (roles & SITE_ROLE_GROUPS.get(group, {group}))
    ]
    details["l3_object_count"] = len(objects)
    details["l3_roles_found"] = sorted(roles)
    details["l3_missing_roles"] = missing
    if case.min_objects is not None and len(objects) < case.min_objects:
        details["l3_reason"] = f"sparse scene: {len(objects)} < {case.min_objects}"
        return False
    return not missing


def score_run(
    case: EvalCase,
    events: list[dict],
    run_dir: Path,
    *,
    duration_s: float,
    geometry: GeometryReport | None = None,
) -> RunScore:
    done = next((e for e in reversed(events) if e.get("type") == "done"), {})
    l1_ok = bool(done.get("ok"))
    attempts = sum(1 for e in events if e.get("type") == "script")
    retries = sum(1 for e in events if e.get("type") == "retry")
    error = next(
        (e.get("message") for e in reversed(events) if e.get("type") == "error"), None
    )

    details: dict = {"engine": done.get("engine")}
    l2_ok: bool | None = None
    l3_ok: bool | None = None
    if l1_ok:
        geometry = geometry if geometry is not None else check_artifacts(run_dir / "artifacts")
        l2_ok = geometry.ok
        details["geometry"] = {
            "step_valid": geometry.step_valid,
            "stl_watertight": geometry.stl_watertight,
            "stl_volume_mm3": geometry.stl_volume_mm3,
            "fcstd_loadable": geometry.fcstd_loadable,
            "issues": geometry.issues,
        }
        l3_ok = _score_l3(case, run_dir, details)

    return RunScore(
        l1_ok=l1_ok,
        l2_ok=l2_ok,
        l3_ok=l3_ok,
        attempts=attempts,
        retries=retries,
        duration_s=duration_s,
        error=None if l1_ok else error,
        details=details,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_eval_scoring.py tests/test_loop.py -q`
Expected: PASS (new tests + all existing loop tests still green)

- [ ] **Step 5: Commit**

```bash
git add app/agent/loop.py app/evals/scoring.py tests/test_eval_scoring.py
git commit -m "feat(evals): run scoring (L1/L2/L3) + public scene-role helpers"
```

---

### Task 5: Report aggregation, thresholds, persistence

**Files:**
- Create: `app/evals/report.py` (STDLIB-ONLY imports — this module is imported by `app/main.py` in Task 6)
- Test: `tests/test_eval_report.py`

**Interfaces:**
- Consumes: record dicts produced by the runner (Task 6): each has `case_id, domain, tier, rep, l1_ok, l2_ok, l3_ok, attempts, retries, duration_s, error, details` (details may contain `geometry.fcstd_loadable`).
- Produces:
  - `THRESHOLDS = {"success_rate": 0.90, "geometry_valid_rate": 0.95, "fcstd_loadable_rate": 0.95, "site_quality_pass_rate": 0.85, "human_review_mean": 4.0}`
  - `aggregate(records: list[dict]) -> dict` — report dict `{schema: "4yi-cad.eval_report.v1", generated_at, total_runs, metrics: {success_rate, geometry_valid_rate, fcstd_loadable_rate, site_quality_pass_rate, human_review_mean}, thresholds, thresholds_met, by_tier, top_failures, fcstd_checks_skipped}`
  - `evaluate_thresholds(metrics: dict) -> bool` (None metric → False)
  - `save_report(report: dict, reports_root: Path, records: list[dict]) -> Path` — writes `<stamp>/report.json`, `<stamp>/records.jsonl`, `<stamp>/report.md`, and copies report.json to `reports_root/latest.json`
  - `load_latest_eval_report(path: str | Path | None = None) -> dict | None` — env `CAD_EVAL_REPORT_PATH` → default `<repo>/evals/reports/latest.json`; None on missing/unparseable
  - `default_report_path() -> Path`
  - `render_markdown(report: dict) -> str`
  - `build_ai_quality_checks(report: dict | None, *, report_path: str) -> list[dict]` — two check dicts in the exact `_release_check` shape (`key/status/message/required_for/details`): `ai_quality_baseline` (pass iff `total_runs > 0`; `required_for: ["private_beta", "public_beta", "ga"]`) and `ai_quality_thresholds` (pass iff `thresholds_met`; `required_for: ["public_beta", "ga"]`).

- [ ] **Step 1: Write the failing test**

`tests/test_eval_report.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_report.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.evals.report'`

- [ ] **Step 3: Write the implementation**

`app/evals/report.py`:

```python
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


def evaluate_thresholds(metrics: dict) -> bool:
    for key, floor in THRESHOLDS.items():
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
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    with (out / "records.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (reports_root / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_eval_report.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/evals/report.py tests/test_eval_report.py
git commit -m "feat(evals): report aggregation, thresholds, latest.json + ai_quality check builders"
```

---

### Task 6: Wire `ai_quality` checks into production readiness

**Files:**
- Modify: `app/main.py` (import + 3-line insertion in `_production_readiness_report`, around line 1183 where the `checks` list literal closes)
- Test: `tests/test_readiness_ai_quality.py`

**Interfaces:**
- Consumes: `build_ai_quality_checks`, `load_latest_eval_report`, `default_report_path` (Task 5); existing `_production_readiness_report` / `create_app` / `GET /api/production/readiness`.
- Produces: readiness response now contains checks `ai_quality_baseline` + `ai_quality_thresholds`; `release_targets` semantics: private_beta additionally requires a recorded baseline; public_beta/ga additionally require thresholds met. No other check changes.

- [ ] **Step 1: Write the failing test**

`tests/test_readiness_ai_quality.py`:

```python
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
```

Note: if `GET /api/production/readiness` nests checks under a different key than `checks`, read the actual response shape from the existing readiness tests in `tests/test_main.py` (around line 249) and adjust `_check` accordingly — do not change the endpoint's existing shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_readiness_ai_quality.py -q`
Expected: FAIL — `StopIteration` in `_check` (keys absent)

- [ ] **Step 3: Implement the wiring**

In `app/main.py` imports, add:

```python
from app.evals.report import (
    build_ai_quality_checks,
    default_report_path,
    load_latest_eval_report,
)
```

In `_production_readiness_report`, immediately after the `checks = [...]` list literal closes (after the `license_gate` entry, before `summary = {...}`), add:

```python
    checks.extend(
        build_ai_quality_checks(
            load_latest_eval_report(),
            report_path=str(default_report_path()),
        )
    )
```

- [ ] **Step 4: Run tests to verify they pass — including existing readiness tests**

Run: `.venv/bin/python -m pytest tests/test_readiness_ai_quality.py tests/test_main.py -q`
Expected: new tests PASS. If any existing `test_main.py` readiness test fails because it asserts an exact set/count of check keys or `private_beta_ready is True`, update ONLY those assertions to include the two new keys / set `CAD_EVAL_REPORT_PATH` to a baseline fixture in that test — the endpoint shape must not change otherwise.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_readiness_ai_quality.py tests/test_main.py
git commit -m "feat(readiness): ai_quality_baseline + ai_quality_thresholds gates from eval report"
```

---

### Task 7: Eval runner (metered gateway + run_case + CLI)

**Files:**
- Create: `scripts/eval/__init__.py` (empty), `scripts/eval/run_eval.py`
- Test: `tests/test_eval_runner.py`

**Interfaces:**
- Consumes: `run_generation` + `ExecResult` (app.agent.loop), `GatewayClient`/`load_config`, `load_corpus`/`EvalCase`, `score_run`, `aggregate`/`save_report`, and at CLI-time `default_execute`/`default_freecad_execute` from `app.main`.
- Produces: `MeteredGateway` (wraps any gateway; counts `usage` tokens from `ChatCompletion.raw`; raises `EvalBudgetExceeded` when `total_tokens > max_total_tokens`); `async run_case(case, *, gateway, execute, execute_freecad, run_dir) -> dict` (a record dict per Task 5's consumed shape, artifacts decoded to `run_dir/artifacts/` with filenames `model.step`, `model.stl`, `model.fcstd`, `viewer_scene.json`, `preview.png`, other formats `<fmt>.bin`); CLI `python scripts/eval/run_eval.py --corpus evals/cases --out evals/reports [--smoke] [--tier t1] [--case-id ID] [--repeats 3] [--max-total-tokens 2000000] [--no-fcstd-check]`.

- [ ] **Step 1: Write the failing test**

`tests/test_eval_runner.py`:

```python
import base64
import json

import pytest

from app.agent.loop import ExecResult
from app.evals.corpus import EvalCase
from app.gateway import ChatCompletion
from scripts.eval.run_eval import EvalBudgetExceeded, MeteredGateway, run_case

TETRA_STL_B64 = base64.b64encode(
    b"solid t\nfacet normal 0 0 -1\n outer loop\n  vertex 0 0 0\n  vertex 0 1 0\n"
    b"  vertex 1 0 0\n endloop\nendfacet\nfacet normal 0 -1 0\n outer loop\n"
    b"  vertex 0 0 0\n  vertex 1 0 0\n  vertex 0 0 1\n endloop\nendfacet\n"
    b"facet normal -1 0 0\n outer loop\n  vertex 0 0 0\n  vertex 0 0 1\n"
    b"  vertex 0 1 0\n endloop\nendfacet\nfacet normal 1 1 1\n outer loop\n"
    b"  vertex 1 0 0\n  vertex 0 1 0\n  vertex 0 0 1\n endloop\nendfacet\nendsolid t\n"
).decode()
STEP_B64 = base64.b64encode(b"ISO-10303-21;\nENDSEC;\nEND-ISO-10303-21;\n").decode()


class ScriptedGateway:
    def __init__(self, usage_per_call=100):
        self.calls = 0
        self._usage = usage_per_call

    async def chat_completion(self, messages, *, tools=None, tool_choice=None):
        self.calls += 1
        return ChatCompletion(
            content=None,
            tool_calls=[{
                "id": f"call-{self.calls}",
                "function": {"name": "run_cadquery",
                             "arguments": json.dumps({"script": "result = 1"})},
            }],
            raw={"usage": {"total_tokens": self._usage}},
        )


async def ok_execute(script: str) -> ExecResult:
    return ExecResult(ok=True, exports={"stl": TETRA_STL_B64, "step": STEP_B64})


CASE = EvalCase(id="m1-t", domain="mechanical", tier="t1", prompt="一个法兰", timeout_s=30)


@pytest.mark.anyio
async def test_run_case_success_writes_artifacts_and_record(tmp_path):
    record = await run_case(
        CASE,
        gateway=ScriptedGateway(),
        execute=ok_execute,
        execute_freecad=None,
        run_dir=tmp_path,
    )
    assert record["case_id"] == "m1-t"
    assert record["l1_ok"] is True
    assert record["l2_ok"] is True
    assert (tmp_path / "artifacts" / "model.stl").exists()
    assert (tmp_path / "artifacts" / "model.step").exists()
    assert record["tokens"]["total_tokens"] == 0  # plain gateway: no meter attached


@pytest.mark.anyio
async def test_metered_gateway_budget(tmp_path):
    metered = MeteredGateway(ScriptedGateway(usage_per_call=600), max_total_tokens=1000)
    await metered.chat_completion([])
    with pytest.raises(EvalBudgetExceeded):
        await metered.chat_completion([])
    assert metered.total_tokens == 600


@pytest.mark.anyio
async def test_run_case_survives_executor_crash(tmp_path):
    async def boom(script: str) -> ExecResult:
        raise RuntimeError("worker exploded")

    record = await run_case(
        CASE, gateway=ScriptedGateway(), execute=boom, execute_freecad=None,
        run_dir=tmp_path,
    )
    assert record["l1_ok"] is False
    assert "worker exploded" in (record["error"] or "")
```

If the repo's pytest is not configured for `anyio`, use the same async-test mechanism `tests/test_loop.py` already uses (check its decorators) — mirror it exactly.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.eval.run_eval'` (create `scripts/__init__.py` too if scripts/ is not yet a package)

- [ ] **Step 3: Write the implementation**

`scripts/eval/run_eval.py`:

```python
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
        if self._max is not None and self.total_tokens >= self._max:
            raise EvalBudgetExceeded(
                f"token budget exhausted: {self.total_tokens} >= {self._max}"
            )
        completion = await self._inner.chat_completion(
            messages, tools=tools, tool_choice=tool_choice
        )
        usage = (completion.raw or {}).get("usage") or {}
        self.total_tokens += int(usage.get("total_tokens") or 0)
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
```

Also create empty `scripts/__init__.py` and `scripts/eval/__init__.py` so tests can import the module.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_eval_runner.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/eval/ tests/test_eval_runner.py
git commit -m "feat(evals): real-gateway eval runner with token budget and artifact capture"
```

---

### Task 8: L4 human review sheet (generate + ingest)

**Files:**
- Create: `scripts/eval/review_sheet.py`
- Test: `tests/test_eval_review_sheet.py`

**Interfaces:**
- Consumes: a report dir produced by `save_report` (`report.json`, `records.jsonl`) and the runner's per-run `artifacts/` dirs; `evaluate_thresholds`, `render_markdown` (Task 5).
- Produces: `generate_review_sheet(report_dir: Path, runs_root: Path, sample_size: int = 15) -> Path` — writes `review_sheet.csv` (columns `case_id,rep,tier,prompt,artifacts_dir,score,notes`; deterministic sample via `random.Random(0)`, only `l1_ok` records, spread over tiers); `ingest_reviews(report_dir: Path, reports_root: Path) -> dict` — reads filled `score` column (ints 1–5; blank rows skipped; raises `ValueError` on out-of-range), sets `metrics.human_review_mean`, recomputes `thresholds_met` via `evaluate_thresholds`, rewrites `report.json`, `report.md`, and `reports_root/latest.json`, returns the updated report. CLI: `python scripts/eval/review_sheet.py generate|ingest --report-dir <dir> --reports-root evals/reports`.

- [ ] **Step 1: Write the failing test**

`tests/test_eval_review_sheet.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_review_sheet.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.eval.review_sheet'`

- [ ] **Step 3: Write the implementation**

`scripts/eval/review_sheet.py`:

```python
#!/usr/bin/env python3
"""L4 human-review sheet: sample successful runs into a CSV, ingest scores back."""

from __future__ import annotations

import argparse
import csv
import json
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
            writer.writerow({
                "case_id": record.get("case_id"),
                "rep": record.get("rep"),
                "tier": record.get("tier"),
                "prompt": record.get("prompt", ""),
                "artifacts_dir": str(
                    runs_root / "runs" / "*" / str(record.get("case_id"))
                    / f"rep{record.get('rep')}" / "artifacts"
                ),
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
    report["thresholds_met"] = evaluate_thresholds(report["metrics"])
    (report_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    (report_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (reports_root / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
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
```

Note: `generate_review_sheet` requires records to carry `prompt` — add `"prompt": case.prompt,` to the record dict in `scripts/eval/run_eval.py::run_case` (one-line addition) and to its test's expected keys.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_eval_review_sheet.py tests/test_eval_runner.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/eval/review_sheet.py scripts/eval/run_eval.py tests/
git commit -m "feat(evals): L4 human review sheet generate/ingest"
```

---

### Task 9: Runbook, gitignore, full-suite green, baseline instructions

**Files:**
- Create: `evals/README.md`
- Create: `evals/reports/.gitignore`
- Modify: none

**Interfaces:**
- Consumes: everything above.
- Produces: the operational runbook; repo hygiene (run artifacts never committed; reports/latest.json committed).

- [ ] **Step 1: Write `evals/reports/.gitignore`**

```
runs/
*/records.jsonl
```

(`latest.json`, `*/report.json`, `*/report.md`, `*/review_sheet.csv` stay committable — the readiness gate reads `latest.json` baked into the image.)

- [ ] **Step 2: Write `evals/README.md`**

```markdown
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
```

- [ ] **Step 3: Run the entire test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — zero failures (pre-existing suite + all new eval tests).

- [ ] **Step 4: Commit**

```bash
git add evals/README.md evals/reports/.gitignore
git commit -m "docs(evals): runbook + report hygiene"
```

- [ ] **Step 5: Manual baseline (operator step — NOT automatable here)**

Follow `evals/README.md` on an x86_64 host with real gateway env: smoke run first, then full 84×3. Then generate + ingest the human review sheet, commit `latest.json`, and verify `GET /api/production/readiness` shows `ai_quality_baseline: pass`. This closes M0; the failure taxonomy in `report.md` seeds the Phase 1 plan.

---

## Self-Review

- **Spec coverage:** corpus 64+20 with tiers ✓ (Task 2); runner real-gateway/x86_64/成本上限/超时/smoke 子集 ✓ (Task 7); L1–L4 ✓ (Tasks 3/4/8); 阈值 verbatim ✓ (Task 5); `ai_quality` gate private_beta=baseline、public_beta/ga=thresholds ✓ (Tasks 5/6); 报告 JSON+Markdown+历史留存 ✓ (Task 5/9). Phase 1/2/3 and W1–W3 are intentionally separate plans per scope check.
- **Placeholders:** none — every code step has full code; the two "mirror existing test mechanism" notes point at concrete files/lines to copy, with the default written out.
- **Type consistency:** `EvalCase` fields, artifact filenames (`model.step/model.stl/model.fcstd/viewer_scene.json`), record dict keys (`case_id/domain/tier/rep/l1_ok/l2_ok/l3_ok/attempts/retries/duration_s/error/details/tokens/prompt/smoke`), and `build_ai_quality_checks` shape are used identically across Tasks 3–8.
