# Phase 2 FreeCAD GUI Bridge Protocol

Status: local control-plane contract.

Phase 1 proved that the browser can open a real FreeCAD GUI through noVNC.
Phase 2 starts connecting that GUI workload back to the 4yi-cad workbench through
an explicit bridge protocol. Phase 3 adds the local bridge client and Web Chat
linkage described in `docs/PHASE3-freecad-bridge-chat.md`; Phase 4 moves the
default bridge into the FreeCAD addon described in
`docs/PHASE4-freecad-workbench-integration.md`.

## Scope

This phase adds the API and persistence contract that a FreeCAD Workbench addon
or lightweight bridge client can use:

- Register liveness and FreeCAD document state through heartbeat.
- Poll queued workbench commands.
- Mark commands completed or failed.
- Keep command queue state in SQLite, separate from append-only audit events.
- Pass control-plane endpoint URLs into local Docker GUI containers.

The actual in-process FreeCAD Workbench addon is still a follow-up. The bridge
contract is intentionally HTTP/JSON so it can be exercised by tests and simple
scripts before a GUI addon exists.

## Command Lifecycle

```text
POST /api/freecad/sessions/{remote_session_id}/commands
  -> command.status = pending

POST /api/freecad/sessions/{remote_session_id}/bridge/poll
  -> pending commands are atomically marked dispatched

POST /api/freecad/sessions/{remote_session_id}/bridge/commands/{command_id}/result
  -> command.status = completed | failed
```

Session events are still written for observability:

- `bridge_command_queued`
- `bridge_heartbeat`
- `bridge_poll`
- `bridge_command_completed`
- `bridge_command_failed`

## Heartbeat

```http
POST /api/freecad/sessions/{remote_session_id}/bridge/heartbeat
content-type: application/json
```

```json
{
  "bridge_id": "bridge_1",
  "freecad_version": "1.0.0",
  "document_name": "model.FCStd",
  "active_document_path": "/workspace/input.FCStd",
  "current_version_id": "version_id",
  "capabilities": ["inspect_document", "run_macro"],
  "metadata": {"workbench": "4yi-cad"}
}
```

The server updates:

- `bridge_status = connected`
- `metadata.bridge.last_seen_at`
- `metadata.bridge.freecad_version`
- `metadata.bridge.document_name`
- `metadata.bridge.capabilities`

## Poll

```http
POST /api/freecad/sessions/{remote_session_id}/bridge/poll
content-type: application/json
```

```json
{
  "bridge_id": "bridge_1",
  "max_commands": 10
}
```

Response:

```json
{
  "session": {"bridge_status": "connected"},
  "commands": [
    {
      "command_id": "cmd_...",
      "op": "inspect_document",
      "input": {},
      "base_version_id": "version_id",
      "status": "dispatched"
    }
  ]
}
```

Polling a second time does not return the same command unless a new command is
queued.

## Result

```http
POST /api/freecad/sessions/{remote_session_id}/bridge/commands/{command_id}/result
content-type: application/json
```

```json
{
  "status": "completed",
  "result": {
    "document_summary": {"object_count": 1}
  },
  "current_version_id": "version_id",
  "metadata": {"bridge_id": "bridge_1"}
}
```

Failed command:

```json
{
  "status": "failed",
  "error": "FreeCAD macro failed"
}
```

## Local Docker Bridge Environment

When the local Docker GUI backend is enabled, set the control-plane URL that the
container can reach:

```bash
CAD_GUI_SESSION_BACKEND=local_docker \
CAD_GUI_SESSION_IMAGE=4yi-cad-freecad-gui:phase1-spike \
CAD_GUI_SESSION_CONTROL_PLANE_URL=http://host.docker.internal:8081 \
CAD_GUI_SESSION_HEALTH_WAIT_SECONDS=60 \
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8081
```

The orchestrator passes these variables into each GUI container:

- `CAD_REMOTE_SESSION_ID`
- `CAD_WORKBENCH_SESSION_ID`
- `CAD_CONTROL_PLANE_URL`
- `CAD_BRIDGE_HEARTBEAT_URL`
- `CAD_BRIDGE_POLL_URL`
- `CAD_BRIDGE_COMMAND_RESULT_URL_BASE`
- `CAD_BRIDGE_SAVE_URL`
- `CAD_BRIDGE_POLL_INTERVAL_SECONDS`

## Acceptance Checks

- Commands are stored in `freecad_remote_session_commands`.
- Polling marks pending commands as `dispatched`.
- Repeated polling does not duplicate commands.
- Result submission stores command result/error and appends an audit event.
- Heartbeat sets `bridge_status = connected` and records bridge metadata.
- Local Docker run command includes bridge endpoint environment variables when
  `CAD_GUI_SESSION_CONTROL_PLANE_URL` is configured.
