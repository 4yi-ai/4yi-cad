"""Extract and patch numeric parameters from generated CadQuery scripts."""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass

MAX_SCRIPT_PARAMETERS = 80
MAX_ABS_PARAMETER_VALUE = 1_000_000


@dataclass(frozen=True)
class ScriptParameterPatch:
    name: str
    value: float


def extract_script_parameters(script: str) -> list[dict]:
    """Return top-level numeric assignments that can be safely patched later.

    The agent prompt asks generated scripts to define dimensions as named variables
    at the top. This parser keeps that contract explicit: only simple top-level
    numeric assignments before the final `result = ...` assignment become editable.
    """
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return []

    params: list[dict] = []
    seen: set[str] = set()
    for node in tree.body:
        if _assigns_result(node):
            break
        for item in _parameters_from_node(script, node):
            if item["name"] in seen:
                continue
            params.append(item)
            seen.add(item["name"])
            if len(params) >= MAX_SCRIPT_PARAMETERS:
                return params
    return params


def apply_script_parameter_patches(
    script: str, patches: list[ScriptParameterPatch]
) -> str:
    """Patch extracted numeric assignments without asking the model to rewrite code."""
    patch_by_name = {
        patch.name: _validated_number(patch.value, patch.name)
        for patch in patches
    }
    if not patch_by_name:
        return script

    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        raise ValueError(f"script is not valid Python: {exc}") from exc

    lines = script.splitlines(keepends=True)
    replacements: list[tuple[int, int, str]] = []
    updated: set[str] = set()

    for node in tree.body:
        if _assigns_result(node):
            break
        editable = _editable_assignment(node)
        if editable is None:
            continue
        names, values = editable
        touched = [name for name in names if name in patch_by_name]
        if not touched:
            continue
        if node.lineno != getattr(node, "end_lineno", node.lineno):
            continue

        next_values = [patch_by_name.get(name, value) for name, value in zip(names, values)]
        replacements.append(
            (
                node.lineno - 1,
                node.end_lineno - 1,
                _render_assignment_line(script, node, names, next_values),
            )
        )
        updated.update(touched)

    missing = sorted(set(patch_by_name) - updated)
    if missing:
        raise ValueError(f"unknown or non-editable script parameter: {', '.join(missing)}")

    for start, end, replacement in sorted(replacements, reverse=True):
        newline = "\n" if lines[start].endswith("\n") else ""
        lines[start : end + 1] = [replacement + newline]

    return "".join(lines)


def _parameters_from_node(script: str, node: ast.stmt) -> list[dict]:
    editable = _editable_assignment(node)
    if editable is None:
        return []
    names, values = editable
    if node.lineno != getattr(node, "end_lineno", node.lineno):
        return []
    return [
        {
            "name": name,
            "label": _label_from_name(name),
            "value": value,
            "unit": _unit_for_name(name),
            "line": node.lineno,
            "group": _group_from_name(name),
        }
        for name, value in zip(names, values)
        if _source_segment(script, node)
    ]


def _editable_assignment(node: ast.stmt) -> tuple[list[str], list[float]] | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        names = _target_names(node.targets[0])
        values = _numeric_values(node.value)
        if names and values and len(names) == len(values):
            return names, values

    if isinstance(node, ast.AnnAssign):
        names = _target_names(node.target)
        values = _numeric_values(node.value) if node.value is not None else None
        if names and values and len(names) == len(values):
            return names, values

    return None


def _target_names(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Name) and _valid_parameter_name(node.id):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for item in node.elts:
            if not isinstance(item, ast.Name) or not _valid_parameter_name(item.id):
                return None
            names.append(item.id)
        return names
    return None


def _numeric_values(node: ast.AST | None) -> list[float] | None:
    if node is None:
        return None
    scalar = _numeric_literal(node)
    if scalar is not None:
        return [scalar]
    if isinstance(node, (ast.Tuple, ast.List)):
        values: list[float] = []
        for item in node.elts:
            value = _numeric_literal(item)
            if value is None:
                return None
            values.append(value)
        return values
    return None


def _numeric_literal(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        try:
            return _validated_number(float(node.value), "parameter")
        except ValueError:
            return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _numeric_literal(node.operand)
        if value is None:
            return None
        return -value if isinstance(node.op, ast.USub) else value
    return None


def _validated_number(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or abs(number) > MAX_ABS_PARAMETER_VALUE:
        raise ValueError(f"invalid numeric value for {name}")
    return number


def _render_assignment_line(
    script: str, node: ast.stmt, names: list[str], values: list[float]
) -> str:
    source = _source_segment(script, node) or ""
    indent = source[: len(source) - len(source.lstrip())]
    rendered_values = ", ".join(_format_number(value) for value in values)
    if isinstance(node, ast.AnnAssign):
        annotation = ast.get_source_segment(script, node.annotation) or "float"
        return f"{indent}{names[0]}: {annotation} = {rendered_values}"
    return f"{indent}{', '.join(names)} = {rendered_values}"


def _source_segment(script: str, node: ast.AST) -> str | None:
    return ast.get_source_segment(script, node)


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return format(value, ".12g")


def _assigns_result(node: ast.stmt) -> bool:
    if isinstance(node, ast.Assign):
        return any("result" in (_target_names(target) or []) for target in node.targets)
    if isinstance(node, ast.AnnAssign):
        return _target_names(node.target) == ["result"]
    return False


def _valid_parameter_name(name: str) -> bool:
    return name.isidentifier() and not name.startswith("_")


def _label_from_name(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _group_from_name(name: str) -> str:
    if "_" not in name:
        return "dimensions"
    return name.split("_", 1)[0]


def _unit_for_name(name: str) -> str:
    count_markers = ("count", "num", "qty", "n_", "segments", "teeth")
    if name.startswith(count_markers) or any(marker in name for marker in count_markers[:3]):
        return ""
    return "mm"
