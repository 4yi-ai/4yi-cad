from app.session_store import SqliteSessionStore


def _make_store(tmp_path):
    return SqliteSessionStore(tmp_path / "sessions.sqlite3")


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
