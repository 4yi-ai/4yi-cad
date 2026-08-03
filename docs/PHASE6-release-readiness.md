# Phase 6: Release readiness gate

Phase 6 turns the previous FreeCAD GUI, bridge, and Web-native workbench work
into a concrete release gate. The app now exposes one self-check contract that
operators can run before promoting an install from internal use to Private Beta,
Public Beta, or GA.

## API contract

`GET /api/production/readiness`

Returns `schema: "4yi-cad.production_readiness.v1"` and:

- `release_targets.private_beta_ready`
- `release_targets.public_beta_ready`
- `release_targets.ga_ready`
- `summary.pass`, `summary.warn`, `summary.fail`, and `summary.blockers`
- check rows with `key`, `status`, `message`, `required_for`, and sanitized
  `details`

`GET /api/production/smoke` keeps its existing top-level fields for deployment
smoke scripts and now embeds the same report under `readiness`.

## Release checks

The Phase 6 report currently checks:

- `/healthz` contract: config-independent and fast.
- Gateway contract: `OPENAI_BASE_URL` or `OPENAI_API_BASE`, `OPENAI_API_KEY`,
  and `TEXT_MODEL` are injected, and traffic is routed through the platform
  endpoint, not `api.openai.com`.
- Storage: session DB and artifact root are writable.
- Durability: `CAD_DATA_DIR` or explicit storage paths resolve outside
  tmp-backed fallback storage.
- Upload policy: FreeCAD imports keep at least the 100 MB Private Beta cap.
- FreeCAD smoke: `/api/freecad/smoke` remains the built-container verification
  endpoint for the FreeCADCmd path.
- Remote GUI bridge: a GUI runtime backend, control-plane URL, and remote
  desktop routing are configured. The runtime can be the local Docker spike,
  an external orchestrator, or the short-term fixed `shared_service` desktop.
- Bridge observability: bridge context, command queue, and command result
  surfaces exist.
- Worker isolation: GA requires a split FreeCAD worker endpoint plus egress
  block, read-only rootfs, seccomp, and tmpfs workspace flags.
- License gate: Public Beta/GA require explicit acceptance that FreeCAD/GPL and
  any ported-code licensing has been reviewed.

## Environment gates

Private Beta expects:

```bash
OPENAI_BASE_URL=https://<platform-gateway>/api/v1
OPENAI_API_KEY=<platform-injected-token>
TEXT_MODEL=<tool-calling-model>
CAD_FREECAD_UPLOAD_MAX_BYTES=104857600
```

Public Beta also expects:

```bash
CAD_DATA_DIR=/data/4yi-cad
CAD_GUI_SESSION_BACKEND=<orchestrated-backend>
CAD_GUI_SESSION_CONTROL_PLANE_URL=https://<4yi-cad-control-plane>
CAD_REMOTE_DESKTOP_BASE_URL=https://<remote-desktop-router>
FOURYI_CAD_LICENSE_REVIEW_ACCEPTED=1
```

For a personal/demo dedicated app that uses one fixed FreeCAD desktop in the
same public service, use:

```bash
CAD_UNIFIED_APP=1
CAD_GUI_SESSION_BACKEND=shared_service
CAD_FREECAD_FIRST_ENTRY=1
CAD_SHARED_FREECAD_SESSION_ID=shared-freecad-gui
CAD_REMOTE_DESKTOP_BASE_URL=/freecad/vnc.html?autoconnect=1&resize=remote&path=freecad/websockify
CAD_FREECAD_GUI_PROXY_PREFIX=/freecad
CAD_FREECAD_GUI_UPSTREAM_URL=http://127.0.0.1:6080
CAD_CONTROL_PLANE_URL=http://127.0.0.1:8080
CAD_GUI_SESSION_CONTROL_PLANE_URL=http://127.0.0.1:8080
```

The same container runs FastAPI on `8080` and noVNC/FreeCAD on local `6080`.
The FreeCAD addon bridge polls `shared-freecad-gui` endpoints on
`http://127.0.0.1:8080` with `CAD_BRIDGE_MODE=freecad_addon` and
`CAD_BRIDGE_AUTOSTART=1`. With `CAD_FREECAD_FIRST_ENTRY=1`, `/` redirects to the
FreeCAD desktop and the Web workbench remains available at `/workbench`. A plain
prompt from the FreeCAD companion panel runs the FreeCAD agent, stores a new
FCStd version, and queues `load_model` for the bridge; the Web workbench still
has **Load current session** for manually loading the active FCStd artifact.

GA also expects the FreeCAD worker to run outside the web process:

```bash
FOURYI_FREECAD_WORKER_URL=http://<freecad-worker>
FOURYI_FREECAD_WORKER_EGRESS_BLOCKED=1
FOURYI_FREECAD_WORKER_READ_ONLY_ROOTFS=1
FOURYI_FREECAD_WORKER_SECCOMP_PROFILE=runtime/default
FOURYI_FREECAD_WORKER_TMPFS=1
```

## Web control surface

The right-side properties panel now includes **Release readiness**. It is a
manual check so normal workbench startup does not wait on deployment diagnostics.
The panel shows the three release targets, blocker keys, check counts, and each
sanitized check message.

## Acceptance

- `GET /api/production/readiness` returns a Phase 6 schema without exposing
  secret values.
- `GET /api/production/smoke` stays backward-compatible and includes
  `readiness`.
- Default local development reports writable storage but blocked release targets
  when gateway, remote GUI, worker isolation, or license gates are missing.
- A fully configured test environment reports `private_beta_ready:true`,
  `public_beta_ready:true`, `ga_ready:true`, and `production_ready:true`.
- The Web panel can request readiness on demand and renders pass/fail target
  states plus blockers.
