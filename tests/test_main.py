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


def _client():
    app = create_app(gateway=FakeGateway(), execute=_fake_execute)
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


def test_root_serves_the_spa():
    # The single container serves the SPA same-origin (fullstack service).
    resp = _client().get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in resp.text.lower()
