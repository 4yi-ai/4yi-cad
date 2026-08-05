from fastapi.testclient import TestClient

from app.agent.loop import ExecResult
from app.artifact_store import FileArtifactStore
from app.main import create_app
from app.session_store import SqliteSessionStore
from tests.test_main import FakeGateway


def _make_store(tmp_path):
    return SqliteSessionStore(tmp_path / "sessions.sqlite3")


def _external_client(tmp_path, *, store=None):
    """TestClient simulating a request from outside the container/localhost.

    Mirrors tests/test_api_tokens.py: passing a public-looking `client`
    address exercises the non-exempt path through the bearer middleware.
    """
    app = create_app(session_store=store if store is not None else _make_store(tmp_path))
    return TestClient(app, client=("203.0.113.9", 12345))


def _heartbeat_payload():
    return {
        "bridge_id": "bridge_1",
        "freecad_version": "1.1.0",
        "workbench": "Part Design",
    }


def test_external_client_with_token_auto_creates_local_session_on_heartbeat(tmp_path):
    store = _make_store(tmp_path)
    client = TestClient(create_app(session_store=store), client=("203.0.113.9", 12345))
    token = client.post("/api/tokens", json={"label": "ci"}).json()["token"]

    resp = client.post(
        "/api/freecad/sessions/local-abc123/bridge/heartbeat",
        json=_heartbeat_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code != 404
    assert resp.status_code == 200
    body = resp.json()
    assert body["session"]["session_id"] == "local-abc123"
    assert body["session"]["metadata"]["auto_created"] is True
    assert body["session"]["metadata"]["source"] == "local_addon_autocreate"

    # A repeat heartbeat (or fetching the session) should now succeed too.
    again = client.post(
        "/api/freecad/sessions/local-abc123/bridge/heartbeat",
        json=_heartbeat_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert again.status_code == 200

    fetched = client.get(
        "/api/freecad/sessions/local-abc123",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert fetched.status_code == 200
    assert fetched.json()["session_id"] == "local-abc123"
    assert [event["event_type"] for event in fetched.json()["events"]][:2] == [
        "session_auto_created",
        "bridge_heartbeat",
    ]


def test_external_client_with_token_local_session_id_trailing_dash_still_404(tmp_path):
    store = _make_store(tmp_path)
    client = TestClient(create_app(session_store=store), client=("203.0.113.9", 12345))
    token = client.post("/api/tokens", json={"label": "ci"}).json()["token"]

    resp = client.post(
        "/api/freecad/sessions/local-/bridge/heartbeat",
        json=_heartbeat_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404


def test_external_client_with_token_non_local_unknown_session_still_404(tmp_path):
    store = _make_store(tmp_path)
    client = TestClient(create_app(session_store=store), client=("203.0.113.9", 12345))
    token = client.post("/api/tokens", json={"label": "ci"}).json()["token"]

    resp = client.post(
        "/api/freecad/sessions/not-local-x/bridge/heartbeat",
        json=_heartbeat_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404


def test_external_client_without_token_on_local_session_gets_401(tmp_path):
    client = _external_client(tmp_path)

    resp = client.post(
        "/api/freecad/sessions/local-abc123/bridge/heartbeat",
        json=_heartbeat_payload(),
    )

    assert resp.status_code == 401
    assert resp.json() == {"detail": "api_token_required"}


def _create_remote_session_with_fcstd_artifact(client, headers):
    """Mirrors test_main.py's save-flow fixture: workbench session + version +
    fcstd artifact, then a remote FreeCAD session pointed at that version."""
    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "Plugin remote alias"},
        headers=headers,
    ).json()["session"]["id"]
    version = client.post(
        f"/api/sessions/{workbench_session_id}/versions",
        json={
            "intent": "create",
            "design_state": {"kind": "box", "params": {"width": 1, "height": 1, "depth": 1}},
            "script": "result = box(1,1,1)",
            "preview_png_b64": "UE5H",
            "artifacts": {"fcstd": "RkNTdGQ="},
        },
        headers=headers,
    ).json()["version"]
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id, "version_id": version["id"]},
        headers=headers,
    ).json()
    return workbench_session_id, version, remote


def test_alias_route_serves_fcstd_artifact_with_valid_token(tmp_path):
    store = _make_store(tmp_path)
    client = TestClient(create_app(session_store=store), client=("203.0.113.9", 12345))
    token = client.post("/api/tokens", json={"label": "ci"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    workbench_session_id, version, remote = _create_remote_session_with_fcstd_artifact(
        client, headers
    )

    direct = client.get(
        f"/api/sessions/{workbench_session_id}/versions/{version['id']}/artifacts/fcstd"
    )
    assert direct.status_code == 200

    alias = client.get(
        f"/api/freecad/sessions/{remote['session_id']}/versions/{version['id']}/artifacts/fcstd",
        headers=headers,
    )

    assert alias.status_code == 200
    assert alias.content == direct.content
    assert alias.content == b"FCStd"


def test_alias_route_without_token_gets_401(tmp_path):
    store = _make_store(tmp_path)
    client = TestClient(create_app(session_store=store), client=("203.0.113.9", 12345))
    token = client.post("/api/tokens", json={"label": "ci"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    _workbench_session_id, version, remote = _create_remote_session_with_fcstd_artifact(
        client, headers
    )

    resp = client.get(
        f"/api/freecad/sessions/{remote['session_id']}/versions/{version['id']}/artifacts/fcstd",
    )

    assert resp.status_code == 401
    assert resp.json() == {"detail": "api_token_required"}


def test_alias_route_unknown_remote_session_id_gets_404(tmp_path):
    client = _external_client(tmp_path)
    token = client.post("/api/tokens", json={"label": "ci"}).json()["token"]

    resp = client.get(
        "/api/freecad/sessions/does-not-exist/versions/v1/artifacts/fcstd",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404


class _FcstdlessArtifactStore(FileArtifactStore):
    """Drops the "fcstd" ref from save_version_artifacts' return value even
    though the fcstd export was written, forcing
    `_queue_freecad_panel_agent_generation`'s `fcstd_ref.get("url")` preferred
    branch to be empty so the fallback URL construction fires.
    """

    def save_version_artifacts(self, **kwargs):
        refs = super().save_version_artifacts(**kwargs)
        refs.pop("fcstd", None)
        return refs


def test_queued_load_model_command_fcstd_url_uses_guarded_alias_on_fallback(
    tmp_path, monkeypatch
):
    async def fake_inspect_fcstd(fcstd_b64):
        return {
            "ok": False,
            "engine": "freecad",
            "error": None,
            "freecad_version": None,
            "document_summary": None,
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

    store = _make_store(tmp_path)
    app = create_app(
        session_store=store,
        artifact_store=_FcstdlessArtifactStore(tmp_path / "artifacts"),
        gateway=FakeGateway(tool_name="run_freecad", script="import FreeCAD\nresult=[]\n"),
        freecad_execute=fake_freecad_execute,
    )
    client = TestClient(app, client=("203.0.113.9", 12345))
    token = client.post("/api/tokens", json={"label": "ci"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    workbench_session_id = client.post(
        "/api/sessions",
        json={"title": "FreeCAD panel generate (fallback)"},
        headers=headers,
    ).json()["session"]["id"]
    remote = client.post(
        "/api/freecad/sessions",
        json={"session_id": workbench_session_id},
        headers=headers,
    ).json()

    prompt = client.post(
        f"/api/freecad/sessions/{remote['session_id']}/panel/actions",
        json={
            "action": "prompt",
            "prompt": "生成一个入口门厅模型",
            "selection": {},
            "metadata": {"source": "freecad_panel_test", "document_tree": {"objects": []}},
        },
        headers=headers,
    )

    assert prompt.status_code == 200
    body = prompt.json()
    fcstd_url = body["command"]["input"]["fcstd_url"]
    assert fcstd_url.startswith("/api/freecad/sessions/")
    assert fcstd_url == (
        f"/api/freecad/sessions/{remote['session_id']}"
        f"/versions/{body['generated_version']['id']}/artifacts/fcstd"
    )
