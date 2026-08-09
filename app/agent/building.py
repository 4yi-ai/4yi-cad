"""Single-building intent and prompt-side planning helpers."""

from __future__ import annotations

import json
import re

from app.cad.building_spec import BuildingTypology, default_building_spec

BUILDING_PROMPT_RE = re.compile(
    r"\b(building|tower|office\s*(?:building|tower)?|residential\s*(?:building|tower)?|"
    r"apartment|villa|house|hotel|school|hospital|mixed[-\s]*use|high[-\s]*rise)\b|"
    r"楼房|大楼|住宅楼|住宅塔楼|办公楼|写字楼|公寓|别墅|房子|酒店|学校|医院|商业楼|综合体|塔楼|高层"
)


def is_building_prompt(prompt: str) -> bool:
    return bool(BUILDING_PROMPT_RE.search(prompt or ""))


def infer_building_typology(prompt: str) -> BuildingTypology:
    text = (prompt or "").lower()
    if re.search(r"\b(villa|house)\b|别墅|房子", text):
        return "villa"
    if re.search(r"\b(office|commercial|mixed[-\s]*use)\b|办公|写字楼|商业|综合体", text):
        return "office_tower"
    return "residential_tower"


def building_planner_message(prompt: str) -> str:
    spec = default_building_spec(infer_building_typology(prompt))
    example = json.dumps(spec.model_dump(), ensure_ascii=False, separators=(",", ":"))
    return f"""Single-building LOD planning contract for this FreeCAD request:
- Treat the request as one editable BIM-like building, not a site master plan and not one decorated massing box.
- First resolve the request against the 4yi-cad.building/v1 contract. Use millimetres and preserve explicit user dimensions; record defaults as assumptions.
- Build a Project/Site/Building/Storey hierarchy with separate slabs, exterior walls, core, stairs, doors, windows, roof/parapet, and entrance articulation.
- Use three or more facade depth layers (wall, recessed glazing, projecting frame/balcony/canopy) so axonometric and side views read as three-dimensional.
- Prefer reusable types, links, arrays, or compounds for repeated windows. Keep geometry valid and avoid uncontrolled boolean chains.
- Default to LOD 200. Use LOD 300 only when the user supplies sufficient dimensions and program detail.
- Attach semantic properties (IfcType or FourYiElementType, Storey, MaterialKey) rather than relying on object names alone.
- Before delivery, require zero invalid shapes and zero OCC check errors. Save editable FCStd and IFC when available.

Validated default contract to override with explicit user requirements:
{example}

User request:
{prompt}"""


def augment_prompt_with_building_plan(prompt: str) -> str:
    return building_planner_message(prompt) if is_building_prompt(prompt) else prompt
