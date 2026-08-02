# Phase 3 FreeCAD Bridge and Chat Linkage

Status: local bridge-client loop with Web Chat command routing.

Phase 4 supersedes the default runtime for remote GUI sessions with an
in-process FreeCAD addon bridge. The standalone client documented here remains
available as `CAD_BRIDGE_MODE=standalone`.

Phase 3 builds on the Phase 2 HTTP/JSON contract. The control plane can now read
bridge context directly, the local Docker GUI image autostarts a bridge client,
and Web Chat can route remote FreeCAD commands when a remote GUI session is
active.

## Scope

Implemented in this phase:

- Container-side `freecad-bridge-client.py` with heartbeat, poll, command
  execution, and result reporting.
- Bridge heartbeat fields for `workbench`, `selection`, `document_tree`, and
  `console_tail`.
- `GET /api/freecad/sessions/{remote_session_id}/bridge/context` for Web Chat
  and diagnostics.
- `GET /api/freecad/sessions/{remote_session_id}/commands/{command_id}` for
  command-result polling.
- Docker autostart wiring for the bridge client when
  `CAD_GUI_SESSION_CONTROL_PLANE_URL` is configured.
- Web Chat display of current bridge context and structured command results.

The client intentionally uses only Python standard library modules so it can run
inside the disposable GUI image without changing the production FastAPI image.

## Bridge Client

Source:

```text
scripts/freecad-gui/freecad-bridge-client.py
```

The client uses these environment variables:

- `CAD_REMOTE_SESSION_ID`
- `CAD_WORKBENCH_SESSION_ID`
- `CAD_BRIDGE_HEARTBEAT_URL`
- `CAD_BRIDGE_POLL_URL`
- `CAD_BRIDGE_COMMAND_RESULT_URL_BASE`
- `CAD_BRIDGE_SAVE_URL`
- `CAD_BRIDGE_POLL_INTERVAL_SECONDS`
- `CAD_SESSION_WORKSPACE`

Workspace state files:

- `/workspace/bridge-selection.json`
- `/workspace/bridge-document-tree.json`
- `/workspace/bridge-console.log`
- `/workspace/output.FCStd`
- `/workspace/screenshot.png`

These files are the fallback state boundary for Phase 3. A later in-process
FreeCAD addon can write richer live state into the same contract, or replace the
standalone client with a `FreeCADGui`/Qt timer bridge.
That in-process addon bridge is implemented in
`docs/PHASE4-freecad-workbench-integration.md`.

## Command Support

Supported command ops:

- `inspect_document` returns the current document tree, selection, workspace
  files, active document path, console tail, recompute status, and undo status.
- `select_object` writes bridge selection state and returns the selected object
  as a changed object.
- `save_revision` uploads an FCStd file through the existing
  `/api/freecad/sessions/{remote_session_id}/save` endpoint and returns artifact
  refs from the saved workbench version.
- `capture_screenshot` returns a screenshot when `/workspace/screenshot.png`
  exists; otherwise it returns a structured `screenshot_not_available` error.
- `run_macro` journals the requested macro to `/workspace/bridge-last-macro.py`
  and returns a structured `macro_execution_disabled` error by default.

`run_macro` is deliberately not executed by the standalone client. Live macro
execution belongs inside a FreeCAD addon or a trusted GUI-side bridge where
selection, undo transaction state, and recompute status are available from the
real active document.

## API Additions

### Bridge Context

```http
GET /api/freecad/sessions/{remote_session_id}/bridge/context
```

Response shape:

```json
{
  "bridge_status": "connected",
  "workbench": "PartDesignWorkbench",
  "selection": {
    "objects": [{"name": "Hole001"}],
    "active_object": {"name": "Hole001"}
  },
  "document_tree": {
    "document": {"name": "model.FCStd"},
    "objects": [{"name": "Body"}]
  },
  "console_tail": ["ready"]
}
```

### Command Lookup

```http
GET /api/freecad/sessions/{remote_session_id}/commands/{command_id}
```

The Web Chat polls this endpoint after queueing a bridge command. Completed
commands include the bridge result, structured error details, transaction ID,
changed objects, console output, recompute status, undo status, and artifact
refs when available.

## Web Chat Flow

When a remote FreeCAD session is active:

1. Opening the session refreshes bridge context and adds a Chat message with
   selection, document, and bridge status.
2. Chat commands that the local parser cannot handle are considered for bridge
   routing.
3. Selection-oriented text such as "把选中孔改成 6mm" queues `run_macro` with the
   current bridge selection and a journaled macro proposal.
4. Save-oriented text queues `save_revision`.
5. Screenshot-oriented text queues `capture_screenshot`.
6. The UI polls the command status endpoint and shows completion or structured
   failure in Chat.

## Local Smoke

Run the API with the local Docker GUI backend:

```bash
PORT=8081 \
OPENAI_BASE_URL=http://localhost:9999/api/v1 \
OPENAI_API_KEY=local-dummy \
TEXT_MODEL=local-dummy \
CAD_GUI_SESSION_BACKEND=local_docker \
CAD_GUI_SESSION_IMAGE=4yi-cad-freecad-gui:phase1-spike \
CAD_GUI_SESSION_ROOT=/tmp/4yi-cad/freecad-gui-sessions \
CAD_GUI_SESSION_CONTROL_PLANE_URL=http://host.docker.internal:8081 \
CAD_GUI_SESSION_HEALTH_WAIT_SECONDS=60 \
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8081
```

Then:

```bash
SESSION_ID="$(
  curl -s -X POST http://127.0.0.1:8081/api/sessions \
    -H 'content-type: application/json' \
    -d '{"title":"FreeCAD bridge Phase 3"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["session"]["id"])'
)"

REMOTE_SESSION_ID="$(
  curl -s -X POST http://127.0.0.1:8081/api/freecad/sessions \
    -H 'content-type: application/json' \
    -d "{\"session_id\":\"${SESSION_ID}\"}" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["session_id"])'
)"

curl -s "http://127.0.0.1:8081/api/freecad/sessions/${REMOTE_SESSION_ID}/bridge/context"

COMMAND_ID="$(
  curl -s -X POST "http://127.0.0.1:8081/api/freecad/sessions/${REMOTE_SESSION_ID}/commands" \
    -H 'content-type: application/json' \
    -d '{"op":"inspect_document","input":{}}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["command_id"])'
)"

curl -s "http://127.0.0.1:8081/api/freecad/sessions/${REMOTE_SESSION_ID}/commands/${COMMAND_ID}"
```

## Acceptance Checks

- The Docker GUI image contains `freecad-bridge-client.py`.
- API-created GUI containers receive heartbeat, poll, result, and save URLs.
- The bridge client heartbeat marks the remote session `bridge_status=connected`.
- `inspect_document` can be queued from the control plane and completed by the
  bridge client.
- Web Chat can display bridge context and structured command result/failure.
- Macro requests produce a command journal file and structured error until an
  in-process FreeCAD addon executes them.
