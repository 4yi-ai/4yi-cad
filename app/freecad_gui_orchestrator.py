"""FreeCAD GUI session orchestration backends.

The production path will eventually be a platform/Kubernetes orchestrator. This
module keeps the first local spike explicit and env-gated: no Docker container is
started unless CAD_GUI_SESSION_BACKEND=local_docker is set.
"""

from __future__ import annotations

import base64
import binascii
import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DEFAULT_GUI_IMAGE = "4yi-cad-freecad-gui:phase1-spike"
DEFAULT_SESSION_ROOT = "/tmp/4yi-cad/freecad-gui-sessions"
DEFAULT_CONTAINER_PREFIX = "4yi-cad-freecad-gui"
DEFAULT_HEALTH_WAIT_SECONDS = 60.0
DEFAULT_BRIDGE_POLL_INTERVAL_SECONDS = 2.0


@dataclass(frozen=True)
class FreeCadGuiSessionLaunch:
    status: str
    remote_url: str | None
    bridge_status: str
    metadata: dict


class FreeCadGuiSessionOrchestrator:
    def enabled(self) -> bool:
        return False

    def start_session(
        self,
        *,
        remote_session_id: str,
        workbench_session_id: str,
        base_version_id: str | None,
        fcstd_b64: str | None = None,
    ) -> FreeCadGuiSessionLaunch | None:
        raise NotImplementedError

    def stop_session(self, *, remote_session_id: str) -> dict:
        raise NotImplementedError


class DisabledFreeCadGuiSessionOrchestrator(FreeCadGuiSessionOrchestrator):
    def start_session(
        self,
        *,
        remote_session_id: str,
        workbench_session_id: str,
        base_version_id: str | None,
        fcstd_b64: str | None = None,
    ) -> FreeCadGuiSessionLaunch | None:
        return None

    def stop_session(self, *, remote_session_id: str) -> dict:
        return {"backend": "disabled", "stopped": False}


class LocalDockerFreeCadGuiSessionOrchestrator(FreeCadGuiSessionOrchestrator):
    def __init__(
        self,
        *,
        image: str = DEFAULT_GUI_IMAGE,
        session_root: str | os.PathLike[str] = DEFAULT_SESSION_ROOT,
        public_host: str = "127.0.0.1",
        container_prefix: str = DEFAULT_CONTAINER_PREFIX,
        docker_bin: str = "docker",
        control_plane_url: str | None = None,
        health_wait_seconds: float = DEFAULT_HEALTH_WAIT_SECONDS,
        bridge_poll_interval_seconds: float = DEFAULT_BRIDGE_POLL_INTERVAL_SECONDS,
        run_cmd: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.image = image
        self.session_root = Path(session_root)
        self.public_host = public_host
        self.container_prefix = container_prefix
        self.docker_bin = docker_bin
        self.control_plane_url = control_plane_url.rstrip("/") if control_plane_url else None
        self.health_wait_seconds = health_wait_seconds
        self.bridge_poll_interval_seconds = bridge_poll_interval_seconds
        self.run_cmd = run_cmd
        self.sleep = sleep

    def enabled(self) -> bool:
        return True

    def start_session(
        self,
        *,
        remote_session_id: str,
        workbench_session_id: str,
        base_version_id: str | None,
        fcstd_b64: str | None = None,
    ) -> FreeCadGuiSessionLaunch:
        session_id = _safe_id(remote_session_id)
        workspace = self.session_root / session_id
        workspace.mkdir(parents=True, exist_ok=True)

        input_path = None
        if fcstd_b64:
            input_path = workspace / "input.FCStd"
            input_path.write_bytes(_decode_b64(fcstd_b64, label="FCStd session input"))

        container_name = self.container_name(remote_session_id)
        self._run([self.docker_bin, "rm", "-f", container_name], check=False)

        cmd = [
            self.docker_bin,
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
            "-p",
            "127.0.0.1::6080",
            "-e",
            f"CAD_SESSION_ID={remote_session_id}",
            "-e",
            f"CAD_WORKBENCH_SESSION_ID={workbench_session_id}",
            "-e",
            f"CAD_REMOTE_SESSION_ID={remote_session_id}",
            "-v",
            f"{workspace}:/workspace",
        ]
        if self.control_plane_url:
            bridge_base = f"{self.control_plane_url}/api/freecad/sessions/{remote_session_id}"
            cmd.extend(
                [
                    "-e",
                    f"CAD_CONTROL_PLANE_URL={self.control_plane_url}",
                    "-e",
                    f"CAD_BRIDGE_HEARTBEAT_URL={bridge_base}/bridge/heartbeat",
                    "-e",
                    f"CAD_BRIDGE_POLL_URL={bridge_base}/bridge/poll",
                    "-e",
                    f"CAD_BRIDGE_COMMAND_RESULT_URL_BASE={bridge_base}/bridge/commands",
                    "-e",
                    f"CAD_BRIDGE_COMMAND_QUEUE_URL={bridge_base}/commands",
                    "-e",
                    f"CAD_BRIDGE_SAVE_URL={bridge_base}/save",
                    "-e",
                    f"CAD_PANEL_ACTION_URL={bridge_base}/panel/actions",
                    "-e",
                    f"CAD_BRIDGE_POLL_INTERVAL_SECONDS={self.bridge_poll_interval_seconds:g}",
                    "-e",
                    "CAD_BRIDGE_MODE=freecad_addon",
                    "-e",
                    "CAD_BRIDGE_ALLOW_MACRO_EXEC=1",
                ]
            )
        if input_path:
            cmd.extend(["-e", "SESSION_FCSTD_PATH=/workspace/input.FCStd"])
        cmd.append(self.image)

        container_id = self._run(cmd).stdout.strip()
        try:
            host_port = self._published_port(container_name)
            self._wait_for_novnc(host_port)
        except Exception:
            self._run([self.docker_bin, "rm", "-f", container_name], check=False)
            raise
        remote_url = (
            f"http://{self.public_host}:{host_port}"
            f"/vnc.html?autoconnect=1&resize=remote&session_id={remote_session_id}"
        )
        metadata = {
            "orchestrator_backend": "local_docker",
            "container_id": container_id,
            "container_name": container_name,
            "workspace_path": str(workspace),
            "public_host": self.public_host,
            "public_port": host_port,
            "control_plane_url_configured": bool(self.control_plane_url),
            "bridge_autostart_configured": bool(self.control_plane_url),
            "bridge_poll_interval_seconds": self.bridge_poll_interval_seconds,
            "input_fcstd_path": str(input_path) if input_path else None,
            "base_version_id": base_version_id,
        }
        return FreeCadGuiSessionLaunch(
            status="ready",
            remote_url=remote_url,
            bridge_status="pending",
            metadata=metadata,
        )

    def stop_session(self, *, remote_session_id: str) -> dict:
        container_name = self.container_name(remote_session_id)
        result = self._run([self.docker_bin, "rm", "-f", container_name], check=False)
        return {
            "backend": "local_docker",
            "container_name": container_name,
            "stopped": result.returncode == 0,
            "message": result.stdout.strip() or result.stderr.strip() or None,
        }

    def container_name(self, remote_session_id: str) -> str:
        return f"{self.container_prefix}-{_safe_id(remote_session_id)}"

    def _published_port(self, container_name: str) -> str:
        result = self._run([self.docker_bin, "port", container_name, "6080/tcp"])
        endpoint = result.stdout.strip().splitlines()[0]
        if ":" not in endpoint:
            raise RuntimeError(f"unexpected docker port output: {endpoint}")
        return endpoint.rsplit(":", 1)[1]

    def _wait_for_novnc(self, host_port: str) -> None:
        if self.health_wait_seconds <= 0:
            return
        deadline = time.monotonic() + self.health_wait_seconds
        health_url = f"http://127.0.0.1:{host_port}/vnc.html"
        last_error = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(health_url, timeout=2) as response:
                    if 200 <= response.status < 500:
                        return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            self.sleep(0.5)
        raise RuntimeError(
            f"noVNC did not become ready on port {host_port}: {last_error}"
        )

    def _run(self, cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
        return self.run_cmd(
            cmd,
            check=check,
            text=True,
            capture_output=True,
            timeout=120,
        )


def freecad_gui_orchestrator_from_env() -> FreeCadGuiSessionOrchestrator:
    backend = os.environ.get("CAD_GUI_SESSION_BACKEND", "disabled").strip().lower()
    if backend in {"", "disabled", "none", "metadata"}:
        return DisabledFreeCadGuiSessionOrchestrator()
    if backend == "local_docker":
        return LocalDockerFreeCadGuiSessionOrchestrator(
            image=os.environ.get("CAD_GUI_SESSION_IMAGE", DEFAULT_GUI_IMAGE).strip()
            or DEFAULT_GUI_IMAGE,
            session_root=os.environ.get("CAD_GUI_SESSION_ROOT", DEFAULT_SESSION_ROOT).strip()
            or DEFAULT_SESSION_ROOT,
            public_host=os.environ.get("CAD_GUI_SESSION_PUBLIC_HOST", "127.0.0.1").strip()
            or "127.0.0.1",
            container_prefix=os.environ.get(
                "CAD_GUI_SESSION_CONTAINER_PREFIX",
                DEFAULT_CONTAINER_PREFIX,
            ).strip()
            or DEFAULT_CONTAINER_PREFIX,
            docker_bin=os.environ.get("CAD_GUI_SESSION_DOCKER_BIN", "docker").strip()
            or "docker",
            control_plane_url=os.environ.get("CAD_GUI_SESSION_CONTROL_PLANE_URL", "").strip()
            or None,
            health_wait_seconds=_float_env(
                "CAD_GUI_SESSION_HEALTH_WAIT_SECONDS",
                DEFAULT_HEALTH_WAIT_SECONDS,
            ),
            bridge_poll_interval_seconds=_float_env(
                "CAD_GUI_SESSION_BRIDGE_POLL_INTERVAL_SECONDS",
                DEFAULT_BRIDGE_POLL_INTERVAL_SECONDS,
            ),
        )
    raise ValueError(f"unsupported CAD_GUI_SESSION_BACKEND: {backend}")


def _decode_b64(data_b64: str, *, label: str) -> bytes:
    try:
        data = base64.b64decode(data_b64, validate=True)
    except binascii.Error as exc:
        raise ValueError(f"invalid base64 for {label}") from exc
    if not data:
        raise ValueError(f"empty {label}")
    return data


def _safe_id(value: str) -> str:
    safe = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
    if not safe:
        raise ValueError("empty session identifier")
    return safe


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.0, value)
