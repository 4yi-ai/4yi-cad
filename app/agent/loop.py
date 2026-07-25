"""MVP generation loop: prompt -> run_cadquery tool call -> sandboxed execute -> events.

The loop is an async generator of plain-dict domain events (formatted into SSE wire
frames by app/events.py). It is dependency-injected with a `gateway` (any object
exposing async chat_completion) and an async `execute(script) -> ExecResult`, so it
is fully unit-testable with fakes and needs neither the network nor cadquery.

MVP scope: a single model turn that must produce a run_cadquery tool call; the
script is executed once. There is NO self-correction yet (that is V1) — a failed
execution is reported as an error event and the run ends.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from app.agent.tools import MVP_TOOLS, SYSTEM_PROMPT


@dataclass
class ExecResult:
    ok: bool
    preview_png_b64: str | None = None
    exports: dict[str, str] = field(default_factory=dict)
    error: str | None = None


Executor = Callable[[str], Awaitable[ExecResult]]


def _extract_script(completion) -> str | None:
    for call in completion.tool_calls or []:
        fn = call.get("function", {})
        if fn.get("name") == "run_cadquery":
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                return None
            script = args.get("script")
            if isinstance(script, str) and script.strip():
                return script
    return None


async def run_generation(
    prompt: str,
    *,
    gateway,
    execute: Executor,
    history: list[dict] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    tools: list[dict] | None = None,
) -> AsyncIterator[dict]:
    tools = tools if tools is not None else MVP_TOOLS
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": prompt})

    yield {"type": "status", "message": "thinking"}

    completion = await gateway.chat_completion(
        messages,
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "run_cadquery"}},
    )

    script = _extract_script(completion)
    if script is None:
        yield {
            "type": "error",
            "message": "The model did not produce a CAD script.",
        }
        yield {"type": "done", "ok": False}
        return

    yield {"type": "script", "script": script}

    result = await execute(script)

    if not result.ok:
        yield {"type": "error", "message": result.error or "execution failed"}
        yield {"type": "done", "ok": False}
        return

    if result.preview_png_b64:
        yield {"type": "preview", "png_b64": result.preview_png_b64}

    for fmt, data_b64 in result.exports.items():
        yield {"type": "artifact", "format": fmt, "data_b64": data_b64}

    yield {"type": "done", "ok": True}
