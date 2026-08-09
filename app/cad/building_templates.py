"""Deterministic FreeCAD LOD 200 building builders.

The LLM supplies only a validated BuildingSpec. This module owns executable CAD
code so repeated prompts produce stable hierarchy, semantics, and geometry.
"""

from __future__ import annotations

from textwrap import dedent

from app.cad.building_spec import BuildingSpec


def residential_tower_script(spec: BuildingSpec | dict) -> str:
    payload = spec.model_dump() if isinstance(spec, BuildingSpec) else spec
    validated = BuildingSpec.model_validate(payload)
    if validated.typology != "residential_tower":
        raise ValueError("residential_tower_script requires typology=residential_tower")
    payload = validated.model_dump()
    template = dedent(
        """
        import json
        import math
        import FreeCAD
        import Part

        BUILDING_SPEC = __BUILDING_SPEC__
        doc = FreeCAD.newDocument("FourYiResidentialTower")
        created = []

        COLORS = {
            "slab": (0.72, 0.74, 0.76),
            "wall": (0.88, 0.86, 0.81),
            "window": (0.32, 0.56, 0.68),
            "door": (0.28, 0.31, 0.35),
            "core": (0.66, 0.68, 0.70),
            "stair": (0.74, 0.58, 0.38),
            "column": (0.68, 0.70, 0.72),
            "balcony": (0.78, 0.80, 0.82),
            "roof": (0.52, 0.55, 0.58),
        }

        IFC_TYPES = {
            "slab": "IfcSlab",
            "wall": "IfcWall",
            "window": "IfcWindow",
            "door": "IfcDoor",
            "core": "IfcWall",
            "stair": "IfcStair",
            "column": "IfcColumn",
            "balcony": "IfcSlab",
            "roof": "IfcRoof",
        }

        def add_string(obj, name, value, group="4yi BIM"):
            if name not in obj.PropertiesList:
                obj.addProperty("App::PropertyString", name, group)
            setattr(obj, name, str(value))

        def style_object(obj, role):
            color = COLORS.get(role, (0.70, 0.72, 0.74))
            add_string(obj, "FourYiColor", "#{:02x}{:02x}{:02x}".format(*[int(v * 255) for v in color]), "4yi Presentation")
            add_string(obj, "FourYiDisplayMode", "Shaded with edges", "4yi Presentation")
            if FreeCAD.GuiUp:
                try:
                    obj.ViewObject.ShapeColor = color
                    obj.ViewObject.LineColor = (0.18, 0.22, 0.27)
                    obj.ViewObject.DisplayMode = "Flat Lines"
                    if role == "window":
                        obj.ViewObject.Transparency = 35
                except Exception:
                    pass

        def add_shape(group, name, label, shape, role, storey):
            obj = doc.addObject("Part::Feature", name)
            obj.Label = label
            obj.Shape = shape
            add_string(obj, "FourYiElementType", role)
            add_string(obj, "IfcType", IFC_TYPES.get(role, "IfcBuildingElementProxy"))
            add_string(obj, "Storey", storey)
            add_string(obj, "MaterialKey", BUILDING_SPEC["materials"].get("glazing" if role == "window" else "wall", role))
            style_object(obj, role)
            group.addObject(obj)
            created.append(obj)
            return obj

        def add_group(parent, name, label, kind, elevation=None):
            group = doc.addObject("App::DocumentObjectGroup", name)
            group.Label = label
            add_string(group, "FourYiElementType", kind)
            if elevation is not None:
                if "Elevation" not in group.PropertiesList:
                    group.addProperty("App::PropertyLength", "Elevation", "4yi BIM")
                group.Elevation = float(elevation)
            parent.addObject(group)
            return group

        def compound(shapes):
            shapes = [shape for shape in shapes if shape is not None]
            return Part.makeCompound(shapes)

        def facade_components(length, origin_x, origin_y, z, height, thickness, axis, storey_index, entrance=False):
            envelope = BUILDING_SPEC["envelope"]
            openings = BUILDING_SPEC["openings"]
            bay_target = envelope["bay_spacing_mm"]
            bay_count = max(2, int(round(length / bay_target)))
            bay = length / bay_count
            pier = min(280.0, max(160.0, bay * 0.11))
            sill = min(openings["sill_height_mm"], height * 0.34)
            window_h = min(openings["window_height_mm"], height - sill - 320.0)
            lintel_h = max(220.0, height - sill - window_h)
            wall_shapes = []
            window_shapes = []
            central_bay = bay_count // 2
            for index in range(bay_count):
                start = index * bay
                is_entry = entrance and index == central_bay
                open_start = (pier if index == 0 else start + pier / 2) + 1.0
                open_end = (length - pier if index == bay_count - 1 else start + bay - pier / 2) - 1.0
                open_length = max(100.0, open_end - open_start)
                if not is_entry:
                    if axis == "x":
                        wall_shapes.append(Part.makeBox(open_length, thickness, sill, FreeCAD.Vector(origin_x + open_start, origin_y, z)))
                    else:
                        wall_shapes.append(Part.makeBox(thickness, open_length, sill, FreeCAD.Vector(origin_x, origin_y + open_start, z)))
                if axis == "x":
                    wall_shapes.append(Part.makeBox(open_length, thickness, lintel_h, FreeCAD.Vector(origin_x + open_start, origin_y, z + sill + window_h)))
                else:
                    wall_shapes.append(Part.makeBox(thickness, open_length, lintel_h, FreeCAD.Vector(origin_x, origin_y + open_start, z + sill + window_h)))
                if not is_entry:
                    glass_margin = max(25.0, open_length * 0.025)
                    if axis == "x":
                        window_shapes.append(Part.makeBox(max(100.0, open_length - 2 * glass_margin), max(40.0, thickness * 0.24), window_h, FreeCAD.Vector(origin_x + open_start + glass_margin, origin_y + thickness * 0.38, z + sill)))
                    else:
                        window_shapes.append(Part.makeBox(max(40.0, thickness * 0.24), max(100.0, open_length - 2 * glass_margin), window_h, FreeCAD.Vector(origin_x + thickness * 0.38, origin_y + open_start + glass_margin, z + sill)))
            for index in range(bay_count + 1):
                position = min(length - pier, max(0.0, index * bay - pier / 2))
                if axis == "x":
                    wall_shapes.append(Part.makeBox(pier, thickness, height, FreeCAD.Vector(origin_x + position, origin_y, z)))
                else:
                    wall_shapes.append(Part.makeBox(thickness, pier, height, FreeCAD.Vector(origin_x, origin_y + position, z)))
            return compound(wall_shapes), compound(window_shapes), central_bay, bay

        def core_shape(z, height, width, depth, wall_t, cx, cy):
            return compound([
                Part.makeBox(width - 2 * wall_t - 2, wall_t, height, FreeCAD.Vector(cx + wall_t + 1, cy, z)),
                Part.makeBox(width - 2 * wall_t - 2, wall_t, height, FreeCAD.Vector(cx + wall_t + 1, cy + depth - wall_t, z)),
                Part.makeBox(wall_t, depth, height, FreeCAD.Vector(cx, cy, z)),
                Part.makeBox(wall_t, depth, height, FreeCAD.Vector(cx + width - wall_t, cy, z)),
            ])

        def stair_shape(z, floor_height, cx, cy, core_width, core_depth):
            flight_w = max(1_000.0, core_width * 0.28)
            run = max(1_800.0, core_depth * 0.34)
            landing_y = cy + (core_depth - 900) / 2
            return compound([
                Part.makeBox(flight_w, run, max(180.0, floor_height / 2 - 180), FreeCAD.Vector(cx + 350, cy + 350, z)),
                Part.makeBox(core_width - 704, 896, 160, FreeCAD.Vector(cx + 352, landing_y + 2, z + floor_height / 2)),
                Part.makeBox(flight_w, run, max(180.0, floor_height / 2 - 180), FreeCAD.Vector(cx + core_width - flight_w - 350, cy + core_depth - run - 350, z + floor_height / 2 + 180)),
            ])

        project = doc.addObject("App::DocumentObjectGroup", "Project_4yi_CAD")
        project.Label = "4yi CAD Project"
        add_string(project, "IfcType", "IfcProject")
        site = add_group(project, "Site", "Site", "site")
        add_string(site, "IfcType", "IfcSite")
        building = add_group(site, "Building", "Residential Tower", "building")
        add_string(building, "IfcType", "IfcBuilding")
        add_string(building, "BuildingSpecJson", json.dumps(BUILDING_SPEC, ensure_ascii=False))
        add_string(building, "TargetLOD", BUILDING_SPEC["target_lod"])
        add_string(building, "Assumptions", json.dumps(BUILDING_SPEC["assumptions"], ensure_ascii=False))

        width = float(BUILDING_SPEC["footprint"]["width_mm"])
        depth = float(BUILDING_SPEC["footprint"]["depth_mm"])
        storeys = BUILDING_SPEC["storeys"]
        structure = BUILDING_SPEC["structure"]
        envelope = BUILDING_SPEC["envelope"]
        slab_t = float(storeys["slab_thickness_mm"])
        wall_t = float(envelope["wall_thickness_mm"])
        core_w = float(structure["core_width_mm"])
        core_d = float(structure["core_depth_mm"])
        core_x = (width - core_w) / 2
        core_y = (depth - core_d) / 2
        elevation = 0.0

        for floor_index in range(int(storeys["count"])):
            floor_number = floor_index + 1
            floor_height = float(storeys["ground_floor_height_mm"] if floor_index == 0 else storeys["typical_floor_height_mm"])
            storey_name = "Storey_{:02d}".format(floor_number)
            storey_group = add_group(building, storey_name, "Storey {:02d}".format(floor_number), "storey", elevation)
            add_string(storey_group, "IfcType", "IfcBuildingStorey")

            add_shape(storey_group, "Slab_{:02d}".format(floor_number), "Slab {:02d}".format(floor_number), Part.makeBox(width, depth, slab_t, FreeCAD.Vector(0, 0, elevation)), "slab", storey_name)
            add_shape(storey_group, "Core_{:02d}".format(floor_number), "Core {:02d}".format(floor_number), core_shape(elevation + slab_t, floor_height - slab_t, core_w, core_d, wall_t, core_x, core_y), "core", storey_name)
            add_shape(storey_group, "Stair_{:02d}".format(floor_number), "Stair {:02d}".format(floor_number), stair_shape(elevation + slab_t, floor_height - slab_t, core_x, core_y, core_w, core_d), "stair", storey_name)

            column_shapes = []
            for x in (wall_t, width / 2, width - wall_t - structure["column_size_mm"]):
                for y in (wall_t, depth - wall_t - structure["column_size_mm"]):
                    column_shapes.append(Part.makeBox(structure["column_size_mm"], structure["column_size_mm"], floor_height - slab_t, FreeCAD.Vector(x, y, elevation + slab_t)))
            add_shape(storey_group, "Columns_{:02d}".format(floor_number), "Columns {:02d}".format(floor_number), compound(column_shapes), "column", storey_name)

            facade_z = elevation + slab_t
            facade_h = floor_height - slab_t
            front_wall, front_windows, entry_bay, bay = facade_components(width, 0, 0, facade_z, facade_h, wall_t, "x", floor_index, entrance=floor_index == 0)
            back_wall, back_windows, _, _ = facade_components(width, 0, depth - wall_t, facade_z, facade_h, wall_t, "x", floor_index)
            left_wall, left_windows, _, _ = facade_components(depth, 0, 0, facade_z, facade_h, wall_t, "y", floor_index)
            right_wall, right_windows, _, _ = facade_components(depth, width - wall_t, 0, facade_z, facade_h, wall_t, "y", floor_index)
            for side, wall_shape, window_shape in (
                ("Front", front_wall, front_windows),
                ("Back", back_wall, back_windows),
                ("Left", left_wall, left_windows),
                ("Right", right_wall, right_windows),
            ):
                add_shape(storey_group, "Wall_{}_{:02d}".format(side, floor_number), "{} Wall {:02d}".format(side, floor_number), wall_shape, "wall", storey_name)
                add_shape(storey_group, "Window_{}_{:02d}".format(side, floor_number), "{} Windows {:02d}".format(side, floor_number), window_shape, "window", storey_name)

            if floor_index == 0:
                door_w = float(BUILDING_SPEC["openings"]["entrance_door_width_mm"])
                door_h = min(float(BUILDING_SPEC["openings"]["entrance_door_height_mm"]), facade_h - 120)
                door_x = entry_bay * bay + (bay - door_w) / 2
                add_shape(storey_group, "Door_Main_Entrance", "Main Entrance Door", Part.makeBox(door_w, 90, door_h, FreeCAD.Vector(door_x, -50, facade_z)), "door", storey_name)
                canopy_depth = min(2_400.0, depth * 0.15)
                add_shape(storey_group, "Entrance_Canopy", "Entrance Canopy", Part.makeBox(max(4_000.0, bay * 1.6), canopy_depth, 220, FreeCAD.Vector(width / 2 - max(4_000.0, bay * 1.6) / 2, -canopy_depth, facade_z + door_h + 180)), "balcony", storey_name)

            if floor_index > 0 and floor_index % 2 == 1:
                balcony_w = min(width * 0.48, 12_000.0)
                balcony_x = (width - balcony_w) / 2
                balcony_shapes = [
                    Part.makeBox(balcony_w, 1_400, 180, FreeCAD.Vector(balcony_x, -1_400, elevation + 80)),
                    Part.makeBox(balcony_w - 164, 80, 1_096, FreeCAD.Vector(balcony_x + 82, -1_400, elevation + 262)),
                    Part.makeBox(80, 1_316, 1_096, FreeCAD.Vector(balcony_x, -1_316, elevation + 262)),
                    Part.makeBox(80, 1_316, 1_096, FreeCAD.Vector(balcony_x + balcony_w - 80, -1_316, elevation + 262)),
                ]
                add_shape(storey_group, "Balcony_{:02d}".format(floor_number), "Balcony {:02d}".format(floor_number), compound(balcony_shapes), "balcony", storey_name)

            space = doc.addObject("App::FeaturePython", "Space_{:02d}_Typical".format(floor_number))
            space.Label = "Typical Floor Space {:02d}".format(floor_number)
            add_string(space, "FourYiElementType", "space")
            add_string(space, "IfcType", "IfcSpace")
            add_string(space, "Storey", storey_name)
            storey_group.addObject(space)
            elevation += floor_height

        roof_group = add_group(building, "Roof", "Roof", "roof", elevation)
        add_string(roof_group, "IfcType", "IfcRoof")
        roof_shapes = [
            Part.makeBox(width, depth, slab_t, FreeCAD.Vector(0, 0, elevation)),
            Part.makeBox(width - 2 * wall_t - 2, wall_t, BUILDING_SPEC["roof"]["parapet_height_mm"], FreeCAD.Vector(wall_t + 1, 0, elevation + slab_t + 1)),
            Part.makeBox(width - 2 * wall_t - 2, wall_t, BUILDING_SPEC["roof"]["parapet_height_mm"], FreeCAD.Vector(wall_t + 1, depth - wall_t, elevation + slab_t + 1)),
            Part.makeBox(wall_t, depth, BUILDING_SPEC["roof"]["parapet_height_mm"], FreeCAD.Vector(0, 0, elevation + slab_t + 1)),
            Part.makeBox(wall_t, depth, BUILDING_SPEC["roof"]["parapet_height_mm"], FreeCAD.Vector(width - wall_t, 0, elevation + slab_t + 1)),
        ]
        add_shape(roof_group, "Roof_Slab_Parapet", "Roof Slab and Parapet", compound(roof_shapes), "roof", "Roof")
        if BUILDING_SPEC["roof"]["plant_room"]:
            plant_w = max(4_000.0, core_w * 0.75)
            plant_d = max(3_500.0, core_d * 0.75)
            plant = Part.makeBox(plant_w, plant_d, 3_000, FreeCAD.Vector((width - plant_w) / 2, (depth - plant_d) / 2, elevation + slab_t))
            add_shape(roof_group, "Roof_Plant_Room", "Roof Plant Room", plant, "roof", "Roof")

        doc.recompute()
        result = created
        """
    )
    return template.replace("__BUILDING_SPEC__", repr(payload))


def building_script(spec: BuildingSpec | dict) -> str:
    payload = spec.model_dump() if isinstance(spec, BuildingSpec) else spec
    validated = BuildingSpec.model_validate(payload)
    if validated.typology == "residential_tower":
        return residential_tower_script(validated)
    raise NotImplementedError(f"building template not implemented for {validated.typology}")
