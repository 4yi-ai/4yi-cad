# 4yi-cad Phase 2 — FreeCAD Integration + Productionization

> Status: **spec / not started.** Prereq: Phase 1 (MVP + V1 CadQuery) must ship and be
> real-model verified first (currently blocked on the platform smoke false-negative).

## Context — where we are

Phase 1 delivered a **CadQuery-primary** app: prompt → `run_cadquery` → sandboxed
execute → geometry validation → self-correction loop → preview + STEP/STL. CadQuery
uses the **same geometry kernel as FreeCAD (OpenCASCADE)**, so *regular parametric
parts* (screws, brackets, flanges, enclosures) are already covered with kernel-grade
quality.

**The gap** (what CadQuery can't do well): constraint sketching, multi-part
**assemblies**, **technical drawings (2D)**, FEM/CAM, and importing rich formats
(FCStd, IGES, DXF, assembly trees). Phase 2 closes that gap by wiring in **FreeCAD**,
and hardens the runtime for real load.

## Goals / Non-goals

**Goals**
- Add a **FreeCAD execution engine** alongside CadQuery, with the agent routing per task.
- Support **assemblies**, **technical drawings (PDF/SVG)**, and **import/export** of
  STEP/IGES/DXF/FCStd.
- Productionize: **multi-service** (web + heavy FreeCAD worker), **EBS persistence**,
  a **lean image**, and generation **billing**.

**Non-goals (defer to Phase 3)**
- FEM simulation, CAM/toolpaths, BIM/Arch.
- Real-time collaborative editing.
- A full FreeCAD GUI in the browser.

## The three tracks

### Track A — FreeCAD engine (the core of Phase 2)

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
- `import_model(format, data_b64)` — STEP/IGES/DXF/FCStd → into the working document
  (base for "edit an existing part / assembly").
- `export_drawing()` — TechDraw page → **PDF/SVG** (2D manufacturing drawing).
- Assembly + constraints — expressed *through* `run_freecad` first (escape hatch);
  promote to dedicated tools (`add_part`, `add_constraint`) only if the model struggles.

**State model.** Assemblies/drawings are stateful (FreeCAD feature tree / FCStd doc).
Two options:
- (a) **Script-history as truth** (Phase 1 model) — replay the FreeCAD script sequence
  to rebuild the doc each turn. Deterministic, replayable, no server storage.
- (b) **Persisted FCStd** (EBS) — keep the mutable doc, faster iteration on big models.

  **Decision:** start with (a) for CadQuery-parity work; introduce (b) for large
  assemblies where replay is too slow (ties into Track B EBS). Client still holds the
  authoritative history; server keeps a rebuild cache + optional EBS snapshot.

### Track B — Productionization / reliability

The moment assemblies + FreeCAD land, single-container 512 Mi will OOM. Do these
**with** Track A, not after:
- **Multi-service manifest** (`xclaw.app.yaml`): `web` (public) + `freecad-worker`
  (`route: none`) with high CPU/mem. The web↔worker call is already an HTTP boundary
  in Phase 1, so this is a config promotion, not a rewrite.
- **EBS volume** on the worker for FCStd docs + scratch (PVC-backed; the platform
  supports declared storage).
- **Lean image** (multi-stage, strip FreeCAD docs/examples/tests, slim base) — cuts
  the ~2 GB image → faster pull / cold-start / resume. (Also mitigates the Phase-1
  deploy pain.)
- **Generation billing**: meter CAD compute + LLM per install/org (platform compute
  credits); the gateway token already attributes LLM spend per-install.

### Track C — Product experience (differentiators; pick 1–2)

- **Interactive viewport (WS)**: rotate / measure / pick faces on the 3D model
  (upgrade from static PNG; reuses the shipped `xclaw-router` WS path).
- **Parametric panel**: surface the script's named dimensions as sliders/inputs so
  users tweak without re-prompting.
- **Multimodal input**: reference image → model (CADialogue direction).
- **Template/starter library**: common parts (screw/flange/bracket) one-click.
- **inspect_model tool + confirmed-script cache**: stronger self-correction.

## Architecture after Phase 2

```
Browser SPA (history = source of truth)
      │  HTTP + SSE (WS optional for viewport)
      ▼
web service (public)            ── FastAPI: orchestrate + /healthz + SSE + agent loop
      │  internal HTTP
      ▼
freecad-worker (route:none)     ── big CPU/mem + EBS
      ├─ CadQuery runner (sandboxed subprocess)     [existing]
      ├─ FreeCAD  runner (sandboxed subprocess)     [new: assembly/drawing/import]
      ├─ preview  (PyVista offscreen)               [existing]
      └─ EBS: FCStd docs + scratch                  [new]
```

## Task breakdown (TDD, subagent-driven; each ships independently)

**P2.0 — Foundations**
1. Multi-service manifest: split `web` + `freecad-worker`; internal HTTP contract; healthz on both.
2. Image: add headless FreeCAD; multi-stage slim; verify `FreeCADCmd` runs headless.
3. EBS volume on the worker (declared storage); TMPDIR/tmpfs still writable.
4. Engine-routing scaffolding: `run_freecad` tool registered; loop routes CadQuery vs FreeCAD.

**P2.1 — FreeCAD escape hatch + formats**
5. `app/cad/freecad.py`: sandboxed FreeCAD script exec + result validation + preview.
6. STEP/IGES/DXF/FCStd **export**; then `import_model` (import → edit flow).
7. Cookbook/few-shot for FreeCAD Python (assembly, sketch, TechDraw idioms).

**P2.2 — Assembly**
8. Multi-part assembly via `run_freecad`; then `add_part`/`add_constraint` tools if needed.
9. Assembly preview (exploded/positioned); export assembled STEP.

**P2.3 — Technical drawings**
10. TechDraw page generation → PDF/SVG export tool; validation (non-empty page).

**P2.4 — State & scale**
11. FCStd persistence on EBS + confirmed-script cache; large-assembly replay strategy.
12. Generation billing hookup (compute credits per org).

**P2.5 — UX (choose)**
13. Parametric panel **or** interactive WS viewport.
14. Optional: multimodal image input; template library.

Each task: red→green TDD (unit tests with fakes; heavy FreeCAD/preview via container
smoke). Adversarial review + verification-before-completion per task.

## Verification (per capability, end-to-end)

- **FreeCAD engine**: prompt → `run_freecad` → valid FCStd/STEP + preview (container smoke).
- **Assembly**: two parts + a mate constraint → positioned assembly, exported STEP opens in a viewer.
- **Drawing**: a part → TechDraw → PDF with correct views/dimensions.
- **Import→edit**: upload a STEP → modify → re-export (round-trips).
- **Scale**: worst-case assembly load-test; confirm no OOM on the worker's sized memory;
  cold-start within readiness budget on the lean image.
- **Sandbox (regression)**: FreeCAD-generated code cannot read the gateway token or reach the network.

## Risks

- **Headless FreeCAD fragility** (GUI/display deps) — isolate rendering in a subprocess
  (like the Phase-1 preview fix); FreeCAD compute headless via `FreeCADCmd`.
- **Image size** — FreeCAD is large; aggressive slimming required or cold-start/deploy suffers.
- **Security surface** — full FreeCAD Python is powerful; sandbox invariants are load-bearing.
- **State complexity** — assemblies push toward mutable FCStd; keep client-history as the
  source of truth to stay replayable.
- **License gate** — FreeCAD ships **GPL** components; bundling it in a distributed public
  image has copyleft implications — resolve before wide release (already flagged in the plan).
- **Phase-1 debt** — do not start Phase 2 until Phase 1 is shipped + real-model verified
  (smoke unblocked), or you build on unverified ground.

## Dependencies / sequencing

1. **Ship Phase 1** (unblock smoke → publish → install → verify CadQuery path with real models).
2. P2.0 foundations (multi-service + FreeCAD image + EBS) — enabling.
3. P2.1 → P2.2 → P2.3 (capability, highest value first: escape hatch → assembly → drawings).
4. P2.4 scale/billing in parallel once assemblies are real.
5. P2.5 UX differentiator last.
