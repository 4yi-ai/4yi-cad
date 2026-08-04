from fastapi.testclient import TestClient

from app.main import create_app
from app.session_store import SqliteSessionStore


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
