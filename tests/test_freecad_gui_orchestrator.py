import subprocess

import pytest

from app.freecad_gui_orchestrator import (
    DisabledFreeCadGuiSessionOrchestrator,
    LocalDockerFreeCadGuiSessionOrchestrator,
    SharedServiceFreeCadGuiSessionOrchestrator,
    freecad_gui_orchestrator_from_env,
)


def test_gui_orchestrator_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CAD_GUI_SESSION_BACKEND", raising=False)

    orchestrator = freecad_gui_orchestrator_from_env()

    assert isinstance(orchestrator, DisabledFreeCadGuiSessionOrchestrator)
    assert orchestrator.enabled() is False


def test_shared_service_orchestrator_is_external_runtime(monkeypatch):
    monkeypatch.setenv("CAD_GUI_SESSION_BACKEND", "shared_service")

    orchestrator = freecad_gui_orchestrator_from_env()

    assert isinstance(orchestrator, SharedServiceFreeCadGuiSessionOrchestrator)
    assert orchestrator.enabled() is False
    assert orchestrator.start_session(
        remote_session_id="shared-freecad-gui",
        workbench_session_id="workbench_1",
        base_version_id="version_1",
    ) is None
    assert orchestrator.stop_session(remote_session_id="shared-freecad-gui")["stopped"] is False


def test_local_docker_orchestrator_builds_session_container_command(tmp_path):
    calls = []

    def fake_run(cmd, *, check=True, text=True, capture_output=True, timeout=120):
        calls.append(
            {
                "cmd": cmd,
                "check": check,
                "text": text,
                "capture_output": capture_output,
                "timeout": timeout,
            }
        )
        if cmd[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="container123\n", stderr="")
        if cmd[:3] == ["docker", "port", "cad-remote_1"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="127.0.0.1:49153\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    orchestrator = LocalDockerFreeCadGuiSessionOrchestrator(
        image="gui-image:test",
        session_root=tmp_path,
        container_prefix="cad",
        control_plane_url="http://host.docker.internal:8081/",
        health_wait_seconds=0,
        run_cmd=fake_run,
    )

    launch = orchestrator.start_session(
        remote_session_id="remote_1",
        workbench_session_id="workbench_1",
        base_version_id="version_1",
        fcstd_b64="RkNTdGQ=",
    )

    run_cmd = calls[1]["cmd"]
    assert calls[0]["cmd"] == ["docker", "rm", "-f", "cad-remote_1"]
    assert run_cmd[:6] == ["docker", "run", "--rm", "-d", "--name", "cad-remote_1"]
    assert "-p" in run_cmd
    assert "127.0.0.1::6080" in run_cmd
    assert "-e" in run_cmd
    assert "CAD_SESSION_ID=remote_1" in run_cmd
    assert "CAD_WORKBENCH_SESSION_ID=workbench_1" in run_cmd
    assert "CAD_REMOTE_SESSION_ID=remote_1" in run_cmd
    assert "CAD_CONTROL_PLANE_URL=http://host.docker.internal:8081" in run_cmd
    assert (
        "CAD_BRIDGE_HEARTBEAT_URL="
        "http://host.docker.internal:8081/api/freecad/sessions/remote_1/bridge/heartbeat"
    ) in run_cmd
    assert (
        "CAD_BRIDGE_POLL_URL="
        "http://host.docker.internal:8081/api/freecad/sessions/remote_1/bridge/poll"
    ) in run_cmd
    assert (
        "CAD_BRIDGE_COMMAND_RESULT_URL_BASE="
        "http://host.docker.internal:8081/api/freecad/sessions/remote_1/bridge/commands"
    ) in run_cmd
    assert (
        "CAD_BRIDGE_COMMAND_QUEUE_URL="
        "http://host.docker.internal:8081/api/freecad/sessions/remote_1/commands"
    ) in run_cmd
    assert "CAD_BRIDGE_POLL_INTERVAL_SECONDS=2" in run_cmd
    assert (
        "CAD_BRIDGE_SAVE_URL="
        "http://host.docker.internal:8081/api/freecad/sessions/remote_1/save"
    ) in run_cmd
    assert (
        "CAD_PANEL_ACTION_URL="
        "http://host.docker.internal:8081/api/freecad/sessions/remote_1/panel/actions"
    ) in run_cmd
    assert "CAD_BRIDGE_MODE=freecad_addon" in run_cmd
    assert "CAD_BRIDGE_ALLOW_MACRO_EXEC=1" in run_cmd
    assert "SESSION_FCSTD_PATH=/workspace/input.FCStd" in run_cmd
    assert "-v" in run_cmd
    assert f"{tmp_path / 'remote_1'}:/workspace" in run_cmd
    assert run_cmd[-1] == "gui-image:test"
    assert calls[2]["cmd"] == ["docker", "port", "cad-remote_1", "6080/tcp"]
    assert (tmp_path / "remote_1" / "input.FCStd").read_bytes() == b"FCStd"
    assert launch.status == "ready"
    assert launch.remote_url == (
        "http://127.0.0.1:49153"
        "/vnc.html?autoconnect=1&resize=remote&session_id=remote_1"
    )
    assert launch.metadata["container_id"] == "container123"
    assert launch.metadata["container_name"] == "cad-remote_1"
    assert launch.metadata["control_plane_url_configured"] is True
    assert launch.metadata["bridge_autostart_configured"] is True


def test_local_docker_orchestrator_rejects_invalid_fcstd_base64(tmp_path):
    orchestrator = LocalDockerFreeCadGuiSessionOrchestrator(
        session_root=tmp_path,
        health_wait_seconds=0,
    )

    with pytest.raises(ValueError, match="invalid base64"):
        orchestrator.start_session(
            remote_session_id="remote_1",
            workbench_session_id="workbench_1",
            base_version_id=None,
            fcstd_b64="not base64",
        )


def test_local_docker_orchestrator_cleans_up_container_when_novnc_never_readies(tmp_path):
    calls = []

    def fake_run(cmd, *, check=True, text=True, capture_output=True, timeout=120):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="container123\n", stderr="")
        if cmd[:3] == ["docker", "port", "cad-remote_1"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="127.0.0.1:49153\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    orchestrator = LocalDockerFreeCadGuiSessionOrchestrator(
        session_root=tmp_path,
        container_prefix="cad",
        health_wait_seconds=0.01,
        run_cmd=fake_run,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(RuntimeError, match="noVNC did not become ready"):
        orchestrator.start_session(
            remote_session_id="remote_1",
            workbench_session_id="workbench_1",
            base_version_id=None,
        )

    assert calls[-1] == ["docker", "rm", "-f", "cad-remote_1"]
