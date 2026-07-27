"""Unit tests for the V1 self-correcting generation loop.

The loop is a real tool-calling agent loop: it asks the model for a run_cadquery
script, executes it in the sandbox, and if execution fails feeds the error back as
a tool result and asks for a fix — bounded to max_attempts, each a separate gateway
call (<290s). Success emits preview + artifacts; exhausting attempts emits a
terminal error. Dependency-injected fakes: no cadquery/network.
"""

import json

from app.agent.loop import ExecResult, run_generation
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
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
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


async def _collect(agen):
    return [ev async for ev in agen]


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
