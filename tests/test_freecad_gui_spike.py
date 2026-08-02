import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_freecad_gui_spike_dockerfile_contains_remote_desktop_stack():
    dockerfile = (ROOT / "Dockerfile.freecad-gui").read_text()

    for package in [
        "freecad",
        "xvfb",
        "x11vnc",
        "novnc",
        "websockify",
        "fluxbox",
        "fonts-noto-cjk",
        "fontconfig",
        "locales",
        "tini",
    ]:
        assert package in dockerfile

    assert "EXPOSE 6080" in dockerfile
    assert "start-freecad-gui.sh" in dockerfile
    assert "freecad-bridge-client.py" in dockerfile
    assert "freecad-addon/fouryi_cad_companion" in dockerfile
    assert ".local/share/FreeCAD/Mod/fouryi_cad_companion" in dockerfile
    assert ".FreeCAD/Mod/fouryi_cad_companion" in dockerfile
    assert "CAD_SESSION_WORKSPACE=/workspace" in dockerfile
    assert "CAD_BRIDGE_AUTOSTART=1" in dockerfile
    assert "CAD_BRIDGE_MODE=freecad_addon" in dockerfile
    assert "CAD_BRIDGE_ALLOW_MACRO_EXEC=1" in dockerfile
    assert "CAD_BRIDGE_HTTP_TIMEOUT_SECONDS=10" in dockerfile
    assert "CAD_PANEL_ACTION_HTTP_TIMEOUT_SECONDS=300" in dockerfile
    assert "LANG=zh_CN.UTF-8" in dockerfile
    assert "CAD_COMPANION_PANEL_AUTOSTART=1" in dockerfile


def test_freecad_gui_start_script_is_executable_and_valid_bash():
    script = ROOT / "scripts/freecad-gui/start-freecad-gui.sh"

    assert os.access(script, os.X_OK)
    subprocess.run(["bash", "-n", str(script)], check=True)

    content = script.read_text()
    for token in [
        "SESSION_FCSTD_PATH",
        "CAD_SESSION_WORKSPACE",
        "NOVNC_PORT",
        "VNC_PORT",
        "CAD_CONTROL_PLANE_URL",
        "CAD_BRIDGE_AUTOSTART",
        "CAD_BRIDGE_MODE",
        "CAD_BRIDGE_POLL_URL",
        "freecad-bridge-client.py",
        "freecad_addon",
        "standalone",
        "Xvfb",
        "x11vnc",
        "websockify",
    ]:
        assert token in content


def test_freecad_bridge_client_is_executable_python():
    script = ROOT / "scripts/freecad-gui/freecad-bridge-client.py"

    assert os.access(script, os.X_OK)
    subprocess.run(["python3", "-m", "py_compile", str(script)], check=True)


def test_freecad_addon_files_are_packaged_for_workbench():
    addon = ROOT / "freecad-addon/fouryi_cad_companion"

    assert (addon / "InitGui.py").is_file()
    assert (addon / "FourYiCadCompanion.py").is_file()
    assert (addon / "package.xml").is_file()
    assert (addon / "Resources/icons/fouryi_cad_companion.svg").is_file()

    init_gui = (addon / "InitGui.py").read_text()
    companion = (addon / "FourYiCadCompanion.py").read_text()
    assert "FourYiCadCompanionWorkbench" in init_gui
    assert "Icon = \"\"" in init_gui
    assert "autostart_remote_bridge" in init_gui
    assert "autostart_companion_panel" in init_gui
    assert "CompanionTaskPanel" in companion
    assert "FreeCADGui" in companion
    assert "def autostart_companion_panel" in companion


def test_freecad_gui_spike_doc_lists_manual_smoke_steps():
    doc = (ROOT / "docs/PHASE1-freecad-gui-spike.md").read_text()

    assert "docker build -f Dockerfile.freecad-gui" in doc
    assert "SESSION_FCSTD_PATH=/workspace/input.FCStd" in doc
    assert "http://127.0.0.1:6080/vnc.html" in doc
    assert "/workspace/output.FCStd" in doc
