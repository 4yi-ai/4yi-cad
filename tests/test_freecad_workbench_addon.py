import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDON_PATH = ROOT / "freecad-addon/fouryi_cad_companion/FourYiCadCompanion.py"


def _load_addon():
    spec = importlib.util.spec_from_file_location("FourYiCadCompanion", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeObject:
    Name = "Hole001"
    Label = "Mounting hole"
    TypeId = "PartDesign::Pocket"
    Diameter = 4.5
    Visibility = True
    InList = []
    OutList = []


class FakeDocument:
    Name = "Doc"
    Label = "Demo document"
    FileName = "/workspace/input.FCStd"
    Objects = [FakeObject()]

    def getObject(self, name):
        return FakeObject() if name == "Hole001" else None


class FakeSelectionItem:
    Object = FakeObject()
    SubElementNames = ["Face2"]


class FakeSelection:
    @staticmethod
    def getSelectionEx():
        return [FakeSelectionItem()]


class FakeGui:
    Selection = FakeSelection()


def test_freecad_addon_builds_document_tree_from_active_document():
    addon = _load_addon()

    tree = addon.document_tree_from_document(FakeDocument())

    assert tree["schema"] == "4yi.freecad.bridge.document_tree.v2"
    assert tree["document"]["name"] == "Doc"
    assert tree["objects"][0]["name"] == "Hole001"
    assert tree["objects"][0]["properties"]["Diameter"] == 4.5


def test_freecad_addon_builds_selection_from_gui():
    addon = _load_addon()

    selection = addon.selection_from_gui(FakeGui)

    assert selection["schema"] == "4yi.freecad.bridge.selection.v2"
    assert selection["active_object"]["name"] == "Hole001"
    assert selection["active_object"]["reference"] == "Face2"


def test_freecad_addon_prompt_macro_uses_selected_object_and_dimension():
    addon = _load_addon()

    macro = addon.macro_for_selected_numeric_edit(
        "把选中孔改成 6mm",
        {"active_object": {"name": "Hole001"}},
    )

    assert 'object_name = "Hole001"' in macro
    assert "target_mm = 6.0" in macro
    assert "doc.recompute()" in macro


def test_freecad_addon_diagnostics_redacts_endpoint_values(tmp_path):
    addon = _load_addon()
    env = {
        "CAD_SESSION_WORKSPACE": str(tmp_path),
        "CAD_BRIDGE_MODE": "freecad_addon",
        "CAD_REMOTE_SESSION_ID": "remote_1",
        "CAD_BRIDGE_POLL_URL": "http://control.test/poll",
        "CAD_PANEL_ACTION_URL": "http://control.test/panel/actions",
    }

    diagnostics = addon.collect_diagnostics(env)

    assert diagnostics["addon_version"] == addon.ADDON_VERSION
    assert diagnostics["environment"]["CAD_BRIDGE_MODE"] == "freecad_addon"
    assert diagnostics["environment"]["CAD_BRIDGE_POLL_URL"] is True
    assert diagnostics["environment"]["CAD_PANEL_ACTION_URL"] is True
    assert "http://control.test" not in str(diagnostics["environment"])


def test_freecad_addon_runtime_polls_and_posts_command_result(tmp_path):
    addon = _load_addon()
    calls = []

    def fake_post(url, payload, timeout):
        calls.append({"url": url, "payload": payload, "timeout": timeout})
        if url.endswith("/bridge/poll"):
            return {
                "commands": [
                    {
                        "id": "cmd_1",
                        "command_id": "cmd_1",
                        "op": "inspect_document",
                        "input": {},
                    }
                ]
            }
        return {}

    runtime = addon.InProcessBridgeRuntime(
        env={
            "CAD_SESSION_WORKSPACE": str(tmp_path),
            "CAD_REMOTE_SESSION_ID": "remote_1",
            "CAD_BRIDGE_HEARTBEAT_URL": "http://control.test/api/freecad/sessions/remote_1/bridge/heartbeat",
            "CAD_BRIDGE_POLL_URL": "http://control.test/api/freecad/sessions/remote_1/bridge/poll",
            "CAD_BRIDGE_COMMAND_RESULT_URL_BASE": "http://control.test/api/freecad/sessions/remote_1/bridge/commands",
        },
        http_post=fake_post,
    )
    runtime.running = True

    summary = runtime.run_once()

    assert summary["command_count"] == 1
    heartbeat = calls[0]["payload"]
    assert heartbeat["metadata"]["client"] == "freecad-addon"
    result_call = [call for call in calls if call["url"].endswith("/cmd_1/result")][0]
    assert result_call["payload"]["status"] == "completed"
    assert result_call["payload"]["result"]["schema"] == "4yi.freecad.bridge.command_result.v2"


def test_freecad_addon_load_model_opens_fcstd(tmp_path, monkeypatch):
    addon = _load_addon()

    class LoadedDocument:
        def __init__(self, path):
            self.Name = "Loaded"
            self.Label = "Loaded"
            self.FileName = path
            self.Objects = [FakeObject()]
            self.recomputed = False

        def recompute(self):
            self.recomputed = True

    class FakeApp:
        ActiveDocument = None
        opened_paths = []
        active_name = ""
        closed_names = []

        @staticmethod
        def listDocuments():
            return {"Old": object()}

        @staticmethod
        def closeDocument(name):
            FakeApp.closed_names.append(name)

        @staticmethod
        def openDocument(path):
            FakeApp.opened_paths.append(path)
            FakeApp.ActiveDocument = LoadedDocument(path)
            return FakeApp.ActiveDocument

        @staticmethod
        def setActiveDocument(name):
            FakeApp.active_name = name

    monkeypatch.setattr(addon, "App", FakeApp)
    monkeypatch.setattr(addon, "Gui", None)
    env = {
        "CAD_SESSION_WORKSPACE": str(tmp_path),
        "CAD_REMOTE_SESSION_ID": "shared-freecad-gui",
    }

    result = addon.execute_command(
        {
            "id": "cmd_load",
            "command_id": "cmd_load",
            "op": "load_model",
            "input": {
                "fcstd_b64": "RkNTdGQ=",
                "filename": "current.FCStd",
                "version_id": "version_1",
            },
        },
        env,
    )

    loaded_path = tmp_path / "current.FCStd"
    assert result["status"] == "completed"
    assert loaded_path.read_bytes() == b"FCStd"
    assert FakeApp.opened_paths == [str(loaded_path)]
    assert FakeApp.active_name == "Loaded"
    assert FakeApp.ActiveDocument.recomputed is True
    assert FakeApp.closed_names == ["Old"]
    assert env["CAD_CURRENT_VERSION_ID"] == "version_1"
    assert result["result"]["document_tree"]["document"]["file_name"] == str(loaded_path)
