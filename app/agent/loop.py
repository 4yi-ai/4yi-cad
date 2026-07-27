"""V1 self-correcting generation loop.

A real tool-calling agent loop: ask the model for a run_cadquery script, execute it
in the sandbox, and if execution fails feed the error back as a tool result and ask
for a fix. Bounded to `max_attempts`, each a SEPARATE gateway call (<290s) — the
self-correction is multiple calls, never one long call.

The loop is an async generator of plain-dict domain events (formatted into SSE by
app/events.py) and is dependency-injected with a `gateway` (async chat_completion)
and an async `execute(script) -> ExecResult`, so it is fully unit-testable with
fakes — no network, no cadquery.

Events: status | script | retry | preview | artifact | error | done.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from app.agent.tools import MVP_TOOLS, SYSTEM_PROMPT
from app.cad.script_params import extract_script_parameters

DEFAULT_MAX_ATTEMPTS = 3


@dataclass
class ExecResult:
    ok: bool
    preview_png_b64: str | None = None
    exports: dict[str, str] = field(default_factory=dict)  # format -> base64
    error: str | None = None


Executor = Callable[[str], Awaitable[ExecResult]]

_FORCE_RUN = {"type": "function", "function": {"name": "run_cadquery"}}


def _first_run_call(completion):
    for call in completion.tool_calls or []:
        if call.get("function", {}).get("name") == "run_cadquery":
            return call
    return None


def _script_of(call) -> str | None:
    try:
        args = json.loads(call.get("function", {}).get("arguments") or "{}")
    except json.JSONDecodeError:
        return None
    script = args.get("script")
    return script if isinstance(script, str) and script.strip() else None


async def run_generation(
    prompt: str,
    *,
    gateway,
    execute: Executor,
    history: list[dict] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    tools: list[dict] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> AsyncIterator[dict]:
    tools = tools if tools is not None else MVP_TOOLS
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": prompt})

    yield {"type": "status", "message": "thinking"}

    last_error = "no CAD script was produced"

    for attempt in range(1, max_attempts + 1):
        completion = await gateway.chat_completion(
            messages, tools=tools, tool_choice=_FORCE_RUN
        )

        call = _first_run_call(completion)
        script = _script_of(call) if call else None

        if script is None:
            # Model replied without a usable tool call — nudge and retry.
            last_error = "you must call run_cadquery with a complete script"
            if attempt < max_attempts:
                yield {"type": "retry", "attempt": attempt, "message": last_error}
                messages.append({"role": "assistant", "content": completion.content or ""})
                messages.append({"role": "user", "content": last_error})
                continue
            break

        yield {
            "type": "script",
            "script": script,
            "attempt": attempt,
            "parameters": extract_script_parameters(script),
        }

        result = await execute(script)

        if result.ok:
            if result.preview_png_b64:
                yield {"type": "preview", "png_b64": result.preview_png_b64}
            for fmt, data_b64 in result.exports.items():
                yield {"type": "artifact", "format": fmt, "data_b64": data_b64}
            yield {"type": "done", "ok": True}
            return

        # Recoverable execution failure — feed the error back and try again.
        last_error = result.error or "execution failed"
        if attempt < max_attempts:
            yield {"type": "retry", "attempt": attempt, "message": last_error}
            messages.append(
                {"role": "assistant", "content": completion.content, "tool_calls": completion.tool_calls}
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": f"Execution failed:\n{last_error}\nFix the script and call run_cadquery again.",
                }
            )
            continue
        break

    yield {"type": "error", "message": last_error}
    yield {"type": "done", "ok": False}
