"""Tests for the FastAPI surface.

create_app is dependency-injected with a gateway + executor so the app can be
built and driven without env vars, cadquery, or the network. /healthz must be
trivial and independent of config (it is the k8s readiness/liveness probe).
"""

import json

from fastapi.testclient import TestClient

from app.agent.loop import ExecResult
from app.gateway import ChatCompletion
from app.main import create_app


class FakeGateway:
    async def chat_completion(self, messages, *, tools=None, tool_choice=None):
        return ChatCompletion(
            content=None,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "run_cadquery",
                        "arguments": json.dumps({"script": "result = box(1,1,1)"}),
                    },
                }
            ],
        )


async def _fake_execute(script):
    return ExecResult(ok=True, preview_png_b64="UE5H", exports={"step": "/tmp/a.step"})


async def _raising_execute(script):
    raise RuntimeError("sandbox unavailable")


def _client(*, execute=_fake_execute):
    app = create_app(gateway=FakeGateway(), execute=execute)
    return TestClient(app)


def test_healthz_is_200_without_config():
    # No env set — must still be healthy.
    resp = _client().get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_generate_streams_sse_events():
    resp = _client().post("/api/generate", json={"prompt": "make a 1mm cube"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    body = resp.text
    assert "event: status" in body
    assert "event: script" in body
    assert "event: preview" in body
    assert "event: artifact" in body
    assert "event: done" in body
    # the injected script should appear in the streamed script event
    assert "result = box(1,1,1)" in body


def test_generate_requires_prompt():
    resp = _client().post("/api/generate", json={})
    assert resp.status_code == 422


def test_design_initial_returns_state_and_script():
    resp = _client().get("/api/design/initial")

    assert resp.status_code == 200
    body = resp.json()
    assert body["design_state"]["parameters"]["plate_length"] == 60
    assert "plate_length = 60" in body["script"]
    assert body["geometry_summary"]["bbox_mm"] == [60, 40, 14]


def test_design_patch_applies_update_parameter_without_llm():
    initial = _client().get("/api/design/initial").json()["design_state"]

    resp = _client().post(
        "/api/design/patch",
        json={
            "design_state": initial,
            "patches": [{"op": "update_parameter", "name": "hole_d", "value": 6}],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["design_state"]["parameters"]["hole_d"] == 6
    assert "hole_d = 6" in body["script"]


def test_design_patch_rejects_semantically_invalid_patch():
    initial = _client().get("/api/design/initial").json()["design_state"]

    resp = _client().post(
        "/api/design/patch",
        json={"design_state": initial, "patches": [{"op": "update_parameter"}]},
    )

    assert resp.status_code == 422
    assert "name and value" in resp.json()["detail"]


def test_design_render_uses_injected_executor():
    initial = _client().get("/api/design/initial").json()["design_state"]

    resp = _client().post("/api/design/render", json={"design_state": initial})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["preview_png_b64"] == "UE5H"
    assert body["exports"] == {"step": "/tmp/a.step"}
    assert "result =" in body["script"]


def test_design_render_reports_executor_failure_without_500():
    client = _client(execute=_raising_execute)
    initial = client.get("/api/design/initial").json()["design_state"]

    resp = client.post("/api/design/render", json={"design_state": initial})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["exports"] == {}
    assert "sandbox unavailable" in body["error"]


def test_root_serves_the_spa():
    # The single container serves the SPA same-origin (fullstack service).
    resp = _client().get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in resp.text.lower()
