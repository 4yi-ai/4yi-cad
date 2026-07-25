"""Unit tests for the MVP generation loop.

The loop is dependency-injected: a fake gateway returns a canned assistant
message (a run_cadquery tool call), and a fake executor returns a canned
ExecResult. We assert the *sequence of streamed events* and that the script from
the tool call is what gets executed. MVP has no self-correction — a failed
execute reports an error and ends (retry is V1).
"""

import json

from app.agent.loop import ExecResult, run_generation
from app.gateway import ChatCompletion


class FakeGateway:
    def __init__(self, completion: ChatCompletion):
        self._completion = completion
        self.calls: list[dict] = []

    async def chat_completion(self, messages, *, tools=None, tool_choice=None):
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        return self._completion


def _tool_call(script: str) -> ChatCompletion:
    return ChatCompletion(
        content=None,
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "run_cadquery",
                    "arguments": json.dumps({"script": script}),
                },
            }
        ],
    )


async def _collect(agen):
    return [ev async for ev in agen]


async def test_happy_path_emits_script_preview_artifact_done():
    gw = FakeGateway(_tool_call("box(10,10,10)"))
    executed = {}

    async def execute(script):
        executed["script"] = script
        return ExecResult(
            ok=True,
            preview_png_b64="UE5H",
            exports={"step": "/tmp/out.step", "stl": "/tmp/out.stl"},
        )

    events = await _collect(
        run_generation("make a 10mm cube", gateway=gw, execute=execute)
    )
    types = [e["type"] for e in events]

    assert executed["script"] == "box(10,10,10)"
    assert types == ["status", "script", "preview", "artifact", "artifact", "done"]
    preview = next(e for e in events if e["type"] == "preview")
    assert preview["png_b64"] == "UE5H"
    # exports travel inline as base64 (no server-side storage in MVP)
    arts = {e["format"]: e["data_b64"] for e in events if e["type"] == "artifact"}
    assert arts == {"step": "/tmp/out.step", "stl": "/tmp/out.stl"}


async def test_execute_failure_emits_error_and_no_artifacts():
    gw = FakeGateway(_tool_call("bad_script()"))

    async def execute(script):
        return ExecResult(ok=False, error="NameError: bad_script")

    events = await _collect(run_generation("x", gateway=gw, execute=execute))
    types = [e["type"] for e in events]

    assert "error" in types
    assert "artifact" not in types
    assert "preview" not in types
    err = next(e for e in events if e["type"] == "error")
    assert "NameError" in err["message"]


async def test_no_tool_call_is_reported_as_error():
    gw = FakeGateway(ChatCompletion(content="I can't do that", tool_calls=[]))

    async def execute(script):  # should never be called
        raise AssertionError("execute must not run without a script")

    events = await _collect(run_generation("x", gateway=gw, execute=execute))
    types = [e["type"] for e in events]

    assert "error" in types
    assert "artifact" not in types


async def test_run_cadquery_tool_is_offered_to_the_model():
    gw = FakeGateway(_tool_call("box(1,1,1)"))

    async def execute(script):
        return ExecResult(ok=True, preview_png_b64="x", exports={})

    await _collect(run_generation("x", gateway=gw, execute=execute))

    tool_names = {t["function"]["name"] for t in gw.calls[0]["tools"]}
    assert "run_cadquery" in tool_names
