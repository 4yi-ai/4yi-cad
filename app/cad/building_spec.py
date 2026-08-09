"""Validated contract between the AI building planner and FreeCAD builders.

The model is intentionally independent from FreeCAD so malformed dimensions and
unsupported building requests are rejected before a CAD worker is started.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BuildingTypology = Literal["residential_tower", "office_tower", "villa"]
TargetLod = Literal["lod200", "lod300"]


class StrictSpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FootprintSpec(StrictSpecModel):
    width_mm: float = Field(ge=6_000, le=100_000)
    depth_mm: float = Field(ge=6_000, le=100_000)
    shape: Literal["rectangle", "l_shape", "u_shape"] = "rectangle"
    corner_radius_mm: float = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_corner_radius(self):
        maximum = min(self.width_mm, self.depth_mm) / 4
        if self.corner_radius_mm > maximum:
            raise ValueError("corner_radius_mm must not exceed one quarter of the shortest side")
        return self


class StoreySpec(StrictSpecModel):
    count: int = Field(ge=1, le=80)
    ground_floor_height_mm: float = Field(ge=2_400, le=10_000)
    typical_floor_height_mm: float = Field(ge=2_400, le=6_000)
    slab_thickness_mm: float = Field(default=220, ge=120, le=600)


class StructureSpec(StrictSpecModel):
    system: Literal["concrete_frame_core", "steel_frame_core", "load_bearing_wall"]
    grid_x_mm: float = Field(ge=2_400, le=15_000)
    grid_y_mm: float = Field(ge=2_400, le=15_000)
    column_size_mm: float = Field(default=500, ge=200, le=1_500)
    core_width_mm: float = Field(ge=2_400, le=30_000)
    core_depth_mm: float = Field(ge=2_400, le=30_000)


class CirculationSpec(StrictSpecModel):
    stair_count: int = Field(default=2, ge=1, le=8)
    elevator_count: int = Field(default=2, ge=0, le=20)
    accessible_entrance: bool = True


class EnvelopeSpec(StrictSpecModel):
    wall_thickness_mm: float = Field(default=240, ge=100, le=800)
    facade_system: Literal["punched_window", "curtain_wall", "mixed"] = "punched_window"
    glazing_ratio: float = Field(default=0.4, ge=0.1, le=0.85)
    bay_spacing_mm: float = Field(default=3_000, ge=900, le=9_000)
    facade_depth_mm: float = Field(default=300, ge=100, le=2_500)


class OpeningSpec(StrictSpecModel):
    window_width_mm: float = Field(default=1_500, ge=600, le=5_000)
    window_height_mm: float = Field(default=1_800, ge=600, le=4_500)
    sill_height_mm: float = Field(default=900, ge=0, le=1_800)
    entrance_door_width_mm: float = Field(default=1_800, ge=900, le=6_000)
    entrance_door_height_mm: float = Field(default=2_400, ge=1_900, le=5_000)


class RoofSpec(StrictSpecModel):
    type: Literal["flat", "pitched", "terrace"] = "flat"
    parapet_height_mm: float = Field(default=1_200, ge=0, le=2_500)
    plant_room: bool = True


class MaterialSpec(StrictSpecModel):
    wall: str = Field(default="light_concrete", min_length=1, max_length=80)
    glazing: str = Field(default="blue_grey_glass", min_length=1, max_length=80)
    frame: str = Field(default="dark_metal", min_length=1, max_length=80)
    roof: str = Field(default="neutral_grey", min_length=1, max_length=80)


class PresentationSpec(StrictSpecModel):
    view: Literal["axonometric", "front", "top"] = "axonometric"
    camera: Literal["orthographic", "perspective"] = "perspective"
    display_mode: Literal["shaded", "shaded_with_edges", "flat_lines"] = "shaded_with_edges"
    fit_all: bool = True


class BuildingSpec(StrictSpecModel):
    schema_version: Literal["4yi-cad.building/v1"] = "4yi-cad.building/v1"
    typology: BuildingTypology
    target_lod: TargetLod = "lod200"
    units: Literal["mm"] = "mm"
    footprint: FootprintSpec
    storeys: StoreySpec
    structure: StructureSpec
    circulation: CirculationSpec
    envelope: EnvelopeSpec
    openings: OpeningSpec
    roof: RoofSpec
    materials: MaterialSpec = Field(default_factory=MaterialSpec)
    presentation: PresentationSpec = Field(default_factory=PresentationSpec)
    assumptions: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_buildable_relationships(self):
        if self.structure.core_width_mm >= self.footprint.width_mm - 2 * self.envelope.wall_thickness_mm:
            raise ValueError("core_width_mm leaves no usable floor area")
        if self.structure.core_depth_mm >= self.footprint.depth_mm - 2 * self.envelope.wall_thickness_mm:
            raise ValueError("core_depth_mm leaves no usable floor area")
        clear_height = self.storeys.typical_floor_height_mm - self.storeys.slab_thickness_mm
        if self.openings.window_height_mm + self.openings.sill_height_mm > clear_height:
            raise ValueError("window and sill exceed the typical clear storey height")
        return self

    @property
    def total_height_mm(self) -> float:
        return self.storeys.ground_floor_height_mm + max(0, self.storeys.count - 1) * self.storeys.typical_floor_height_mm


def default_building_spec(typology: BuildingTypology = "residential_tower") -> BuildingSpec:
    """Return conservative LOD 200 defaults for an underspecified request."""
    if typology == "office_tower":
        return BuildingSpec(
            typology=typology,
            footprint=FootprintSpec(width_mm=36_000, depth_mm=24_000),
            storeys=StoreySpec(count=12, ground_floor_height_mm=5_000, typical_floor_height_mm=3_900),
            structure=StructureSpec(system="steel_frame_core", grid_x_mm=8_400, grid_y_mm=8_400, core_width_mm=10_000, core_depth_mm=8_000),
            circulation=CirculationSpec(stair_count=2, elevator_count=4),
            envelope=EnvelopeSpec(facade_system="curtain_wall", glazing_ratio=0.65, bay_spacing_mm=1_400, facade_depth_mm=450),
            openings=OpeningSpec(window_width_mm=1_300, window_height_mm=2_700, sill_height_mm=300, entrance_door_width_mm=2_400, entrance_door_height_mm=3_000),
            roof=RoofSpec(type="flat", plant_room=True),
            assumptions=["12-storey office tower", "regular structural grid", "central core", "LOD 200 concept model"],
        )
    if typology == "villa":
        return BuildingSpec(
            typology=typology,
            footprint=FootprintSpec(width_mm=14_000, depth_mm=10_000),
            storeys=StoreySpec(count=2, ground_floor_height_mm=3_600, typical_floor_height_mm=3_300, slab_thickness_mm=180),
            structure=StructureSpec(system="load_bearing_wall", grid_x_mm=4_200, grid_y_mm=4_200, column_size_mm=300, core_width_mm=2_800, core_depth_mm=3_200),
            circulation=CirculationSpec(stair_count=1, elevator_count=0),
            envelope=EnvelopeSpec(facade_system="mixed", glazing_ratio=0.38, bay_spacing_mm=2_400, facade_depth_mm=600),
            openings=OpeningSpec(window_width_mm=1_800, window_height_mm=1_800, sill_height_mm=750, entrance_door_width_mm=1_500, entrance_door_height_mm=2_400),
            roof=RoofSpec(type="pitched", plant_room=False),
            assumptions=["two-storey detached villa", "load-bearing wall concept", "pitched roof", "LOD 200 concept model"],
        )
    return BuildingSpec(
        typology="residential_tower",
        footprint=FootprintSpec(width_mm=30_000, depth_mm=18_000),
        storeys=StoreySpec(count=12, ground_floor_height_mm=4_200, typical_floor_height_mm=3_000),
        structure=StructureSpec(system="concrete_frame_core", grid_x_mm=6_000, grid_y_mm=6_000, core_width_mm=8_000, core_depth_mm=6_000),
        circulation=CirculationSpec(stair_count=2, elevator_count=2),
        envelope=EnvelopeSpec(facade_system="punched_window", glazing_ratio=0.4, bay_spacing_mm=3_000, facade_depth_mm=600),
        openings=OpeningSpec(),
        roof=RoofSpec(type="terrace", plant_room=True),
        assumptions=["12-storey residential tower", "concrete frame and core", "regular punched-window facade", "LOD 200 concept model"],
    )
