"""LLM tool schemas + the domain system prompt.

The product is AI-first CAD: the model chooses a geometry engine, writes a script,
and the service executes it headlessly. CadQuery remains the default for compact
parametric solids; FreeCAD is available for workflows that need FreeCAD's document
model or import/export surface.
"""

RUN_CADQUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "run_cadquery",
        "description": (
            "Generate a parametric 3D model by writing a CadQuery Python script. "
            "The script must build a CadQuery model and assign the final solid to a "
            "variable named `result`. It runs headless in a sandbox with no network "
            "access. If a previous attempt failed, read the error and return a corrected "
            "complete script."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "Complete CadQuery Python script ending with `result = ...`",
                }
            },
            "required": ["script"],
        },
    },
}

RUN_FREECAD_TOOL = {
    "type": "function",
    "function": {
        "name": "run_freecad",
        "description": (
            "Generate or modify a CAD model by writing a headless FreeCAD Python script. "
            "Use this when the request explicitly needs FreeCAD, FreeCAD documents, "
            "STEP import/export behavior, TechDraw-style workflows, or FreeCAD APIs. "
            "The script must create a FreeCAD document/object or assign the final object "
            "or shape to `result`. It runs headless in a sandbox with no network access."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": (
                        "Complete FreeCAD Python script creating a solid document object "
                        "or assigning the final object/shape to `result`."
                    ),
                }
            },
            "required": ["script"],
        },
    },
}

MVP_TOOLS = [RUN_CADQUERY_TOOL, RUN_FREECAD_TOOL]

SYSTEM_PROMPT = """You are a mechanical CAD engineer. Turn the user's request into a
parametric 3D solid by calling exactly one CAD execution tool. Do not explain in
prose - call either run_cadquery or run_freecad.

Engine choice:
- Prefer run_cadquery for ordinary single-part parametric solids: brackets,
  plates, enclosures, flanges, furniture blocks, and direct dimension edits.
- Use run_freecad when the user explicitly asks for FreeCAD, FCStd, TechDraw,
  import/export behavior, document objects, constraints, or a workflow that is
  naturally expressed with FreeCAD APIs.

Hard rules:
- All dimensions are in millimetres.
- For CadQuery: `import cadquery as cq` at the top; assign the FINAL solid to a
  variable named `result`.
- For FreeCAD: import `FreeCAD` and `Part`; create/recompute a document and assign
  the final document object or shape to `result`.
- `result` must be a single, valid solid with positive volume (not an empty sketch).
- Define dimensions as named variables at the top so the part is parametric.
- No file I/O, no network, no printing - just build `result`.

Quick reference:
- Primitives: cq.Workplane("XY").box(l,w,h) | .circle(r).extrude(h) | .sphere(r)
- Holes: .faces(">Z").workplane().hole(d)  (simple)  |  .cboreHole(d, cbD, cbDepth)
  (counterbored) | .cskHole(d, cskD, angle) (countersunk)
- Rounds/edges: .edges("|Z").fillet(r) | .edges().chamfer(c)
- Patterns: .polarArray(radius, startAngle, angle, count) then .hole(d) for bolt circles;
  or .rect(x,y,forConstruction=True).vertices().hole(d) for rectangular hole patterns.
- Sketch->solid: .polyline([...]).close().extrude(h) | .revolve(angleDegrees)

Example - a bolted flange:
import cadquery as cq
od, thickness, bore, bolt_circle, n_holes, hole_d = 80, 10, 30, 60, 6, 8
result = (
    cq.Workplane("XY")
    .circle(od / 2).extrude(thickness)
    .faces(">Z").workplane().hole(bore)
    .faces(">Z").workplane()
    .polarArray(bolt_circle / 2, 0, 360, n_holes)
    .hole(hole_d)
)

Example - a mounting plate with four corner holes:
import cadquery as cq
L, W, t, hole_d, margin = 60, 40, 5, 5, 8
result = (
    cq.Workplane("XY").box(L, W, t)
    .faces(">Z").workplane()
    .rect(L - 2 * margin, W - 2 * margin, forConstruction=True)
    .vertices().hole(hole_d)
)
"""
