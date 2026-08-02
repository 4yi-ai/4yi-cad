# Phase 1 FreeCAD GUI Session Spike

Status: initial local spike.

This spike validates the first L1 requirement from the remote-session evolution
plan: a browser can connect to a real FreeCAD GUI through noVNC. The image is a
separate interactive workload and does not replace the production FastAPI image.

## What This Covers

- FreeCAD GUI starts under Xvfb.
- A lightweight window manager runs in the virtual display.
- x11vnc exposes the display on localhost inside the container.
- noVNC/websockify exposes the session over HTTP.
- `/workspace` is the writable session workspace.
- `SESSION_FCSTD_PATH` can load a specific FCStd file at startup.

## What This Does Not Cover Yet

- Kubernetes pod orchestration.
- Authenticated, short-lived remote desktop URLs.
- In-process FreeCAD addon command execution.
- Automatic upload of saved FCStd files back to the app.
- Idle timeout and resource telemetry.

Those belong to the next Phase 1/2 tasks after the local GUI path is proven.
Phase 2/3 now cover the HTTP bridge contract, the local bridge client, and Web
Chat command routing.

## Build

```bash
docker build -f Dockerfile.freecad-gui -t 4yi-cad-freecad-gui:phase1-spike .
```

## Run an Empty FreeCAD Session

```bash
mkdir -p .local/freecad-gui-workspace
docker run --rm \
  -p 6080:6080 \
  -e CAD_SESSION_ID=local-spike \
  -v "$PWD/.local/freecad-gui-workspace:/workspace" \
  4yi-cad-freecad-gui:phase1-spike
```

Open:

```text
http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=remote
```

## Run With an Existing FCStd

```bash
mkdir -p .local/freecad-gui-workspace
cp path/to/model.FCStd .local/freecad-gui-workspace/input.FCStd
docker run --rm \
  -p 6080:6080 \
  -e SESSION_FCSTD_PATH=/workspace/input.FCStd \
  -v "$PWD/.local/freecad-gui-workspace:/workspace" \
  4yi-cad-freecad-gui:phase1-spike
```

Inside FreeCAD, save manually to `/workspace/output.FCStd` for this spike. The
host can then inspect `.local/freecad-gui-workspace/output.FCStd`.

## Connect to the 4yi-cad Web Control Plane

The standalone app has `/api/freecad/sessions`. For local Phase 1 development,
enable the env-gated Docker backend so the API starts a disposable GUI container
with a random localhost noVNC port:

```bash
CAD_GUI_SESSION_BACKEND=local_docker \
CAD_GUI_SESSION_IMAGE=4yi-cad-freecad-gui:phase1-spike \
CAD_GUI_SESSION_ROOT=/tmp/4yi-cad/freecad-gui-sessions \
CAD_GUI_SESSION_CONTROL_PLANE_URL=http://host.docker.internal:8081 \
CAD_GUI_SESSION_HEALTH_WAIT_SECONDS=60 \
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8081
```

Then create a workbench session and a remote GUI session:

```bash
SESSION_ID="$(
  curl -s -X POST http://127.0.0.1:8081/api/sessions \
    -H 'content-type: application/json' \
    -d '{"title":"FreeCAD GUI spike"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["session"]["id"])'
)"

curl -s -X POST http://127.0.0.1:8081/api/freecad/sessions \
  -H 'content-type: application/json' \
  -d "{\"session_id\":\"${SESSION_ID}\"}"
```

The response `remote_url` opens the noVNC desktop for that API-created
container. Stop it with:

```bash
REMOTE_SESSION_ID="<remote session id from the response>"
curl -s -X DELETE "http://127.0.0.1:8081/api/freecad/sessions/${REMOTE_SESSION_ID}" \
  -H 'content-type: application/json' \
  -d '{"reason":"manual_stop"}'
```

When a platform remote desktop gateway is available instead, leave
`CAD_GUI_SESSION_BACKEND` disabled and point the web app at it:

```bash
export CAD_REMOTE_DESKTOP_BASE_URL="http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=remote&session_id={session_id}"
```

That URL mode does not create a unique GUI workload by itself. It only lets the
existing `Open FreeCAD` button open the configured browser desktop URL while the
API records session lifecycle events.

## Acceptance Checks

- `docker build -f Dockerfile.freecad-gui -t 4yi-cad-freecad-gui:phase1-spike .` succeeds.
- Container logs show Xvfb, x11vnc, noVNC, and FreeCAD startup.
- Browser loads the noVNC page on port `6080`.
- FreeCAD GUI is visible and interactive.
- Loading with `SESSION_FCSTD_PATH=/workspace/input.FCStd` opens that document.
- Manual save to `/workspace/output.FCStd` produces a file on the host volume.
- With `CAD_GUI_SESSION_BACKEND=local_docker`, `POST /api/freecad/sessions`
  returns a `remote_url` on a random localhost noVNC port.
- `DELETE /api/freecad/sessions/{remote_session_id}` stops the API-created
  container and records the stop event.
