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
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.loop import ExecResult, MAX_CHAT_HISTORY_MESSAGE_CHARS, run_generation
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
from app.events import HEARTBEAT_FRAME, HEARTBEAT_INTERVAL_S, format_sse
from app.freecad_intents import parse_freecad_intent
from app.freecad_state import storage_status, typed_state_diff
from app.session_store import SessionStore, SqliteSessionStore

# The SPA is a single self-contained file at the repo root, served same-origin.
# Living at the root (next to pyproject/Dockerfile) also makes the deployment
# scanner classify this one container as a fullstack service, not an undeployed
# standalone frontend.
_INDEX_HTML = Path(__file__).resolve().parents[1] / "index.html"
DEFAULT_FREECAD_UPLOAD_MAX_BYTES = 100 * 1024 * 1024
FREECAD_IMPORT_FORMATS = ("fcstd", "step", "stp", "iges", "igs", "brep")
MAX_CHAT_HISTORY_PAYLOAD_MESSAGES = 40


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


class ChatHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=MAX_CHAT_HISTORY_MESSAGE_CHARS)


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    # Client is the source of truth: it replays prior turns as chat messages.
    history: list[ChatHistoryMessage] = Field(
        default_factory=list,
        max_length=MAX_CHAT_HISTORY_PAYLOAD_MESSAGES,
    )


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


def _get_artifact_store(app: FastAPI) -> ArtifactStore:
    store = getattr(app.state, "artifact_store", None)
    if store is None:
        store = FileArtifactStore()
        app.state.artifact_store = store
    return store


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
        timeout_s=180,
        cpu_seconds=120,
        address_space_mb=4096,
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
        timeout_s=180,
        cpu_seconds=120,
        address_space_mb=4096,
    )
    return _freecad_exec_result_from_sandbox(res, "FreeCAD sandbox execution failed")


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
) -> FastAPI:
    app = FastAPI(title="4yi-cad")
    app.state.gateway = gateway
    app.state.execute = execute
    app.state.freecad_execute = freecad_execute
    app.state.session_store = session_store
    app.state.artifact_store = artifact_store

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/api/production/smoke")
    async def production_smoke():
        store = _get_session_store(app)
        artifact_store = _get_artifact_store(app)
        storage = storage_status(
            str(getattr(store, "db_path", "custom-session-store")),
            str(getattr(artifact_store, "root", "custom-artifact-store")),
        )
        durable = all(item.get("durable_configured") for item in storage.values())
        writable = all(item.get("writable") for item in storage.values())
        worker_endpoint = os.environ.get("FOURYI_FREECAD_WORKER_URL") or os.environ.get("FREECAD_WORKER_URL")
        worker_split = bool(worker_endpoint)
        security_controls = {
            "egress_blocked": os.environ.get("FOURYI_FREECAD_WORKER_EGRESS_BLOCKED", "").lower() in {"1", "true", "yes", "on"},
            "read_only_rootfs": os.environ.get("FOURYI_FREECAD_WORKER_READ_ONLY_ROOTFS", "").lower() in {"1", "true", "yes", "on"},
            "seccomp_profile": bool(os.environ.get("FOURYI_FREECAD_WORKER_SECCOMP_PROFILE")),
            "tmpfs_workspace": os.environ.get("FOURYI_FREECAD_WORKER_TMPFS", "").lower() in {"1", "true", "yes", "on"},
        }
        hardened_worker = bool(worker_split and all(security_controls.values()))
        return {
            "ok": bool(writable),
            "durable_storage_configured": bool(durable),
            "production_ready": bool(durable and writable and hardened_worker),
            "storage": storage,
            "freecad_worker": {
                "mode": os.environ.get("FOURYI_CAD_WORKER_MODE", "single_container_subprocess"),
                "split_service_configured": worker_split,
                "endpoint_configured": worker_split,
                "hardened_worker_service": hardened_worker,
                "security_controls": security_controls,
                "risk": None if hardened_worker else "FreeCAD still runs in the app container or lacks required runtime isolation controls",
            },
        }

    @app.post("/api/sessions")
    async def create_session(req: CreateSessionRequest | None = None):
        store = _get_session_store(app)
        try:
            session = store.create_session(title=req.title if req else None)
        except Exception as exc:  # noqa: BLE001 - storage setup can be env/volume dependent
            raise HTTPException(status_code=503, detail=f"session storage unavailable: {exc}") from exc
        return {"session": session.__dict__}

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
            timeout_s=120,
            cpu_seconds=90,
            address_space_mb=4096,
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

    @app.post("/api/freecad/import_model")
    async def freecad_import_model(req: FreeCadImportModelRequest):
        _enforce_freecad_upload_size(req.data_b64, label="FreeCAD import")
        res = await asyncio.to_thread(
            run_freecad_import_sandboxed,
            req.format,
            req.data_b64,
            filename=req.filename,
            timeout_s=180,
            cpu_seconds=120,
            address_space_mb=4096,
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
            timeout_s=180,
            cpu_seconds=120,
            address_space_mb=4096,
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
            timeout_s=180,
            cpu_seconds=120,
            address_space_mb=4096,
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
            history=[item.model_dump() for item in req.history],
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
        if _INDEX_HTML.is_file():
            return FileResponse(str(_INDEX_HTML), media_type="text/html")
        return {"status": "ok", "ui": "unavailable"}

    return app


app = create_app()
