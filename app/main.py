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
from app.events import HEARTBEAT_FRAME, HEARTBEAT_INTERVAL_S, format_sse

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


def create_app(*, gateway=None, execute=None) -> FastAPI:
    app = FastAPI(title="4yi-cad")
    app.state.gateway = gateway
    app.state.execute = execute

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.post("/api/generate")
    async def generate(req: GenerateRequest):
        gw = _get_gateway(app)
        execute = _get_execute(app)
        agen = run_generation(req.prompt, gateway=gw, execute=execute, history=req.history)
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
            "preview_png_b64": result.preview_png_b64,
            "exports": result.exports,
            "geometry_summary": geometry_summary(req.design_state),
            "error": result.error,
        }

    @app.get("/")
    async def index():
        if _INDEX_HTML.is_file():
            return FileResponse(str(_INDEX_HTML), media_type="text/html")
        return {"status": "ok", "ui": "unavailable"}

    return app


app = create_app()
