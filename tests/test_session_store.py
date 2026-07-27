from app.cad.design_state import default_design_state, geometry_summary, render_cadquery_script
from app.session_store import SqliteSessionStore


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
