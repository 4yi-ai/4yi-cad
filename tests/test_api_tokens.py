from fastapi.testclient import TestClient

from app.main import create_app
from app.session_store import SqliteSessionStore


def _make_store(tmp_path):
    return SqliteSessionStore(tmp_path / "sessions.sqlite3")


def _external_client(tmp_path, *, store=None):
    """TestClient simulating a request from outside the container/localhost.

    starlette's TestClient accepts a `client` tuple overriding the synthetic
    ASGI scope client address (default ("testclient", 50000), which is one of
    the exempt hosts). Using a public-looking address here exercises the
    non-exempt path through the bearer middleware.
    """
    app = create_app(session_store=store if store is not None else _make_store(tmp_path))
    return TestClient(app, client=("203.0.113.9", 12345))


def _local_client(tmp_path, *, store=None):
    app = create_app(session_store=store if store is not None else _make_store(tmp_path))
    return TestClient(app)


def test_create_api_token_returns_prefixed_plaintext_once(tmp_path):
    store = _make_store(tmp_path)

    result = store.create_api_token(label="ci")

    assert set(result.keys()) == {"id", "token", "label", "created_at"}
    assert result["label"] == "ci"
    assert result["token"].startswith("4yi-cad-tok-")
    assert len(result["token"]) == 12 + 48


def test_create_api_token_generates_unique_tokens(tmp_path):
    store = _make_store(tmp_path)

    first = store.create_api_token()
    second = store.create_api_token()

    assert first["token"] != second["token"]
    assert first["id"] != second["id"]


def test_verify_api_token_accepts_valid_plaintext_and_updates_last_used(tmp_path):
    store = _make_store(tmp_path)
    created = store.create_api_token(label="agent")

    assert store.verify_api_token(created["token"]) is True

    listed = store.list_api_tokens()
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]
    assert listed[0]["last_used_at"] is not None


def test_verify_api_token_rejects_wrong_token(tmp_path):
    store = _make_store(tmp_path)
    store.create_api_token(label="agent")

    assert store.verify_api_token("4yi-cad-tok-" + "0" * 48) is False


def test_verify_api_token_rejects_empty_string(tmp_path):
    store = _make_store(tmp_path)

    assert store.verify_api_token("") is False


def test_verify_api_token_rejects_missing_prefix(tmp_path):
    store = _make_store(tmp_path)
    created = store.create_api_token()
    # strip the prefix off an otherwise-valid token
    stripped = created["token"][len("4yi-cad-tok-"):]

    assert store.verify_api_token(stripped) is False


def test_list_api_tokens_excludes_plaintext_and_hash(tmp_path):
    store = _make_store(tmp_path)
    store.create_api_token(label="a")
    store.create_api_token(label="b")

    listed = store.list_api_tokens()

    assert len(listed) == 2
    for item in listed:
        assert set(item.keys()) == {"id", "label", "created_at", "last_used_at", "revoked_at"}
        assert "token" not in item
        assert "token_hash" not in item


def test_revoke_api_token_marks_revoked_and_blocks_verify(tmp_path):
    store = _make_store(tmp_path)
    created = store.create_api_token(label="agent")

    assert store.revoke_api_token(created["id"]) is True
    assert store.verify_api_token(created["token"]) is False

    listed = store.list_api_tokens()
    assert listed[0]["revoked_at"] is not None


def test_revoke_api_token_is_idempotent(tmp_path):
    store = _make_store(tmp_path)
    created = store.create_api_token(label="agent")

    assert store.revoke_api_token(created["id"]) is True
    assert store.revoke_api_token(created["id"]) is True


def test_revoke_api_token_missing_id_returns_false(tmp_path):
    store = _make_store(tmp_path)

    assert store.revoke_api_token("does-not-exist") is False


# --- Bearer middleware -------------------------------------------------


def test_external_client_without_token_on_generate_gets_401_required(tmp_path):
    client = _external_client(tmp_path)

    resp = client.post("/api/generate", json={"prompt": "a box"})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "api_token_required"}


def test_external_client_bad_auth_scheme_gets_401_required(tmp_path):
    client = _external_client(tmp_path)

    resp = client.post(
        "/api/generate",
        json={"prompt": "a box"},
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )

    assert resp.status_code == 401
    assert resp.json() == {"detail": "api_token_required"}


def test_external_client_with_wrong_token_on_generate_gets_401_invalid(tmp_path):
    client = _external_client(tmp_path)

    resp = client.post(
        "/api/generate",
        json={"prompt": "a box"},
        headers={"Authorization": "Bearer 4yi-cad-tok-" + "0" * 48},
    )

    assert resp.status_code == 401
    assert resp.json() == {"detail": "api_token_invalid"}


def test_external_client_without_token_on_freecad_sessions_gets_401_required(tmp_path):
    client = _external_client(tmp_path)

    resp = client.post("/api/freecad/sessions", json={})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "api_token_required"}


def test_external_client_with_valid_token_passes_generate_guard(tmp_path):
    store = _make_store(tmp_path)
    client = TestClient(create_app(session_store=store), client=("203.0.113.9", 12345))
    token = client.post("/api/tokens", json={"label": "ci"}).json()["token"]

    resp = client.post(
        "/api/generate",
        json={"prompt": "a box"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code != 401


def test_external_client_with_revoked_token_gets_401_invalid(tmp_path):
    store = _make_store(tmp_path)
    client = TestClient(create_app(session_store=store), client=("203.0.113.9", 12345))
    created = client.post("/api/tokens", json={"label": "ci"}).json()
    client.delete(f"/api/tokens/{created['id']}")

    resp = client.post(
        "/api/generate",
        json={"prompt": "a box"},
        headers={"Authorization": f"Bearer {created['token']}"},
    )

    assert resp.status_code == 401
    assert resp.json() == {"detail": "api_token_invalid"}


def test_external_client_on_unguarded_path_is_exempt(tmp_path):
    client = _external_client(tmp_path)

    resp = client.get("/healthz")

    assert resp.status_code == 200


def test_localhost_client_is_exempt_from_guard(tmp_path):
    # TestClient's default synthetic client host is "testclient", one of the
    # explicitly exempt hosts, so existing tests (built with plain
    # TestClient(app)) stay unauthenticated by design.
    client = _local_client(tmp_path)

    resp = client.post("/api/generate", json={"prompt": "a box"})

    assert resp.status_code != 401


def test_guard_fails_closed_when_store_missing(tmp_path):
    app = create_app(session_store=None)
    client = TestClient(app, client=("203.0.113.9", 12345))

    resp = client.post("/api/generate", json={"prompt": "a box"})

    # No store was ever injected. Regardless of whether the guard reaches
    # into _get_session_store's lazy default construction, an external
    # request with no Authorization header must never slip through.
    assert resp.status_code == 401
    assert resp.json() == {"detail": "api_token_required"}


# --- Token management endpoints ----------------------------------------


def test_post_tokens_returns_201_with_plaintext_once(tmp_path):
    client = _local_client(tmp_path)

    resp = client.post("/api/tokens", json={"label": "agent"})

    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {"id", "token", "label", "created_at"}
    assert body["label"] == "agent"
    assert body["token"].startswith("4yi-cad-tok-")


def test_post_tokens_without_label_defaults_to_none(tmp_path):
    client = _local_client(tmp_path)

    resp = client.post("/api/tokens", json={})

    assert resp.status_code == 201
    assert resp.json()["label"] is None


def test_get_tokens_lists_without_plaintext_or_hash(tmp_path):
    client = _local_client(tmp_path)
    client.post("/api/tokens", json={"label": "a"})
    client.post("/api/tokens", json={"label": "b"})

    resp = client.get("/api/tokens")

    assert resp.status_code == 200
    tokens = resp.json()["tokens"]
    assert len(tokens) == 2
    for item in tokens:
        assert set(item.keys()) == {"id", "label", "created_at", "last_used_at", "revoked_at"}


def test_delete_tokens_revokes_and_returns_204(tmp_path):
    client = _local_client(tmp_path)
    created = client.post("/api/tokens", json={"label": "x"}).json()

    resp = client.delete(f"/api/tokens/{created['id']}")

    assert resp.status_code == 204


def test_delete_tokens_missing_id_returns_404(tmp_path):
    client = _local_client(tmp_path)

    resp = client.delete("/api/tokens/does-not-exist")

    assert resp.status_code == 404


def test_management_endpoints_not_guarded_for_external_client(tmp_path):
    client = _external_client(tmp_path)

    resp = client.post("/api/tokens", json={"label": "ext"})
    assert resp.status_code == 201

    resp = client.get("/api/tokens")
    assert resp.status_code == 200
