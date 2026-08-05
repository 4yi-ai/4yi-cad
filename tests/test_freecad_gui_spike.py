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

    assert "EXPOSE 8080" in dockerfile
    assert "EXPOSE 6080" in dockerfile
    assert "pip install --no-cache-dir -r requirements.txt" in dockerfile
    assert "COPY app ./app" in dockerfile
    assert "COPY index.html ./index.html" in dockerfile
    assert "start-freecad-gui.sh" in dockerfile
    assert "freecad-bridge-client.py" in dockerfile
    assert "freecad-addon/fouryi_cad_companion" in dockerfile
    assert ".local/share/FreeCAD/Mod/fouryi_cad_companion" in dockerfile
    assert ".FreeCAD/Mod/fouryi_cad_companion" in dockerfile
    # COPY pre-creates /home/appuser as root before useradd -m; chown must cover the whole home
    assert "chown -R appuser:appuser /workspace /data /tmp/4yi-cad-freecad-gui /home/appuser" in dockerfile
    assert "CAD_UNIFIED_APP=1" in dockerfile
    assert "PORT=8080" in dockerfile
    assert "PYTHONPATH=/app" in dockerfile
    assert "CAD_DATA_DIR=/data/4yi-cad" in dockerfile
    assert "CAD_RUNTIME_DIR=/data/4yi-cad/runtime" in dockerfile
    assert "/home/appuser/.fluxbox" in dockerfile
    assert "CAD_SESSION_WORKSPACE=/workspace" in dockerfile
    # Online CAD (in-browser kiosk) is retired by default: the GUI stack stays
    # installed for opt-in (CAD_ONLINE_CAD=1), but the container runs the
    # control plane only, so the session-backend and kiosk-entry flags are no
    # longer hardcoded on — the start script derives them from CAD_ONLINE_CAD.
    assert "CAD_ONLINE_CAD=0" in dockerfile
    assert "CAD_FREECAD_FIRST_ENTRY=1" not in dockerfile
    assert "CAD_GUI_SESSION_BACKEND=shared_service" not in dockerfile
    assert "CAD_FREECAD_GUI_UPSTREAM_URL=http://127.0.0.1:6080" in dockerfile
    assert "CAD_PANEL_ACTION_URL=http://127.0.0.1:8080/api/freecad/sessions/shared-freecad-gui/panel/actions" in dockerfile
    assert "CAD_BRIDGE_AUTOSTART=1" in dockerfile
    assert "CAD_BRIDGE_MODE=freecad_addon" in dockerfile
    assert "CAD_BRIDGE_ALLOW_MACRO_EXEC=1" in dockerfile
    assert "CAD_BRIDGE_HTTP_TIMEOUT_SECONDS=10" in dockerfile
    assert "CAD_PANEL_ACTION_HTTP_TIMEOUT_SECONDS=300" in dockerfile
    assert "LANG=zh_CN.UTF-8" in dockerfile
    assert "CAD_COMPANION_PANEL_AUTOSTART=1" in dockerfile

    assert "fluxbox-init" in dockerfile
    assert "fluxbox-apps" in dockerfile
    assert "freecad-user.cfg" in dockerfile
    assert "kiosk.html" in dockerfile
    assert "CAD_REMOTE_DESKTOP_BASE_URL=/freecad/kiosk.html?path=freecad/websockify" in dockerfile


def test_kiosk_assets_present_and_configured():
    root = Path(__file__).resolve().parents[1] / "scripts" / "freecad-gui"
    init = (root / "fluxbox-init").read_text()
    assert "session.screen0.toolbar.visible" in init and "false" in init
    apps = (root / "fluxbox-apps").read_text()
    assert "[Maximized]" in apps and "{yes}" in apps
    assert "[Deco]" in apps and "{NONE}" in apps
    cfg = (root / "freecad-user.cfg").read_text()
    assert "FCParameters" in cfg
    assert 'Name="ShowOnStartup" Value="0"' in cfg
    kiosk = (root / "kiosk.html").read_text()
    assert "RFB" in kiosk and "websockify" not in kiosk  # path comes from query param, not hardcoded
    assert "resizeSession" in kiosk


def test_freecad_gui_start_script_is_executable_and_valid_bash():
    script = ROOT / "scripts/freecad-gui/start-freecad-gui.sh"

    assert os.access(script, os.X_OK)
    subprocess.run(["bash", "-n", str(script)], check=True)

    content = script.read_text()
    for token in [
        "SESSION_FCSTD_PATH",
        "CAD_SESSION_WORKSPACE",
        "CAD_UNIFIED_APP",
        "CAD_ONLINE_CAD",
        "CAD_RUNTIME_DIR",
        "XDG_RUNTIME_DIR",
        "TMPDIR",
        "uvicorn",
        "supervise_unified_app",
        "-u",
        "NOVNC_PORT",
        "VNC_PORT",
        "CAD_CONTROL_PLANE_URL",
        "CAD_FREECAD_GUI_UPSTREAM_URL",
        "CAD_PANEL_ACTION_URL",
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
