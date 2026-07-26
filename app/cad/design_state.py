"""Structured CAD state for the browser workbench.

This is the patch-first path for normal edits: the browser sends DesignState and
CADPatch objects, the service validates them, renders a deterministic CadQuery
script, and the existing sandbox executes that script for preview/export.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ParameterName = Literal[
    "plate_length",
    "plate_width",
    "plate_thickness",
    "corner_radius",
    "hole_d",
    "boss_d",
    "boss_h",
    "center_hole_d",
]

FeatureId = Literal[
    "design",
    "body",
    "base_plate",
    "fillet_corners",
    "hole_pattern",
    "center_boss",
    "center_hole",
]

PATCH_OPS = {"update_parameter", "suppress_feature", "enable_feature", "rollback_to_feature"}

PARAMETER_LIMITS: dict[ParameterName, tuple[float, float]] = {
    "plate_length": (20, 240),
    "plate_width": (20, 180),
    "plate_thickness": (2, 40),
    "corner_radius": (0, 18),
    "hole_d": (1, 28),
    "boss_d": (4, 80),
    "boss_h": (1, 50),
    "center_hole_d": (1, 32),
}

DEFAULT_PARAMETERS: dict[ParameterName, float] = {
    "plate_length": 60,
    "plate_width": 40,
    "plate_thickness": 8,
    "corner_radius": 5,
    "hole_d": 4.5,
    "boss_d": 20,
    "boss_h": 6,
    "center_hole_d": 8,
}


class CadFeature(BaseModel):
    id: FeatureId
    parent_id: FeatureId | None = None
    name: str
    kind: Literal["document", "body", "solid", "operation", "pattern", "cut"]
    parameters: list[ParameterName] = Field(default_factory=list)
    suppressible: bool = False
    suppressed: bool = False


class DesignState(BaseModel):
    schema_version: str = "4yi-cad.design/v1"
    version: int = 1
    parameters: dict[ParameterName, float] = Field(default_factory=lambda: DEFAULT_PARAMETERS.copy())
    features: list[CadFeature] = Field(default_factory=list)
    active_feature_id: FeatureId = "center_hole"
    selected_feature_id: FeatureId = "base_plate"
    rollback_feature_id: FeatureId | None = None

    @field_validator("features")
    @classmethod
    def _default_features(cls, value: list[CadFeature]) -> list[CadFeature]:
        return value or default_features()


class CadPatch(BaseModel):
    op: Literal["update_parameter", "suppress_feature", "enable_feature", "rollback_to_feature"]
    name: ParameterName | None = None
    value: float | None = None
    feature_id: FeatureId | None = None


def default_features() -> list[CadFeature]:
    return [
        CadFeature(
            id="design",
            parent_id=None,
            name="Design",
            kind="document",
        ),
        CadFeature(
            id="body",
            parent_id="design",
            name="Body",
            kind="body",
        ),
        CadFeature(
            id="base_plate",
            parent_id="body",
            name="Base plate",
            kind="solid",
            parameters=["plate_length", "plate_width", "plate_thickness"],
        ),
        CadFeature(
            id="fillet_corners",
            parent_id="body",
            name="Fillet corners",
            kind="operation",
            parameters=["corner_radius"],
            suppressible=True,
        ),
        CadFeature(
            id="hole_pattern",
            parent_id="body",
            name="Hole pattern",
            kind="pattern",
            parameters=["hole_d"],
            suppressible=True,
        ),
        CadFeature(
            id="center_boss",
            parent_id="body",
            name="Center boss",
            kind="solid",
            parameters=["boss_d", "boss_h"],
            suppressible=True,
        ),
        CadFeature(
            id="center_hole",
            parent_id="center_boss",
            name="Center hole",
            kind="cut",
            parameters=["center_hole_d"],
            suppressible=True,
        ),
    ]


def default_design_state() -> DesignState:
    return DesignState(features=default_features())


def clamp_parameter(name: ParameterName, value: float) -> float:
    low, high = PARAMETER_LIMITS[name]
    return min(high, max(low, float(value)))


def enabled_feature_ids(state: DesignState) -> set[FeatureId]:
    active_index = len(state.features) - 1
    if state.rollback_feature_id:
        for index, feature in enumerate(state.features):
            if feature.id == state.rollback_feature_id:
                active_index = index
                break

    return {
        feature.id
        for feature in state.features[: active_index + 1]
        if not feature.suppressed
    }


def apply_patch(state: DesignState, patch: CadPatch) -> DesignState:
    data = state.model_dump()
    data["version"] = state.version + 1

    if patch.op == "update_parameter":
        if patch.name is None or patch.value is None:
            raise ValueError("update_parameter requires name and value")
        parameters = dict(state.parameters)
        parameters[patch.name] = clamp_parameter(patch.name, patch.value)
        data["parameters"] = parameters
        data["selected_feature_id"] = parameter_feature_id(patch.name)
        return DesignState(**data)

    if patch.op in {"suppress_feature", "enable_feature"}:
        if patch.feature_id is None:
            raise ValueError(f"{patch.op} requires feature_id")
        suppressed = patch.op == "suppress_feature"
        data["features"] = [
            {
                **feature.model_dump(),
                "suppressed": suppressed
                if feature.id == patch.feature_id and feature.suppressible
                else feature.suppressed,
            }
            for feature in state.features
        ]
        data["selected_feature_id"] = patch.feature_id
        return DesignState(**data)

    if patch.op == "rollback_to_feature":
        if patch.feature_id is None:
            raise ValueError("rollback_to_feature requires feature_id")
        data["rollback_feature_id"] = patch.feature_id
        data["active_feature_id"] = patch.feature_id
        data["selected_feature_id"] = patch.feature_id
        return DesignState(**data)

    raise ValueError(f"unsupported CADPatch op: {patch.op}")


def apply_patches(state: DesignState, patches: list[CadPatch]) -> DesignState:
    next_state = state
    for patch in patches:
        next_state = apply_patch(next_state, patch)
    return next_state


def parameter_feature_id(name: ParameterName) -> FeatureId:
    for feature in default_features():
        if name in feature.parameters:
            return feature.id
    return "body"


def geometry_summary(state: DesignState) -> dict:
    p = state.parameters
    enabled = enabled_feature_ids(state)
    height = p["plate_thickness"] + (p["boss_h"] if "center_boss" in enabled else 0)
    return {
        "bbox_mm": [
            round(p["plate_length"], 3),
            round(p["plate_width"], 3),
            round(height, 3),
        ],
        "features_enabled": sorted(enabled),
        "volume_estimate_mm3": round(
            p["plate_length"] * p["plate_width"] * p["plate_thickness"]
            + (
                3.14159 * (p["boss_d"] / 2) ** 2 * p["boss_h"]
                if "center_boss" in enabled
                else 0
            ),
            3,
        ),
    }


def render_cadquery_script(state: DesignState) -> str:
    p = {name: clamp_parameter(name, value) for name, value in state.parameters.items()}
    enabled = enabled_feature_ids(state)

    lines = [
        "import cadquery as cq",
        "",
    ]
    for name in DEFAULT_PARAMETERS:
        lines.append(f"{name} = {p[name]:g}")
    lines.extend(
        [
            "",
            "plate = cq.Workplane(\"XY\").box(plate_length, plate_width, plate_thickness)",
        ]
    )

    if "fillet_corners" in enabled:
        lines.extend(
            [
                "if corner_radius > 0:",
                "    plate = plate.edges(\"|Z\").fillet(corner_radius)",
            ]
        )

    if "hole_pattern" in enabled:
        lines.extend(
            [
                "hole_span_x = max(1, plate_length - 24)",
                "hole_span_y = max(1, plate_width - 24)",
                "plate = (",
                "    plate.faces(\">Z\").workplane()",
                "    .rect(hole_span_x, hole_span_y, forConstruction=True)",
                "    .vertices().hole(hole_d)",
                ")",
            ]
        )

    if "center_boss" in enabled:
        lines.extend(
            [
                "boss = (",
                "    cq.Workplane(\"XY\")",
                "    .workplane(offset=plate_thickness / 2)",
                "    .circle(boss_d / 2)",
                "    .extrude(boss_h)",
                ")",
                "result = plate.union(boss)",
            ]
        )
        if "center_hole" in enabled:
            lines.append("result = result.faces(\">Z\").workplane().hole(center_hole_d)")
    else:
        lines.append("result = plate")

    return "\n".join(lines) + "\n"

