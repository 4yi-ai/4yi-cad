"""Tests for the FastAPI surface.

create_app is dependency-injected with a gateway + executor so the app can be
built and driven without env vars, cadquery, or the network. /healthz must be
trivial and independent of config (it is the k8s readiness/liveness probe).
"""

import json

from fastapi.testclient import TestClient

from app.agent.loop import ExecResult
from app.cad.design_state import default_design_state, render_cadquery_script
from app.cad.runner import SandboxResult
from app.gateway import ChatCompletion
from app.main import create_app
from app.session_store import SqliteSessionStore


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


def _client_with_store(tmp_path, *, execute=_fake_execute):
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    app = create_app(gateway=FakeGateway(), execute=execute, session_store=store)
    return TestClient(app)


def test_healthz_is_200_without_config():
    # No env set — must still be healthy.
    resp = _client().get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_session_api_creates_reads_and_appends_versions(tmp_path):
    client = _client_with_store(tmp_path)
    state = default_design_state()
    script = render_cadquery_script(state)

    created = client.post("/api/sessions", json={"title": "Workbench"}).json()
    session_id = created["session"]["id"]

    resp = client.post(
        f"/api/sessions/{session_id}/versions",
        json={
            "intent": "create",
            "user_instruction": "initial",
            "design_state": state.model_dump(),
            "script": script,
            "metadata": {"preview_mode": "design_state"},
        },
    )

    assert resp.status_code == 200
    version = resp.json()["version"]
    assert version["version_number"] == 1
    assert version["metadata"]["preview_mode"] == "design_state"

    loaded = client.get(f"/api/sessions/{session_id}")
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["session"]["active_version_id"] == version["id"]
    assert body["active_version"]["script"] == script
    assert body["active_version"]["geometry_summary"]["bbox_mm"] == [60, 40, 14]
    assert body["versions"][0]["intent"] == "create"


def test_session_api_returns_404_for_missing_session(tmp_path):
    client = _client_with_store(tmp_path)

    assert client.get("/api/sessions/missing").status_code == 404
    resp = client.post(
        "/api/sessions/missing/versions",
        json={
            "intent": "create",
            "design_state": default_design_state().model_dump(),
            "script": "result = None\n",
        },
    )

    assert resp.status_code == 404


def test_freecad_smoke_endpoint_reports_sandbox_result(monkeypatch):
    def fake_run_freecad_sandboxed(*args, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.0.0",
                "exports": {"step": "STEP", "stl": "STL"},
                "preview_png_b64": None,
            },
        )

    monkeypatch.setattr("app.main.run_freecad_sandboxed", fake_run_freecad_sandboxed)

    resp = _client().get("/api/freecad/smoke")

    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "freecad_version": "1.0.0",
        "exports": ["step", "stl"],
        "preview": False,
        "error": None,
    }


def test_freecad_smoke_endpoint_reports_missing_runtime(monkeypatch):
    def fake_run_freecad_sandboxed(*args, **kwargs):
        return SandboxResult(success=False, error="FreeCADCmd unavailable")

    monkeypatch.setattr("app.main.run_freecad_sandboxed", fake_run_freecad_sandboxed)

    resp = _client().get("/api/freecad/smoke")

    assert resp.status_code == 200
    assert resp.json() == {
        "ok": False,
        "error": "FreeCADCmd unavailable",
        "timed_out": False,
    }


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
    assert '"parameters"' in body


def test_generate_requires_prompt():
    resp = _client().post("/api/generate", json={})
    assert resp.status_code == 422


def test_generate_reports_missing_gateway_config_without_500(monkeypatch):
    for name in ("OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_API_KEY", "TEXT_MODEL"):
        monkeypatch.delenv(name, raising=False)

    client = TestClient(create_app())
    resp = client.post("/api/generate", json={"prompt": "make a chair"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: error" in resp.text
    assert "Missing required environment variable" in resp.text
    assert "event: done" in resp.text


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


def test_script_patch_updates_generated_script_parameter_and_renders():
    executed = []

    async def execute(script):
        executed.append(script)
        return ExecResult(ok=True, preview_png_b64="UE5H", exports={"stl": "mesh"})

    script = (
        "import cadquery as cq\n"
        "sofa_length, sofa_depth = 200, 80\n"
        "seat_height = 38\n"
        "result = cq.Workplane('XY').box(sofa_length, sofa_depth, seat_height)\n"
    )

    resp = _client(execute=execute).post(
        "/api/script/patch",
        json={"script": script, "patches": [{"name": "sofa_depth", "value": 95}]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "sofa_length, sofa_depth = 200, 95" in body["script"]
    assert executed == [body["script"]]
    assert {param["name"]: param["value"] for param in body["parameters"]}["sofa_depth"] == 95
    assert body["exports"] == {"stl": "mesh"}


def test_script_patch_rejects_non_editable_parameter():
    resp = _client().post(
        "/api/script/patch",
        json={
            "script": "length = 10\nresult = None\n",
            "patches": [{"name": "width", "value": 20}],
        },
    )

    assert resp.status_code == 422
    assert "unknown or non-editable" in resp.json()["detail"]


def test_root_serves_the_spa():
    # The single container serves the SPA same-origin (fullstack service).
    resp = _client().get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in resp.text.lower()
