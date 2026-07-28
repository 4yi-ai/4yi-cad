"""Deterministic FreeCAD intent parsing for common typed document edits."""

from __future__ import annotations

import re
from typing import Any

JOINT_TYPES = ("fixed", "revolute", "slider", "cylindrical", "distance", "angle")
PROJECTIONS = ("Front", "Left", "Top", "Right", "Rear", "Bottom")


def parse_freecad_intent(text: str, document_summary: dict[str, Any] | None) -> dict[str, Any]:
    source = FreeCadIntentSource(document_summary or {})
    raw = (text or "").strip()
    if not raw:
        return _miss("empty FreeCAD command")

    return (
        _parse_exact_property(raw, source)
        or _parse_constraint_value(raw, source)
        or _parse_create_sketch(raw, source)
        or _parse_attach_sketch(raw, source)
        or _parse_external_geometry(raw, source)
        or _parse_sketch_geometry(raw, source)
        or _parse_sketch_constraint(raw, source)
        or _parse_validate_sketch(raw, source)
        or _parse_solver_status(raw, source)
        or _parse_assembly(raw, source)
        or _parse_techdraw(raw, source)
        or _miss("no typed FreeCAD intent matched")
    )


class FreeCadIntentSource:
    def __init__(self, summary: dict[str, Any]) -> None:
        self.summary = summary
        self.objects = list(summary.get("objects") or [])
        self.sketches = list(summary.get("sketches") or [])
        self.assemblies = list(summary.get("assemblies") or [])
        self.techdraw_pages = list(summary.get("techdraw") or [])

    def object(self, token: str | None, *, sketch: bool = False, assembly: bool = False, page: bool = False) -> dict[str, Any] | None:
        candidates = self.objects
        if sketch:
            candidates = self.sketches
        elif assembly:
            candidates = self.assemblies
        elif page:
            candidates = self.techdraw_pages
        return _match_named(candidates, token)

    def object_selector(self, token: str | None, *, sketch: bool = False, assembly: bool = False, page: bool = False) -> dict[str, str] | None:
        obj = self.object(token, sketch=sketch, assembly=assembly, page=page)
        if obj:
            return _selector(obj)
        if token:
            return {"name": token}
        return None

    def first_shape_selector(self) -> dict[str, str] | None:
        for obj in self.objects:
            if obj.get("shape"):
                return _selector(obj)
        return _selector(self.objects[0]) if self.objects else None

    def first_sketch_selector(self) -> dict[str, str] | None:
        return _selector(self.sketches[0]) if self.sketches else None

    def first_assembly_selector(self) -> dict[str, str] | None:
        return _selector(self.assemblies[0]) if self.assemblies else None

    def first_page_selector(self) -> dict[str, str] | None:
        return _selector(self.techdraw_pages[0]) if self.techdraw_pages else None


def _parse_exact_property(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    match = re.search(r"\b([A-Za-z_][\w.-]*)\s*\.\s*([A-Za-z_]\w*)\s*=\s*(-?\d+(?:\.\d+)?)", text)
    if not match:
        match = re.search(
            r"\b(?:set|change|update|设置|修改)\s+([A-Za-z_][\w.-]*)\s+([A-Za-z_]\w*)\s+(?:to|为|成)?\s*(-?\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
    if not match:
        return None
    obj_token, prop, value_text = match.groups()
    selector = source.object_selector(obj_token)
    if selector is None:
        return None
    value = float(value_text)
    name = f"{obj_token}.{prop}"
    return _hit(
        "set_property",
        [{"op": "set_property", "selector": selector, "property": prop, "value": value}],
        name=name,
        value=value,
    )


def _parse_constraint_value(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    match = re.search(
        r"\b([A-Za-z_][\w.-]*)\s*(?:\.|:)?\s*(?:constraint|约束)\s*\[?#?(\d+)\]?\s*=\s*(-?\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    sketch_token, index_text, value_text = match.groups()
    selector = source.object_selector(sketch_token, sketch=True)
    if selector is None:
        return None
    value = float(value_text)
    index = int(index_text)
    return _hit(
        "set_constraint_value",
        [{"op": "set_constraint_value", "selector": selector, "constraint_index": index, "value": value}],
        name=f"{sketch_token}.constraint[{index}]",
        value=value,
    )


def _parse_create_sketch(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    if not re.search(r"\b(create|new|add)\s+sketch\b|创建.*草图|新建.*草图", text, re.IGNORECASE):
        return None
    name = _named_after(text, "sketch") or "Sketch"
    support = _token_after(text, r"\b(?:on|to|attach(?:ed)?\s+to)\b") or _token_after(text, r"(?:在|附着到)")
    reference = _reference(text) or "Face1"
    patch = {"op": "create_sketch", "name": name, "map_mode": "FlatFace"}
    selector = source.object_selector(support) if support else source.first_shape_selector()
    if selector:
        patch["support_selector"] = selector
        patch["reference"] = reference
    return _hit("create_sketch", [patch], name=f"createSketch:{name}")


def _parse_attach_sketch(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    if not re.search(r"\battach\s+sketch\b|\battach\s+[A-Za-z_][\w.-]*\s+to\b|附着.*草图", text, re.IGNORECASE):
        return None
    sketch_token = _token_after(text, r"\battach(?:\s+sketch)?\b") or _token_after(text, r"草图")
    support = _token_after(text, r"\bto\b") or _token_after(text, r"(?:到|至)")
    sketch_selector = source.object_selector(sketch_token, sketch=True) or source.first_sketch_selector()
    support_selector = source.object_selector(support) or source.first_shape_selector()
    if not sketch_selector or not support_selector:
        return None
    reference = _reference(text) or "Face1"
    return _hit(
        "attach_sketch",
        [{
            "op": "attach_sketch",
            "selector": sketch_selector,
            "support_selector": support_selector,
            "reference": reference,
            "map_mode": "FlatFace",
        }],
        name="attachSketch",
    )


def _parse_external_geometry(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    if not re.search(r"\bexternal\s+geometry\b|外部几何", text, re.IGNORECASE):
        return None
    sketch = _token_after(text, r"\b(?:to|into)\s+sketch\b") or _token_after(text, r"\bsketch\b")
    source_token = _token_after(text, r"\b(?:from|of)\b")
    sketch_selector = source.object_selector(sketch, sketch=True) or source.first_sketch_selector()
    source_selector = source.object_selector(source_token) or source.first_shape_selector()
    if not sketch_selector or not source_selector:
        return None
    return _hit(
        "add_external_geometry",
        [{
            "op": "add_external_geometry",
            "selector": sketch_selector,
            "source_selector": source_selector,
            "references": [_reference(text) or "Edge1"],
        }],
        name="addExternalGeometry",
    )


def _parse_sketch_geometry(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    if not re.search(r"\b(add|create|draw)\b.*\b(line|rectangle|circle|ellipse|arc|polyline)\b.*\bsketch\b|草图.*(直线|矩形|圆|椭圆|圆弧)", text, re.IGNORECASE):
        return None
    sketch = _token_after(text, r"\bsketch\b") or _token_after(text, r"草图")
    selector = source.object_selector(sketch, sketch=True) or source.first_sketch_selector()
    if not selector:
        return None
    numbers = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", text)]
    lower = text.lower()
    geometry: dict[str, Any]
    if re.search(r"\brectangle\b|矩形", text, re.IGNORECASE):
        width = numbers[0] if numbers else 10.0
        height = numbers[1] if len(numbers) > 1 else width
        geometry = {"type": "rectangle", "points": [[0, 0, 0], [width, height, 0]]}
    elif re.search(r"\bcircle\b|圆", text, re.IGNORECASE):
        radius = numbers[-1] if numbers else 5.0
        geometry = {"type": "circle", "center": [0, 0, 0], "radius": radius}
    elif re.search(r"\bellipse\b|椭圆", text, re.IGNORECASE):
        major = numbers[0] if numbers else 8.0
        minor = numbers[1] if len(numbers) > 1 else major / 2.0
        geometry = {"type": "ellipse", "center": [0, 0, 0], "major_radius": major, "minor_radius": minor}
    elif re.search(r"\barc\b|圆弧", text, re.IGNORECASE):
        radius = numbers[0] if numbers else 5.0
        geometry = {
            "type": "arc_of_circle",
            "center": [0, 0, 0],
            "radius": radius,
            "start_angle_degrees": 0,
            "end_angle_degrees": 90,
        }
    elif "polyline" in lower:
        length = numbers[0] if numbers else 10.0
        geometry = {"type": "polyline", "points": [[0, 0, 0], [length, 0, 0], [length, length, 0]]}
    else:
        length = numbers[-1] if numbers else 10.0
        geometry = {"type": "line_segment", "start": [0, 0, 0], "end": [length, 0, 0]}
    return _hit(
        "add_geometry",
        [{"op": "add_geometry", "selector": selector, "geometry": geometry}],
        name=f"SketchGeometry:{geometry['type']}",
        value=numbers[-1] if numbers else None,
    )


def _parse_sketch_constraint(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    remove = re.search(r"\bremove\b.*\bconstraint\b|删除.*约束|移除.*约束", text, re.IGNORECASE)
    add = re.search(r"\b(add|create)\b.*\bconstraint\b|添加.*约束|创建.*约束", text, re.IGNORECASE)
    if not remove and not add:
        return None
    sketch = _token_after(text, r"\bsketch\b") or _token_after(text, r"草图")
    selector = source.object_selector(sketch, sketch=True) or source.first_sketch_selector()
    if not selector:
        return None
    index = _constraint_index(text)
    if remove:
        if index is None:
            return None
        return _hit(
            "remove_constraint",
            [{"op": "remove_constraint", "selector": selector, "constraint_index": index}],
            name=f"removeConstraint:{index}",
        )
    constraint_type = _constraint_type(text)
    if not constraint_type:
        return None
    geometry_index = index if index is not None else 0
    patch: dict[str, Any] = {
        "op": "add_constraint",
        "selector": selector,
        "constraint": {"type": constraint_type, "geometry_index": geometry_index},
    }
    value = _number(text)
    if constraint_type in {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"} and value is not None:
        patch["constraint"] = {"type": constraint_type, "args": [geometry_index, value]}
    return _hit("add_constraint", [patch], name=f"addConstraint:{constraint_type}", value=value)


def _parse_validate_sketch(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    if not re.search(r"\b(validate|check)\b.*\bsketch\b|校验.*草图|检查.*草图", text, re.IGNORECASE):
        return None
    sketch = _token_after(text, r"\bsketch\b") or _token_after(text, r"草图")
    selector = source.object_selector(sketch, sketch=True) or source.first_sketch_selector()
    if not selector:
        return None
    return _hit("validate_sketch", [{"op": "validate_sketch", "selector": selector, "solve": True}], name="validateSketch")


def _parse_solver_status(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    if not re.search(r"\b(solve|solver|dof|degrees\s+of\s+freedom)\b.*\bsketch\b|草图.*求解", text, re.IGNORECASE):
        return None
    sketch = _token_after(text, r"\bsketch\b") or _token_after(text, r"草图")
    selector = source.object_selector(sketch, sketch=True) or source.first_sketch_selector()
    if not selector:
        return None
    return _hit("solver_status", [{"op": "solver_status", "selector": selector}], name="sketchSolver")


def _parse_assembly(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    if re.search(r"\bcreate\s+assembly\b|新建.*装配|创建.*装配", text, re.IGNORECASE):
        name = _named_after(text, "assembly") or "Assembly"
        return _hit("create_assembly", [{"op": "create_assembly", "name": name}], name=f"createAssembly:{name}")

    if re.search(r"\b(add|insert)\b.*\b(to|into)\b.*\bassembly\b|加入.*装配", text, re.IGNORECASE):
        assembly = _token_after(text, r"\bassembly\b") or _token_after(text, r"装配")
        part = _token_after(text, r"\b(?:add|insert)\b")
        assembly_selector = source.object_selector(assembly, assembly=True) or source.first_assembly_selector()
        part_selector = source.object_selector(part) or source.first_shape_selector()
        if assembly_selector and part_selector:
            return _hit(
                "add_part_to_assembly",
                [{"op": "add_part_to_assembly", "selector": assembly_selector, "part_selector": part_selector}],
                name="addPartToAssembly",
            )

    if re.search(r"\bsolve\b.*\bassembly\b|装配.*求解", text, re.IGNORECASE):
        assembly = _token_after(text, r"\bassembly\b") or _token_after(text, r"装配")
        selector = source.object_selector(assembly, assembly=True) or source.first_assembly_selector()
        if selector:
            return _hit("solve_assembly", [{"op": "solve_assembly", "selector": selector}], name="solveAssembly")

    joint_match = re.search(r"\b(" + "|".join(JOINT_TYPES) + r")\s+joint\b", text, re.IGNORECASE)
    if not joint_match:
        return None
    joint_type = joint_match.group(1).lower()
    assembly = _token_after(text, r"\bassembly\b")
    left, right = _between_tokens(text)
    assembly_selector = source.object_selector(assembly, assembly=True) or source.first_assembly_selector()
    part1_selector = source.object_selector(left)
    part2_selector = source.object_selector(right)
    if not assembly_selector or not part1_selector or not part2_selector:
        return None
    patch = {
        "op": "create_joint",
        "selector": assembly_selector,
        "joint_type": joint_type,
        "connector1": {"selector": part1_selector},
        "connector2": {"selector": part2_selector},
    }
    if joint_type == "distance":
        value = _number(text)
        if value is not None:
            patch["distance"] = value
    if joint_type == "angle":
        value = _number(text)
        if value is not None:
            patch["angle_degrees"] = value
    return _hit("create_joint", [patch], name=f"{joint_type}Joint", value=_number(text))


def _parse_techdraw(text: str, source: FreeCadIntentSource) -> dict[str, Any] | None:
    if not re.search(r"\btechdraw\b|工程图|技术图", text, re.IGNORECASE):
        return None
    if re.search(r"\bpdf\b|导出", text, re.IGNORECASE):
        selector = source.first_page_selector()
        patch = {"op": "export_techdraw_pdf"}
        if selector:
            patch["page_selector"] = selector
        return _hit("export_techdraw_pdf", [patch], name="TechDrawPDF")
    if re.search(r"\bdimension\b|标注|尺寸", text, re.IGNORECASE):
        page_selector = source.first_page_selector()
        if not page_selector:
            return None
        mode = "chain" if re.search(r"\bchain\b|链式", text, re.IGNORECASE) else (
            "coordinate" if re.search(r"\bcoordinate\b|坐标", text, re.IGNORECASE) else "single"
        )
        return _hit(
            "add_techdraw_dimension",
            [{
                "op": "add_techdraw_dimension",
                "page_selector": page_selector,
                "view_selector": {"name": _token_after(text, r"\bview\b") or "FrontView"},
                "dimension_type": "Distance",
                "dimension_mode": mode,
                "reference": _reference(text) or "Edge1",
            }],
            name=f"TechDrawDimension:{mode}",
        )
    if re.search(r"\b(page|sheet)\b|页面|图纸", text, re.IGNORECASE):
        name = _named_after(text, "page") or "Page"
        return _hit("create_techdraw_page", [{"op": "create_techdraw_page", "name": name}], name=f"TechDrawPage:{name}")
    if re.search(r"\bprojection\b|投影", text, re.IGNORECASE):
        page_selector = source.first_page_selector()
        source_selector = source.first_shape_selector()
        if not page_selector or not source_selector:
            return None
        return _hit(
            "add_techdraw_projection_group",
            [{
                "op": "add_techdraw_projection_group",
                "page_selector": page_selector,
                "source_selector": source_selector,
                "projection_names": [name for name in PROJECTIONS if re.search(rf"\b{name}\b", text, re.IGNORECASE)] or ["Front", "Left", "Top"],
            }],
            name="TechDrawProjection",
        )
    if re.search(r"\bview\b|视图", text, re.IGNORECASE):
        page_selector = source.first_page_selector()
        source_selector = source.first_shape_selector()
        if not page_selector or not source_selector:
            return None
        direction = [0, -1, 0]
        if re.search(r"\btop\b|顶", text, re.IGNORECASE):
            direction = [0, 0, 1]
        elif re.search(r"\bright\b|右", text, re.IGNORECASE):
            direction = [1, 0, 0]
        return _hit(
            "add_techdraw_view",
            [{"op": "add_techdraw_view", "page_selector": page_selector, "source_selector": source_selector, "direction": direction}],
            name="TechDrawView",
        )
    return None


def _hit(intent: str, patches: list[dict[str, Any]], *, name: str, value: float | None = None) -> dict[str, Any]:
    result = {"ok": True, "intent": intent, "name": name, "patches": patches, "source": "freecad_intent"}
    if value is not None:
        result["value"] = value
        result["unit"] = "mm"
    return result


def _miss(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "patches": []}


def _selector(obj: dict[str, Any]) -> dict[str, str]:
    if obj.get("name"):
        return {"name": str(obj["name"])}
    if obj.get("label"):
        return {"label": str(obj["label"])}
    return {"type_id": str(obj.get("type_id") or "")}


def _match_named(items: list[dict[str, Any]], token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    normalized = _norm(token)
    for item in items:
        if normalized in {_norm(item.get("name")), _norm(item.get("label"))}:
            return item
    return None


def _norm(value: Any) -> str:
    return re.sub(r"[\s_.:-]+", "", str(value or "")).lower()


def _number(text: str) -> float | None:
    matches = re.findall(r"-?\d+(?:\.\d+)?", text)
    return float(matches[-1]) if matches else None


def _token_after(text: str, pattern: str) -> str | None:
    match = re.search(pattern + r"\s+([A-Za-z_][\w.-]*)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _named_after(text: str, noun: str) -> str | None:
    match = re.search(rf"\b{noun}\s+([A-Za-z_][\w.-]*)", text, re.IGNORECASE)
    if not match:
        return None
    token = match.group(1)
    return None if token.lower() in {"on", "to", "from", "in", "with", "for"} else token


def _reference(text: str) -> str | None:
    match = re.search(r"\b(Face\d+|Edge\d+|Vertex\d+)\b", text, re.IGNORECASE)
    return match.group(1) if match else None


def _constraint_index(text: str) -> int | None:
    match = re.search(r"(?:constraint|约束)\s*\[?#?(\d+)\]?", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"\bgeometry\s*\[?#?(\d+)\]?|\bline\s*\[?#?(\d+)\]?", text, re.IGNORECASE)
    if match:
        return int(next(group for group in match.groups() if group is not None))
    return None


def _constraint_type(text: str) -> str | None:
    checks = [
        ("Horizontal", r"\bhorizontal\b|水平"),
        ("Vertical", r"\bvertical\b|垂直线"),
        ("Parallel", r"\bparallel\b|平行"),
        ("Perpendicular", r"\bperpendicular\b|垂直"),
        ("Coincident", r"\bcoincident\b|重合"),
        ("Tangent", r"\btangent\b|相切"),
        ("Equal", r"\bequal\b|相等"),
        ("Radius", r"\bradius\b|半径"),
        ("Diameter", r"\bdiameter\b|直径"),
        ("Angle", r"\bangle\b|角度"),
        ("DistanceX", r"\bdistancex\b|\bdistance\s*x\b|水平距离"),
        ("DistanceY", r"\bdistancey\b|\bdistance\s*y\b|垂直距离"),
        ("Distance", r"\bdistance\b|距离"),
    ]
    for value, pattern in checks:
        if re.search(pattern, text, re.IGNORECASE):
            return value
    return None


def _between_tokens(text: str) -> tuple[str | None, str | None]:
    match = re.search(r"\bbetween\s+([A-Za-z_][\w.-]*)\s+and\s+([A-Za-z_][\w.-]*)", text, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    match = re.search(r"\b([A-Za-z_][\w.-]*)\s+(?:to|with)\s+([A-Za-z_][\w.-]*)", text, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    return None, None
