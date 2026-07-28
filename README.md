# 4yi-cad

AI CAD app: **prompt → parametric 3D model → preview → STEP/STL export → iterative refine**.
Packaged as a **4yi dedicated app** — a standalone repo the 4yi platform builds
(CodeBuild → ECR) and installs per-consumer-org into EKS, injecting the LLM gateway.

## Architecture (MVP)

Single FastAPI process, single origin, single container.

- `app/config.py` — reads the injected gateway contract (`OPENAI_BASE_URL`,
  `OPENAI_API_KEY`, `TEXT_MODEL`, `PORT`); fail-fast, no `api.openai.com` fallback.
- `app/gateway.py` — OpenAI-compatible client → `${OPENAI_BASE_URL}/chat/completions`
  (tool-calling; `/responses` is not used).
- `app/agent/loop.py` — prompt → CAD tool call (`run_cadquery` or `run_freecad`) →
  sandboxed execute → streamed events with retry/self-correction.
- `app/cad/runner.py` — **sandbox**: scrubbed env (no gateway token), CPU/mem
  rlimits, wall-clock deadline. Runs the worker in an isolated subprocess.
- `app/cad/worker.py` — execs the CadQuery script, exports STEP+STL, renders a
  preview PNG. Untrusted-code-facing; the container is the isolation boundary.
- `app/cad/freecad.py` + `app/cad/freecad_worker.py` — P2.0 headless FreeCADCmd
  path, still single-container: import STEP/IGES/BREP/FCStd, load/edit/save FCStd,
  export STEP/STL/FCStd, inspect geometry/feature-tree state, and apply typed
  FreeCAD document patch ops, including typed Sketcher create/attach/external
  geometry/solver and geometry/constraint edits, native Assembly
  container/member/joint/solve edits, and typed TechDraw page/view/projection
  group/section/detail/centerline/cosmetic/dimension edits with SVG/DXF/PDF
  artifact paths.
- `app/cad/preview.py` — PyVista offscreen render (xvfb/mesa).
- `app/main.py` — `/healthz` (trivial, always 200), `/api/generate` (SSE),
  `/api/freecad/smoke` (diagnostic), SPA.
- `app/session_store.py` — P1.5 SQLite session/version metadata store:
  `DesignState` JSON, scripts, patches, geometry summaries, and version metadata.
- `app/artifact_store.py` — filesystem artifact store for PNG/STEP/STL/FCStd/TechDraw,
  referenced from version metadata instead of being stored in SQLite.
- `index.html` — vanilla SPA at the repo root, served same-origin; **client holds
  conversation history** and replays it to the stateless server; retries on 503
  (cold start). Living at the root also makes the deploy scanner see one fullstack
  service (not an undeployed standalone frontend).

### Session storage boundary
The platform injects no DB/object-storage and idle auto-pause scales the pod to
zero, wiping pod-local state unless a persistent volume is attached. The browser
therefore still keeps a local session cache, and the server mirrors lightweight
session/version facts into SQLite for reload/recovery. `CAD_SESSION_DB_PATH`
controls the SQLite file location; without a persistent volume, the default
`/tmp/4yi-cad/sessions.sqlite3` is a convenience cache, not durable production
storage. `CAD_ARTIFACT_ROOT` controls server-side artifact storage; the default
`/tmp/4yi-cad/artifacts` also needs a persistent volume or object-storage backend
before production use.

Do not store secrets or cross-session artifacts in SQLite until the CAD worker is
isolated from the web process; model-generated Python currently runs in the same
container/user boundary.

### Security (tenant-isolation)
LLM-generated Python runs in `run_sandboxed`: **no gateway token or `XCLAW_*` in
the child env**, CPU/address-space rlimits, wall-clock deadline. Network egress
blocking, non-root, read-only rootfs and seccomp are applied by the container /
k8s securityContext (TMPDIR must be a writable tmpfs/emptyDir).

P2.0 keeps FreeCAD in the same container for import simplicity. This is not a
multi-service worker split yet; generated FreeCAD Python still runs behind the
same scrubbed-env sandbox path.

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest            # unit tests (no cadquery/network needed)
```

Local FreeCAD smoke is optional. If `FreeCADCmd` is installed locally (or
`FREECADCMD_BINARY=/path/to/FreeCADCmd` is set), the FreeCAD smoke tests exercise
    real STEP/STL/FCStd export, FCStd load/patch/save, typed feature-tree ops,
    Sketcher create/attach/external geometry/solver coverage, Assembly
    member/joint/solver coverage, TechDraw page/view/projection/section/detail/
    cosmetic/dimension plus SVG/DXF/PDF artifact-path coverage, and document
    inspection with diffable typed state:

```bash
.venv/bin/pytest tests/test_freecad_worker.py -q
```

## Local container smoke (end-to-end, needs Docker)

```bash
docker build -t 4yi-cad .
docker run --rm -p 8080:8080 \
  -e PORT=8080 \
  -e OPENAI_BASE_URL="https://<staging>/api/v1" \
  -e OPENAI_API_KEY="<xclaw-bsl test token>" \
  -e TEXT_MODEL="<model id>" \
  --tmpfs /tmp \
  4yi-cad
# then open http://localhost:8080 and generate a model
curl -s localhost:8080/healthz     # {"status":"ok"}
curl -s localhost:8080/api/freecad/smoke
```

## Install as a 4yi app

Import via `/admin/marketplace/ai-import` as a **Dedicated app** (`public_git`,
this repo URL + branch). Proposal must have: one public service, `runtime_port`
= `$PORT`, `health_path` = `/healthz`, `auth_policy: platform_sso`, and a
`platform_runtime.gateway` block (`apiBaseEnv: [OPENAI_BASE_URL]`,
`apiKeyEnv: [OPENAI_API_KEY]`, `TEXT_MODEL` slot with `defaultModel` +
`allowedModels`). Size memory to measured peak RSS, schedulable on one node.

> **Pre-public-release license gate:** FreeCAD ships GPL components and any ported
> Text23D code must be license-compatible — resolve before making the repo public.
