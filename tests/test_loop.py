"""Unit tests for the V1 self-correcting generation loop.

The loop is a real tool-calling agent loop: it asks the model for a run_cadquery
script, executes it in the sandbox, and if execution fails feeds the error back as
a tool result and asks for a fix — bounded to max_attempts, each a separate gateway
call (<290s). Success emits preview + artifacts; exhausting attempts emits a
terminal error. Dependency-injected fakes: no cadquery/network.
"""

import base64
import copy
import json

from app.agent.tools import BUILD_BUILDING_TOOL, RUN_FREECAD_TOOL, SYSTEM_PROMPT
from app.agent.loop import ExecResult, infer_engine_hint, run_generation, site_layout_quality_error
from app.cad.building_spec import default_building_spec
from app.gateway import ChatCompletion


class FakeGateway:
    """Returns a scripted sequence of completions, one per call (clamped to last)."""

    def __init__(self, completions):
        if isinstance(completions, ChatCompletion):
            completions = [completions]
        self._completions = completions
        self.calls: list[dict] = []

    async def chat_completion(self, messages, *, tools=None, tool_choice=None):
        idx = min(len(self.calls), len(self._completions) - 1)
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools, "tool_choice": tool_choice})
        return self._completions[idx]


def _tool_call(
    script: str,
    call_id: str = "call_1",
    *,
    name: str = "run_cadquery",
) -> ChatCompletion:
    return ChatCompletion(
        content=None,
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps({"script": script}),
                },
            }
        ],
    )


def _no_tool(content: str) -> ChatCompletion:
    return ChatCompletion(content=content, tool_calls=[])


def _building_tool_call(call_id: str = "building_1") -> ChatCompletion:
    return ChatCompletion(
        content=None,
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "build_building",
                    "arguments": json.dumps(default_building_spec().model_dump()),
                },
            }
        ],
    )


def _viewer_scene_b64(*roles: str, object_count: int = 18) -> str:
    roles = roles or ("plot", "building")
    objects = []
    for index in range(object_count):
        role = roles[index % len(roles)]
        objects.append({
            "name": f"{role}_{index}",
            "label": f"{role}_{index}",
            "style": {"semantic_role": role},
            "faces": [{"reference": "Face1", "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "triangles": [[0, 1, 2]]}],
        })
    payload = {
        "schema": "freecad.viewer_scene.v1",
        "objects": objects,
        "object_count": len(objects),
    }
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


async def _collect(agen):
    return [ev async for ev in agen]


def test_system_prompt_supports_freecad_site_layouts():
    freecad_description = RUN_FREECAD_TOOL["function"]["description"]

    assert "FreeCAD users" in SYSTEM_PROMPT
    assert "multi-object site/community/building layouts" in SYSTEM_PROMPT
    assert "convert to\n  millimetres" in SYSTEM_PROMPT
    assert "rather than\n  collapsing everything into one block" in SYSTEM_PROMPT
    assert "Private beta complexity budget" in SYSTEM_PROMPT
    assert "40-90 named objects" in SYSTEM_PROMPT
    assert "Schematic does not mean blank boxes" in SYSTEM_PROMPT
    assert "horizontal floor/story bands grouped every 2-4" in SYSTEM_PROMPT
    assert "App::DocumentObjectGroup" in SYSTEM_PROMPT
    assert "Do not rely on viewer-only overlays" in SYSTEM_PROMPT
    assert "individual windows" in SYSTEM_PROMPT
    assert "site_layout.v1-style master-plan model" in SYSTEM_PROMPT
    assert "PlanningMetrics object" in SYSTEM_PROMPT
    assert "fire lane" in SYSTEM_PROMPT
    assert "parking or basement" in SYSTEM_PROMPT
    assert "12000 mm" in SYSTEM_PROMPT
    assert "reference-quality bar" in SYSTEM_PROMPT
    assert "ViewObject.ShapeColor" in SYSTEM_PROMPT
    assert "translucent blue" in SYSTEM_PROMPT
    assert "multi-object site/building layouts" in freecad_description


def test_system_prompt_exposes_deterministic_building_tool():
    assert BUILD_BUILDING_TOOL["function"]["name"] == "build_building"
    schema = BUILD_BUILDING_TOOL["function"]["parameters"]
    assert schema["properties"]["typology"]["const"] == "residential_tower"
    assert "Use build_building for a single residential tower" in SYSTEM_PROMPT


def test_system_prompt_supports_freecad_mechanical_assemblies():
    freecad_description = RUN_FREECAD_TOOL["function"]["description"]

    assert "mechanical assemblies" in freecad_description
    assert "landing gear" in freecad_description
    assert "Use run_freecad for mechanical assemblies" in SYSTEM_PROMPT
    assert "wheel-and-strut assemblies" in SYSTEM_PROMPT
    assert "create an editable concept assembly" in SYSTEM_PROMPT
    assert "12-60 named exportable objects" in SYSTEM_PROMPT
    assert "Avoid thread geometry" in SYSTEM_PROMPT
    assert "wheel_d" in SYSTEM_PROMPT
    assert "hydraulic bodies white" in SYSTEM_PROMPT


def test_site_community_prompts_infer_freecad_engine_hint():
    assert infer_engine_hint("make a 3-floor villa on a 100x100m site") == "freecad"
    assert infer_engine_hint("设计一个带水景和楼栋的小区总图") == "freecad"
    assert infer_engine_hint("make a cube") is None


def test_single_building_prompts_infer_freecad_without_becoming_site_layout():
    from app.agent.site_layout import is_site_layout_prompt

    for prompt in ("生成一栋楼房", "设计一栋办公楼", "make a 12-storey residential tower"):
        assert infer_engine_hint(prompt) == "freecad"
        assert is_site_layout_prompt(prompt) is False


async def test_single_building_prompt_injects_validated_contract():
    gw = FakeGateway(_building_tool_call())
    executed = []

    async def execute(script):
        return ExecResult(ok=True)

    async def execute_freecad(script):
        executed.append(script)
        return ExecResult(ok=True, engine="freecad", exports={"fcstd": "F"})

    await _collect(
        run_generation(
            "生成一栋楼房",
            gateway=gw,
            execute=execute,
            execute_freecad=execute_freecad,
        )
    )

    user_message = gw.calls[0]["messages"][-1]["content"]
    assert "Single-building LOD planning contract" in user_message
    assert '"schema_version":"4yi-cad.building/v1"' in user_message
    assert '"typology":"residential_tower"' in user_message
    assert "Project/Site/Building/Storey" in user_message
    assert "zero OCC check errors" in user_message
    assert gw.calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "build_building"},
    }
    assert len(executed) == 1
    assert 'FreeCAD.newDocument("FourYiResidentialTower")' in executed[0]


async def test_site_prompt_injects_component_planner_message():
    script = "import FreeCAD\nresult = object()\n"
    gw = FakeGateway(_tool_call(script, name="run_freecad"))

    async def execute(script):
        return ExecResult(ok=True, exports={"stl": "wrong"})

    async def execute_freecad(script):
        return ExecResult(ok=True, engine="freecad", exports={"step": "S", "stl": "L"})

    await _collect(
        run_generation(
            "一块100米X100米的地块，设计高档小区，有别墅、高层、会所、人工湖和儿童游乐区",
            gateway=gw,
            execute=execute,
            execute_freecad=execute_freecad,
        )
    )

    user_message = gw.calls[0]["messages"][-1]["content"]
    assert "Site-layout component plan" in user_message
    assert "add_perimeter_wall" in user_message
    assert "add_artificial_lake" in user_message
    assert "40-90\n  named objects" in user_message
    assert "45-60 exportable components" in user_message
    assert "620+ faces and 1200+" in user_message
    assert "only actual villa/tower bodies" in user_message
    assert "document_summary.site_layout.status == \"pass\"" in user_message


def test_mechanical_assembly_prompts_infer_freecad_engine_hint():
    assert infer_engine_hint("design an aircraft landing gear with a hydraulic actuator") == "freecad"
    assert infer_engine_hint("make a wheel assembly with suspension linkage") == "freecad"
    assert infer_engine_hint("设计一个带液压杆、连杆和轮胎的起落架装配体") == "freecad"
    assert infer_engine_hint("make a simple bearing spacer") is None


async def test_happy_path_first_attempt_succeeds():
    gw = FakeGateway(_tool_call("result = box(10,10,10)"))
    executed = []

    async def execute(script):
        executed.append(script)
        return ExecResult(
            ok=True, preview_png_b64="UE5H", exports={"step": "S", "stl": "L"}
        )

    events = await _collect(run_generation("make a cube", gateway=gw, execute=execute))
    types = [e["type"] for e in events]

    assert executed == ["result = box(10,10,10)"]
    assert types == ["status", "script", "preview", "artifact", "artifact", "done"]
    assert events[1]["engine"] == "cadquery"
    arts = {e["format"]: e["data_b64"] for e in events if e["type"] == "artifact"}
    assert arts == {"step": "S", "stl": "L"}
    assert events[-1]["ok"] is True
    assert events[-1]["engine"] == "cadquery"
    assert gw.calls[0]["tool_choice"] == "required"


async def test_done_event_includes_execution_diagnostics():
    gw = FakeGateway(_tool_call("result = box(10,10,10)"))

    async def execute(script):
        return ExecResult(
            ok=True,
            exports={"step": "S"},
            diagnostics={"site_layout_audit": {"status": "pass"}},
        )

    events = await _collect(run_generation("make a cube", gateway=gw, execute=execute))

    assert events[-1]["type"] == "done"
    assert events[-1]["diagnostics"] == {"site_layout_audit": {"status": "pass"}}


async def test_freecad_tool_uses_freecad_executor_and_emits_engine_metadata():
    script = "import FreeCAD\nresult = object()\n"
    gw = FakeGateway(_tool_call(script, name="run_freecad"))
    cadquery_calls = []
    freecad_calls = []

    async def execute(script):
        cadquery_calls.append(script)
        return ExecResult(ok=True, exports={"stl": "wrong"})

    async def execute_freecad(script):
        freecad_calls.append(script)
        return ExecResult(
            ok=True,
            engine="freecad",
            freecad_version="1.1.3",
            preview_png_b64="UE5H",
            exports={"step": "S", "stl": "L"},
        )

    events = await _collect(
        run_generation(
            "make this in FreeCAD",
            gateway=gw,
            execute=execute,
            execute_freecad=execute_freecad,
        )
    )

    assert cadquery_calls == []
    assert freecad_calls == [script]
    assert events[1]["engine"] == "freecad"
    assert events[-1]["ok"] is True
    assert events[-1]["engine"] == "freecad"
    assert events[-1]["freecad_version"] == "1.1.3"
    assert any(e["type"] == "preview" and e["engine"] == "freecad" for e in events)


async def test_site_prompt_forces_freecad_tool_choice():
    script = "import FreeCAD\nresult = object()\n"
    gw = FakeGateway(_tool_call(script, name="run_freecad"))
    cadquery_calls = []
    freecad_calls = []

    async def execute(script):
        cadquery_calls.append(script)
        return ExecResult(ok=True, exports={"stl": "wrong"})

    async def execute_freecad(script):
        freecad_calls.append(script)
        return ExecResult(ok=True, engine="freecad", exports={"step": "S", "stl": "L", "viewer_scene": _viewer_scene_b64()})

    events = await _collect(
        run_generation(
            "make a 3-floor villa on a 100x100m site",
            gateway=gw,
            execute=execute,
            execute_freecad=execute_freecad,
        )
    )

    assert gw.calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "run_freecad"},
    }
    assert cadquery_calls == []
    assert freecad_calls == [script]
    assert events[1]["engine"] == "freecad"
    assert events[-1]["ok"] is True


async def test_mechanical_assembly_prompt_forces_freecad_tool_choice():
    script = "import FreeCAD\nresult = object()\n"
    gw = FakeGateway(_tool_call(script, name="run_freecad"))
    cadquery_calls = []
    freecad_calls = []

    async def execute(script):
        cadquery_calls.append(script)
        return ExecResult(ok=True, exports={"stl": "wrong"})

    async def execute_freecad(script):
        freecad_calls.append(script)
        return ExecResult(
            ok=True,
            engine="freecad",
            exports={"step": "S", "stl": "L", "viewer_scene": _viewer_scene_b64("plot", "building", "water", "play")},
        )

    events = await _collect(
        run_generation(
            "design a detailed aircraft landing gear with wheel, strut, hydraulic cylinder, and linkage",
            gateway=gw,
            execute=execute,
            execute_freecad=execute_freecad,
        )
    )

    assert gw.calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "run_freecad"},
    }
    assert cadquery_calls == []
    assert freecad_calls == [script]
    assert events[1]["engine"] == "freecad"
    assert events[-1]["ok"] is True


async def test_site_prompt_retries_if_model_uses_wrong_engine():
    gw = FakeGateway(
        [
            _tool_call("wrong", "c1", name="run_cadquery"),
            _tool_call("right", "c2", name="run_freecad"),
        ]
    )
    cadquery_calls = []
    freecad_calls = []

    async def execute(script):
        cadquery_calls.append(script)
        return ExecResult(ok=True, exports={"stl": "wrong"})

    async def execute_freecad(script):
        freecad_calls.append(script)
        return ExecResult(
            ok=True,
            engine="freecad",
            exports={"step": "S", "stl": "L", "viewer_scene": _viewer_scene_b64("plot", "building", "water", "play")},
        )

    events = await _collect(
        run_generation(
            "community building layout with water and playground",
            gateway=gw,
            execute=execute,
            execute_freecad=execute_freecad,
            max_attempts=2,
        )
    )

    assert cadquery_calls == []
    assert freecad_calls == ["right"]
    assert len(gw.calls) == 2
    assert any(e["type"] == "retry" and "run_freecad" in e["message"] for e in events)
    assert events[-1]["ok"] is True


async def test_site_prompt_retries_when_viewer_scene_quality_is_too_sparse():
    gw = FakeGateway([
        _tool_call("sparse", "c1", name="run_freecad"),
        _tool_call("rich", "c2", name="run_freecad"),
    ])
    freecad_calls = []

    async def execute(script):
        return ExecResult(ok=True, exports={"stl": "wrong"})

    async def execute_freecad(script):
        freecad_calls.append(script)
        if script == "sparse":
            return ExecResult(
                ok=True,
                engine="freecad",
                exports={"step": "S", "stl": "L", "viewer_scene": _viewer_scene_b64("plot", "building", object_count=4)},
            )
        return ExecResult(
            ok=True,
            engine="freecad",
            exports={
                "step": "S",
                "stl": "L",
                "viewer_scene": _viewer_scene_b64("plot", "building", "water", "play", "amenity", "building_articulation"),
            },
        )

    events = await _collect(
        run_generation(
            "设计一个100米x100米高档小区，有儿童游乐区，有人工湖，有高档会所，有高层",
            gateway=gw,
            execute=execute,
            execute_freecad=execute_freecad,
            max_attempts=2,
        )
    )

    assert freecad_calls == ["sparse", "rich"]
    assert any(e["type"] == "retry" and "too sparse" in e["message"] for e in events)
    assert events[-1]["ok"] is True


def test_site_quality_requires_requested_scene_roles():
    result = ExecResult(
        ok=True,
        engine="freecad",
        exports={"viewer_scene": _viewer_scene_b64("plot", "building", object_count=18)},
    )

    error = site_layout_quality_error("小区有儿童游乐区、人工湖和高档会所", "freecad", result)

    assert error
    assert "water" in error
    assert "play" in error
    assert "amenity" in error


async def test_self_corrects_after_a_failed_attempt():
    gw = FakeGateway([_tool_call("broken", "c1"), _tool_call("fixed", "c2")])
    executed = []

    async def execute(script):
        executed.append(script)
        if script == "broken":
            return ExecResult(ok=False, error="NameError: broken")
        return ExecResult(ok=True, preview_png_b64="P", exports={"stl": "L"})

    events = await _collect(run_generation("x", gateway=gw, execute=execute, max_attempts=3))
    types = [e["type"] for e in events]

    assert executed == ["broken", "fixed"]           # retried with a new script
    assert "retry" in types                            # a recoverable failure was surfaced
    assert events[-1]["ok"] is True
    assert events[-1]["engine"] == "cadquery"
    assert any(e["type"] == "artifact" for e in events)
    # the error was fed back to the model on the second call as a tool result
    second_msgs = gw.calls[1]["messages"]
    assert any(m.get("role") == "tool" and "NameError" in str(m.get("content")) for m in second_msgs)


async def test_gives_up_after_max_attempts():
    gw = FakeGateway(_tool_call("broken"))
    calls = []

    async def execute(script):
        calls.append(script)
        return ExecResult(ok=False, error="boom")

    events = await _collect(run_generation("x", gateway=gw, execute=execute, max_attempts=2))
    types = [e["type"] for e in events]

    assert len(calls) == 2                             # bounded
    assert "artifact" not in types
    assert types[-2:] == ["error", "done"]
    assert events[-1]["ok"] is False


async def test_no_tool_call_is_nudged_then_can_succeed():
    gw = FakeGateway([_no_tool("here's how you could..."), _tool_call("good")])
    executed = []

    async def execute(script):
        executed.append(script)
        return ExecResult(ok=True, preview_png_b64="P", exports={"stl": "L"})

    events = await _collect(run_generation("x", gateway=gw, execute=execute, max_attempts=3))

    assert executed == ["good"]                        # only the real script ran
    assert len(gw.calls) == 2                           # nudged once, then succeeded
    assert events[-1]["ok"] is True
    assert events[-1]["engine"] == "cadquery"
