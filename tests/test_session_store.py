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

    sessions = store.list_sessions(limit=10)
    assert len(sessions) == 1
    assert sessions[0]["session"]["id"] == session.id
    assert sessions[0]["version_count"] == 2
    assert sessions[0]["active_version"]["id"] == second.id
    assert sessions[0]["active_version"]["user_instruction"] == "hole_d = 6"
    assert "script" not in sessions[0]["active_version"]
    assert "design_state" not in sessions[0]["active_version"]


def test_sqlite_session_store_tracks_remote_freecad_sessions(tmp_path):
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    state = default_design_state()
    session = store.create_session(title="Remote FreeCAD")
    version = store.add_version(
        session_id=session.id,
        intent="create",
        user_instruction="initial",
        design_state=state.model_dump(),
        script=render_cadquery_script(state),
        geometry_summary=geometry_summary(state),
    )

    remote, reused = store.create_or_reuse_remote_freecad_session(
        workbench_session_id=session.id,
        base_version_id=version.id,
        remote_url="http://desktop/session",
        metadata={"mode": "freecad_gui"},
    )

    assert reused is False
    assert remote.workbench_session_id == session.id
    assert remote.base_version_id == version.id
    assert remote.current_version_id == version.id
    assert remote.status == "starting"
    assert remote.bridge_status == "pending"
    assert remote.metadata["mode"] == "freecad_gui"

    reused_remote, reused = store.create_or_reuse_remote_freecad_session(
        workbench_session_id=session.id,
        base_version_id=version.id,
    )

    assert reused is True
    assert reused_remote.id == remote.id

    event = store.add_remote_freecad_session_event(
        remote_session_id=remote.id,
        event_type="bridge_command_queued",
        metadata={"command_id": "cmd_1"},
    )
    events = store.list_remote_freecad_session_events(remote_session_id=remote.id)

    assert events == [event.__dict__]

    updated = store.update_remote_freecad_session(
        remote_session_id=remote.id,
        status="ready",
        current_version_id=version.id,
        bridge_status="connected",
    )
    assert updated.status == "ready"
    assert updated.bridge_status == "connected"

    listed = store.list_remote_freecad_sessions(workbench_session_id=session.id)
    assert listed[0]["id"] == remote.id
    assert listed[0]["session_id"] == remote.id

    stopped = store.stop_remote_freecad_session(
        remote_session_id=remote.id,
        reason="idle_timeout",
    )
    assert stopped.status == "stopped"
    assert stopped.bridge_status == "disconnected"
    assert stopped.metadata["stop_reason"] == "idle_timeout"


def test_sqlite_session_store_retargets_explicit_remote_freecad_session_id(tmp_path):
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    state = default_design_state()
    first_session = store.create_session(title="First workbench")
    first_version = store.add_version(
        session_id=first_session.id,
        intent="create",
        design_state=state.model_dump(),
        script=render_cadquery_script(state),
        geometry_summary=geometry_summary(state),
    )
    second_session = store.create_session(title="Second workbench")
    second_version = store.add_version(
        session_id=second_session.id,
        intent="create",
        design_state=state.model_dump(),
        script=render_cadquery_script(state),
        geometry_summary=geometry_summary(state),
    )

    remote, reused = store.create_or_reuse_remote_freecad_session(
        remote_session_id="shared-freecad-gui",
        workbench_session_id=first_session.id,
        base_version_id=first_version.id,
        status="ready",
        bridge_status="connected",
        metadata={"mode": "freecad_gui", "bridge": {"bridge_id": "bridge_1"}},
    )
    retargeted, reused = store.create_or_reuse_remote_freecad_session(
        remote_session_id="shared-freecad-gui",
        workbench_session_id=second_session.id,
        base_version_id=second_version.id,
        status="ready",
        bridge_status="pending",
        metadata={"shared_service_configured": True},
    )

    assert remote.id == "shared-freecad-gui"
    assert reused is True
    assert retargeted.id == "shared-freecad-gui"
    assert retargeted.workbench_session_id == second_session.id
    assert retargeted.base_version_id == second_version.id
    assert retargeted.current_version_id == second_version.id
    assert retargeted.bridge_status == "connected"
    assert retargeted.stopped_at is None
    assert retargeted.metadata["bridge"]["bridge_id"] == "bridge_1"
    assert retargeted.metadata["shared_service_configured"] is True


def test_sqlite_session_store_claims_and_completes_remote_freecad_commands(tmp_path):
    store = SqliteSessionStore(tmp_path / "sessions.sqlite3")
    session = store.create_session(title="Remote command queue")
    remote, _ = store.create_or_reuse_remote_freecad_session(
        workbench_session_id=session.id,
    )

    command = store.create_remote_freecad_session_command(
        remote_session_id=remote.id,
        op="inspect_document",
        input={"selection": "Box"},
        base_version_id="version_1",
        metadata={"source": "test"},
    )

    assert command.id.startswith("cmd_")
    assert command.status == "pending"
    assert command.input["selection"] == "Box"

    first_claim = store.claim_pending_remote_freecad_session_commands(
        remote_session_id=remote.id,
    )
    second_claim = store.claim_pending_remote_freecad_session_commands(
        remote_session_id=remote.id,
    )

    assert len(first_claim) == 1
    assert first_claim[0]["command_id"] == command.id
    assert first_claim[0]["status"] == "dispatched"
    assert first_claim[0]["dispatched_at"] is not None
    assert second_claim == []

    dispatched = store.get_remote_freecad_session_command(
        remote_session_id=remote.id,
        command_id=command.id,
    )
    assert dispatched is not None
    assert dispatched.status == "dispatched"

    completed = store.complete_remote_freecad_session_command(
        remote_session_id=remote.id,
        command_id=command.id,
        status="completed",
        result={"document": {"object_count": 1}},
        metadata={"bridge_id": "bridge_1"},
    )

    assert completed.status == "completed"
    assert completed.result["document"]["object_count"] == 1
    assert completed.completed_at is not None
    assert completed.metadata["source"] == "test"
    assert completed.metadata["bridge_id"] == "bridge_1"
