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
- `app/agent/loop.py` — prompt → `run_cadquery` tool call → sandboxed execute →
  streamed events (MVP: no self-correction).
- `app/cad/runner.py` — **sandbox**: scrubbed env (no gateway token), CPU/mem
  rlimits, wall-clock deadline. Runs the worker in an isolated subprocess.
- `app/cad/worker.py` — execs the CadQuery script, exports STEP+STL, renders a
  preview PNG. Untrusted-code-facing; the container is the isolation boundary.
- `app/cad/preview.py` — PyVista offscreen render (xvfb/mesa).
- `app/main.py` — `/healthz` (trivial, always 200), `/api/generate` (SSE), SPA.
- `index.html` — vanilla SPA at the repo root, served same-origin; **client holds
  conversation history** and replays it to the stateless server; retries on 503
  (cold start). Living at the root also makes the deploy scanner see one fullstack
  service (not an undeployed standalone frontend).

### Why the client is the source of truth
The platform injects no DB/object-storage and idle auto-pause scales the pod to
zero, wiping pod-local state. So the browser keeps the authoritative script
history; the server is stateless across pause/restart. Exports stream inline as
base64 (no server storage).

### Security (tenant-isolation)
LLM-generated Python runs in `run_sandboxed`: **no gateway token or `XCLAW_*` in
the child env**, CPU/address-space rlimits, wall-clock deadline. Network egress
blocking, non-root, read-only rootfs and seccomp are applied by the container /
k8s securityContext (TMPDIR must be a writable tmpfs/emptyDir).

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest            # unit tests (no cadquery/network needed)
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
