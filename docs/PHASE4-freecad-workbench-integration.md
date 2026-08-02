# Phase 4 FreeCAD Workbench Integration

Status: first in-process FreeCAD addon bridge and companion panel.

Phase 4 moves the bridge from a standalone sidecar client into the FreeCAD GUI
process. This gives the bridge access to real `FreeCADGui.Selection`,
`App.ActiveDocument`, document transactions, recompute, and screenshot APIs.

## Scope

Implemented in this phase:

- FreeCAD addon package:
  `freecad-addon/fouryi_cad_companion`.
- `4yi CAD` workbench with a companion task panel.
- Panel actions for refresh, start/stop bridge, explain selection, send prompt,
  generate patch, accept patch, reject patch, and support bundle export.
- In-process bridge runtime using a Qt timer when PySide is available.
- Heartbeat payloads from real FreeCAD state:
  - FreeCAD version
  - active document
  - active workbench
  - GUI selection
  - document tree
  - recent addon events
- In-process command execution for:
  - `inspect_document`
  - `select_object`
  - `run_macro`
  - `save_revision`
  - `capture_screenshot`
- Macro execution under FreeCAD document transactions when
  `CAD_BRIDGE_ALLOW_MACRO_EXEC=1`.
- Command journal files in `/workspace/bridge-command-journal.jsonl` plus one
  macro file per command.
- Platform `panel/actions` API to record FreeCAD panel actions and queue macro
  commands from the panel.
- Docker GUI image installation of the addon under the FreeCAD user `Mod`
  directory.

## Addon Layout

```text
freecad-addon/fouryi_cad_companion/
  InitGui.py
  FourYiCadCompanion.py
  package.xml
  README.md
  Resources/icons/fouryi_cad_companion.svg
```

Manual desktop install locations are listed in the addon README. The Docker GUI
image copies the same addon into:

```text
/home/appuser/.local/share/FreeCAD/Mod/fouryi_cad_companion
```

## Remote GUI Mode

The remote GUI image defaults to:

```text
CAD_BRIDGE_MODE=freecad_addon
CAD_BRIDGE_AUTOSTART=1
CAD_BRIDGE_ALLOW_MACRO_EXEC=1
```

With `CAD_BRIDGE_MODE=freecad_addon`, the shell entrypoint does not start the
standalone Phase 3 client. The addon autostarts inside FreeCAD after `InitGui.py`
is loaded.

The Phase 3 standalone client remains available for fallback:

```text
CAD_BRIDGE_MODE=standalone
```

## Panel Action API

```http
POST /api/freecad/sessions/{remote_session_id}/panel/actions
content-type: application/json
```

Request:

```json
{
  "action": "prompt",
  "prompt": "make selected hole 6mm",
  "selection": {"objects": [{"name": "Hole001"}]},
  "macro": "print('change')",
  "patch_id": null,
  "metadata": {"source": "freecad_panel"}
}
```

Actions:

- `prompt`
- `explain_object`
- `generate_patch`
- `accept_patch`
- `reject_patch`

If `prompt` or `generate_patch` includes a `macro`, the platform records the
panel action and queues a `run_macro` bridge command. Other actions are recorded
as audit events and can be connected to richer platform-native patch APIs later.

## Command Execution

`run_macro` in addon mode executes inside FreeCAD with:

- command journal file
- Python macro artifact file
- document transaction when an active document exists
- recompute by default
- structured error result on exception
- undo/recompute metadata in the command result

This is the first path where a prompt such as "把选中孔改成 6mm" can move beyond a
journaled request and execute against the active FreeCAD document, provided the
selected object exposes a compatible `Diameter`, `HoleDiameter`, or `Radius`
property.

## Support Bundle

The panel and `FourYi_ExportSupportBundle` command write:

```text
/workspace/4yi-freecad-support-bundle-YYYYMMDDTHHMMSSZ.json
```

The bundle includes addon version, FreeCAD version, platform info, redacted
bridge configuration, selection, document tree, recent addon events, and a
matrix-gate section for FreeCAD 1.1.x plus macOS/Windows/Linux release targets.

Endpoint URLs are stored as booleans, not plaintext, to avoid leaking internal
control-plane addresses into diagnostics.

## Acceptance Checks

- Docker image contains the addon under the FreeCAD `Mod` directory.
- Remote GUI containers default to `CAD_BRIDGE_MODE=freecad_addon`.
- The addon heartbeat reports `metadata.client=freecad-addon`.
- `inspect_document` returns a `document_tree` with
  `source=freecad_addon`.
- `run_macro` returns `schema=4yi.freecad.bridge.command_result.v2`, journal
  artifact refs, changed objects when detectable, and recompute/undo metadata.
- `panel/actions` records FreeCAD panel actions and queues macro commands when
  a panel prompt includes a macro.

## Remaining Work

- Real platform login inside this standalone repo is still not implemented.
- Rich AI patch generation/accept/reject needs the upstream project/revision API
  contract; this phase records the panel actions and provides the bridge path.
- Release matrix execution on macOS/Windows/Linux and multiple FreeCAD 1.1.x
  builds still needs CI or manual gate infrastructure.
