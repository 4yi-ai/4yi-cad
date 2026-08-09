# 4yi CAD Companion

This FreeCAD addon provides the Phase 4 Workbench surface for 4yi-cad.

It has two modes:

- Desktop companion: users can select an object, describe a change in natural
  language, review a typed edit plan, preview safe dimensional changes directly
  in the 3D canvas, apply or cancel them, and undo the last applied transaction.
  Complex edits continue through the cloud revision generator and load back
  through the same bridge.
- Remote session bridge: GUI containers set `CAD_BRIDGE_MODE=freecad_addon`, so
  the addon autostarts an in-process bridge that reads real `FreeCADGui`
  selection/document state and executes bridge commands in FreeCAD transactions.

## Manual Install

Copy this directory to the FreeCAD user `Mod` directory:

```text
macOS: ~/Library/Application Support/FreeCAD/Mod/fouryi_cad_companion
Linux: ~/.local/share/FreeCAD/Mod/fouryi_cad_companion
Windows: %APPDATA%/FreeCAD/Mod/fouryi_cad_companion
```

Restart FreeCAD and switch to the `4yi CAD` workbench.

## Remote Bridge Environment

The remote GUI Docker image configures these values automatically:

- `CAD_BRIDGE_MODE=freecad_addon`
- `CAD_BRIDGE_AUTOSTART=1`
- `CAD_BRIDGE_ALLOW_MACRO_EXEC=1` (legacy remote commands only; the natural-
  language panel never attaches executable Python to user prompts)
- `CAD_REMOTE_SESSION_ID`
- `CAD_WORKBENCH_SESSION_ID`
- `CAD_BRIDGE_HEARTBEAT_URL`
- `CAD_BRIDGE_POLL_URL`
- `CAD_BRIDGE_COMMAND_RESULT_URL_BASE`
- `CAD_BRIDGE_COMMAND_QUEUE_URL`
- `CAD_BRIDGE_SAVE_URL`
- `CAD_PANEL_ACTION_URL`

The standalone `freecad-bridge-client.py` remains available as a fallback when
`CAD_BRIDGE_MODE=standalone`.
