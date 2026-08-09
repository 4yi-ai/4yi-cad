"""Tests for the FastAPI surface.

create_app is dependency-injected with a gateway + executor so the app can be
built and driven without env vars, cadquery, or the network. /healthz must be
trivial and independent of config (it is the k8s readiness/liveness probe).
"""

import base64
import json

import httpx
from fastapi.testclient import TestClient

from app.agent.loop import ExecResult, MAX_CHAT_HISTORY_MESSAGE_CHARS
from app.artifact_store import FileArtifactStore
from app.cad.design_state import default_design_state, render_cadquery_script
from app.cad.runner import SandboxResult
from app.freecad_gui_orchestrator import FreeCadGuiSessionLaunch
from app.gateway import ChatCompletion
from app.main import (
    _freecad_edit_delivery_error,
    _freecad_edit_script_contract_error,
    _freecad_gui_proxy_target_url,
    create_app,
    default_freecad_document_edit_execute,
    default_freecad_execute,
)
from app.session_store import SqliteSessionStore


class FakeGateway:
    def __init__(self, *, tool_name: str = "run_cadquery", script: str = "result = box(1,1,1)"):
        self.tool_name = tool_name
        self.script = script
        self.calls: list[dict] = []

    async def chat_completion(self, messages, *, tools=None, tool_choice=None):
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        return ChatCompletion(
            content=None,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": self.tool_name,
                        "arguments": json.dumps({"script": self.script}),
                    },
                }
            ],
        )


async def _fake_execute(script):
    return ExecResult(ok=True, preview_png_b64="UE5H", exports={"step": "/tmp/a.step"})


async def _raising_execute(script):
    raise RuntimeError("sandbox unavailable")


async def _fake_freecad_execute(script):
    return ExecResult(
        ok=True,
        engine="freecad",
        freecad_version="1.1.3",
        preview_png_b64="RlBORw==",
        exports={"step": "STEP", "stl": "STL", "viewer_scene": _viewer_scene_b64("plot", "building")},
    )


def _viewer_scene_b64(*roles: str, object_count: int = 18) -> str:
    roles = roles or ("plot", "building")
    objects = [
        {
            "name": f"{roles[index % len(roles)]}_{index}",
            "style": {"semantic_role": roles[index % len(roles)]},
            "faces": [{"reference": "Face1", "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "triangles": [[0, 1, 2]]}],
        }
        for index in range(object_count)
    ]
    return base64.b64encode(json.dumps({"schema": "freecad.viewer_scene.v1", "objects": objects}).encode("utf-8")).decode("ascii")


def _client(*, execute=_fake_execute, freecad_execute=_fake_freecad_execute, gateway=None):
    app = create_app(
        gateway=gateway or FakeGateway(),
        execute=execute,
        freecad_execute=freecad_execute,
    )
    return TestClient(app)


class FakeFreeCadGuiOrchestrator:
    def __init__(self):
        self.started: list[dict] = []
        self.stopped: list[str] = []

    def enabled(self) -> bool:
        return True

    def start_session(
        self,
        *,
        remote_session_id,
        workbench_session_id,
        base_version_id,
        fcstd_b64=None,
    ):
        self.started.append(
            {
                "remote_session_id": remote_session_id,
                "workbench_session_id": workbench_session_id,
                "base_version_id": base_version_id,
                "fcstd_b64": fcstd_b64,
            }
        )
        return FreeCadGuiSessionLaunch(
            status="ready",
            remote_url=f"http://desktop.test/{remote_session_id}",
            bridge_status="pending",
            metadata={
                "orchestrator_backend": "fake",
                "container_name": f"fake-{remote_session_id}",
            },
        )

    def stop_session(self, *, remote_session_id):
        self.stopped.append(remote_session_id)
        return {
            "backend": "fake",
            "container_name": f"fake-{remote_session_id}",
            "stopped": True,
        }


def _client_with_store(
    tmp_path,
    *,
    execute=_fake_execute,
    freecad_execute=_fake_freecad_execute,
    gateway=None,
    freecad_gui_orchestrator=None,
):
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    app = create_app(
        gateway=gateway or FakeGateway(),
        execute=execute,
        freecad_execute=freecad_execute,
        session_store=store,
        artifact_store=artifacts,
        freecad_gui_orchestrator=freecad_gui_orchestrator,
    )
    return TestClient(app)


FREECAD_DOC_SUMMARY = {
    "document": {"name": "Imported", "label": "Imported", "file_name": "source.FCStd"},
    "objects": [
        {
            "name": "Box",
            "label": "Box",
            "type_id": "Part::Box",
            "shape": {
                "valid": True,
                "bbox": {"min": [0, 0, 0], "max": [10, 8, 6], "size": [10, 8, 6]},
                "volume": 480.0,
                "solid_count": 1,
                "face_count": 6,
                "edge_count": 12,
                "vertex_count": 8,
            },
        }
    ],
    "geometry": {
        "object_count": 1,
        "shape_object_count": 1,
        "valid": True,
        "bbox": {"min": [0, 0, 0], "max": [10, 8, 6], "size": [10, 8, 6]},
        "volume": 480.0,
        "solid_count": 1,
        "face_count": 6,
        "edge_count": 12,
        "vertex_count": 8,
    },
    "feature_tree": {
        "roots": [{"name": "Box", "label": "Box", "type_id": "Part::Box"}],
        "nodes": [
            {
                "object": {"name": "Box", "label": "Box", "type_id": "Part::Box"},
                "kind": "part_primitive",
                "parents": [],
                "children": [],
                "tip": None,
                "placement": {
                    "base": [0, 0, 0],
                    "axis": [0, 0, 1],
                    "angle_degrees": 0,
                },
            }
        ],
    },
    "sketches": [],
    "assemblies": [],
    "techdraw": [],
}


def test_healthz_is_200_without_config():
    # No env set — must still be healthy.
    resp = _client().get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_production_smoke_reports_storage_and_worker_boundary(tmp_path):
    resp = _client_with_store(tmp_path).get("/api/production/smoke")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["durable_storage_configured"], bool)
    assert body["production_ready"] is False
    assert body["storage"]["session_db"]["writable"] is True
    assert body["storage"]["artifact_root"]["writable"] is True
    assert body["freecad_worker"]["mode"] == "single_container_subprocess"
    assert body["freecad_worker"]["split_service_configured"] is False
    assert body["freecad_worker"]["hardened_worker_service"] is False
    assert body["freecad_worker"]["security_controls"]["egress_blocked"] is False
    assert body["readiness"]["schema"] == "4yi-cad.production_readiness.v1"
    assert body["readiness"]["phase"] == "phase6"


def test_production_smoke_falls_back_when_platform_data_dir_is_unwritable(tmp_path, monkeypatch):
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory")
    monkeypatch.delenv("CAD_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("CAD_ARTIFACT_ROOT", raising=False)
    monkeypatch.setenv("CAD_DATA_DIR", str(blocked_parent / "data"))

    resp = _client().get("/api/production/smoke")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["durable_storage_configured"] is False
    assert body["storage"]["session_db"]["path"] == "/tmp/4yi-cad/sessions.sqlite3"
    assert body["storage"]["artifact_root"]["path"] == "/tmp/4yi-cad/artifacts"


def test_production_readiness_reports_phase6_release_gates(tmp_path):
    resp = _client_with_store(tmp_path).get("/api/production/readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["schema"] == "4yi-cad.production_readiness.v1"
    assert body["phase"] == "phase6"
    assert body["ok"] is True
    assert body["production_ready"] is False
    assert set(body["release_targets"]) == {
        "private_beta_ready",
        "public_beta_ready",
        "ga_ready",
    }
    assert body["summary"]["fail"] >= 1
    check_keys = {check["key"] for check in body["checks"]}
    assert {
        "gateway_contract",
        "storage_writable",
        "durable_storage",
        "freecad_upload_policy",
        "freecad_smoke_endpoint",
        "remote_gui_bridge",
        "bridge_observability",
        "worker_isolation",
        "license_gate",
    } <= check_keys
    assert body["runtime"]["openai_api_key_configured"] is False
    assert "xclaw-bsl-test" not in json.dumps(body)


def test_production_readiness_can_pass_when_release_env_is_configured(tmp_path, monkeypatch):
    import app.freecad_state as freecad_state

    monkeypatch.setattr(freecad_state, "_is_under_tmp", lambda path: False)
    eval_report_path = tmp_path / "eval-latest.json"
    eval_report_path.write_text(
        json.dumps(
            {
                "schema": "4yi-cad.eval_report.v1",
                "total_runs": 84,
                "thresholds_met": True,
                "metrics": {"success_rate": 0.95},
                "thresholds": {},
            }
        )
    )
    monkeypatch.setenv("CAD_EVAL_REPORT_PATH", str(eval_report_path))
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gateway.test/api/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "xclaw-bsl-test")
    monkeypatch.setenv("TEXT_MODEL", "test-model")
    monkeypatch.setenv("CAD_GUI_SESSION_BACKEND", "local_docker")
    monkeypatch.setenv("CAD_GUI_SESSION_CONTROL_PLANE_URL", "http://control.test")
    monkeypatch.setenv("FOURYI_FREECAD_WORKER_URL", "http://worker.test")
    monkeypatch.setenv("FOURYI_FREECAD_WORKER_EGRESS_BLOCKED", "1")
    monkeypatch.setenv("FOURYI_FREECAD_WORKER_READ_ONLY_ROOTFS", "1")
    monkeypatch.setenv("FOURYI_FREECAD_WORKER_SECCOMP_PROFILE", "runtime/default")
    monkeypatch.setenv("FOURYI_FREECAD_WORKER_TMPFS", "1")
    monkeypatch.setenv("FOURYI_CAD_LICENSE_REVIEW_ACCEPTED", "1")

    resp = _client_with_store(tmp_path).get("/api/production/readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["release_targets"]["private_beta_ready"] is True
    assert body["release_targets"]["public_beta_ready"] is True
    assert body["release_targets"]["ga_ready"] is True
    assert body["production_ready"] is True
    assert body["durable_storage_configured"] is True
    assert body["runtime"]["gateway_configured"] is True
    assert body["freecad_worker"]["hardened_worker_service"] is True
    assert body["remote_gui"]["ready"] is True
    assert body["license"]["review_accepted"] is True
    assert body["summary"]["fail"] == 0
    assert "xclaw-bsl-test" not in resp.text


def test_freecad_upload_policy_defaults_to_100mb(tmp_path, monkeypatch):
    monkeypatch.delenv("CAD_FREECAD_UPLOAD_MAX_BYTES", raising=False)
    monkeypatch.delenv("FOURYI_CAD_UPLOAD_MAX_BYTES", raising=False)
    resp = _client_with_store(tmp_path).get("/api/freecad/upload_policy")

    assert resp.status_code == 200
    body = resp.json()
    assert body["max_bytes"] == 100 * 1024 * 1024
    assert body["max_mb"] == 100
    assert "fcstd" in body["formats"]
    assert "step" in body["formats"]


def test_freecad_upload_policy_uses_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD_FREECAD_UPLOAD_MAX_BYTES", "524288000")

    resp = _client_with_store(tmp_path).get("/api/freecad/upload_policy")

    assert resp.status_code == 200
    assert resp.json()["max_bytes"] == 524288000


def test_freecad_import_model_rejects_oversize_payload_before_sandbox(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CAD_FREECAD_UPLOAD_MAX_BYTES", "5")

    def fake_import_sandboxed(*args, **kwargs):
        raise AssertionError("oversize upload must not reach FreeCAD sandbox")

    monkeypatch.setattr("app.main.run_freecad_import_sandboxed", fake_import_sandboxed)
    resp = _client_with_store(tmp_path).post(
        "/api/freecad/import_model",
        json={
            "format": "fcstd",
            "data_b64": "MTIzNDU2",
            "filename": "too-large.FCStd",
        },
    )

    assert resp.status_code == 413
    assert "max allowed is 5 bytes" in resp.json()["detail"]


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

    listed = client.get("/api/sessions?limit=10")
    assert listed.status_code == 200
    sessions = listed.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session"]["id"] == session_id
    assert sessions[0]["version_count"] == 1
    assert sessions[0]["active_version"]["id"] == version["id"]
    assert sessions[0]["active_version"]["metadata"]["preview_mode"] == "design_state"
    assert "script" not in sessions[0]["active_version"]
    assert "design_state" not in sessions[0]["active_version"]


def test_session_api_persists_artifacts_outside_sqlite(tmp_path):
    client = _client_with_store(tmp_path)
    state = default_design_state()
    script = render_cadquery_script(state)
    session_id = client.post("/api/sessions", json={"title": "Artifacts"}).json()["session"]["id"]

    resp = client.post(
        f"/api/sessions/{session_id}/versions",
        json={
            "intent": "create",
            "user_instruction": "initial",
            "design_state": state.model_dump(),
            "script": script,
            "metadata": {"preview_mode": "generated", "engine": "freecad"},
            "preview_png_b64": "UE5H",
            "artifacts": {"step": "U1RFUA==", "stl": "U1RM", "fcstd": "RkNTdGQ="},
        },
    )

    assert resp.status_code == 200
    version = resp.json()["version"]
    refs = version["metadata"]["artifact_refs"]
    assert set(refs) == {"preview", "step", "stl", "fcstd"}
    assert refs["fcstd"]["filename"] == "model.FCStd"
    assert "UE5H" not in json.dumps(version["metadata"])

    assert client.get(refs["preview"]["url"]).content == b"PNG"
    assert client.get(refs["step"]["url"]).content == b"STEP"
    assert client.get(refs["stl"]["url"]).content == b"STL"
    assert client.get(refs["fcstd"]["url"]).content == b"FCStd"


def test_session_api_rolls_back_to_prior_version_and_copies_artifacts(tmp_path):
    client = _client_with_store(tmp_path)
    state = default_design_state()
    script = render_cadquery_script(state)
    session_id = client.post("/api/sessions", json={"title": "Rollback"}).json()["session"]["id"]

    first = client.post(
        f"/api/sessions/{session_id}/versions",
        json={
            "intent": "create",
            "user_instruction": "v1",
            "design_state": state.model_dump(),
            "script": script,
            "metadata": {"preview_mode": "generated", "engine": "cadquery"},
            "preview_png_b64": "T05F",
            "artifacts": {"step": "T05F"},
        },
    ).json()["version"]
    second = client.post(
        f"/api/sessions/{session_id}/versions",
        json={
            "intent": "modify",
            "user_instruction": "v2",
            "design_state": state.model_dump(),
            "script": script.replace("plate_length = 60", "plate_length = 90"),
            "metadata": {"preview_mode": "generated", "engine": "cadquery"},
            "preview_png_b64": "VFdP",
            "artifacts": {"step": "VFdP"},
        },
    ).json()["version"]

    resp = client.post(
        f"/api/sessions/{session_id}/rollback",
        json={"version_id": first["id"], "user_instruction": "restore v1"},
    )

    assert resp.status_code == 200
    rollback = resp.json()["version"]
    assert rollback["version_number"] == 3
    assert rollback["parent_version_id"] == second["id"]
    assert rollback["intent"] == "rollback"
    assert rollback["patch"]["op"] == "rollback_to_version"
    assert rollback["script"] == first["script"]

    loaded = client.get(f"/api/sessions/{session_id}").json()
    assert loaded["session"]["active_version_id"] == rollback["id"]
    assert client.get(rollback["metadata"]["artifact_refs"]["step"]["url"]).content == b"ONE"


def test_freecad_remote_session_api_creates_reuses_and_queues_commands(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "CAD_REMOTE_DESKTOP_BASE_URL",
        "https://desktop.example.test/freecad/{session_id}",
    )
    client = _client_with_store(tmp_path)
    state = default_design_state()
    script = render_cadquery_script(state)
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "Remote GUI"},
    ).json()["session"]["id"]
    version = client.post(
        f"/api/sessions/{workbench_session_id}/versions",
        json={
            "intent": "create",
            "design_state": state.model_dump(),
            "script": script,
        },
    ).json()["version"]

    created = client.post(
        "/api/freecad/sessions",
        json={
            "session_id": workbench_session_id,
            "version_id": version["id"],
            "reuse": True,
        },
    )

    assert created.status_code == 200
    remote = created.json()
    assert remote["status"] == "ready"
    assert remote["bridge_status"] == "pending"
    assert remote["remote_url"].endswith(remote["session_id"])
    assert remote["current_version_id"] == version["id"]
    assert remote["reused"] is False

    reused = client.post(
        "/api/freecad/sessions",
        json={
            "session_id": workbench_session_id,
            "version_id": version["id"],
            "reuse": True,
        },
    ).json()
    assert reused["session_id"] == remote["session_id"]
    assert reused["reused"] is True

    command = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/commands",
        json={
            "op": "run_macro",
            "base_version_id": version["id"],
            "input": {"prompt": "change selected hole to 6mm"},
        },
    )

    assert command.status_code == 200
    body = command.json()
    assert body["command_id"].startswith("cmd_")
    assert body["status"] == "pending"
    assert body["command"]["status"] == "pending"
    assert body["event"]["event_type"] == "bridge_command_queued"
    assert body["event"]["metadata"]["op"] == "run_macro"

    loaded = client.get(f"/api/freecad/sessions/{remote['session_id']}").json()
    assert [event["event_type"] for event in loaded["events"]] == [
        "session_requested",
        "session_reused",
        "bridge_command_queued",
    ]


def test_freecad_shared_service_session_uses_fixed_id_and_load_model_command(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CAD_GUI_SESSION_BACKEND", "shared_service")
    monkeypatch.setenv("CAD_SHARED_FREECAD_SESSION_ID", "shared-freecad-gui")
    monkeypatch.setenv(
        "CAD_REMOTE_DESKTOP_BASE_URL",
        "/freecad/vnc.html?autoconnect=1&resize=remote&path=freecad/websockify",
    )
    monkeypatch.setenv("CAD_GUI_SESSION_CONTROL_PLANE_URL", "http://app-4yi-cad:8080")
    monkeypatch.setenv(
        "CAD_FREECAD_GUI_UPSTREAM_URL",
        "http://app-4yi-cad-freecad-gui:6080",
    )
    client = _client_with_store(tmp_path)
    state = default_design_state()
    script = render_cadquery_script(state)
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "Shared FreeCAD GUI"},
    ).json()["session"]["id"]
    version = client.post(
        f"/api/sessions/{workbench_session_id}/versions",
        json={
            "intent": "create",
            "design_state": state.model_dump(),
            "script": script,
            "artifacts": {"fcstd": "RkNTdGQ="},
        },
    ).json()["version"]

    created = client.post(
        "/api/freecad/sessions",
        json={
            "session_id": workbench_session_id,
            "version_id": version["id"],
            "reuse": True,
        },
    )

    assert created.status_code == 200
    remote = created.json()
    assert remote["session_id"] == "shared-freecad-gui"
    assert remote["status"] == "ready"
    assert remote["remote_url"] == (
        "/freecad/vnc.html?autoconnect=1&resize=remote&path=freecad/websockify"
    )
    assert remote["metadata"]["gui_session_backend"] == "shared_service"
    assert remote["metadata"]["shared_remote_session_id"] == "shared-freecad-gui"
    assert remote["metadata"]["load_model_required"] is True
    assert remote["metadata"]["freecad_gui_proxy_configured"] is True

    queued = client.post(
        "/api/freecad/sessions/shared-freecad-gui/commands",
        json={
            "op": "load_model",
            "base_version_id": version["id"],
            "input": {
                "fcstd_url": version["metadata"]["artifact_refs"]["fcstd"]["url"],
                "filename": "model.FCStd",
                "version_id": version["id"],
            },
        },
    )

    assert queued.status_code == 200
    assert queued.json()["command"]["op"] == "load_model"

    readiness = client.get("/api/production/readiness").json()
    assert readiness["remote_gui"]["ready"] is True
    assert readiness["remote_gui"]["shared_service_configured"] is True
    assert readiness["remote_gui"]["freecad_gui_proxy_required"] is True
    assert readiness["remote_gui"]["freecad_gui_proxy_configured"] is True


def test_freecad_first_entry_redirects_root_and_keeps_workbench_route(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CAD_FREECAD_FIRST_ENTRY", "1")
    monkeypatch.setenv(
        "CAD_REMOTE_DESKTOP_BASE_URL",
        "/freecad/vnc.html?autoconnect=1&resize=remote&path=freecad/websockify",
    )

    client = _client_with_store(tmp_path)
    root = client.get("/", follow_redirects=False)
    workbench = client.get("/workbench")
    readiness = client.get("/api/production/readiness").json()

    assert root.status_code == 307
    assert root.headers["location"] == (
        "/freecad/vnc.html?autoconnect=1&resize=remote&path=freecad/websockify"
    )
    assert workbench.status_code == 200
    assert workbench.headers["content-type"].startswith("text/html")
    assert readiness["entrypoint"]["freecad_first_enabled"] is True
    assert readiness["entrypoint"]["web_workbench_url"] == "/workbench"


def test_shared_freecad_bridge_heartbeat_auto_creates_fixed_session(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CAD_GUI_SESSION_BACKEND", "shared_service")
    monkeypatch.setenv("CAD_SHARED_FREECAD_SESSION_ID", "shared-freecad-gui")
    monkeypatch.setenv(
        "CAD_REMOTE_DESKTOP_BASE_URL",
        "/freecad/vnc.html?autoconnect=1&resize=remote&path=freecad/websockify",
    )

    client = _client_with_store(tmp_path)
    heartbeat = client.post(
        "/api/freecad/sessions/shared-freecad-gui/bridge/heartbeat",
        json={
            "bridge_id": "bridge_1",
            "freecad_version": "1.1.0",
            "workbench": "Part Design",
        },
    )

    assert heartbeat.status_code == 200
    body = heartbeat.json()
    assert body["session"]["session_id"] == "shared-freecad-gui"
    assert body["session"]["bridge_status"] == "connected"
    assert body["session"]["metadata"]["auto_created"] is True
    assert body["session"]["remote_url"] == (
        "/freecad/vnc.html?autoconnect=1&resize=remote&path=freecad/websockify"
    )
    loaded = client.get("/api/freecad/sessions/shared-freecad-gui").json()
    assert [event["event_type"] for event in loaded["events"]][:2] == [
        "session_auto_created",
        "bridge_heartbeat",
    ]


def test_freecad_gui_proxy_target_url_builds_http_and_websocket_urls(monkeypatch):
    monkeypatch.setenv(
        "CAD_FREECAD_GUI_UPSTREAM_URL",
        "http://app-4yi-cad-freecad-gui:6080/novnc",
    )

    assert _freecad_gui_proxy_target_url("vnc.html", b"autoconnect=1") == (
        "http://app-4yi-cad-freecad-gui:6080/novnc/vnc.html?autoconnect=1"
    )
    assert _freecad_gui_proxy_target_url("websockify", b"", websocket=True) == (
        "ws://app-4yi-cad-freecad-gui:6080/novnc/websockify"
    )


def test_freecad_gui_http_proxy_forwards_to_internal_novnc(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, *, headers):
            requests.append({"method": method, "url": url, "headers": headers})
            return httpx.Response(
                200,
                content=b"<html>noVNC</html>",
                headers={
                    "content-type": "text/html; charset=utf-8",
                    "connection": "close",
                },
            )

    monkeypatch.setenv("CAD_FREECAD_GUI_UPSTREAM_URL", "http://freecad-gui:6080")
    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    resp = _client().get("/freecad/vnc.html?autoconnect=1")

    assert resp.status_code == 200
    assert resp.text == "<html>noVNC</html>"
    assert requests == [
        {
            "method": "GET",
            "url": "http://freecad-gui:6080/vnc.html?autoconnect=1",
            "headers": requests[0]["headers"],
        }
    ]
    assert "host" not in {key.lower() for key in requests[0]["headers"]}


def test_freecad_gui_http_proxy_requires_upstream(monkeypatch):
    monkeypatch.delenv("CAD_FREECAD_GUI_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("FOURYI_CAD_FREECAD_GUI_UPSTREAM_URL", raising=False)

    resp = _client().get("/freecad/vnc.html")

    assert resp.status_code == 503
    assert resp.json()["detail"] == "FreeCAD GUI proxy upstream is not configured"


def test_freecad_bridge_heartbeat_poll_and_command_result(tmp_path):
    client = _client_with_store(tmp_path)
    state = default_design_state()
    script = render_cadquery_script(state)
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "Bridge protocol"},
    ).json()["session"]["id"]
    version = client.post(
        f"/api/sessions/{workbench_session_id}/versions",
        json={
            "intent": "create",
            "design_state": state.model_dump(),
            "script": script,
        },
    ).json()["version"]
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id, "version_id": version["id"]},
    ).json()

    heartbeat = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/bridge/heartbeat",
        json={
            "bridge_id": "bridge_1",
            "freecad_version": "1.0.0",
            "document_name": "model.FCStd",
            "workbench": "PartDesignWorkbench",
            "selection": {
                "objects": [{"name": "Hole001", "label": "Mounting hole"}],
                "active_object": {"name": "Hole001", "label": "Mounting hole"},
            },
            "document_tree": {
                "document": {"name": "model.FCStd"},
                "objects": [{"name": "Body", "type_id": "PartDesign::Body"}],
            },
            "console_tail": ["ready"],
            "capabilities": ["inspect_document", "run_macro"],
        },
    )

    assert heartbeat.status_code == 200
    heartbeat_session = heartbeat.json()["session"]
    assert heartbeat_session["bridge_status"] == "connected"
    assert heartbeat_session["metadata"]["bridge"]["bridge_id"] == "bridge_1"
    assert heartbeat_session["metadata"]["bridge"]["freecad_version"] == "1.0.0"
    assert heartbeat_session["metadata"]["bridge"]["workbench"] == "PartDesignWorkbench"
    assert heartbeat_session["metadata"]["bridge"]["selection"]["active_object"]["name"] == "Hole001"

    context = client.get(
        f"/api/freecad/sessions/{remote['session_id']}/bridge/context",
    )
    assert context.status_code == 200
    assert context.json()["selection"]["active_object"]["name"] == "Hole001"
    assert context.json()["document_tree"]["document"]["name"] == "model.FCStd"

    queued = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/commands",
        json={
            "op": "inspect_document",
            "base_version_id": version["id"],
            "input": {"selection": "Box"},
        },
    ).json()

    poll = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/bridge/poll",
        json={"bridge_id": "bridge_1", "max_commands": 5},
    )

    assert poll.status_code == 200
    poll_body = poll.json()
    assert poll_body["commands"] == [
        {
            **queued["command"],
            "status": "dispatched",
            "dispatched_at": poll_body["commands"][0]["dispatched_at"],
        }
    ]
    assert poll_body["commands"][0]["command_id"] == queued["command_id"]
    assert poll_body["event"]["metadata"]["command_count"] == 1

    empty_poll = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/bridge/poll",
        json={"bridge_id": "bridge_1", "max_commands": 5},
    ).json()
    assert empty_poll["commands"] == []

    result = client.post(
        (
            f"/api/freecad/sessions/{remote['session_id']}"
            f"/bridge/commands/{queued['command_id']}/result"
        ),
        json={
            "status": "completed",
            "result": {"document_summary": {"object_count": 1}},
            "metadata": {"bridge_id": "bridge_1"},
        },
    )

    assert result.status_code == 200
    result_body = result.json()
    assert result_body["command"]["status"] == "completed"
    assert result_body["command"]["result"]["document_summary"]["object_count"] == 1
    assert result_body["event"]["event_type"] == "bridge_command_completed"

    command_lookup = client.get(
        f"/api/freecad/sessions/{remote['session_id']}/commands/{queued['command_id']}",
    )
    assert command_lookup.status_code == 200
    assert command_lookup.json()["command"]["status"] == "completed"

    loaded = client.get(f"/api/freecad/sessions/{remote['session_id']}").json()
    assert [event["event_type"] for event in loaded["events"]] == [
        "session_requested",
        "bridge_heartbeat",
        "bridge_command_queued",
        "bridge_poll",
        "bridge_poll",
        "bridge_command_completed",
    ]


def test_freecad_panel_action_records_and_can_queue_macro_command(tmp_path):
    client = _client_with_store(tmp_path)
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "FreeCAD panel action"},
    ).json()["session"]["id"]
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id},
    ).json()

    explain = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/panel/actions",
        json={
            "action": "explain_object",
            "prompt": "explain selection",
            "selection": {"objects": [{"name": "Box"}]},
        },
    )

    assert explain.status_code == 200
    assert explain.json()["status"] == "recorded"
    assert explain.json()["command"] is None
    assert explain.json()["event"]["event_type"] == "panel_action_requested"

    prompt = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/panel/actions",
        json={
            "action": "prompt",
            "prompt": "make selected hole 6mm",
            "selection": {"objects": [{"name": "Hole001"}]},
            "macro": "print('change')",
            "metadata": {"source": "freecad_panel_test"},
        },
    )

    assert prompt.status_code == 200
    prompt_body = prompt.json()
    assert prompt_body["status"] == "queued"
    assert prompt_body["command"]["op"] == "run_macro"
    assert prompt_body["command"]["metadata"]["source"] == "freecad_panel"
    assert prompt_body["command_event"]["event_type"] == "bridge_command_queued"

    poll = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/bridge/poll",
        json={"bridge_id": "bridge_1"},
    )
    assert poll.status_code == 200
    assert poll.json()["commands"][0]["command_id"] == prompt_body["command"]["command_id"]


def test_freecad_panel_prompt_generates_version_and_queues_load_model(
    tmp_path,
    monkeypatch,
):
    async def fake_inspect_fcstd(fcstd_b64):
        assert fcstd_b64 == "RkNTdGQ="
        return {
            "ok": True,
            "engine": "freecad",
            "error": None,
            "freecad_version": "1.1.0",
            "document_summary": FREECAD_DOC_SUMMARY,
        }

    async def fake_freecad_execute(script):
        return ExecResult(
            ok=True,
            engine="freecad",
            freecad_version="1.1.0",
            preview_png_b64="UE5H",
            exports={"fcstd": "RkNTdGQ=", "step": "U1RFUA=="},
        )

    monkeypatch.setattr("app.main._inspect_fcstd_b64", fake_inspect_fcstd)
    client = _client_with_store(
        tmp_path,
        gateway=FakeGateway(tool_name="run_freecad", script="import FreeCAD\nresult=[]\n"),
        freecad_execute=fake_freecad_execute,
    )
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "FreeCAD panel generate"},
    ).json()["session"]["id"]
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id},
    ).json()

    prompt = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/panel/actions",
        json={
            "action": "prompt",
            "prompt": "生成一个入口门厅模型",
            "selection": {},
            "metadata": {"source": "freecad_panel_test", "document_tree": {"objects": []}},
        },
    )

    assert prompt.status_code == 200
    body = prompt.json()
    assert body["status"] == "queued"
    assert body["generated_version"]["metadata"]["source"] == "freecad_panel_agent"
    assert body["generated_version"]["metadata"]["artifact_refs"]["fcstd"]["bytes"] == 5
    assert body["generation_event"]["event_type"] == "panel_agent_generation_completed"
    assert body["command"]["op"] == "load_model"
    assert body["command"]["input"]["version_id"] == body["generated_version"]["id"]
    assert body["command"]["input"]["fcstd_url"].endswith("/artifacts/fcstd")

    poll = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/bridge/poll",
        json={"bridge_id": "bridge_1"},
    )
    assert poll.status_code == 200
    assert poll.json()["commands"][0]["op"] == "load_model"


def test_freecad_panel_prompt_edits_active_fcstd_instead_of_replacing_it(
    tmp_path,
    monkeypatch,
):
    source_summary = {
        "document": {"name": "Site", "label": "Site"},
        "objects": [
            {
                "name": "Tower1",
                "label": "HighRise residential tower 1 body",
                "type_id": "Part::Box",
                "shape": {"valid": True, "volume": 1000.0},
            }
        ],
        "geometry": {
            "object_count": 1,
            "shape_object_count": 1,
            "valid": True,
            "invalid_object_count": 0,
            "check_error_count": 0,
        },
    }
    output_summary = {
        "document": {"name": "Site", "label": "Site"},
        "objects": [
            *source_summary["objects"],
            {
                "name": "SkyGarden_F8_Slab",
                "label": "Sky Garden F8 Slab",
                "type_id": "Part::Box",
                "shape": {"valid": True, "volume": 100.0},
            },
        ],
        "geometry": {
            "object_count": 2,
            "shape_object_count": 2,
            "valid": True,
            "invalid_object_count": 0,
            "check_error_count": 0,
        },
    }
    inspect_calls = []
    edit_calls = []

    async def fake_inspect_fcstd(fcstd_b64):
        inspect_calls.append(fcstd_b64)
        summary = source_summary if fcstd_b64 == "QkFTRQ==" else output_summary
        return {"ok": True, "document_summary": summary, "freecad_version": "1.1.3"}

    async def fake_edit_execute(
        script,
        base_fcstd_b64,
        *,
        prompt,
        source_document_summary,
        selection,
    ):
        edit_calls.append(
            {
                "script": script,
                "base_fcstd_b64": base_fcstd_b64,
                "prompt": prompt,
                "source_document_summary": source_document_summary,
                "selection": selection,
            }
        )
        return ExecResult(
            ok=True,
            engine="freecad",
            freecad_version="1.1.3",
            exports={"fcstd": "TkVX", "step": "U1RFUA=="},
        )

    monkeypatch.setattr("app.main._inspect_fcstd_b64", fake_inspect_fcstd)
    monkeypatch.setattr(
        "app.main.default_freecad_document_edit_execute",
        fake_edit_execute,
    )
    gateway = FakeGateway(
        tool_name="run_freecad",
        script="garden = doc.addObject('Part::Box', 'SkyGarden_F8_Slab')\nresult = doc\n",
    )
    client = _client_with_store(tmp_path, gateway=gateway)
    workbench = client.post(
        "/api/sessions",
        json={"title": "Edit existing FCStd"},
    ).json()["session"]
    store = client.app.state.session_store
    source_version = store.add_version(
        session_id=workbench["id"],
        intent="create",
        design_state={},
        script="",
        geometry_summary={},
        metadata={"document_summary": source_summary},
    )
    client.app.state.artifact_store.save_version_artifacts(
        session_id=workbench["id"],
        version_id=source_version.id,
        exports={"fcstd": "QkFTRQ=="},
    )
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench["id"]},
    ).json()

    response = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/panel/actions",
        json={
            "action": "prompt",
            "prompt": "Add a sky garden on floor 8",
            "selection": {"active_object": {"name": "Tower1"}},
            "metadata": {"document_tree": {"objects": source_summary["objects"]}},
        },
    )

    assert response.status_code == 200
    generated = response.json()["generated_version"]
    assert generated["parent_version_id"] == source_version.id
    assert generated["metadata"]["source_version_id"] == source_version.id
    assert edit_calls == [
        {
            "script": "garden = doc.addObject('Part::Box', 'SkyGarden_F8_Slab')\nresult = doc\n",
            "base_fcstd_b64": "QkFTRQ==",
            "prompt": "Add a sky garden on floor 8",
            "source_document_summary": source_summary,
            "selection": {"active_object": {"name": "Tower1"}},
        }
    ]
    assert inspect_calls == ["QkFTRQ==", "TkVX"]
    prompt_text = gateway.calls[0]["messages"][-1]["content"]
    assert "This is an EDIT of an existing FCStd document" in prompt_text
    assert "Do not call newDocument" in prompt_text


def test_freecad_panel_prompt_logs_generation_failure(
    tmp_path,
    monkeypatch,
    caplog,
):
    async def fail_generation(*args, **kwargs):
        raise RuntimeError("gateway generation failed")

    monkeypatch.setattr(
        "app.main._queue_freecad_panel_agent_generation",
        fail_generation,
    )
    client = _client_with_store(tmp_path)
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "FreeCAD panel failure logging"},
    ).json()["session"]["id"]
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id},
    ).json()

    with caplog.at_level("ERROR", logger="app.main"):
        response = client.post(
            f"/api/freecad/sessions/{remote['session_id']}/panel/actions",
            json={
                "action": "prompt",
                "prompt": "generate a building",
                "selection": {},
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"].endswith("gateway generation failed")
    record = next(
        record
        for record in caplog.records
        if record.message.startswith("remote_freecad_panel_action_failed")
    )
    assert f"session_id={remote['session_id']}" in record.message
    assert "action=prompt" in record.message
    assert record.exc_info is not None


def test_freecad_bridge_poll_does_not_dispatch_commands_after_stop(tmp_path):
    client = _client_with_store(tmp_path)
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "Stopped bridge protocol"},
    ).json()["session"]["id"]
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id},
    ).json()
    queued = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/commands",
        json={"op": "inspect_document", "input": {}},
    ).json()

    stopped = client.request(
        "DELETE",
        f"/api/freecad/sessions/{remote['session_id']}",
        json={"reason": "test_stop"},
    )
    poll = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/bridge/poll",
        json={"bridge_id": "late_bridge"},
    )

    assert stopped.status_code == 200
    assert poll.status_code == 200
    assert poll.json()["session"]["status"] == "stopped"
    assert poll.json()["session"]["bridge_status"] == "disconnected"
    assert poll.json()["commands"] == []

    result = client.post(
        (
            f"/api/freecad/sessions/{remote['session_id']}"
            f"/bridge/commands/{queued['command_id']}/result"
        ),
        json={"status": "completed", "result": {"late": True}},
    )

    assert result.status_code == 200
    assert result.json()["session"]["status"] == "stopped"
    assert result.json()["session"]["bridge_status"] == "disconnected"


def test_freecad_remote_session_command_rejects_revision_conflict(tmp_path):
    client = _client_with_store(tmp_path)
    state = default_design_state()
    script = render_cadquery_script(state)
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "Remote conflict"},
    ).json()["session"]["id"]
    first = client.post(
        f"/api/sessions/{workbench_session_id}/versions",
        json={
            "intent": "create",
            "design_state": state.model_dump(),
            "script": script,
        },
    ).json()["version"]
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id, "version_id": first["id"]},
    ).json()

    resp = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/commands",
        json={"op": "inspect_document", "base_version_id": "stale"},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "cad_session_revision_conflict"


def test_freecad_remote_session_uses_gui_orchestrator_with_source_fcstd(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("CAD_REMOTE_DESKTOP_BASE_URL", raising=False)
    monkeypatch.delenv("FOURYI_CAD_REMOTE_DESKTOP_BASE_URL", raising=False)
    orchestrator = FakeFreeCadGuiOrchestrator()
    client = _client_with_store(tmp_path, freecad_gui_orchestrator=orchestrator)
    state = default_design_state()
    script = render_cadquery_script(state)
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "Orchestrated Remote GUI"},
    ).json()["session"]["id"]
    version = client.post(
        f"/api/sessions/{workbench_session_id}/versions",
        json={
            "intent": "create",
            "design_state": state.model_dump(),
            "script": script,
            "artifacts": {"fcstd": "RkNTdGQ="},
        },
    ).json()["version"]

    created = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id, "version_id": version["id"]},
    )

    assert created.status_code == 200
    remote = created.json()
    assert remote["status"] == "ready"
    assert remote["remote_url"] == f"http://desktop.test/{remote['session_id']}"
    assert remote["metadata"]["orchestrator_backend"] == "fake"
    assert remote["metadata"]["container_name"] == f"fake-{remote['session_id']}"
    assert orchestrator.started == [
        {
            "remote_session_id": remote["session_id"],
            "workbench_session_id": workbench_session_id,
            "base_version_id": version["id"],
            "fcstd_b64": "RkNTdGQ=",
        }
    ]

    stopped = client.request(
        "DELETE",
        f"/api/freecad/sessions/{remote['session_id']}",
        json={"reason": "done"},
    )

    assert stopped.status_code == 200
    assert orchestrator.stopped == [remote["session_id"]]
    stopped_body = stopped.json()
    assert stopped_body["status"] == "stopped"
    assert stopped_body["metadata"]["orchestrator_stop"]["stopped"] is True


def test_freecad_remote_session_save_creates_workbench_version_and_artifacts(tmp_path):
    client = _client_with_store(tmp_path)
    state = default_design_state()
    script = render_cadquery_script(state)
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "Remote save"},
    ).json()["session"]["id"]
    first = client.post(
        f"/api/sessions/{workbench_session_id}/versions",
        json={
            "intent": "create",
            "design_state": state.model_dump(),
            "script": script,
        },
    ).json()["version"]
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id, "version_id": first["id"]},
    ).json()

    resp = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/save",
        json={
            "message": "manual remote edit",
            "base_version_id": first["id"],
            "fcstd_b64": "UkVNT1RFRkNTdGQ=",
            "preview_png_b64": "UkVNT1RFUE5H",
            "artifacts": {"step": "UkVNT1RFU1RFUA=="},
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    version = body["version"]
    assert body["revision_id"] == version["id"]
    assert version["version_number"] == 2
    assert version["parent_version_id"] == first["id"]
    assert version["patch"]["op"] == "remote_freecad_session_save"
    assert version["metadata"]["source"] == "remote_freecad_session"
    assert version["metadata"]["artifact_refs"]["fcstd"]["filename"] == "model.FCStd"
    fcstd_url = version["metadata"]["artifact_refs"]["fcstd"]["url"]
    preview_url = version["metadata"]["artifact_refs"]["preview"]["url"]
    assert client.get(fcstd_url).content == b"REMOTEFCStd"
    assert client.get(preview_url).content == b"REMOTEPNG"
    assert body["session"]["current_version_id"] == version["id"]
    assert body["event"]["event_type"] == "session_saved"

    loaded = client.get(f"/api/sessions/{workbench_session_id}").json()
    assert loaded["session"]["active_version_id"] == version["id"]


def test_freecad_remote_session_save_rejects_stale_base_version(tmp_path):
    client = _client_with_store(tmp_path)
    state = default_design_state()
    script = render_cadquery_script(state)
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "Remote save conflict"},
    ).json()["session"]["id"]
    first = client.post(
        f"/api/sessions/{workbench_session_id}/versions",
        json={
            "intent": "create",
            "design_state": state.model_dump(),
            "script": script,
        },
    ).json()["version"]
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id, "version_id": first["id"]},
    ).json()
    second = client.post(
        f"/api/sessions/{workbench_session_id}/versions",
        json={
            "intent": "modify",
            "design_state": state.model_dump(),
            "script": script,
            "user_instruction": "other edit",
        },
    ).json()["version"]

    resp = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/save",
        json={
            "base_version_id": first["id"],
            "fcstd_b64": "UkVNT1RFRkNTdGQ=",
        },
    )

    assert second["id"] != first["id"]
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "cad_session_revision_conflict"
    assert resp.json()["detail"]["active_version_id"] == second["id"]


def test_freecad_import_model_creates_session_version_and_artifacts(tmp_path, monkeypatch):
    def fake_import_sandboxed(import_format, data_b64, **kwargs):
        assert import_format == "step"
        assert data_b64 == "U1RFUERBVEE="
        assert kwargs["filename"] == "bracket.step"
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "preview_png_b64": "UE5H",
                "exports": {"step": "U1RFUA==", "stl": "U1RM", "fcstd": "RkNTdGQ="},
            },
        )

    def fake_inspect_sandboxed(fcstd_b64, **kwargs):
        assert fcstd_b64 == "RkNTdGQ="
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "document_summary": FREECAD_DOC_SUMMARY,
            },
        )

    monkeypatch.setattr("app.main.run_freecad_import_sandboxed", fake_import_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect_sandboxed)
    client = _client_with_store(tmp_path)

    resp = client.post(
        "/api/freecad/import_model",
        json={
            "format": "step",
            "data_b64": "U1RFUERBVEE=",
            "filename": "bracket.step",
            "title": "Imported bracket",
            "user_instruction": "import bracket",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["engine"] == "freecad"
    assert body["freecad_version"] == "1.2.3"
    assert body["document_summary"] == FREECAD_DOC_SUMMARY
    assert body["session_id"]
    version = body["version"]
    assert version["version_number"] == 1
    assert version["intent"] == "create"
    assert version["metadata"]["document_state"] == "fcstd_artifact"
    assert version["metadata"]["document_summary"] == FREECAD_DOC_SUMMARY
    assert version["geometry_summary"]["document_geometry"] == FREECAD_DOC_SUMMARY["geometry"]
    assert version["metadata"]["source_format"] == "step"
    refs = version["metadata"]["artifact_refs"]
    assert set(refs) == {"preview", "step", "stl", "fcstd"}
    assert client.get(refs["fcstd"]["url"]).content == b"FCStd"


def test_freecad_import_model_appends_to_existing_session(tmp_path, monkeypatch):
    def fake_import_sandboxed(import_format, data_b64, **kwargs):
        assert import_format == "step"
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "preview_png_b64": "UE5H",
                "exports": {"step": "U1RFUA==", "stl": "U1RM", "fcstd": "RkNTdGQ="},
            },
        )

    def fake_inspect_sandboxed(fcstd_b64, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "document_summary": FREECAD_DOC_SUMMARY,
            },
        )

    monkeypatch.setattr("app.main.run_freecad_import_sandboxed", fake_import_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect_sandboxed)
    client = _client_with_store(tmp_path)
    session_id = client.post("/api/sessions", json={"title": "Existing"}).json()["session"]["id"]

    resp = client.post(
        "/api/freecad/import_model",
        json={
            "format": "step",
            "data_b64": "U1RFUERBVEE=",
            "filename": "bracket.step",
            "session_id": session_id,
            "user_instruction": "import into existing session",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["session_id"] == session_id
    version = body["version"]
    assert version["version_number"] == 1
    assert version["metadata"]["document_state"] == "fcstd_artifact"
    assert version["metadata"]["source_format"] == "step"
    loaded = client.get(f"/api/sessions/{session_id}").json()
    assert loaded["session"]["active_version_id"] == version["id"]


def test_freecad_document_intent_endpoint_returns_typed_patch():
    client = _client()

    resp = client.post(
        "/api/freecad/document/intent",
        json={"text": "Box.Length = 25", "document_summary": FREECAD_DOC_SUMMARY},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["intent"] == "set_property"
    assert body["patches"][0] == {
        "op": "set_property",
        "selector": {"name": "Box"},
        "property": "Length",
        "value": 25.0,
    }


def test_freecad_document_edit_loads_fcstd_artifact_and_saves_new_version(
    tmp_path, monkeypatch
):
    inspect_calls = []

    def fake_import_sandboxed(import_format, data_b64, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "preview_png_b64": "T0xEUE5H",
                "exports": {"step": "T0xEU1RFUA==", "stl": "T0xEU1RM", "fcstd": "T0xERkNTdGQ="},
            },
        )

    def fake_inspect_sandboxed(fcstd_b64, **kwargs):
        inspect_calls.append(fcstd_b64)
        summary = dict(FREECAD_DOC_SUMMARY)
        summary["geometry"] = dict(FREECAD_DOC_SUMMARY["geometry"])
        summary["geometry"]["volume"] = 960.0 if fcstd_b64 == "TkVXRkNTdGQ=" else 480.0
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.4",
                "document_summary": summary,
            },
        )

    def fake_edit_sandboxed(script, fcstd_b64, **kwargs):
        assert "doc.recompute()" in script
        assert fcstd_b64 == "T0xERkNTdGQ="
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.4",
                "preview_png_b64": "TkVXUE5H",
                "exports": {"step": "TkVXU1RFUA==", "stl": "TkVXU1RM", "fcstd": "TkVXRkNTdGQ="},
            },
        )

    monkeypatch.setattr("app.main.run_freecad_import_sandboxed", fake_import_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_edit_sandboxed", fake_edit_sandboxed)
    client = _client_with_store(tmp_path)
    imported = client.post(
        "/api/freecad/import_model",
        json={"format": "fcstd", "data_b64": "T0xERkNTdGQ=", "filename": "old.FCStd"},
    ).json()
    session_id = imported["session_id"]
    source_version = imported["version"]

    resp = client.post(
        "/api/freecad/document/edit",
        json={
            "session_id": session_id,
            "version_id": source_version["id"],
            "script": "doc.recompute()\nresult = doc.Objects\n",
            "user_instruction": "edit loaded doc",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    version = body["version"]
    assert version["version_number"] == 2
    assert version["parent_version_id"] == source_version["id"]
    assert version["intent"] == "modify"
    assert version["patch"]["op"] == "edit_fcstd_document"
    assert version["metadata"]["source_version_id"] == source_version["id"]
    assert version["metadata"]["document_summary"]["geometry"]["volume"] == 960.0
    assert version["geometry_summary"]["document_geometry"]["volume"] == 960.0
    refs = version["metadata"]["artifact_refs"]
    assert client.get(refs["fcstd"]["url"]).content == b"NEWFCStd"
    loaded = client.get(f"/api/sessions/{session_id}").json()
    assert loaded["session"]["active_version_id"] == version["id"]
    assert inspect_calls == ["T0xERkNTdGQ=", "TkVXRkNTdGQ="]


def test_freecad_document_inspect_loads_active_fcstd_artifact(tmp_path, monkeypatch):
    inspect_calls = []

    def fake_import_sandboxed(import_format, data_b64, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "preview_png_b64": "UE5H",
                "exports": {"step": "U1RFUA==", "stl": "U1RM", "fcstd": "RkNTdGQ="},
            },
        )

    def fake_inspect_sandboxed(fcstd_b64, **kwargs):
        inspect_calls.append(fcstd_b64)
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "document_summary": FREECAD_DOC_SUMMARY,
            },
        )

    monkeypatch.setattr("app.main.run_freecad_import_sandboxed", fake_import_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect_sandboxed)
    client = _client_with_store(tmp_path)
    imported = client.post(
        "/api/freecad/import_model",
        json={"format": "fcstd", "data_b64": "RkNTdGQ=", "filename": "model.FCStd"},
    ).json()

    resp = client.post(
        "/api/freecad/document/inspect",
        json={"session_id": imported["session_id"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["version_id"] == imported["version"]["id"]
    assert body["document_summary"] == FREECAD_DOC_SUMMARY
    assert inspect_calls == ["RkNTdGQ=", "RkNTdGQ="]


def test_freecad_document_patch_applies_structured_patches_and_saves_version(
    tmp_path, monkeypatch
):
    patch_calls = []
    inspect_calls = []

    def fake_import_sandboxed(import_format, data_b64, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "preview_png_b64": "T0xEUE5H",
                "exports": {"step": "T0xEU1RFUA==", "stl": "T0xEU1RM", "fcstd": "T0xERkNTdGQ="},
            },
        )

    def fake_patch_sandboxed(patches, fcstd_b64, **kwargs):
        patch_calls.append({"patches": patches, "fcstd_b64": fcstd_b64})
        assert fcstd_b64 == "T0xERkNTdGQ="
        assert patches == [
            {
                "op": "set_property",
                "selector": {"name": "Box"},
                "property": "Length",
                "value": 25,
            },
            {
                "op": "set_constraint_value",
                "selector": {"name": "Sketch"},
                "constraint_index": 0,
                "value": 12,
            },
        ]
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.5",
                "preview_png_b64": "UEFUQ0hQTkc=",
                "exports": {
                    "step": "UEFUQ0hTVEVQ",
                    "stl": "UEFUQ0hTVEw=",
                    "fcstd": "UEFUQ0hGQ1N0ZA==",
                },
                "techdraw_export_status": {
                    "mode": "headless_fallback",
                    "product_grade": False,
                    "svg": {"ok": True},
                    "dxf": {"ok": True},
                    "pdf": {"ok": False, "error": "rsvg-convert unavailable"},
                },
                "patch_results": [
                    {
                        "index": 0,
                        "op": "set_property",
                        "object": {"name": "Box", "label": "Box", "type_id": "Part::Box"},
                        "property": "Length",
                        "old_value": 10,
                        "new_value": 25,
                    },
                    {
                        "index": 1,
                        "op": "set_constraint_value",
                        "object": {
                            "name": "Sketch",
                            "label": "Sketch",
                            "type_id": "Sketcher::SketchObject",
                        },
                        "constraint_index": 0,
                        "old_value": 10,
                        "new_value": 12,
                    },
                ],
            },
        )

    def fake_inspect_sandboxed(fcstd_b64, **kwargs):
        inspect_calls.append(fcstd_b64)
        summary = dict(FREECAD_DOC_SUMMARY)
        summary["geometry"] = dict(FREECAD_DOC_SUMMARY["geometry"])
        summary["geometry"]["volume"] = 1200.0 if fcstd_b64 == "UEFUQ0hGQ1N0ZA==" else 480.0
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.5",
                "document_summary": summary,
            },
        )

    monkeypatch.setattr("app.main.run_freecad_import_sandboxed", fake_import_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_patch_sandboxed", fake_patch_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect_sandboxed)
    client = _client_with_store(tmp_path)
    imported = client.post(
        "/api/freecad/import_model",
        json={"format": "fcstd", "data_b64": "T0xERkNTdGQ=", "filename": "old.FCStd"},
    ).json()

    resp = client.post(
        "/api/freecad/document/patch",
        json={
            "session_id": imported["session_id"],
            "version_id": imported["version"]["id"],
            "patches": [
                {
                    "op": "set_property",
                    "selector": {"name": "Box"},
                    "property": "Length",
                    "value": 25,
                },
                {
                    "op": "set_constraint_value",
                    "selector": {"name": "Sketch"},
                    "constraint_index": 0,
                    "value": 12,
                },
            ],
            "user_instruction": "set box length and sketch constraint",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["patch_results"][0]["op"] == "set_property"
    assert body["patch_results"][1]["op"] == "set_constraint_value"
    version = body["version"]
    assert version["version_number"] == 2
    assert version["parent_version_id"] == imported["version"]["id"]
    assert version["patch"]["op"] == "patch_fcstd_document"
    assert version["patch"]["patches"][1]["op"] == "set_constraint_value"
    assert version["metadata"]["document_patch_results"] == body["patch_results"]
    assert version["metadata"]["document_summary"]["geometry"]["volume"] == 1200.0
    assert version["metadata"]["document_state_diff"]["geometry_delta"]["volume"] == {
        "from": 480.0,
        "to": 1200.0,
        "delta": 720.0,
    }
    assert version["metadata"]["techdraw_export_status"]["mode"] == "headless_fallback"
    assert version["metadata"]["techdraw_export_status"]["product_grade"] is False
    assert body["techdraw_export_status"]["pdf"]["error"] == "rsvg-convert unavailable"
    assert version["geometry_summary"]["document_geometry"]["volume"] == 1200.0
    refs = version["metadata"]["artifact_refs"]
    assert client.get(refs["fcstd"]["url"]).content == b"PATCHFCStd"
    assert patch_calls == [
        {
            "patches": version["patch"]["patches"],
            "fcstd_b64": "T0xERkNTdGQ=",
        }
    ]
    assert inspect_calls == ["T0xERkNTdGQ=", "UEFUQ0hGQ1N0ZA=="]


def test_freecad_document_patch_accepts_typed_feature_tree_ops(tmp_path, monkeypatch):
    patch_calls = []

    def fake_import_sandboxed(import_format, data_b64, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "preview_png_b64": "T0xEUE5H",
                "exports": {"step": "T0xEU1RFUA==", "stl": "T0xEU1RM", "fcstd": "T0xERkNTdGQ="},
            },
        )

    def fake_patch_sandboxed(patches, fcstd_b64, **kwargs):
        patch_calls.append(patches)
        assert fcstd_b64 == "T0xERkNTdGQ="
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.6",
                "preview_png_b64": "UEFUQ0hQTkc=",
                "exports": {
                    "step": "UEFUQ0hTVEVQ",
                    "stl": "UEFUQ0hTVEw=",
                    "fcstd": "UEFUQ0hGQ1N0ZA==",
                },
                "patch_results": [
                    {"index": index, "op": patch["op"]}
                    for index, patch in enumerate(patches)
                ],
            },
        )

    def fake_inspect_sandboxed(fcstd_b64, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.6",
                "document_summary": FREECAD_DOC_SUMMARY,
            },
        )

    monkeypatch.setattr("app.main.run_freecad_import_sandboxed", fake_import_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_patch_sandboxed", fake_patch_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect_sandboxed)
    client = _client_with_store(tmp_path)
    imported = client.post(
        "/api/freecad/import_model",
        json={"format": "fcstd", "data_b64": "T0xERkNTdGQ=", "filename": "old.FCStd"},
    ).json()
    patches = [
        {
            "op": "create_feature",
            "type_id": "Part::Cylinder",
            "name": "Boss",
            "label": "Boss",
            "properties": {"Radius": 2, "Height": 5},
            "placement": {"base": [1, 2, 3]},
        },
        {
            "op": "set_placement",
            "selector": {"name": "Box"},
            "base": [4, 5, 6],
            "axis": [0, 0, 1],
            "angle_degrees": 90,
        },
        {
            "op": "set_expression",
            "selector": {"name": "Box"},
            "property": "Length",
            "expression": "30 mm",
        },
        {
            "op": "set_body_tip",
            "selector": {"name": "Body"},
            "tip_selector": {"name": "Pad"},
        },
        {
            "op": "create_sketch",
            "name": "FaceSketch",
            "support_selector": {"name": "Box"},
            "reference": "Face1",
            "map_mode": "FlatFace",
        },
        {
            "op": "attach_sketch",
            "selector": {"name": "Sketch"},
            "support_selector": {"name": "Box"},
            "reference": "Face1",
            "map_mode": "FlatFace",
        },
        {
            "op": "add_external_geometry",
            "selector": {"name": "Sketch"},
            "source_selector": {"name": "Box"},
            "references": ["Edge1"],
            "stable_ids": ["edge-stable-1"],
            "stable_references": ["Edge1"],
            "stable_signatures": [{"kind": "Edge", "length": 20}],
        },
        {
            "op": "solver_status",
            "selector": {"name": "Sketch"},
        },
        {
            "op": "add_geometry",
            "selector": {"name": "Sketch"},
            "geometry": {"type": "line_segment", "start": [0, 0, 0], "end": [20, 0, 0]},
            "auto_constraints": True,
            "auto_constraint_tolerance": 0.001,
        },
        {
            "op": "set_geometry_construction",
            "selector": {"name": "Sketch"},
            "geometry_index": 0,
            "construction": True,
        },
        {
            "op": "set_geometry_point",
            "selector": {"name": "Sketch"},
            "geometry_index": 0,
            "point_role": "end",
            "value": [25, 0, 0],
            "solve": True,
        },
        {
            "op": "add_endpoint_coincidence",
            "selector": {"name": "Sketch"},
            "first": {"geometry_index": 0, "point_role": "start"},
            "second": {"geometry_index": 1, "point_role": "end"},
        },
        {
            "op": "add_constraint",
            "selector": {"name": "Sketch"},
            "constraint": {"type": "DistanceX", "first": 0, "first_pos": 1, "second": 0, "second_pos": 2, "value": 20},
        },
        {
            "op": "set_constraint_state",
            "selector": {"name": "Sketch"},
            "constraint_index": 0,
            "new_name": "Width",
            "active": True,
            "driving": False,
            "virtual_space": False,
        },
        {
            "op": "remove_constraint",
            "selector": {"name": "Sketch"},
            "constraint_index": 0,
        },
        {
            "op": "validate_sketch",
            "selector": {"name": "Sketch"},
            "solve": True,
        },
        {
            "op": "create_assembly",
            "name": "Assembly",
            "label": "Assembly",
        },
        {
            "op": "add_part_to_assembly",
            "selector": {"name": "Assembly"},
            "part_selector": {"name": "Box"},
            "placement": {"base": [10, 0, 0]},
        },
        {
            "op": "set_assembly_part_placement",
            "selector": {"name": "Assembly"},
            "part_selector": {"name": "Box"},
            "base": [20, 0, 0],
        },
        {
            "op": "ground_assembly_part",
            "selector": {"name": "Assembly"},
            "part_selector": {"name": "Box"},
            "name": "GroundBox",
        },
        {
            "op": "create_joint",
            "selector": {"name": "Assembly"},
            "joint_type": "fixed",
            "name": "FixedJoint",
            "connector1": {"selector": {"name": "Box"}, "element": "Face6", "vertex": "Vertex7"},
            "connector2": {"selector": {"name": "Boss"}, "element": "Face6", "vertex": "Vertex7"},
        },
        {
            "op": "update_joint",
            "selector": {"name": "FixedJoint"},
            "joint_type": "distance",
            "distance": 15,
        },
        {
            "op": "solve_assembly",
            "selector": {"name": "Assembly"},
        },
        {
            "op": "remove_part_from_assembly",
            "selector": {"name": "Assembly"},
            "part_selector": {"name": "Box"},
        },
        {
            "op": "create_techdraw_page",
            "name": "Page",
            "scale": 1,
        },
        {
            "op": "add_techdraw_view",
            "page_selector": {"name": "Page"},
            "source_selector": {"name": "Box"},
            "name": "FrontView",
            "direction": [0, -1, 0],
            "x": 100,
            "y": 100,
            "scale": 1,
        },
        {
            "op": "add_techdraw_projection_group",
            "page_selector": {"name": "Page"},
            "source_selector": {"name": "Box"},
            "name": "ProjectionGroup",
            "projection_names": ["Front", "Left", "Top"],
        },
        {
            "op": "add_techdraw_section_view",
            "page_selector": {"name": "Page"},
            "base_view_selector": {"name": "FrontView"},
            "name": "SectionView",
            "section_normal": [0, 1, 0],
            "section_origin": [5, 5, 5],
        },
        {
            "op": "add_techdraw_detail_view",
            "page_selector": {"name": "Page"},
            "base_view_selector": {"name": "FrontView"},
            "name": "DetailView",
            "anchor_point": [5, 5, 0],
            "radius": 5,
        },
        {
            "op": "add_techdraw_centerline",
            "view_selector": {"name": "FrontView"},
            "references": ["Edge1", "Edge2"],
        },
        {
            "op": "add_techdraw_cosmetic_vertex",
            "view_selector": {"name": "FrontView"},
            "point": [5, 5, 0],
        },
        {
            "op": "add_techdraw_cosmetic_line",
            "view_selector": {"name": "FrontView"},
            "start": [0, 0, 0],
            "end": [10, 0, 0],
        },
        {
            "op": "export_techdraw_pdf",
            "page_selector": {"name": "Page"},
        },
        {
            "op": "add_techdraw_dimension",
            "page_selector": {"name": "Page"},
            "view_selector": {"name": "FrontView"},
            "name": "WidthDim",
            "dimension_type": "Distance",
            "reference": "Edge1",
        },
        {
            "op": "delete_feature",
            "selector": {"name": "Boss"},
        },
    ]

    resp = client.post(
        "/api/freecad/document/patch",
        json={
            "session_id": imported["session_id"],
            "version_id": imported["version"]["id"],
            "patches": patches,
            "user_instruction": "typed feature tree edit",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert patch_calls == [patches]
    assert [item["op"] for item in body["patch_results"]] == [patch["op"] for patch in patches]
    assert body["version"]["patch"]["patches"] == patches


def test_freecad_document_patch_dry_run_does_not_save_version(tmp_path, monkeypatch):
    patch_calls = []
    inspect_calls = []
    dry_summary = dict(FREECAD_DOC_SUMMARY)
    dry_summary["geometry"] = dict(FREECAD_DOC_SUMMARY["geometry"])
    dry_summary["geometry"]["volume"] = 512.0
    dry_summary["objects"] = [
        *FREECAD_DOC_SUMMARY["objects"],
        {
            "name": "Sketch",
            "label": "Sketch",
            "type_id": "Sketcher::SketchObject",
            "sketch": {
                "geometry_count": 1,
                "constraint_count": 1,
                "degrees_of_freedom": 2,
                "edit_mode": {
                    "state": "conflicting",
                    "conflicting_constraints": [0],
                    "redundant_constraints": [],
                    "malformed_constraints": [],
                    "diagnostics": [
                        {
                            "severity": "error",
                            "code": "conflicting_constraints",
                            "message": "constraint conflict",
                        }
                    ],
                },
            },
        },
    ]

    def fake_import_sandboxed(import_format, data_b64, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "preview_png_b64": "T0xEUE5H",
                "exports": {"step": "T0xEU1RFUA==", "stl": "T0xEU1RM", "fcstd": "T0xERkNTdGQ="},
            },
        )

    def fake_patch_sandboxed(patches, fcstd_b64, **kwargs):
        patch_calls.append({"patches": patches, "fcstd_b64": fcstd_b64, "dry_run": kwargs.get("dry_run")})
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "dry_run": True,
                "freecad_version": "1.2.7",
                "exports": {},
                "document_summary": dry_summary,
                "patch_results": [
                    {
                        "index": 0,
                        "op": "set_geometry_point",
                        "valid": False,
                        "solver": {"error": None, "degrees_of_freedom": 2},
                    },
                    {
                        "index": 1,
                        "op": "validate_sketch",
                        "valid": False,
                        "diagnostics": {"conflicting_constraints": [0]},
                        "sketch_summary": dry_summary["objects"][1]["sketch"],
                    },
                ],
            },
        )

    def fake_inspect_sandboxed(fcstd_b64, **kwargs):
        inspect_calls.append(fcstd_b64)
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.2.3",
                "document_summary": FREECAD_DOC_SUMMARY,
            },
        )

    monkeypatch.setattr("app.main.run_freecad_import_sandboxed", fake_import_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_patch_sandboxed", fake_patch_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect_sandboxed)
    client = _client_with_store(tmp_path)
    imported = client.post(
        "/api/freecad/import_model",
        json={"format": "fcstd", "data_b64": "T0xERkNTdGQ=", "filename": "old.FCStd"},
    ).json()
    session_id = imported["session_id"]
    source_version_id = imported["version"]["id"]

    resp = client.post(
        "/api/freecad/document/patch",
        json={
            "session_id": session_id,
            "version_id": source_version_id,
            "dry_run": True,
            "patches": [
                {
                    "op": "set_geometry_point",
                    "selector": {"name": "Sketch"},
                    "geometry_index": 0,
                    "point_role": "end",
                    "value": [25, 0, 0],
                    "solve": True,
                },
                {
                    "op": "validate_sketch",
                    "selector": {"name": "Sketch"},
                    "solve": True,
                },
            ],
            "user_instruction": "preview sketch drag",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert body["would_create_version"] is False
    assert body["version"] is None
    assert body["exports"] == {}
    assert body["source_version_id"] == source_version_id
    assert body["document_summary"]["geometry"]["volume"] == 512.0
    assert body["document_state_diff"]["geometry_delta"]["volume"] == {
        "from": 480.0,
        "to": 512.0,
        "delta": 32.0,
    }
    assert [item["op"] for item in body["patch_results"]] == ["set_geometry_point", "validate_sketch"]
    assert patch_calls == [
        {
            "patches": [
                {
                    "op": "set_geometry_point",
                    "selector": {"name": "Sketch"},
                    "geometry_index": 0,
                    "point_role": "end",
                    "value": [25, 0, 0],
                    "solve": True,
                },
                {
                    "op": "validate_sketch",
                    "selector": {"name": "Sketch"},
                    "solve": True,
                },
            ],
            "fcstd_b64": "T0xERkNTdGQ=",
            "dry_run": True,
        }
    ]
    assert inspect_calls == ["T0xERkNTdGQ="]

    loaded = client.get(f"/api/sessions/{session_id}").json()
    assert loaded["session"]["active_version_id"] == source_version_id
    assert [version["id"] for version in loaded["versions"]] == [source_version_id]


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
                "exports": {"step": "STEP", "stl": "STL", "fcstd": "FCStd"},
                "preview_png_b64": None,
            },
        )

    monkeypatch.setattr("app.main.run_freecad_sandboxed", fake_run_freecad_sandboxed)

    resp = _client().get("/api/freecad/smoke")

    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "freecad_version": "1.0.0",
        "exports": ["fcstd", "step", "stl"],
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


def test_freecad_edit_contract_rejects_document_replacement_calls():
    assert _freecad_edit_script_contract_error(
        "doc = FreeCAD.newDocument('replacement')\n"
    )
    assert _freecad_edit_script_contract_error("App.closeDocument(doc.Name)\n")
    assert _freecad_edit_script_contract_error(
        "garden = doc.addObject('Part::Box', 'SkyGarden_F8')\n"
    ) is None


def test_freecad_edit_delivery_requires_every_requested_sky_garden_floor():
    source_summary = {
        "objects": [
            {
                "name": "Tower1",
                "label": "HighRise residential tower 1 body",
                "shape": {"valid": True, "volume": 1000.0},
            }
        ]
    }
    output_summary = {
        "objects": [
            *source_summary["objects"],
            {
                "name": "SkyGarden_F24_Slab",
                "label": "Sky Garden Floor 24",
                "shape": {"valid": True, "volume": 50.0},
            },
        ]
    }

    error, diagnostics = _freecad_edit_delivery_error(
        prompt="给左侧高楼在第8、16、24层各增加一个空中花园",
        source_document_summary=source_summary,
        output_document_summary=output_summary,
        selection={"active_object": {"name": "Tower1"}},
    )

    assert error is not None
    assert "missing requested floors" in error
    assert diagnostics["requested_floors"] == [8, 16, 24]
    assert diagnostics["missing_floors"] == [8, 16]


async def test_default_freecad_document_edit_execute_rejects_replacement_before_sandbox(
    monkeypatch,
):
    calls = []

    def fake_edit(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("replacement script must not reach the sandbox")

    monkeypatch.setattr("app.main.run_freecad_document_edit_sandboxed", fake_edit)

    result = await default_freecad_document_edit_execute(
        "doc = FreeCAD.newDocument('replacement')\n",
        "QkFTRQ==",
        prompt="add a sky garden on floor 8",
        source_document_summary={"objects": [{"name": "Tower1"}]},
        selection={"active_object": {"name": "Tower1"}},
    )

    assert result.ok is False
    assert "newDocument" in (result.error or "")
    assert calls == []


async def test_default_freecad_document_edit_execute_preserves_base_and_semantics(
    monkeypatch,
):
    edit_calls = []
    source_summary = {
        "objects": [
            {
                "name": "Tower1",
                "label": "HighRise residential tower 1 body",
                "placement": {"base": [0, 0, 0]},
                "shape": {"valid": True, "volume": 1000.0},
            },
            {
                "name": "ExistingSite",
                "label": "Existing site",
                "shape": {"valid": True, "volume": 2000.0},
            },
        ],
        "geometry": {
            "valid": True,
            "invalid_object_count": 0,
            "check_error_count": 0,
        },
    }
    output_summary = {
        "objects": [
            *source_summary["objects"],
            *[
                {
                    "name": f"SkyGarden_F{floor}_Slab",
                    "label": f"Sky Garden Floor {floor}",
                    "shape": {"valid": True, "volume": 50.0},
                }
                for floor in (8, 16, 24)
            ],
        ],
        "geometry": {
            "valid": True,
            "invalid_object_count": 0,
            "check_error_count": 0,
        },
        "site_layout": {
            "applicable": True,
            "status": "needs_review",
            "coverage_score": 1.0,
            "issues": [
                {
                    "severity": "warning",
                    "code": "building_spacing_below_minimum",
                    "message": "Review tower spacing.",
                }
            ],
        },
    }

    def fake_edit(script, fcstd_b64, **kwargs):
        edit_calls.append({"script": script, "fcstd_b64": fcstd_b64})
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "exports": {"fcstd": "RURJVEVE", "step": "U1RFUA=="},
            },
        )

    async def fake_inspect(fcstd_b64):
        assert fcstd_b64 == "RURJVEVE"
        return {
            "ok": True,
            "freecad_version": "1.1.3",
            "document_summary": output_summary,
        }

    monkeypatch.setattr("app.main.run_freecad_document_edit_sandboxed", fake_edit)
    monkeypatch.setattr("app.main._inspect_fcstd_b64", fake_inspect)

    result = await default_freecad_document_edit_execute(
        "garden = doc.addObject('Part::Box', 'SkyGarden_F8_Slab')\nresult = doc\n",
        "QkFTRQ==",
        prompt="给左侧高楼在第8、16、24层各增加一个空中花园",
        source_document_summary=source_summary,
        selection={"active_object": {"name": "Tower1"}},
    )

    assert result.ok is True
    assert result.exports["fcstd"] == "RURJVEVE"
    assert edit_calls == [
        {
            "script": "garden = doc.addObject('Part::Box', 'SkyGarden_F8_Slab')\nresult = doc\n",
            "fcstd_b64": "QkFTRQ==",
        }
    ]
    delivery = result.diagnostics["edit_delivery"]
    assert delivery["requested_floors"] == [8, 16, 24]
    assert delivery["matched_sky_garden_shape_count"] == 3
    assert delivery["missing_source_object_count"] == 0
    assert result.diagnostics["site_layout_audit"]["repair_status"] == "preserved_base_edit"


async def test_default_freecad_execute_repairs_missing_site_layout_roles(monkeypatch):
    inspect_calls = []
    edit_calls = []

    failing_summary = {
        "site_layout": {
            "applicable": True,
            "status": "fail",
            "coverage_score": 0.55,
            "issues": [
                {
                    "severity": "error",
                    "code": "missing_enclosure_system",
                    "message": "Missing enclosure wall system.",
                },
                {
                    "severity": "warning",
                    "code": "missing_fire_access",
                    "message": "Missing fire access.",
                },
            ],
        }
    }
    repaired_summary = {
        "site_layout": {
            "applicable": True,
            "status": "pass",
            "coverage_score": 1.0,
            "issues": [],
        }
    }

    def fake_run_freecad_sandboxed(*args, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "exports": {"fcstd": "OLD", "step": "OLDSTEP", "stl": "OLDSTL"},
            },
        )

    def fake_inspect(fcstd_b64, **kwargs):
        inspect_calls.append(fcstd_b64)
        summary = failing_summary if fcstd_b64 == "OLD" else repaired_summary
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "document_summary": summary,
            },
        )

    def fake_edit(script, fcstd_b64, **kwargs):
        edit_calls.append({"script": script, "fcstd_b64": fcstd_b64})
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "exports": {"fcstd": "REPAIRED", "step": "NEWSTEP", "stl": "NEWSTL"},
            },
        )

    monkeypatch.setattr("app.main.run_freecad_sandboxed", fake_run_freecad_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect)
    monkeypatch.setattr("app.main.run_freecad_document_edit_sandboxed", fake_edit)

    result = await default_freecad_execute("import FreeCAD\nresult = []")

    assert result.ok is True
    assert result.exports["fcstd"] == "REPAIRED"
    assert inspect_calls == ["OLD", "REPAIRED"]
    assert edit_calls and edit_calls[0]["fcstd_b64"] == "OLD"
    assert "Repair_Boundary_Wall" in edit_calls[0]["script"]
    assert "Repair_Fire_Road" in edit_calls[0]["script"]
    audit_diagnostics = result.diagnostics["site_layout_audit"]
    assert audit_diagnostics["status"] == "pass"
    assert audit_diagnostics["coverage_score"] == 1.0
    assert audit_diagnostics["issue_count"] == 0
    assert audit_diagnostics["repair_status"] == "repaired"
    assert audit_diagnostics["before"] == failing_summary["site_layout"]
    assert audit_diagnostics["after"] == repaired_summary["site_layout"]
    assert audit_diagnostics["audit"] == repaired_summary["site_layout"]


async def test_default_freecad_execute_accepts_site_layout_warning_only_audit(monkeypatch):
    edit_calls = []
    warning_summary = {
        "site_layout": {
            "applicable": True,
            "status": "needs_review",
            "coverage_score": 1.0,
            "issues": [
                {"severity": "warning", "code": "floating_site_components"},
                {
                    "severity": "warning",
                    "code": "site_layout_reference_quality_below_reference",
                    "failed_checks": [{"key": "building_density_range", "status": "fail"}],
                },
            ],
        }
    }

    def fake_run_freecad_sandboxed(*args, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "exports": {"fcstd": "OK", "step": "STEP", "stl": "STL"},
            },
        )

    def fake_inspect(fcstd_b64, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "document_summary": warning_summary,
            },
        )

    def fake_edit(*args, **kwargs):
        edit_calls.append(args)
        return SandboxResult(success=False, error="should not repair warning-only audit")

    monkeypatch.setattr("app.main.run_freecad_sandboxed", fake_run_freecad_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect)
    monkeypatch.setattr("app.main.run_freecad_document_edit_sandboxed", fake_edit)

    result = await default_freecad_execute("import FreeCAD\nresult = []")

    assert result.ok is True
    assert result.exports["fcstd"] == "OK"
    assert edit_calls == []
    audit_diagnostics = result.diagnostics["site_layout_audit"]
    assert audit_diagnostics["status"] == "needs_review"
    assert audit_diagnostics["coverage_score"] == 1.0
    assert audit_diagnostics["issue_count"] == 2
    assert audit_diagnostics["repair_status"] == "not_needed"


async def test_default_freecad_execute_keeps_spacing_warning_without_repair(monkeypatch):
    edit_calls = []
    warning_summary = {
        "site_layout": {
            "applicable": True,
            "status": "needs_review",
            "coverage_score": 1.0,
            "component_count": 80,
            "issues": [
                {
                    "severity": "warning",
                    "code": "building_spacing_below_minimum",
                    "message": "Residential building spacing is below the concept-plan threshold.",
                },
            ],
        }
    }

    def fake_run_freecad_sandboxed(*args, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "exports": {"fcstd": "OK", "step": "STEP", "stl": "STL"},
            },
        )

    def fake_inspect(fcstd_b64, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "document_summary": warning_summary,
            },
        )

    def fake_edit(*args, **kwargs):
        edit_calls.append(args)
        return SandboxResult(success=False, error="should not repair spacing warning")

    monkeypatch.setattr("app.main.run_freecad_sandboxed", fake_run_freecad_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect)
    monkeypatch.setattr("app.main.run_freecad_document_edit_sandboxed", fake_edit)

    result = await default_freecad_execute("import FreeCAD\nresult = []")

    assert result.ok is True
    assert result.exports["fcstd"] == "OK"
    assert edit_calls == []
    audit_diagnostics = result.diagnostics["site_layout_audit"]
    assert audit_diagnostics["status"] == "needs_review"
    assert audit_diagnostics["issue_count"] == 1
    assert audit_diagnostics["repair_status"] == "not_needed"
    assert audit_diagnostics["audit"]["issues"][0]["code"] == "building_spacing_below_minimum"


async def test_default_freecad_execute_accepts_repaired_site_layout_with_warnings(monkeypatch):
    failing_summary = {
        "site_layout": {
            "applicable": True,
            "status": "needs_review",
            "coverage_score": 0.9,
            "issues": [
                {
                    "severity": "warning",
                    "code": "missing_enclosure_system",
                    "message": "Missing enclosure wall system.",
                },
            ],
        }
    }
    repaired_warning_summary = {
        "site_layout": {
            "applicable": True,
            "status": "needs_review",
            "coverage_score": 1.0,
            "issues": [
                {
                    "severity": "warning",
                    "code": "site_layout_reference_quality_below_reference",
                    "failed_checks": [{"key": "building_density_range", "status": "fail"}],
                },
            ],
        }
    }

    def fake_run_freecad_sandboxed(*args, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "exports": {"fcstd": "OLD", "step": "OLDSTEP", "stl": "OLDSTL"},
            },
        )

    def fake_inspect(fcstd_b64, **kwargs):
        summary = failing_summary if fcstd_b64 == "OLD" else repaired_warning_summary
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "document_summary": summary,
            },
        )

    def fake_edit(script, fcstd_b64, **kwargs):
        return SandboxResult(
            success=True,
            result={
                "ok": True,
                "freecad_version": "1.1.3",
                "exports": {"fcstd": "REPAIRED", "step": "NEWSTEP", "stl": "NEWSTL"},
            },
        )

    monkeypatch.setattr("app.main.run_freecad_sandboxed", fake_run_freecad_sandboxed)
    monkeypatch.setattr("app.main.run_freecad_document_inspect_sandboxed", fake_inspect)
    monkeypatch.setattr("app.main.run_freecad_document_edit_sandboxed", fake_edit)

    result = await default_freecad_execute("import FreeCAD\nresult = []")

    assert result.ok is True
    assert result.exports["fcstd"] == "REPAIRED"
    audit_diagnostics = result.diagnostics["site_layout_audit"]
    assert audit_diagnostics["status"] == "needs_review"
    assert audit_diagnostics["repair_status"] == "repaired"
    assert audit_diagnostics["before"] == failing_summary["site_layout"]
    assert audit_diagnostics["after"] == repaired_warning_summary["site_layout"]


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
    assert '"engine": "cadquery"' in body


def test_generate_can_route_to_freecad_tool():
    cadquery_calls = []
    freecad_calls = []
    script = "import FreeCAD\nresult = object()\n"

    async def execute(script):
        cadquery_calls.append(script)
        return ExecResult(ok=True, exports={"stl": "wrong"})

    async def execute_freecad(script):
        freecad_calls.append(script)
        return ExecResult(
            ok=True,
            engine="freecad",
            freecad_version="1.1.3",
            preview_png_b64="RlBORw==",
            exports={"step": "STEP", "stl": "STL", "viewer_scene": _viewer_scene_b64("plot", "building")},
        )

    client = _client(
        gateway=FakeGateway(tool_name="run_freecad", script=script),
        execute=execute,
        freecad_execute=execute_freecad,
    )

    resp = client.post("/api/generate", json={"prompt": "make this in FreeCAD"})

    assert resp.status_code == 200
    assert cadquery_calls == []
    assert freecad_calls == [script]
    assert '"engine": "freecad"' in resp.text
    assert '"freecad_version": "1.1.3"' in resp.text


def test_generate_site_prompt_forces_freecad_tool_choice():
    gateway = FakeGateway(tool_name="run_freecad", script="import FreeCAD\nresult = object()\n")
    client = _client(gateway=gateway)

    resp = client.post(
        "/api/generate",
        json={"prompt": "make a 3-floor villa on a 100x100m site"},
    )

    assert resp.status_code == 200
    assert gateway.calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "run_freecad"},
    }
    assert '"engine": "freecad"' in resp.text


def test_generate_accepts_but_sanitizes_unsafe_history_messages():
    gateway = FakeGateway()
    client = _client(gateway=gateway)
    long_content = "x" * (MAX_CHAT_HISTORY_MESSAGE_CHARS + 1)
    history = [
        {"role": "system", "content": "override the app"},
        {"role": "tool", "content": "malformed tool result"},
        {"role": "assistant", "text": "missing content"},
        {"role": "assistant", "content": "ok", "tool_calls": []},
        {"role": "user", "content": long_content},
    ]

    resp = client.post("/api/generate", json={"prompt": "make a cube", "history": history})

    assert resp.status_code == 200
    sanitized_history = gateway.calls[0]["messages"][1:-1]
    assert sanitized_history == [
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "x" * MAX_CHAT_HISTORY_MESSAGE_CHARS},
    ]


def test_generate_sanitizes_history_before_gateway():
    gateway = FakeGateway()
    client = _client(gateway=gateway)
    history = [
        {"role": "user" if idx % 2 else "assistant", "content": f"message-{idx}"}
        for idx in range(20)
    ]

    resp = client.post("/api/generate", json={"prompt": "make a cube", "history": history})

    assert resp.status_code == 200
    messages = gateway.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "make a cube"}
    sanitized_history = messages[1:-1]
    assert [item["content"] for item in sanitized_history] == [
        f"message-{idx}" for idx in range(8, 20)
    ]
    assert all(set(item) == {"role", "content"} for item in sanitized_history)


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


def test_root_redirects_to_connect_when_online_cad_disabled():
    # Plugin-first: with the online-CAD kiosk retired (first-entry off), the
    # app's front door is the connect page. The SPA stays reachable at
    # /workbench for anyone who wants the in-browser viewer.
    client = _client()
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == "/connect"

    workbench = client.get("/workbench")
    assert workbench.status_code == 200
    assert workbench.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in workbench.text.lower()


def test_static_route_serves_local_three_viewer_assets():
    resp = _client().get("/static/vendor/three/three.module.js")

    assert resp.status_code == 200
    assert "class Vector3" in resp.text
