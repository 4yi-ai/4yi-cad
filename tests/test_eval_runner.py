import base64
import json

import pytest

from app.agent.loop import ExecResult
from app.evals.corpus import EvalCase
from app.gateway import ChatCompletion
from scripts.eval.run_eval import EvalBudgetExceeded, MeteredGateway, run_case

TETRA_STL_B64 = base64.b64encode(
    b"solid t\nfacet normal 0 0 -1\n outer loop\n  vertex 0 0 0\n  vertex 0 1 0\n"
    b"  vertex 1 0 0\n endloop\nendfacet\nfacet normal 0 -1 0\n outer loop\n"
    b"  vertex 0 0 0\n  vertex 1 0 0\n  vertex 0 0 1\n endloop\nendfacet\n"
    b"facet normal -1 0 0\n outer loop\n  vertex 0 0 0\n  vertex 0 0 1\n"
    b"  vertex 0 1 0\n endloop\nendfacet\nfacet normal 1 1 1\n outer loop\n"
    b"  vertex 1 0 0\n  vertex 0 1 0\n  vertex 0 0 1\n endloop\nendfacet\nendsolid t\n"
).decode()
STEP_B64 = base64.b64encode(b"ISO-10303-21;\nENDSEC;\nEND-ISO-10303-21;\n").decode()


class ScriptedGateway:
    def __init__(self, usage_per_call=100):
        self.calls = 0
        self._usage = usage_per_call

    async def chat_completion(self, messages, *, tools=None, tool_choice=None):
        self.calls += 1
        return ChatCompletion(
            content=None,
            tool_calls=[{
                "id": f"call-{self.calls}",
                "function": {"name": "run_cadquery",
                             "arguments": json.dumps({"script": "result = 1"})},
            }],
            raw={"usage": {"total_tokens": self._usage}},
        )


async def ok_execute(script: str) -> ExecResult:
    return ExecResult(ok=True, exports={"stl": TETRA_STL_B64, "step": STEP_B64})


CASE = EvalCase(id="m1-t", domain="mechanical", tier="t1", prompt="一个法兰", timeout_s=30)


async def test_run_case_success_writes_artifacts_and_record(tmp_path):
    record = await run_case(
        CASE,
        gateway=ScriptedGateway(),
        execute=ok_execute,
        execute_freecad=None,
        run_dir=tmp_path,
    )
    assert record["case_id"] == "m1-t"
    assert record["prompt"] == "一个法兰"
    assert record["l1_ok"] is True
    assert record["l2_ok"] is True
    assert (tmp_path / "artifacts" / "model.stl").exists()
    assert (tmp_path / "artifacts" / "model.step").exists()
    assert record["tokens"]["total_tokens"] == 0  # plain gateway: no meter attached


async def test_metered_gateway_budget(tmp_path):
    metered = MeteredGateway(ScriptedGateway(usage_per_call=600), max_total_tokens=1000)
    await metered.chat_completion([])
    with pytest.raises(EvalBudgetExceeded):
        await metered.chat_completion([])
    assert metered.total_tokens == 600


async def test_run_case_survives_executor_crash(tmp_path):
    async def boom(script: str) -> ExecResult:
        raise RuntimeError("worker exploded")

    record = await run_case(
        CASE, gateway=ScriptedGateway(), execute=boom, execute_freecad=None,
        run_dir=tmp_path,
    )
    assert record["l1_ok"] is False
    assert "worker exploded" in (record["error"] or "")


async def test_run_case_survives_scoring_crash(tmp_path, monkeypatch):
    def boom_scorer(*args, **kwargs):
        raise RuntimeError("bad scorer")

    monkeypatch.setattr("scripts.eval.run_eval.score_run", boom_scorer)

    record = await run_case(
        CASE, gateway=ScriptedGateway(), execute=ok_execute, execute_freecad=None,
        run_dir=tmp_path,
    )
    assert record["l1_ok"] is False
    assert "bad scorer" in (record["error"] or "")
