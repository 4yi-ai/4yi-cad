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


def test_freecad_addon_prompt_only_uses_macro_for_selected_numeric_edit():
    addon = _load_addon()

    assert (
        addon.macro_for_prompt_if_selected_numeric_edit(
            "把选中孔改成 6mm",
            {"active_object": {"name": "Hole001"}},
        )
        is not None
    )
    assert addon.macro_for_prompt_if_selected_numeric_edit("生成一个小区", {}) is None


def test_freecad_addon_panel_prompt_without_selection_posts_agent_prompt(monkeypatch):
    addon = _load_addon()
    submitted = {}

    def fake_selection():
        return {"schema": "4yi.freecad.bridge.selection.v2", "objects": []}

    def fake_submit(action, payload):
        submitted["action"] = action
        submitted["payload"] = payload
        return {"status": "queued"}

    monkeypatch.setattr(addon, "current_selection", fake_selection)
    monkeypatch.setattr(addon, "submit_panel_action", fake_submit)

    result = addon.submit_prompt_from_panel("生成一个小区")

    assert result == {"status": "queued"}
    assert submitted["action"] == "prompt"
    assert submitted["payload"]["prompt"] == "生成一个小区"
    assert submitted["payload"]["macro"] is None


def test_freecad_addon_selected_numeric_prompt_never_posts_executable_macro(monkeypatch):
    addon = _load_addon()
    submitted = {}

    def fake_submit(action, payload):
        submitted["action"] = action
        submitted["payload"] = payload
        return {"status": "queued"}

    monkeypatch.setattr(addon, "submit_panel_action", fake_submit)

    result = addon.submit_prompt_from_panel(
        "把选中孔改成 6mm",
        selection={"active_object": {"name": "Hole001"}},
        document_tree={"objects": []},
    )

    assert result == {"status": "queued"}
    assert submitted["action"] == "prompt"
    assert submitted["payload"]["macro"] is None


def test_natural_language_edit_plan_compiles_selected_property_with_units():
    addon = _load_addon()
    selection = {"active_object": {"name": "Tower002", "label": "住宅塔楼 2"}}
    tree = {
        "objects": [
            {
                "name": "Tower002",
                "label": "住宅塔楼 2",
                "type_id": "Part::Feature",
                "properties": {"Height": {"value": 96000.0, "unit": "mm"}, "Width": 24000.0},
            }
        ]
    }

    plan = addon.plan_natural_language_edit("将选中塔楼高度增加 10 米", selection, tree)

    assert plan["mode"] == "typed_property"
    assert plan["target"]["name"] == "Tower002"
    assert plan["operations"] == [
        {
            "op": "set_property",
            "selector": {"name": "Tower002"},
            "property": "Height",
            "from": 96000.0,
            "value": 106000.0,
            "unit": "mm",
            "operation_kind": "increase",
        }
    ]


def test_natural_language_edit_plan_routes_ambiguous_change_to_cloud_revision():
    addon = _load_addon()
    selection = {"active_object": {"name": "Tower002", "label": "住宅塔楼 2"}}
    tree = {
        "objects": [
            {
                "name": "Tower002",
                "label": "住宅塔楼 2",
                "properties": {"Height": 96000.0, "Width": 24000.0},
            }
        ]
    }

    plan = addon.plan_natural_language_edit("给这栋楼增加三个空中花园", selection, tree)

    assert plan["mode"] == "generative_revision"
    assert plan["operations"] == []


def test_floor_count_language_is_not_misread_as_millimetres():
    addon = _load_addon()
    selection = {"active_object": {"name": "Tower002", "label": "高层住宅 2"}}
    tree = {
        "objects": [
            {
                "name": "Tower002",
                "label": "高层住宅 2",
                "properties": {"Height": 96000.0},
            }
        ]
    }

    plan = addon.plan_natural_language_edit("给这栋高楼增加 3 层", selection, tree)

    assert plan["mode"] == "generative_revision"


class EditableObject:
    Name = "Tower002"
    Label = "住宅塔楼 2"
    TypeId = "Part::Feature"

    def __init__(self):
        self.Height = 96000.0


class TransactionDocument:
    def __init__(self):
        self.obj = EditableObject()
        self.before = None
        self.committed = None
        self.recompute_count = 0

    def getObject(self, name):
        return self.obj if name == self.obj.Name else None

    def openTransaction(self, _label):
        self.before = self.obj.Height

    def abortTransaction(self):
        self.obj.Height = self.before
        self.before = None

    def commitTransaction(self):
        self.committed = self.before
        self.before = None

    def undo(self):
        self.obj.Height = self.committed

    def recompute(self):
        self.recompute_count += 1


def test_typed_edit_preview_can_cancel_commit_and_undo():
    addon = _load_addon()
    plan = {
        "mode": "typed_property",
        "operations": [
            {
                "op": "set_property",
                "selector": {"name": "Tower002"},
                "property": "Height",
                "from": 96000.0,
                "value": 106000.0,
            }
        ],
    }
    doc = TransactionDocument()

    preview = addon.begin_typed_edit_preview(plan, doc)
    assert doc.obj.Height == 106000.0
    addon.cancel_typed_edit_preview(preview)
    assert doc.obj.Height == 96000.0

    preview = addon.begin_typed_edit_preview(plan, doc)
    addon.commit_typed_edit_preview(preview)
    assert doc.obj.Height == 106000.0
    addon.undo_last_typed_edit(doc)
    assert doc.obj.Height == 96000.0
    assert doc.recompute_count == 4


def test_freecad_addon_panel_action_uses_dedicated_timeout(monkeypatch):
    addon = _load_addon()
    captured = {}

    def fake_post(url, payload, timeout, env=None):
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout"] = timeout
        return {"status": "recorded"}

    # submit_panel_action reads the module-level EFFECTIVE_ENV (derived from
    # os.environ at import time / ParamGet remote overlay), not os.environ
    # directly -- patch that instead.
    monkeypatch.setattr(
        addon,
        "EFFECTIVE_ENV",
        {
            "CAD_PANEL_ACTION_URL": "http://control.test/panel/actions",
            "CAD_BRIDGE_HTTP_TIMEOUT_SECONDS": "10",
            "CAD_PANEL_ACTION_HTTP_TIMEOUT_SECONDS": "300",
        },
    )
    monkeypatch.setattr(addon, "post_json", fake_post)
    monkeypatch.setattr(addon, "current_selection", lambda: {})
    monkeypatch.setattr(addon, "current_document_tree", lambda: {})

    result = addon.submit_panel_action("prompt", {"prompt": "生成一个小区"})

    assert result == {"status": "recorded"}
    assert captured["url"] == "http://control.test/panel/actions"
    assert captured["timeout"] == 300


def test_freecad_addon_panel_action_recovers_controls_after_error():
    addon = _load_addon()

    class Button:
        def __init__(self):
            self.enabled = True

        def setEnabled(self, enabled):
            self.enabled = enabled

    class Output:
        def __init__(self):
            self.value = ""

        def toPlainText(self):
            return self.value

        def setPlainText(self, value):
            self.value = value

    panel = object.__new__(addon.CompanionTaskPanel)
    panel._action_busy = False
    panel._action_thread = None
    panel._edit_plan = {"mode": "generative_revision"}
    panel.plan_button = Button()
    panel.apply_button = Button()
    panel.output = Output()

    def fail():
        raise RuntimeError("generation rejected")

    panel._run_action_async(fail, "pending")

    assert panel._action_busy is False
    assert panel.plan_button.enabled is True
    assert panel.apply_button.enabled is True
    assert "generation rejected" in panel.output.value


def test_freecad_addon_panel_recovers_if_finished_worker_callback_is_missing():
    addon = _load_addon()

    class Button:
        def __init__(self):
            self.enabled = False

        def setEnabled(self, enabled):
            self.enabled = enabled

    class Output:
        def __init__(self):
            self.value = ""

        def toPlainText(self):
            return self.value

        def setPlainText(self, value):
            self.value = value

    class FinishedWorker:
        @staticmethod
        def is_alive():
            return False

    panel = object.__new__(addon.CompanionTaskPanel)
    panel._action_busy = True
    panel._action_thread = FinishedWorker()
    panel._edit_plan = {"mode": "generative_revision"}
    panel.plan_button = Button()
    panel.apply_button = Button()
    panel.output = Output()

    panel._recover_finished_action()

    assert panel._action_busy is False
    assert panel._action_thread is None
    assert panel.plan_button.enabled is True
    assert panel.apply_button.enabled is True
    assert "控件已恢复" in panel.output.value or "controls were restored" in panel.output.value


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

    class FakeShape:
        @staticmethod
        def isNull():
            return False

    class FakeViewObject:
        Visibility = False

    class HiddenShapeObject(FakeObject):
        Shape = FakeShape()
        ViewObject = FakeViewObject()

    class LoadedDocument:
        def __init__(self, path):
            self.Name = "Loaded"
            self.Label = "Loaded"
            self.FileName = path
            self.Objects = [HiddenShapeObject()]
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
                "workbench_session_id": "workbench_1",
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
    assert FakeApp.ActiveDocument.Objects[0].ViewObject.Visibility is True
    assert FakeApp.closed_names == ["Old"]
    assert env["CAD_CURRENT_VERSION_ID"] == "version_1"
    assert env["CAD_WORKBENCH_SESSION_ID"] == "workbench_1"
    assert result["result"]["document_tree"]["document"]["file_name"] == str(loaded_path)
    assert result["result"]["visibility_restore"] == {
        "status": "restored_all_hidden_shapes",
        "shape_object_count": 1,
        "visible_count": 1,
        "restored_count": 1,
    }


def test_freecad_addon_load_model_visibility_preserves_intentional_mix():
    addon = _load_addon()

    class Shape:
        @staticmethod
        def isNull():
            return False

    class View:
        def __init__(self, visible):
            self.Visibility = visible

    class Obj:
        def __init__(self, visible):
            self.Shape = Shape()
            self.ViewObject = View(visible)

    visible = Obj(True)
    hidden = Obj(False)
    doc = type("Doc", (), {"Objects": [visible, hidden]})()

    result = addon.restore_loaded_model_visibility(doc)

    assert result["status"] == "preserved"
    assert visible.ViewObject.Visibility is True
    assert hidden.ViewObject.Visibility is False
