import base64
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_CLIENT_PATH = ROOT / "scripts/freecad-gui/freecad-bridge-client.py"


def _load_bridge_client():
    spec = importlib.util.spec_from_file_location("freecad_bridge_client", BRIDGE_CLIENT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_bridge_client_heartbeat_reports_workspace_context(tmp_path):
    bridge = _load_bridge_client()
    (tmp_path / "input.FCStd").write_bytes(b"FCStd")
    (tmp_path / "bridge-selection.json").write_text(
        '{"objects":[{"name":"Hole001"}],"active_object":{"name":"Hole001"}}',
        encoding="utf-8",
    )
    (tmp_path / "bridge-document-tree.json").write_text(
        '{"document":{"name":"input.FCStd"},"objects":[{"name":"Body"}]}',
        encoding="utf-8",
    )
    (tmp_path / "bridge-console.log").write_text("line 1\nline 2\n", encoding="utf-8")

    payload = bridge.heartbeat_payload(
        {
            "CAD_SESSION_WORKSPACE": str(tmp_path),
            "CAD_REMOTE_SESSION_ID": "remote_1",
            "SESSION_FCSTD_PATH": str(tmp_path / "input.FCStd"),
            "FREECAD_VERSION": "1.0.0",
            "CAD_FREECAD_WORKBENCH": "PartDesignWorkbench",
        },
    )

    assert payload["bridge_id"] == "4yi-freecad-bridge-remote_1"
    assert payload["freecad_version"] == "1.0.0"
    assert payload["workbench"] == "PartDesignWorkbench"
    assert payload["selection"]["active_object"]["name"] == "Hole001"
    assert payload["document_tree"]["document"]["name"] == "input.FCStd"
    assert payload["console_tail"] == ["line 1", "line 2"]


def test_bridge_client_run_once_inspects_and_selects(tmp_path):
    bridge = _load_bridge_client()
    calls = []
    poll_commands = [
        {
            "id": "cmd_inspect",
            "command_id": "cmd_inspect",
            "op": "inspect_document",
            "input": {},
        },
        {
            "id": "cmd_select",
            "command_id": "cmd_select",
            "op": "select_object",
            "input": {"object_name": "Pocket001", "reference": "Face3"},
        },
    ]

    def fake_post(url, payload, timeout):
        calls.append({"url": url, "payload": payload, "timeout": timeout})
        if url.endswith("/bridge/poll"):
            return {"commands": poll_commands}
        return {}

    env = {
        "CAD_SESSION_WORKSPACE": str(tmp_path),
        "CAD_REMOTE_SESSION_ID": "remote_1",
        "CAD_BRIDGE_HEARTBEAT_URL": "http://control.test/api/freecad/sessions/remote_1/bridge/heartbeat",
        "CAD_BRIDGE_POLL_URL": "http://control.test/api/freecad/sessions/remote_1/bridge/poll",
        "CAD_BRIDGE_COMMAND_RESULT_URL_BASE": "http://control.test/api/freecad/sessions/remote_1/bridge/commands",
    }

    summary = bridge.run_once(env, fake_post)

    assert summary["heartbeat_sent"] is True
    assert summary["command_count"] == 2
    result_calls = [call for call in calls if call["url"].endswith("/result")]
    assert [call["payload"]["status"] for call in result_calls] == ["completed", "completed"]
    assert result_calls[0]["payload"]["result"]["document_tree"]["objects"] == []
    assert result_calls[1]["payload"]["result"]["selection"]["active_object"]["name"] == "Pocket001"
    assert bridge.current_selection(env)["active_object"]["name"] == "Pocket001"


def test_bridge_client_run_macro_returns_structured_error(tmp_path):
    bridge = _load_bridge_client()

    result = bridge.execute_command(
        {
            "id": "cmd_macro",
            "command_id": "cmd_macro",
            "op": "run_macro",
            "input": {"macro": "print('unsafe')"},
        },
        {"CAD_SESSION_WORKSPACE": str(tmp_path), "CAD_REMOTE_SESSION_ID": "remote_1"},
    )

    assert result["status"] == "failed"
    assert result["result"]["error"]["code"] == "macro_execution_disabled"
    assert result["result"]["transaction"]["ok"] is False
    assert (tmp_path / "bridge-last-macro.py").read_text(encoding="utf-8") == "print('unsafe')"


def test_bridge_client_save_revision_uploads_fcstd(tmp_path):
    bridge = _load_bridge_client()
    output = tmp_path / "output.FCStd"
    output.write_bytes(b"Saved FCStd")
    calls = []

    def fake_post(url, payload, timeout):
        calls.append({"url": url, "payload": payload, "timeout": timeout})
        assert base64.b64decode(payload["fcstd_b64"]) == b"Saved FCStd"
        return {
            "revision_id": "version_2",
            "artifact_refs": {"fcstd": {"url": "/artifacts/version_2/model.FCStd"}},
            "version": {"id": "version_2"},
        }

    result = bridge.execute_command(
        {
            "id": "cmd_save",
            "command_id": "cmd_save",
            "op": "save_revision",
            "input": {"message": "save from bridge", "fcstd_path": str(output)},
        },
        {
            "CAD_SESSION_WORKSPACE": str(tmp_path),
            "CAD_REMOTE_SESSION_ID": "remote_1",
            "CAD_BRIDGE_SAVE_URL": "http://control.test/api/freecad/sessions/remote_1/save",
        },
        http_post=fake_post,
    )

    assert result["status"] == "completed"
    assert result["result"]["artifact_refs"]["fcstd"]["url"].endswith("model.FCStd")
    assert calls[0]["payload"]["message"] == "save from bridge"
