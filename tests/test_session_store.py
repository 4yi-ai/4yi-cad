from app.cad.design_state import default_design_state, geometry_summary, render_cadquery_script
from app.artifact_store import default_artifact_root
from app.session_store import SqliteSessionStore, default_db_path


def test_default_storage_paths_use_platform_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("CAD_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("CAD_ARTIFACT_ROOT", raising=False)
    monkeypatch.setenv("CAD_DATA_DIR", str(tmp_path / "data"))

    assert default_db_path() == str(tmp_path / "data" / "sessions.sqlite3")
    assert default_artifact_root() == str(tmp_path / "data" / "artifacts")


def test_explicit_storage_paths_override_platform_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CAD_SESSION_DB_PATH", str(tmp_path / "custom" / "sessions.sqlite3"))
    monkeypatch.setenv("CAD_ARTIFACT_ROOT", str(tmp_path / "custom" / "artifacts"))

    assert default_db_path() == str(tmp_path / "custom" / "sessions.sqlite3")
    assert default_artifact_root() == str(tmp_path / "custom" / "artifacts")


def test_platform_data_dir_falls_back_when_not_writable(tmp_path, monkeypatch):
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory")
    monkeypatch.delenv("CAD_SESSION_DB_PATH", raising=False)
    monkeypatch.delenv("CAD_ARTIFACT_ROOT", raising=False)
    monkeypatch.setenv("CAD_DATA_DIR", str(blocked_parent / "data"))

    assert default_db_path() == "/tmp/4yi-cad/sessions.sqlite3"
    assert default_artifact_root() == "/tmp/4yi-cad/artifacts"


def test_sqlite_session_store_persists_versions(tmp_path):
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    state = default_design_state()
    script = render_cadquery_script(state)

    session = store.create_session(title="Bracket")
    first = store.add_version(
        session_id=session.id,
        intent="create",
        user_instruction="initial",
        design_state=state.model_dump(),
        script=script,
        geometry_summary=geometry_summary(state),
        metadata={"preview_mode": "design_state"},
    )
    second = store.add_version(
        session_id=session.id,
        intent="modify",
        user_instruction="hole_d = 6",
        design_state=state.model_dump(),
        script=script.replace("hole_d = 4.5", "hole_d = 6"),
        geometry_summary=geometry_summary(state),
        patch={"op": "update_parameter", "name": "hole_d", "value": 6},
        metadata={"preview_mode": "design_state"},
    )

    loaded = store.get_session(session.id)

    assert loaded is not None
    assert loaded["session"]["title"] == "Bracket"
    assert loaded["session"]["active_version_id"] == second.id
    assert loaded["active_version"]["id"] == second.id
    assert loaded["active_version"]["parent_version_id"] == first.id
    assert loaded["active_version"]["patch"]["name"] == "hole_d"
    assert loaded["versions"][0]["version_number"] == 1
    assert loaded["versions"][1]["version_number"] == 2
