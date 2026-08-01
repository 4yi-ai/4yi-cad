# High-End Community FreeCAD Reference

This reference is the local FreeCAD baseline for a 100 m x 100 m high-end residential
community master plan. It is intentionally massing-level, but it must still look and
inspect like a professional site plan rather than a random set of blocks.

## Source

- Script: `scripts/freecad/high_end_community_100m.py`
- Plot: 100000 mm x 100000 mm
- FreeCAD smoke: `tests/test_freecad_worker.py::test_local_freecadcmd_site_layout_smoke_exports_named_scene_objects`

## Required Program

The reference covers the minimum professional layers expected for this prompt:

- Plot/redline boundary and 8 m setback control.
- North axis, elevation datum, and planning metrics panel.
- Mostly closed perimeter wall with a controlled south entrance opening.
- Main gate, gate columns, guard booth, arrival/drop-off court.
- Vehicle road, pedestrian spine, fire loop, fire ladder access, turning-radius marker.
- Underground garage outline, basement ramp, visitor parking.
- Four villas, two high-rise residential towers, clubhouse, terrace.
- Organic artificial lake, lake bridge, central green, private gardens, children play area.

## Import Result

Generated locally with FreeCAD 1.1.3 and imported through the same 4yi-cad
`run_freecad_import_model("fcstd", ...)` path.

- Viewer objects: 53.
- Site audit status after import: `pass`.
- Coverage score after import: `1.0`.
- Issues after import: `[]`.
- Estimated building density: `0.149228`.
- Estimated landscape ratio: `0.2808687243397721`.

Key imported component counts:

- `plot_boundary`: 3
- `setback_control`: 1
- `north_axis`: 1
- `elevation_benchmark`: 1
- `boundary_wall`: 5
- `entrance_system`: 5
- `traffic_network`: 8
- `fire_access`: 6
- `parking_underground`: 3
- `residential_building`: 6
- `public_amenity`: 4
- `landscape_open_space`: 14
- `planning_metrics`: 1

## Gap Against Current AI Output

The current generated site shown in the UI is still below this baseline:

- It is visibly boxy and diagrammatic: buildings, roofs, and lake are coarse primitives,
  with weak site composition and little hierarchy.
- The selected lake reads as a triangular/low-face prism rather than a designed water body.
- Audit still reports missing planning controls, enclosure, entrance, and fire access
  even when some similarly named objects appear in the tree, which means generation,
  metadata, or classifier alignment is not reliable enough.
- Road/fire/parking layers are not treated as a connected site system.
- Villas, high-rise towers, clubhouse, and landscape do not follow a clear planning
  grammar around redline, setback, datum, spacing, access, and amenity sequence.
- Object naming and labels are not consistently role-rich enough for downstream audit
  and UI diagnostics.

## Fix Plan

1. Add a site-layout component planner before script generation. **Done.**
   The agent should produce a component checklist from the prompt first, then generate
   FreeCAD objects from that checklist. For this prompt, the checklist must include the
   reference program above.

2. Add missing-first repair after FreeCAD inspect. **Done.**
   If `document_summary.site_layout.status != "pass"`, feed the concrete audit issues
   back into a repair pass before returning the model. Do not rely on the initial script.

3. Introduce reusable high-end community templates. **Done.**
   Provide small parametric builders for redline/setback, perimeter walls, entry sequence,
   road/fire loop, underground parking, villas, towers, clubhouse, organic lake, play area,
   and metrics panel.

4. Add spatial validation before export. **Done.**
   Enforce inside-plot placement, z datum landing, tower spacing around 12000 mm, no
   floating public/traffic/landscape components, and a 20-60 exportable object budget.

5. Improve semantic naming rules. **Done.**
   Names and labels should contain role terms used by the audit, while avoiding false
   positives such as roof caps or private gardens being counted as residential massing.

6. Add UI-side comparison support. **Done.**
   Show reference-vs-generated audit deltas: missing roles, failed spatial checks,
   object count, building density, landscape ratio, and selected object semantic role.

7. Tune visual style after the audit passes. **Done.**
   Use restrained CAD materials and a professional plan composition: muted site base,
   dark roads, translucent water, green landscape, warm amenity, light residential massing,
   and a default top/axon view that reveals the full site.

The remaining visual work is optional product material rather than a blocker: curated
screenshots and presentation presets can be added after generated/imported models already
pass the structural site audit and carry viewer presentation hints.
