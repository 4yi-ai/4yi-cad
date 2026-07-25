"""LLM tool schemas offered to the model.

MVP keeps the tool surface deliberately small — a single CadQuery codegen tool
does most of the work. Resist tool-count parity with the 150-tool reference
projects; breadth (inspect_model, import_step, export options) is added in later
phases, not MVP.
"""

RUN_CADQUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "run_cadquery",
        "description": (
            "Generate a parametric 3D model by writing a CadQuery Python script. "
            "The script must build a CadQuery Workplane/Shape and assign the final "
            "result to a variable named `result`. It runs headless in a sandbox "
            "with no network access. Return the complete script."
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

SYSTEM_PROMPT = (
    "You are a CAD engineer. Turn the user's request into a parametric 3D model by "
    "calling the run_cadquery tool with a complete CadQuery script that assigns the "
    "final solid to `result`. Prefer simple, parametric constructions. Do not explain; "
    "call the tool."
)
