# 4yi-cad

AI CAD app: **prompt → parametric 3D model → preview → STEP/STL export → iterative refine**.
Packaged as a **4yi dedicated app** — a standalone repo the 4yi platform builds
(CodeBuild → ECR) and installs per-consumer-org into EKS, injecting the LLM gateway.

## Our target

Our target is to serve FreeCAD users with an AI companion for early model
generation and bidirectional FreeCAD handoff. Generated models can be exported
back to FreeCAD-compatible artifacts such as FCStd/STEP/STL, and existing
FreeCAD outputs can be imported into 4yi-cad for preview, parameter edits,
diagnostics, and inspectable iteration. It is not intended to replace FreeCAD's
GUI or mature workbenches. Private/Public Beta should be positioned around
reliable Sketcher/PartDesign-style generation, upload, preview, diagnostics,
import from FreeCAD, and export back to FreeCAD; GA requires a stronger closed
loop for topology references, Assembly, TechDraw, and external geometry repair.

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
- `index.html` — vanilla SPA at the repo root, served same-origin; the browser
  keeps a local conversation cache while the server persists lightweight
  session/version/artifact metadata. It retries on 503 (cold start). Living at
  the root also makes the deploy scanner see one fullstack service (not an
  undeployed standalone frontend).

### Session storage boundary
The platform injects no DB/object-storage and idle auto-pause scales the pod to
zero, wiping pod-local state unless a persistent volume is attached. The browser
therefore still keeps a local session cache, and the server mirrors lightweight
session/version facts into SQLite for reload/recovery. `CAD_SESSION_DB_PATH`
controls the SQLite file location and `CAD_ARTIFACT_ROOT` controls server-side
artifact storage. When the platform injects `CAD_DATA_DIR`, the app defaults to
`${CAD_DATA_DIR}/sessions.sqlite3` and `${CAD_DATA_DIR}/artifacts` if that
directory is writable by the app user; otherwise it falls back to
`/tmp/4yi-cad/...`, which is a convenience cache, not durable production storage.

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

## FreeCAD-first unified runtime

`Dockerfile.freecad-gui` is the marketplace/runtime image for the FreeCAD-first
flow. It starts the FastAPI control plane on `8080`, starts noVNC/FreeCAD on
local `6080`, and serves the desktop through the same-origin `/freecad` proxy:

```bash
docker build -f Dockerfile.freecad-gui -t 4yi-cad-freecad-gui:unified .
docker run --rm -p 8080:8080 \
  -v "$PWD/.local/freecad-gui-workspace:/workspace" \
  4yi-cad-freecad-gui:unified
# then open:
# http://127.0.0.1:8080/
```

Marketplace deployments target `x86_64`. On Apple Silicon, the default local
Docker build produces an `arm64` image; that is useful for API/noVNC smoke tests,
but the Debian FreeCAD GUI package can be unstable on that architecture. Use
`docker build --platform linux/amd64 ...` when validating the real FreeCAD GUI
locally.

The legacy noVNC-only spike is still available with `CAD_UNIFIED_APP=0` and
`-p 6080:6080`.

The FastAPI control plane can also start one local GUI container per remote
session when explicitly enabled:

```bash
CAD_GUI_SESSION_BACKEND=local_docker \
CAD_GUI_SESSION_IMAGE=4yi-cad-freecad-gui:phase1-spike \
CAD_GUI_SESSION_CONTROL_PLANE_URL=http://host.docker.internal:8081 \
CAD_GUI_SESSION_HEALTH_WAIT_SECONDS=60 \
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8081
```

For marketplace import, `Dockerfile.freecad-gui` declares `CAD_UNIFIED_APP=1`.
The importer should therefore generate one public service on port `8080` using
that Dockerfile. `/` redirects to the same-origin noVNC desktop, `/workbench`
remains available for the Web workbench, and the FreeCAD companion panel can send
plain prompts to the in-container FastAPI control plane. The control plane runs
the FreeCAD agent, stores a new FCStd version, and queues `load_model` back to
the bridge.

See `docs/PHASE1-freecad-gui-spike.md` for loading an existing FCStd, API-driven
startup, and the manual save smoke path. See
`docs/PHASE2-freecad-gui-bridge.md` for the bridge heartbeat/poll/result
contract, and `docs/PHASE3-freecad-bridge-chat.md` for the autostart bridge
client, bridge context endpoint, command lookup endpoint, and Web Chat routing
flow. See `docs/PHASE4-freecad-workbench-integration.md` for the in-process
FreeCAD addon bridge and shared desktop/remote companion panel. See
`docs/PHASE5-web-native-workbench.md` for the Web-native high-frequency
workbench layer, operation routing, object visibility/isolation, measurement,
and bridge selection/revision sync. See `docs/PHASE6-release-readiness.md` for
the production readiness API, Web release gate, and Private/Public Beta/GA
promotion checks.

## Install as a 4yi app

Import via `/admin/marketplace/ai-import` as a **Dedicated app** (`public_git`,
this repo URL + branch). Proposal must have: one public service, `runtime_port`
= `$PORT`, `health_path` = `/healthz`, `auth_policy: platform_sso`, and a
`platform_runtime.gateway` block (`apiBaseEnv: [OPENAI_BASE_URL, OPENAI_API_BASE]`,
`apiKeyEnv: [OPENAI_API_KEY]`, `TEXT_MODEL` slot with `defaultModel` +
`allowedModels`). Set `CAD_FREECAD_UPLOAD_MAX_BYTES=104857600` for the Private
Beta 100 MB upload cap. Size memory to measured peak RSS, schedulable on one
node, and confirm the platform injects a writable `CAD_DATA_DIR` backed by
durable storage before claiming Public Beta/GA persistence.

Before promotion, run `/api/production/readiness`. Private Beta requires the
gateway, storage, upload, FreeCAD smoke, and bridge observability checks. Public
Beta adds durable storage, remote GUI bridge routing, and license acceptance. GA
also requires a split hardened FreeCAD worker.

> **Pre-public-release license gate:** FreeCAD ships GPL components and any ported
> Text23D code must be license-compatible — resolve before making the repo public.
