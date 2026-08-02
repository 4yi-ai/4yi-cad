# Phase 5 - Web-Native High-Frequency Workbench

Phase 5 keeps 4yi-cad as a Web control plane instead of cloning the full
FreeCAD desktop. The browser now exposes the high-frequency operations that are
safe to perform from typed state and makes the handoff route explicit for
operations that should stay inside FreeCAD.

## Implemented Scope

- Web workbench summary in the generated FreeCAD properties panel:
  - visible/hidden/isolated object counts
  - route counts for Web patch, batch worker, and remote FreeCAD
  - bridge selection and bridge revision readouts
- Object visibility controls:
  - hide/show object in the Web viewer
  - isolate a single object
  - clear isolation
  - object tree keeps all objects visible as selectable rows and marks hidden or
    isolated state
- Measurement panel for the selected FreeCAD object:
  - envelope
  - center
  - volume and surface area when available
  - topology counts
  - selected subelement summary when a face/edge/vertex is selected
- Explicit operation routing:
  - numeric properties and placement edits remain controlled Web patches
  - import/export/rebuild operations remain batch-worker work
  - sectioning and complex Sketcher/Assembly/TechDraw edits route to remote
    FreeCAD
  - local Web view operations are labeled separately from document mutations
- Bridge synchronization:
  - refresh bridge context and mirror the remote active object into the Web
    selection when bridge sync is enabled
  - send the current Web object selection to FreeCAD with the existing
    `select_object` bridge command
  - pull a saved remote bridge revision back into Web state via the existing
    session/version API

## Architecture Boundary

Phase 5 adds no new backend persistence schema. It reuses:

- `/api/freecad/sessions/{id}/bridge/context`
- `/api/freecad/sessions/{id}/commands`
- `/api/sessions/{session_id}/versions/{version_id}`
- existing typed FreeCAD document patch APIs

Visibility and isolation are Web view state only. They do not modify FCStd,
FreeCAD object `Visibility`, or persisted FreeCAD document content.

## Acceptance

- Simple numeric edits can be completed from the Web properties panel without
  opening the remote GUI.
- Complex workbench operations show `Remote FreeCAD` as their route and expose
  an `Open FreeCAD` action.
- The user can see whether an operation is routed through Web patch, batch
  worker, Web view, or remote FreeCAD.
- Web and FreeCAD bridge selection can be synchronized in both directions.
- A remote bridge revision can be pulled back into the Web workbench after it is
  saved by the bridge.

## Verification

Static coverage lives in `tests/test_workbench_p1.py` and checks the Phase 5 Web
surface markers for route selection, visibility state, measurement rendering,
and bridge sync actions.
