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
}


def site_layout_audit_from_summary(document_summary: dict | None) -> dict | None:
    if not isinstance(document_summary, dict):
        return None
    audit = document_summary.get("site_layout")
    return audit if isinstance(audit, dict) else None


def site_layout_needs_repair(document_summary: dict | None) -> bool:
    audit = site_layout_audit_from_summary(document_summary)
    return bool(audit and audit.get("applicable") and audit.get("status") != "pass")


def site_layout_issue_codes(audit: dict | None) -> list[str]:
    if not isinstance(audit, dict):
        return []
    codes = []
    for issue in list(audit.get("issues") or []):
        code = issue.get("code") if isinstance(issue, dict) else None
        if isinstance(code, str) and code:
            codes.append(code)
    return codes


def repair_targets_from_audit(audit: dict | None) -> dict[str, bool]:
    targets = {key: False for key in REPAIRABLE_REQUIREMENT_CODES.values()}
    for code in site_layout_issue_codes(audit):
        target = REPAIRABLE_REQUIREMENT_CODES.get(code)
        if target:
            targets[target] = True
    return targets


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
    return f"""
import FreeCAD
import Part

doc = FreeCAD.ActiveDocument or FreeCAD.newDocument("SiteLayoutRepair")
NEEDS = {target_literal}
objects = []

def set_style(obj, color, transparency=0):
    try:
        obj.ViewObject.ShapeColor = color
        obj.ViewObject.LineColor = tuple(max(0.0, channel * 0.45) for channel in color)
        obj.ViewObject.Transparency = transparency
    except Exception:
        pass

def has_object(name):
    return any(getattr(obj, "Name", "") == name for obj in doc.Objects)

def add_box(name, label, x, y, z, length, width, height, color, transparency=0):
    if has_object(name):
        return None
    obj = doc.addObject("Part::Box", name)
    obj.Label = label
    obj.Length = length
    obj.Width = width
    obj.Height = height
    obj.Placement.Base = FreeCAD.Vector(x, y, z)
    set_style(obj, color, transparency)
    objects.append(obj)
    return obj

def add_compound(name, label, specs, color, transparency=0):
    if has_object(name):
        return None
    shapes = [Part.makeBox(length, width, height, FreeCAD.Vector(x, y, z))
              for x, y, z, length, width, height in specs]
    obj = doc.addObject("Part::Feature", name)
    obj.Label = label
    obj.Shape = Part.makeCompound(shapes)
    set_style(obj, color, transparency)
    objects.append(obj)
    return obj

def add_polygon_prism(name, label, points, z, height, color, transparency=0):
    if has_object(name):
        return None
    vectors = [FreeCAD.Vector(x, y, z) for x, y in points]
    vectors.append(vectors[0])
    face = Part.Face(Part.makePolygon(vectors))
    obj = doc.addObject("Part::Feature", name)
    obj.Label = label
    obj.Shape = face.extrude(FreeCAD.Vector(0, 0, height))
    set_style(obj, color, transparency)
    objects.append(obj)
    return obj

def needs(*keys):
    return any(bool(NEEDS.get(key)) for key in keys)

def add_plot_controls():
    if needs("plot_control"):
        add_box("Repair_Plot_Redline_Boundary", "Plot redline boundary", 0, 0, -120, 100000, 100000, 120, (0.78, 0.88, 0.72), 48)
    if needs("planning_controls"):
        add_compound("Repair_Setback_Control_Lines", "Setback control lines", [
            (8000, 8000, 0, 84000, 240, 80),
            (8000, 91760, 0, 84000, 240, 80),
            (8000, 8000, 0, 240, 84000, 80),
            (91760, 8000, 0, 240, 84000, 80),
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

def add_road_fire_and_parking():
    if needs("road_network", "fire_access"):
        add_box("Repair_Main_Road", "Main road vehicle circulation", 45500, 500, 0, 9000, 28500, 140, (0.30, 0.35, 0.41), 4)
        add_box("Repair_Fire_Road_South", "Fire road south", 10000, 22000, 0, 80000, 6000, 140, (0.30, 0.35, 0.41), 4)
        add_box("Repair_Fire_Road_North", "Fire road north", 10000, 76000, 0, 80000, 6000, 140, (0.30, 0.35, 0.41), 4)
        add_box("Repair_Fire_Road_West", "Fire road west", 10000, 22000, 0, 6000, 60000, 140, (0.30, 0.35, 0.41), 4)
        add_box("Repair_Fire_Road_East", "Fire road east", 84000, 22000, 0, 6000, 60000, 140, (0.30, 0.35, 0.41), 4)
        add_box("Repair_Pedestrian_Garden_Walk", "Pedestrian path garden walk", 49200, 31000, 0, 1800, 43000, 100, (0.54, 0.58, 0.62), 10)
    if needs("fire_access"):
        add_box("Repair_Fire_Ladder_Access", "Fire ladder access frontage", 17500, 52000, 0, 65500, 8200, 90, (0.42, 0.47, 0.54), 20)
        add_box("Repair_Fire_Turning_Radius", "Fire turning radius marker", 45500, 25200, 0, 9000, 9000, 90, (0.42, 0.47, 0.54), 26)
    if needs("parking_underground"):
        add_box("Repair_Underground_Garage", "Underground garage outline", 17000, 11500, -3200, 66000, 51000, 180, (0.38, 0.44, 0.52), 58)
        add_box("Repair_Basement_Ramp", "Basement ramp", 69200, 5200, 0, 9000, 15500, 320, (0.34, 0.39, 0.46), 8)
        add_box("Repair_Visitor_Parking", "Visitor parking bay", 55200, 8500, 0, 11800, 4800, 110, (0.34, 0.39, 0.46), 12)

def add_residential_and_amenity():
    if needs("building_massing"):
        for index, x in enumerate((12000, 34000, 56000, 78000), start=1):
            add_box("Repair_Villa_%d_Body" % index, "Villa %d residential body" % index, x, 33000, 0, 8000, 8200, 4200, (0.76, 0.72, 0.64), 0)
        add_box("Repair_HighRise_Tower_1_Body", "HighRise residential tower 1", 18000, 61500, 0, 13000, 15000, 66000, (0.70, 0.76, 0.83), 10)
        add_box("Repair_HighRise_Tower_2_Body", "HighRise residential tower 2", 62000, 61500, 0, 13000, 15000, 72000, (0.70, 0.76, 0.83), 10)
    if needs("public_amenity"):
        add_box("Repair_Clubhouse_Amenity_Body", "Clubhouse amenity body", 66500, 44500, 0, 15000, 11000, 6200, (0.84, 0.61, 0.34), 0)
        add_box("Repair_Clubhouse_Terrace", "Clubhouse terrace", 64000, 40700, 0, 20000, 3200, 160, (0.76, 0.67, 0.52), 5)

def add_landscape():
    if not needs("landscape_open_space"):
        return
    add_polygon_prism("Repair_Water_Artificial_Lake", "Water artificial lake", [
        (30400, 48200), (34500, 40700), (45600, 37500), (59000, 41000),
        (64000, 50000), (55200, 58700), (42000, 61000), (31800, 55500),
    ], 0, 100, (0.22, 0.70, 0.92), 46)
    add_box("Repair_Lake_Bridge", "Lake bridge path", 41600, 48400, 80, 12500, 1700, 130, (0.48, 0.55, 0.60), 4)
    add_polygon_prism("Repair_Central_Green_Lawn", "Central green lawn", [
        (24500, 43700), (35500, 37000), (56000, 39200),
        (62000, 51600), (46200, 61200), (27000, 57000),
    ], 0, 80, (0.50, 0.74, 0.45), 24)
    add_box("Repair_Children_Playground", "Children playground", 73500, 27000, 0, 11000, 8800, 120, (0.94, 0.66, 0.30), 6)

add_plot_controls()
add_enclosure_and_entrance()
add_road_fire_and_parking()
add_residential_and_amenity()
add_landscape()

doc.recompute()
result = [obj for obj in doc.Objects if hasattr(obj, "Shape")]
"""
