"""V1 self-correcting generation loop.

A real tool-calling agent loop: ask the model for a CAD script, execute it in the
selected engine sandbox, and if execution fails feed the error back as a tool
result and ask for a fix. Bounded to `max_attempts`, each a SEPARATE gateway call
(<290s) - the self-correction is multiple calls, never one long call.

The loop is an async generator of plain-dict domain events (formatted into SSE by
app/events.py) and is dependency-injected with a `gateway` (async chat_completion)
and async executor functions, so it is fully unit-testable with fakes - no network
or CAD runtime required.

Events: status | script | retry | preview | artifact | error | done.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from app.agent.site_layout import augment_prompt_with_site_layout_plan, is_site_layout_prompt
from app.agent.tools import MVP_TOOLS, SYSTEM_PROMPT
from app.cad.script_params import extract_script_parameters

DEFAULT_MAX_ATTEMPTS = 3
MAX_CHAT_HISTORY_MESSAGES = 12
MAX_CHAT_HISTORY_CHARS = 12_000
MAX_CHAT_HISTORY_MESSAGE_CHARS = 2_000


@dataclass
class ExecResult:
    ok: bool
    preview_png_b64: str | None = None
    exports: dict[str, str] = field(default_factory=dict)  # format -> base64
    error: str | None = None
    engine: str = "cadquery"
    freecad_version: str | None = None
    diagnostics: dict = field(default_factory=dict)


Executor = Callable[[str], Awaitable[ExecResult]]

_CAD_TOOL_NAMES = {"run_cadquery": "cadquery", "run_freecad": "freecad"}
_REQUIRE_CAD_TOOL = "required"
_FREECAD_HINT_RE = re.compile(
    r"\b(freecad|fcstd|techdraw|bim|site|community|campus|neighbou?rhood|"
    r"master\s+plan|building\s+layout|architectural\s+massing|massing)\b|"
    r"小区|社区|园区|场地|地块|总图|建筑布局|建筑群|楼栋|道路|景观"
)
_MECHANICAL_ASSEMBLY_HINT_RE = re.compile(
    r"\b(mechanical\s+assembly|landing\s+gear|nose\s+gear|main\s+gear|"
    r"wheel\s+assembly|suspension\s+assembly|hydraulic\s+(?:cylinder|actuator|strut)|"
    r"shock\s+absorber|oleo\s+strut|piston\s+rod|linkage|clevis|trunnion|"
    r"hinge\s+bracket|pivot\s+pin)\b|"
    r"机械装配|装配体|起落架|液压(?:杆|缸|作动筒)|避震|减震|连杆机构|"
    r"铰链|销轴|耳片|支柱总成|轮胎.*(?:支柱|连杆|液压)|(?:支柱|连杆|液压).*轮胎"
)


def _first_run_call(completion):
    for call in completion.tool_calls or []:
        if call.get("function", {}).get("name") in _CAD_TOOL_NAMES:
            return call
    return None


def _engine_of(call) -> str:
    name = call.get("function", {}).get("name")
    return _CAD_TOOL_NAMES.get(name, "cadquery")


def _tool_name_for(engine: str) -> str:
    return "run_freecad" if engine == "freecad" else "run_cadquery"


def _tool_choice_for_engine(engine: str) -> dict:
    return {"type": "function", "function": {"name": _tool_name_for(engine)}}


def _script_of(call) -> str | None:
    try:
        args = json.loads(call.get("function", {}).get("arguments") or "{}")
    except json.JSONDecodeError:
        return None
    script = args.get("script")
    return script if isinstance(script, str) and script.strip() else None


def sanitize_chat_history(history: list[dict] | None) -> list[dict[str, str]]:
    """Return only recent user/assistant text messages under the context budget."""
    messages: list[dict[str, str]] = []
    total_chars = 0
    for item in reversed((history or [])[-MAX_CHAT_HISTORY_MESSAGES:]):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        content = content.strip()[:MAX_CHAT_HISTORY_MESSAGE_CHARS]
        if not content:
            continue
        if total_chars + len(content) > MAX_CHAT_HISTORY_CHARS:
            break
        messages.append({"role": role, "content": content})
        total_chars += len(content)
    return list(reversed(messages))


def infer_engine_hint(prompt: str) -> str | None:
    normalized = (prompt or "").lower()
    return (
        "freecad"
        if is_site_layout_prompt(prompt)
        or _FREECAD_HINT_RE.search(normalized)
        or _MECHANICAL_ASSEMBLY_HINT_RE.search(normalized)
        else None
    )


async def run_generation(
    prompt: str,
    *,
    gateway,
    execute: Executor,
    execute_freecad: Executor | None = None,
    history: list[dict] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    tools: list[dict] | None = None,
    engine_hint: str | None = None,
    tool_choice=None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> AsyncIterator[dict]:
    tools = tools if tools is not None else MVP_TOOLS
    forced_engine = engine_hint if engine_hint in {"cadquery", "freecad"} else infer_engine_hint(prompt)
    tool_choice = (
        _tool_choice_for_engine(forced_engine)
        if forced_engine
        else (_REQUIRE_CAD_TOOL if tool_choice is None else tool_choice)
    )
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(sanitize_chat_history(history))
    messages.append({"role": "user", "content": augment_prompt_with_site_layout_plan(prompt)})

    yield {"type": "status", "message": "thinking"}

    last_error = "no CAD script was produced"

    for attempt in range(1, max_attempts + 1):
        completion = await gateway.chat_completion(
            messages, tools=tools, tool_choice=tool_choice
        )

        call = _first_run_call(completion)
        script = _script_of(call) if call else None

        if script is None:
            # Model replied without a usable tool call; nudge and retry.
            required = _tool_name_for(forced_engine) if forced_engine else "run_cadquery or run_freecad"
            last_error = f"you must call {required} with a complete script"
            if attempt < max_attempts:
                yield {"type": "retry", "attempt": attempt, "message": last_error}
                messages.append({"role": "assistant", "content": completion.content or ""})
                messages.append({"role": "user", "content": last_error})
                continue
            break

        engine = _engine_of(call)
        if forced_engine and engine != forced_engine:
            required = _tool_name_for(forced_engine)
            last_error = f"this request must use {required}; call {required} with a complete script"
            if attempt < max_attempts:
                yield {"type": "retry", "attempt": attempt, "message": last_error}
                messages.append({"role": "assistant", "content": completion.content or ""})
                messages.append({"role": "user", "content": last_error})
                continue
            break

        tool_name = _tool_name_for(engine)
        yield {
            "type": "script",
            "script": script,
            "engine": engine,
            "attempt": attempt,
            "parameters": extract_script_parameters(script),
        }

        if engine == "freecad":
            if execute_freecad is None:
                result = ExecResult(
                    ok=False,
                    engine="freecad",
                    error="FreeCAD executor unavailable",
                )
            else:
                result = await execute_freecad(script)
        else:
            result = await execute(script)
        result.engine = engine

        if result.ok:
            if result.preview_png_b64:
                yield {
                    "type": "preview",
                    "png_b64": result.preview_png_b64,
                    "engine": engine,
                    "freecad_version": result.freecad_version,
                }
            for fmt, data_b64 in result.exports.items():
                yield {
                    "type": "artifact",
                    "format": fmt,
                    "data_b64": data_b64,
                    "engine": engine,
                    "freecad_version": result.freecad_version,
                }
            yield {
                "type": "done",
                "ok": True,
                "engine": engine,
                "freecad_version": result.freecad_version,
            }
            return

        # Recoverable execution failure; feed the error back and try again.
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
                    "content": f"Execution failed:\n{last_error}\nFix the script and call {tool_name} again.",
                }
            )
            continue
        break

    yield {"type": "error", "message": last_error}
    yield {"type": "done", "ok": False}
