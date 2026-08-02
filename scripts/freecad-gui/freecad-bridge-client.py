#!/usr/bin/env python3
"""Lightweight FreeCAD remote-session bridge client.

This process runs next to the GUI session and speaks the control-plane bridge
API. Phase 3 keeps the client deliberately small and safe: it reports workspace
state, executes non-invasive commands, and returns structured failures for
operations that require an in-process FreeCAD workbench hook.
"""

from __future__ import annotations

import base64
import json
import os
import time
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SUPPORTED_COMMANDS = [
    "inspect_document",
    "select_object",
    "run_macro",
    "save_revision",
    "capture_screenshot",
]
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0


JsonPost = Callable[[str, dict[str, Any], float], dict[str, Any]]


class BridgeCommandError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(f"[freecad-bridge] {message}", flush=True)


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def float_env(env: dict[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.1, value)


def workspace(env: dict[str, str]) -> Path:
    return Path(env.get("CAD_SESSION_WORKSPACE") or "/workspace")


def bridge_id(env: dict[str, str]) -> str:
    explicit = (env.get("CAD_BRIDGE_ID") or "").strip()
    if explicit:
        return explicit
    session_id = (env.get("CAD_REMOTE_SESSION_ID") or env.get("CAD_SESSION_ID") or "unknown").strip()
    return f"4yi-freecad-bridge-{session_id}"


def read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": "4yi.freecad.bridge.read_error.v1",
            "path": str(path),
            "error": str(exc),
            "fallback": default,
        }


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def selection_path(env: dict[str, str]) -> Path:
    return Path(env.get("CAD_BRIDGE_SELECTION_PATH") or workspace(env) / "bridge-selection.json")


def document_tree_path(env: dict[str, str]) -> Path:
    return Path(env.get("CAD_BRIDGE_DOCUMENT_TREE_PATH") or workspace(env) / "bridge-document-tree.json")


def console_path(env: dict[str, str]) -> Path:
    return Path(env.get("CAD_BRIDGE_CONSOLE_PATH") or workspace(env) / "bridge-console.log")


def screenshot_path(env: dict[str, str]) -> Path:
    return Path(env.get("CAD_BRIDGE_SCREENSHOT_PATH") or workspace(env) / "screenshot.png")


def active_document_path(env: dict[str, str]) -> str | None:
    explicit = (env.get("SESSION_FCSTD_PATH") or env.get("CAD_BRIDGE_ACTIVE_DOCUMENT_PATH") or "").strip()
    if explicit:
        return explicit
    candidates = sorted(workspace(env).glob("*.FCStd"))
    if candidates:
        return str(candidates[0])
    return None


def active_document_name(env: dict[str, str]) -> str | None:
    active_path = active_document_path(env)
    if not active_path:
        return None
    return Path(active_path).name


def workspace_file_entries(env: dict[str, str]) -> list[dict[str, Any]]:
    root = workspace(env)
    if not root.exists():
        return []
    entries = []
    for path in sorted(root.iterdir()):
        if path.is_file():
            try:
                size = path.stat().st_size
            except OSError:
                size = None
            entries.append({"name": path.name, "size": size})
    return entries[:200]


def current_selection(env: dict[str, str]) -> dict[str, Any]:
    default = {
        "schema": "4yi.freecad.bridge.selection.v1",
        "objects": [],
        "active_object": None,
        "source": "workspace_fallback",
        "updated_at": None,
    }
    value = read_json_file(selection_path(env), default)
    return value if isinstance(value, dict) else default


def current_document_tree(env: dict[str, str]) -> dict[str, Any]:
    default = {
        "schema": "4yi.freecad.bridge.document_tree.v1",
        "document": {
            "name": active_document_name(env),
            "path": active_document_path(env),
        },
        "objects": [],
        "source": "workspace_fallback",
        "workspace_files": workspace_file_entries(env),
    }
    value = read_json_file(document_tree_path(env), default)
    return value if isinstance(value, dict) else default


def console_tail(env: dict[str, str], *, limit: int = 40) -> list[str]:
    path = console_path(env)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [f"console read failed: {exc}"]
    return lines[-limit:]


def capabilities(env: dict[str, str]) -> list[str]:
    available = list(SUPPORTED_COMMANDS)
    if not truthy(env.get("CAD_BRIDGE_ALLOW_MACRO_EXEC")):
        available.append("structured_macro_error")
    return available


def heartbeat_payload(env: dict[str, str], *, event: str = "heartbeat") -> dict[str, Any]:
    return {
        "bridge_id": bridge_id(env),
        "freecad_version": env.get("FREECAD_VERSION") or "unknown",
        "document_name": active_document_name(env),
        "active_document_path": active_document_path(env),
        "current_version_id": env.get("CAD_CURRENT_VERSION_ID") or None,
        "workbench": env.get("CAD_FREECAD_WORKBENCH") or None,
        "selection": current_selection(env),
        "document_tree": current_document_tree(env),
        "console_tail": console_tail(env),
        "capabilities": capabilities(env),
        "metadata": {
            "event": event,
            "workspace": str(workspace(env)),
            "client": "freecad-bridge-client",
            "client_schema": "4yi.freecad.bridge.client.v1",
            "poll_interval_seconds": float_env(
                env,
                "CAD_BRIDGE_POLL_INTERVAL_SECONDS",
                DEFAULT_POLL_INTERVAL_SECONDS,
            ),
        },
    }


def post_json(url: str, payload: dict[str, Any], timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def command_payload(command: dict[str, Any]) -> dict[str, Any]:
    value = command.get("input") or {}
    if not isinstance(value, dict):
        return {}
    return value


def command_result_url(env: dict[str, str], command_id: str) -> str:
    explicit = (env.get("CAD_BRIDGE_COMMAND_RESULT_URL_BASE") or "").strip()
    if explicit:
        return f"{explicit.rstrip('/')}/{command_id}/result"
    poll_url = (env.get("CAD_BRIDGE_POLL_URL") or "").strip()
    if poll_url.endswith("/bridge/poll"):
        return f"{poll_url[: -len('/poll')]}/commands/{command_id}/result"
    raise RuntimeError("CAD_BRIDGE_COMMAND_RESULT_URL_BASE is required")


def command_result_payload(
    command: dict[str, Any],
    *,
    status: str,
    result: dict[str, Any],
    error: str | None,
    started_at: str,
    env: dict[str, str],
) -> dict[str, Any]:
    completed_at = utc_now()
    transaction = {
        "id": f"txn_{uuid.uuid4().hex}",
        "command_id": command.get("command_id") or command.get("id"),
        "op": command.get("op"),
        "started_at": started_at,
        "completed_at": completed_at,
        "ok": status == "completed",
        "undo_available": bool(result.get("undo", {}).get("available", False)),
        "recompute_status": result.get("recompute_status") or {"status": "not_run"},
    }
    merged_result = {
        "schema": "4yi.freecad.bridge.command_result.v1",
        "transaction": transaction,
        "changed_objects": result.get("changed_objects") or [],
        "console": result.get("console") or console_tail(env),
        "recompute_status": transaction["recompute_status"],
        "undo": result.get("undo") or {"available": False, "source": "bridge_client"},
        **result,
    }
    if error:
        merged_result.setdefault(
            "error",
            {
                "code": "bridge_command_failed",
                "message": error,
                "details": {},
            },
        )
    return {
        "status": status,
        "result": merged_result,
        "error": error,
        "current_version_id": env.get("CAD_CURRENT_VERSION_ID") or None,
        "metadata": {
            "bridge_id": bridge_id(env),
            "transaction_id": transaction["id"],
            "op": command.get("op"),
        },
    }


def execute_inspect_document(env: dict[str, str]) -> dict[str, Any]:
    return {
        "document_tree": current_document_tree(env),
        "selection": current_selection(env),
        "active_document_path": active_document_path(env),
        "workspace_files": workspace_file_entries(env),
        "console": console_tail(env),
        "changed_objects": [],
        "recompute_status": {"status": "not_run"},
        "undo": {"available": False, "source": "inspect_document"},
    }


def execute_select_object(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    selector = payload.get("selector") if isinstance(payload.get("selector"), dict) else {}
    object_name = (
        payload.get("object_name")
        or payload.get("name")
        or selector.get("object_name")
        or selector.get("name")
        or selector.get("label")
    )
    if not object_name:
        raise BridgeCommandError(
            "selection_target_required",
            "select_object requires object_name, name, or selector.name",
        )
    selected = {
        "name": str(object_name),
        "label": str(payload.get("label") or selector.get("label") or object_name),
        "type_id": payload.get("type_id") or selector.get("type_id"),
        "reference": payload.get("reference") or selector.get("reference"),
    }
    selection = {
        "schema": "4yi.freecad.bridge.selection.v1",
        "objects": [selected],
        "active_object": selected,
        "source": "bridge_command",
        "updated_at": utc_now(),
    }
    write_json_file(selection_path(env), selection)
    return {
        "selection": selection,
        "changed_objects": [selected["name"]],
        "console": [f"Selected {selected['name']}"],
        "recompute_status": {"status": "not_run"},
        "undo": {"available": False, "source": "select_object"},
    }


def execute_run_macro(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    macro = payload.get("macro") or payload.get("script") or ""
    macro_path = workspace(env) / "bridge-last-macro.py"
    macro_path.write_text(str(macro), encoding="utf-8")
    if not truthy(env.get("CAD_BRIDGE_ALLOW_MACRO_EXEC")):
        raise BridgeCommandError(
            "macro_execution_disabled",
            "run_macro is disabled in the standalone bridge client",
            details={
                "macro_path": str(macro_path),
                "enable_with": "CAD_BRIDGE_ALLOW_MACRO_EXEC=1",
            },
        )
    raise BridgeCommandError(
        "macro_execution_requires_freecad_addon",
        "run_macro requires the in-process FreeCAD addon bridge",
        details={"macro_path": str(macro_path)},
    )


def execute_save_revision(
    payload: dict[str, Any],
    env: dict[str, str],
    http_post: JsonPost,
    timeout: float,
) -> dict[str, Any]:
    save_url = (env.get("CAD_BRIDGE_SAVE_URL") or "").strip()
    if not save_url:
        raise BridgeCommandError(
            "save_url_not_configured",
            "CAD_BRIDGE_SAVE_URL is required for save_revision",
        )
    candidate = payload.get("fcstd_path") or env.get("CAD_BRIDGE_OUTPUT_FCSTD_PATH") or "/workspace/output.FCStd"
    path = Path(str(candidate))
    if not path.exists():
        active_path = active_document_path(env)
        path = Path(active_path) if active_path else path
    if not path.exists():
        raise BridgeCommandError(
            "fcstd_not_found",
            "save_revision could not find an FCStd file to upload",
            details={"checked_path": str(candidate), "active_document_path": active_document_path(env)},
        )
    fcstd_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    save_payload = {
        "message": payload.get("message") or "Remote FreeCAD bridge save",
        "fcstd_b64": fcstd_b64,
        "base_version_id": payload.get("base_version_id") or env.get("CAD_CURRENT_VERSION_ID") or None,
        "preview_png_b64": payload.get("preview_png_b64"),
        "artifacts": payload.get("artifacts") or {},
        "include_derivatives": bool(payload.get("include_derivatives", True)),
    }
    save_result = http_post(save_url, save_payload, timeout)
    version = save_result.get("version") or {}
    next_version_id = version.get("id") or save_result.get("version_id")
    if next_version_id:
        env["CAD_CURRENT_VERSION_ID"] = next_version_id
    return {
        "save": save_result,
        "artifact_refs": save_result.get("artifact_refs") or {},
        "changed_objects": [],
        "console": [f"Saved {path.name}"],
        "recompute_status": {"status": "not_run"},
        "undo": {"available": False, "source": "save_revision"},
    }


def execute_capture_screenshot(env: dict[str, str]) -> dict[str, Any]:
    path = screenshot_path(env)
    if not path.exists():
        raise BridgeCommandError(
            "screenshot_not_available",
            "capture_screenshot requires a screenshot file from the GUI bridge",
            details={"screenshot_path": str(path)},
        )
    return {
        "screenshot_png_b64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "artifact_refs": {"screenshot_png": str(path)},
        "changed_objects": [],
        "console": [f"Captured screenshot from {path.name}"],
        "recompute_status": {"status": "not_run"},
        "undo": {"available": False, "source": "capture_screenshot"},
    }


def execute_command(
    command: dict[str, Any],
    env: dict[str, str],
    http_post: JsonPost = post_json,
    timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    started_at = utc_now()
    op = command.get("op")
    payload = command_payload(command)
    try:
        if op == "inspect_document":
            result = execute_inspect_document(env)
        elif op == "select_object":
            result = execute_select_object(payload, env)
        elif op == "run_macro":
            result = execute_run_macro(payload, env)
        elif op == "save_revision":
            result = execute_save_revision(payload, env, http_post, timeout)
        elif op == "capture_screenshot":
            result = execute_capture_screenshot(env)
        else:
            raise BridgeCommandError(
                "unsupported_command",
                f"unsupported bridge command op: {op}",
                details={"supported_commands": SUPPORTED_COMMANDS},
            )
        return command_result_payload(
            command,
            status="completed",
            result=result,
            error=None,
            started_at=started_at,
            env=env,
        )
    except BridgeCommandError as exc:
        return command_result_payload(
            command,
            status="failed",
            result={
                "error": exc.to_dict(),
                "changed_objects": [],
                "console": console_tail(env),
                "recompute_status": {"status": "not_run", "error": exc.code},
                "undo": {"available": False, "source": "bridge_client"},
            },
            error=exc.message,
            started_at=started_at,
            env=env,
        )
    except Exception as exc:  # noqa: BLE001
        return command_result_payload(
            command,
            status="failed",
            result={
                "error": {
                    "code": "bridge_client_exception",
                    "message": str(exc),
                    "details": {"traceback": traceback.format_exc(limit=12)},
                },
                "changed_objects": [],
                "console": console_tail(env),
                "recompute_status": {"status": "not_run", "error": "bridge_client_exception"},
                "undo": {"available": False, "source": "bridge_client"},
            },
            error=str(exc),
            started_at=started_at,
            env=env,
        )


def poll_payload(env: dict[str, str]) -> dict[str, Any]:
    return {
        **heartbeat_payload(env, event="poll"),
        "max_commands": int(float_env(env, "CAD_BRIDGE_MAX_COMMANDS", 10)),
    }


def run_once(
    env: dict[str, str] | None = None,
    http_post: JsonPost = post_json,
    timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    env = env if env is not None else os.environ
    heartbeat_url = (env.get("CAD_BRIDGE_HEARTBEAT_URL") or "").strip()
    poll_url = (env.get("CAD_BRIDGE_POLL_URL") or "").strip()
    if not poll_url:
        raise RuntimeError("CAD_BRIDGE_POLL_URL is required")

    heartbeat_sent = False
    if heartbeat_url:
        http_post(heartbeat_url, heartbeat_payload(env, event="heartbeat"), timeout)
        heartbeat_sent = True

    poll_response = http_post(poll_url, poll_payload(env), timeout)
    commands = poll_response.get("commands") or []
    results = []
    for command in commands:
        command_id = command.get("command_id") or command.get("id")
        if not command_id:
            continue
        result_payload = execute_command(command, env, http_post=http_post, timeout=timeout)
        result_url = command_result_url(env, str(command_id))
        http_post(result_url, result_payload, timeout)
        results.append(
            {
                "command_id": command_id,
                "op": command.get("op"),
                "status": result_payload["status"],
            }
        )
    return {
        "heartbeat_sent": heartbeat_sent,
        "command_count": len(commands),
        "results": results,
    }


def main() -> int:
    env = os.environ
    interval = float_env(env, "CAD_BRIDGE_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
    timeout = float_env(env, "CAD_BRIDGE_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS)
    oneshot = truthy(env.get("CAD_BRIDGE_ONESHOT"))
    log(f"bridge client started for {env.get('CAD_REMOTE_SESSION_ID') or env.get('CAD_SESSION_ID')}")
    while True:
        try:
            summary = run_once(env, post_json, timeout)
            if summary["command_count"]:
                log(f"processed {summary['command_count']} command(s): {summary['results']}")
        except Exception as exc:  # noqa: BLE001
            log(f"loop error: {exc}")
        if oneshot:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
