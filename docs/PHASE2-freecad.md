# 4yi-cad Phase 2 — Conversational CAD + FreeCAD Integration

> Status: **in progress.** P1.5 session/version metadata, filesystem artifact
> references, FreeCADCmd execution, FCStd import/load/edit/save, typed FreeCAD
> document patch ops, typed Sketcher create/attach/external geometry/solver ops,
> native Assembly container/member ops, typed Assembly joints/solve, typed
> TechDraw page/view/projection group/section/detail/centerline/cosmetic/dimension
> ops, SVG/DXF/PDF artifact paths, diffable typed document state, and local
> real-FreeCAD smoke coverage are implemented. The remote FreeCAD GUI bridge now
> has a first HTTP/SQLite command protocol for heartbeat, poll, dispatch, and
> command results; Phase 3 adds a local bridge client, bridge context endpoint,
> command lookup endpoint, and Web Chat routing; Phase 4 adds the shared FreeCAD
> addon panel and in-process addon bridge. Production storage and worker
> isolation still require deployment work.

## Current implementation snapshot

- FastAPI can import STEP/STP/IGES/IGS/BREP/FCStd into a FreeCAD session and save
  PNG/STEP/STL/FCStd/TechDraw SVG/TechDraw DXF/TechDraw PDF artifacts outside
  SQLite metadata when the local exporter path is available.
- The web workbench now exposes this headless FreeCAD state directly: users can
  import FCStd/STEP/STP/IGES/IGS/BREP from the browser, the server creates or
  appends a versioned FreeCAD session, and the UI hydrates the returned
  `document_summary`, FCStd artifact refs, preview, STEP, and STL state.
- FCStd is now mutable product state: `/api/freecad/document/patch` loads the
  selected FCStd, applies typed patch ops, exports fresh artifacts, inspects the
  result, and saves a new version.
- Generated/imported FreeCAD documents now use a FreeCAD-like web surface rather
  than a static PNG-only preview: the model tree binds to `document_summary`
  objects, the viewport can mount an STL-backed interactive scene with object
  bounding-box selection, `viewer_scene` preserves object-level style metadata
  for semantically colored CAD-readable scenes, and the right panel edits
  selected object properties, placement, and Sketcher constraint values through
  typed document patch ops.
- Typed FreeCAD patch ops currently include `create_feature`, `delete_feature`,
  `set_body_tip`, `set_placement`, `set_expression`, `create_sketch`,
  `attach_sketch`, `add_external_geometry`, `solver_status`, `add_geometry`,
  `add_constraint`, `remove_constraint`, `create_assembly`, `create_joint`,
  `update_joint`, `solve_assembly`,
  `add_part_to_assembly`, `remove_part_from_assembly`,
  `set_assembly_part_placement`, `ground_assembly_part`,
  `create_techdraw_page`, `add_techdraw_view`,
  `add_techdraw_projection_group`, `add_techdraw_section_view`,
  `add_techdraw_detail_view`, `add_techdraw_centerline`,
  `add_techdraw_cosmetic_vertex`, `add_techdraw_cosmetic_line`,
  `export_techdraw_pdf`, `add_techdraw_dimension`,
  `set_property`, and `set_constraint_value`.
- `document_summary` now includes geometry counts, object summaries, sketch
  geometry, sketch constraints, external geometry, sketch attachment state, and a
  `feature_tree` model with roots, nodes, parent/child links, feature kind,
  placement, PartDesign Body Tip, and Assembly summaries with parts, placements,
  joint groups, joints, and grounded state. TechDraw summaries include pages,
  templates, views, projection groups/items, section/detail views, centerlines,
  cosmetic edges/vertices, source links, dimensions, references, and view update
  state. `document_summary.typed_state` is keyed by stable FreeCAD object names
  for server-side diffs.
- Still open: richer Sketcher geometry/constraint coverage, richer Assembly
  LCS/element selection, motion/simulation, BOM, exploded views, stronger
  geometry checks, object-store/PVC durability, a separate hardened FreeCAD
  worker service, and deeper visual controls for multi-object FreeCAD scenes.
- Remote GUI bridge contract now persists queued commands separately from audit
  events and exposes heartbeat/poll/result endpoints. Phase 3 adds the local
  client and Web Chat integration in `docs/PHASE3-freecad-bridge-chat.md`.
  Phase 4 adds the in-process addon bridge and companion panel in
  `docs/PHASE4-freecad-workbench-integration.md`.

## Web Workbench Plan

4yi-cad should not attempt to embed or clone the desktop FreeCAD GUI. The durable
product path is:

1. **FreeCADCmd kernel:** run FreeCAD headlessly for import, typed edits, geometry
   checks, and artifact export.
2. **Authoritative FCStd state:** store the FCStd artifact outside metadata for
   every version; reload that FCStd for every edit, then save a new FCStd version.
3. **Typed document model:** expose a stable `document_summary.typed_state` with
   object, feature tree, sketch, assembly, and TechDraw state that the UI can diff
   and select.
4. **Web-native workbench:** render an interactive mesh scene when STL exists,
   bind tree/scene clicks to selected document objects, and send property edits
   through typed FreeCAD ops instead of raw script rewrites.
5. **Versioned workflows:** support import, generate, edit, rollback, and download
   as one continuous session flow.

Current implementation covers items 1-5 for the main FCStd/import/edit/download
path. Remaining parity work is deeper native semantics: more Sketcher constraints
and geometry types, Assembly LCS/element references and motion, TechDraw export
quality, stronger geometry validation classes, and hardened production worker
isolation/storage.

## Context — where we are

Phase 1 delivered a **CadQuery-primary** app: prompt → `run_cadquery` → sandboxed
execute → geometry validation → self-correction loop → preview + STEP/STL. CadQuery
uses the **same geometry kernel as FreeCAD (OpenCASCADE)**, so *regular parametric
parts* (screws, brackets, flanges, enclosures) are already covered with kernel-grade
quality.

**The product gap**: Phase 1 is still a one-shot generator. A user can create a
model, but cannot keep editing the same design through natural language, preserve
versions, roll back, or safely perform 50+ incremental changes without losing
context.

**The engine gap** (what CadQuery can't do well): constraint sketching, multi-part
**assemblies**, **technical drawings (2D)**, FEM/CAM, and importing rich formats
(FCStd, IGES, DXF, assembly trees). Phase 2 closes that gap by wiring in **FreeCAD**,
and hardens the runtime for real load. But FreeCAD is not the first user-visible
milestone; the first milestone is a durable conversational CAD state.

## Goals / Non-goals

**Goals**
- Add **design sessions + version history**: every generation/edit becomes a
  durable version with script, parameters, geometry summary, preview, artifacts, and
  rollback/download.
- Add a **patch-first modification loop**: for normal edits the model emits structured
  JSON patches, the app applies them to CAD state, and deterministic code renders the
  next CadQuery script. Do not rely on the LLM remembering 50 turns of chat.
- Add a **FreeCAD execution engine** alongside CadQuery, with the agent routing per task.
- Support **assemblies**, **technical drawings (PDF/SVG/DXF export)**, and
  model import/export for STEP/IGES/BREP/FCStd.
- Productionize: **multi-service** (web + heavy FreeCAD worker), **EBS persistence**,
  a **lean image**, and generation **billing**.

**Non-goals (defer to Phase 3)**
- FEM simulation, CAM/toolpaths, BIM/Arch.
- Real-time collaborative editing.
- A full FreeCAD GUI in the browser.

## The four tracks

### Track A — Conversational CAD state (first priority)

Phase 2 must stop treating the chat transcript as the CAD model. The model's long-term
state lives in application data, and the LLM only sees the current compact state needed
for the next edit.

**Session/version model.**

```
DesignSession
  id
  title
  activeVersionId
  versions[]

DesignVersion
  id
  number
  intent: create | modify | rollback | repair
  sourceVersionId?
  userInstruction
  designState
  script
  previewPng
  artifacts: { step, stl, fcstd, techdraw_svg?, techdraw_dxf? }
  geometrySummary
  createdAt
```

**Structured CAD state.** Each version stores a compact, editable state:

```
DesignState
  designBrief        # short human-readable summary of the current design
  engine             # cadquery | freecad
  parameters         # named numeric/string params, units, bounds when known
  features[]         # base, holes, boss, ribs, fillets, patterns, etc.
  constraints[]      # relationships that must be preserved
  activeScript       # complete script for replay/export/debug
  geometrySummary    # bbox, volume, face/edge counts, validation facts
```

**Patch-first modify loop.** For common edits, the LLM should not rewrite the whole
Python script. It outputs patches; the app validates/applies them; deterministic code
renders a fresh script; the sandbox executes and validates the result.

```
user instruction
  -> LLM emits CADPatch[]
  -> app validates and applies patches to DesignState
  -> deterministic renderer generates CadQuery script
  -> sandbox execute + geometry validation + preview/export
  -> save new DesignVersion
```

Example:

```json
{
  "op": "update_parameter",
  "name": "hole_d",
  "value": 6
}
```

Initial patch ops:
- `update_parameter(name, value)` — safest path for dimension changes.
- `add_feature(feature)` — holes, bosses, ribs, chamfers, fillets, patterns.
- `remove_feature(feature_id)` — remove a named/identified feature.
- `update_feature(feature_id, changes)` — edit a feature without touching unrelated ones.
- `rollback_to_version(version_id)` — branch from a previous version.
- `request_script_rewrite(reason)` — explicit fallback when structured state cannot
  represent the edit yet.

**Context budget and compaction.** Version history is persisted, but model context stays
small. Each edit request includes only:
- current active version's `DesignState` and complete `activeScript`;
- compact design brief + feature/parameter table;
- latest geometry summary;
- user's new instruction;
- last 3-5 relevant edits or a compressed edit summary;
- previous failure, if retrying.

Never send 50 full turns of transcript back to the model. Add a 60-turn regression test
that proves request context size does not grow linearly with version count, rollback
still works, and core dimensions/features remain stable.

**Fallback rule.** Script rewrite remains allowed for unsupported geometry, but it is a
controlled escape hatch: mark the version as `intent: repair` or `request_script_rewrite`,
extract parameters/features afterward, and save the resulting script as the new
authoritative active version.

### Track B — FreeCAD engine

**Runtime.** Install **headless FreeCAD** in the worker image (`FreeCADCmd` / the
`freecad` Python module — `import FreeCAD, Part, Sketcher, Draft, TechDraw`). Runs
without a GUI; the offscreen render path (xvfb/mesa, already in the image for VTK)
covers previews.

**Engine routing.** Keep the tool surface small and explicit — the agent picks:
- `run_cadquery(script)` — *existing.* Parametric solids (declarative, deterministic).
- `run_freecad(script)` — *new.* FreeCAD Python for assemblies, sketch constraints,
  TechDraw, and formats CadQuery lacks. The script builds a FreeCAD document and marks
  the result objects (convention: a `result` object or a `doc` to export).

  The `run_freecad` tool description tells the model *when* to prefer it (assembly,
  drawing, import, constraint sketch) vs `run_cadquery` (simple parametric solids), so
  routing is prompt-driven, not a separate classifier.

**New module** `app/cad/freecad.py` — mirrors `app/cad/runner.py`/`worker.py`:
- Executes the FreeCAD script **in the same sandbox** (`run_sandboxed`: scrubbed env —
  no gateway token/`XCLAW_*`, CPU/mem rlimits, wall-clock deadline; container does
  network-egress block + read-only rootfs + non-root).
- Validates the result (non-empty, valid shapes), exports the requested formats,
  renders a preview (reuse `app/cad/preview.py`).
- **Security note:** FreeCAD Python is a *larger* API surface than CadQuery — the
  sandbox invariants become more important, not less. No new escape from the sandbox.

**New capabilities (added incrementally, not all at once):**
- `import_model(format, data_b64)` — STEP/STP/IGES/IGS/BREP/FCStd → into the working document
  (base for "edit an existing part / assembly").
- `export_drawing()` — TechDraw page → **SVG/DXF** headless today; **PDF** is
  exported through the explicit `rsvg-convert` SVG→PDF path when available because
  this FreeCADCmd build cannot load `TechDrawGui`.
- Assembly + constraints — expressed *through* `run_freecad` first (escape hatch);
  promote to dedicated tools (`add_part`, `add_constraint`) only if the model struggles.

**State model.** Assemblies/drawings are stateful (FreeCAD feature tree / FCStd doc).
Two options:
- (a) **Script-history as truth** (Phase 1 model) — replay the FreeCAD script sequence
  to rebuild the doc each turn. Deterministic, replayable, no server storage.
- (b) **Persisted FCStd** (EBS) — keep the mutable doc, faster iteration on big models.

  **Decision:** FreeCAD joins the same `DesignState`/version model instead of replacing
  it. Start with replayable scripts and structured patches; introduce persisted FCStd
  for large assemblies where replay is too slow (ties into Track C EBS). Client/session
  history stays authoritative; server keeps a rebuild cache + optional EBS snapshot.

### Track C — Productionization / reliability

The moment assemblies + FreeCAD land, single-container 512 Mi will OOM. Do these
before or with Track B, not after:
- **Multi-service manifest** (`xclaw.app.yaml`): `web` (public) + `freecad-worker`
  (`route: none`) with high CPU/mem. Phase 1 currently uses a local sandbox subprocess,
  so Phase 2 must add a real internal HTTP contract: request schema, worker auth,
  timeout/cancel, artifact transfer, and SSE status bridging.
- **EBS volume** on the worker for FCStd docs + scratch (PVC-backed; the platform
  supports declared storage).
- **Lean image** (multi-stage, strip FreeCAD docs/examples/tests, slim base) — cuts
  the ~2 GB image → faster pull / cold-start / resume. (Also mitigates the Phase-1
  deploy pain.)
- **Generation billing**: meter CAD compute + LLM per install/org (platform compute
  credits); the gateway token already attributes LLM spend per-install.

### Track D — Product experience (differentiators; pick 1–2)

- **Interactive viewport (WS)**: rotate / measure / pick faces on the 3D model
  (upgrade from static PNG; reuses the shipped `xclaw-router` WS path).
- **Styled FreeCAD scenes (baseline implemented)**: preserve object-level colors,
  transparency, display modes, and semantic labels from generated FreeCAD scripts through
  `viewer_scene`, so site/community/assembly outputs are visually readable
  without becoming render-heavy marketing images.
- **Parametric panel**: surface the `DesignState.parameters` dimensions as sliders/inputs so
  users tweak without re-prompting.
- **Multimodal input**: reference image → model (CADialogue direction).
- **Template/starter library**: common parts (screw/flange/bracket) one-click.
- **inspect_model tool + confirmed-script cache**: stronger self-correction.

## Architecture after Phase 2

```
Browser SPA (session + version history)
      │  HTTP + SSE (WS optional for viewport)
      ▼
web service (public)            ── FastAPI: agent loop + patch validator + renderer + SSE
      │  internal HTTP
      ▼
cad-worker/freecad-worker        ── big CPU/mem + EBS
      ├─ CadQuery runner (sandboxed subprocess)     [existing]
      ├─ FreeCAD  runner (sandboxed subprocess)     [new: assembly/drawing/import]
      ├─ preview  (PyVista offscreen)               [existing]
      └─ EBS/cache: FCStd docs + scratch + server-side sessions [new]
```

## Task breakdown (TDD, subagent-driven; each ships independently)

**P2.0 — Design session + version history**
1. Add `DesignSession`, `DesignVersion`, and `DesignState` models in the frontend.
2. Save versions locally first (`localStorage`): prompt/instruction, script, preview,
   STEP/STL, parameters, geometry summary, and created time.
3. Add UI for version list, active version, rollback/branch, download old artifacts,
   and clear session.
4. Add context compaction: current state + active script + compact edit summary, never
   full unbounded transcript.

**P2.1 — Patch-first conversational modify**
5. Define `CADPatch` schema and validator (`update_parameter`, `add_feature`,
   `remove_feature`, `update_feature`, `rollback_to_version`, `request_script_rewrite`).
6. Add deterministic renderer: `DesignState` → complete CadQuery script.
7. Add `/api/modify`: user instruction + active version → patch proposal → validate/apply
   → render script → execute → save new version.
8. Add script-rewrite fallback only when patch schema cannot express the requested edit;
   re-extract state after successful rewrite.

**P2.2 — Parameters + geometry inspection**
9. Extract named parameters from successful scripts and expose them in `DesignState`.
10. Add parametric panel edits that create versions via `update_parameter` patches.
11. Compute geometry summary in the worker: bbox, volume, solid count, face/edge count,
    validation facts.
12. Add 60-turn regression: repeated edits keep context bounded and preserve features.

**P2.3 — Production boundary + persistence**
13. Split `web` + `cad-worker`: internal HTTP contract, healthz on both, worker auth,
    timeout/cancel, artifact transfer, and SSE status bridging.
14. Add server-side session/artifact persistence after local UX is validated.
15. Add EBS volume for large artifacts/FCStd/cache; enforce per-session directories,
    quota, cleanup, and no cross-session file access.

**P2.4 — FreeCAD escape hatch + formats**
16. Image: add headless FreeCAD; multi-stage slim; verify `FreeCADCmd` and
    `import FreeCAD, Part, Sketcher, TechDraw` run headless.
17. `app/cad/freecad.py`: sandboxed FreeCAD script exec + result validation + preview.
18. STEP/IGES/BREP/FCStd import/export; TechDraw DXF export; then `import_model`
    (import → edit flow).
19. Cookbook/few-shot for FreeCAD Python (assembly, sketch, TechDraw idioms).

**P2.5 — Assembly + drawings**
20. Multi-part assembly via `run_freecad`; then `add_part`/`create_joint`/`solve_assembly`
    typed tools for fixed/revolute/slider/cylindrical/distance/angle joints.
21. Assembly preview (exploded/positioned); export assembled STEP.
22. TechDraw page generation → projection group, section/detail, centerline/
    cosmetic, dimension, SVG/DXF export, and SVG→PDF exporter path. Validate
    non-empty page/view output.

**P2.6 — Scale, billing, UX**
23. Generation billing hookup (compute credits per org).
24. FreeCAD scene styling schema baseline: agent prompt/cookbook sets
    `ViewObject` colors/transparency/display modes for major objects; worker
    exports style metadata in `viewer_scene`; frontend renders those styles with
    CAD-readable object outlines and selection highlighting. Follow-up work:
    user-editable style controls and richer saved style presets.
25. Agent/render activity feedback baseline: frontend shows live status chip,
    viewport activity overlay, and chat activity card while SSE generation is
    submitting, thinking, rendering, retrying, and receiving artifacts.
26. Cross-session history baseline: backend lists recent saved workbench sessions;
    frontend drawer restores a selected session's active version while keeping
    current-session version history separate.
27. Parametric panel polish **or** interactive WS viewport.
28. Optional: multimodal image input; template library.

Each task: red→green TDD (unit tests with fakes; heavy FreeCAD/preview via container
smoke). Adversarial review + verification-before-completion per task.

## Verification (per capability, end-to-end)

- **FreeCAD engine**: prompt → `run_freecad` → valid FCStd/STEP + preview (container smoke).
- **Conversational edit**: create v1 → 3 natural-language edits → v4; every version is
  visible, downloadable, and rollback-able.
- **Patch determinism**: "change four holes to 6mm" produces an `update_parameter`
  patch, deterministic renderer changes only that parameter, and unrelated features remain.
- **60-turn context**: 60 sequential edits do not linearly grow model context and do not
  lose core parameters/features.
- **Assembly**: two parts + fixed/revolute/slider/cylindrical/distance/angle joint
  coverage → positioned assembly, solver status, exported STEP opens in a viewer.
- **Styled FreeCAD scene**: generated multi-object site/community model includes
  named plot/building/road/water/green/play objects with distinct colors or
  transparency in `viewer_scene`; viewer preserves selection/readability and does
  not require dense decorative geometry.
- **Drawing**: a part → TechDraw → SVG/DXF/PDF artifacts with correct projection,
  section/detail, cosmetic/centerline, and dimension state when the PDF converter
  is available.
- **Import→edit**: upload a STEP → modify → re-export (round-trips).
- **Scale**: worst-case assembly load-test; confirm no OOM on the worker's sized memory;
  cold-start within readiness budget on the lean image.
- **Sandbox (regression)**: FreeCAD-generated code cannot read the gateway token or reach the network.

## Risks

- **Headless FreeCAD fragility** (GUI/display deps) — isolate rendering in a subprocess
  (like the Phase-1 preview fix); FreeCAD compute headless via `FreeCADCmd`.
- **Image size** — FreeCAD is large; aggressive slimming required or cold-start/deploy suffers.
- **Security surface** — full FreeCAD Python is powerful; sandbox invariants are load-bearing.
- **Patch schema coverage** — structured patches will not cover every CAD edit at first.
  Keep script rewrite as an explicit fallback and re-extract state afterward.
- **Context drift** — long chat history cannot be the source of truth. Keep application
  `DesignState` authoritative and compact old edits into summaries.
- **State complexity** — assemblies push toward mutable FCStd; keep versioned
  `DesignState` + scripts as the source of truth to stay replayable.
- **Cross-session storage isolation** — EBS/cache must enforce per-session directories,
  quota, cleanup, and no generated-code access to other sessions' artifacts.
- **License gate** — FreeCAD ships **GPL** components; bundling it in a distributed public
  image has copyleft implications — resolve before wide release (already flagged in the plan).
- **Phase-1 debt** — do not start Phase 2 until Phase 1 is shipped + real-model verified
  (smoke unblocked), or you build on unverified ground.

## Dependencies / sequencing

1. **Ship Phase 1** (unblock smoke → publish → install → verify CadQuery path with real models).
2. P2.0 → P2.2 conversational edit loop: sessions, versions, patches, deterministic
   rendering, parameter panel, and bounded context.
3. P2.3 production boundary + persistence once the edit UX is validated.
4. P2.4 → P2.5 FreeCAD capability: escape hatch → import/export → assembly → drawings.
5. P2.6 scale/billing/UX polish.
