"""Reusable site-layout audit repair templates for FreeCAD outputs."""

from __future__ import annotations

REPAIRABLE_REQUIREMENT_CODES = {
    "missing_plot_control": "plot_control",
    "missing_planning_controls": "planning_controls",
    "missing_enclosure_system": "enclosure_system",
    "missing_entrance_system": "entrance_system",
    "missing_road_network": "road_network",
    "missing_fire_access": "fire_access",
    "missing_parking_underground": "parking_underground",
    "missing_building_massing": "building_massing",
    "missing_public_amenity": "public_amenity",
    "missing_landscape_open_space": "landscape_open_space",
    "missing_planning_metrics": "planning_metrics",
    "site_layout_object_budget_below_reference": "program_detail",
}

QUALITY_REBUILD_ISSUE_CODES = {
    "outside_plot_boundary",
    "site_layout_object_budget_above_reference",
}

# Concept-plan spacing is advisory until the project has authoritative building
# use, height, fire-code, and jurisdiction data. Keep it visible in the audit,
# but do not rebuild or reject an otherwise successful, unrelated CAD edit.
NON_BLOCKING_REVIEW_ISSUE_CODES = {
    "building_spacing_below_minimum",
}

REFERENCE_QUALITY_REPAIR_CHECK_TARGETS = {
    "component_depth": "program_detail",
    "topology_detail_faces": "program_detail",
    "topology_detail_edges": "program_detail",
    "traffic_network_depth": "road_network",
    "landscape_depth": "landscape_open_space",
    "building_articulation_depth": "program_detail",
    "amenity_depth": "public_amenity",
}

REFERENCE_QUALITY_REBUILD_CHECKS = {
    "clean_geometry",
    "object_budget",
}

CORE_REPAIR_TARGETS = tuple(
    sorted({target for target in REPAIRABLE_REQUIREMENT_CODES.values() if target != "program_detail"})
)


def _numeric(value, fallback: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number else fallback


def _sequence(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    numbers = [_numeric(item) for item in value[:3]]
    if numbers[0] is None or numbers[1] is None:
        return None
    if len(numbers) < 3 or numbers[2] is None:
        numbers = [numbers[0], numbers[1], 0.0]
    return [float(numbers[0]), float(numbers[1]), float(numbers[2])]


def site_layout_plot_frame(audit: dict | None) -> dict[str, float]:
    """Map the 100m reference repair template into the audited plot bbox."""
    bbox = audit.get("plot_bbox") if isinstance(audit, dict) else None
    bbox = bbox if isinstance(bbox, dict) else {}
    min_values = _sequence(bbox.get("min")) or [0.0, 0.0, 0.0]
    max_values = _sequence(bbox.get("max"))
    size_values = _sequence(bbox.get("size"))
    width = (
        (max_values[0] - min_values[0])
        if max_values
        else (size_values[0] if size_values else 100000.0)
    )
    depth = (
        (max_values[1] - min_values[1])
        if max_values
        else (size_values[1] if size_values else 100000.0)
    )
    width = width if width and width > 1 else 100000.0
    depth = depth if depth and depth > 1 else 100000.0
    scale_x = width / 100000.0
    scale_y = depth / 100000.0
    top_z = max_values[2] if max_values else min_values[2]
    bottom_z = min_values[2]
    if abs(top_z) <= 1000.0:
        origin_z = top_z
    elif abs(bottom_z) <= 1000.0:
        origin_z = bottom_z
    else:
        origin_z = 0.0
    return {
        "origin_x": min_values[0],
        "origin_y": min_values[1],
        "origin_z": origin_z,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "scale_z": min(scale_x, scale_y),
    }


def site_layout_audit_from_summary(document_summary: dict | None) -> dict | None:
    if not isinstance(document_summary, dict):
        return None
    audit = document_summary.get("site_layout")
    return audit if isinstance(audit, dict) else None


def site_layout_needs_repair(document_summary: dict | None) -> bool:
    audit = site_layout_audit_from_summary(document_summary)
    if not (audit and audit.get("applicable") and audit.get("status") != "pass"):
        return False
    return any(site_layout_issue_requires_repair(issue) for issue in list(audit.get("issues") or []))


def site_layout_issue_codes(audit: dict | None) -> list[str]:
    if not isinstance(audit, dict):
        return []
    codes = []
    for issue in list(audit.get("issues") or []):
        code = issue.get("code") if isinstance(issue, dict) else None
        if isinstance(code, str) and code:
            codes.append(code)
    return codes


def site_layout_reference_quality_failed_keys(issue: dict | None) -> set[str]:
    if not isinstance(issue, dict):
        return set()
    keys = set()
    for check in list(issue.get("failed_checks") or []):
        if isinstance(check, dict) and isinstance(check.get("key"), str):
            keys.add(check["key"])
    return keys


def site_layout_issue_requires_repair(issue: dict | None) -> bool:
    if not isinstance(issue, dict):
        return False
    code = issue.get("code")
    if code in NON_BLOCKING_REVIEW_ISSUE_CODES:
        return False
    if code in REPAIRABLE_REQUIREMENT_CODES or code in QUALITY_REBUILD_ISSUE_CODES:
        return True
    if code == "site_layout_reference_quality_below_reference":
        failed_keys = site_layout_reference_quality_failed_keys(issue)
        return bool(
            failed_keys.intersection(REFERENCE_QUALITY_REPAIR_CHECK_TARGETS)
            or failed_keys.intersection(REFERENCE_QUALITY_REBUILD_CHECKS)
        )
    return False


def repair_targets_from_audit(audit: dict | None) -> dict[str, bool]:
    targets = {key: False for key in REPAIRABLE_REQUIREMENT_CODES.values()}
    if site_layout_needs_rebuild(audit):
        for target in CORE_REPAIR_TARGETS:
            targets[target] = True
        targets["program_detail"] = False
        return targets
    for code in site_layout_issue_codes(audit):
        target = REPAIRABLE_REQUIREMENT_CODES.get(code)
        if target:
            targets[target] = True
    for issue in list((audit or {}).get("issues") or []):
        if not isinstance(issue, dict) or issue.get("code") != "site_layout_reference_quality_below_reference":
            continue
        for key in site_layout_reference_quality_failed_keys(issue):
            target = REFERENCE_QUALITY_REPAIR_CHECK_TARGETS.get(key)
            if target:
                targets[target] = True
    return targets


def site_layout_needs_rebuild(audit: dict | None) -> bool:
    if isinstance(audit, dict):
        component_count = _numeric(audit.get("component_count"), 0.0) or 0.0
        if 0.0 < component_count < 45.0:
            return True
    for issue in list((audit or {}).get("issues") or []):
        if not isinstance(issue, dict):
            continue
        code = issue.get("code")
        if code in QUALITY_REBUILD_ISSUE_CODES:
            return True
        if (
            code == "site_layout_reference_quality_below_reference"
            and site_layout_reference_quality_failed_keys(issue).intersection(REFERENCE_QUALITY_REBUILD_CHECKS)
        ):
            return True
    return False


def site_layout_failure_message(audit: dict | None) -> str:
    if not isinstance(audit, dict):
        return "site_layout audit failed: missing document_summary.site_layout"
    parts = [
        f"site_layout audit status={audit.get('status')}",
        f"coverage={audit.get('coverage_score')}",
    ]
    issue_summaries = []
    for issue in list(audit.get("issues") or [])[:8]:
        if not isinstance(issue, dict):
            continue
        issue_summaries.append(
            "{}: {}".format(issue.get("code") or "issue", issue.get("message") or "")
        )
    if issue_summaries:
        parts.append("issues: " + " | ".join(issue_summaries))
    return "; ".join(parts)


def site_layout_repair_script(audit: dict | None) -> str:
    targets = repair_targets_from_audit(audit)
    target_literal = repr(dict(sorted(targets.items())))
    frame_literal = repr(dict(sorted(site_layout_plot_frame(audit).items())))
    rebuild_literal = repr(site_layout_needs_rebuild(audit))
    return f"""
import math
import FreeCAD
import Part

doc = FreeCAD.ActiveDocument or FreeCAD.newDocument("SiteLayoutRepair")
NEEDS = {target_literal}
FRAME = {frame_literal}
REBUILD_EXISTING = {rebuild_literal}
objects = []

def frame_x(value):
    return FRAME["origin_x"] + float(value) * FRAME["scale_x"]

def frame_y(value):
    return FRAME["origin_y"] + float(value) * FRAME["scale_y"]

def frame_z(value):
    return FRAME["origin_z"] + float(value) * FRAME["scale_z"]

def frame_dx(value):
    return max(1.0, float(value) * FRAME["scale_x"])

def frame_dy(value):
    return max(1.0, float(value) * FRAME["scale_y"])

def frame_dz(value):
    return max(1.0, float(value) * FRAME["scale_z"])

def frame_dr(value):
    return max(1.0, float(value) * min(FRAME["scale_x"], FRAME["scale_y"]))

def template_box(x, y, z, length, width, height):
    return (
        frame_x(x),
        frame_y(y),
        frame_z(z),
        frame_dx(length),
        frame_dy(width),
        frame_dz(height),
    )

def set_style(obj, color, transparency=0):
    try:
        obj.ViewObject.ShapeColor = color
        obj.ViewObject.LineColor = tuple(max(0.0, channel * 0.45) for channel in color)
        obj.ViewObject.Transparency = transparency
    except Exception:
        pass

def set_semantic_role(obj, role):
    if not role:
        return
    try:
        if not hasattr(obj, "SemanticRole"):
            obj.addProperty("App::PropertyString", "SemanticRole", "Planning", "Semantic role")
        obj.SemanticRole = role
    except Exception:
        pass

def has_object(name):
    return any(getattr(obj, "Name", "") == name for obj in doc.Objects)

def clear_existing_site_layout():
    for obj in reversed(list(doc.Objects)):
        try:
            doc.removeObject(obj.Name)
        except Exception:
            pass

if REBUILD_EXISTING:
    clear_existing_site_layout()

def add_box(name, label, x, y, z, length, width, height, color, transparency=0, role=""):
    if has_object(name):
        return None
    x, y, z, length, width, height = template_box(x, y, z, length, width, height)
    obj = doc.addObject("Part::Box", name)
    obj.Label = label
    obj.Length = length
    obj.Width = width
    obj.Height = height
    obj.Placement.Base = FreeCAD.Vector(x, y, z)
    set_style(obj, color, transparency)
    set_semantic_role(obj, role)
    objects.append(obj)
    return obj

def add_shape(name, label, shape, color, transparency=0, role=""):
    if has_object(name):
        return None
    obj = doc.addObject("Part::Feature", name)
    obj.Label = label
    obj.Shape = shape
    set_style(obj, color, transparency)
    set_semantic_role(obj, role)
    objects.append(obj)
    return obj

def box_shape(x, y, z, length, width, height):
    x, y, z, length, width, height = template_box(x, y, z, length, width, height)
    return Part.makeBox(length, width, height, FreeCAD.Vector(x, y, z))

def cylinder_shape(cx, cy, z, radius, height):
    return Part.makeCylinder(
        frame_dr(radius),
        frame_dz(height),
        FreeCAD.Vector(frame_x(cx), frame_y(cy), frame_z(z)),
    )

def add_compound_shapes(name, label, shapes, color, transparency=0, role=""):
    if has_object(name):
        return None
    return add_shape(name, label, Part.makeCompound([shape for shape in shapes if shape]), color, transparency, role)

def add_cylinder(name, label, cx, cy, z, radius, height, color, transparency=0, role=""):
    if has_object(name):
        return None
    return add_shape(name, label, cylinder_shape(cx, cy, z, radius, height), color, transparency, role)

def add_compound(name, label, specs, color, transparency=0, role=""):
    if has_object(name):
        return None
    return add_compound_shapes(name, label, [box_shape(*spec) for spec in specs], color, transparency, role)

def add_polygon_prism(name, label, points, z, height, color, transparency=0, role=""):
    if has_object(name):
        return None
    vectors = [FreeCAD.Vector(frame_x(x), frame_y(y), frame_z(z)) for x, y in points]
    vectors.append(vectors[0])
    face = Part.Face(Part.makePolygon(vectors))
    return add_shape(name, label, face.extrude(FreeCAD.Vector(0, 0, frame_dz(height))), color, transparency, role)

def organic_lake_points(cx, cy, rx, ry, count=24):
    points = []
    for index in range(count):
        angle = math.tau * index / count
        modifier = 1.0 + 0.10 * math.sin(angle * 3.0) + 0.06 * math.cos(angle * 5.0)
        points.append((cx + math.cos(angle) * rx * modifier, cy + math.sin(angle) * ry * modifier))
    return points

def gable_roof_shape(x, y, z, length, width, height):
    x0 = frame_x(x)
    y0 = frame_y(y)
    z0 = frame_z(z)
    dx = frame_dx(length)
    dy = frame_dy(width)
    dz = frame_dz(height)
    vectors = [
        FreeCAD.Vector(x0, y0, z0),
        FreeCAD.Vector(x0 + dx, y0, z0),
        FreeCAD.Vector(x0 + dx / 2, y0, z0 + dz),
        FreeCAD.Vector(x0, y0, z0),
    ]
    return Part.Face(Part.makePolygon(vectors)).extrude(FreeCAD.Vector(0, dy, 0))

def needs(*keys):
    return any(bool(NEEDS.get(key)) for key in keys)

def add_villa_template(index, x, y, prefix="Repair"):
    add_box("%s_Villa_%d_Body" % (prefix, index), "Villa %d residential body" % index, x, y, 0, 8000, 8200, 4200, (0.76, 0.72, 0.64), 0, "residential building")
    add_villa_detail(index, x, y, prefix)

def add_villa_detail(index, x, y, prefix="Repair_Detail"):
    add_shape("%s_Villa_%d_Roof" % (prefix, index), "Villa %d roof cap" % index, gable_roof_shape(x - 700, y - 700, 4200, 9400, 9600, 2300), (0.48, 0.54, 0.60), 0, "building articulation")
    add_box("%s_Private_Garden_%d" % (prefix, index), "Private garden green landscape %d" % index, x - 1600, y - 1700, 0, 11200, 11600, 70, (0.50, 0.72, 0.45), 35, "landscape open space")

def add_villa_courtyard_detail(prefix="Repair_Detail"):
    specs = []
    for index, x in enumerate((12000, 34000, 56000, 78000), start=1):
        y = 33000
        specs.extend([
            (x - 1500, y - 1560, 80, 10900, 180, 520),
            (x - 1500, y + 9860, 80, 10900, 180, 520),
            (x - 1500, y - 1220, 80, 180, 10600, 520),
            (x + 9220, y - 1220, 80, 180, 10600, 520),
            (x + 1850, y - 5200, 90, 3400, 1500, 90),
        ])
    add_compound("%s_Private_Courtyard_Details" % prefix, "Private courtyard low walls and paving", specs, (0.62, 0.68, 0.58), 14, "site detail")

def add_tower_template(index, x, y, height, prefix="Repair"):
    add_box("%s_HighRise_Tower_%d_Body" % (prefix, index), "HighRise residential tower %d body" % index, x, y, 0, 13000, 15000, height, (0.70, 0.76, 0.83), 10, "residential building")
    add_tower_detail(index, x, y, height, prefix)

def add_tower_detail(index, x, y, height, prefix="Repair_Detail"):
    add_box("%s_HighRise_Tower_%d_Lobby_Podium" % (prefix, index), "HighRise tower %d lobby podium" % index, x - 1800, y - 1700, 0, 16600, 18400, 5200, (0.78, 0.68, 0.54), 0, "building articulation")
    add_compound("%s_HighRise_Tower_%d_Floor_Bands" % (prefix, index), "HighRise tower %d floor bands" % index, [
        (x - 250, y - 250, z, 13500, 15500, 220)
        for z in range(12000, int(height), 12000)
    ], (0.54, 0.60, 0.68), 8, "building articulation")
    add_box("%s_HighRise_Tower_%d_Roof_Cap" % (prefix, index), "HighRise tower %d roof cap" % index, x + 2400, y + 3000, height, 8200, 9000, 2800, (0.60, 0.65, 0.72), 0, "building articulation")
    add_tower_facade_detail(index, x, y, height, prefix)

def add_tower_facade_detail(index, x, y, height, prefix="Repair_Detail"):
    facade_height = max(12000, height - 9000)
    specs = []
    for offset in (2100, 5200, 8300, 11400):
        specs.append((x - 260, y + offset, 5400, 180, 360, facade_height))
        specs.append((x + 13080, y + offset, 5400, 180, 360, facade_height))
    for offset in (2500, 6200, 9900):
        specs.append((x + offset, y - 320, 5800, 420, 180, facade_height - 1800))
        specs.append((x + offset, y + 15140, 5800, 420, 180, facade_height - 1800))
    add_compound("%s_HighRise_Tower_%d_Facade_Fins" % (prefix, index), "HighRise tower %d facade fins and balcony lines" % index, specs, (0.36, 0.43, 0.51), 18, "building articulation")

def add_clubhouse_template(prefix="Repair"):
    add_box("%s_Clubhouse_Amenity_Body" % prefix, "Clubhouse amenity body", 66500, 44500, 0, 15000, 11000, 6200, (0.84, 0.61, 0.34), 0, "public amenity")
    add_clubhouse_detail(prefix)

def add_clubhouse_detail(prefix="Repair_Detail"):
    add_shape("%s_Clubhouse_Roof_Cap" % prefix, "Clubhouse roof cap", gable_roof_shape(65400, 43500, 6200, 17200, 13000, 2800), (0.50, 0.55, 0.62), 0, "building articulation")
    add_box("%s_Clubhouse_Terrace" % prefix, "Clubhouse ceremony terrace amenity", 64000, 40700, 0, 20000, 3200, 160, (0.76, 0.67, 0.52), 5, "public amenity")
    add_compound_shapes("%s_Clubhouse_Colonnade" % prefix, "Clubhouse amenity colonnade and pergola", [
        cylinder_shape(cx, 42100, 160, 360, 3600)
        for cx in (65400, 68400, 71400, 74400, 77400, 80400)
    ] + [
        box_shape(65000 + index * 3600, 41700, 3900, 2400, 420, 260)
        for index in range(5)
    ], (0.78, 0.68, 0.53), 0, "public amenity")

def add_landscape_template(prefix="Repair"):
    add_polygon_prism("%s_Water_Artificial_Lake" % prefix, "Water artificial lake", organic_lake_points(47200, 48500, 16800, 10500), 0, 100, (0.22, 0.70, 0.92), 46, "landscape open space")
    add_box("%s_Lake_Bridge" % prefix, "Lake bridge path", 41600, 48400, 80, 12500, 1700, 130, (0.48, 0.55, 0.60), 4, "traffic network")
    add_lake_and_landscape_detail(prefix)
    add_polygon_prism("%s_Central_Green_Lawn" % prefix, "Central green lawn landscape", [
        (24500, 43700), (35500, 37000), (56000, 39200),
        (62000, 51600), (46200, 61200), (27000, 57000),
    ], 0, 80, (0.50, 0.74, 0.45), 24, "landscape open space")
    add_box("%s_Children_Playground" % prefix, "Children playground", 73500, 27000, 0, 11000, 8800, 120, (0.94, 0.66, 0.30), 6, "landscape open space")
    add_compound("%s_Children_Play_Equipment" % prefix, "Children playground equipment", [
        (75500, 29200, 120, 1800, 900, 900),
        (79200, 30000, 120, 2200, 1100, 1200),
    ], (0.88, 0.47, 0.25), 0, "landscape open space")

def add_lake_and_landscape_detail(prefix="Repair_Detail"):
    add_compound("%s_Lake_Edge_Promenade" % prefix, "Lake edge promenade path and decks", [
        (29400, 41400, 120, 11200, 1300, 120),
        (53600, 41400, 120, 9600, 1300, 120),
        (30200, 58600, 120, 12600, 1300, 120),
        (52200, 57500, 120, 10300, 1300, 120),
        (29000, 43200, 120, 1300, 11200, 120),
        (61800, 44300, 120, 1300, 9500, 120),
        (33600, 46800, 140, 5200, 2400, 120),
        (55800, 49800, 140, 4600, 2200, 120),
    ], (0.58, 0.62, 0.66), 12, "traffic network landscape open space")
    tree_shapes = []
    for cx, cy in (
        (32600, 39800), (36200, 37600), (41200, 36500), (45800, 38200),
        (54800, 60600), (58600, 57500), (60800, 62200), (63200, 54800),
        (62800, 36500), (67600, 35800), (70400, 38600), (72000, 42000),
    ):
        tree_shapes.append(cylinder_shape(cx, cy, 0, 950, 900))
    add_compound_shapes("%s_Landscape_Tree_Groves" % prefix, "Landscape tree groves and shaded allees", tree_shapes, (0.30, 0.57, 0.34), 12, "landscape open space")

def add_entrance_detail(prefix="Repair_Detail"):
    add_compound("%s_Entrance_Paving_Markings" % prefix, "Entrance paving markings pedestrian path and lane control", [
        (43100, 8800, 150, 2400, 260, 70),
        (46900, 8800, 150, 2400, 260, 70),
        (50700, 8800, 150, 2400, 260, 70),
        (54500, 8800, 150, 2400, 260, 70),
        (41200, 21600, 150, 17600, 220, 70),
        (41200, 30400, 150, 17600, 220, 70),
    ], (0.86, 0.88, 0.84), 0, "entrance system traffic network")

def add_program_detail():
    add_tower_detail(1, 18000, 61500, 66000, "Repair_Detail")
    add_tower_detail(2, 62000, 61500, 72000, "Repair_Detail")
    for index, x in enumerate((12000, 34000, 56000, 78000), start=1):
        add_villa_detail(index, x, 33000, "Repair_Detail")
    add_villa_courtyard_detail("Repair_Detail")
    add_clubhouse_detail("Repair_Detail")
    add_landscape_template("Repair_Detail")
    add_entrance_detail("Repair_Detail")

def add_plot_controls():
    if needs("plot_control"):
        add_box("Repair_Plot_Redline_Boundary", "Plot redline boundary", 0, 0, -120, 100000, 100000, 120, (0.78, 0.88, 0.72), 48)
    if needs("planning_controls"):
        add_compound("Repair_Setback_Control_Lines", "Setback control lines", [
            (8480, 8000, 0, 83040, 240, 80),
            (8480, 91760, 0, 83040, 240, 80),
            (8000, 8480, 0, 240, 83040, 80),
            (91760, 8480, 0, 240, 83040, 80),
        ], (0.50, 0.66, 0.80), 35)
        add_box("Repair_North_Axis", "NorthAxis marker", 92000, 74500, 0, 900, 15500, 120, (0.18, 0.29, 0.47), 0)
        add_box("Repair_Elevation_Datum", "ElevationDatum benchmark", 4600, 90000, 0, 16000, 900, 120, (0.45, 0.49, 0.56), 0)
    if needs("planning_metrics"):
        metrics = add_box("Repair_PlanningMetrics_Panel", "PlanningMetrics FAR density green ratio", 2500, 87000, 0, 18000, 9000, 120, (0.95, 0.94, 0.86), 8)
        if metrics:
            try:
                metrics.addProperty("App::PropertyString", "FloorAreaRatio", "Planning", "FAR")
                metrics.addProperty("App::PropertyString", "BuildingDensity", "Planning", "Building density")
                metrics.addProperty("App::PropertyString", "GreenRatio", "Planning", "Green ratio")
                metrics.FloorAreaRatio = "1.80"
                metrics.BuildingDensity = "0.15"
                metrics.GreenRatio = "0.30"
            except Exception:
                pass

def add_enclosure_and_entrance():
    if needs("enclosure_system"):
        add_box("Repair_Boundary_Wall_South_West", "Boundary wall south west", 0, 0, 0, 41500, 400, 3300, (0.48, 0.53, 0.59), 8)
        add_box("Repair_Boundary_Wall_South_East", "Boundary wall south east", 58500, 0, 0, 41500, 400, 3300, (0.48, 0.53, 0.59), 8)
        add_box("Repair_Boundary_Wall_North", "Boundary wall north", 0, 99600, 0, 100000, 400, 3300, (0.48, 0.53, 0.59), 8)
        add_box("Repair_Boundary_Wall_West", "Boundary wall west", 0, 0, 0, 400, 100000, 3300, (0.48, 0.53, 0.59), 8)
        add_box("Repair_Boundary_Wall_East", "Boundary wall east", 99600, 0, 0, 400, 100000, 3300, (0.48, 0.53, 0.59), 8)
    if needs("entrance_system"):
        add_box("Repair_Main_Entrance_Gate", "Entrance gate canopy", 43000, 700, 4200, 14000, 2200, 1100, (0.77, 0.79, 0.82), 0)
        add_compound("Repair_Main_Entrance_Gate_Columns", "Entrance gate columns", [
            (42800, 700, 0, 1700, 1700, 5600),
            (55500, 700, 0, 1700, 1700, 5600),
        ], (0.84, 0.62, 0.34), 0)
        add_box("Repair_Guard_Booth", "Entrance guard booth", 59000, 4300, 0, 4200, 3100, 3400, (0.84, 0.62, 0.34), 0)
        add_box("Repair_Entrance_Dropoff", "Entrance dropoff court", 41000, 23500, 0, 18000, 7600, 120, (0.35, 0.38, 0.43), 8)
        add_entrance_detail("Repair_Detail")

def add_road_fire_and_parking():
    if needs("road_network", "fire_access"):
        add_box("Repair_Main_Road", "Main road vehicle circulation", 45500, 500, 0, 9000, 28500, 140, (0.30, 0.35, 0.41), 4)
        add_box("Repair_Fire_Road_South", "Fire road south", 10000, 22000, 0, 80000, 6000, 140, (0.30, 0.35, 0.41), 4)
        add_box("Repair_Fire_Road_North", "Fire road north", 10000, 76000, 0, 80000, 6000, 140, (0.30, 0.35, 0.41), 4)
        add_box("Repair_Fire_Road_West", "Fire road west", 10000, 22000, 0, 6000, 60000, 140, (0.30, 0.35, 0.41), 4)
        add_box("Repair_Fire_Road_East", "Fire road east", 84000, 22000, 0, 6000, 60000, 140, (0.30, 0.35, 0.41), 4)
        add_box("Repair_Pedestrian_Main_Spine", "Pedestrian path main spine", 49200, 31000, 0, 1800, 43000, 100, (0.54, 0.58, 0.62), 10)
    if needs("fire_access"):
        add_box("Repair_Fire_Ladder_Access", "Fire ladder access frontage", 17500, 52000, 0, 65500, 8200, 90, (0.42, 0.47, 0.54), 20)
        add_cylinder("Repair_Fire_Turning_Radius", "Fire turning radius marker", 50000, 29700, 0, 4500, 90, (0.42, 0.47, 0.54), 26, "fire access")
    if needs("parking_underground"):
        add_box("Repair_Underground_Garage", "Underground garage outline", 17000, 11500, -3200, 66000, 51000, 180, (0.38, 0.44, 0.52), 58)
        add_box("Repair_Basement_Ramp", "Basement ramp", 69200, 5200, 0, 9000, 15500, 320, (0.34, 0.39, 0.46), 8)
        add_box("Repair_Visitor_Parking", "Visitor parking bay", 55200, 8500, 0, 11800, 4800, 110, (0.34, 0.39, 0.46), 12)

def add_residential_and_amenity():
    if needs("building_massing"):
        for index, x in enumerate((12000, 34000, 56000, 78000), start=1):
            add_villa_template(index, x, 33000)
        add_villa_courtyard_detail("Repair_Detail")
        add_tower_template(1, 18000, 61500, 66000)
        add_tower_template(2, 62000, 61500, 72000)
    if needs("public_amenity"):
        add_clubhouse_template()

def add_landscape():
    if not needs("landscape_open_space"):
        return
    add_landscape_template()

add_plot_controls()
add_enclosure_and_entrance()
add_road_fire_and_parking()
add_residential_and_amenity()
add_landscape()
if needs("program_detail"):
    add_program_detail()

doc.recompute()
result = [obj for obj in doc.Objects if hasattr(obj, "Shape")]
"""
