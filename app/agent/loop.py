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

import base64
import json
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from app.agent.building import (
    augment_prompt_with_building_plan,
    infer_building_typology,
    is_building_prompt,
)
from app.agent.site_layout import augment_prompt_with_site_layout_plan, is_site_layout_prompt
from app.agent.tools import MVP_TOOLS, SYSTEM_PROMPT
from app.cad.building_templates import building_script
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

_CAD_TOOL_NAMES = {
    "run_cadquery": "cadquery",
    "run_freecad": "freecad",
    "build_building": "freecad",
}
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
_ROLE_TEXT_PATTERNS = (
    ("setback", r"setback|control\s*line|退界|控制线"),
    ("north_axis", r"north\s*axis|northarrow|orientation|北向|指北"),
    ("elevation_benchmark", r"elevation\s*datum|benchmark|datum|标高|基准"),
    ("planning_metrics", r"planning\s*metrics?|far|coverage|指标|容积率|覆盖率"),
    ("boundary_wall", r"boundary\s*wall|perimeter\s*wall|围墙|边界墙"),
    ("plot_boundary", r"redline|red\s*line|plot\s*boundary|parcel\s*boundary|boundary|红线|用地线|地界|边界"),
    ("storey", r"storey|story|floor\s*\d+|楼层|层级"),
    ("slab", r"floor\s*slab|roof\s*slab|slab|楼板|底板"),
    ("wall", r"exterior\s*wall|interior\s*wall|wall|外墙|内墙|墙体"),
    ("window", r"window|glazing|curtain\s*wall|窗|玻璃幕墙"),
    ("door", r"door|门"),
    ("core", r"service\s*core|elevator\s*core|core|核心筒|电梯井"),
    ("stair", r"stair|staircase|楼梯"),
    ("roof", r"roof|parapet|penthouse|屋顶|女儿墙|机房"),
    ("space", r"space|room|apartment|office\s*space|房间|户型|办公室"),
    ("building_articulation", r"facade|fin|balcony|floor\s*band|story\s*band|roof\s*cap|立面|阳台|百叶|楼层线|屋顶"),
    ("entrance_system", r"entrance|gate|guard|dropoff|canopy|入口|大门|门岗|落客|雨棚"),
    ("fire_access", r"fire\s*road|fire\s*lane|消防|消防车道"),
    ("parking_underground", r"parking|garage|underground|车库|停车|地下"),
    ("water", r"lake|pond|water|river|pool|人工湖|水景|水系|湖|河|池"),
    ("play", r"playground|play|kids?|children|儿童|游乐|运动"),
    ("green", r"green|garden|park|landscape|lawn|绿化|绿地|草坪|景观|花园|公园"),
    ("road", r"road|street|drive|path|walkway|loop|道路|车道|步道|路"),
    ("amenity", r"club|clubhouse|hall|amenity|retail|lobby|会所|配套|商业"),
    ("building", r"building|tower|villa|apartment|residential|podium|house|楼|住宅|别墅|高层"),
    ("plot", r"plot|site|parcel|base|ground|terrain|slab|地块|场地|基地|底板"),
)

_SITE_ROLE_GROUPS = {
    "plot": {"plot", "plot_boundary"},
    "building": {"building", "building_articulation"},
    "water": {"water"},
    "play": {"play"},
    "amenity": {"amenity", "entrance_system"},
}

# Public aliases for eval scoring (app/evals/scoring.py) — same taxonomy the
# in-loop site-layout quality gate uses.
SITE_ROLE_GROUPS = _SITE_ROLE_GROUPS
BUILDING_ROLE_GROUPS = {
    role: {role}
    for role in ("building", "storey", "slab", "wall", "window", "door", "core", "stair", "roof", "space")
}


def scene_role_set(scene: dict) -> set[str]:
    objects = scene.get("objects") if isinstance(scene.get("objects"), list) else []
    return {_role_for_scene_object(obj) for obj in objects if isinstance(obj, dict)}


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


def _tool_choice_for_name(name: str) -> dict:
    return {"type": "function", "function": {"name": name}}


def _script_of(call) -> str | None:
    try:
        args = json.loads(call.get("function", {}).get("arguments") or "{}")
    except json.JSONDecodeError:
        return None
    if call.get("function", {}).get("name") == "build_building":
        try:
            return building_script(args)
        except (TypeError, ValueError, NotImplementedError):
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
        or is_building_prompt(prompt)
        or _FREECAD_HINT_RE.search(normalized)
        or _MECHANICAL_ASSEMBLY_HINT_RE.search(normalized)
        else None
    )


def _viewer_scene_from_result(result: ExecResult) -> dict | None:
    raw = (result.exports or {}).get("viewer_scene")
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        scene = json.loads(decoded)
    except Exception:
        return None
    return scene if isinstance(scene, dict) else None


def _role_for_scene_object(obj: dict) -> str:
    style = obj.get("style") if isinstance(obj.get("style"), dict) else {}
    explicit = style.get("semantic_role") or style.get("semanticRole")
    if explicit:
        return str(explicit).lower()
    text = " ".join(str(obj.get(key) or "") for key in ("name", "label", "type_id", "kind")).lower()
    for role, pattern in _ROLE_TEXT_PATTERNS:
        if re.search(pattern, text):
            return role
    return "generic"


def _site_requested_role_groups(prompt: str) -> set[str]:
    text = (prompt or "").lower()
    required = {"plot", "building"}
    if re.search(r"lake|pond|water|river|pool|人工湖|水景|水系|湖|河|池", text):
        required.add("water")
    if re.search(r"playground|play|kids?|children|儿童|游乐|运动", text):
        required.add("play")
    if re.search(r"club|clubhouse|hall|amenity|retail|lobby|会所|配套|商业", text):
        required.add("amenity")
    return required


def site_layout_quality_error(prompt: str, engine: str, result: ExecResult) -> str | None:
    if engine != "freecad" or not is_site_layout_prompt(prompt):
        return None
    scene = _viewer_scene_from_result(result)
    if not scene:
        return "site/community FreeCAD output must include a viewer_scene artifact with object-level geometry and style metadata"
    objects = scene.get("objects") if isinstance(scene.get("objects"), list) else []
    object_count = len(objects)
    minimum_objects = 18 if re.search(r"community|neighbou?rhood|residential\s+complex|小区|社区|高档", (prompt or "").lower()) else 10
    if object_count < minimum_objects:
        return f"site/community model is too sparse: expected at least {minimum_objects} named objects, got {object_count}"
    roles = [_role_for_scene_object(obj) for obj in objects if isinstance(obj, dict)]
    role_set = set(roles)
    missing = [
        group
        for group in sorted(_site_requested_role_groups(prompt))
        if not (role_set & _SITE_ROLE_GROUPS[group])
    ]
    if missing:
        return (
            "site/community model is missing requested role groups: "
            + ", ".join(missing)
            + ". Add named FreeCAD objects for each missing group and keep result valid/exportable."
        )
    if re.search(r"high[-\s]*rise|tower|高层|塔楼", (prompt or "").lower()) and "building_articulation" not in role_set:
        return "high-rise site model needs real named facade/floor/balcony/roof articulation objects, not only plain tower boxes"
    return None


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
    intent_prompt: str | None = None,
    tool_choice=None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> AsyncIterator[dict]:
    tools = tools if tools is not None else MVP_TOOLS
    classification_prompt = intent_prompt if intent_prompt is not None else prompt
    forced_engine = (
        engine_hint
        if engine_hint in {"cadquery", "freecad"}
        else infer_engine_hint(classification_prompt)
    )
    use_building_tool = (
        is_building_prompt(classification_prompt)
        and not is_site_layout_prompt(classification_prompt)
        and infer_building_typology(classification_prompt) == "residential_tower"
    )
    forced_tool_name = (
        "build_building"
        if use_building_tool
        else (_tool_name_for(forced_engine) if forced_engine else None)
    )
    tool_choice = (
        _tool_choice_for_name(forced_tool_name)
        if forced_tool_name
        else (_REQUIRE_CAD_TOOL if tool_choice is None else tool_choice)
    )
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(sanitize_chat_history(history))
    planned_prompt = (
        augment_prompt_with_site_layout_plan(prompt)
        if is_site_layout_prompt(classification_prompt)
        else (
            augment_prompt_with_building_plan(prompt)
            if is_building_prompt(classification_prompt)
            else prompt
        )
    )
    messages.append({"role": "user", "content": planned_prompt})

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
            required = forced_tool_name or "run_cadquery, run_freecad, or build_building"
            requirement = "a valid building specification" if required == "build_building" else "a complete script"
            last_error = f"you must call {required} with {requirement}"
            if attempt < max_attempts:
                yield {"type": "retry", "attempt": attempt, "message": last_error}
                messages.append({"role": "assistant", "content": completion.content or ""})
                messages.append({"role": "user", "content": last_error})
                continue
            break

        engine = _engine_of(call)
        called_tool_name = call.get("function", {}).get("name")
        if forced_tool_name and called_tool_name != forced_tool_name:
            requirement = (
                "a valid building specification"
                if forced_tool_name == "build_building"
                else "a complete script"
            )
            last_error = (
                f"this request must use {forced_tool_name}; "
                f"call {forced_tool_name} with {requirement}"
            )
            if attempt < max_attempts:
                yield {"type": "retry", "attempt": attempt, "message": last_error}
                messages.append({"role": "assistant", "content": completion.content or ""})
                messages.append({"role": "user", "content": last_error})
                continue
            break

        tool_name = called_tool_name or _tool_name_for(engine)
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
            quality_error = site_layout_quality_error(classification_prompt, engine, result)
            if quality_error:
                last_error = quality_error
                if attempt < max_attempts:
                    yield {"type": "retry", "attempt": attempt, "message": quality_error}
                    messages.append(
                        {"role": "assistant", "content": completion.content, "tool_calls": completion.tool_calls}
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": (
                                "Execution succeeded, but the model failed the FreeCAD site-layout quality gate:\n"
                                f"{quality_error}\n"
                                "Fix the script and call run_freecad again. Use grouped, named FreeCAD objects; "
                                "separate reference/site-boundary layers from model geometry; include real low-cost "
                                "building articulation as geometry rather than relying on viewer overlays."
                            ),
                        }
                    )
                    continue
                break
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
                "diagnostics": result.diagnostics,
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
    yield {"type": "done", "ok": False, "diagnostics": {}}
