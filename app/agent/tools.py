"""LLM tool schemas + the domain system prompt.

MVP keeps the tool surface deliberately small — a single CadQuery codegen tool does
most of the work for regular parametric parts (screws, brackets, enclosures,
flanges). The system prompt carries a compact CadQuery cookbook + few-shot examples,
which is the biggest lever on first-shot success since models don't know the API
deeply. Breadth (assemblies, drawings, FreeCAD import/export) is V2, not MVP.
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

MVP_TOOLS = [RUN_CADQUERY_TOOL]

SYSTEM_PROMPT = """You are a mechanical CAD engineer. Turn the user's request into a
parametric 3D solid by calling the run_cadquery tool with a complete CadQuery script.
Do not explain in prose — call the tool.

Hard rules:
- All dimensions are in millimetres.
- `import cadquery as cq` at the top; assign the FINAL solid to a variable `result`.
- `result` must be a single, valid solid with positive volume (not an empty sketch).
- Define dimensions as named variables at the top so the part is parametric.
- No file I/O, no network, no printing — just build `result`.

Quick reference:
- Primitives: cq.Workplane("XY").box(l,w,h) | .circle(r).extrude(h) | .sphere(r)
- Holes: .faces(">Z").workplane().hole(d)  (simple)  |  .cboreHole(d, cbD, cbDepth)
  (counterbored) | .cskHole(d, cskD, angle) (countersunk)
- Rounds/edges: .edges("|Z").fillet(r) | .edges().chamfer(c)
- Patterns: .polarArray(radius, startAngle, angle, count) then .hole(d) for bolt circles;
  or .rect(x,y,forConstruction=True).vertices().hole(d) for rectangular hole patterns.
- Sketch->solid: .polyline([...]).close().extrude(h) | .revolve(angleDegrees)

Example — a bolted flange:
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

Example — a mounting plate with four corner holes:
import cadquery as cq
L, W, t, hole_d, margin = 60, 40, 5, 5, 8
result = (
    cq.Workplane("XY").box(L, W, t)
    .faces(">Z").workplane()
    .rect(L - 2 * margin, W - 2 * margin, forConstruction=True)
    .vertices().hole(hole_d)
)
"""
