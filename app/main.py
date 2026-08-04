"""FastAPI surface for the 4yi-cad dedicated app.

Single origin, single process. The main process only orchestrates, serves the
trivial /healthz probe, and streams SSE — every CAD operation runs in the sandbox
subprocess (app/cad/runner.py), so a long render can never block the health probe
and trip the k8s liveness check (plan review I6).

create_app is dependency-injected (gateway + execute) for tests; in production
both are built lazily from the injected gateway env on first request, so importing
the module and answering /healthz never require config.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import urllib.parse
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.loop import ExecResult, run_generation
from app.artifact_store import ArtifactStore, FileArtifactStore
from app.cad.design_state import (
    CadPatch,
    DesignState,
    apply_patches,
    default_design_state,
    geometry_summary,
    render_cadquery_script,
)
from app.cad.freecad import (
    MINIMAL_FREECAD_SMOKE_SCRIPT,
    run_freecad_document_edit_sandboxed,
    run_freecad_document_inspect_sandboxed,
    run_freecad_document_patch_sandboxed,
    run_freecad_import_sandboxed,
    run_freecad_sandboxed,
)
from app.cad.script_params import (
    ScriptParameterPatch,
    apply_script_parameter_patches,
    extract_script_parameters,
)
from app.cad.site_layout_templates import (
    site_layout_audit_from_summary,
    site_layout_failure_message,
    site_layout_needs_repair,
    site_layout_repair_script,
)
from app.connect_page import CONNECT_PAGE_HTML
from app.evals.report import (
    build_ai_quality_checks,
    default_report_path,
    load_latest_eval_report,
)
from app.events import HEARTBEAT_FRAME, HEARTBEAT_INTERVAL_S, format_sse
from app.freecad_gui_orchestrator import (
    FreeCadGuiSessionOrchestrator,
    freecad_gui_orchestrator_from_env,
)
from app.freecad_intents import parse_freecad_intent
from app.freecad_state import storage_status, typed_state_diff
from app.session_store import SessionStore, SqliteSessionStore, utc_now

# The SPA is a single self-contained file at the repo root, served same-origin.
# Living at the root (next to pyproject/Dockerfile) also makes the deployment
# scanner classify this one container as a fullstack service, not an undeployed
# standalone frontend.
_INDEX_HTML = Path(__file__).resolve().parents[1] / "index.html"
_STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_FREECAD_UPLOAD_MAX_BYTES = 100 * 1024 * 1024
FREECAD_IMPORT_FORMATS = ("fcstd", "step", "stp", "iges", "igs", "brep")
FREECAD_SANDBOX_TIMEOUT_S = 240
FREECAD_SANDBOX_CPU_SECONDS = 240
FREECAD_SANDBOX_ADDRESS_SPACE_MB = 4096
FREECAD_SMOKE_TIMEOUT_S = 120
FREECAD_SMOKE_CPU_SECONDS = 90
FREECAD_GUI_PROXY_DEFAULT_PREFIX = "/freecad"
FREECAD_GUI_PROXY_HTTP_TIMEOUT_S = 20.0
# Bearer token guard: only these path prefixes require an API token. Requests
# from inside the container/loopback (or the ASGI TestClient's synthetic
# default) are exempt so local orchestration and existing tests never need a
# token; everything else must present a valid `Authorization: Bearer <token>`
# header (see app/session_store.py's create_api_token/verify_api_token).
GUARDED_PREFIXES = ("/api/freecad/sessions", "/api/generate")
BEARER_GUARD_EXEMPT_HOSTS = {"127.0.0.1", "::1", "testclient"}
# Native local FreeCAD addon (Plugin V2 P2) remote session ids: auto-registered
# on first bridge contact the same way the shared kiosk session id is, so the
# addon's first heartbeat creates its session instead of 404ing. Independent
# of the shared/GUI backend config (see _ensure_shared_remote_freecad_session).
_LOCAL_SESSION_ID_RE = re.compile(r"^local-[A-Za-z0-9][A-Za-z0-9_.-]{2,62}$")
API_TOKEN_REQUIRED_DETAIL = "api_token_required"
API_TOKEN_INVALID_DETAIL = "api_token_invalid"
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _freecad_upload_max_bytes() -> int:
    raw = (
        os.environ.get("CAD_FREECAD_UPLOAD_MAX_BYTES")
        or os.environ.get("FOURYI_CAD_UPLOAD_MAX_BYTES")
        or ""
    ).strip()
    if not raw:
        return DEFAULT_FREECAD_UPLOAD_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_FREECAD_UPLOAD_MAX_BYTES
    return value if value > 0 else DEFAULT_FREECAD_UPLOAD_MAX_BYTES


def _estimated_base64_decoded_size(data_b64: str) -> int:
    compact = "".join(str(data_b64 or "").split())
    if not compact:
        return 0
    padding = len(compact) - len(compact.rstrip("="))
    return max(0, (len(compact) * 3) // 4 - padding)


def _enforce_freecad_upload_size(data_b64: str, *, label: str) -> None:
    max_bytes = _freecad_upload_max_bytes()
    size = _estimated_base64_decoded_size(data_b64)
    if size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{label} is {size} bytes; max allowed is {max_bytes} bytes. "
                "Increase CAD_FREECAD_UPLOAD_MAX_BYTES for larger private-beta files."
            ),
        )


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    # Client replays prior turns, but the payload is untrusted and may contain
    # old localStorage shapes. app.agent.loop sanitizes before gateway use.
    history: list[Any] = Field(default_factory=list)


class DesignPatchRequest(BaseModel):
    design_state: DesignState = Field(default_factory=default_design_state)
    patches: list[CadPatch] = Field(default_factory=list)


class DesignRenderRequest(BaseModel):
    design_state: DesignState = Field(default_factory=default_design_state)


class ScriptPatchItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    value: float


class ScriptPatchRequest(BaseModel):
    script: str = Field(..., min_length=1)
    engine: Literal["cadquery", "freecad"] = "cadquery"
    patches: list[ScriptPatchItem] = Field(default_factory=list)


class CreateApiTokenRequest(BaseModel):
    label: str | None = Field(default=None, max_length=160)


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=160)


class SessionVersionRequest(BaseModel):
    intent: Literal["create", "modify", "repair", "rollback"] = "modify"
    user_instruction: str | None = Field(default=None, max_length=4000)
    design_state: DesignState = Field(default_factory=default_design_state)
    script: str = Field(..., min_length=1)
    geometry_summary: dict = Field(default_factory=dict)
    patch: dict | None = None
    metadata: dict = Field(default_factory=dict)
    preview_png_b64: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    status: Literal["ok", "failed"] = "ok"
    error: str | None = Field(default=None, max_length=8000)


class RollbackSessionRequest(BaseModel):
    version_id: str = Field(..., min_length=1)
    user_instruction: str | None = Field(default=None, max_length=4000)


class FreeCadImportModelRequest(BaseModel):
    format: str = Field(..., min_length=1, max_length=16)
    data_b64: str = Field(..., min_length=1)
    filename: str | None = Field(default=None, max_length=240)
    session_id: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, max_length=160)
    user_instruction: str | None = Field(default=None, max_length=4000)


class FreeCadDocumentEditRequest(BaseModel):
    script: str = Field(..., min_length=1)
    fcstd_b64: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    version_id: str | None = Field(default=None, min_length=1)
    user_instruction: str | None = Field(default=None, max_length=4000)


class FreeCadDocumentInspectRequest(BaseModel):
    fcstd_b64: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    version_id: str | None = Field(default=None, min_length=1)


class FreeCadIntentRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    document_summary: dict[str, Any] = Field(default_factory=dict)


class FreeCadObjectSelector(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    type_id: str | None = Field(default=None, min_length=1, max_length=160)


class FreeCadDocumentPatchItem(BaseModel):
    op: Literal[
        "create_feature",
        "delete_feature",
        "set_body_tip",
        "set_placement",
        "set_expression",
        "create_sketch",
        "attach_sketch",
        "add_geometry",
        "add_external_geometry",
        "set_geometry_point",
        "move_geometry",
        "move_sketch_geometry",
        "set_geometry_construction",
        "toggle_geometry_construction",
        "add_constraint",
        "add_endpoint_coincidence",
        "remove_constraint",
        "set_constraint_state",
        "solver_status",
        "validate_sketch",
        "create_assembly",
        "add_part_to_assembly",
        "remove_part_from_assembly",
        "set_assembly_part_placement",
        "ground_assembly_part",
        "create_joint",
        "update_joint",
        "solve_assembly",
        "create_techdraw_page",
        "add_techdraw_view",
        "add_techdraw_projection_group",
        "add_techdraw_section_view",
        "add_techdraw_detail_view",
        "add_techdraw_centerline",
        "add_techdraw_cosmetic_vertex",
        "add_techdraw_cosmetic_line",
        "export_techdraw_pdf",
        "add_techdraw_dimension",
        "set_property",
        "set_constraint_value",
    ]
    selector: FreeCadObjectSelector | None = None
    parent_selector: FreeCadObjectSelector | None = None
    assembly_selector: FreeCadObjectSelector | None = None
    body_selector: FreeCadObjectSelector | None = None
    part_selector: FreeCadObjectSelector | None = None
    part1_selector: FreeCadObjectSelector | None = None
    part2_selector: FreeCadObjectSelector | None = None
    page_selector: FreeCadObjectSelector | None = None
    view_selector: FreeCadObjectSelector | None = None
    base_view_selector: FreeCadObjectSelector | None = None
    sketch_selector: FreeCadObjectSelector | None = None
    support_selector: FreeCadObjectSelector | None = None
    source_selector: FreeCadObjectSelector | None = None
    feature_selector: FreeCadObjectSelector | None = None
    tip_selector: FreeCadObjectSelector | None = None
    joint_selector: FreeCadObjectSelector | None = None
    target_selector: FreeCadObjectSelector | None = None
    type_id: str | None = Field(default=None, min_length=1, max_length=160)
    type: str | None = Field(default=None, min_length=1, max_length=80)
    joint_type: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    property: str | None = Field(default=None, min_length=1, max_length=160)
    properties: dict[str, Any] | None = None
    geometry: dict[str, Any] | None = None
    geometries: list[dict[str, Any]] | None = Field(default=None, max_length=20)
    external_geometry: dict[str, Any] | None = None
    external_geometries: list[dict[str, Any]] | None = Field(default=None, max_length=40)
    constraint: dict[str, Any] | None = None
    constraints: list[dict[str, Any]] | None = Field(default=None, max_length=40)
    geometry_index: int | None = Field(default=None, ge=0)
    geometry_indexes: list[int] | None = Field(default=None, max_length=80)
    geometry_indices: list[int] | None = Field(default=None, max_length=80)
    index: int | None = Field(default=None, ge=0)
    point_role: str | None = Field(default=None, min_length=1, max_length=40)
    role: str | None = Field(default=None, min_length=1, max_length=40)
    point_pos: int | None = Field(default=None, ge=0)
    first: Any = None
    first_index: int | None = Field(default=None, ge=0)
    first_pos: int | None = Field(default=None, ge=0)
    second: Any = None
    second_index: int | None = Field(default=None, ge=0)
    second_pos: int | None = Field(default=None, ge=0)
    third: Any = None
    third_index: int | None = Field(default=None, ge=0)
    third_pos: int | None = Field(default=None, ge=0)
    args: list[Any] | None = Field(default=None, max_length=12)
    construction: bool | None = None
    toggle: bool | None = None
    auto_constraints: bool | None = None
    auto_constraint_tolerance: float | None = None
    new_name: str | None = Field(default=None, max_length=160)
    constraint_new_name: str | None = Field(default=None, max_length=160)
    rename_to: str | None = Field(default=None, max_length=160)
    active: bool | None = None
    enabled: bool | None = None
    driving: bool | None = None
    virtual_space: bool | None = None
    virtual: bool | None = None
    value: Any = None
    distance: float | None = None
    expression: str | None = Field(default=None, min_length=1, max_length=1000)
    expressions: dict[str, str] | None = None
    placement: dict[str, Any] | None = None
    attachment_offset: dict[str, Any] | None = None
    connector1: dict[str, Any] | None = None
    connector2: dict[str, Any] | None = None
    connectors: list[dict[str, Any]] | None = Field(default=None, max_length=2)
    solve: bool | None = None
    template_path: str | None = Field(default=None, min_length=1, max_length=1000)
    map_mode: str | None = Field(default=None, min_length=1, max_length=80)
    direction: list[float] | None = Field(default=None, min_length=3, max_length=3)
    x_direction: list[float] | None = Field(default=None, min_length=3, max_length=3)
    rotation_vector: list[float] | None = Field(default=None, min_length=3, max_length=3)
    x: float | None = None
    y: float | None = None
    scale: float | None = None
    rotation: float | None = None
    projection: str | None = Field(default=None, min_length=1, max_length=80)
    projections: list[str] | None = Field(default=None, max_length=8)
    projection_names: list[str] | None = Field(default=None, max_length=8)
    projection_type: str | None = Field(default=None, min_length=1, max_length=80)
    auto_distribute: bool | None = None
    spacing_x: float | None = None
    spacing_y: float | None = None
    dimension_type: str | None = Field(default=None, min_length=1, max_length=80)
    measure_type: str | None = Field(default=None, min_length=1, max_length=80)
    reference: str | None = Field(default=None, min_length=1, max_length=160)
    references: list[str] | None = Field(default=None, max_length=20)
    stable_id: str | None = Field(default=None, max_length=200)
    stable_ids: list[str] | None = Field(default=None, max_length=80)
    stable_reference: str | None = Field(default=None, max_length=200)
    stable_references: list[str] | None = Field(default=None, max_length=80)
    stable_signature: dict[str, Any] | None = None
    stable_signatures: list[Any] | None = Field(default=None, max_length=80)
    signature: dict[str, Any] | str | None = None
    signature_version: str | None = Field(default=None, max_length=80)
    ref_history: list[Any] | None = Field(default=None, max_length=80)
    element: str | None = Field(default=None, min_length=1, max_length=160)
    subelement: str | None = Field(default=None, min_length=1, max_length=160)
    base: list[float] | None = Field(default=None, min_length=3, max_length=3)
    axis: list[float] | None = Field(default=None, min_length=3, max_length=3)
    origin: list[float] | None = Field(default=None, min_length=3, max_length=3)
    section_normal: list[float] | None = Field(default=None, min_length=3, max_length=3)
    section_origin: list[float] | None = Field(default=None, min_length=3, max_length=3)
    section_symbol: str | None = Field(default=None, min_length=1, max_length=20)
    anchor_point: list[float] | None = Field(default=None, min_length=3, max_length=3)
    point: list[float] | None = Field(default=None, min_length=3, max_length=3)
    position: list[float] | None = Field(default=None, min_length=3, max_length=3)
    start: list[float] | None = Field(default=None, min_length=3, max_length=3)
    end: list[float] | None = Field(default=None, min_length=3, max_length=3)
    angle_degrees: float | None = None
    angle_radians: float | None = None
    radius: float | None = None
    centerline_mode: bool | None = None
    mode: Any = None
    constraint_index: int | None = Field(default=None, ge=0)
    constraint_name: str | None = Field(default=None, min_length=1, max_length=160)


class FreeCadDocumentPatchRequest(BaseModel):
    patches: list[FreeCadDocumentPatchItem] = Field(..., min_length=1, max_length=80)
    fcstd_b64: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    version_id: str | None = Field(default=None, min_length=1)
    user_instruction: str | None = Field(default=None, max_length=4000)
    dry_run: bool = False


class FreeCadRemoteSessionRequest(BaseModel):
    session_id: str | None = Field(default=None, min_length=1)
    workbench_session_id: str | None = Field(default=None, min_length=1)
    version_id: str | None = Field(default=None, min_length=1)
    mode: Literal["freecad_gui"] = "freecad_gui"
    reuse: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class FreeCadRemoteSessionSaveRequest(BaseModel):
    message: str | None = Field(default=None, max_length=4000)
    fcstd_b64: str = Field(..., min_length=1)
    base_version_id: str | None = Field(default=None, min_length=1)
    preview_png_b64: str | None = Field(default=None, min_length=1)
    artifacts: dict[str, str] = Field(default_factory=dict)
    include_derivatives: bool = True


class FreeCadRemoteSessionCommandRequest(BaseModel):
    op: Literal[
        "inspect_document",
        "load_model",
        "select_object",
        "run_macro",
        "save_revision",
        "capture_screenshot",
    ]
    input: dict[str, Any] = Field(default_factory=dict)
    base_version_id: str | None = Field(default=None, min_length=1)


class FreeCadRemoteSessionStopRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class FreeCadBridgeHeartbeatRequest(BaseModel):
    bridge_id: str | None = Field(default=None, max_length=160)
    freecad_version: str | None = Field(default=None, max_length=120)
    document_name: str | None = Field(default=None, max_length=240)
    active_document_path: str | None = Field(default=None, max_length=1000)
    current_version_id: str | None = Field(default=None, min_length=1)
    workbench: str | None = Field(default=None, max_length=160)
    selection: dict[str, Any] = Field(default_factory=dict)
    document_tree: dict[str, Any] = Field(default_factory=dict)
    console_tail: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FreeCadBridgePollRequest(FreeCadBridgeHeartbeatRequest):
    max_commands: int = Field(default=10, ge=1, le=50)


class FreeCadBridgeCommandResultRequest(BaseModel):
    status: Literal["completed", "failed"]
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=8000)
    current_version_id: str | None = Field(default=None, min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FreeCadPanelActionRequest(BaseModel):
    action: Literal[
        "prompt",
        "explain_object",
        "generate_patch",
        "accept_patch",
        "reject_patch",
    ]
    prompt: str | None = Field(default=None, max_length=8000)
    selection: dict[str, Any] = Field(default_factory=dict)
    patch_id: str | None = Field(default=None, max_length=240)
    macro: str | None = Field(default=None, max_length=120000)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _get_gateway(app: FastAPI):
    gw = getattr(app.state, "gateway", None)
    if gw is None:
        from app.config import load_config
        from app.gateway import GatewayClient

        gw = GatewayClient.from_config(load_config())
        app.state.gateway = gw
    return gw


def _get_execute(app: FastAPI):
    execute = getattr(app.state, "execute", None)
    if execute is None:
        execute = default_execute
        app.state.execute = execute
    return execute


def _get_freecad_execute(app: FastAPI):
    execute = getattr(app.state, "freecad_execute", None)
    if execute is None:
        execute = default_freecad_execute
        app.state.freecad_execute = execute
    return execute


def _get_session_store(app: FastAPI) -> SessionStore:
    store = getattr(app.state, "session_store", None)
    if store is None:
        store = SqliteSessionStore()
        app.state.session_store = store
    return store


def _bearer_guard_exempt(request: Request) -> bool:
    client = request.client
    return client is None or client.host in BEARER_GUARD_EXEMPT_HOSTS


def _bearer_guard_token_from_header(request: Request) -> str | None:
    auth_header = request.headers.get("authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[len("Bearer "):].strip()
    return token or None


def _get_artifact_store(app: FastAPI) -> ArtifactStore:
    store = getattr(app.state, "artifact_store", None)
    if store is None:
        store = FileArtifactStore()
        app.state.artifact_store = store
    return store


def _get_freecad_gui_orchestrator(app: FastAPI) -> FreeCadGuiSessionOrchestrator:
    orchestrator = getattr(app.state, "freecad_gui_orchestrator", None)
    if orchestrator is None:
        orchestrator = freecad_gui_orchestrator_from_env()
        app.state.freecad_gui_orchestrator = orchestrator
    return orchestrator


def _metadata_with_artifact_refs(
    metadata: dict,
    artifact_refs: dict[str, dict],
) -> dict:
    next_metadata = dict(metadata or {})
    if artifact_refs:
        next_metadata["artifact_refs"] = artifact_refs
    return next_metadata


def _metadata_with_freecad_diagnostics(metadata: dict, result: ExecResult) -> dict:
    next_metadata = dict(metadata or {})
    diagnostics = dict(result.diagnostics or {})
    if diagnostics:
        next_metadata["freecad_diagnostics"] = diagnostics
        if diagnostics.get("techdraw_pdf_status") is not None:
            next_metadata["techdraw_pdf_status"] = diagnostics["techdraw_pdf_status"]
        if diagnostics.get("techdraw_export_status") is not None:
            next_metadata["techdraw_export_status"] = diagnostics["techdraw_export_status"]
    return next_metadata


def _freecad_exec_result_from_sandbox(res, fallback_error: str) -> ExecResult:
    if not res.success or not isinstance(res.result, dict):
        return ExecResult(
            ok=False,
            engine="freecad",
            error=res.error or fallback_error,
        )

    r = res.result
    return ExecResult(
        ok=bool(r.get("ok")),
        preview_png_b64=r.get("preview_png_b64"),
        exports=r.get("exports") or {},
        error=r.get("error"),
        engine="freecad",
        freecad_version=r.get("freecad_version"),
        diagnostics={
            key: r.get(key)
            for key in ["techdraw_pdf_status", "techdraw_export_status", "freecad_exit_code"]
            if r.get(key) is not None
        },
    )


def _freecad_inspection_from_sandbox(res, fallback_error: str) -> dict:
    if not res.success or not isinstance(res.result, dict):
        return {
            "ok": False,
            "engine": "freecad",
            "error": res.error or fallback_error,
            "document_summary": None,
            "freecad_version": None,
        }

    r = res.result
    return {
        "ok": bool(r.get("ok")),
        "engine": "freecad",
        "error": r.get("error"),
        "document_summary": r.get("document_summary"),
        "freecad_version": r.get("freecad_version"),
    }


def _freecad_document_state() -> dict:
    state = default_design_state().model_dump()
    state["engine"] = "freecad"
    state["document_state"] = "fcstd_artifact"
    return state


def _freecad_import_marker_script(import_format: str, filename: str | None) -> str:
    source = filename or f"uploaded.{import_format}"
    return (
        f"# Imported {import_format.upper()} model from {source}.\n"
        "# The FCStd artifact is the authoritative mutable FreeCAD document state.\n"
        "doc = FreeCAD.ActiveDocument\n"
        "result = [obj for obj in doc.Objects if hasattr(obj, 'Shape')]\n"
    )


def _freecad_patch_marker_script(patches: list[dict]) -> str:
    return (
        "# Applied structured FreeCAD document patches.\n"
        "# The FCStd artifact is the authoritative mutable FreeCAD document state.\n"
        f"# Patch count: {len(patches)}\n"
        "doc = FreeCAD.ActiveDocument\n"
        "result = [obj for obj in doc.Objects if hasattr(obj, 'Shape')]\n"
    )


def _artifact_b64(artifact_store: ArtifactStore, session_id: str, version_id: str, name: str) -> str:
    artifact = artifact_store.get_artifact(
        session_id=session_id,
        version_id=version_id,
        artifact_name=name,
    )
    if artifact is None:
        raise HTTPException(status_code=422, detail=f"source version has no {name} artifact")
    return base64.b64encode(artifact.path.read_bytes()).decode("ascii")


def _optional_artifact_b64(
    artifact_store: ArtifactStore,
    session_id: str,
    version_id: str | None,
    name: str,
) -> str | None:
    if not version_id:
        return None
    artifact = artifact_store.get_artifact(
        session_id=session_id,
        version_id=version_id,
        artifact_name=name,
    )
    if artifact is None:
        return None
    return base64.b64encode(artifact.path.read_bytes()).decode("ascii")


def _resolve_fcstd_b64(
    store: SessionStore,
    artifact_store: ArtifactStore,
    *,
    fcstd_b64: str | None,
    session_id: str | None,
    version_id: str | None,
) -> tuple[str, object | None, str | None]:
    source_version = None
    resolved_version_id = version_id

    if fcstd_b64 is not None:
        _enforce_freecad_upload_size(fcstd_b64, label="FCStd document")
        if session_id and version_id:
            source_version = store.get_version(session_id, version_id)
        return fcstd_b64, source_version, resolved_version_id

    if not session_id:
        raise HTTPException(
            status_code=422,
            detail="fcstd_b64 or session_id is required",
        )

    if resolved_version_id is None:
        session = store.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        resolved_version_id = session["session"]["active_version_id"]
    if not resolved_version_id:
        raise HTTPException(status_code=422, detail="session has no active version")

    source_version = store.get_version(session_id, resolved_version_id)
    if source_version is None:
        raise KeyError(resolved_version_id)
    return (
        _artifact_b64(artifact_store, session_id, resolved_version_id, "fcstd"),
        source_version,
        resolved_version_id,
    )


async def _inspect_fcstd_b64(fcstd_b64: str | None) -> dict:
    if not fcstd_b64:
        return {
            "ok": False,
            "engine": "freecad",
            "error": "missing FCStd artifact for inspection",
            "document_summary": None,
            "freecad_version": None,
        }
    res = await asyncio.to_thread(
        run_freecad_document_inspect_sandboxed,
        fcstd_b64,
        timeout_s=FREECAD_SANDBOX_TIMEOUT_S,
        cpu_seconds=FREECAD_SANDBOX_CPU_SECONDS,
        address_space_mb=FREECAD_SANDBOX_ADDRESS_SPACE_MB,
    )
    return _freecad_inspection_from_sandbox(res, "FreeCAD document inspection failed")


def _metadata_with_document_summary(metadata: dict, inspection: dict) -> dict:
    next_metadata = dict(metadata or {})
    if inspection.get("ok") and inspection.get("document_summary"):
        next_metadata["document_summary"] = inspection["document_summary"]
        next_metadata["document_summary_schema"] = 6
        next_metadata.pop("document_summary_error", None)
    elif inspection.get("error"):
        next_metadata["document_summary_error"] = inspection["error"]
    return next_metadata


def _metadata_with_typed_state_diff(
    metadata: dict,
    before_summary: dict | None,
    inspection: dict,
) -> dict:
    next_metadata = dict(metadata or {})
    after_summary = inspection.get("document_summary") if inspection.get("ok") else None
    if before_summary and after_summary:
        next_metadata["document_state_diff"] = typed_state_diff(before_summary, after_summary)
    return next_metadata


def _freecad_geometry_metadata(
    *,
    exports: dict[str, str],
    inspection: dict,
    extra: dict | None = None,
) -> dict:
    summary = dict(extra or {})
    summary.update(
        {
            "engine": "freecad",
            "exports": sorted(exports),
        }
    )
    if inspection.get("ok") and inspection.get("document_summary"):
        summary["document_geometry"] = inspection["document_summary"].get("geometry")
    elif inspection.get("error"):
        summary["document_summary_error"] = inspection["error"]
    return summary


def _freecad_response(
    result: ExecResult,
    *,
    session_id: str | None = None,
    version=None,
) -> dict:
    metadata = version.metadata if version else {}
    return {
        "ok": result.ok,
        "session_id": session_id,
        "version": version.__dict__ if version else None,
        "engine": "freecad",
        "freecad_version": result.freecad_version,
        "parameters": extract_script_parameters(version.script) if version else [],
        "preview_png_b64": result.preview_png_b64,
        "exports": result.exports,
        "artifact_refs": (metadata or {}).get("artifact_refs") if version else {},
        "diagnostics": result.diagnostics,
        "techdraw_pdf_status": result.diagnostics.get("techdraw_pdf_status"),
        "techdraw_export_status": result.diagnostics.get("techdraw_export_status"),
        "document_summary": (metadata or {}).get("document_summary") if version else None,
        "document_summary_error": (metadata or {}).get("document_summary_error")
        if version
        else None,
        "error": result.error,
    }


def _remote_session_dict(remote_session) -> dict[str, Any]:
    return {
        "id": remote_session.id,
        "session_id": remote_session.id,
        "workbench_session_id": remote_session.workbench_session_id,
        "base_version_id": remote_session.base_version_id,
        "current_version_id": remote_session.current_version_id,
        "status": remote_session.status,
        "remote_url": remote_session.remote_url,
        "bridge_status": remote_session.bridge_status,
        "created_at": remote_session.created_at,
        "started_at": remote_session.started_at,
        "last_active_at": remote_session.last_active_at,
        "stopped_at": remote_session.stopped_at,
        "metadata": remote_session.metadata,
    }


def _remote_command_dict(command) -> dict[str, Any]:
    return {
        "id": command.id,
        "command_id": command.id,
        "remote_session_id": command.remote_session_id,
        "session_id": command.remote_session_id,
        "op": command.op,
        "input": command.input,
        "base_version_id": command.base_version_id,
        "status": command.status,
        "result": command.result,
        "error": command.error,
        "created_at": command.created_at,
        "dispatched_at": command.dispatched_at,
        "completed_at": command.completed_at,
        "metadata": command.metadata,
    }


def _remote_desktop_base_url() -> str | None:
    raw = (
        os.environ.get("CAD_REMOTE_DESKTOP_BASE_URL")
        or os.environ.get("FOURYI_CAD_REMOTE_DESKTOP_BASE_URL")
        or ""
    ).strip()
    return raw or None


def _session_orchestrator_url() -> str | None:
    raw = (
        os.environ.get("CAD_SESSION_ORCHESTRATOR_URL")
        or os.environ.get("FOURYI_CAD_SESSION_ORCHESTRATOR_URL")
        or ""
    ).strip()
    return raw or None


def _freecad_gui_backend() -> str:
    return os.environ.get("CAD_GUI_SESSION_BACKEND", "disabled").strip().lower() or "disabled"


def _shared_freecad_service_enabled() -> bool:
    return _freecad_gui_backend() in {"shared_service", "fixed_service", "static_service"}


def _shared_freecad_session_id() -> str:
    return (
        os.environ.get("CAD_SHARED_FREECAD_SESSION_ID")
        or os.environ.get("CAD_FIXED_FREECAD_SESSION_ID")
        or "shared-freecad-gui"
    ).strip()


def _remote_desktop_url_for(remote_session_id: str) -> str | None:
    base_url = _remote_desktop_base_url()
    if not base_url:
        return None
    if "{session_id}" in base_url:
        return base_url.format(session_id=remote_session_id)
    if _shared_freecad_service_enabled():
        return base_url
    return f"{base_url.rstrip('/')}/{remote_session_id}"


def _freecad_gui_proxy_prefix() -> str:
    raw = (
        os.environ.get("CAD_FREECAD_GUI_PROXY_PREFIX")
        or os.environ.get("FOURYI_CAD_FREECAD_GUI_PROXY_PREFIX")
        or FREECAD_GUI_PROXY_DEFAULT_PREFIX
    ).strip()
    prefix = f"/{raw.strip('/')}"
    return prefix if prefix != "/" else FREECAD_GUI_PROXY_DEFAULT_PREFIX


def _freecad_gui_upstream_url() -> str | None:
    raw = (
        os.environ.get("CAD_FREECAD_GUI_UPSTREAM_URL")
        or os.environ.get("FOURYI_CAD_FREECAD_GUI_UPSTREAM_URL")
        or ""
    ).strip().rstrip("/")
    if not raw:
        return None
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return raw


def _remote_desktop_uses_freecad_gui_proxy() -> bool:
    base_url = _remote_desktop_base_url() or ""
    prefix = _freecad_gui_proxy_prefix()
    return (
        base_url == prefix
        or base_url.startswith(f"{prefix}/")
        or base_url.startswith(f"{prefix}?")
    )


def _freecad_gui_proxy_target_url(
    path: str,
    query_string: bytes = b"",
    *,
    websocket: bool = False,
) -> str | None:
    upstream = _freecad_gui_upstream_url()
    if not upstream:
        return None
    parts = urllib.parse.urlsplit(upstream)
    scheme = parts.scheme
    if websocket:
        scheme = "wss" if scheme == "https" else "ws"
    relative_path = urllib.parse.quote(
        path.lstrip("/"),
        safe="/._~!$&'()*+,;=:@%",
    )
    upstream_path = parts.path.rstrip("/")
    target_path = (
        f"{upstream_path}/{relative_path}"
        if relative_path
        else (upstream_path or "/")
    )
    request_query = query_string.decode("latin-1") if query_string else ""
    target_query = "&".join(item for item in [parts.query, request_query] if item)
    return urllib.parse.urlunsplit((scheme, parts.netloc, target_path, target_query, ""))


def _freecad_gui_proxy_request_headers(headers) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
    }


def _freecad_gui_proxy_response_headers(headers) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
        and key.lower() not in {"content-encoding", "content-length"}
    }


def _remote_session_config_metadata(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    gui_backend = _freecad_gui_backend()
    shared_service = _shared_freecad_service_enabled()
    config_metadata = {
        **dict(metadata or {}),
        "mode": "freecad_gui",
        "orchestrator_configured": bool(_session_orchestrator_url())
        or gui_backend == "local_docker",
        "gui_session_backend": gui_backend,
        "shared_service_configured": shared_service,
        "remote_desktop_configured": bool(_remote_desktop_base_url()),
    }
    if shared_service:
        config_metadata["shared_remote_session_id"] = _shared_freecad_session_id()
        config_metadata["load_model_required"] = True
        config_metadata["freecad_gui_proxy_configured"] = bool(_freecad_gui_upstream_url())
    return config_metadata


def _truthy_env(*names: str) -> bool:
    for name in names:
        if os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _freecad_first_entry_enabled() -> bool:
    return _truthy_env("CAD_FREECAD_FIRST_ENTRY", "FOURYI_CAD_FREECAD_FIRST_ENTRY")


def _freecad_first_entry_url() -> str | None:
    raw = (
        os.environ.get("CAD_FREECAD_FIRST_ENTRY_URL")
        or os.environ.get("FOURYI_CAD_FREECAD_FIRST_ENTRY_URL")
        or ""
    ).strip()
    if raw:
        return raw
    remote_url = _remote_desktop_base_url()
    if remote_url:
        return remote_url
    if not _freecad_gui_upstream_url():
        return None
    proxy_prefix = _freecad_gui_proxy_prefix()
    websockify_path = f"{proxy_prefix.strip('/')}/websockify"
    return f"{proxy_prefix}/vnc.html?autoconnect=1&resize=remote&path={websockify_path}"


def _configured_env(*names: str) -> bool:
    return any(bool(os.environ.get(name, "").strip()) for name in names)


def _release_check(
    key: str,
    status: Literal["pass", "warn", "fail"],
    message: str,
    *,
    required_for: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "status": status,
        "message": message,
        "required_for": required_for or [],
        "details": details or {},
    }


def _release_target_ready(checks: list[dict[str, Any]], target: str) -> bool:
    return all(
        check.get("status") == "pass"
        for check in checks
        if target in set(check.get("required_for") or [])
    )


def _production_readiness_report(app: FastAPI) -> dict[str, Any]:
    store = _get_session_store(app)
    artifact_store = _get_artifact_store(app)
    storage = storage_status(
        str(getattr(store, "db_path", "custom-session-store")),
        str(getattr(artifact_store, "root", "custom-artifact-store")),
    )
    durable = all(item.get("durable_configured") for item in storage.values())
    writable = all(item.get("writable") for item in storage.values())

    gateway_base_configured = _configured_env("OPENAI_BASE_URL", "OPENAI_API_BASE")
    gateway_base = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or ""
    gateway_uses_platform = gateway_base_configured and "api.openai.com" not in gateway_base.lower()
    gateway_configured = bool(
        gateway_base_configured
        and _configured_env("OPENAI_API_KEY")
        and _configured_env("TEXT_MODEL")
        and gateway_uses_platform
    )

    worker_endpoint = os.environ.get("FOURYI_FREECAD_WORKER_URL") or os.environ.get("FREECAD_WORKER_URL")
    worker_split = bool(worker_endpoint)
    security_controls = {
        "egress_blocked": _truthy_env("FOURYI_FREECAD_WORKER_EGRESS_BLOCKED"),
        "read_only_rootfs": _truthy_env("FOURYI_FREECAD_WORKER_READ_ONLY_ROOTFS"),
        "seccomp_profile": _configured_env("FOURYI_FREECAD_WORKER_SECCOMP_PROFILE"),
        "tmpfs_workspace": _truthy_env("FOURYI_FREECAD_WORKER_TMPFS"),
    }
    hardened_worker = bool(worker_split and all(security_controls.values()))

    gui_backend = _freecad_gui_backend()
    gui_orchestrator_configured = bool(_session_orchestrator_url()) or gui_backend == "local_docker"
    gui_shared_service_configured = _shared_freecad_service_enabled() and bool(
        _shared_freecad_session_id()
    )
    gui_runtime_configured = gui_orchestrator_configured or gui_shared_service_configured
    gui_control_plane_configured = _configured_env("CAD_GUI_SESSION_CONTROL_PLANE_URL")
    remote_desktop_configured = bool(_remote_desktop_base_url()) or gui_backend == "local_docker"
    gui_proxy_required = (
        _shared_freecad_service_enabled()
        and _remote_desktop_uses_freecad_gui_proxy()
    )
    gui_proxy_configured = bool(_freecad_gui_upstream_url())
    gui_ready = bool(
        gui_runtime_configured
        and gui_control_plane_configured
        and remote_desktop_configured
        and (not gui_proxy_required or gui_proxy_configured)
    )

    upload_max_bytes = _freecad_upload_max_bytes()
    upload_policy_ready = upload_max_bytes >= DEFAULT_FREECAD_UPLOAD_MAX_BYTES
    license_review_accepted = _truthy_env(
        "FOURYI_CAD_LICENSE_REVIEW_ACCEPTED",
        "CAD_LICENSE_REVIEW_ACCEPTED",
    )

    checks = [
        _release_check(
            "healthz_contract",
            "pass",
            "/healthz is config-independent and returns a fast liveness response.",
            required_for=["private_beta", "public_beta", "ga"],
        ),
        _release_check(
            "gateway_contract",
            "pass" if gateway_configured else "fail",
            "Gateway env is configured for the platform OpenAI-compatible endpoint."
            if gateway_configured
            else "OPENAI_BASE_URL/OPENAI_API_BASE, OPENAI_API_KEY, and TEXT_MODEL must be injected; api.openai.com is not allowed.",
            required_for=["private_beta", "public_beta", "ga"],
            details={
                "base_url_configured": gateway_base_configured,
                "api_key_configured": _configured_env("OPENAI_API_KEY"),
                "text_model_configured": _configured_env("TEXT_MODEL"),
                "platform_endpoint": bool(gateway_uses_platform),
            },
        ),
        _release_check(
            "storage_writable",
            "pass" if writable else "fail",
            "Session DB and artifact root are writable."
            if writable
            else "Session DB and artifact root must be writable by the app user.",
            required_for=["private_beta", "public_beta", "ga"],
        ),
        _release_check(
            "durable_storage",
            "pass" if durable else "fail",
            "Session DB and artifacts are outside tmp-backed fallback storage."
            if durable
            else "CAD_DATA_DIR or explicit storage paths must be backed by durable storage before Public Beta/GA.",
            required_for=["public_beta", "ga"],
            details={"cad_data_dir_configured": _configured_env("CAD_DATA_DIR")},
        ),
        _release_check(
            "freecad_upload_policy",
            "pass" if upload_policy_ready else "fail",
            "FreeCAD upload cap supports the 100 MB Private Beta default."
            if upload_policy_ready
            else "CAD_FREECAD_UPLOAD_MAX_BYTES is below the 100 MB Private Beta default.",
            required_for=["private_beta", "public_beta", "ga"],
            details={
                "max_bytes": upload_max_bytes,
                "formats": list(FREECAD_IMPORT_FORMATS),
            },
        ),
        _release_check(
            "freecad_smoke_endpoint",
            "pass",
            "/api/freecad/smoke is available for built-image FreeCADCmd verification.",
            required_for=["private_beta", "public_beta", "ga"],
            details={
                "endpoint": "/api/freecad/smoke",
                "manual_container_smoke_required": True,
            },
        ),
        _release_check(
            "remote_gui_bridge",
            "pass" if gui_ready else "fail",
            "Remote GUI runtime, control-plane URL, and desktop routing are configured."
            if gui_ready
            else (
                "Remote FreeCAD GUI handoff needs a runtime backend, control-plane URL, "
                "desktop routing, and proxy upstream when using the fixed internal desktop."
            ),
            required_for=["public_beta", "ga"],
            details={
                "gui_session_backend": gui_backend,
                "orchestrator_configured": gui_orchestrator_configured,
                "shared_service_configured": gui_shared_service_configured,
                "shared_remote_session_id": _shared_freecad_session_id()
                if _shared_freecad_service_enabled()
                else None,
                "control_plane_url_configured": gui_control_plane_configured,
                "remote_desktop_configured": remote_desktop_configured,
                "freecad_gui_proxy_required": gui_proxy_required,
                "freecad_gui_proxy_configured": gui_proxy_configured,
            },
        ),
        _release_check(
            "bridge_observability",
            "pass",
            "Remote session events, bridge heartbeat, command queue, and command results are persisted.",
            required_for=["private_beta", "public_beta", "ga"],
            details={
                "context_endpoint": "/api/freecad/sessions/{id}/bridge/context",
                "command_queue": "/api/freecad/sessions/{id}/commands",
            },
        ),
        _release_check(
            "worker_isolation",
            "pass" if hardened_worker else "fail",
            "Split FreeCAD worker and runtime isolation controls are configured."
            if hardened_worker
            else "GA requires a split FreeCAD worker with egress block, read-only rootfs, seccomp, and tmpfs workspace.",
            required_for=["ga"],
            details={
                "split_service_configured": worker_split,
                "security_controls": security_controls,
            },
        ),
        _release_check(
            "license_gate",
            "pass" if license_review_accepted else "fail",
            "FreeCAD/GPL and any ported-code license review has been accepted."
            if license_review_accepted
            else "Public release requires explicit license review acceptance.",
            required_for=["public_beta", "ga"],
        ),
    ]
    checks.extend(
        build_ai_quality_checks(
            load_latest_eval_report(),
            report_path=str(default_report_path()),
        )
    )
    summary = {
        "pass": sum(1 for check in checks if check["status"] == "pass"),
        "warn": sum(1 for check in checks if check["status"] == "warn"),
        "fail": sum(1 for check in checks if check["status"] == "fail"),
        "blockers": [
            check["key"]
            for check in checks
            if check["status"] == "fail" and check.get("required_for")
        ],
    }
    release_targets = {
        "private_beta_ready": _release_target_ready(checks, "private_beta"),
        "public_beta_ready": _release_target_ready(checks, "public_beta"),
        "ga_ready": _release_target_ready(checks, "ga"),
    }
    return {
        "schema": "4yi-cad.production_readiness.v1",
        "ok": bool(writable),
        "phase": "phase6",
        "generated_at": utc_now(),
        "summary": summary,
        "release_targets": release_targets,
        "production_ready": release_targets["ga_ready"],
        "durable_storage_configured": bool(durable),
        "storage": storage,
        "runtime": {
            "gateway_configured": gateway_configured,
            "gateway_base_url_configured": gateway_base_configured,
            "gateway_platform_endpoint": bool(gateway_uses_platform),
            "openai_api_key_configured": _configured_env("OPENAI_API_KEY"),
            "text_model_configured": _configured_env("TEXT_MODEL"),
            "port": os.environ.get("PORT", "8080"),
        },
        "freecad_worker": {
            "mode": os.environ.get("FOURYI_CAD_WORKER_MODE", "single_container_subprocess"),
            "split_service_configured": worker_split,
            "endpoint_configured": worker_split,
            "hardened_worker_service": hardened_worker,
            "security_controls": security_controls,
            "risk": None if hardened_worker else "FreeCAD still runs in the app container or lacks required runtime isolation controls",
        },
        "remote_gui": {
            "gui_session_backend": gui_backend,
            "orchestrator_configured": gui_orchestrator_configured,
            "shared_service_configured": gui_shared_service_configured,
            "shared_remote_session_id": _shared_freecad_session_id()
            if _shared_freecad_service_enabled()
            else None,
            "control_plane_url_configured": gui_control_plane_configured,
            "remote_desktop_configured": remote_desktop_configured,
            "freecad_gui_proxy_required": gui_proxy_required,
            "freecad_gui_proxy_configured": gui_proxy_configured,
            "freecad_gui_proxy_prefix": _freecad_gui_proxy_prefix(),
            "ready": gui_ready,
        },
        "entrypoint": {
            "freecad_first_enabled": _freecad_first_entry_enabled(),
            "freecad_first_url": _freecad_first_entry_url(),
            "web_workbench_url": "/workbench",
        },
        "license": {
            "review_accepted": license_review_accepted,
        },
        "checks": checks,
    }


def _remote_session_workbench_id(req: FreeCadRemoteSessionRequest) -> str:
    workbench_session_id = req.workbench_session_id or req.session_id
    if not workbench_session_id:
        raise HTTPException(status_code=422, detail="session_id is required")
    return workbench_session_id


def _remote_session_conflict_detail(
    *,
    active_version_id: str | None,
    expected_version_id: str | None,
) -> dict[str, Any]:
    return {
        "code": "cad_session_revision_conflict",
        "message": "remote FreeCAD session is based on an older workbench version",
        "active_version_id": active_version_id,
        "expected_version_id": expected_version_id,
    }


def _remote_bridge_metadata(
    existing_metadata: dict[str, Any],
    req: FreeCadBridgeHeartbeatRequest,
    *,
    event: str,
) -> dict[str, Any]:
    bridge = dict((existing_metadata or {}).get("bridge") or {})
    if req.bridge_id is not None:
        bridge["bridge_id"] = req.bridge_id
    if req.freecad_version is not None:
        bridge["freecad_version"] = req.freecad_version
    if req.document_name is not None:
        bridge["document_name"] = req.document_name
    if req.active_document_path is not None:
        bridge["active_document_path"] = req.active_document_path
    if req.workbench is not None:
        bridge["workbench"] = req.workbench
    if req.selection:
        bridge["selection"] = dict(req.selection)
    if req.document_tree:
        bridge["document_tree"] = dict(req.document_tree)
    if req.console_tail:
        bridge["console_tail"] = list(req.console_tail[-80:])
    if req.capabilities:
        bridge["capabilities"] = list(req.capabilities)
    if req.metadata:
        bridge["metadata"] = dict(req.metadata)
    bridge["last_event"] = event
    bridge["last_seen_at"] = utc_now()
    return {
        **dict(existing_metadata or {}),
        "bridge": bridge,
    }


def _ensure_shared_remote_freecad_session(
    store: SessionStore,
    remote_session_id: str,
):
    remote_session = store.get_remote_freecad_session(remote_session_id)
    if remote_session is not None:
        return remote_session
    if (
        _shared_freecad_service_enabled()
        and remote_session_id == _shared_freecad_session_id()
    ):
        workbench_session = store.create_session(title="FreeCAD GUI session")
        remote_url = _remote_desktop_url_for(remote_session_id)
        remote_session, reused = store.create_or_reuse_remote_freecad_session(
            remote_session_id=remote_session_id,
            workbench_session_id=workbench_session.id,
            base_version_id=None,
            reuse=True,
            remote_url=remote_url,
            status="ready" if remote_url else "starting",
            bridge_status="pending",
            metadata=_remote_session_config_metadata(
                {
                    "source": "shared_freecad_service_autocreate",
                    "auto_created": True,
                    "workbench_session_id": workbench_session.id,
                }
            ),
        )
        store.add_remote_freecad_session_event(
            remote_session_id=remote_session.id,
            event_type="session_auto_created",
            metadata={
                "workbench_session_id": workbench_session.id,
                "remote_url_configured": bool(remote_url),
                "reused": reused,
                "source": "shared_freecad_service",
            },
        )
        return remote_session

    if _LOCAL_SESSION_ID_RE.match(remote_session_id):
        # Native local FreeCAD addon (Plugin V2 P2): auto-register on first
        # bridge contact, independent of the shared/GUI backend config.
        workbench_session = store.create_session(
            title=f"Local FreeCAD session {remote_session_id}"
        )
        remote_session, reused = store.create_or_reuse_remote_freecad_session(
            remote_session_id=remote_session_id,
            workbench_session_id=workbench_session.id,
            base_version_id=None,
            reuse=True,
            remote_url=None,
            status="ready",
            bridge_status="pending",
            metadata={
                "source": "local_addon_autocreate",
                "auto_created": True,
                "workbench_session_id": workbench_session.id,
            },
        )
        store.add_remote_freecad_session_event(
            remote_session_id=remote_session.id,
            event_type="session_auto_created",
            metadata={
                "workbench_session_id": workbench_session.id,
                "remote_url_configured": False,
                "reused": reused,
                "source": "local_addon",
            },
        )
        return remote_session

    return None


def _compact_json_for_prompt(value: Any, *, max_chars: int = 4000) -> str:
    if not value:
        return "{}"
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "...[truncated]"


def _freecad_panel_agent_prompt(req: FreeCadPanelActionRequest) -> str:
    metadata = req.metadata if isinstance(req.metadata, dict) else {}
    document_tree = metadata.get("document_tree") if isinstance(metadata, dict) else None
    return "\n".join(
        [
            "Use FreeCAD and call run_freecad with a complete script.",
            "Create or update the remote FreeCAD document, and make sure the result exports an FCStd artifact.",
            "User request:",
            (req.prompt or "").strip(),
            "",
            "Current FreeCAD selection JSON:",
            _compact_json_for_prompt(req.selection, max_chars=2500),
            "",
            "Current FreeCAD document tree JSON:",
            _compact_json_for_prompt(document_tree, max_chars=5000),
        ]
    )


async def _collect_freecad_panel_generation(app: FastAPI, req: FreeCadPanelActionRequest) -> dict[str, Any]:
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")

    gw = _get_gateway(app)
    execute = _get_execute(app)
    freecad_execute = _get_freecad_execute(app)
    events: list[dict[str, Any]] = []
    exports: dict[str, str] = {}
    script = ""
    parameters: list[dict[str, Any]] = []
    preview_png_b64: str | None = None
    freecad_version: str | None = None
    diagnostics: dict[str, Any] = {}
    error_message: str | None = None
    ok = False

    async for event in run_generation(
        _freecad_panel_agent_prompt(req),
        gateway=gw,
        execute=execute,
        execute_freecad=freecad_execute,
        engine_hint="freecad",
        history=[],
    ):
        events.append(event)
        event_type = event.get("type")
        if event_type == "script":
            script = event.get("script") or script
            parameters = event.get("parameters") or parameters
        elif event_type == "preview":
            preview_png_b64 = event.get("png_b64") or preview_png_b64
            freecad_version = event.get("freecad_version") or freecad_version
        elif event_type == "artifact":
            artifact_format = str(event.get("format") or "").lower()
            data_b64 = event.get("data_b64")
            if artifact_format and isinstance(data_b64, str) and data_b64:
                exports[artifact_format] = data_b64
            freecad_version = event.get("freecad_version") or freecad_version
        elif event_type == "error":
            error_message = event.get("message") or error_message
        elif event_type == "done":
            ok = bool(event.get("ok"))
            freecad_version = event.get("freecad_version") or freecad_version
            diagnostics = event.get("diagnostics") or diagnostics

    if not ok:
        raise HTTPException(
            status_code=502,
            detail=error_message or "FreeCAD panel generation failed",
        )
    if not exports.get("fcstd"):
        raise HTTPException(
            status_code=502,
            detail="FreeCAD panel generation did not produce an FCStd artifact",
        )
    return {
        "script": script,
        "parameters": parameters,
        "preview_png_b64": preview_png_b64,
        "exports": exports,
        "freecad_version": freecad_version,
        "diagnostics": diagnostics,
        "events": [
            {key: value for key, value in event.items() if key not in {"script", "data_b64", "png_b64"}}
            for event in events
        ],
    }


async def _queue_freecad_panel_agent_generation(
    app: FastAPI,
    *,
    store: SessionStore,
    artifact_store: ArtifactStore,
    remote_session,
    remote_session_id: str,
    req: FreeCadPanelActionRequest,
):
    workbench_session = store.get_session(remote_session.workbench_session_id)
    if workbench_session is None:
        raise KeyError(remote_session.workbench_session_id)
    active_version_id = workbench_session["session"]["active_version_id"]
    generation = await _collect_freecad_panel_generation(app, req)
    exports = generation["exports"]
    inspection = await _inspect_fcstd_b64(exports.get("fcstd"))
    result = ExecResult(
        ok=True,
        preview_png_b64=generation["preview_png_b64"],
        exports=exports,
        engine="freecad",
        freecad_version=generation["freecad_version"],
        diagnostics=generation["diagnostics"],
    )
    metadata = _remote_session_config_metadata(
        {
            "preview_mode": "generated",
            "engine": "freecad",
            "freecad_version": generation["freecad_version"],
            "document_state": "fcstd_artifact",
            "source": "freecad_panel_agent",
            "remote_session_id": remote_session_id,
            "source_version_id": active_version_id,
            "generated_parameters": generation["parameters"],
            "panel_selection": req.selection,
            "panel_generation_events": generation["events"],
        }
    )
    metadata = _metadata_with_freecad_diagnostics(metadata, result)
    metadata = _metadata_with_document_summary(metadata, inspection)
    version = store.add_version(
        session_id=remote_session.workbench_session_id,
        intent="modify" if active_version_id else "create",
        user_instruction=req.prompt or "Generate from FreeCAD panel",
        design_state=_freecad_document_state(),
        script=generation["script"],
        geometry_summary=_freecad_geometry_metadata(
            exports=exports,
            inspection=inspection,
            extra={
                "source": "freecad_panel_agent",
                "remote_session_id": remote_session_id,
                "source_version_id": active_version_id,
            },
        ),
        patch={
            "op": "freecad_panel_agent_generate",
            "remote_session_id": remote_session_id,
            "source_version_id": active_version_id,
        },
        metadata=metadata,
        status="ok",
    )
    artifact_refs = artifact_store.save_version_artifacts(
        session_id=remote_session.workbench_session_id,
        version_id=version.id,
        preview_png_b64=generation["preview_png_b64"],
        exports=exports,
    )
    if artifact_refs:
        version = store.update_version_metadata(
            session_id=remote_session.workbench_session_id,
            version_id=version.id,
            metadata=_metadata_with_artifact_refs(version.metadata, artifact_refs),
        )
    fcstd_ref = artifact_refs.get("fcstd") or {}
    fcstd_url = fcstd_ref.get("url") or (
        f"/api/sessions/{remote_session.workbench_session_id}/versions/{version.id}/artifacts/fcstd"
    )
    command = store.create_remote_freecad_session_command(
        remote_session_id=remote_session_id,
        op="load_model",
        input={
            "fcstd_url": fcstd_url,
            "filename": f"4yi-cad-v{version.version_number}.FCStd",
            "version_id": version.id,
            "workbench_session_id": remote_session.workbench_session_id,
            "instruction": req.prompt,
            "source": "freecad_panel_agent",
            "close_existing": True,
            "recompute": True,
        },
        base_version_id=remote_session.current_version_id,
        metadata={"source": "freecad_panel_agent", "panel_action": req.action},
    )
    event = store.add_remote_freecad_session_event(
        remote_session_id=remote_session_id,
        event_type="panel_agent_generation_completed",
        metadata={
            "version_id": version.id,
            "command_id": command.id,
            "artifact_refs": artifact_refs,
            "freecad_version": generation["freecad_version"],
        },
    )
    return version, command, event


def _site_layout_audit_diagnostics(
    audit: dict | None,
    *,
    repair_status: str,
    before: dict | None = None,
) -> dict:
    audit = audit if isinstance(audit, dict) else {}
    diagnostics = {
        "status": audit.get("status"),
        "coverage_score": audit.get("coverage_score"),
        "issue_count": len(list(audit.get("issues") or [])),
        "repair_status": repair_status,
        "audit": audit,
    }
    if before and before is not audit:
        diagnostics["before"] = before
        diagnostics["after"] = audit
    return diagnostics


async def default_execute(script: str) -> ExecResult:
    """Production executor: run the CAD worker in the sandbox, off the event loop."""
    from app.cad.runner import run_sandboxed

    res = await asyncio.to_thread(
        run_sandboxed,
        {"script": script},
        timeout_s=120,
        cpu_seconds=100,
        address_space_mb=4096,
    )
    if not res.success or not isinstance(res.result, dict):
        return ExecResult(ok=False, error=res.error or "sandbox execution failed")

    r = res.result
    return ExecResult(
        ok=bool(r.get("ok")),
        preview_png_b64=r.get("preview_png_b64"),
        exports=r.get("exports") or {},
        error=r.get("error"),
        engine="cadquery",
    )


async def default_freecad_execute(script: str) -> ExecResult:
    """Production FreeCAD executor: run FreeCADCmd through the sandbox worker."""
    res = await asyncio.to_thread(
        run_freecad_sandboxed,
        script,
        timeout_s=FREECAD_SANDBOX_TIMEOUT_S,
        cpu_seconds=FREECAD_SANDBOX_CPU_SECONDS,
        address_space_mb=FREECAD_SANDBOX_ADDRESS_SPACE_MB,
    )
    result = _freecad_exec_result_from_sandbox(res, "FreeCAD sandbox execution failed")
    if not result.ok:
        return result

    inspection = await _inspect_fcstd_b64(result.exports.get("fcstd"))
    document_summary = inspection.get("document_summary") if inspection.get("ok") else None
    audit = site_layout_audit_from_summary(document_summary)
    needs_repair = site_layout_needs_repair(document_summary)
    if audit:
        result.diagnostics["site_layout_audit"] = _site_layout_audit_diagnostics(
            audit,
            repair_status="pending" if needs_repair else "not_needed",
        )
    if not needs_repair:
        return result

    repair_script = site_layout_repair_script(audit)
    repair_res = await asyncio.to_thread(
        run_freecad_document_edit_sandboxed,
        repair_script,
        result.exports.get("fcstd"),
        timeout_s=FREECAD_SANDBOX_TIMEOUT_S,
        cpu_seconds=FREECAD_SANDBOX_CPU_SECONDS,
        address_space_mb=FREECAD_SANDBOX_ADDRESS_SPACE_MB,
    )
    repaired = _freecad_exec_result_from_sandbox(repair_res, "FreeCAD site-layout repair failed")
    repaired.diagnostics.update(result.diagnostics)
    if not repaired.ok:
        repaired.error = repaired.error or site_layout_failure_message(audit)
        return repaired

    repaired_inspection = await _inspect_fcstd_b64(repaired.exports.get("fcstd"))
    repaired_summary = (
        repaired_inspection.get("document_summary") if repaired_inspection.get("ok") else None
    )
    repaired_audit = site_layout_audit_from_summary(repaired_summary)
    repaired_needs_repair = site_layout_needs_repair(repaired_summary)
    if repaired_audit:
        repaired.diagnostics["site_layout_audit"] = _site_layout_audit_diagnostics(
            repaired_audit,
            repair_status="repaired" if not repaired_needs_repair else "failed",
            before=audit,
        )
    if repaired_audit and not repaired_needs_repair:
        return repaired

    repaired.ok = False
    repaired.error = site_layout_failure_message(repaired_audit or audit)
    return repaired


async def _sse_with_heartbeat(agen):
    """Format loop events as SSE frames, injecting keepalive comments when idle."""
    queue: asyncio.Queue = asyncio.Queue()

    async def produce():
        try:
            async for ev in agen:
                await queue.put(("event", ev))
        except Exception as exc:  # noqa: BLE001 - surface as an error event, never hang
            await queue.put(("event", {"type": "error", "message": str(exc)}))
        finally:
            await queue.put(("end", None))

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                kind, ev = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL_S)
            except asyncio.TimeoutError:
                yield HEARTBEAT_FRAME
                continue
            if kind == "end":
                break
            yield format_sse(ev)
    finally:
        task.cancel()


def create_app(
    *,
    gateway=None,
    execute=None,
    freecad_execute=None,
    session_store: SessionStore | None = None,
    artifact_store: ArtifactStore | None = None,
    freecad_gui_orchestrator: FreeCadGuiSessionOrchestrator | None = None,
) -> FastAPI:
    app = FastAPI(title="4yi-cad")
    app.state.gateway = gateway
    app.state.execute = execute
    app.state.freecad_execute = freecad_execute
    app.state.session_store = session_store
    app.state.artifact_store = artifact_store
    app.state.freecad_gui_orchestrator = freecad_gui_orchestrator
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.middleware("http")
    async def bearer_token_guard(request: Request, call_next):
        if not request.url.path.startswith(GUARDED_PREFIXES):
            return await call_next(request)
        if _bearer_guard_exempt(request):
            return await call_next(request)

        # Fail closed: an unusable/unconfigured token store must never be
        # treated as "no auth required" for a non-exempt request.
        try:
            store = _get_session_store(app)
        except Exception:  # noqa: BLE001 - store construction is env/volume dependent
            store = None
        if store is None:
            return JSONResponse(status_code=401, content={"detail": API_TOKEN_REQUIRED_DETAIL})

        token = _bearer_guard_token_from_header(request)
        if token is None:
            return JSONResponse(status_code=401, content={"detail": API_TOKEN_REQUIRED_DETAIL})

        try:
            valid = store.verify_api_token(token)
        except Exception:  # noqa: BLE001 - verification must not 500 the guard
            valid = False
        if not valid:
            return JSONResponse(status_code=401, content={"detail": API_TOKEN_INVALID_DETAIL})

        return await call_next(request)

    @app.api_route("/freecad", methods=["GET", "HEAD", "OPTIONS"])
    @app.api_route("/freecad/{path:path}", methods=["GET", "HEAD", "OPTIONS"])
    async def proxy_freecad_gui_http(request: Request, path: str = ""):
        target_url = _freecad_gui_proxy_target_url(
            path,
            request.scope.get("query_string", b""),
        )
        if not target_url:
            raise HTTPException(
                status_code=503,
                detail="FreeCAD GUI proxy upstream is not configured",
            )
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=FREECAD_GUI_PROXY_HTTP_TIMEOUT_S,
            ) as client:
                upstream = await client.request(
                    request.method,
                    target_url,
                    headers=_freecad_gui_proxy_request_headers(request.headers),
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"FreeCAD GUI proxy request failed: {exc}",
            ) from exc
        return Response(
            content=b"" if request.method == "HEAD" else upstream.content,
            status_code=upstream.status_code,
            headers=_freecad_gui_proxy_response_headers(upstream.headers),
        )

    @app.websocket("/freecad")
    @app.websocket("/freecad/{path:path}")
    async def proxy_freecad_gui_websocket(websocket: WebSocket, path: str = ""):
        target_url = _freecad_gui_proxy_target_url(
            path,
            websocket.scope.get("query_string", b""),
            websocket=True,
        )
        await websocket.accept()
        if not target_url:
            await websocket.close(
                code=1011,
                reason="FreeCAD GUI proxy upstream is not configured",
            )
            return
        try:
            import websockets

            async with websockets.connect(target_url, max_size=None) as upstream:
                async def browser_to_freecad():
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        if message.get("bytes") is not None:
                            await upstream.send(message["bytes"])
                        elif message.get("text") is not None:
                            await upstream.send(message["text"])

                async def freecad_to_browser():
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                tasks = {
                    asyncio.create_task(browser_to_freecad()),
                    asyncio.create_task(freecad_to_browser()),
                }
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    task.result()
                for task in pending:
                    with suppress(asyncio.CancelledError):
                        await task
        except WebSocketDisconnect:
            return
        except Exception:
            with suppress(RuntimeError):
                await websocket.close(code=1011)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    # Token management endpoints are deliberately NOT under GUARDED_PREFIXES:
    # the SSO edge in front of this app protects /api/tokens/*, so the bearer
    # guard only needs to cover the bridge/generate surface the desktop
    # FreeCAD client and CLI callers hit directly.
    @app.post("/api/tokens", status_code=201)
    async def create_api_token(req: CreateApiTokenRequest | None = None):
        store = _get_session_store(app)
        try:
            return store.create_api_token(label=req.label if req else None)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc

    @app.get("/api/tokens")
    async def list_api_tokens():
        store = _get_session_store(app)
        try:
            tokens = store.list_api_tokens()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc
        return {"tokens": tokens}

    @app.delete("/api/tokens/{token_id}", status_code=204)
    async def revoke_api_token(token_id: str):
        store = _get_session_store(app)
        try:
            revoked = store.revoke_api_token(token_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc
        if not revoked:
            raise HTTPException(status_code=404, detail="token not found")
        return Response(status_code=204)

    @app.get("/api/production/smoke")
    async def production_smoke():
        readiness = _production_readiness_report(app)
        return {
            "ok": readiness["ok"],
            "durable_storage_configured": readiness["durable_storage_configured"],
            "production_ready": readiness["production_ready"],
            "storage": readiness["storage"],
            "freecad_worker": readiness["freecad_worker"],
            "readiness": readiness,
        }

    @app.get("/api/production/readiness")
    async def production_readiness():
        return _production_readiness_report(app)

    @app.post("/api/sessions")
    async def create_session(req: CreateSessionRequest | None = None):
        store = _get_session_store(app)
        try:
            session = store.create_session(title=req.title if req else None)
        except Exception as exc:  # noqa: BLE001 - storage setup can be env/volume dependent
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc
        return {"session": session.__dict__}

    @app.get("/api/sessions")
    async def list_sessions(limit: int = Query(default=20, ge=1, le=100)):
        store = _get_session_store(app)
        try:
            sessions = store.list_sessions(limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc
        return {"sessions": sessions}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        store = _get_session_store(app)
        try:
            session = store.get_session(session_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session

    @app.post("/api/sessions/{session_id}/versions")
    async def add_session_version(session_id: str, req: SessionVersionRequest):
        store = _get_session_store(app)
        artifact_store = _get_artifact_store(app)
        summary = req.geometry_summary or geometry_summary(req.design_state)
        try:
            version = store.add_version(
                session_id=session_id,
                intent=req.intent,
                user_instruction=req.user_instruction,
                design_state=req.design_state.model_dump(),
                script=req.script,
                geometry_summary=summary,
                patch=req.patch,
                metadata=req.metadata,
                status=req.status,
                error=req.error,
            )
            artifact_refs = artifact_store.save_version_artifacts(
                session_id=session_id,
                version_id=version.id,
                preview_png_b64=req.preview_png_b64,
                exports=req.artifacts,
            )
            if artifact_refs:
                version = store.update_version_metadata(
                    session_id=session_id,
                    version_id=version.id,
                    metadata=_metadata_with_artifact_refs(version.metadata, artifact_refs),
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc
        return {"version": version.__dict__}

    @app.get("/api/sessions/{session_id}/versions/{version_id}")
    async def get_session_version(session_id: str, version_id: str):
        store = _get_session_store(app)
        try:
            version = store.get_version(session_id, version_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc
        if version is None:
            raise HTTPException(status_code=404, detail="version not found")
        return {"version": version.__dict__}

    @app.get("/api/sessions/{session_id}/versions/{version_id}/artifacts/{artifact_name}")
    async def get_session_artifact(session_id: str, version_id: str, artifact_name: str):
        artifact_store = _get_artifact_store(app)
        try:
            artifact = artifact_store.get_artifact(
                session_id=session_id,
                version_id=version_id,
                artifact_name=artifact_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(
            str(artifact.path),
            media_type=artifact.media_type,
            filename=artifact.filename,
        )

    @app.post("/api/sessions/{session_id}/rollback")
    async def rollback_session(session_id: str, req: RollbackSessionRequest):
        store = _get_session_store(app)
        artifact_store = _get_artifact_store(app)
        try:
            target = store.get_version(session_id, req.version_id)
            if target is None:
                raise KeyError(req.version_id)
            metadata = dict(target.metadata or {})
            metadata["rollback_source_version_id"] = target.id
            version = store.add_version(
                session_id=session_id,
                intent="rollback",
                user_instruction=req.user_instruction
                or f"Rollback to v{target.version_number}",
                design_state=target.design_state,
                script=target.script,
                geometry_summary=target.geometry_summary,
                patch={
                    "op": "rollback_to_version",
                    "version_id": target.id,
                    "version_number": target.version_number,
                },
                metadata=metadata,
                status=target.status,
                error=target.error,
            )
            artifact_refs = artifact_store.copy_version_artifacts(
                session_id=session_id,
                source_version_id=target.id,
                dest_version_id=version.id,
            )
            if artifact_refs:
                version = store.update_version_metadata(
                    session_id=session_id,
                    version_id=version.id,
                    metadata=_metadata_with_artifact_refs(version.metadata, artifact_refs),
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="version not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc
        return {"version": version.__dict__}

    @app.get("/api/freecad/smoke")
    async def freecad_smoke():
        res = await asyncio.to_thread(
            run_freecad_sandboxed,
            MINIMAL_FREECAD_SMOKE_SCRIPT,
            timeout_s=FREECAD_SMOKE_TIMEOUT_S,
            cpu_seconds=FREECAD_SMOKE_CPU_SECONDS,
            address_space_mb=FREECAD_SANDBOX_ADDRESS_SPACE_MB,
        )
        if not res.success or not isinstance(res.result, dict):
            return {
                "ok": False,
                "error": res.error or "FreeCAD sandbox execution failed",
                "timed_out": res.timed_out,
            }
        result = res.result
        return {
            "ok": bool(result.get("ok")),
            "freecad_version": result.get("freecad_version"),
            "exports": sorted((result.get("exports") or {}).keys()),
            "preview": bool(result.get("preview_png_b64")),
            "error": result.get("error"),
        }

    @app.get("/api/freecad/upload_policy")
    async def freecad_upload_policy():
        max_bytes = _freecad_upload_max_bytes()
        return {
            "max_bytes": max_bytes,
            "max_mb": round(max_bytes / 1024 / 1024, 2),
            "formats": list(FREECAD_IMPORT_FORMATS),
        }

    @app.post("/api/freecad/sessions")
    async def create_freecad_remote_session(req: FreeCadRemoteSessionRequest):
        store = _get_session_store(app)
        artifact_store = _get_artifact_store(app)
        orchestrator = _get_freecad_gui_orchestrator(app)
        workbench_session_id = _remote_session_workbench_id(req)
        orchestrator_enabled = orchestrator.enabled()
        remote_url_configured = bool(_remote_desktop_base_url())
        shared_remote_session_id = (
            _shared_freecad_session_id() if _shared_freecad_service_enabled() else None
        )
        status = "starting" if orchestrator_enabled else ("ready" if remote_url_configured else "starting")
        try:
            remote_session, reused = store.create_or_reuse_remote_freecad_session(
                remote_session_id=shared_remote_session_id,
                workbench_session_id=workbench_session_id,
                base_version_id=req.version_id,
                reuse=req.reuse,
                status=status,
                bridge_status="pending",
                metadata=_remote_session_config_metadata(req.metadata),
            )

            event_type = "session_reused" if reused else "session_requested"
            event_metadata = {
                "workbench_session_id": workbench_session_id,
                "base_version_id": remote_session.base_version_id,
                "mode": req.mode,
                "remote_url_configured": bool(remote_session.remote_url),
                "shared_remote_session_id": shared_remote_session_id,
            }

            should_launch = (
                orchestrator_enabled
                and (
                    not reused
                    or not remote_session.remote_url
                    or remote_session.status != "ready"
                )
            )
            if should_launch:
                try:
                    fcstd_b64 = _optional_artifact_b64(
                        artifact_store,
                        workbench_session_id,
                        remote_session.base_version_id,
                        "fcstd",
                    )
                    launch = await asyncio.to_thread(
                        orchestrator.start_session,
                        remote_session_id=remote_session.id,
                        workbench_session_id=workbench_session_id,
                        base_version_id=remote_session.base_version_id,
                        fcstd_b64=fcstd_b64,
                    )
                except Exception as exc:  # noqa: BLE001
                    failure_metadata = {
                        **remote_session.metadata,
                        "orchestrator_error": str(exc),
                    }
                    remote_session = store.update_remote_freecad_session(
                        remote_session_id=remote_session.id,
                        status="failed",
                        metadata=failure_metadata,
                    )
                    store.add_remote_freecad_session_event(
                        remote_session_id=remote_session.id,
                        event_type="session_start_failed",
                        metadata={
                            **event_metadata,
                            "error": str(exc),
                        },
                    )
                    raise HTTPException(
                        status_code=503,
                        detail=f"remote FreeCAD session launch failed: {exc}",
                    ) from exc

                if launch is not None:
                    remote_session = store.update_remote_freecad_session(
                        remote_session_id=remote_session.id,
                        status=launch.status,
                        remote_url=launch.remote_url,
                        bridge_status=launch.bridge_status,
                        metadata={
                            **remote_session.metadata,
                            **launch.metadata,
                        },
                    )
                    event_type = "session_started"
                    event_metadata = {
                        **event_metadata,
                        "orchestrator_backend": launch.metadata.get("orchestrator_backend"),
                        "remote_url_configured": bool(launch.remote_url),
                    }

            remote_url = _remote_desktop_url_for(remote_session.id)
            if not orchestrator_enabled and remote_url and remote_session.remote_url != remote_url:
                remote_session = store.update_remote_freecad_session(
                    remote_session_id=remote_session.id,
                    status="ready",
                    remote_url=remote_url,
                    metadata={
                        **remote_session.metadata,
                        "remote_desktop_configured": True,
                    },
                )
                event_metadata["remote_url_configured"] = True

            store.add_remote_freecad_session_event(
                remote_session_id=remote_session.id,
                event_type=event_type,
                metadata=event_metadata,
            )
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session or version not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD session storage unavailable: {exc}",
            ) from exc
        return {**_remote_session_dict(remote_session), "reused": reused}

    @app.get("/api/freecad/sessions")
    async def list_freecad_remote_sessions(
        session_id: str | None = None,
        workbench_session_id: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
    ):
        store = _get_session_store(app)
        try:
            sessions = store.list_remote_freecad_sessions(
                workbench_session_id=workbench_session_id or session_id,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD session storage unavailable: {exc}",
            ) from exc
        return {"sessions": sessions}

    @app.get("/api/freecad/sessions/{remote_session_id}")
    async def get_freecad_remote_session(remote_session_id: str):
        store = _get_session_store(app)
        try:
            remote_session = _ensure_shared_remote_freecad_session(store, remote_session_id)
            if remote_session is None:
                raise KeyError(remote_session_id)
            events = store.list_remote_freecad_session_events(
                remote_session_id=remote_session_id,
                limit=100,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="remote FreeCAD session not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD session storage unavailable: {exc}",
            ) from exc
        return {**_remote_session_dict(remote_session), "events": events}

    @app.delete("/api/freecad/sessions/{remote_session_id}")
    async def stop_freecad_remote_session(
        remote_session_id: str,
        req: FreeCadRemoteSessionStopRequest | None = None,
    ):
        store = _get_session_store(app)
        orchestrator = _get_freecad_gui_orchestrator(app)
        try:
            existing = store.get_remote_freecad_session(remote_session_id)
            if existing is None:
                raise KeyError(remote_session_id)
            orchestrator_result = None
            if orchestrator.enabled():
                orchestrator_result = await asyncio.to_thread(
                    orchestrator.stop_session,
                    remote_session_id=remote_session_id,
                )
            remote_session = store.stop_remote_freecad_session(
                remote_session_id=remote_session_id,
                reason=req.reason if req else None,
            )
            metadata = remote_session.metadata
            if orchestrator_result is not None:
                metadata = {
                    **metadata,
                    "orchestrator_stop": orchestrator_result,
                }
                remote_session = store.update_remote_freecad_session(
                    remote_session_id=remote_session_id,
                    metadata=metadata,
                )
            store.add_remote_freecad_session_event(
                remote_session_id=remote_session_id,
                event_type="session_stopped",
                metadata={
                    "reason": req.reason if req else None,
                    "orchestrator_stop": orchestrator_result,
                },
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="remote FreeCAD session not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD session storage unavailable: {exc}",
            ) from exc
        return _remote_session_dict(remote_session)

    @app.post("/api/freecad/sessions/{remote_session_id}/commands")
    async def queue_freecad_remote_session_command(
        remote_session_id: str,
        req: FreeCadRemoteSessionCommandRequest,
    ):
        store = _get_session_store(app)
        try:
            remote_session = _ensure_shared_remote_freecad_session(store, remote_session_id)
            if remote_session is None:
                raise KeyError(remote_session_id)
            if (
                req.base_version_id
                and remote_session.current_version_id
                and req.base_version_id != remote_session.current_version_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail=_remote_session_conflict_detail(
                        active_version_id=remote_session.current_version_id,
                        expected_version_id=req.base_version_id,
                    ),
                )
            command = store.create_remote_freecad_session_command(
                remote_session_id=remote_session_id,
                op=req.op,
                input=req.input,
                base_version_id=req.base_version_id or remote_session.current_version_id,
                metadata={"source": "web_control_plane"},
            )
            event = store.add_remote_freecad_session_event(
                remote_session_id=remote_session_id,
                event_type="bridge_command_queued",
                metadata={
                    "command_id": command.id,
                    "op": req.op,
                    "input": req.input,
                    "base_version_id": req.base_version_id
                    or remote_session.current_version_id,
                    "status": command.status,
                },
            )
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="remote FreeCAD session not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD session storage unavailable: {exc}",
            ) from exc
        return {
            "command_id": command.id,
            "status": command.status,
            "session_id": remote_session_id,
            "command": _remote_command_dict(command),
            "event": event.__dict__,
        }

    @app.get("/api/freecad/sessions/{remote_session_id}/commands/{command_id}")
    async def get_freecad_remote_session_command(
        remote_session_id: str,
        command_id: str,
    ):
        store = _get_session_store(app)
        try:
            remote_session = store.get_remote_freecad_session(remote_session_id)
            if remote_session is None:
                raise KeyError(remote_session_id)
            command = store.get_remote_freecad_session_command(
                remote_session_id=remote_session_id,
                command_id=command_id,
            )
            if command is None:
                raise KeyError(command_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="remote FreeCAD command not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD command lookup failed: {exc}",
            ) from exc
        return {
            "session": _remote_session_dict(remote_session),
            "command": _remote_command_dict(command),
        }

    @app.post("/api/freecad/sessions/{remote_session_id}/panel/actions")
    async def create_freecad_panel_action(
        remote_session_id: str,
        req: FreeCadPanelActionRequest,
    ):
        store = _get_session_store(app)
        artifact_store = _get_artifact_store(app)
        try:
            remote_session = _ensure_shared_remote_freecad_session(store, remote_session_id)
            if remote_session is None:
                raise KeyError(remote_session_id)
            command = None
            generated_version = None
            generation_event = None
            should_queue_macro = req.action in {"prompt", "generate_patch"} and bool(req.macro)
            if should_queue_macro:
                command = store.create_remote_freecad_session_command(
                    remote_session_id=remote_session_id,
                    op="run_macro",
                    input={
                        "instruction": req.prompt,
                        "selection": req.selection,
                        "macro": req.macro,
                        "panel_action": req.action,
                        "patch_id": req.patch_id,
                    },
                    base_version_id=remote_session.current_version_id,
                    metadata={"source": "freecad_panel", "panel_action": req.action},
                )
            elif req.action == "prompt" and (req.prompt or "").strip():
                generated_version, command, generation_event = await _queue_freecad_panel_agent_generation(
                    app,
                    store=store,
                    artifact_store=artifact_store,
                    remote_session=remote_session,
                    remote_session_id=remote_session_id,
                    req=req,
                )
            action_event = store.add_remote_freecad_session_event(
                remote_session_id=remote_session_id,
                event_type="panel_action_requested",
                metadata={
                    "action": req.action,
                    "prompt": req.prompt,
                    "patch_id": req.patch_id,
                    "selection_count": len(req.selection.get("objects") or [])
                    if isinstance(req.selection, dict)
                    else 0,
                    "command_id": command.id if command else None,
                    "generated_version_id": generated_version.id if generated_version else None,
                    "source": "freecad_panel",
                    "metadata": req.metadata,
                },
            )
            command_event = None
            if command is not None:
                command_event = store.add_remote_freecad_session_event(
                    remote_session_id=remote_session_id,
                    event_type="bridge_command_queued",
                    metadata={
                        "command_id": command.id,
                        "op": command.op,
                        "input": command.input,
                        "base_version_id": command.base_version_id,
                        "status": command.status,
                        "source": "freecad_panel",
                    },
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="remote FreeCAD session not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD panel action failed: {exc}",
            ) from exc
        return {
            "status": "queued" if command else "recorded",
            "session": _remote_session_dict(remote_session),
            "command": _remote_command_dict(command) if command else None,
            "generated_version": generated_version.__dict__ if generated_version else None,
            "event": action_event.__dict__,
            "command_event": command_event.__dict__ if command_event else None,
            "generation_event": generation_event.__dict__ if generation_event else None,
        }

    @app.get("/api/freecad/sessions/{remote_session_id}/bridge/context")
    async def get_freecad_bridge_context(remote_session_id: str):
        store = _get_session_store(app)
        try:
            remote_session = _ensure_shared_remote_freecad_session(store, remote_session_id)
            if remote_session is None:
                raise KeyError(remote_session_id)
            bridge = dict((remote_session.metadata or {}).get("bridge") or {})
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="remote FreeCAD session not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD bridge context lookup failed: {exc}",
            ) from exc
        return {
            "session": _remote_session_dict(remote_session),
            "bridge_status": remote_session.bridge_status,
            "bridge": bridge,
            "workbench": bridge.get("workbench"),
            "selection": bridge.get("selection") or {},
            "document_tree": bridge.get("document_tree") or {},
            "console_tail": bridge.get("console_tail") or [],
        }

    @app.post("/api/freecad/sessions/{remote_session_id}/bridge/heartbeat")
    async def freecad_bridge_heartbeat(
        remote_session_id: str,
        req: FreeCadBridgeHeartbeatRequest,
    ):
        store = _get_session_store(app)
        try:
            remote_session = _ensure_shared_remote_freecad_session(store, remote_session_id)
            if remote_session is None:
                raise KeyError(remote_session_id)
            metadata = _remote_bridge_metadata(
                remote_session.metadata,
                req,
                event="heartbeat",
            )
            stopped = remote_session.status == "stopped"
            remote_session = store.update_remote_freecad_session(
                remote_session_id=remote_session_id,
                status="ready" if remote_session.status in {"starting", "idle"} else None,
                current_version_id=req.current_version_id,
                bridge_status="disconnected" if stopped else "connected",
                metadata=metadata,
            )
            event = store.add_remote_freecad_session_event(
                remote_session_id=remote_session_id,
                event_type="bridge_heartbeat",
                metadata={
                    "bridge_id": req.bridge_id,
                    "freecad_version": req.freecad_version,
                    "document_name": req.document_name,
                    "current_version_id": req.current_version_id,
                    "workbench": req.workbench,
                    "selection_count": len(req.selection.get("objects") or [])
                    if isinstance(req.selection, dict)
                    else 0,
                    "document_object_count": len(req.document_tree.get("objects") or [])
                    if isinstance(req.document_tree, dict)
                    else 0,
                },
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="remote FreeCAD session not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD bridge heartbeat failed: {exc}",
            ) from exc
        return {
            "session": _remote_session_dict(remote_session),
            "event": event.__dict__,
        }

    @app.post("/api/freecad/sessions/{remote_session_id}/bridge/poll")
    async def freecad_bridge_poll(
        remote_session_id: str,
        req: FreeCadBridgePollRequest,
    ):
        store = _get_session_store(app)
        try:
            remote_session = _ensure_shared_remote_freecad_session(store, remote_session_id)
            if remote_session is None:
                raise KeyError(remote_session_id)
            metadata = _remote_bridge_metadata(
                remote_session.metadata,
                req,
                event="poll",
            )
            stopped = remote_session.status == "stopped"
            remote_session = store.update_remote_freecad_session(
                remote_session_id=remote_session_id,
                status="ready" if remote_session.status in {"starting", "idle"} else None,
                current_version_id=req.current_version_id,
                bridge_status="disconnected" if stopped else "connected",
                metadata=metadata,
            )
            commands = []
            if not stopped:
                commands = store.claim_pending_remote_freecad_session_commands(
                    remote_session_id=remote_session_id,
                    limit=req.max_commands,
                )
            event = store.add_remote_freecad_session_event(
                remote_session_id=remote_session_id,
                event_type="bridge_poll",
                metadata={
                    "bridge_id": req.bridge_id,
                    "command_count": len(commands),
                    "current_version_id": req.current_version_id,
                    "workbench": req.workbench,
                    "selection_count": len(req.selection.get("objects") or [])
                    if isinstance(req.selection, dict)
                    else 0,
                },
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="remote FreeCAD session not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD bridge poll failed: {exc}",
            ) from exc
        return {
            "session": _remote_session_dict(remote_session),
            "commands": commands,
            "event": event.__dict__,
        }

    @app.post("/api/freecad/sessions/{remote_session_id}/bridge/commands/{command_id}/result")
    async def freecad_bridge_command_result(
        remote_session_id: str,
        command_id: str,
        req: FreeCadBridgeCommandResultRequest,
    ):
        store = _get_session_store(app)
        try:
            existing = store.get_remote_freecad_session(remote_session_id)
            if existing is None:
                raise KeyError(remote_session_id)
            command = store.complete_remote_freecad_session_command(
                remote_session_id=remote_session_id,
                command_id=command_id,
                status=req.status,
                result=req.result,
                error=req.error,
                metadata={
                    **req.metadata,
                    "source": "freecad_bridge",
                },
            )
            event_type = (
                "bridge_command_completed"
                if req.status == "completed"
                else "bridge_command_failed"
            )
            remote_session = store.update_remote_freecad_session(
                remote_session_id=remote_session_id,
                status="ready" if existing.status in {"starting", "idle", "paused"} else None,
                current_version_id=req.current_version_id,
                bridge_status="disconnected" if existing.status == "stopped" else "connected",
            )
            event = store.add_remote_freecad_session_event(
                remote_session_id=remote_session_id,
                event_type=event_type,
                metadata={
                    "command_id": command_id,
                    "status": req.status,
                    "error": req.error,
                    "error_code": (req.result.get("error") or {}).get("code")
                    if isinstance(req.result.get("error"), dict)
                    else None,
                    "current_version_id": req.current_version_id,
                },
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="remote FreeCAD command not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD bridge command result failed: {exc}",
            ) from exc
        return {
            "session": _remote_session_dict(remote_session),
            "command": _remote_command_dict(command),
            "event": event.__dict__,
        }

    @app.post("/api/freecad/sessions/{remote_session_id}/save")
    async def save_freecad_remote_session(
        remote_session_id: str,
        req: FreeCadRemoteSessionSaveRequest,
    ):
        _enforce_freecad_upload_size(req.fcstd_b64, label="remote FreeCAD FCStd save")
        store = _get_session_store(app)
        artifact_store = _get_artifact_store(app)
        try:
            remote_session = store.get_remote_freecad_session(remote_session_id)
            if remote_session is None:
                raise KeyError(remote_session_id)
            workbench_session = store.get_session(remote_session.workbench_session_id)
            if workbench_session is None:
                raise KeyError(remote_session.workbench_session_id)

            expected_base_version_id = (
                req.base_version_id
                or remote_session.current_version_id
                or remote_session.base_version_id
            )
            active_version_id = workbench_session["session"]["active_version_id"]
            if (
                expected_base_version_id
                and active_version_id
                and expected_base_version_id != active_version_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail=_remote_session_conflict_detail(
                        active_version_id=active_version_id,
                        expected_version_id=expected_base_version_id,
                    ),
                )

            artifacts = {**req.artifacts, "fcstd": req.fcstd_b64}
            metadata = _remote_session_config_metadata(
                {
                    "preview_mode": "generated",
                    "engine": "freecad",
                    "document_state": "fcstd_artifact",
                    "source": "remote_freecad_session",
                    "remote_session_id": remote_session_id,
                    "base_version_id": expected_base_version_id,
                    "include_derivatives": req.include_derivatives,
                    "generated_parameters": [],
                }
            )
            version = store.add_version(
                session_id=remote_session.workbench_session_id,
                intent="modify",
                user_instruction=req.message or "Save remote FreeCAD session",
                design_state=_freecad_document_state(),
                script=(
                    "# Saved from remote FreeCAD GUI session.\n"
                    "# The FCStd artifact is the authoritative document state.\n"
                    f"# Remote session: {remote_session_id}\n"
                ),
                geometry_summary={
                    "engine": "freecad",
                    "source": "remote_freecad_session",
                    "remote_session_id": remote_session_id,
                    "exports": sorted(name.lower() for name in artifacts),
                },
                patch={
                    "op": "remote_freecad_session_save",
                    "remote_session_id": remote_session_id,
                    "base_version_id": expected_base_version_id,
                },
                metadata=metadata,
                status="ok",
            )
            artifact_refs = artifact_store.save_version_artifacts(
                session_id=remote_session.workbench_session_id,
                version_id=version.id,
                preview_png_b64=req.preview_png_b64,
                exports=artifacts,
            )
            if artifact_refs:
                version = store.update_version_metadata(
                    session_id=remote_session.workbench_session_id,
                    version_id=version.id,
                    metadata=_metadata_with_artifact_refs(version.metadata, artifact_refs),
                )
            remote_session = store.update_remote_freecad_session(
                remote_session_id=remote_session_id,
                status="ready",
                current_version_id=version.id,
            )
            event = store.add_remote_freecad_session_event(
                remote_session_id=remote_session_id,
                event_type="session_saved",
                metadata={
                    "version_id": version.id,
                    "base_version_id": expected_base_version_id,
                    "message": req.message,
                    "artifact_refs": artifact_refs,
                },
            )
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session or version not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"remote FreeCAD session save failed: {exc}",
            ) from exc
        return {
            "revision_id": version.id,
            "version_id": version.id,
            "fcstd_artifact_id": "fcstd",
            "fcstd_artifact_ref": artifact_refs.get("fcstd"),
            "derivative_job_id": None,
            "version": version.__dict__,
            "session": _remote_session_dict(remote_session),
            "event": event.__dict__,
        }

    @app.post("/api/freecad/import_model")
    async def freecad_import_model(req: FreeCadImportModelRequest):
        _enforce_freecad_upload_size(req.data_b64, label="FreeCAD import")
        res = await asyncio.to_thread(
            run_freecad_import_sandboxed,
            req.format,
            req.data_b64,
            filename=req.filename,
            timeout_s=FREECAD_SANDBOX_TIMEOUT_S,
            cpu_seconds=FREECAD_SANDBOX_CPU_SECONDS,
            address_space_mb=FREECAD_SANDBOX_ADDRESS_SPACE_MB,
        )
        result = _freecad_exec_result_from_sandbox(res, "FreeCAD import failed")
        if not result.ok:
            return _freecad_response(result)
        inspection = await _inspect_fcstd_b64(result.exports.get("fcstd"))

        store = _get_session_store(app)
        artifact_store = _get_artifact_store(app)
        try:
            session_id = req.session_id
            metadata = {
                "preview_mode": "generated",
                "engine": "freecad",
                "freecad_version": result.freecad_version,
                "document_state": "fcstd_artifact",
                "source_format": req.format,
                "source_filename": req.filename,
                "generated_parameters": [],
            }
            if session_id is None:
                session = store.create_session(
                    title=req.title or req.filename or "Imported FreeCAD model"
                )
                session_id = session.id
            else:
                session = store.get_session(session_id)
                if session is None:
                    raise KeyError(session_id)
            metadata = _metadata_with_freecad_diagnostics(metadata, result)
            metadata = _metadata_with_document_summary(metadata, inspection)
            version = store.add_version(
                session_id=session_id,
                intent="create",
                user_instruction=req.user_instruction or f"Import {req.format.upper()} model",
                design_state=_freecad_document_state(),
                script=_freecad_import_marker_script(req.format, req.filename),
                geometry_summary=_freecad_geometry_metadata(
                    exports=result.exports,
                    inspection=inspection,
                    extra={"source_format": req.format},
                ),
                metadata=metadata,
                status="ok",
            )
            artifact_refs = artifact_store.save_version_artifacts(
                session_id=session_id,
                version_id=version.id,
                preview_png_b64=result.preview_png_b64,
                exports=result.exports,
            )
            if artifact_refs:
                version = store.update_version_metadata(
                    session_id=session_id,
                    version_id=version.id,
                    metadata=_metadata_with_artifact_refs(version.metadata, artifact_refs),
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc

        return _freecad_response(result, session_id=session_id, version=version)

    @app.post("/api/freecad/document/edit")
    async def freecad_document_edit(req: FreeCadDocumentEditRequest):
        store = _get_session_store(app)
        artifact_store = _get_artifact_store(app)
        session_id = req.session_id
        try:
            fcstd_b64, source_version, resolved_version_id = _resolve_fcstd_b64(
                store,
                artifact_store,
                fcstd_b64=req.fcstd_b64,
                session_id=session_id,
                version_id=req.version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="version not found") from exc

        res = await asyncio.to_thread(
            run_freecad_document_edit_sandboxed,
            req.script,
            fcstd_b64,
            timeout_s=FREECAD_SANDBOX_TIMEOUT_S,
            cpu_seconds=FREECAD_SANDBOX_CPU_SECONDS,
            address_space_mb=FREECAD_SANDBOX_ADDRESS_SPACE_MB,
        )
        result = _freecad_exec_result_from_sandbox(res, "FreeCAD document edit failed")
        if not result.ok:
            return _freecad_response(result, session_id=session_id)
        inspection = await _inspect_fcstd_b64(result.exports.get("fcstd"))

        version = None
        if session_id:
            try:
                metadata = dict(source_version.metadata if source_version else {})
                metadata.update(
                    {
                        "preview_mode": "generated",
                        "engine": "freecad",
                        "freecad_version": result.freecad_version,
                        "document_state": "fcstd_artifact",
                        "source_version_id": source_version.id
                        if source_version
                        else resolved_version_id,
                        "generated_parameters": extract_script_parameters(req.script),
                    }
                )
                metadata = _metadata_with_freecad_diagnostics(metadata, result)
                metadata = _metadata_with_document_summary(metadata, inspection)
                metadata = _metadata_with_typed_state_diff(
                    metadata,
                    (source_version.metadata or {}).get("document_summary")
                    if source_version
                    else None,
                    inspection,
                )
                version = store.add_version(
                    session_id=session_id,
                    intent="modify",
                    user_instruction=req.user_instruction or "Edit FreeCAD document",
                    design_state=source_version.design_state
                    if source_version
                    else _freecad_document_state(),
                    script=req.script,
                    geometry_summary=_freecad_geometry_metadata(
                        exports=result.exports,
                        inspection=inspection,
                        extra={
                            "source_version_id": source_version.id
                            if source_version
                            else resolved_version_id,
                        },
                    ),
                    patch={
                        "op": "edit_fcstd_document",
                        "source_version_id": source_version.id
                        if source_version
                        else resolved_version_id,
                    },
                    metadata=metadata,
                    status="ok",
                )
                artifact_refs = artifact_store.save_version_artifacts(
                    session_id=session_id,
                    version_id=version.id,
                    preview_png_b64=result.preview_png_b64,
                    exports=result.exports,
                )
                if artifact_refs:
                    version = store.update_version_metadata(
                        session_id=session_id,
                        version_id=version.id,
                        metadata=_metadata_with_artifact_refs(version.metadata, artifact_refs),
                    )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="session not found") from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc

        return _freecad_response(result, session_id=session_id, version=version)

    @app.post("/api/freecad/document/patch")
    async def freecad_document_patch(req: FreeCadDocumentPatchRequest):
        store = _get_session_store(app)
        artifact_store = _get_artifact_store(app)
        session_id = req.session_id
        patches = [patch.model_dump(exclude_none=True) for patch in req.patches]
        try:
            fcstd_b64, source_version, resolved_version_id = _resolve_fcstd_b64(
                store,
                artifact_store,
                fcstd_b64=req.fcstd_b64,
                session_id=session_id,
                version_id=req.version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="version not found") from exc

        res = await asyncio.to_thread(
            run_freecad_document_patch_sandboxed,
            patches,
            fcstd_b64,
            dry_run=req.dry_run,
            timeout_s=FREECAD_SANDBOX_TIMEOUT_S,
            cpu_seconds=FREECAD_SANDBOX_CPU_SECONDS,
            address_space_mb=FREECAD_SANDBOX_ADDRESS_SPACE_MB,
        )
        result = _freecad_exec_result_from_sandbox(res, "FreeCAD document patch failed")
        raw_result = res.result or {}
        patch_results = raw_result.get("patch_results") if raw_result else []
        if not result.ok:
            response = _freecad_response(result, session_id=session_id)
            response["patch_results"] = patch_results or []
            response["dry_run"] = bool(req.dry_run)
            if req.dry_run:
                response["would_create_version"] = False
            return response

        source_version_id = source_version.id if source_version else resolved_version_id
        source_summary = (
            (source_version.metadata or {}).get("document_summary")
            if source_version
            else None
        )
        if req.dry_run and raw_result.get("document_summary"):
            inspection = {
                "ok": True,
                "engine": "freecad",
                "error": raw_result.get("document_summary_error"),
                "document_summary": raw_result.get("document_summary"),
                "freecad_version": raw_result.get("freecad_version") or result.freecad_version,
            }
        else:
            inspection = await _inspect_fcstd_b64(result.exports.get("fcstd"))

        if req.dry_run:
            response = _freecad_response(result, session_id=session_id, version=None)
            response.update(
                {
                    "dry_run": True,
                    "would_create_version": False,
                    "source_version_id": source_version_id,
                    "patch_results": patch_results or [],
                    "document_summary": inspection.get("document_summary"),
                    "document_summary_error": inspection.get("error"),
                }
            )
            if source_summary and inspection.get("ok") and inspection.get("document_summary"):
                response["document_state_diff"] = typed_state_diff(
                    source_summary,
                    inspection["document_summary"],
                )
            return response

        version = None
        if session_id:
            try:
                metadata = dict(source_version.metadata if source_version else {})
                metadata.update(
                    {
                        "preview_mode": "generated",
                        "engine": "freecad",
                        "freecad_version": result.freecad_version,
                        "document_state": "fcstd_artifact",
                        "source_version_id": source_version_id,
                        "document_patch_results": patch_results or [],
                        "generated_parameters": [],
                    }
                )
                metadata = _metadata_with_freecad_diagnostics(metadata, result)
                metadata = _metadata_with_document_summary(metadata, inspection)
                metadata = _metadata_with_typed_state_diff(
                    metadata,
                    (source_version.metadata or {}).get("document_summary")
                    if source_version
                    else None,
                    inspection,
                )
                version = store.add_version(
                    session_id=session_id,
                    intent="modify",
                    user_instruction=req.user_instruction or "Patch FreeCAD document",
                    design_state=source_version.design_state
                    if source_version
                    else _freecad_document_state(),
                    script=_freecad_patch_marker_script(patches),
                    geometry_summary=_freecad_geometry_metadata(
                        exports=result.exports,
                        inspection=inspection,
                        extra={"source_version_id": source_version_id},
                    ),
                    patch={
                        "op": "patch_fcstd_document",
                        "source_version_id": source_version_id,
                        "patches": patches,
                        "results": patch_results or [],
                    },
                    metadata=metadata,
                    status="ok",
                )
                artifact_refs = artifact_store.save_version_artifacts(
                    session_id=session_id,
                    version_id=version.id,
                    preview_png_b64=result.preview_png_b64,
                    exports=result.exports,
                )
                if artifact_refs:
                    version = store.update_version_metadata(
                        session_id=session_id,
                        version_id=version.id,
                        metadata=_metadata_with_artifact_refs(version.metadata, artifact_refs),
                    )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="session not found") from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc

        response = _freecad_response(result, session_id=session_id, version=version)
        response["patch_results"] = patch_results or []
        if version is None:
            response["document_summary"] = inspection.get("document_summary")
            response["document_summary_error"] = inspection.get("error")
        return response

    @app.post("/api/freecad/document/intent")
    async def freecad_document_intent(req: FreeCadIntentRequest):
        return parse_freecad_intent(req.text, req.document_summary)

    @app.post("/api/freecad/document/inspect")
    async def freecad_document_inspect(req: FreeCadDocumentInspectRequest):
        store = _get_session_store(app)
        artifact_store = _get_artifact_store(app)
        try:
            fcstd_b64, _source_version, resolved_version_id = _resolve_fcstd_b64(
                store,
                artifact_store,
                fcstd_b64=req.fcstd_b64,
                session_id=req.session_id,
                version_id=req.version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="version not found") from exc

        inspection = await _inspect_fcstd_b64(fcstd_b64)
        return {
            "ok": inspection["ok"],
            "session_id": req.session_id,
            "version_id": resolved_version_id,
            "engine": "freecad",
            "freecad_version": inspection.get("freecad_version"),
            "document_summary": inspection.get("document_summary"),
            "error": inspection.get("error"),
        }

    @app.post("/api/generate")
    async def generate(req: GenerateRequest):
        execute = _get_execute(app)
        freecad_execute = _get_freecad_execute(app)
        try:
            gw = _get_gateway(app)
        except Exception as exc:  # noqa: BLE001 - report config/gateway setup as SSE, not 500
            error_message = str(exc)

            async def config_error_stream():
                yield format_sse({"type": "error", "message": error_message})
                yield format_sse({"type": "done", "ok": False})

            return StreamingResponse(
                config_error_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        agen = run_generation(
            req.prompt,
            gateway=gw,
            execute=execute,
            execute_freecad=freecad_execute,
            history=req.history,
        )
        return StreamingResponse(
            _sse_with_heartbeat(agen),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/design/initial")
    async def design_initial():
        state = default_design_state()
        return {
            "design_state": state.model_dump(),
            "script": render_cadquery_script(state),
            "engine": "cadquery",
            "geometry_summary": geometry_summary(state),
        }

    @app.post("/api/design/patch")
    async def design_patch(req: DesignPatchRequest):
        try:
            state = apply_patches(req.design_state, req.patches)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "design_state": state.model_dump(),
            "script": render_cadquery_script(state),
            "engine": "cadquery",
            "geometry_summary": geometry_summary(state),
        }

    @app.post("/api/design/render")
    async def design_render(req: DesignRenderRequest):
        execute = _get_execute(app)
        script = render_cadquery_script(req.design_state)
        try:
            result = await execute(script)
        except Exception as exc:  # noqa: BLE001 - render must report failure, not 500
            result = ExecResult(ok=False, error=f"sandbox execution failed: {exc}")
        return {
            "ok": result.ok,
            "design_state": req.design_state.model_dump(),
            "script": script,
            "engine": result.engine,
            "freecad_version": result.freecad_version,
            "preview_png_b64": result.preview_png_b64,
            "exports": result.exports,
            "geometry_summary": geometry_summary(req.design_state),
            "error": result.error,
        }

    @app.post("/api/script/patch")
    async def script_patch(req: ScriptPatchRequest):
        execute = _get_freecad_execute(app) if req.engine == "freecad" else _get_execute(app)
        try:
            script = apply_script_parameter_patches(
                req.script,
                [
                    ScriptParameterPatch(name=patch.name, value=patch.value)
                    for patch in req.patches
                ],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            result = await execute(script)
        except Exception as exc:  # noqa: BLE001 - script render must not 500 the UI
            result = ExecResult(
                ok=False,
                engine=req.engine,
                error=f"sandbox execution failed: {exc}",
            )

        return {
            "ok": result.ok,
            "script": script,
            "engine": result.engine,
            "freecad_version": result.freecad_version,
            "parameters": extract_script_parameters(script),
            "preview_png_b64": result.preview_png_b64,
            "exports": result.exports,
            "error": result.error,
        }

    @app.get("/")
    async def index():
        if _freecad_first_entry_enabled():
            url = _freecad_first_entry_url()
            if url:
                return RedirectResponse(url=url, status_code=307)
        if _INDEX_HTML.is_file():
            return FileResponse(str(_INDEX_HTML), media_type="text/html")
        return {"status": "ok", "ui": "unavailable"}

    @app.get("/workbench")
    async def workbench():
        if _INDEX_HTML.is_file():
            return FileResponse(str(_INDEX_HTML), media_type="text/html")
        return {"status": "ok", "ui": "unavailable"}

    # Not under GUARDED_PREFIXES by design (mirrors /api/tokens/*): this is a
    # same-origin, SSO-protected browser page for issuing/managing tokens and
    # installing the local FreeCAD addon (P3a, see docs/superpowers/specs/
    # 2026-08-04-plugin-mode-v2-design.md §1-2). It must stay reachable
    # without a bearer token from any client, including non-localhost ones.
    @app.get("/connect")
    async def connect():
        return HTMLResponse(content=CONNECT_PAGE_HTML)

    return app


app = create_app()
