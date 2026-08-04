#!/usr/bin/env bash
set -Eeuo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-99}"
SCREEN="${SCREEN:-0}"
GEOMETRY="${GEOMETRY:-1600x1000x24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
PORT="${PORT:-8080}"
CAD_UNIFIED_APP="${CAD_UNIFIED_APP:-0}"
CAD_API_HOST="${CAD_API_HOST:-0.0.0.0}"
CAD_API_APP_DIR="${CAD_API_APP_DIR:-/app}"
CAD_API_MODULE="${CAD_API_MODULE:-app.main:app}"
CAD_SESSION_WORKSPACE="${CAD_SESSION_WORKSPACE:-/workspace}"
CAD_DATA_DIR="${CAD_DATA_DIR:-/data/4yi-cad}"
CAD_RUNTIME_DIR="${CAD_RUNTIME_DIR:-${CAD_DATA_DIR}/runtime}"
SESSION_FCSTD_PATH="${SESSION_FCSTD_PATH:-}"
CAD_CONTROL_PLANE_URL="${CAD_CONTROL_PLANE_URL:-}"
CAD_BRIDGE_AUTOSTART="${CAD_BRIDGE_AUTOSTART:-1}"
CAD_BRIDGE_MODE="${CAD_BRIDGE_MODE:-freecad_addon}"
CAD_BRIDGE_CLIENT_BIN="${CAD_BRIDGE_CLIENT_BIN:-/usr/local/bin/freecad-bridge-client.py}"
CAD_BRIDGE_POLL_URL="${CAD_BRIDGE_POLL_URL:-}"
CAD_BRIDGE_POLL_INTERVAL_SECONDS="${CAD_BRIDGE_POLL_INTERVAL_SECONDS:-2}"
NOVNC_ROOT="${NOVNC_ROOT:-/usr/share/novnc}"
export DISPLAY=":${DISPLAY_NUM}"

PIDS=()
LAST_BACKGROUND_PID=""
CAD_API_PID=""
FREECAD_PID=""

log() {
  printf '[freecad-gui] %s\n' "$*"
}

cleanup() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
}
trap cleanup EXIT INT TERM

start_background() {
  "$@" &
  local pid="$!"
  PIDS+=("$pid")
  LAST_BACKGROUND_PID="$pid"
}

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    log "missing required command: $name"
    exit 127
  fi
}

seed_freecad_user_cfg() {
  local template="/usr/local/share/4yi-cad/freecad-user.cfg"
  [ -f "$template" ] || return 0
  local dir
  for dir in "$HOME/.config/FreeCAD" "$HOME/.FreeCAD"; do
    mkdir -p "$dir"
    [ -f "$dir/user.cfg" ] || cp "$template" "$dir/user.cfg"
  done
}

resolve_freecad_bin() {
  if [ -n "${FREECAD_BIN:-}" ]; then
    printf '%s\n' "$FREECAD_BIN"
    return
  fi
  if command -v FreeCAD >/dev/null 2>&1; then
    command -v FreeCAD
    return
  fi
  if command -v freecad >/dev/null 2>&1; then
    command -v freecad
    return
  fi
  log "missing required command: FreeCAD or freecad"
  exit 127
}

start_novnc() {
  if [ -x "${NOVNC_ROOT}/utils/novnc_proxy" ]; then
    start_background "${NOVNC_ROOT}/utils/novnc_proxy" \
      --listen "${NOVNC_PORT}" \
      --vnc "127.0.0.1:${VNC_PORT}"
    return
  fi
  require_command websockify
  start_background websockify \
    --web "${NOVNC_ROOT}" \
    "${NOVNC_PORT}" \
    "127.0.0.1:${VNC_PORT}"
}

configure_unified_app_defaults() {
  if [ "${CAD_UNIFIED_APP}" != "1" ]; then
    return
  fi

  local control_plane_url="http://127.0.0.1:${PORT}"
  local remote_session_id="${CAD_REMOTE_SESSION_ID:-${CAD_SHARED_FREECAD_SESSION_ID:-shared-freecad-gui}}"

  export PORT
  export CAD_DATA_DIR
  export TMPDIR="${TMPDIR:-${CAD_RUNTIME_DIR}/tmp}"
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-${CAD_RUNTIME_DIR}/xdg-runtime}"
  export CAD_GUI_SESSION_BACKEND="${CAD_GUI_SESSION_BACKEND:-shared_service}"
  export CAD_FREECAD_FIRST_ENTRY="${CAD_FREECAD_FIRST_ENTRY:-1}"
  export CAD_SHARED_FREECAD_SESSION_ID="${CAD_SHARED_FREECAD_SESSION_ID:-${remote_session_id}}"
  export CAD_REMOTE_SESSION_ID="${CAD_REMOTE_SESSION_ID:-${CAD_SHARED_FREECAD_SESSION_ID}}"
  export CAD_SESSION_ID="${CAD_SESSION_ID:-${CAD_REMOTE_SESSION_ID}}"
  export CAD_WORKBENCH_SESSION_ID="${CAD_WORKBENCH_SESSION_ID:-${CAD_REMOTE_SESSION_ID}}"
  export CAD_REMOTE_DESKTOP_BASE_URL="${CAD_REMOTE_DESKTOP_BASE_URL:-/freecad/vnc.html?autoconnect=1&resize=remote&path=freecad/websockify}"
  export CAD_FREECAD_GUI_PROXY_PREFIX="${CAD_FREECAD_GUI_PROXY_PREFIX:-/freecad}"
  export CAD_FREECAD_GUI_UPSTREAM_URL="${CAD_FREECAD_GUI_UPSTREAM_URL:-http://127.0.0.1:${NOVNC_PORT}}"
  export CAD_CONTROL_PLANE_URL="${CAD_CONTROL_PLANE_URL:-${control_plane_url}}"
  export CAD_GUI_SESSION_CONTROL_PLANE_URL="${CAD_GUI_SESSION_CONTROL_PLANE_URL:-${CAD_CONTROL_PLANE_URL}}"
  export CAD_BRIDGE_HEARTBEAT_URL="${CAD_BRIDGE_HEARTBEAT_URL:-${CAD_CONTROL_PLANE_URL}/api/freecad/sessions/${CAD_REMOTE_SESSION_ID}/bridge/heartbeat}"
  export CAD_BRIDGE_POLL_URL="${CAD_BRIDGE_POLL_URL:-${CAD_CONTROL_PLANE_URL}/api/freecad/sessions/${CAD_REMOTE_SESSION_ID}/bridge/poll}"
  export CAD_BRIDGE_COMMAND_RESULT_URL_BASE="${CAD_BRIDGE_COMMAND_RESULT_URL_BASE:-${CAD_CONTROL_PLANE_URL}/api/freecad/sessions/${CAD_REMOTE_SESSION_ID}/bridge/commands}"
  export CAD_BRIDGE_COMMAND_QUEUE_URL="${CAD_BRIDGE_COMMAND_QUEUE_URL:-${CAD_CONTROL_PLANE_URL}/api/freecad/sessions/${CAD_REMOTE_SESSION_ID}/commands}"
  export CAD_BRIDGE_SAVE_URL="${CAD_BRIDGE_SAVE_URL:-${CAD_CONTROL_PLANE_URL}/api/freecad/sessions/${CAD_REMOTE_SESSION_ID}/save}"
  export CAD_PANEL_ACTION_URL="${CAD_PANEL_ACTION_URL:-${CAD_CONTROL_PLANE_URL}/api/freecad/sessions/${CAD_REMOTE_SESSION_ID}/panel/actions}"
}

start_unified_app_control_plane() {
  if [ "${CAD_UNIFIED_APP}" != "1" ]; then
    return
  fi

  require_command python3
  log "starting FastAPI control plane on ${CAD_API_HOST}:${PORT}"
  start_background python3 -m uvicorn "${CAD_API_MODULE}" \
    --app-dir "${CAD_API_APP_DIR}" \
    --host "${CAD_API_HOST}" \
    --port "${PORT}"
  CAD_API_PID="$LAST_BACKGROUND_PID"
}

start_freecad_gui() {
  log "starting FreeCAD GUI"
  start_background "${FREECAD_RESOLVED_BIN}" "${FREECAD_ARGS[@]}"
  FREECAD_PID="$LAST_BACKGROUND_PID"
}

supervise_unified_app() {
  if [ "${CAD_UNIFIED_APP}" != "1" ]; then
    exec "${FREECAD_RESOLVED_BIN}" "${FREECAD_ARGS[@]}"
  fi

  start_freecad_gui
  while true; do
    if [ -n "${CAD_API_PID}" ] && ! kill -0 "${CAD_API_PID}" >/dev/null 2>&1; then
      wait "${CAD_API_PID}" || exit "$?"
      exit 0
    fi
    if [ -n "${FREECAD_PID}" ] && ! kill -0 "${FREECAD_PID}" >/dev/null 2>&1; then
      wait "${FREECAD_PID}" || true
      log "FreeCAD GUI exited; restarting in 5 seconds"
      sleep 5
      start_freecad_gui
    fi
    sleep 2
  done
}

configure_unified_app_defaults
mkdir -p "${CAD_SESSION_WORKSPACE}" "${CAD_DATA_DIR}" "${CAD_RUNTIME_DIR}" "${TMPDIR:-/tmp}" "${XDG_RUNTIME_DIR:-/tmp/runtime-appuser}" /tmp/4yi-cad-freecad-gui
chmod 700 "${XDG_RUNTIME_DIR:-/tmp/runtime-appuser}" >/dev/null 2>&1 || true
seed_freecad_user_cfg
cd "${CAD_SESSION_WORKSPACE}"

FREECAD_RESOLVED_BIN="$(resolve_freecad_bin)"
require_command Xvfb
require_command x11vnc

start_unified_app_control_plane

log "starting Xvfb on ${DISPLAY} with ${GEOMETRY}"
start_background Xvfb "${DISPLAY}" -screen "${SCREEN}" "${GEOMETRY}" -ac +extension GLX +render -noreset
sleep 1

if command -v fluxbox >/dev/null 2>&1; then
  log "starting Fluxbox"
  start_background fluxbox
elif command -v openbox >/dev/null 2>&1; then
  log "starting Openbox"
  start_background openbox
else
  log "no window manager found; continuing with bare Xvfb"
fi

log "starting x11vnc on ${VNC_PORT}"
start_background x11vnc \
  -display "${DISPLAY}" \
  -localhost \
  -forever \
  -shared \
  -nopw \
  -rfbport "${VNC_PORT}" \
  -quiet

log "starting noVNC on ${NOVNC_PORT}"
start_novnc

FREECAD_ARGS=()
if [ -n "${SESSION_FCSTD_PATH}" ]; then
  if [ ! -f "${SESSION_FCSTD_PATH}" ]; then
    log "SESSION_FCSTD_PATH does not exist: ${SESSION_FCSTD_PATH}"
    exit 66
  fi
  FREECAD_ARGS+=("${SESSION_FCSTD_PATH}")
  log "loading FCStd: ${SESSION_FCSTD_PATH}"
else
  log "starting empty FreeCAD document"
fi

touch /tmp/4yi-cad-freecad-gui/ready
if [ -n "${CAD_CONTROL_PLANE_URL}" ]; then
  log "control plane bridge endpoints configured"
fi
if [ "${CAD_BRIDGE_AUTOSTART}" = "1" ] && [ -n "${CAD_BRIDGE_POLL_URL}" ]; then
  case "${CAD_BRIDGE_MODE}" in
    standalone)
      if [ -f "${CAD_BRIDGE_CLIENT_BIN}" ]; then
        log "starting standalone FreeCAD bridge client"
        start_background python3 "${CAD_BRIDGE_CLIENT_BIN}"
      else
        log "FreeCAD bridge client not found: ${CAD_BRIDGE_CLIENT_BIN}"
      fi
      ;;
    freecad_addon|addon|in_process)
      log "FreeCAD addon bridge mode selected"
      ;;
    both)
      if [ -f "${CAD_BRIDGE_CLIENT_BIN}" ]; then
        log "starting standalone FreeCAD bridge client with addon bridge mode"
        start_background python3 "${CAD_BRIDGE_CLIENT_BIN}"
      fi
      ;;
    *)
      log "unknown CAD_BRIDGE_MODE=${CAD_BRIDGE_MODE}; continuing without standalone bridge client"
      ;;
  esac
fi
log "open http://127.0.0.1:${NOVNC_PORT}/vnc.html?autoconnect=1&resize=remote"
supervise_unified_app
