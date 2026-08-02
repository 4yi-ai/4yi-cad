#!/usr/bin/env bash
set -Eeuo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-99}"
SCREEN="${SCREEN:-0}"
GEOMETRY="${GEOMETRY:-1600x1000x24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
CAD_SESSION_WORKSPACE="${CAD_SESSION_WORKSPACE:-/workspace}"
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
  PIDS+=("$!")
}

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    log "missing required command: $name"
    exit 127
  fi
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

mkdir -p "${CAD_SESSION_WORKSPACE}" /tmp/4yi-cad-freecad-gui
cd "${CAD_SESSION_WORKSPACE}"

FREECAD_RESOLVED_BIN="$(resolve_freecad_bin)"
require_command Xvfb
require_command x11vnc

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
exec "${FREECAD_RESOLVED_BIN}" "${FREECAD_ARGS[@]}"
