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
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent.loop import ExecResult, run_generation
from app.cad.design_state import (
    CadPatch,
    DesignState,
    apply_patches,
    default_design_state,
    geometry_summary,
    render_cadquery_script,
)
from app.cad.freecad import MINIMAL_FREECAD_SMOKE_SCRIPT, run_freecad_sandboxed
from app.cad.script_params import (
    ScriptParameterPatch,
    apply_script_parameter_patches,
    extract_script_parameters,
)
from app.events import HEARTBEAT_FRAME, HEARTBEAT_INTERVAL_S, format_sse
from app.session_store import SessionStore, SqliteSessionStore

# The SPA is a single self-contained file at the repo root, served same-origin.
# Living at the root (next to pyproject/Dockerfile) also makes the deployment
# scanner classify this one container as a fullstack service, not an undeployed
# standalone frontend.
_INDEX_HTML = Path(__file__).resolve().parents[1] / "index.html"


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    # Client is the source of truth: it replays prior turns as chat messages.
    history: list[dict] = Field(default_factory=list)


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
    status: Literal["ok", "failed"] = "ok"
    error: str | None = Field(default=None, max_length=8000)


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
    if not res.success or not isinstance(res.result, dict):
        return ExecResult(
            ok=False,
            engine="freecad",
            error=res.error or "FreeCAD sandbox execution failed",
        )

    r = res.result
    return ExecResult(
        ok=bool(r.get("ok")),
        preview_png_b64=r.get("preview_png_b64"),
        exports=r.get("exports") or {},
        error=r.get("error"),
        engine="freecad",
        freecad_version=r.get("freecad_version"),
    )


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
) -> FastAPI:
    app = FastAPI(title="4yi-cad")
    app.state.gateway = gateway
    app.state.execute = execute
    app.state.freecad_execute = freecad_execute
    app.state.session_store = session_store

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

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
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
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
        if _INDEX_HTML.is_file():
            return FileResponse(str(_INDEX_HTML), media_type="text/html")
        return {"status": "ok", "ui": "unavailable"}

    return app


app = create_app()
