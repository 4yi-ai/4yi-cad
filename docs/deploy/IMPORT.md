# Importing 4yi-cad as a 4yi dedicated app

Follow the platform runbook `docs/runbooks/2026-07-14-next-ai-draw-io-import.md`
(in the XClaw repo). `import-proposal.reference.json` in this folder is the target
the edited wizard proposal should match.

## Steps

1. **Analyze** — `/admin/marketplace/ai-import` → **Dedicated app** → repo URL
   `https://github.com/4yi-ai/4yi-cad`, branch `main` (`public_git`). The wizard
   scans and generates a proposal.
2. **Edit the proposal** to match `import-proposal.reference.json`:
   - **web** service: `runtime_port` **8080**, `health_path` **`/healthz`**
   - `auth_policy` **`platform_sso`**
   - `platform_runtime.gateway`: `apiBaseEnv:[OPENAI_BASE_URL, OPENAI_API_BASE]`, `apiKeyEnv:[OPENAI_API_KEY]`
   - `TEXT_MODEL` slot: real tool-calling model id as `defaultModel` + `allowedModels`
     (no vision requirement)
   - `CAD_FREECAD_UPLOAD_MAX_BYTES=104857600` for the Private Beta default
     100 MB FCStd/STEP/IGES/BREP upload cap
   - **no** native LLM key env proposed as a required secret
   - `memory_request_mb` = measured worst-case peak RSS, schedulable on one node
   - stateful behavior is explicit: SQLite session metadata and filesystem CAD
     artifacts use `${CAD_DATA_DIR}` when the platform injects it; otherwise they
     fall back to pod-local `/tmp`. Confirm the live install is PVC/object-storage
     backed and that `CAD_DATA_DIR` is writable by the app user before making
     Public Beta/GA durability claims.
   - for a short-term personal/demo install, keep one public `web` service and
     add an internal fixed `freecad-gui` service from `Dockerfile.freecad-gui`
     on port **6080**. The web app proxies noVNC at `/freecad` and uses
     `CAD_GUI_SESSION_BACKEND=shared_service`. This is one shared desktop per
     dedicated app install, not per model tab.
3. **Release** — CodeBuild → ECR (sets `last_image_uri`). Confirm the image builds
   from the root `Dockerfile`.
4. **Publish** — needs a smoke pass + **tenant-isolation certification**. The
   certification gate is the sandbox: generated code runs with a scrubbed env (no
   gateway token / `XCLAW_*`), no network egress, non-root, read-only rootfs +
   writable `/tmp` tmpfs, seccomp, CPU/mem/wall-clock limits.
5. **Cross-org install smoke** — install into a second org; generate a model;
   confirm LLM + compute bill the **installing** org (`resolvePerOrgToken`), and
   the app is reachable within the ~70s readiness budget (SPA retries on 503).

## Pre-publish checklist

- [ ] `/healthz` returns 200 fast, independent of any render (liveness safe)
- [ ] `/api/freecad/upload_policy` reports the intended upload cap and formats
- [ ] `/api/freecad/smoke` returns `ok:true` in the built container (single-container
      FreeCADCmd path is installed and can export STEP/STL)
- [ ] `/api/production/smoke` reports `durable_storage_configured:true` for installs
      that have a PVC/object-storage backed `CAD_DATA_DIR`
- [ ] `/api/production/readiness` reports the intended release target as ready:
      Private Beta requires gateway/storage/upload/smoke/observability; Public
      Beta adds durable storage, remote GUI routing, and license acceptance; GA
      also requires a split hardened FreeCAD worker
- [ ] gateway calls hit the injected `${OPENAI_BASE_URL}` or `${OPENAI_API_BASE}`
      `/chat/completions` endpoint (not `/responses`, not `api.openai.com`)
- [ ] self-correction (V1) uses multiple <290s calls, never one long call
- [ ] `/tmp` is a writable tmpfs; rootfs read-only; runs as non-root
- [ ] sandbox proof: generated code cannot read `OPENAI_API_KEY` or reach the network/IMDS
- [ ] **license gate**: FreeCAD GPL components + any ported Text23D code are
      license-compatible for a public distributed image (resolve before wide release)

## Phase 6 readiness env

The readiness report intentionally returns booleans and check messages, not
secret values. For a GA-ready install, the app expects the platform gateway env,
durable `CAD_DATA_DIR`, remote GUI routing env, explicit
`FOURYI_CAD_LICENSE_REVIEW_ACCEPTED=1`, and the hardened worker flags:

Short-term fixed FreeCAD GUI service for personal/dedicated app smoke:

```bash
# Web app service
CAD_GUI_SESSION_BACKEND=shared_service
CAD_FREECAD_FIRST_ENTRY=1
CAD_SHARED_FREECAD_SESSION_ID=shared-freecad-gui
CAD_REMOTE_DESKTOP_BASE_URL=/freecad/vnc.html?autoconnect=1&resize=remote&path=freecad/websockify
CAD_GUI_SESSION_CONTROL_PLANE_URL=http://app-4yi-cad:8080
CAD_FREECAD_GUI_PROXY_PREFIX=/freecad
CAD_FREECAD_GUI_UPSTREAM_URL=http://app-4yi-cad-freecad-gui:6080

# Internal freecad-gui service
CAD_SESSION_ID=shared-freecad-gui
CAD_REMOTE_SESSION_ID=shared-freecad-gui
CAD_WORKBENCH_SESSION_ID=shared-freecad-gui
CAD_CONTROL_PLANE_URL=http://app-4yi-cad:8080
CAD_BRIDGE_HEARTBEAT_URL=http://app-4yi-cad:8080/api/freecad/sessions/shared-freecad-gui/bridge/heartbeat
CAD_BRIDGE_POLL_URL=http://app-4yi-cad:8080/api/freecad/sessions/shared-freecad-gui/bridge/poll
CAD_BRIDGE_COMMAND_RESULT_URL_BASE=http://app-4yi-cad:8080/api/freecad/sessions/shared-freecad-gui/bridge/commands
CAD_BRIDGE_COMMAND_QUEUE_URL=http://app-4yi-cad:8080/api/freecad/sessions/shared-freecad-gui/commands
CAD_BRIDGE_SAVE_URL=http://app-4yi-cad:8080/api/freecad/sessions/shared-freecad-gui/save
CAD_PANEL_ACTION_URL=http://app-4yi-cad:8080/api/freecad/sessions/shared-freecad-gui/panel/actions
CAD_BRIDGE_MODE=freecad_addon
CAD_BRIDGE_AUTOSTART=1
CAD_BRIDGE_ALLOW_MACRO_EXEC=1
```

The operator smoke flow can now be FreeCAD-first: open the app, land in noVNC,
use the 4yi CAD companion panel in FreeCAD, and send a plain prompt. The web
control plane creates the shared remote session on bridge heartbeat if needed,
runs the FreeCAD agent, stores the FCStd version, and queues `load_model` back to
`shared-freecad-gui`. The Web workbench is still available at `/workbench`; from
there, **Load current session** also queues `load_model` for the active FCStd
artifact.

```bash
FOURYI_FREECAD_WORKER_URL=http://<freecad-worker>
FOURYI_FREECAD_WORKER_EGRESS_BLOCKED=1
FOURYI_FREECAD_WORKER_READ_ONLY_ROOTFS=1
FOURYI_FREECAD_WORKER_SECCOMP_PROFILE=runtime/default
FOURYI_FREECAD_WORKER_TMPFS=1
```
