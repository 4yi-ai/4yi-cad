"""Headless FreeCAD worker invoked from the existing sandbox.

Protocol: reads {"script": "..."} on stdin and writes one JSON object on stdout:
{"ok", "preview_png_b64"?, "exports": {"step": b64, "stl": b64, "fcstd": b64}, "error"?}.

The generated FreeCAD Python runs under FreeCADCmd in this worker's scrubbed
environment. P2.0 keeps this in the same container as the FastAPI app; splitting
it into a separate service is a later production hardening step.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.cad.worker import _b64_file, render_preview_isolated

FREECAD_RESULT_PREFIX = "__4YI_FREECAD_RESULT__"
FREECADCMD_CANDIDATES = ("FreeCADCmd", "freecadcmd")
FREECADCMD_MACOS_CANDIDATES = (
    "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    "/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd",
)
SUPPORTED_IMPORT_FORMATS = {"step", "stp", "iges", "igs", "brep", "fcstd"}


HARNESS = r'''
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import traceback

import FreeCAD
import Mesh
import Part
try:
    import TechDraw
except Exception:
    TechDraw = None
try:
    import Assembly  # noqa: F401
except Exception:
    Assembly = None
try:
    import JointObject
except Exception:
    JointObject = None
try:
    import Sketcher
except Exception:
    Sketcher = None
try:
    import PartDesign  # noqa: F401
except Exception:
    PartDesign = None

PREFIX = "__4YI_FREECAD_RESULT__"
PART_FEATURE_TYPES = {
    "Part::Box",
    "Part::Circle",
    "Part::Cone",
    "Part::Cylinder",
    "Part::Ellipse",
    "Part::Ellipsoid",
    "Part::Line",
    "Part::Plane",
    "Part::Point",
    "Part::Sphere",
    "Part::Torus",
}
PARTDESIGN_BODY_TYPE = "PartDesign::Body"
ASSEMBLY_TYPE = "Assembly::AssemblyObject"
ASSEMBLY_JOINT_GROUP_TYPE = "Assembly::JointGroup"
TECHDRAW_PAGE_TYPE = "TechDraw::DrawPage"
TECHDRAW_TEMPLATE_TYPE = "TechDraw::DrawSVGTemplate"
TECHDRAW_VIEW_PART_TYPE = "TechDraw::DrawViewPart"
TECHDRAW_PROJECTION_GROUP_TYPE = "TechDraw::DrawProjGroup"
TECHDRAW_PROJECTION_GROUP_ITEM_TYPE = "TechDraw::DrawProjGroupItem"
TECHDRAW_SECTION_VIEW_TYPE = "TechDraw::DrawViewSection"
TECHDRAW_DETAIL_VIEW_TYPE = "TechDraw::DrawViewDetail"
TECHDRAW_DIMENSION_TYPE = "TechDraw::DrawViewDimension"
TECHDRAW_FALLBACK_VIEWS_PROPERTY = "FourYiTechDrawFallbackViews"
TECHDRAW_FALLBACK_STATE_OBJECT = "FourYiTechDrawState"
TECHDRAW_FALLBACK_PAGES_PROPERTY = "FourYiTechDrawFallbackPages"
FEATURE_FALLBACK_STATE_OBJECT = "FourYiFeatureState"
FEATURE_FALLBACK_FEATURES_PROPERTY = "FourYiFallbackFeatures"
ASSEMBLY_FALLBACK_STATE_OBJECT = "FourYiAssemblyState"
ASSEMBLY_FALLBACK_ASSEMBLIES_PROPERTY = "FourYiFallbackAssemblies"
PARTDESIGN_FEATURE_TYPES = {
    PARTDESIGN_BODY_TYPE,
    "PartDesign::AdditiveBox",
    "PartDesign::AdditiveCone",
    "PartDesign::AdditiveCylinder",
    "PartDesign::AdditiveEllipsoid",
    "PartDesign::AdditiveLoft",
    "PartDesign::AdditivePipe",
    "PartDesign::AdditivePrism",
    "PartDesign::AdditiveSphere",
    "PartDesign::AdditiveTorus",
    "PartDesign::AdditiveWedge",
    "PartDesign::Boolean",
    "PartDesign::Chamfer",
    "PartDesign::Clone",
    "PartDesign::Draft",
    "PartDesign::Fillet",
    "PartDesign::Groove",
    "PartDesign::Helix",
    "PartDesign::Hole",
    "PartDesign::LinearPattern",
    "PartDesign::Mirrored",
    "PartDesign::Pad",
    "PartDesign::Pocket",
    "PartDesign::PolarPattern",
    "PartDesign::Revolution",
    "PartDesign::Scaled",
    "PartDesign::SubShapeBinder",
    "PartDesign::SubtractiveBox",
    "PartDesign::SubtractiveCone",
    "PartDesign::SubtractiveCylinder",
    "PartDesign::SubtractiveEllipsoid",
    "PartDesign::SubtractiveLoft",
    "PartDesign::SubtractivePipe",
    "PartDesign::SubtractivePrism",
    "PartDesign::SubtractiveSphere",
    "PartDesign::SubtractiveTorus",
    "PartDesign::SubtractiveWedge",
    "PartDesign::Thickness",
}
SUPPORTED_FEATURE_TYPES = PART_FEATURE_TYPES | PARTDESIGN_FEATURE_TYPES
SUPPORTED_ASSEMBLY_JOINT_TYPES = {
    "angle": "Angle",
    "cylindrical": "Cylindrical",
    "distance": "Distance",
    "fixed": "Fixed",
    "revolute": "Revolute",
    "slider": "Slider",
}
SUPPORTED_SKETCH_GEOMETRY_TYPES = {
    "arc",
    "arc_3_points",
    "arc_of_circle",
    "arc_of_ellipse",
    "arcofellipse",
    "arcofcircle",
    "circle",
    "ellipse",
    "line",
    "line_segment",
    "linesegment",
    "point",
    "polyline",
    "rectangle",
}
SUPPORTED_SKETCH_CONSTRAINT_TYPES = {
    "Angle",
    "Block",
    "Coincident",
    "Diameter",
    "Distance",
    "DistanceX",
    "DistanceY",
    "Equal",
    "Horizontal",
    "Parallel",
    "Perpendicular",
    "PointOnObject",
    "Radius",
    "SnellsLaw",
    "Symmetric",
    "Tangent",
    "Vertical",
}
SKETCH_CONSTRAINT_TYPE_BY_KEY = {
    constraint_type.lower(): constraint_type
    for constraint_type in SUPPORTED_SKETCH_CONSTRAINT_TYPES
}
SUPPORTED_TECHDRAW_DIMENSION_TYPES = {
    "Angle",
    "Area",
    "Diameter",
    "Distance",
    "DistanceX",
    "DistanceY",
    "Radius",
}
SUPPORTED_TECHDRAW_MEASURE_TYPES = {
    "Projected",
    "True",
}
SUPPORTED_TECHDRAW_DIMENSION_MODES = {
    "single",
    "chain",
    "coordinate",
}
SUPPORTED_TECHDRAW_PROJECTION_NAMES = {
    "Front",
    "Left",
    "Top",
    "Right",
    "Rear",
    "Bottom",
}


def emit(payload):
    print(PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def shape_volume(shape):
    try:
        return float(shape.Volume)
    except Exception:
        try:
            return float(shape.Volume())
        except Exception:
            return None


def safe_text(value, limit=240):
    try:
        text = str(value)
    except Exception:
        text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def safe_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "Value"):
        try:
            return float(value.Value)
        except Exception:
            pass
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        try:
            return [float(value.x), float(value.y), float(value.z)]
        except Exception:
            pass
    if isinstance(value, (list, tuple)):
        return [safe_value(item) for item in list(value)[:20]]
    return safe_text(value)


def object_ref(obj):
    return {
        "name": safe_text(getattr(obj, "Name", "")),
        "label": safe_text(getattr(obj, "Label", "")),
        "type_id": safe_text(getattr(obj, "TypeId", "")),
    }


def placement_summary(obj):
    try:
        placement = obj.Placement
        return placement_value_summary(placement)
    except Exception:
        return None


def placement_value_summary(placement):
    try:
        base = placement.Base
        rotation = placement.Rotation
        return {
            "base": [float(base.x), float(base.y), float(base.z)],
            "axis": [float(rotation.Axis.x), float(rotation.Axis.y), float(rotation.Axis.z)],
            "angle_degrees": float(rotation.Angle) * 180.0 / 3.141592653589793,
        }
    except Exception:
        return None


def bbox_summary(shape):
    try:
        bbox = shape.BoundBox
        return {
            "min": [float(bbox.XMin), float(bbox.YMin), float(bbox.ZMin)],
            "max": [float(bbox.XMax), float(bbox.YMax), float(bbox.ZMax)],
            "size": [float(bbox.XLength), float(bbox.YLength), float(bbox.ZLength)],
        }
    except Exception:
        return None


def bbox_center_summary(shape):
    bbox = bbox_summary(shape)
    if not bbox:
        return None
    return [
        float((bbox["min"][0] + bbox["max"][0]) / 2.0),
        float((bbox["min"][1] + bbox["max"][1]) / 2.0),
        float((bbox["min"][2] + bbox["max"][2]) / 2.0),
    ]


def stable_number(value, digits=6):
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def stable_sequence(values, digits=6):
    if not isinstance(values, (list, tuple)):
        return None
    result = []
    for value in list(values)[:3]:
        number = stable_number(value, digits=digits)
        if number is None:
            return None
        result.append(number)
    return result


def subshape_stable_signature(subshape, prefix, index=None, *, include_index=False):
    bbox = bbox_summary(subshape)
    center = bbox_center_summary(subshape) if bbox else None
    signature = {
        "kind": prefix,
        "shape_type": safe_text(getattr(subshape, "ShapeType", "")),
        "center": stable_sequence(center),
        "bbox_size": stable_sequence(bbox.get("size") if bbox else None),
    }
    if include_index and index is not None:
        signature["index_hint"] = int(index)
    try:
        signature["length"] = stable_number(getattr(subshape, "Length"))
    except Exception:
        pass
    try:
        signature["area"] = stable_number(getattr(subshape, "Area"))
    except Exception:
        pass
    try:
        signature["volume"] = stable_number(getattr(subshape, "Volume"))
    except Exception:
        pass
    return {key: value for key, value in signature.items() if value is not None and value != ""}


def subshape_stable_digest(signature):
    return hashlib.sha1(
        json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def subshape_stable_fields(subshape, prefix, index):
    signature = subshape_stable_signature(subshape, prefix, index, include_index=False)
    indexed_signature = subshape_stable_signature(subshape, prefix, index, include_index=True)
    digest = subshape_stable_digest(signature)
    legacy_digest = subshape_stable_digest(indexed_signature)
    reference = "{}{}".format(prefix, index + 1)
    stable_id = "{}:v2:{}".format(prefix.lower(), digest)
    legacy_stable_id = "{}:{}".format(prefix.lower(), legacy_digest)
    stable_reference = "{}:{}".format(reference, digest[:8])
    provenance = {
        "schema": "freecad.subelement_provenance.v1",
        "source": "shape_scan",
        "kind": prefix,
        "topological_reference": reference,
        "signature_version": 2,
        "signature_digest": digest,
        "legacy_signature_digest": legacy_digest,
        "index_hint": int(index),
    }
    return {
        "topological_reference": reference,
        "stable_id": stable_id,
        "legacy_stable_id": legacy_stable_id,
        "stable_reference": stable_reference,
        "signature": signature,
        "signature_version": 2,
        "index_hint": int(index),
        "stability": "geometric_signature_v2",
        "provenance": provenance,
        "ref_history": [
            {"scheme": "stable_id_v2", "value": stable_id, "signature_version": 2},
            {"scheme": "stable_reference_v2", "value": stable_reference, "signature_version": 2},
            {"scheme": "topological_name", "value": reference},
            {"scheme": "stable_id_v1", "value": legacy_stable_id, "signature_version": 1},
        ],
    }


def subshape_ref_summary(subshape, prefix, index):
    item = {
        "name": "{}{}".format(prefix, index + 1),
        "kind": prefix.lower(),
        "index": index,
        "reference": "{}{}".format(prefix, index + 1),
    }
    item.update(subshape_stable_fields(subshape, prefix, index))
    try:
        item["shape_type"] = safe_text(getattr(subshape, "ShapeType", ""))
    except Exception:
        pass
    bbox = bbox_summary(subshape)
    if bbox is not None:
        item["bbox"] = bbox
        item["center"] = bbox_center_summary(subshape)
    try:
        item["length"] = float(getattr(subshape, "Length"))
    except Exception:
        pass
    try:
        item["area"] = float(getattr(subshape, "Area"))
    except Exception:
        pass
    return item


def shape_subelements_summary(shape, limit=160):
    refs = {}
    for attr, prefix, key in [
        ("Faces", "Face", "faces"),
        ("Edges", "Edge", "edges"),
        ("Vertexes", "Vertex", "vertices"),
    ]:
        try:
            subshapes = list(getattr(shape, attr) or [])
        except Exception:
            subshapes = []
        refs[key] = [
            subshape_ref_summary(subshape, prefix, index)
            for index, subshape in enumerate(subshapes[:limit])
        ]
    return refs


def viewer_vector(value, placement=None):
    try:
        vector = placement.multVec(value) if placement is not None else value
    except Exception:
        vector = value
    return [float(vector.x), float(vector.y), float(vector.z)]


def bbox_from_points(points):
    coords = []
    for point in points or []:
        try:
            values = [float(point[0]), float(point[1]), float(point[2])]
        except Exception:
            continue
        if all(math.isfinite(value) for value in values):
            coords.append(values)
    if not coords:
        return None
    mins = [min(point[index] for point in coords) for index in range(3)]
    maxs = [max(point[index] for point in coords) for index in range(3)]
    return {
        "min": mins,
        "max": maxs,
        "size": [maxs[index] - mins[index] for index in range(3)],
    }


def bbox_center_from_summary(bbox):
    try:
        mins = [float(value) for value in bbox["min"][:3]]
        maxs = [float(value) for value in bbox["max"][:3]]
    except Exception:
        return None
    return [(mins[index] + maxs[index]) / 2 for index in range(3)]


def merge_bbox_summaries(bboxes):
    valid = []
    for bbox in bboxes or []:
        try:
            mins = [float(value) for value in bbox["min"][:3]]
            maxs = [float(value) for value in bbox["max"][:3]]
        except Exception:
            continue
        if all(math.isfinite(value) for value in [*mins, *maxs]):
            valid.append((mins, maxs))
    if not valid:
        return None
    mins = [min(item[0][index] for item in valid) for index in range(3)]
    maxs = [max(item[1][index] for item in valid) for index in range(3)]
    return {
        "min": mins,
        "max": maxs,
        "size": [maxs[index] - mins[index] for index in range(3)],
    }


def viewer_object_placement(obj):
    try:
        return obj.getGlobalPlacement()
    except Exception:
        pass
    try:
        return obj.Placement
    except Exception:
        return None


def viewer_face_mesh(face, index, placement=None, tolerance=0.8, triangle_limit=800):
    try:
        vertices, facets = face.tessellate(float(tolerance))
    except Exception:
        return None
    if not vertices or not facets:
        return None
    facets = list(facets)[:triangle_limit]
    used = sorted({int(vertex_index) for facet in facets for vertex_index in list(facet)[:3]})
    remap = {old: new for new, old in enumerate(used)}
    coords = [viewer_vector(vertices[old], placement) for old in used]
    triangles = []
    for facet in facets:
        values = list(facet)[:3]
        if len(values) != 3:
            continue
        try:
            triangles.append([remap[int(values[0])], remap[int(values[1])], remap[int(values[2])]])
        except Exception:
            continue
    if not coords or not triangles:
        return None
    bbox = bbox_from_points(coords)
    return {
        "reference": "Face{}".format(index + 1),
        "kind": "Face",
        "index": index,
        **subshape_stable_fields(face, "Face", index),
        "bbox": bbox,
        "center": bbox_center_from_summary(bbox),
        "vertices": coords,
        "triangles": triangles,
    }


def viewer_edge_geometry(edge, index, placement=None, point_limit=32):
    points = []
    try:
        raw_points = list(edge.discretize(Number=max(2, int(point_limit))))
    except Exception:
        raw_points = []
    if not raw_points:
        try:
            raw_points = [vertex.Point for vertex in list(getattr(edge, "Vertexes") or [])]
        except Exception:
            raw_points = []
    for point in raw_points[:point_limit]:
        try:
            points.append(viewer_vector(point, placement))
        except Exception:
            continue
    if len(points) < 2:
        return None
    bbox = bbox_from_points(points)
    item = {
        "reference": "Edge{}".format(index + 1),
        "kind": "Edge",
        "index": index,
        **subshape_stable_fields(edge, "Edge", index),
        "bbox": bbox,
        "center": bbox_center_from_summary(bbox),
        "points": points,
    }
    try:
        item["length"] = float(edge.Length)
    except Exception:
        pass
    return item


def viewer_vertex_geometry(vertex, index, placement=None):
    try:
        point = viewer_vector(vertex.Point, placement)
    except Exception:
        try:
            point = viewer_vector(vertex, placement)
        except Exception:
            return None
    bbox = bbox_from_points([point])
    return {
        "reference": "Vertex{}".format(index + 1),
        "kind": "Vertex",
        "index": index,
        **subshape_stable_fields(vertex, "Vertex", index),
        "bbox": bbox,
        "center": point,
        "point": point,
    }


def viewer_object_scene(obj, face_limit=96, total_triangle_limit=12000):
    shape = getattr(obj, "Shape", obj)
    if shape_summary(shape) is None:
        return None
    placement = viewer_object_placement(obj) if hasattr(obj, "Placement") else None
    faces = []
    edges = []
    vertices = []
    triangle_count = 0
    try:
        subfaces = list(shape.Faces or [])
    except Exception:
        subfaces = []
    for index, face in enumerate(subfaces[:face_limit]):
        if triangle_count >= total_triangle_limit:
            break
        remaining = max(1, total_triangle_limit - triangle_count)
        mesh = viewer_face_mesh(face, index, placement=placement, triangle_limit=min(800, remaining))
        if not mesh:
            continue
        triangle_count += len(mesh["triangles"])
        faces.append(mesh)
    try:
        subedges = list(shape.Edges or [])
    except Exception:
        subedges = []
    for index, edge in enumerate(subedges[:160]):
        item = viewer_edge_geometry(edge, index, placement=placement)
        if item:
            edges.append(item)
    try:
        subvertices = list(shape.Vertexes or [])
    except Exception:
        subvertices = []
    for index, vertex in enumerate(subvertices[:240]):
        item = viewer_vertex_geometry(vertex, index, placement=placement)
        if item:
            vertices.append(item)
    if not (faces or edges or vertices):
        return None
    merged_bbox = merge_bbox_summaries(
        [face.get("bbox") for face in faces]
        + [edge.get("bbox") for edge in edges]
        + [vertex.get("bbox") for vertex in vertices]
    )
    ref = object_ref(obj) if hasattr(obj, "Name") else {
        "name": "Shape",
        "label": "Shape",
        "type_id": safe_text(getattr(shape, "ShapeType", "")),
    }
    ref.update({
        "bbox": merged_bbox or bbox_summary(shape),
        "faces": faces,
        "edges": edges,
        "vertices": vertices,
        "face_count": len(faces),
        "edge_count": len(edges),
        "vertex_count": len(vertices),
        "triangle_count": triangle_count,
    })
    return ref


def viewer_scene_summary(doc, objects):
    scene_objects = []
    triangle_count = 0
    for obj in list(objects or []):
        item = viewer_object_scene(obj)
        if not item:
            continue
        scene_objects.append(item)
        triangle_count += int(item.get("triangle_count") or 0)
    scene_bbox = merge_bbox_summaries(item.get("bbox") for item in scene_objects)
    return {
        "schema": "freecad.viewer_scene.v1",
        "units": "mm",
        "document": object_ref(doc) if doc is not None else None,
        "bbox": scene_bbox,
        "objects": scene_objects,
        "object_count": len(scene_objects),
        "triangle_count": triangle_count,
    }


def export_viewer_scene_json(doc, objects, out_dir):
    try:
        scene = viewer_scene_summary(doc, objects)
    except Exception:
        return None
    if not scene.get("objects"):
        return None
    path = os.path.join(out_dir, "viewer-scene.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(scene, fh, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return None
    return path


def topology_count(shape, attr):
    try:
        return len(getattr(shape, attr))
    except Exception:
        return None


def shape_check_summary(shape):
    try:
        result = shape.check(True)
        return {
            "ok": True if result is None else bool(result),
            "result": safe_value(result),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": safe_text(exc),
        }


def shape_tolerance_summary(shape):
    values = []
    for attr in ["Vertexes", "Edges", "Faces"]:
        try:
            subshapes = list(getattr(shape, attr) or [])
        except Exception:
            subshapes = []
        for subshape in subshapes:
            try:
                tolerance = getattr(subshape, "Tolerance", None)
            except Exception:
                tolerance = None
            if isinstance(tolerance, (int, float)):
                values.append(float(tolerance))
    if not values:
        return None
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
    }


def shape_failure_class(info):
    if info.get("valid") is False:
        return "invalid_shape"
    check = info.get("check") or {}
    if check.get("ok") is False:
        return "occ_check_failed"
    volume = info.get("volume")
    solid_count = info.get("solid_count")
    if volume is not None and float(volume) <= 1e-9:
        return "zero_volume" if solid_count else "no_solid"
    return None


def shape_summary(shape):
    try:
        if hasattr(shape, "isNull") and shape.isNull():
            return None
    except Exception:
        return None
    try:
        shape_type = safe_text(getattr(shape, "ShapeType", ""))
    except Exception:
        return None
    valid = None
    try:
        valid = bool(shape.isValid())
    except Exception:
        pass
    item = {
        "shape_type": shape_type,
        "valid": valid,
        "bbox": bbox_summary(shape),
        "volume": shape_volume(shape),
        "solid_count": topology_count(shape, "Solids"),
        "shell_count": topology_count(shape, "Shells"),
        "face_count": topology_count(shape, "Faces"),
        "edge_count": topology_count(shape, "Edges"),
        "vertex_count": topology_count(shape, "Vertexes"),
        "check": shape_check_summary(shape),
        "tolerance": shape_tolerance_summary(shape),
        "subelements": shape_subelements_summary(shape),
    }
    item["failure_class"] = shape_failure_class(item)
    return item


def property_summary(obj):
    props = []
    for name in list(getattr(obj, "PropertiesList", []))[:80]:
        try:
            prop_type = obj.getTypeIdOfProperty(name)
        except Exception:
            prop_type = ""
        try:
            group = obj.getGroupOfProperty(name)
        except Exception:
            group = ""
        if name in {"Shape", "Mesh", "Proxy", "ExpressionEngine"}:
            value = "<omitted>"
        else:
            try:
                value = safe_value(getattr(obj, name))
            except Exception:
                value = "<unreadable>"
        props.append({
            "name": name,
            "type": safe_text(prop_type),
            "group": safe_text(group),
            "value": value,
        })
    return props


def constraint_summary(constraint, index):
    item = {
        "index": index,
        "type": safe_text(getattr(constraint, "Type", type(constraint).__name__)),
        "name": safe_text(getattr(constraint, "Name", "")),
        "status": "driving",
    }
    active = None
    driving = None
    for key, attr in [("active", "IsActive"), ("driving", "IsDriving"), ("virtual_space", "InVirtualSpace")]:
        if hasattr(constraint, attr):
            try:
                item[key] = bool(getattr(constraint, attr))
                if key == "active":
                    active = item[key]
                if key == "driving":
                    driving = item[key]
            except Exception:
                pass
    if active is False:
        item["status"] = "disabled"
    elif driving is False:
        item["status"] = "reference"
    for attr in [
        "Value",
        "First",
        "FirstPos",
        "Second",
        "SecondPos",
        "Third",
        "ThirdPos",
        "AlignmentType",
    ]:
        if hasattr(constraint, attr):
            try:
                item[attr[0].lower() + attr[1:]] = safe_value(getattr(constraint, attr))
            except Exception:
                pass
    return item


def sketch_constraint_index_values(value):
    result = []
    if isinstance(value, bool) or value is None:
        return result
    if isinstance(value, int):
        return [value]
    if isinstance(value, float) and value.is_integer():
        return [int(value)]
    if isinstance(value, dict):
        for item in value.values():
            result.extend(sketch_constraint_index_values(item))
        return result
    if isinstance(value, (list, tuple, set)):
        for item in value:
            result.extend(sketch_constraint_index_values(item))
        return result
    return result


def sketch_constraint_indexes(sketch, method_names):
    indexes = []
    for method_name in method_names:
        method = getattr(sketch, method_name, None)
        if not callable(method):
            continue
        try:
            indexes.extend(sketch_constraint_index_values(method()))
        except Exception:
            continue
    return sorted({int(index) for index in indexes if int(index) >= 0})


def sketch_edit_mode_summary(sketch, constraints, solver):
    diagnostics = []
    state = "unknown"
    degrees_of_freedom = solver.get("degrees_of_freedom")
    fully_constrained = solver.get("fully_constrained")
    solver_status = solver.get("solver_status")
    conflicting_indexes = sketch_constraint_indexes(
        sketch,
        [
            "getConflictingConstraints",
            "getConflicting",
            "getConflicts",
        ],
    )
    redundant_indexes = sketch_constraint_indexes(
        sketch,
        [
            "getRedundantConstraints",
            "getRedundant",
            "getRedundants",
            "getPartiallyRedundantConstraints",
        ],
    )
    malformed_indexes = sketch_constraint_indexes(
        sketch,
        [
            "getMalformedConstraints",
            "getMalformed",
        ],
    )
    if solver.get("error"):
        state = "solver_error"
        diagnostics.append({
            "severity": "error",
            "code": "solver_error",
            "message": safe_text(solver.get("error"), 300),
        })
    elif fully_constrained is True or degrees_of_freedom == 0:
        state = "fully_constrained"
    elif degrees_of_freedom is not None and degrees_of_freedom > 0:
        state = "under_constrained"
        diagnostics.append({
            "severity": "warning",
            "code": "under_constrained",
            "message": "{} degrees of freedom remain".format(degrees_of_freedom),
            "degrees_of_freedom": degrees_of_freedom,
        })
    status_text = safe_text(solver_status, 300).lower()
    if "redundant" in status_text:
        diagnostics.append({
            "severity": "warning",
            "code": "redundant_constraint",
            "message": "Sketch solver reports redundant constraints",
            "constraint_indexes": redundant_indexes,
        })
    if "conflict" in status_text or "inconsistent" in status_text or "over" in status_text:
        diagnostics.append({
            "severity": "error",
            "code": "conflicting_constraint",
            "message": "Sketch solver reports conflicting constraints",
            "constraint_indexes": conflicting_indexes,
        })
        state = "conflicting"
    if conflicting_indexes:
        diagnostics.append({
            "severity": "error",
            "code": "conflicting_constraint_indexes",
            "message": "{} conflicting constraints".format(len(conflicting_indexes)),
            "constraint_indexes": conflicting_indexes,
        })
        state = "conflicting"
    if redundant_indexes:
        diagnostics.append({
            "severity": "warning",
            "code": "redundant_constraint_indexes",
            "message": "{} redundant constraints".format(len(redundant_indexes)),
            "constraint_indexes": redundant_indexes,
        })
    if malformed_indexes:
        diagnostics.append({
            "severity": "error",
            "code": "malformed_constraint_indexes",
            "message": "{} malformed constraints".format(len(malformed_indexes)),
            "constraint_indexes": malformed_indexes,
        })
        state = "conflicting"
    disabled = [item for item in list(constraints or []) if item.get("status") == "disabled"]
    reference = [item for item in list(constraints or []) if item.get("status") == "reference"]
    if disabled:
        diagnostics.append({
            "severity": "info",
            "code": "disabled_constraints",
            "message": "{} disabled constraints".format(len(disabled)),
            "constraint_indexes": [item.get("index") for item in disabled],
        })
    return {
        "state": state,
        "degrees_of_freedom": degrees_of_freedom,
        "fully_constrained": fully_constrained,
        "constraint_count": len(constraints or []),
        "reference_constraint_count": len(reference),
        "conflicting_constraints": conflicting_indexes,
        "redundant_constraints": redundant_indexes,
        "malformed_constraints": malformed_indexes,
        "diagnostics": diagnostics,
        "diagnostic_count": len(diagnostics),
    }


def annotate_sketch_constraints(constraints, edit_mode):
    conflicting = set(edit_mode.get("conflicting_constraints") or [])
    redundant = set(edit_mode.get("redundant_constraints") or [])
    malformed = set(edit_mode.get("malformed_constraints") or [])
    annotated = []
    for constraint in list(constraints or []):
        item = dict(constraint)
        index = item.get("index")
        if index in conflicting or index in malformed:
            item["solver_status"] = "conflicting"
            item["diagnostic_severity"] = "error"
        elif index in redundant:
            item["solver_status"] = "redundant"
            item["diagnostic_severity"] = "warning"
        annotated.append(item)
    return annotated


def vector_summary(value):
    if value is None:
        return None
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        try:
            return [float(value.x), float(value.y), float(value.z)]
        except Exception:
            return None
    return None


def sketch_geometry_summary(geometry, index, sketch=None):
    type_id = safe_text(getattr(geometry, "TypeId", type(geometry).__name__))
    item = {
        "index": index,
        "type": type_id,
    }
    if sketch is not None and hasattr(sketch, "getConstruction"):
        try:
            item["construction"] = bool(sketch.getConstruction(index))
        except Exception:
            pass
    for key, attr in [
        ("start", "StartPoint"),
        ("end", "EndPoint"),
        ("center", "Center"),
        ("location", "Location"),
        ("point", "Point"),
        ("axis", "Axis"),
    ]:
        if hasattr(geometry, attr):
            try:
                value = vector_summary(getattr(geometry, attr))
                if value is not None:
                    item[key] = value
            except Exception:
                pass
    for key, attr in [
        ("radius", "Radius"),
        ("major_radius", "MajorRadius"),
        ("minor_radius", "MinorRadius"),
        ("first_parameter", "FirstParameter"),
        ("last_parameter", "LastParameter"),
    ]:
        if hasattr(geometry, attr):
            try:
                item[key] = float(getattr(geometry, attr))
            except Exception:
                pass
    return item


def sketch_attachment_support_summary(sketch):
    values = []
    try:
        support = list(getattr(sketch, "AttachmentSupport", []) or [])
    except Exception:
        support = []
    for item in support:
        try:
            obj, subelements = item
            values.append({
                "object": object_ref(obj),
                "subelements": [safe_text(value) for value in list(subelements or [])],
            })
        except Exception:
            values.append(safe_value(item))
    return values


def sketch_external_geometry_summary(sketch):
    values = []
    try:
        external_geometry = list(getattr(sketch, "ExternalGeometry", []) or [])
    except Exception:
        external_geometry = []
    for index, item in enumerate(external_geometry):
        try:
            obj, subelements = item
            values.append({
                "index": index,
                "object": object_ref(obj),
                "subelements": [safe_text(value) for value in list(subelements or [])],
            })
        except Exception:
            values.append({"index": index, "value": safe_value(item)})
    return values


def sketch_solver_status(sketch, *, solve=False):
    ensure_sketch(sketch)
    status = None
    error = None
    if solve and hasattr(sketch, "solve"):
        try:
            status = safe_value(sketch.solve())
        except Exception as exc:
            error = safe_text(exc)
    degrees_of_freedom = None
    fully_constrained = None
    try:
        if hasattr(sketch, "getDegreesOfFreedom"):
            degrees_of_freedom = int(sketch.getDegreesOfFreedom())
    except Exception:
        pass
    try:
        fully_constrained = bool(getattr(sketch, "FullyConstrained"))
    except Exception:
        if degrees_of_freedom is not None:
            fully_constrained = degrees_of_freedom == 0
    result = {
        "solver_status": status,
        "degrees_of_freedom": degrees_of_freedom,
        "fully_constrained": fully_constrained,
    }
    if error is not None:
        result["error"] = error
    return result


def sketch_summary(obj):
    type_id = safe_text(getattr(obj, "TypeId", ""))
    if "Sketcher::SketchObject" not in type_id and not hasattr(obj, "Constraints"):
        return None
    geometries = []
    try:
        geometries = [
            sketch_geometry_summary(geometry, index, obj)
            for index, geometry in enumerate(list(obj.Geometry))
        ]
    except Exception:
        pass
    constraints = []
    try:
        constraints = [
            constraint_summary(constraint, index)
            for index, constraint in enumerate(list(obj.Constraints))
        ]
    except Exception:
        pass
    external_geometry = sketch_external_geometry_summary(obj)
    geometry_count = None
    try:
        geometry_count = len(obj.Geometry)
    except Exception:
        pass
    solver = sketch_solver_status(obj, solve=False)
    degrees_of_freedom = solver.get("degrees_of_freedom")
    fully_constrained = solver.get("fully_constrained")
    edit_mode = sketch_edit_mode_summary(obj, constraints, solver)
    constraints = annotate_sketch_constraints(constraints, edit_mode)
    return {
        "name": safe_text(getattr(obj, "Name", "")),
        "label": safe_text(getattr(obj, "Label", "")),
        "type_id": type_id,
        "placement": placement_summary(obj),
        "map_mode": safe_text(getattr(obj, "MapMode", "")),
        "attachment_support": sketch_attachment_support_summary(obj),
        "geometry_count": geometry_count,
        "geometry": geometries[:200],
        "external_geometry_count": len(external_geometry),
        "external_geometry": external_geometry[:200],
        "constraint_count": len(constraints),
        "degrees_of_freedom": degrees_of_freedom,
        "fully_constrained": fully_constrained,
        "solver": solver,
        "edit_mode": edit_mode,
        "constraints": constraints[:120],
    }


def object_summary(obj):
    shape = getattr(obj, "Shape", None)
    item = object_ref(obj)
    item.update({
        "placement": placement_summary(obj),
        "in_list": [object_ref(parent) for parent in list(getattr(obj, "InList", []))[:40]],
        "out_list": [object_ref(child) for child in list(getattr(obj, "OutList", []))[:40]],
        "properties": property_summary(obj),
    })
    if shape is not None:
        shape_info = shape_summary(shape)
        if shape_info is not None:
            item["shape"] = shape_info
    sketch = sketch_summary(obj)
    if sketch is not None:
        item["sketch"] = sketch
    return item


def feature_kind(obj):
    type_id = safe_text(getattr(obj, "TypeId", ""))
    if type_id == ASSEMBLY_TYPE:
        return "assembly"
    if type_id == ASSEMBLY_JOINT_GROUP_TYPE:
        return "assembly_joint_group"
    if is_assembly_joint(obj):
        return "assembly_joint"
    if type_id == PARTDESIGN_BODY_TYPE:
        return "partdesign_body"
    if type_id.startswith("PartDesign::"):
        return "partdesign_feature"
    if type_id in PART_FEATURE_TYPES:
        return "part_primitive"
    if type_id.startswith("Part::"):
        return "part_feature"
    if "Sketcher::SketchObject" in type_id:
        return "sketch"
    if is_techdraw_page(obj):
        return "techdraw_page"
    if is_techdraw_dimension(obj):
        return "techdraw_dimension"
    if is_techdraw_projection_group(obj):
        return "techdraw_projection_group"
    if is_techdraw_projection_group_item(obj):
        return "techdraw_projection_item"
    if is_techdraw_section_view(obj):
        return "techdraw_section_view"
    if is_techdraw_detail_view(obj):
        return "techdraw_detail_view"
    if is_techdraw_view(obj):
        return "techdraw_view"
    if "TechDraw" in type_id:
        return "techdraw"
    if "Assembly" in type_id or "Assembly" in safe_text(getattr(obj, "Name", "")):
        return "assembly"
    return "document_object"


def is_assembly_object(obj):
    return safe_text(getattr(obj, "TypeId", "")) == ASSEMBLY_TYPE


def is_assembly_joint_group(obj):
    return safe_text(getattr(obj, "TypeId", "")) == ASSEMBLY_JOINT_GROUP_TYPE


def is_assembly_joint(obj):
    return hasattr(obj, "JointType") or hasattr(obj, "ObjectToGround")


def is_techdraw_page(obj):
    return safe_text(getattr(obj, "TypeId", "")) == TECHDRAW_PAGE_TYPE


def is_techdraw_view(obj):
    type_id = safe_text(getattr(obj, "TypeId", ""))
    return type_id.startswith("TechDraw::DrawView") or type_id in {
        TECHDRAW_PROJECTION_GROUP_TYPE,
        TECHDRAW_PROJECTION_GROUP_ITEM_TYPE,
    }


def is_techdraw_part_view(obj):
    return safe_text(getattr(obj, "TypeId", "")) == TECHDRAW_VIEW_PART_TYPE


def is_techdraw_projection_group(obj):
    return safe_text(getattr(obj, "TypeId", "")) == TECHDRAW_PROJECTION_GROUP_TYPE


def is_techdraw_projection_group_item(obj):
    return safe_text(getattr(obj, "TypeId", "")) == TECHDRAW_PROJECTION_GROUP_ITEM_TYPE


def is_techdraw_section_view(obj):
    return safe_text(getattr(obj, "TypeId", "")) == TECHDRAW_SECTION_VIEW_TYPE


def is_techdraw_detail_view(obj):
    return safe_text(getattr(obj, "TypeId", "")) == TECHDRAW_DETAIL_VIEW_TYPE


def is_techdraw_dimension(obj):
    return safe_text(getattr(obj, "TypeId", "")) == TECHDRAW_DIMENSION_TYPE


def object_identity(obj):
    try:
        return obj.Name
    except Exception:
        return id(obj)


def object_list_refs(values, known=None):
    refs = []
    seen = set()
    for item in list(values or []):
        if known is not None and object_identity(item) not in known:
            continue
        name = object_identity(item)
        if name in seen:
            continue
        seen.add(name)
        refs.append(object_ref(item))
    return refs


def feature_tree_node(obj, known):
    children = []
    group_children = []
    try:
        group_children = object_list_refs(getattr(obj, "Group", []), known)
    except Exception:
        group_children = []
    out_children = []
    try:
        out_children = object_list_refs(getattr(obj, "OutList", []), known)
    except Exception:
        out_children = []
    child_names = set()
    for child in group_children + out_children:
        if child["name"] in child_names:
            continue
        child_names.add(child["name"])
        children.append(child)

    tip = None
    try:
        if getattr(obj, "Tip", None) is not None:
            tip = object_ref(obj.Tip)
    except Exception:
        tip = None

    return {
        "object": object_ref(obj),
        "kind": feature_kind(obj),
        "parents": object_list_refs(getattr(obj, "InList", []), known),
        "children": children,
        "tip": tip,
        "placement": placement_summary(obj),
    }


def feature_tree_summary(doc, objects):
    known = {object_identity(obj) for obj in objects}
    nodes = [feature_tree_node(obj, known) for obj in objects]
    roots = []
    for obj in objects:
        parents = []
        try:
            parents = [parent for parent in list(getattr(obj, "InList", [])) if object_identity(parent) in known]
        except Exception:
            parents = []
        if not parents:
            roots.append(object_ref(obj))
    return {
        "roots": roots,
        "nodes": nodes,
    }


def quantity_summary(value):
    if value is None:
        return None
    if hasattr(value, "Value"):
        try:
            return float(value.Value)
        except Exception:
            pass
    try:
        return float(value)
    except Exception:
        return safe_value(value)


def normalize_vector_values(values):
    try:
        x, y, z = [float(value) for value in list(values)[:3]]
    except Exception:
        return None
    length = math.sqrt(x * x + y * y + z * z)
    if not math.isfinite(length) or length <= 1e-12:
        return None
    return [x / length, y / length, z / length]


def vector_from_points(start, end):
    try:
        return normalize_vector_values([
            float(end[0]) - float(start[0]),
            float(end[1]) - float(start[1]),
            float(end[2]) - float(start[2]),
        ])
    except Exception:
        return None


def vector_dot_values(left, right):
    try:
        return sum(float(left[index]) * float(right[index]) for index in range(3))
    except Exception:
        return None


def vector_cross_values(left, right):
    try:
        return [
            float(left[1]) * float(right[2]) - float(left[2]) * float(right[1]),
            float(left[2]) * float(right[0]) - float(left[0]) * float(right[2]),
            float(left[0]) * float(right[1]) - float(left[1]) * float(right[0]),
        ]
    except Exception:
        return None


def connector_helper_axis(primary):
    primary = normalize_vector_values(primary)
    if not primary:
        return [1.0, 0.0, 0.0]
    candidates = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return min(
        candidates,
        key=lambda candidate: abs(vector_dot_values(primary, candidate) or 0.0),
    )


def connector_lcs_axes(primary_axis, *, primary_role="z"):
    primary = normalize_vector_values(primary_axis)
    if not primary:
        return None
    helper = connector_helper_axis(primary)
    if primary_role == "x":
        x_axis = primary
        z_axis = normalize_vector_values(vector_cross_values(x_axis, helper))
        if not z_axis:
            return None
        y_axis = normalize_vector_values(vector_cross_values(z_axis, x_axis))
    else:
        z_axis = primary
        x_axis = normalize_vector_values(vector_cross_values(helper, z_axis))
        if not x_axis:
            return None
        y_axis = normalize_vector_values(vector_cross_values(z_axis, x_axis))
    if not (x_axis and y_axis and z_axis):
        return None
    return {
        "x_axis": x_axis,
        "y_axis": y_axis,
        "z_axis": z_axis,
        "primary_role": primary_role,
        "orientation_quality": "complete",
    }


def connector_lcs_summary(origin, primary_axis=None, *, primary_role="z"):
    stable_origin = stable_sequence(origin)
    if stable_origin is None:
        return None
    axes = connector_lcs_axes(primary_axis, primary_role=primary_role)
    if not axes:
        return {
            "origin": stable_origin,
            "orientation_quality": "origin_only",
        }
    result = {"origin": stable_origin}
    result.update(axes)
    return result


def subshape_by_reference(obj, reference):
    text = safe_text(reference, 160)
    match = re.match(r"^(Face|Edge|Vertex)(\d+)$", text, re.IGNORECASE)
    if not match:
        return None, "", None
    prefix = match.group(1).capitalize()
    index = int(match.group(2)) - 1
    attr = subelement_attr_for_prefix(prefix)
    shape = getattr(obj, "Shape", None)
    if shape is None or not attr:
        return None, prefix, index
    try:
        values = list(getattr(shape, attr) or [])
    except Exception:
        values = []
    if index < 0 or index >= len(values):
        return None, prefix, index
    return values[index], prefix, index


def subshape_connector_frame(obj, reference):
    subshape, prefix, index = subshape_by_reference(obj, reference)
    if subshape is None:
        center = None
        try:
            center = bbox_center_summary(getattr(obj, "Shape", None))
        except Exception:
            center = None
        return {
            "reference": safe_text(reference, 160),
            "origin": center,
            "frame_quality": "missing_reference",
            "source": "fallback_object_center" if center else "unresolved",
            "lcs": connector_lcs_summary(center),
        }
    fields = subshape_stable_fields(subshape, prefix, index)
    origin = bbox_center_summary(subshape)
    frame = {
        "reference": safe_text(reference, 160),
        "origin": origin,
        "stable_id": fields.get("stable_id"),
        "legacy_stable_id": fields.get("legacy_stable_id"),
        "stable_reference": fields.get("stable_reference"),
        "signature": fields.get("signature"),
        "provenance": fields.get("provenance"),
        "ref_history": fields.get("ref_history"),
        "connector_type": prefix.lower(),
    }
    if prefix == "Face":
        normal = None
        try:
            umin, umax, vmin, vmax = subshape.ParameterRange
            normal = vector_summary(subshape.normalAt((float(umin) + float(umax)) / 2.0, (float(vmin) + float(vmax)) / 2.0))
        except Exception:
            pass
        frame.update({
            "primary_axis": normalize_vector_values(normal) if normal else None,
            "frame_quality": "orientation_complete" if normal else "origin_only",
            "source": "face_normal" if normal else "face_center",
        })
        frame["local_axes"] = connector_lcs_axes(frame.get("primary_axis"), primary_role="z")
        frame["lcs"] = connector_lcs_summary(origin, frame.get("primary_axis"), primary_role="z")
    elif prefix == "Edge":
        points = []
        try:
            points = [vector_summary(point) for point in list(subshape.discretize(Number=2))]
        except Exception:
            try:
                points = [vector_summary(vertex.Point) for vertex in list(getattr(subshape, "Vertexes") or [])]
            except Exception:
                points = []
        tangent = vector_from_points(points[0], points[-1]) if len(points) >= 2 else None
        frame.update({
            "primary_axis": tangent,
            "frame_quality": "orientation_complete" if tangent else "origin_only",
            "source": "edge_tangent" if tangent else "edge_center",
        })
        frame["local_axes"] = connector_lcs_axes(frame.get("primary_axis"), primary_role="x")
        frame["lcs"] = connector_lcs_summary(origin, frame.get("primary_axis"), primary_role="x")
    else:
        point = None
        try:
            point = vector_summary(subshape.Point)
        except Exception:
            pass
        frame.update({
            "origin": point or origin,
            "primary_axis": None,
            "frame_quality": "origin_only",
            "source": "vertex_point",
        })
        frame["local_axes"] = None
        frame["lcs"] = connector_lcs_summary(frame.get("origin"))
    return frame


def assembly_connector_frame_summary(obj, subelements):
    values = [safe_text(value, 160) for value in list(subelements or []) if safe_text(value, 160)]
    reference = values[0] if values else ""
    if not reference:
        return {
            "object": object_ref(obj),
            "reference": "",
            "frame_quality": "object_only",
            "origin": bbox_center_summary(getattr(obj, "Shape", None)) if hasattr(obj, "Shape") else None,
            "source": "object_center",
            "lcs": connector_lcs_summary(bbox_center_summary(getattr(obj, "Shape", None)) if hasattr(obj, "Shape") else None),
        }
    frame = subshape_connector_frame(obj, reference)
    frame["object"] = object_ref(obj)
    if len(values) > 1:
        frame["secondary_reference"] = values[1]
    return frame


def assembly_reference_summary(ref):
    try:
        if isinstance(ref, (list, tuple)) and len(ref) == 2:
            obj, subelements = ref
            return {
                "object": object_ref(obj),
                "subelements": [safe_text(value) for value in list(subelements or [])],
                "connector_frame": assembly_connector_frame_summary(obj, subelements),
            }
    except Exception:
        pass
    return safe_value(ref)


def assembly_joint_summary(joint):
    item = object_ref(joint)
    if hasattr(joint, "ObjectToGround"):
        item["kind"] = "grounded"
        try:
            item["object_to_ground"] = object_ref(joint.ObjectToGround)
        except Exception:
            item["object_to_ground"] = None
    elif hasattr(joint, "JointType"):
        item["kind"] = "joint"
        item["joint_type"] = safe_text(getattr(joint, "JointType", ""))
        for prop in ["Distance", "Distance2", "Angle"]:
            if hasattr(joint, prop):
                try:
                    item[prop[0].lower() + prop[1:]] = quantity_summary(getattr(joint, prop))
                except Exception:
                    pass
        for prop in ["Reference1", "Reference2"]:
            if hasattr(joint, prop):
                try:
                    item[prop[0].lower() + prop[1:]] = assembly_reference_summary(
                        getattr(joint, prop)
                    )
                except Exception:
                    pass
        for prop in ["Placement1", "Placement2", "Offset1", "Offset2"]:
            if hasattr(joint, prop):
                try:
                    item[prop[0].lower() + prop[1:]] = placement_value_summary(
                        getattr(joint, prop)
                    )
                except Exception:
                    pass
        for prop in ["Suppressed", "Detach1", "Detach2"]:
            if hasattr(joint, prop):
                try:
                    item[prop[0].lower() + prop[1:]] = bool(getattr(joint, prop))
                except Exception:
                    pass
    else:
        item["kind"] = "unknown"
    return item


def assembly_joint_group_summary(group):
    return {
        "group": object_ref(group),
        "joints": [
            assembly_joint_summary(obj)
            for obj in list(getattr(group, "Group", []))
            if is_assembly_joint(obj)
        ],
    }


def assembly_solver_diagnostics(parts, joints, detail=None, *, fallback=False):
    issues = []
    detail = detail if isinstance(detail, dict) else {}
    if fallback:
        issues.append({
            "severity": "warning",
            "code": "assembly_fallback",
            "message": "Assembly is stored as typed fallback metadata, not native persistent Assembly objects",
        })
    if detail.get("error"):
        issues.append({
            "severity": "error",
            "code": "solver_error",
            "message": safe_text(detail.get("error"), 300),
        })
    for skipped in list(detail.get("skipped_joints") or []):
        if not isinstance(skipped, dict):
            continue
        issues.append({
            "severity": "warning",
            "code": "native_joint_skipped",
            "joint": skipped.get("joint"),
            "message": safe_text(skipped.get("reason") or "Native solver skipped a joint", 300),
        })
    if len(parts or []) < 2 and any(joint.get("kind") == "joint" for joint in list(joints or [])):
        issues.append({
            "severity": "error",
            "code": "not_enough_parts",
            "message": "Assembly joints need at least two parts",
        })
    grounded = any(joint.get("kind") == "grounded" for joint in list(joints or [])) or any(part.get("grounded") for part in list(parts or []))
    if parts and joints and not grounded:
        issues.append({
            "severity": "warning",
            "code": "ungrounded_assembly",
            "message": "No grounded part found; solver may leave rigid-body degrees of freedom",
        })
    for joint in list(joints or []):
        if joint.get("kind") != "joint":
            continue
        for key in ["reference1", "reference2"]:
            ref = joint.get(key)
            if not isinstance(ref, dict) or not ref.get("object"):
                issues.append({
                    "severity": "error",
                    "code": "missing_joint_reference",
                    "joint": joint.get("name"),
                    "reference": key,
                    "message": "Joint is missing connector reference",
                })
                continue
            frame = ref.get("connector_frame") if isinstance(ref.get("connector_frame"), dict) else {}
            quality = frame.get("frame_quality")
            if quality in {"missing_reference", "object_only"}:
                issues.append({
                    "severity": "error",
                    "code": "unresolved_connector",
                    "joint": joint.get("name"),
                    "reference": key,
                    "message": "Connector subelement could not be resolved",
                })
            elif quality == "origin_only":
                issues.append({
                    "severity": "warning",
                    "code": "connector_origin_only",
                    "joint": joint.get("name"),
                    "reference": key,
                    "message": "Connector has origin but no orientation axis",
                })
            lcs = frame.get("lcs") if isinstance(frame.get("lcs"), dict) else None
            if not lcs:
                issues.append({
                    "severity": "warning",
                    "code": "connector_lcs_missing",
                    "joint": joint.get("name"),
                    "reference": key,
                    "message": "Connector has no local coordinate system",
                })
            elif lcs.get("orientation_quality") != "complete":
                issues.append({
                    "severity": "info",
                    "code": "connector_lcs_origin_only",
                    "joint": joint.get("name"),
                    "reference": key,
                    "message": "Connector LCS has origin but no full orientation axes",
                })
    severity_order = {"error": 3, "warning": 2, "info": 1}
    severity = "ok"
    if issues:
        severity = max(issues, key=lambda item: severity_order.get(item.get("severity"), 0)).get("severity", "warning")
    return {
        "ok": severity != "error",
        "severity": severity,
        "issue_count": len(issues),
        "issues": issues,
    }


def assembly_summary(assembly):
    group = list(getattr(assembly, "Group", []) or [])
    joint_groups = [obj for obj in group if is_assembly_joint_group(obj)]
    direct_joints = [obj for obj in group if is_assembly_joint(obj)]
    joint_summaries = []
    seen_joint_names = set()
    for joint_group in joint_groups:
        for joint in assembly_joint_group_summary(joint_group)["joints"]:
            if joint["name"] in seen_joint_names:
                continue
            seen_joint_names.add(joint["name"])
            joint_summaries.append(joint)
    for obj in direct_joints:
        joint = assembly_joint_summary(obj)
        if joint["name"] in seen_joint_names:
            continue
        seen_joint_names.add(joint["name"])
        joint_summaries.append(joint)
    grounded_names = {
        item.get("object_to_ground", {}).get("name")
        for item in joint_summaries
        if item.get("kind") == "grounded" and item.get("object_to_ground")
    }
    parts = []
    for obj in group:
        if is_assembly_joint_group(obj) or is_assembly_joint(obj):
            continue
        if safe_text(getattr(obj, "TypeId", "")) == "App::Origin":
            continue
        part = object_ref(obj)
        part["kind"] = feature_kind(obj)
        part["placement"] = placement_summary(obj)
        part["grounded"] = part["name"] in grounded_names
        parts.append(part)
    return {
        "name": safe_text(getattr(assembly, "Name", "")),
        "label": safe_text(getattr(assembly, "Label", "")),
        "type_id": safe_text(getattr(assembly, "TypeId", "")),
        "assembly_backend": "native",
        "fallback": False,
        "product_grade": True,
        "status": "native_assembly",
        "placement": placement_summary(assembly),
        "part_count": len(parts),
        "joint_count": len(joint_summaries),
        "parts": parts,
        "joint_groups": [assembly_joint_group_summary(obj) for obj in joint_groups],
        "joints": joint_summaries,
        "solver_backend": "native",
        "solver_diagnostics": assembly_solver_diagnostics(parts, joint_summaries, fallback=False),
    }


def techdraw_reference_summary(ref):
    try:
        if isinstance(ref, (list, tuple)) and len(ref) == 2:
            obj, sub = ref
            return {
                "object": object_ref(obj),
                "reference": safe_text(sub),
            }
    except Exception:
        pass
    return safe_value(ref)


def techdraw_format_summary(value):
    if isinstance(value, dict):
        return {safe_text(key): safe_value(val) for key, val in value.items()}
    return safe_value(value)


def techdraw_centerline_summary(centerline, index):
    item = {
        "index": index,
        "tag": safe_text(getattr(centerline, "Tag", "")),
    }
    for prop in ["Type", "Mode", "Flip", "Extension", "HorizShift", "VertShift", "Rotation"]:
        if hasattr(centerline, prop):
            try:
                item[prop[0].lower() + prop[1:]] = safe_value(getattr(centerline, prop))
            except Exception:
                pass
    for prop in ["Edges", "Faces", "Points"]:
        if hasattr(centerline, prop):
            try:
                item[prop[0].lower() + prop[1:]] = safe_value(list(getattr(centerline, prop) or []))
            except Exception:
                pass
    if hasattr(centerline, "Format"):
        try:
            item["format"] = techdraw_format_summary(centerline.Format)
        except Exception:
            pass
    return item


def techdraw_cosmetic_vertex_summary(vertex, index):
    item = {
        "index": index,
        "tag": safe_text(getattr(vertex, "Tag", "")),
        "kind": "cosmetic_vertex",
    }
    if hasattr(vertex, "Point"):
        try:
            item["point"] = vector_summary(vertex.Point)
        except Exception:
            pass
    for prop in ["Style", "Size", "Show", "Color"]:
        if hasattr(vertex, prop):
            try:
                item[prop.lower()] = safe_value(getattr(vertex, prop))
            except Exception:
                pass
    return item


def techdraw_cosmetic_edge_summary(edge, index):
    item = {
        "index": index,
        "tag": safe_text(getattr(edge, "Tag", "")),
        "kind": "cosmetic_edge",
    }
    for key, prop in [("start", "Start"), ("end", "End"), ("center", "Center")]:
        try:
            item[key] = vector_summary(getattr(edge, prop))
        except Exception:
            pass
    try:
        item["radius"] = quantity_summary(edge.Radius)
    except Exception:
        pass
    try:
        item["format"] = techdraw_format_summary(edge.Format)
    except Exception:
        pass
    return item


def techdraw_view_summary(view):
    item = object_ref(view)
    item["kind"] = feature_kind(view)
    for prop in ["X", "Y", "Scale", "Rotation"]:
        if hasattr(view, prop):
            try:
                item[prop.lower()] = quantity_summary(getattr(view, prop))
            except Exception:
                pass
    if hasattr(view, "Direction"):
        try:
            item["direction"] = vector_summary(view.Direction)
        except Exception:
            pass
    if hasattr(view, "Source"):
        try:
            item["source"] = [object_ref(obj) for obj in list(view.Source)]
        except Exception:
            item["source"] = []
    for prop in ["ProjectionType", "Type", "Reference", "SectionSymbol"]:
        if hasattr(view, prop):
            try:
                item[prop[0].lower() + prop[1:]] = safe_value(getattr(view, prop))
            except Exception:
                pass
    for prop in ["BaseView", "Anchor"]:
        if hasattr(view, prop):
            try:
                target = getattr(view, prop)
                item[prop[0].lower() + prop[1:]] = object_ref(target) if target is not None else None
            except Exception:
                pass
    for prop in ["SectionNormal", "SectionOrigin", "SectionDirection", "AnchorPoint", "XDirection"]:
        if hasattr(view, prop):
            try:
                item[prop[0].lower() + prop[1:]] = vector_summary(getattr(view, prop))
            except Exception:
                pass
    if hasattr(view, "Radius"):
        try:
            item["radius"] = quantity_summary(view.Radius)
        except Exception:
            pass
    if hasattr(view, "Views"):
        try:
            item["views"] = [object_ref(obj) for obj in list(view.Views)]
        except Exception:
            item["views"] = []
    for key, prop, summarizer in [
        ("center_lines", "CenterLines", techdraw_centerline_summary),
        ("cosmetic_edges", "CosmeticEdges", techdraw_cosmetic_edge_summary),
        ("cosmetic_vertexes", "CosmeticVertexes", techdraw_cosmetic_vertex_summary),
    ]:
        if hasattr(view, prop):
            try:
                item[key] = [
                    summarizer(value, index)
                    for index, value in enumerate(list(getattr(view, prop) or []))
                ]
            except Exception:
                item[key] = []
    if hasattr(view, "State"):
        try:
            item["state"] = [safe_text(value) for value in list(view.State)]
        except Exception:
            item["state"] = safe_value(view.State)
    if hasattr(view, "getVisibleEdges"):
        try:
            item["visible_edge_count"] = len(view.getVisibleEdges())
        except Exception:
            pass
    return item


def techdraw_fallback_views(page):
    try:
        raw = getattr(page, TECHDRAW_FALLBACK_VIEWS_PROPERTY, "")
    except Exception:
        raw = ""
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except Exception:
        return []
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        if isinstance(value, dict) and value.get("name") and value.get("kind"):
            result.append(value)
    return result


def techdraw_state_holder(doc, create=False):
    if doc is None:
        return None
    for obj in list(getattr(doc, "Objects", []) or []):
        try:
            if getattr(obj, "Name", "") == TECHDRAW_FALLBACK_STATE_OBJECT:
                return obj
        except Exception:
            pass
    if not create:
        return None
    holder = doc.addObject("App::DocumentObjectGroup", TECHDRAW_FALLBACK_STATE_OBJECT)
    try:
        holder.Label = TECHDRAW_FALLBACK_STATE_OBJECT
    except Exception:
        pass
    return holder


def load_techdraw_fallback_state(doc):
    holder = techdraw_state_holder(doc, create=False)
    raw = ""
    if holder is not None:
        try:
            raw = getattr(holder, TECHDRAW_FALLBACK_PAGES_PROPERTY, "")
        except Exception:
            raw = ""
    if raw:
        try:
            state = json.loads(raw)
        except Exception:
            state = {}
    else:
        state = {}
    if not isinstance(state, dict):
        state = {}
    pages = state.get("pages")
    if not isinstance(pages, dict):
        pages = {}
    return {
        "schema": "freecad.techdraw_fallback.v1",
        "pages": pages,
    }


def save_techdraw_fallback_state(doc, state):
    holder = techdraw_state_holder(doc, create=True)
    if holder is None:
        raise ValueError("could not create TechDraw fallback state holder")
    if TECHDRAW_FALLBACK_PAGES_PROPERTY not in list(getattr(holder, "PropertiesList", [])):
        holder.addProperty(
            "App::PropertyString",
            TECHDRAW_FALLBACK_PAGES_PROPERTY,
            "4yi",
            "Headless TechDraw fallback page metadata",
        )
    setattr(holder, TECHDRAW_FALLBACK_PAGES_PROPERTY, json.dumps(state, ensure_ascii=False))


def techdraw_layout_diagnostics(page, views, dimensions, *, fallback=False):
    issues = []
    if fallback:
        issues.append({
            "severity": "warning",
            "code": "typed_vector_fallback",
            "message": "TechDraw page is represented by typed fallback layout metadata",
        })
    if not views:
        issues.append({
            "severity": "error",
            "code": "no_views",
            "message": "TechDraw page has no drawing views",
        })
    if not dimensions:
        issues.append({
            "severity": "warning",
            "code": "no_dimensions",
            "message": "TechDraw page has no dimensions",
        })
    template_path = page.get("template_path") if isinstance(page, dict) else None
    if not template_path:
        try:
            template_path = safe_text(getattr(page.Template, "Template", "")) if getattr(page, "Template", None) is not None else ""
        except Exception:
            template_path = ""
    if not template_path:
        issues.append({
            "severity": "warning",
            "code": "no_template",
            "message": "No drawing template/title block is attached",
        })
    for view in list(views or []):
        width = view.get("width") if isinstance(view, dict) else None
        height = view.get("height") if isinstance(view, dict) else None
        if width is not None and height is not None:
            try:
                if float(width) <= 0 or float(height) <= 0:
                    issues.append({
                        "severity": "error",
                        "code": "invalid_view_extent",
                        "view": view.get("name"),
                        "message": "TechDraw view has invalid layout extent",
                    })
            except Exception:
                pass
    severity_order = {"error": 3, "warning": 2, "info": 1}
    severity = "ok"
    if issues:
        severity = max(issues, key=lambda item: severity_order.get(item.get("severity"), 0)).get("severity", "warning")
    return {
        "ok": severity != "error",
        "severity": severity,
        "issue_count": len(issues),
        "issues": issues,
        "export_quality": "fallback" if fallback else ("product_candidate" if severity != "error" else "incomplete"),
    }


def techdraw_fallback_pages(doc):
    state = load_techdraw_fallback_state(doc)
    pages = []
    for page in sorted(list(state.get("pages", {}).values()), key=lambda item: safe_text(item.get("name", ""))):
        if not isinstance(page, dict) or not page.get("name"):
            continue
        normalized = dict(page)
        views = list(normalized.get("views") or [])
        dimensions = list(normalized.get("dimensions") or [])
        normalized["view_count"] = len(views)
        normalized["dimension_count"] = len(dimensions)
        normalized["views"] = views
        normalized["dimensions"] = dimensions
        normalized["fallback"] = True
        normalized["product_grade"] = False
        normalized["status"] = normalized.get("status") or "typed_vector_fallback"
        normalized["layout_diagnostics"] = techdraw_layout_diagnostics(normalized, views, dimensions, fallback=True)
        pages.append(normalized)
    return pages


def techdraw_dimension_summary(dimension):
    item = techdraw_view_summary(dimension)
    for prop in ["Type", "MeasureType", "FormatSpec"]:
        if hasattr(dimension, prop):
            try:
                item[prop[0].lower() + prop[1:]] = safe_value(getattr(dimension, prop))
            except Exception:
                pass
    for prop in ["References2D", "References3D"]:
        if hasattr(dimension, prop):
            try:
                item[prop[0].lower() + prop[1:]] = [
                    techdraw_reference_summary(ref)
                    for ref in list(getattr(dimension, prop))
                ]
            except Exception:
                pass
    return item


def techdraw_page_summary(page):
    views = []
    dimensions = []
    try:
        page_views = list(getattr(page, "Views", []) or [])
    except Exception:
        page_views = []
    for view in page_views:
        if is_techdraw_dimension(view):
            dimensions.append(techdraw_dimension_summary(view))
        elif is_techdraw_view(view):
            views.append(techdraw_view_summary(view))
    views.extend(techdraw_fallback_views(page))
    item = object_ref(page)
    item.update({
        "kind": "techdraw_page",
        "scale": quantity_summary(getattr(page, "Scale", None)),
        "fallback": False,
        "product_grade": True,
        "status": "native_techdraw",
        "view_count": len(views),
        "dimension_count": len(dimensions),
        "views": views,
        "dimensions": dimensions,
    })
    try:
        if getattr(page, "Template", None) is not None:
            item["template"] = object_ref(page.Template)
            item["template_path"] = safe_text(getattr(page.Template, "Template", ""))
    except Exception:
        pass
    item["layout_diagnostics"] = techdraw_layout_diagnostics(item, views, dimensions, fallback=False)
    return item


def merge_bbox(current, bbox):
    if not bbox:
        return current
    if current is None:
        return {
            "min": list(bbox["min"]),
            "max": list(bbox["max"]),
            "size": list(bbox["size"]),
        }
    for idx in range(3):
        current["min"][idx] = min(current["min"][idx], bbox["min"][idx])
        current["max"][idx] = max(current["max"][idx], bbox["max"][idx])
        current["size"][idx] = current["max"][idx] - current["min"][idx]
    return current


def ref_name(ref):
    if isinstance(ref, dict):
        return ref.get("name")
    return None


def sorted_refs_by_name(values):
    return sorted(list(values or []), key=lambda item: (safe_text(ref_name(item) or ""), safe_text(item)))


def properties_by_name(properties):
    result = {}
    for prop in list(properties or []):
        name = prop.get("name")
        if not name:
            continue
        result[name] = {
            "type": prop.get("type"),
            "group": prop.get("group"),
            "value": prop.get("value"),
        }
    return result


def typed_document_state(document, summaries, geometry, feature_tree, sketches, assemblies, techdraw):
    nodes_by_name = {}
    for node in list(feature_tree.get("nodes", []) or []):
        name = ref_name(node.get("object"))
        if not name:
            continue
        nodes_by_name[name] = {
            "id": name,
            "kind": node.get("kind"),
            "label": node.get("object", {}).get("label"),
            "type_id": node.get("object", {}).get("type_id"),
            "parents": [ref_name(ref) for ref in sorted_refs_by_name(node.get("parents")) if ref_name(ref)],
            "children": [ref_name(ref) for ref in sorted_refs_by_name(node.get("children")) if ref_name(ref)],
            "tip": ref_name(node.get("tip")) if node.get("tip") else None,
            "placement": node.get("placement"),
        }
    objects_by_name = {}
    for item in sorted(list(summaries or []), key=lambda obj: safe_text(obj.get("name", ""))):
        name = item.get("name")
        if not name:
            continue
        objects_by_name[name] = {
            "id": name,
            "label": item.get("label"),
            "type_id": item.get("type_id"),
            "kind": nodes_by_name.get(name, {}).get("kind") or feature_kind_from_summary(item),
            "placement": item.get("placement"),
            "shape": item.get("shape"),
            "properties": properties_by_name(item.get("properties")),
            "parents": [ref_name(ref) for ref in sorted_refs_by_name(item.get("in_list")) if ref_name(ref)],
            "children": [ref_name(ref) for ref in sorted_refs_by_name(item.get("out_list")) if ref_name(ref)],
        }
    sketches_by_name = {}
    for sketch in sorted(list(sketches or []), key=lambda item: safe_text(item.get("name", ""))):
        name = sketch.get("name")
        if not name:
            continue
        sketches_by_name[name] = {
            "id": name,
            "label": sketch.get("label"),
            "type_id": sketch.get("type_id"),
            "map_mode": sketch.get("map_mode"),
            "attachment_support": sketch.get("attachment_support") or [],
            "geometry": sketch.get("geometry") or [],
            "external_geometry": sketch.get("external_geometry") or [],
            "constraints": sketch.get("constraints") or [],
            "solver": sketch.get("solver") or {},
        }
    assemblies_by_name = {}
    for assembly in sorted(list(assemblies or []), key=lambda item: safe_text(item.get("name", ""))):
        name = assembly.get("name")
        if not name:
            continue
        assemblies_by_name[name] = {
            "id": name,
            "label": assembly.get("label"),
            "type_id": assembly.get("type_id"),
            "placement": assembly.get("placement"),
            "parts": {
                part.get("name"): part
                for part in sorted(list(assembly.get("parts") or []), key=lambda item: safe_text(item.get("name", "")))
                if part.get("name")
            },
            "joints": {
                joint.get("name"): joint
                for joint in sorted(list(assembly.get("joints") or []), key=lambda item: safe_text(item.get("name", "")))
                if joint.get("name")
            },
        }
    pages_by_name = {}
    for page in sorted(list(techdraw or []), key=lambda item: safe_text(item.get("name", ""))):
        name = page.get("name")
        if not name:
            continue
        pages_by_name[name] = {
            "id": name,
            "label": page.get("label"),
            "type_id": page.get("type_id"),
            "scale": page.get("scale"),
            "template": page.get("template"),
            "template_path": page.get("template_path"),
            "views": {
                view.get("name"): view
                for view in sorted(list(page.get("views") or []), key=lambda item: safe_text(item.get("name", "")))
                if view.get("name")
            },
            "dimensions": {
                dimension.get("name"): dimension
                for dimension in sorted(list(page.get("dimensions") or []), key=lambda item: safe_text(item.get("name", "")))
                if dimension.get("name")
            },
        }
    return {
        "schema": "freecad.typed_state.v1",
        "document": document,
        "geometry": geometry,
        "objects": objects_by_name,
        "feature_tree": {
            "roots": [ref_name(ref) for ref in sorted_refs_by_name(feature_tree.get("roots")) if ref_name(ref)],
            "nodes": nodes_by_name,
        },
        "sketches": sketches_by_name,
        "assemblies": assemblies_by_name,
        "techdraw": {"pages": pages_by_name},
    }


def feature_kind_from_summary(item):
    type_id = safe_text(item.get("type_id", ""))
    if type_id == ASSEMBLY_TYPE:
        return "assembly"
    if type_id == ASSEMBLY_JOINT_GROUP_TYPE:
        return "assembly_joint_group"
    if type_id == PARTDESIGN_BODY_TYPE:
        return "partdesign_body"
    if type_id.startswith("PartDesign::"):
        return "partdesign_feature"
    if type_id in PART_FEATURE_TYPES:
        return "part_primitive"
    if "Sketcher::SketchObject" in type_id:
        return "sketch"
    if type_id.startswith("TechDraw::"):
        return "techdraw"
    if type_id.startswith("Part::"):
        return "part_feature"
    return "document_object"


def document_summary(doc):
    if doc is None:
        document = None
        geometry = {"object_count": 0}
        return {
            "schema": "freecad.document_summary.v6",
            "document": document,
            "objects": [],
            "geometry": geometry,
            "feature_tree": {"roots": [], "nodes": []},
            "sketches": [],
            "assemblies": [],
            "techdraw": [],
            "assembly_capabilities": assembly_runtime_capabilities(),
            "techdraw_capabilities": techdraw_runtime_capabilities(),
            "typed_state": typed_document_state(document, [], geometry, {"roots": [], "nodes": []}, [], [], []),
        }
    try:
        doc.recompute()
    except Exception:
        pass

    objects = list(getattr(doc, "Objects", []))
    summaries = [object_summary(obj) for obj in objects]
    summaries.extend(feature_fallback_summaries(doc))
    sketches = [item["sketch"] for item in summaries if item.get("sketch")]
    assemblies = [assembly_summary(obj) for obj in objects if is_assembly_object(obj)]
    assemblies.extend(assembly_fallback_summaries(doc))
    techdraw = [techdraw_page_summary(obj) for obj in objects if is_techdraw_page(obj)]
    techdraw.extend(techdraw_fallback_pages(doc))

    valid = True
    saw_validity = False
    bbox = None
    volume = 0.0
    saw_volume = False
    totals = {
        "object_count": len(objects),
        "shape_object_count": 0,
        "solid_count": 0,
        "shell_count": 0,
        "face_count": 0,
        "edge_count": 0,
        "vertex_count": 0,
        "invalid_object_count": 0,
        "check_error_count": 0,
    }
    max_tolerance = None
    failure_class = None
    for item in summaries:
        shape = item.get("shape")
        if not shape:
            continue
        totals["shape_object_count"] += 1
        if shape.get("valid") is not None:
            saw_validity = True
            valid = valid and bool(shape["valid"])
            if shape["valid"] is False:
                totals["invalid_object_count"] += 1
        check = shape.get("check") or {}
        if check.get("ok") is False:
            totals["check_error_count"] += 1
        tolerance = shape.get("tolerance") or {}
        if tolerance.get("max") is not None:
            max_tolerance = max(float(tolerance["max"]), max_tolerance or 0.0)
        if not failure_class and shape.get("failure_class"):
            failure_class = shape.get("failure_class")
        bbox = merge_bbox(bbox, shape.get("bbox"))
        if shape.get("volume") is not None:
            volume += float(shape["volume"])
            saw_volume = True
        for key in ["solid_count", "shell_count", "face_count", "edge_count", "vertex_count"]:
            if shape.get(key) is not None:
                totals[key] += int(shape[key])

    geometry = dict(totals)
    geometry.update({
        "valid": valid if saw_validity else None,
        "bbox": bbox,
        "volume": volume if saw_volume else None,
        "max_tolerance": max_tolerance,
        "failure_class": failure_class,
    })
    document = {
        "name": safe_text(getattr(doc, "Name", "")),
        "label": safe_text(getattr(doc, "Label", "")),
        "file_name": safe_text(getattr(doc, "FileName", "")),
    }
    feature_tree = feature_tree_summary(doc, objects)
    fallback_tree = fallback_feature_tree_nodes(doc)
    feature_tree["roots"].extend(fallback_tree["roots"])
    feature_tree["nodes"].extend(fallback_tree["nodes"])
    fallback_assembly_tree = fallback_assembly_feature_tree_nodes(doc)
    feature_tree["roots"].extend(fallback_assembly_tree["roots"])
    feature_tree["nodes"].extend(fallback_assembly_tree["nodes"])
    return {
        "schema": "freecad.document_summary.v6",
        "document": document,
        "objects": summaries,
        "geometry": geometry,
        "feature_tree": feature_tree,
        "sketches": sketches,
        "assemblies": assemblies,
        "techdraw": techdraw,
        "assembly_capabilities": assembly_runtime_capabilities(),
        "techdraw_capabilities": techdraw_runtime_capabilities(),
        "typed_state": typed_document_state(document, summaries, geometry, feature_tree, sketches, assemblies, techdraw),
    }


def load_document_patches():
    patches_path = os.environ.get("FOURYI_FREECAD_PATCHES_PATH")
    if not patches_path:
        return []
    with open(patches_path, "r", encoding="utf-8") as fh:
        patches = json.load(fh)
    if not isinstance(patches, list):
        raise ValueError("document patches must be a list")
    return patches


def matches_selector(obj, selector):
    if not isinstance(selector, dict) or not selector:
        return False
    checks = {
        "name": safe_text(getattr(obj, "Name", "")),
        "label": safe_text(getattr(obj, "Label", "")),
        "type_id": safe_text(getattr(obj, "TypeId", "")),
    }
    for key, expected in selector.items():
        if key not in checks or expected is None:
            continue
        if checks[key] != safe_text(expected):
            return False
    return True


def select_single_object(doc, selector):
    matches = [obj for obj in list(getattr(doc, "Objects", [])) if matches_selector(obj, selector)]
    if not matches:
        raise ValueError("no FreeCAD object matched selector: " + json.dumps(selector))
    if len(matches) > 1:
        raise ValueError("selector matched multiple FreeCAD objects: " + json.dumps(selector))
    return matches[0]


def subelement_prefix_from_payload(kind="", reference="", stable_id=""):
    raw = safe_text(kind or reference or stable_id, 80).lower()
    if raw.startswith("face"):
        return "Face"
    if raw.startswith("edge"):
        return "Edge"
    if raw.startswith("vertex"):
        return "Vertex"
    return ""


def subelement_attr_for_prefix(prefix):
    return {
        "Face": "Faces",
        "Edge": "Edges",
        "Vertex": "Vertexes",
    }.get(prefix)


def stable_signature_payload(value):
    if not isinstance(value, dict):
        return {}
    return {
        key: value.get(key)
        for key in ["kind", "shape_type", "center", "bbox_size", "length", "area", "volume"]
        if value.get(key) is not None
    }


def relative_delta(expected, actual):
    if expected is None or actual is None:
        return None
    try:
        left = float(expected)
        right = float(actual)
    except Exception:
        return None
    if not math.isfinite(left) or not math.isfinite(right):
        return None
    return abs(left - right) / max(abs(left), abs(right), 1.0)


def sequence_delta(expected, actual):
    if not isinstance(expected, (list, tuple)) or not isinstance(actual, (list, tuple)):
        return None
    if len(expected) < 3 or len(actual) < 3:
        return None
    values = []
    for index in range(3):
        delta = relative_delta(expected[index], actual[index])
        if delta is None:
            return None
        values.append(delta)
    return sum(values) / len(values)


def stable_signature_score(expected, actual):
    expected = stable_signature_payload(expected)
    actual = stable_signature_payload(actual)
    if not expected or not actual:
        return None
    score = 0.0
    matched = 0
    missing = 0
    for key in ["kind", "shape_type"]:
        if expected.get(key) is None or actual.get(key) is None:
            missing += 1
            continue
        matched += 1
        if safe_text(expected.get(key), 80).lower() != safe_text(actual.get(key), 80).lower():
            score += 100.0
    for key in ["center", "bbox_size"]:
        delta = sequence_delta(expected.get(key), actual.get(key))
        if delta is None:
            missing += 1
            continue
        matched += 1
        score += delta * (8.0 if key == "center" else 5.0)
    for key, weight in [("length", 3.0), ("area", 3.0), ("volume", 2.0)]:
        delta = relative_delta(expected.get(key), actual.get(key))
        if delta is None:
            continue
        matched += 1
        score += delta * weight
    if matched == 0:
        return None
    score += missing * 0.25
    return score


def stable_match_confidence(score):
    if score is None:
        return "none"
    if score <= 0.000001:
        return "exact"
    if score <= 0.02:
        return "high"
    if score <= 0.12:
        return "medium"
    return "low"


def resolve_subelement_reference_on_object(obj, *, reference="", stable_id="", kind="", signature=None, stable_reference=""):
    reference = safe_text(reference, 160)
    stable_id = safe_text(stable_id, 160)
    stable_reference = safe_text(stable_reference, 160)
    signature = stable_signature_payload(signature)
    prefix = subelement_prefix_from_payload(kind=kind, reference=reference, stable_id=stable_id)
    if not prefix and signature.get("kind"):
        prefix = subelement_prefix_from_payload(kind=signature.get("kind"), reference=reference, stable_id=stable_id)
    if not stable_id and not stable_reference and not signature:
        return reference, {
            "requested_reference": reference,
            "resolved_reference": reference,
            "stable_id": None,
            "stable_reference": None,
            "status": "topological_reference",
            "confidence": "none",
        }
    attr = subelement_attr_for_prefix(prefix)
    shape = getattr(obj, "Shape", None)
    if shape is None or not attr:
        return reference, {
            "requested_reference": reference,
            "resolved_reference": reference,
            "stable_id": stable_id,
            "stable_reference": stable_reference or None,
            "status": "stable_unresolved_no_shape",
            "confidence": "none",
        }
    try:
        subshapes = list(getattr(shape, attr) or [])
    except Exception:
        subshapes = []
    signature_candidates = []
    for index, subshape in enumerate(subshapes):
        fields = subshape_stable_fields(subshape, prefix, index)
        matched_stable_id = stable_id and stable_id in {fields.get("stable_id"), fields.get("legacy_stable_id")}
        matched_stable_reference = stable_reference and stable_reference == fields.get("stable_reference")
        if matched_stable_id or matched_stable_reference:
            resolved = fields["topological_reference"]
            if matched_stable_reference:
                match_method = "stable_reference"
            elif stable_id == fields.get("stable_id"):
                match_method = "stable_id"
            else:
                match_method = "legacy_stable_id"
            return resolved, {
                "requested_reference": reference,
                "resolved_reference": resolved,
                "stable_id": stable_id or None,
                "resolved_stable_id": fields.get("stable_id"),
                "stable_reference": fields.get("stable_reference"),
                "requested_stable_reference": stable_reference or None,
                "signature_version": fields.get("signature_version"),
                "status": "stable_resolved" if resolved == reference else "stable_remapped",
                "match_method": match_method,
                "confidence": "exact",
                "signature": fields.get("signature"),
                "provenance": fields.get("provenance"),
                "ref_history": fields.get("ref_history"),
            }
        if signature:
            score = stable_signature_score(signature, fields.get("signature"))
            if score is not None:
                signature_candidates.append((score, fields))
    if signature_candidates:
        signature_candidates.sort(key=lambda item: item[0])
        best_score, best_fields = signature_candidates[0]
        second_score = signature_candidates[1][0] if len(signature_candidates) > 1 else None
        confidence = stable_match_confidence(best_score)
        ambiguous = second_score is not None and abs(second_score - best_score) <= max(0.000001, best_score * 0.15)
        if confidence in {"exact", "high", "medium"} and not ambiguous:
            resolved = best_fields["topological_reference"]
            return resolved, {
                "requested_reference": reference,
                "resolved_reference": resolved,
                "stable_id": stable_id or None,
                "resolved_stable_id": best_fields.get("stable_id"),
                "stable_reference": best_fields.get("stable_reference"),
                "requested_stable_reference": stable_reference or None,
                "signature_version": best_fields.get("signature_version"),
                "status": "stable_signature_resolved" if resolved == reference else "stable_signature_remapped",
                "match_method": "signature_score",
                "confidence": confidence,
                "score": best_score,
                "second_score": second_score,
                "signature": best_fields.get("signature"),
                "provenance": best_fields.get("provenance"),
                "ref_history": best_fields.get("ref_history"),
            }
        return reference, {
            "requested_reference": reference,
            "resolved_reference": reference,
            "stable_id": stable_id or None,
            "stable_reference": stable_reference or None,
            "status": "stable_signature_ambiguous" if ambiguous else "stable_signature_low_confidence",
            "match_method": "signature_score",
            "confidence": "ambiguous" if ambiguous else confidence,
            "score": best_score,
            "second_score": second_score,
        }
    return reference, {
        "requested_reference": reference,
        "resolved_reference": reference,
        "stable_id": stable_id,
        "stable_reference": stable_reference or None,
        "status": "stable_unresolved_fallback_reference",
        "confidence": "none",
    }


def assign_property_value(obj, property_name, value):
    if not property_name or not isinstance(property_name, str):
        raise ValueError("set_property requires property")
    blocked = {"Shape", "Mesh", "Proxy", "ExpressionEngine", "ViewObject"}
    if property_name in blocked or property_name.startswith("__"):
        raise ValueError("refusing to set unsafe FreeCAD property: " + property_name)
    if property_name not in list(getattr(obj, "PropertiesList", [])) and not hasattr(obj, property_name):
        raise ValueError("object has no property: " + property_name)

    old_value = None
    try:
        old_value = safe_value(getattr(obj, property_name))
    except Exception:
        pass
    setattr(obj, property_name, value)
    return {
        "property": property_name,
        "old_value": old_value,
        "new_value": safe_value(getattr(obj, property_name)),
    }


def find_constraint_index(sketch, patch):
    if patch.get("constraint_index") is not None:
        index = int(patch["constraint_index"])
        if index < 0 or index >= len(sketch.Constraints):
            raise ValueError("constraint_index out of range: " + str(index))
        return index
    name = patch.get("constraint_name") or patch.get("name")
    if name is not None:
        matches = [
            index
            for index, constraint in enumerate(list(sketch.Constraints))
            if safe_text(getattr(constraint, "Name", "")) == safe_text(name)
        ]
        if not matches:
            raise ValueError("no sketch constraint matched name: " + safe_text(name))
        if len(matches) > 1:
            raise ValueError("constraint_name matched multiple constraints: " + safe_text(name))
        return matches[0]
    raise ValueError("set_constraint_value requires constraint_index or constraint_name")


def assign_constraint_value(sketch, patch):
    if not hasattr(sketch, "Constraints"):
        raise ValueError("selected object is not a sketch with constraints")
    index = find_constraint_index(sketch, patch)
    constraint = sketch.Constraints[index]
    old_value = safe_value(getattr(constraint, "Value", None))
    value = patch.get("value")
    if value is None:
        raise ValueError("set_constraint_value requires value")
    if hasattr(sketch, "setDatum"):
        sketch.setDatum(index, value)
    else:
        constraint.Value = value
    constraint = sketch.Constraints[index]
    return {
        "constraint_index": index,
        "constraint_name": safe_text(getattr(constraint, "Name", "")),
        "old_value": old_value,
        "new_value": safe_value(getattr(constraint, "Value", None)),
    }


def safe_feature_name(value, fallback="Feature"):
    text = safe_text(value or fallback, 80)
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in text)
    if not cleaned:
        cleaned = fallback
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = fallback + "_" + cleaned
    return cleaned[:80]


def patch_mapping(patch, key):
    value = patch.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(key + " must be an object")
    return value


def vector_from_value(value, field_name):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(field_name + " must be a 3-item vector")
    return FreeCAD.Vector(float(value[0]), float(value[1]), float(value[2]))


def ensure_sketch(obj):
    type_id = safe_text(getattr(obj, "TypeId", ""))
    if "Sketcher::SketchObject" not in type_id and not hasattr(obj, "addGeometry"):
        raise ValueError("selected object is not a Sketcher sketch")
    return obj


def select_sketch(doc, patch):
    selector = patch.get("sketch_selector") or patch.get("selector") or {}
    return ensure_sketch(select_single_object(doc, selector))


def sketch_support_payload(doc, patch):
    selector = (
        patch.get("support_selector")
        or patch.get("source_selector")
        or patch.get("part_selector")
        or patch.get("feature_selector")
        or patch.get("target_selector")
        or patch.get("selector")
        or {}
    )
    references = patch.get("references")
    if references is not None:
        if not isinstance(references, list) or len(references) > 8:
            raise ValueError("Sketcher attachment references must be a list with at most 8 items")
        subelements = [safe_text(item, 160) for item in references]
    else:
        reference = (
            patch.get("reference")
            or patch.get("element")
            or patch.get("subelement")
            or patch.get("sub")
            or "Face1"
        )
        subelements = [safe_text(reference, 160)]
    stable_ids = patch.get("stable_ids") if isinstance(patch.get("stable_ids"), list) else []
    if patch.get("stable_id") and not stable_ids:
        stable_ids = [patch.get("stable_id")]
    stable_signatures = patch.get("stable_signatures") if isinstance(patch.get("stable_signatures"), list) else []
    if patch.get("signature") and not stable_signatures:
        stable_signatures = [patch.get("signature")]
    stable_references = patch.get("stable_references") if isinstance(patch.get("stable_references"), list) else []
    if patch.get("stable_reference") and not stable_references:
        stable_references = [patch.get("stable_reference")]
    support = select_single_object(doc, selector)
    diagnostics = []
    resolved = []
    for index, reference in enumerate(subelements):
        stable_id = stable_ids[index] if index < len(stable_ids) else ""
        signature = stable_signatures[index] if index < len(stable_signatures) else None
        stable_reference = stable_references[index] if index < len(stable_references) else ""
        resolved_reference, detail = resolve_subelement_reference_on_object(
            support,
            reference=reference,
            stable_id=stable_id,
            kind=patch.get("kind") or "",
            signature=signature,
            stable_reference=stable_reference,
        )
        resolved.append(resolved_reference)
        diagnostics.append(detail)
    return support, resolved, diagnostics


def attach_sketch_to_support(doc, sketch, patch):
    ensure_sketch(sketch)
    support, subelements, reference_diagnostics = sketch_support_payload(doc, patch)
    old_support = sketch_attachment_support_summary(sketch)
    old_map_mode = safe_text(getattr(sketch, "MapMode", ""))
    sketch.AttachmentSupport = [(support, tuple(subelements))]
    sketch.MapMode = safe_text(patch.get("map_mode") or patch.get("mode") or "FlatFace", 80)
    if patch.get("attachment_offset") is not None:
        offset_patch = {"placement": patch.get("attachment_offset")}
        payload = placement_payload(offset_patch)
        base = vector_from_value(payload.get("base", [0, 0, 0]), "attachment_offset.base")
        axis = vector_from_value(payload.get("axis", [0, 0, 1]), "attachment_offset.axis")
        angle = payload.get("angle_degrees")
        if angle is None and payload.get("angle_radians") is not None:
            angle = float(payload["angle_radians"]) * 180.0 / 3.141592653589793
        if angle is None:
            angle = 0.0
        sketch.AttachmentOffset = FreeCAD.Placement(base, FreeCAD.Rotation(axis, float(angle)))
    try:
        doc.recompute()
    except Exception:
        pass
    return {
        "sketch": object_ref(sketch),
        "support": object_ref(support),
        "references": list(subelements),
        "old_attachment_support": old_support,
        "new_attachment_support": sketch_attachment_support_summary(sketch),
        "reference_diagnostics": reference_diagnostics,
        "old_map_mode": old_map_mode,
        "new_map_mode": safe_text(getattr(sketch, "MapMode", "")),
        "placement": placement_summary(sketch),
    }


def create_sketch(doc, patch):
    name = safe_feature_name(patch.get("name"), "Sketch")
    label = patch.get("label")
    body = None
    body_selector = patch.get("body_selector") or patch.get("parent_selector")
    if body_selector:
        body = select_single_object(doc, body_selector)
        if safe_text(getattr(body, "TypeId", "")) != PARTDESIGN_BODY_TYPE:
            raise ValueError("body_selector/parent_selector must match a PartDesign::Body")
        if hasattr(body, "newObject"):
            sketch = body.newObject("Sketcher::SketchObject", name)
        else:
            sketch = doc.addObject("Sketcher::SketchObject", name)
            if hasattr(body, "addObject"):
                body.addObject(sketch)
    else:
        sketch = doc.addObject("Sketcher::SketchObject", name)
    if label is not None:
        sketch.Label = safe_text(label, 160)
    property_details = assign_feature_properties(sketch, patch_mapping(patch, "properties"))
    placement_detail = None
    if patch.get("placement") is not None or patch.get("base") is not None:
        placement_detail = set_object_placement(sketch, patch)
    attachment_detail = None
    if (
        patch.get("support_selector") is not None
        or patch.get("source_selector") is not None
        or patch.get("part_selector") is not None
        or patch.get("feature_selector") is not None
    ):
        attachment_detail = attach_sketch_to_support(doc, sketch, patch)
    return {
        "sketch": object_ref(sketch),
        "body": object_ref(body) if body is not None else None,
        "properties": property_details,
        "placement": placement_detail,
        "attachment": attachment_detail,
        "solver": sketch_solver_status(sketch, solve=False),
    }, sketch


def patch_items(patch, singular_key, plural_key, required_message):
    items = []
    if patch.get(singular_key) is not None:
        items.append(patch.get(singular_key))
    if patch.get(plural_key) is not None:
        plural = patch.get(plural_key)
        if not isinstance(plural, list):
            raise ValueError(plural_key + " must be a list")
        items.extend(plural)
    if not items:
        raise ValueError(required_message)
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(singular_key + " items must be objects")
    return items


def normalized_kind(value):
    return safe_text(value, 80).strip().replace("-", "_").lower()


def radians_from_payload(payload, key, degrees_key):
    if payload.get(key) is not None:
        return float(payload[key])
    if payload.get(degrees_key) is not None:
        return float(payload[degrees_key]) * 3.141592653589793 / 180.0
    return None


def vector_payload(payload, field_name, *keys, default=None):
    for key in keys:
        if payload.get(key) is not None:
            return vector_from_value(payload[key], field_name + "." + key)
    if default is not None:
        return vector_from_value(default, field_name)
    raise ValueError(field_name + " is required")


def make_sketch_geometry(payload):
    kind = normalized_kind(payload.get("type") or payload.get("kind"))
    if kind not in SUPPORTED_SKETCH_GEOMETRY_TYPES:
        raise ValueError("unsupported Sketcher geometry type: " + safe_text(kind))
    if kind in {"line", "line_segment", "linesegment"}:
        start = vector_payload(payload, "geometry.start", "start", "p1", "from")
        end = vector_payload(payload, "geometry.end", "end", "p2", "to")
        return Part.LineSegment(start, end)
    if kind == "polyline":
        points = payload.get("points")
        if not isinstance(points, list) or len(points) < 2 or len(points) > 80:
            raise ValueError("polyline geometry requires 2-80 points")
        vectors = [vector_from_value(point, "geometry.points") for point in points]
        return [
            Part.LineSegment(vectors[index], vectors[index + 1])
            for index in range(len(vectors) - 1)
        ]
    if kind == "rectangle":
        if payload.get("points") is not None:
            points = payload.get("points")
            if not isinstance(points, list) or len(points) != 2:
                raise ValueError("rectangle points must contain two opposite corners")
            p1 = vector_from_value(points[0], "geometry.points[0]")
            p2 = vector_from_value(points[1], "geometry.points[1]")
        else:
            p1 = vector_payload(payload, "geometry.start", "start", "p1", "corner1", default=[0, 0, 0])
            p2 = vector_payload(payload, "geometry.end", "end", "p2", "corner2")
        corners = [
            p1,
            FreeCAD.Vector(float(p2.x), float(p1.y), float(p1.z)),
            p2,
            FreeCAD.Vector(float(p1.x), float(p2.y), float(p1.z)),
        ]
        return [
            Part.LineSegment(corners[index], corners[(index + 1) % 4])
            for index in range(4)
        ]
    if kind == "circle":
        center = vector_payload(payload, "geometry.center", "center", default=[0, 0, 0])
        axis = vector_payload(payload, "geometry.axis", "axis", "normal", default=[0, 0, 1])
        radius = payload.get("radius")
        if radius is None:
            raise ValueError("circle geometry requires radius")
        return Part.Circle(center, axis, float(radius))
    if kind == "ellipse":
        center = vector_payload(payload, "geometry.center", "center", default=[0, 0, 0])
        major = payload.get("major_radius") or payload.get("major")
        minor = payload.get("minor_radius") or payload.get("minor")
        if major is None or minor is None:
            raise ValueError("ellipse geometry requires major_radius and minor_radius")
        return Part.Ellipse(center, float(major), float(minor))
    if kind in {"arc", "arc_of_circle", "arcofcircle"}:
        center = vector_payload(payload, "geometry.center", "center", default=[0, 0, 0])
        axis = vector_payload(payload, "geometry.axis", "axis", "normal", default=[0, 0, 1])
        radius = payload.get("radius")
        if radius is None:
            raise ValueError("arc geometry requires radius")
        start_angle = radians_from_payload(payload, "start_angle", "start_angle_degrees")
        end_angle = radians_from_payload(payload, "end_angle", "end_angle_degrees")
        if start_angle is None or end_angle is None:
            raise ValueError("arc geometry requires start_angle/end_angle")
        return Part.ArcOfCircle(Part.Circle(center, axis, float(radius)), start_angle, end_angle)
    if kind == "arc_3_points":
        start = vector_payload(payload, "geometry.start", "start", "p1", "from")
        mid = vector_payload(payload, "geometry.mid", "mid", "p2", "through")
        end = vector_payload(payload, "geometry.end", "end", "p3", "to")
        return Part.Arc(start, mid, end)
    if kind in {"arc_of_ellipse", "arcofellipse"}:
        center = vector_payload(payload, "geometry.center", "center", default=[0, 0, 0])
        major = payload.get("major_radius") or payload.get("major")
        minor = payload.get("minor_radius") or payload.get("minor")
        if major is None or minor is None:
            raise ValueError("arc_of_ellipse geometry requires major_radius and minor_radius")
        start_angle = radians_from_payload(payload, "start_angle", "start_angle_degrees")
        end_angle = radians_from_payload(payload, "end_angle", "end_angle_degrees")
        if start_angle is None or end_angle is None:
            raise ValueError("arc_of_ellipse geometry requires start_angle/end_angle")
        return Part.ArcOfEllipse(Part.Ellipse(center, float(major), float(minor)), start_angle, end_angle)
    if kind == "point":
        point = vector_payload(payload, "geometry.point", "point", "position", "center")
        return Part.Point(point)
    raise ValueError("unsupported Sketcher geometry type: " + safe_text(kind))


def add_sketch_geometries(sketch, patch):
    ensure_sketch(sketch)
    results = []
    for item in patch_items(
        patch,
        "geometry",
        "geometries",
        "add_geometry requires geometry or geometries",
    ):
        construction = bool(item.get("construction", patch.get("construction", False)))
        geometries = make_sketch_geometry(item)
        if not isinstance(geometries, list):
            geometries = [geometries]
        for geometry in geometries:
            geometry_index = sketch.addGeometry(geometry, construction)
            summary = sketch_geometry_summary(geometry, int(geometry_index), sketch)
            summary["geometry_index"] = int(geometry_index)
            results.append(summary)
    return {"geometry_results": results}


def add_sketch_external_geometry(doc, sketch, patch):
    ensure_sketch(sketch)
    selector = (
        patch.get("source_selector")
        or patch.get("support_selector")
        or patch.get("part_selector")
        or patch.get("feature_selector")
        or patch.get("target_selector")
        or {}
    )
    source = select_single_object(doc, selector)
    references = patch.get("references")
    if references is not None:
        if not isinstance(references, list) or len(references) > 40:
            raise ValueError("add_external_geometry references must be a list with at most 40 items")
        items = [safe_text(item, 160) for item in references]
    elif patch.get("reference") is not None:
        items = [safe_text(patch["reference"], 160)]
    else:
        external_items = patch_items(
            patch,
            "external_geometry",
            "external_geometries",
            "add_external_geometry requires reference/references or external_geometry/external_geometries",
        )
        items = [
            safe_text(
                item.get("reference")
                or item.get("element")
                or item.get("subelement")
                or item.get("sub"),
                160,
            )
            for item in external_items
        ]
    if any(not item for item in items):
        raise ValueError("add_external_geometry references must be non-empty")
    stable_ids = patch.get("stable_ids") if isinstance(patch.get("stable_ids"), list) else []
    stable_signatures = patch.get("stable_signatures") if isinstance(patch.get("stable_signatures"), list) else []
    if patch.get("signature") and not stable_signatures:
        stable_signatures = [patch.get("signature")]
    stable_references = patch.get("stable_references") if isinstance(patch.get("stable_references"), list) else []
    if patch.get("stable_reference") and not stable_references:
        stable_references = [patch.get("stable_reference")]
    old_count = len(sketch_external_geometry_summary(sketch))
    results = []
    for index, reference in enumerate(items):
        stable_id = stable_ids[index] if index < len(stable_ids) else ""
        signature = stable_signatures[index] if index < len(stable_signatures) else None
        stable_reference = stable_references[index] if index < len(stable_references) else ""
        resolved_reference, detail = resolve_subelement_reference_on_object(
            source,
            reference=reference,
            stable_id=stable_id,
            kind=patch.get("kind") or reference,
            signature=signature,
            stable_reference=stable_reference,
        )
        sketch.addExternal(safe_text(getattr(source, "Name", "")), resolved_reference)
        results.append({"source": object_ref(source), "reference": resolved_reference, "reference_diagnostics": detail})
    try:
        doc.recompute()
    except Exception:
        pass
    external_geometry = sketch_external_geometry_summary(sketch)
    return {
        "sketch": object_ref(sketch),
        "source": object_ref(source),
        "external_geometry_results": results,
        "old_external_geometry_count": old_count,
        "external_geometry_count": len(external_geometry),
        "external_geometry": external_geometry,
    }


def solve_sketch_status(doc, sketch, patch):
    status = sketch_solver_status(sketch, solve=True)
    try:
        doc.recompute()
    except Exception:
        pass
    return {
        "sketch": object_ref(sketch),
        **status,
        "sketch_summary": sketch_summary(sketch),
    }


def validate_sketch(doc, sketch, patch):
    ensure_sketch(sketch)
    solve = patch.get("solve")
    if solve is None:
        solve = True
    solver = sketch_solver_status(sketch, solve=bool(solve))
    diagnostics = {}
    for key, method_name in [
        ("solver_messages", "getSolverMessages"),
        ("conflicting_constraints", "getConflicting"),
        ("redundant_constraints", "getRedundant"),
        ("missing_point_on_point", "detectMissingPointOnPointConstraints"),
    ]:
        method = getattr(sketch, method_name, None)
        if not callable(method):
            continue
        try:
            diagnostics[key] = safe_value(method())
        except Exception as exc:
            diagnostics[key + "_error"] = safe_text(exc)
    try:
        doc.recompute()
    except Exception:
        pass
    summary = sketch_summary(sketch)
    valid = solver.get("error") is None
    if solver.get("degrees_of_freedom") is not None:
        valid = valid and int(solver["degrees_of_freedom"]) >= 0
    if diagnostics.get("conflicting_constraints"):
        valid = False
    return {
        "sketch": object_ref(sketch),
        "valid": valid,
        "solver": solver,
        "diagnostics": diagnostics,
        "degrees_of_freedom": solver.get("degrees_of_freedom"),
        "fully_constrained": solver.get("fully_constrained"),
        "geometry_count": summary.get("geometry_count") if summary else None,
        "constraint_count": summary.get("constraint_count") if summary else None,
        "sketch_summary": summary,
    }


def constraint_payload_value(payload, *keys):
    for key in keys:
        if payload.get(key) is not None:
            return payload.get(key)
    return None


def safe_constraint_arg(value):
    if isinstance(value, bool):
        raise ValueError("constraint args must be numeric or strings")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return value
    raise ValueError("constraint args must be numeric or strings")


def constraint_args_from_payload(payload):
    raw_args = payload.get("args")
    if raw_args is not None:
        if not isinstance(raw_args, list) or len(raw_args) > 12:
            raise ValueError("constraint args must be a list with at most 12 items")
        return [safe_constraint_arg(item) for item in raw_args]

    first = constraint_payload_value(payload, "first", "first_index", "geometry_index")
    first_pos = constraint_payload_value(payload, "first_pos", "firstPos")
    second = constraint_payload_value(payload, "second", "second_index")
    second_pos = constraint_payload_value(payload, "second_pos", "secondPos")
    third = constraint_payload_value(payload, "third", "third_index")
    third_pos = constraint_payload_value(payload, "third_pos", "thirdPos")
    args = []
    for value in [first, first_pos, second, second_pos, third, third_pos]:
        if value is not None:
            args.append(safe_constraint_arg(value))
    if payload.get("value") is not None:
        args.append(safe_constraint_arg(payload["value"]))
    return args


def make_sketch_constraint(payload):
    if Sketcher is None:
        raise ValueError("Sketcher module is unavailable in this FreeCAD runtime")
    raw_type = safe_text(payload.get("type") or payload.get("constraint_type"), 80)
    constraint_type = SKETCH_CONSTRAINT_TYPE_BY_KEY.get(raw_type.lower())
    if constraint_type is None:
        raise ValueError("unsupported Sketcher constraint type: " + raw_type)
    args = constraint_args_from_payload(payload)
    if not args and constraint_type not in {"Block"}:
        raise ValueError("Sketcher constraint requires typed args")
    return Sketcher.Constraint(constraint_type, *args)


def add_sketch_constraints(sketch, patch):
    ensure_sketch(sketch)
    results = []
    for item in patch_items(
        patch,
        "constraint",
        "constraints",
        "add_constraint requires constraint or constraints",
    ):
        constraint = make_sketch_constraint(item)
        constraint_index = sketch.addConstraint(constraint)
        if isinstance(constraint_index, (list, tuple)):
            constraint_index = constraint_index[0]
        constraint_index = int(constraint_index)
        name = item.get("name") or item.get("constraint_name")
        if name:
            try:
                sketch.renameConstraint(constraint_index, safe_text(name, 160))
            except Exception:
                pass
        results.append(constraint_summary(sketch.Constraints[constraint_index], constraint_index))
    return {"constraint_results": results}


def remove_sketch_constraint(sketch, patch):
    ensure_sketch(sketch)
    index = find_constraint_index(sketch, patch.get("constraint") or patch)
    removed = constraint_summary(sketch.Constraints[index], index)
    sketch.delConstraint(index)
    return {
        "removed_constraint": removed,
        "constraint_index": index,
    }


def placement_payload(patch):
    payload = patch.get("placement")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("placement must be an object")
    merged = dict(payload)
    for key in ["base", "axis", "angle_degrees", "angle_radians"]:
        if key in patch and patch.get(key) is not None:
            merged[key] = patch.get(key)
    return merged


def set_object_placement(obj, patch):
    payload = placement_payload(patch)
    if "base" not in payload and "position" in payload:
        payload["base"] = payload["position"]
    base = vector_from_value(payload.get("base", [0, 0, 0]), "placement.base")
    axis = vector_from_value(payload.get("axis", [0, 0, 1]), "placement.axis")
    angle = payload.get("angle_degrees")
    if angle is None and payload.get("angle_radians") is not None:
        angle = float(payload["angle_radians"]) * 180.0 / 3.141592653589793
    if angle is None:
        angle = 0.0
    old_placement = placement_summary(obj)
    obj.Placement = FreeCAD.Placement(base, FreeCAD.Rotation(axis, float(angle)))
    return {
        "old_placement": old_placement,
        "new_placement": placement_summary(obj),
    }


def set_object_expressions(obj, expressions):
    if not expressions:
        raise ValueError("set_expression requires expression")
    if not hasattr(obj, "setExpression"):
        raise ValueError("selected object does not support expressions")
    blocked = {"Shape", "Mesh", "Proxy", "ExpressionEngine", "ViewObject"}
    results = []
    for property_name, expression in expressions.items():
        if not property_name or property_name in blocked or str(property_name).startswith("__"):
            raise ValueError("refusing to set unsafe FreeCAD expression property: " + safe_text(property_name))
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("expression must be a non-empty string")
        old_expression = None
        try:
            old_expression = obj.getExpression(property_name)
        except Exception:
            pass
        obj.setExpression(property_name, expression)
        results.append({
            "property": property_name,
            "old_expression": safe_value(old_expression),
            "expression": expression,
        })
    return {"expressions": results}


def set_object_expression(obj, patch):
    expressions = patch_mapping(patch, "expressions")
    property_name = patch.get("property")
    expression = patch.get("expression")
    if property_name is not None or expression is not None:
        if not property_name or expression is None:
            raise ValueError("set_expression requires property and expression")
        expressions[property_name] = expression
    return set_object_expressions(obj, expressions)


def assign_feature_properties(obj, properties):
    details = []
    for property_name, value in properties.items():
        details.append(assign_property_value(obj, property_name, value))
    return details


def create_feature(doc, patch):
    type_id = safe_text(patch.get("type_id", ""))
    if type_id not in SUPPORTED_FEATURE_TYPES:
        raise ValueError("unsupported FreeCAD feature type_id: " + type_id)
    if type_id.startswith("PartDesign::"):
        return create_fallback_partdesign_feature(doc, patch)
    name = safe_feature_name(patch.get("name"), "Feature")
    label = patch.get("label")
    body = None
    obj = doc.addObject(type_id, name)

    if label is not None:
        obj.Label = safe_text(label, 160)
    property_details = assign_feature_properties(obj, patch_mapping(patch, "properties"))
    placement_detail = None
    if patch.get("placement") is not None or patch.get("base") is not None:
        placement_detail = set_object_placement(obj, patch)
    expression_detail = None
    if patch.get("expressions") or patch.get("expression") is not None:
        expression_detail = set_object_expression(obj, patch)
    if patch.get("set_body_tip") and body is not None:
        set_body_tip(doc, {"selector": object_ref(body), "tip_selector": object_ref(obj)})
    return {
        "created_type_id": type_id,
        "created_name": safe_text(getattr(obj, "Name", "")),
        "created_label": safe_text(getattr(obj, "Label", "")),
        "body": object_ref(body) if body is not None else None,
        "properties": property_details,
        "placement": placement_detail,
        "expression_results": expression_detail.get("expressions") if expression_detail else [],
    }, obj


def delete_feature(doc, obj):
    ref = object_ref(obj)
    doc.removeObject(obj.Name)
    return {"removed_object": ref}


def set_body_tip(doc, patch):
    body_selector = patch.get("selector") or patch.get("body_selector") or {}
    tip_selector = patch.get("tip_selector") or patch.get("feature_selector") or {}
    try:
        return set_fallback_body_tip(doc, patch)
    except ValueError:
        pass
    body = select_single_object(doc, body_selector)
    tip = select_single_object(doc, tip_selector)
    if safe_text(getattr(body, "TypeId", "")) != PARTDESIGN_BODY_TYPE:
        raise ValueError("selector must match a PartDesign::Body")
    old_tip = None
    try:
        if getattr(body, "Tip", None) is not None:
            old_tip = object_ref(body.Tip)
    except Exception:
        old_tip = None
    if hasattr(body, "addObject") and tip not in list(getattr(body, "Group", [])):
        try:
            body.addObject(tip)
        except Exception:
            pass
    if hasattr(body, "setTip"):
        body.setTip(tip)
    else:
        body.Tip = tip
    return {
        "body": object_ref(body),
        "old_tip": old_tip,
        "new_tip": object_ref(tip),
    }, body


def select_assembly(doc, patch):
    selector = patch.get("assembly_selector") or patch.get("selector") or {}
    assembly = select_single_object(doc, selector)
    if not is_assembly_object(assembly):
        raise ValueError("selector must match an Assembly::AssemblyObject")
    return assembly


def select_assembly_part(doc, patch):
    selector = patch.get("part_selector") or patch.get("feature_selector") or {}
    return select_single_object(doc, selector)


def ensure_joint_group(assembly):
    for obj in list(getattr(assembly, "Group", []) or []):
        if is_assembly_joint_group(obj):
            return obj
    if hasattr(assembly, "newObject"):
        return assembly.newObject(ASSEMBLY_JOINT_GROUP_TYPE, "Joints")
    raise ValueError("assembly object cannot create a joint group")


def normalize_assembly_joint_type(value):
    raw = safe_text(value or "", 80).strip()
    normalized = raw.replace("-", "_").replace(" ", "_").lower()
    joint_type = SUPPORTED_ASSEMBLY_JOINT_TYPES.get(normalized)
    if joint_type is None:
        raise ValueError("unsupported Assembly joint_type: " + raw)
    return joint_type


def select_assembly_joint(doc, patch):
    selector = patch.get("joint_selector") or patch.get("selector") or {}
    joint = select_single_object(doc, selector)
    if not is_assembly_joint(joint) or not hasattr(joint, "JointType"):
        raise ValueError("selector must match an Assembly joint with JointType")
    return joint


def assembly_for_joint(doc, joint, patch=None):
    if patch:
        selector = patch.get("assembly_selector")
        if selector:
            return select_assembly(doc, {"selector": selector})
    try:
        proxy = getattr(joint, "Proxy", None)
        if proxy is not None and hasattr(proxy, "getAssembly"):
            assembly = proxy.getAssembly(joint)
            if is_assembly_object(assembly):
                return assembly
    except Exception:
        pass
    seen = set()
    stack = list(getattr(joint, "InList", []) or [])
    while stack:
        obj = stack.pop(0)
        key = object_identity(obj)
        if key in seen:
            continue
        seen.add(key)
        if is_assembly_object(obj):
            return obj
        try:
            stack.extend(list(getattr(obj, "InList", []) or []))
        except Exception:
            pass
    if patch:
        return select_assembly(doc, patch)
    raise ValueError("could not find parent Assembly for joint")


def assembly_connector_from_payload(doc, payload, fallback_selector=None):
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("assembly connector must be an object")
    selector = (
        payload.get("selector")
        or payload.get("object_selector")
        or payload.get("part_selector")
        or fallback_selector
        or {}
    )
    obj = select_single_object(doc, selector)
    subelements = payload.get("subelements")
    if subelements is not None:
        if not isinstance(subelements, list) or len(subelements) > 2:
            raise ValueError("connector.subelements must be a list with at most 2 items")
        values = [safe_text(value, 160) for value in subelements]
    else:
        element = (
            payload.get("element")
            or payload.get("subelement")
            or payload.get("sub")
            or payload.get("reference")
            or ""
        )
        vertex = (
            payload.get("vertex")
            or payload.get("vertex_reference")
            or payload.get("point_reference")
            or ""
        )
        values = [safe_text(element, 160), safe_text(vertex, 160)]
    while len(values) < 2:
        values.append("")
    stable_ids = payload.get("stable_ids") if isinstance(payload.get("stable_ids"), list) else []
    if payload.get("stable_id") and not stable_ids:
        stable_ids = [payload.get("stable_id")]
    stable_signatures = payload.get("stable_signatures") if isinstance(payload.get("stable_signatures"), list) else []
    if payload.get("signature") and not stable_signatures:
        stable_signatures = [payload.get("signature")]
    stable_references = payload.get("stable_references") if isinstance(payload.get("stable_references"), list) else []
    if payload.get("stable_reference") and not stable_references:
        stable_references = [payload.get("stable_reference")]
    resolved = []
    for index, reference in enumerate(values[:2]):
        stable_id = stable_ids[index] if index < len(stable_ids) else ""
        signature = stable_signatures[index] if index < len(stable_signatures) else None
        stable_reference = stable_references[index] if index < len(stable_references) else ""
        kind = payload.get("kind") or payload.get("subelement_kind") or reference
        resolved_reference, _detail = resolve_subelement_reference_on_object(
            obj,
            reference=reference,
            stable_id=stable_id,
            kind=kind,
            signature=signature,
            stable_reference=stable_reference,
        )
        resolved.append(resolved_reference)
    while len(resolved) < 2:
        resolved.append("")
    return [obj, resolved[:2]]


def connector_payloads_from_patch(patch):
    connectors = patch.get("connectors")
    if connectors is not None:
        if not isinstance(connectors, list) or len(connectors) != 2:
            raise ValueError("create_joint/update_joint connectors must contain exactly 2 items")
        return connectors
    connector1 = patch.get("connector1")
    connector2 = patch.get("connector2")
    if connector1 is None and patch.get("part1_selector") is not None:
        connector1 = {"selector": patch.get("part1_selector")}
    if connector1 is None and patch.get("part_selector") is not None:
        connector1 = {"selector": patch.get("part_selector")}
    if connector2 is None and patch.get("part2_selector") is not None:
        connector2 = {"selector": patch.get("part2_selector")}
    if connector2 is None and patch.get("target_selector") is not None:
        connector2 = {"selector": patch.get("target_selector")}
    if connector1 is None and connector2 is None:
        return None
    if connector1 is None or connector2 is None:
        raise ValueError("Assembly joint requires both connector1 and connector2")
    return [connector1, connector2]


def truthy_env(name):
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def native_assembly_available(*, require_joints=False):
    if not truthy_env("FOURYI_FREECAD_PERSIST_NATIVE_ASSEMBLY"):
        return False
    if Assembly is None:
        return False
    if require_joints and JointObject is None:
        return False
    return True


def native_assembly_solver_available():
    return Assembly is not None and JointObject is not None


def assembly_runtime_capabilities():
    module_available = Assembly is not None
    joint_object_available = JointObject is not None
    persist_enabled = truthy_env("FOURYI_FREECAD_PERSIST_NATIVE_ASSEMBLY")
    return {
        "schema": "freecad.assembly_capabilities.v1",
        "assembly_module_available": module_available,
        "joint_object_available": joint_object_available,
        "persistent_native_enabled": persist_enabled,
        "persistent_native_available": bool(persist_enabled and module_available),
        "native_solver_available": bool(module_available and joint_object_available),
        "default_backend": "native_persistent" if native_assembly_available() else "typed_state_native_solver",
        "supported_joint_types": sorted(SUPPORTED_ASSEMBLY_JOINT_TYPES),
        "requires_env": "FOURYI_FREECAD_PERSIST_NATIVE_ASSEMBLY=1",
    }


def native_techdraw_available():
    return TechDraw is not None and truthy_env("FOURYI_FREECAD_NATIVE_TECHDRAW")


def techdraw_runtime_capabilities():
    module_available = TechDraw is not None
    svg_native = bool(module_available and hasattr(TechDraw, "viewPartAsSvg"))
    dxf_native = bool(module_available and hasattr(TechDraw, "writeDXFPage"))
    pdf_converter = resolve_rsvg_convert()
    return {
        "schema": "freecad.techdraw_capabilities.v1",
        "techdraw_module_available": module_available,
        "native_creation_enabled": native_techdraw_available(),
        "native_svg_export_available": svg_native,
        "native_dxf_export_available": dxf_native,
        "pdf_converter_available": bool(pdf_converter),
        "pdf_converter": safe_text(pdf_converter) if pdf_converter else None,
        "fallback_export_available": True,
        "fallback_status": "typed_vector_preview",
        "product_grade_requires": [
            "native TechDraw page/view objects",
            "template/title block",
            "native SVG/DXF export",
            "PDF converter",
        ],
        "requires_env": "FOURYI_FREECAD_NATIVE_TECHDRAW=1",
    }


def native_assembly_detail(detail):
    detail.update({
        "assembly_backend": "native",
        "fallback": False,
        "product_grade": True,
        "status": "native_assembly",
    })
    return detail


def assembly_fallback_or_raise(fallback_func, doc, patch, exc):
    try:
        detail, obj = fallback_func(doc, patch)
    except Exception:
        raise exc
    detail["native_error"] = safe_text(exc)
    return detail, obj


def set_assembly_joint_connectors(doc, joint, patch):
    payloads = connector_payloads_from_patch(patch)
    if payloads is None:
        return None
    refs = [
        assembly_connector_from_payload(doc, payloads[0]),
        assembly_connector_from_payload(doc, payloads[1]),
    ]
    if not hasattr(joint, "Proxy") or not hasattr(joint.Proxy, "setJointConnectors"):
        raise ValueError("selected joint cannot set Assembly connectors")
    joint.Proxy.setJointConnectors(joint, refs)
    return {
        "reference1": assembly_reference_summary(getattr(joint, "Reference1", None)),
        "reference2": assembly_reference_summary(getattr(joint, "Reference2", None)),
    }


def set_assembly_joint_scalars(joint, patch):
    results = []
    if patch.get("distance") is not None or (
        patch.get("value") is not None and safe_text(getattr(joint, "JointType", "")) == "Distance"
    ):
        value = patch.get("distance")
        if value is None:
            value = patch.get("value")
        old_value = quantity_summary(getattr(joint, "Distance", None))
        joint.Distance = float(value)
        results.append({
            "property": "Distance",
            "old_value": old_value,
            "new_value": quantity_summary(getattr(joint, "Distance", None)),
        })
    angle = patch.get("angle_degrees")
    if angle is None and patch.get("angle_radians") is not None:
        angle = float(patch["angle_radians"]) * 180.0 / 3.141592653589793
    if angle is None and patch.get("value") is not None and safe_text(getattr(joint, "JointType", "")) == "Angle":
        angle = patch.get("value")
    if angle is not None:
        old_value = quantity_summary(getattr(joint, "Angle", None))
        joint.Angle = float(angle)
        results.append({
            "property": "Angle",
            "old_value": old_value,
            "new_value": quantity_summary(getattr(joint, "Angle", None)),
        })
    return results


def solve_assembly(doc, patch):
    if not native_assembly_available():
        return solve_fallback_assembly(doc, patch)
    try:
        assembly = select_assembly(doc, patch)
        if not hasattr(assembly, "solve"):
            raise ValueError("selected Assembly object does not support solve()")
        try:
            status = assembly.solve()
        except TypeError:
            status = assembly.solve(False)
        try:
            doc.recompute()
        except Exception:
            pass
        summary = assembly_summary(assembly)
        return native_assembly_detail({
            "assembly": object_ref(assembly),
            "solver_status": safe_value(status),
            "solver_backend": "native",
            "solver_diagnostics": summary.get("solver_diagnostics"),
            "assembly_summary": summary,
        }), assembly
    except Exception as exc:
        return assembly_fallback_or_raise(solve_fallback_assembly, doc, patch, exc)


def create_assembly(doc, patch):
    if not native_assembly_available():
        return create_fallback_assembly(doc, patch)
    name = safe_feature_name(patch.get("name"), "Assembly")
    label = patch.get("label")
    try:
        assembly = doc.addObject(ASSEMBLY_TYPE, name)
        backend = "native"
    except Exception as exc:
        detail, obj = create_fallback_assembly(doc, patch)
        detail["native_error"] = safe_text(exc)
        return detail, obj
    if label is not None:
        assembly.Label = safe_text(label, 160)
    joint_group = None
    if is_assembly_object(assembly):
        joint_group = ensure_joint_group(assembly)
    if patch.get("placement") is not None or patch.get("base") is not None:
        set_object_placement(assembly, patch)
    return native_assembly_detail({
        "created_type_id": safe_text(getattr(assembly, "TypeId", "")),
        "created_name": safe_text(getattr(assembly, "Name", "")),
        "created_label": safe_text(getattr(assembly, "Label", "")),
        "assembly_backend": backend,
        "joint_group": object_ref(joint_group) if joint_group is not None else None,
    }), assembly


def add_part_to_assembly(doc, patch):
    if not native_assembly_available():
        return add_fallback_part_to_assembly(doc, patch)
    try:
        assembly = select_assembly(doc, patch)
        part = select_assembly_part(doc, patch)
        if hasattr(assembly, "addObject"):
            assembly.addObject(part)
        elif hasattr(assembly, "Group"):
            group = list(getattr(assembly, "Group", []) or [])
            if part not in group:
                assembly.Group = group + [part]
        else:
            raise ValueError("assembly object cannot contain parts")
        if patch.get("placement") is not None or patch.get("base") is not None:
            set_object_placement(part, patch)
        return native_assembly_detail({
            "assembly": object_ref(assembly),
            "part": object_ref(part),
            "part_placement": placement_summary(part),
        }), assembly
    except Exception as exc:
        return assembly_fallback_or_raise(add_fallback_part_to_assembly, doc, patch, exc)


def remove_part_from_assembly(doc, patch):
    if not native_assembly_available():
        return remove_fallback_part_from_assembly(doc, patch)
    try:
        assembly = select_assembly(doc, patch)
        part = select_assembly_part(doc, patch)
        if hasattr(assembly, "removeObject"):
            assembly.removeObject(part)
        elif hasattr(assembly, "Group"):
            assembly.Group = [obj for obj in list(getattr(assembly, "Group", []) or []) if obj != part]
        else:
            raise ValueError("assembly object cannot remove parts")
        return native_assembly_detail({
            "assembly": object_ref(assembly),
            "part": object_ref(part),
        }), assembly
    except Exception as exc:
        return assembly_fallback_or_raise(remove_fallback_part_from_assembly, doc, patch, exc)


def set_assembly_part_placement(doc, patch):
    if not native_assembly_available():
        return set_fallback_assembly_part_placement(doc, patch)
    try:
        assembly = select_assembly(doc, patch)
        part = select_assembly_part(doc, patch)
        if assembly not in list(getattr(part, "InList", []) or []):
            raise ValueError("selected part is not a member of the selected assembly")
        detail = set_object_placement(part, patch)
        detail.update({
            "assembly": object_ref(assembly),
            "part": object_ref(part),
        })
        return native_assembly_detail(detail), assembly
    except Exception as exc:
        return assembly_fallback_or_raise(set_fallback_assembly_part_placement, doc, patch, exc)


def ground_assembly_part(doc, patch):
    if not native_assembly_available(require_joints=True):
        return ground_fallback_assembly_part(doc, patch)
    try:
        assembly = select_assembly(doc, patch)
        part = select_assembly_part(doc, patch)
        if assembly not in list(getattr(part, "InList", []) or []):
            raise ValueError("selected part is not a member of the selected assembly")
        joint_group = ensure_joint_group(assembly)
        name = safe_feature_name(patch.get("name"), "GroundedJoint")
        grounded = joint_group.newObject("App::FeaturePython", name)
        if patch.get("label"):
            grounded.Label = safe_text(patch["label"], 160)
        JointObject.GroundedJoint(grounded, part)
        return native_assembly_detail({
            "assembly": object_ref(assembly),
            "part": object_ref(part),
            "joint": object_ref(grounded),
            "joint_group": object_ref(joint_group),
        }), assembly
    except Exception as exc:
        return assembly_fallback_or_raise(ground_fallback_assembly_part, doc, patch, exc)


def create_assembly_joint(doc, patch):
    if not native_assembly_available(require_joints=True):
        return create_fallback_assembly_joint(doc, patch)
    try:
        assembly = select_assembly(doc, patch)
        joint_type = normalize_assembly_joint_type(patch.get("joint_type") or patch.get("type"))
        joint_types = list(getattr(JointObject, "JointTypes", []))
        if joint_type not in joint_types:
            raise ValueError("Assembly runtime does not support joint_type: " + joint_type)
        joint_group = ensure_joint_group(assembly)
        name = safe_feature_name(patch.get("name"), joint_type + "Joint")
        joint = joint_group.newObject("App::FeaturePython", name)
        if patch.get("label"):
            joint.Label = safe_text(patch["label"], 160)
        JointObject.Joint(joint, joint_types.index(joint_type))
        scalar_results = set_assembly_joint_scalars(joint, patch)
        connector_detail = set_assembly_joint_connectors(doc, joint, patch)
        solve_detail = None
        if patch.get("solve", True):
            solve_detail, _ = solve_assembly(doc, {"selector": object_ref(assembly)})
        return native_assembly_detail({
            "assembly": object_ref(assembly),
            "joint_group": object_ref(joint_group),
            "joint": assembly_joint_summary(joint),
            "joint_type": safe_text(getattr(joint, "JointType", "")),
            "scalar_results": scalar_results,
            "connector_results": connector_detail,
            "solve_result": solve_detail,
        }), joint
    except Exception as exc:
        return assembly_fallback_or_raise(create_fallback_assembly_joint, doc, patch, exc)


def update_assembly_joint(doc, patch):
    if not native_assembly_available(require_joints=True):
        return update_fallback_assembly_joint(doc, patch)
    try:
        joint = select_assembly_joint(doc, patch)
        assembly = assembly_for_joint(doc, joint, patch)
        old_joint_type = safe_text(getattr(joint, "JointType", ""))
        if patch.get("joint_type") is not None or patch.get("type") is not None:
            joint_type = normalize_assembly_joint_type(patch.get("joint_type") or patch.get("type"))
            if hasattr(joint, "Proxy") and hasattr(joint.Proxy, "setJointType"):
                joint.Proxy.setJointType(joint, joint_type)
            else:
                joint.JointType = joint_type
        scalar_results = set_assembly_joint_scalars(joint, patch)
        connector_detail = set_assembly_joint_connectors(doc, joint, patch)
        solve_detail = None
        if patch.get("solve", True):
            solve_detail, _ = solve_assembly(doc, {"selector": object_ref(assembly)})
        return native_assembly_detail({
            "assembly": object_ref(assembly),
            "joint": assembly_joint_summary(joint),
            "old_joint_type": old_joint_type,
            "new_joint_type": safe_text(getattr(joint, "JointType", "")),
            "scalar_results": scalar_results,
            "connector_results": connector_detail,
            "solve_result": solve_detail,
        }), joint
    except Exception as exc:
        return assembly_fallback_or_raise(update_fallback_assembly_joint, doc, patch, exc)


def default_techdraw_template_path():
    candidates = []
    try:
        resource_dir = FreeCAD.getResourceDir()
        candidates.extend([
            os.path.join(resource_dir, "Mod", "TechDraw", "TDTest", "TestTemplate.svg"),
            os.path.join(resource_dir, "..", "Mod", "TechDraw", "TDTest", "TestTemplate.svg"),
            os.path.join(resource_dir, "Mod", "TechDraw", "Templates", "A4_LandscapeTD.svg"),
            os.path.join(resource_dir, "..", "Mod", "TechDraw", "Templates", "A4_LandscapeTD.svg"),
        ])
    except Exception:
        pass
    for path in candidates:
        normalized = os.path.abspath(path)
        if os.path.isfile(normalized):
            return normalized
    raise ValueError("no bundled TechDraw SVG template found")


def resolve_techdraw_template_path(patch):
    requested = patch.get("template_path")
    if not requested:
        return default_techdraw_template_path()
    normalized = os.path.abspath(str(requested))
    if not normalized.lower().endswith(".svg"):
        raise ValueError("TechDraw template_path must point to an SVG file")
    try:
        resource_root = os.path.abspath(os.path.join(FreeCAD.getResourceDir(), ".."))
    except Exception:
        resource_root = ""
    if resource_root and not normalized.startswith(resource_root + os.sep):
        raise ValueError("TechDraw template_path must be inside the FreeCAD resource directory")
    if not os.path.isfile(normalized):
        raise ValueError("TechDraw template_path does not exist")
    return normalized


def assign_techdraw_native_layout(view, patch):
    for patch_key, prop in [
        ("x", "X"),
        ("y", "Y"),
        ("scale", "Scale"),
        ("rotation", "Rotation"),
    ]:
        if patch.get(patch_key) is None or not hasattr(view, prop):
            continue
        try:
            setattr(view, prop, float(patch[patch_key]))
        except Exception:
            pass
    for patch_key, prop in [
        ("direction", "Direction"),
        ("x_direction", "XDirection"),
        ("section_normal", "SectionNormal"),
    ]:
        vector = techdraw_vector_summary_from_patch(patch, patch_key)
        if not vector or not hasattr(view, prop):
            continue
        try:
            setattr(view, prop, FreeCAD.Vector(float(vector[0]), float(vector[1]), float(vector[2])))
        except Exception:
            pass


def add_native_techdraw_view_to_page(page, view):
    if hasattr(page, "addView"):
        page.addView(view)
        return
    views = list(getattr(page, "Views", []) or [])
    if view not in views:
        try:
            page.Views = views + [view]
        except Exception:
            pass


def select_techdraw_page(doc, patch):
    selector = patch.get("page_selector") or patch.get("selector") or {}
    page = select_single_object(doc, selector)
    if not is_techdraw_page(page):
        raise ValueError("selector must match a TechDraw::DrawPage")
    return page


def select_techdraw_view(doc, patch):
    selector = patch.get("view_selector") or patch.get("selector") or {}
    view = select_single_object(doc, selector)
    if not is_techdraw_view(view):
        raise ValueError("selector must match a TechDraw view")
    return view


def select_techdraw_base_view(doc, patch):
    selector = patch.get("base_view_selector") or patch.get("view_selector") or patch.get("selector") or {}
    return select_techdraw_view(doc, {"selector": selector})


def select_techdraw_source(doc, patch):
    selector = patch.get("source_selector") or patch.get("part_selector") or patch.get("feature_selector") or {}
    return select_single_object(doc, selector)


def summary_ref(name, label=None, type_id=""):
    return {
        "name": safe_text(name, 80),
        "label": safe_text(label if label is not None else name, 160),
        "type_id": safe_text(type_id, 120),
    }


def matches_summary_selector(item, selector):
    if not isinstance(selector, dict) or not selector:
        return False
    checks = {
        "name": safe_text(item.get("name", "")),
        "label": safe_text(item.get("label", "")),
        "type_id": safe_text(item.get("type_id", "")),
    }
    for key, expected in selector.items():
        if key not in checks or expected is None:
            continue
        if checks[key] != safe_text(expected):
            return False
    return True


def select_techdraw_page_model(doc, selector):
    state = load_techdraw_fallback_state(doc)
    matches = [
        page
        for page in list(state.get("pages", {}).values())
        if isinstance(page, dict) and matches_summary_selector(page, selector)
    ]
    if not matches:
        raise ValueError("no TechDraw fallback page matched selector: " + json.dumps(selector))
    if len(matches) > 1:
        raise ValueError("TechDraw fallback page selector matched multiple pages: " + json.dumps(selector))
    return state, matches[0]


def select_techdraw_view_model(doc, selector):
    state = load_techdraw_fallback_state(doc)
    matches = []
    for page in list(state.get("pages", {}).values()):
        if not isinstance(page, dict):
            continue
        for view in list(page.get("views") or []) + list(page.get("dimensions") or []):
            if isinstance(view, dict) and matches_summary_selector(view, selector):
                matches.append((page, view))
    if not matches:
        raise ValueError("no TechDraw fallback view matched selector: " + json.dumps(selector))
    if len(matches) > 1:
        raise ValueError("TechDraw fallback view selector matched multiple views: " + json.dumps(selector))
    return state, matches[0][0], matches[0][1]


def save_techdraw_page_model(doc, state, page):
    name = safe_text(page.get("name"), 80)
    if not name:
        raise ValueError("TechDraw fallback page requires name")
    state.setdefault("pages", {})[name] = page
    save_techdraw_fallback_state(doc, state)


def add_techdraw_fallback_view_model(doc, page, view):
    state = load_techdraw_fallback_state(doc)
    current = state.get("pages", {}).get(page.get("name"), page)
    views = [item for item in list(current.get("views") or []) if safe_text(item.get("name"), 80) != safe_text(view.get("name"), 80)]
    views.append(view)
    current["views"] = views
    save_techdraw_page_model(doc, state, current)
    return current, view


def add_techdraw_fallback_dimension_model(doc, page, dimension):
    state = load_techdraw_fallback_state(doc)
    current = state.get("pages", {}).get(page.get("name"), page)
    dimensions = [
        item
        for item in list(current.get("dimensions") or [])
        if safe_text(item.get("name"), 80) != safe_text(dimension.get("name"), 80)
    ]
    dimensions.append(dimension)
    current["dimensions"] = dimensions
    save_techdraw_page_model(doc, state, current)
    return current, dimension


def feature_state_holder(doc, create=False):
    if doc is None:
        return None
    for obj in list(getattr(doc, "Objects", []) or []):
        try:
            if getattr(obj, "Name", "") == FEATURE_FALLBACK_STATE_OBJECT:
                return obj
        except Exception:
            pass
    if not create:
        return None
    holder = doc.addObject("App::DocumentObjectGroup", FEATURE_FALLBACK_STATE_OBJECT)
    try:
        holder.Label = FEATURE_FALLBACK_STATE_OBJECT
    except Exception:
        pass
    return holder


def load_feature_fallback_state(doc):
    holder = feature_state_holder(doc, create=False)
    raw = ""
    if holder is not None:
        try:
            raw = getattr(holder, FEATURE_FALLBACK_FEATURES_PROPERTY, "")
        except Exception:
            raw = ""
    if raw:
        try:
            state = json.loads(raw)
        except Exception:
            state = {}
    else:
        state = {}
    if not isinstance(state, dict):
        state = {}
    features = state.get("features")
    if not isinstance(features, dict):
        features = {}
    return {
        "schema": "freecad.feature_fallback.v1",
        "features": features,
    }


def save_feature_fallback_state(doc, state):
    holder = feature_state_holder(doc, create=True)
    if holder is None:
        raise ValueError("could not create PartDesign fallback state holder")
    if FEATURE_FALLBACK_FEATURES_PROPERTY not in list(getattr(holder, "PropertiesList", [])):
        holder.addProperty(
            "App::PropertyString",
            FEATURE_FALLBACK_FEATURES_PROPERTY,
            "4yi",
            "Headless PartDesign fallback feature metadata",
        )
    setattr(holder, FEATURE_FALLBACK_FEATURES_PROPERTY, json.dumps(state, ensure_ascii=False))


def feature_properties_from_mapping(properties):
    return [
        {
            "name": safe_text(name, 120),
            "type": "",
            "group": "Fallback",
            "value": safe_value(value),
        }
        for name, value in (properties or {}).items()
    ]


def placement_summary_from_patch(patch):
    if patch.get("placement") is None and patch.get("base") is None:
        return None
    payload = placement_payload(patch)
    if "base" not in payload and "position" in payload:
        payload["base"] = payload["position"]
    base = vector_summary(vector_from_value(payload.get("base", [0, 0, 0]), "placement.base"))
    axis = vector_summary(vector_from_value(payload.get("axis", [0, 0, 1]), "placement.axis"))
    angle = payload.get("angle_degrees")
    if angle is None and payload.get("angle_radians") is not None:
        angle = float(payload["angle_radians"]) * 180.0 / 3.141592653589793
    if angle is None:
        angle = 0.0
    return {
        "base": base,
        "axis": axis,
        "angle_degrees": float(angle),
    }


def select_fallback_feature(state, selector, *, type_id=None):
    matches = []
    for feature in list(state.get("features", {}).values()):
        if not isinstance(feature, dict) or not matches_summary_selector(feature, selector):
            continue
        if type_id is not None and feature.get("type_id") != type_id:
            continue
        matches.append(feature)
    if not matches:
        raise ValueError("no fallback feature matched selector: " + json.dumps(selector))
    if len(matches) > 1:
        raise ValueError("fallback feature selector matched multiple features: " + json.dumps(selector))
    return matches[0]


def fallback_feature_summary(feature):
    item = {
        **summary_ref(feature.get("name"), feature.get("label"), feature.get("type_id")),
        "placement": feature.get("placement"),
        "in_list": feature.get("in_list") or [],
        "out_list": feature.get("out_list") or [],
        "properties": feature.get("properties") or [],
        "fallback": True,
        "product_grade": False,
        "status": "headless_fallback",
    }
    return item


def feature_fallback_summaries(doc):
    state = load_feature_fallback_state(doc)
    return [
        fallback_feature_summary(feature)
        for feature in sorted(list(state.get("features", {}).values()), key=lambda item: safe_text(item.get("name", "")))
        if isinstance(feature, dict) and feature.get("name")
    ]


def fallback_feature_tree_nodes(doc):
    state = load_feature_fallback_state(doc)
    nodes = []
    roots = []
    for feature in sorted(list(state.get("features", {}).values()), key=lambda item: safe_text(item.get("name", ""))):
        if not isinstance(feature, dict) or not feature.get("name"):
            continue
        ref = summary_ref(feature.get("name"), feature.get("label"), feature.get("type_id"))
        parents = feature.get("in_list") or []
        node = {
            "object": ref,
            "kind": feature_kind_from_summary(feature),
            "parents": parents,
            "children": feature.get("out_list") or [],
            "tip": feature.get("tip"),
            "placement": feature.get("placement"),
        }
        nodes.append(node)
        if not parents:
            roots.append(ref)
    return {"roots": roots, "nodes": nodes}


def create_fallback_partdesign_feature(doc, patch):
    type_id = safe_text(patch.get("type_id", ""))
    name = safe_feature_name(patch.get("name"), "Feature")
    label = patch.get("label")
    state = load_feature_fallback_state(doc)
    features = state.setdefault("features", {})
    body_ref = None
    if type_id != PARTDESIGN_BODY_TYPE:
        body = select_fallback_feature(
            state,
            patch.get("body_selector") or patch.get("parent_selector") or {},
            type_id=PARTDESIGN_BODY_TYPE,
        )
        body_ref = summary_ref(body.get("name"), body.get("label"), body.get("type_id"))
    feature = {
        **summary_ref(name, label, type_id),
        "placement": placement_summary_from_patch(patch),
        "properties": feature_properties_from_mapping(patch_mapping(patch, "properties")),
        "in_list": [body_ref] if body_ref is not None else [],
        "out_list": [],
        "tip": None,
        "fallback": True,
        "product_grade": False,
        "status": "headless_fallback",
    }
    features[name] = feature
    if body_ref is not None:
        body = features[body_ref["name"]]
        children = [item for item in list(body.get("out_list") or []) if item.get("name") != name]
        children.append(summary_ref(name, label, type_id))
        body["out_list"] = children
        if patch.get("set_body_tip"):
            body["tip"] = summary_ref(name, label, type_id)
    save_feature_fallback_state(doc, state)
    return {
        "created_type_id": type_id,
        "created_name": name,
        "created_label": safe_text(label if label is not None else name, 160),
        "body": body_ref,
        "properties": feature.get("properties") or [],
        "placement": {"new_placement": feature.get("placement")} if feature.get("placement") else None,
        "expression_results": [],
        "fallback": True,
        "product_grade": False,
        "status": "headless_fallback",
    }, None


def set_fallback_body_tip(doc, patch):
    state = load_feature_fallback_state(doc)
    body = select_fallback_feature(
        state,
        patch.get("selector") or patch.get("body_selector") or {},
        type_id=PARTDESIGN_BODY_TYPE,
    )
    tip = select_fallback_feature(state, patch.get("tip_selector") or patch.get("feature_selector") or {})
    old_tip = body.get("tip")
    body["tip"] = summary_ref(tip.get("name"), tip.get("label"), tip.get("type_id"))
    children = [item for item in list(body.get("out_list") or []) if item.get("name") != tip.get("name")]
    children.append(body["tip"])
    body["out_list"] = children
    save_feature_fallback_state(doc, state)
    return {
        "body": summary_ref(body.get("name"), body.get("label"), body.get("type_id")),
        "old_tip": old_tip,
        "new_tip": body["tip"],
        "fallback": True,
    }, None


def assembly_state_holder(doc, create=False):
    if doc is None:
        return None
    for obj in list(getattr(doc, "Objects", []) or []):
        try:
            if getattr(obj, "Name", "") == ASSEMBLY_FALLBACK_STATE_OBJECT:
                return obj
        except Exception:
            pass
    if not create:
        return None
    holder = doc.addObject("App::DocumentObjectGroup", ASSEMBLY_FALLBACK_STATE_OBJECT)
    try:
        holder.Label = ASSEMBLY_FALLBACK_STATE_OBJECT
    except Exception:
        pass
    return holder


def load_assembly_fallback_state(doc):
    holder = assembly_state_holder(doc, create=False)
    raw = ""
    if holder is not None:
        try:
            raw = getattr(holder, ASSEMBLY_FALLBACK_ASSEMBLIES_PROPERTY, "")
        except Exception:
            raw = ""
    if raw:
        try:
            state = json.loads(raw)
        except Exception:
            state = {}
    else:
        state = {}
    if not isinstance(state, dict):
        state = {}
    assemblies = state.get("assemblies")
    if not isinstance(assemblies, dict):
        assemblies = {}
    return {
        "schema": "freecad.assembly_fallback.v1",
        "assemblies": assemblies,
    }


def save_assembly_fallback_state(doc, state):
    holder = assembly_state_holder(doc, create=True)
    if holder is None:
        raise ValueError("could not create Assembly fallback state holder")
    if ASSEMBLY_FALLBACK_ASSEMBLIES_PROPERTY not in list(getattr(holder, "PropertiesList", [])):
        holder.addProperty(
            "App::PropertyString",
            ASSEMBLY_FALLBACK_ASSEMBLIES_PROPERTY,
            "4yi",
            "Headless Assembly fallback metadata",
        )
    setattr(holder, ASSEMBLY_FALLBACK_ASSEMBLIES_PROPERTY, json.dumps(state, ensure_ascii=False))


def select_fallback_assembly(state, selector):
    matches = [
        assembly
        for assembly in list(state.get("assemblies", {}).values())
        if isinstance(assembly, dict) and matches_summary_selector(assembly, selector)
    ]
    if not matches:
        raise ValueError("no fallback assembly matched selector: " + json.dumps(selector))
    if len(matches) > 1:
        raise ValueError("fallback assembly selector matched multiple assemblies: " + json.dumps(selector))
    return matches[0]


def save_fallback_assembly(doc, state, assembly):
    name = safe_text(assembly.get("name"), 80)
    if not name:
        raise ValueError("Assembly fallback requires name")
    state.setdefault("assemblies", {})[name] = assembly
    save_assembly_fallback_state(doc, state)


def create_fallback_assembly(doc, patch):
    name = safe_feature_name(patch.get("name"), "Assembly")
    label = patch.get("label")
    assembly = {
        **summary_ref(name, label, ASSEMBLY_TYPE),
        "placement": placement_summary_from_patch(patch),
        "fallback": True,
        "product_grade": False,
        "status": "headless_fallback",
        "parts": [],
        "joints": [],
        "joint_groups": [],
        "solver_status": None,
    }
    state = load_assembly_fallback_state(doc)
    save_fallback_assembly(doc, state, assembly)
    return {
        "created_type_id": ASSEMBLY_TYPE,
        "created_name": name,
        "created_label": safe_text(label if label is not None else name, 160),
        "assembly_backend": "typed_fallback",
        "joint_group": None,
        "fallback": True,
        "product_grade": False,
        "status": "headless_fallback",
    }, None


def fallback_part_entry(part, patch=None):
    entry = object_ref(part)
    entry["kind"] = feature_kind(part)
    entry["placement"] = placement_summary(part)
    entry["grounded"] = False
    if patch and (patch.get("placement") is not None or patch.get("base") is not None):
        entry["placement"] = placement_summary(part)
    return entry


def add_fallback_part_to_assembly(doc, patch):
    state = load_assembly_fallback_state(doc)
    assembly = select_fallback_assembly(state, patch.get("assembly_selector") or patch.get("selector") or {})
    part = select_assembly_part(doc, patch)
    if patch.get("placement") is not None or patch.get("base") is not None:
        set_object_placement(part, patch)
    part_entry = fallback_part_entry(part, patch)
    parts = [item for item in list(assembly.get("parts") or []) if item.get("name") != part_entry["name"]]
    parts.append(part_entry)
    assembly["parts"] = parts
    save_fallback_assembly(doc, state, assembly)
    return {
        "assembly": summary_ref(assembly.get("name"), assembly.get("label"), ASSEMBLY_TYPE),
        "part": object_ref(part),
        "part_placement": part_entry.get("placement"),
        "fallback": True,
    }, None


def remove_fallback_part_from_assembly(doc, patch):
    state = load_assembly_fallback_state(doc)
    assembly = select_fallback_assembly(state, patch.get("assembly_selector") or patch.get("selector") or {})
    part = select_assembly_part(doc, patch)
    assembly["parts"] = [item for item in list(assembly.get("parts") or []) if item.get("name") != safe_text(getattr(part, "Name", ""))]
    save_fallback_assembly(doc, state, assembly)
    return {
        "assembly": summary_ref(assembly.get("name"), assembly.get("label"), ASSEMBLY_TYPE),
        "part": object_ref(part),
        "fallback": True,
    }, None


def set_fallback_assembly_part_placement(doc, patch):
    state = load_assembly_fallback_state(doc)
    assembly = select_fallback_assembly(state, patch.get("assembly_selector") or patch.get("selector") or {})
    part = select_assembly_part(doc, patch)
    part_name = safe_text(getattr(part, "Name", ""))
    if not any(item.get("name") == part_name for item in list(assembly.get("parts") or [])):
        raise ValueError("selected part is not a member of the selected assembly")
    detail = set_object_placement(part, patch)
    for item in list(assembly.get("parts") or []):
        if item.get("name") == part_name:
            item["placement"] = placement_summary(part)
    save_fallback_assembly(doc, state, assembly)
    detail.update({
        "assembly": summary_ref(assembly.get("name"), assembly.get("label"), ASSEMBLY_TYPE),
        "part": object_ref(part),
        "fallback": True,
    })
    return detail, None


def ground_fallback_assembly_part(doc, patch):
    state = load_assembly_fallback_state(doc)
    assembly = select_fallback_assembly(state, patch.get("assembly_selector") or patch.get("selector") or {})
    part = select_assembly_part(doc, patch)
    part_name = safe_text(getattr(part, "Name", ""))
    if not any(item.get("name") == part_name for item in list(assembly.get("parts") or [])):
        raise ValueError("selected part is not a member of the selected assembly")
    name = safe_feature_name(patch.get("name"), "GroundedJoint")
    joint = {
        **summary_ref(name, patch.get("label"), "App::FeaturePython"),
        "kind": "grounded",
        "object_to_ground": object_ref(part),
    }
    joints = [item for item in list(assembly.get("joints") or []) if item.get("name") != name]
    joints.insert(0, joint)
    assembly["joints"] = joints
    for item in list(assembly.get("parts") or []):
        if item.get("name") == part_name:
            item["grounded"] = True
    save_fallback_assembly(doc, state, assembly)
    return {
        "assembly": summary_ref(assembly.get("name"), assembly.get("label"), ASSEMBLY_TYPE),
        "part": object_ref(part),
        "joint": joint,
        "joint_group": None,
        "fallback": True,
    }, None


def fallback_assembly_connector(doc, payload, fallback_selector=None):
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("assembly connector must be an object")
    selector = (
        payload.get("selector")
        or payload.get("object_selector")
        or payload.get("part_selector")
        or fallback_selector
        or {}
    )
    obj = select_single_object(doc, selector)
    element = (
        payload.get("element")
        or payload.get("subelement")
        or payload.get("sub")
        or payload.get("reference")
        or ""
    )
    vertex = payload.get("vertex") or payload.get("vertex_reference") or payload.get("point_reference") or ""
    stable_ids = payload.get("stable_ids") if isinstance(payload.get("stable_ids"), list) else []
    if payload.get("stable_id") and not stable_ids:
        stable_ids = [payload.get("stable_id")]
    stable_signatures = payload.get("stable_signatures") if isinstance(payload.get("stable_signatures"), list) else []
    if payload.get("signature") and not stable_signatures:
        stable_signatures = [payload.get("signature")]
    stable_references = payload.get("stable_references") if isinstance(payload.get("stable_references"), list) else []
    if payload.get("stable_reference") and not stable_references:
        stable_references = [payload.get("stable_reference")]
    element, element_detail = resolve_subelement_reference_on_object(
        obj,
        reference=element,
        stable_id=stable_ids[0] if stable_ids else "",
        kind=payload.get("kind") or payload.get("subelement_kind") or element,
        signature=stable_signatures[0] if stable_signatures else None,
        stable_reference=stable_references[0] if stable_references else "",
    )
    vertex, vertex_detail = resolve_subelement_reference_on_object(
        obj,
        reference=vertex,
        stable_id=stable_ids[1] if len(stable_ids) > 1 else "",
        kind="Vertex" if vertex else "",
        signature=stable_signatures[1] if len(stable_signatures) > 1 else None,
        stable_reference=stable_references[1] if len(stable_references) > 1 else "",
    )
    return {
        "object": object_ref(obj),
        "subelements": [safe_text(element, 160), safe_text(vertex, 160)],
        "connector_frame": assembly_connector_frame_summary(obj, [element, vertex]),
        "reference_diagnostics": [element_detail, vertex_detail],
    }


def create_fallback_assembly_joint(doc, patch):
    state = load_assembly_fallback_state(doc)
    assembly = select_fallback_assembly(state, patch.get("assembly_selector") or patch.get("selector") or {})
    joint_type = normalize_assembly_joint_type(patch.get("joint_type") or patch.get("type"))
    name = safe_feature_name(patch.get("name"), joint_type + "Joint")
    payloads = connector_payloads_from_patch(patch)
    if payloads is None:
        raise ValueError("Assembly joint requires both connector1 and connector2")
    joint = {
        **summary_ref(name, patch.get("label"), "App::FeaturePython"),
        "kind": "joint",
        "joint_type": joint_type,
        "reference1": fallback_assembly_connector(doc, payloads[0]),
        "reference2": fallback_assembly_connector(doc, payloads[1]),
    }
    if patch.get("distance") is not None or (patch.get("value") is not None and joint_type == "Distance"):
        joint["distance"] = float(patch.get("distance") if patch.get("distance") is not None else patch.get("value"))
    if patch.get("angle_degrees") is not None or (patch.get("value") is not None and joint_type == "Angle"):
        joint["angle"] = float(patch.get("angle_degrees") if patch.get("angle_degrees") is not None else patch.get("value"))
    elif patch.get("angle_radians") is not None:
        joint["angle"] = float(patch["angle_radians"]) * 180.0 / 3.141592653589793
    joints = [item for item in list(assembly.get("joints") or []) if item.get("name") != name]
    joints.append(joint)
    assembly["joints"] = joints
    solve_detail = None
    if patch.get("solve", True):
        solve_detail = solve_typed_assembly_with_native(doc, assembly)
        assembly["solver_status"] = solve_detail.get("solver_status", 0 if not solve_detail.get("ok") else solve_detail.get("solver_status"))
        assembly["solver_backend"] = solve_detail.get("solver_backend")
        assembly["solver_detail"] = solve_detail
    save_fallback_assembly(doc, state, assembly)
    return {
        "assembly": summary_ref(assembly.get("name"), assembly.get("label"), ASSEMBLY_TYPE),
        "joint_group": None,
        "joint": joint,
        "joint_type": joint_type,
        "scalar_results": [],
        "connector_results": {
            "reference1": joint["reference1"],
            "reference2": joint["reference2"],
        },
        "solve_result": solve_detail if patch.get("solve", True) else None,
        "fallback": True,
    }, None


def update_fallback_assembly_joint(doc, patch):
    state = load_assembly_fallback_state(doc)
    selector = patch.get("joint_selector") or patch.get("selector") or {}
    matches = []
    for assembly in list(state.get("assemblies", {}).values()):
        for joint in list(assembly.get("joints") or []):
            if joint.get("kind") == "joint" and matches_summary_selector(joint, selector):
                matches.append((assembly, joint))
    if not matches:
        raise ValueError("no fallback Assembly joint matched selector: " + json.dumps(selector))
    if len(matches) > 1:
        raise ValueError("fallback Assembly joint selector matched multiple joints: " + json.dumps(selector))
    assembly, joint = matches[0]
    old_joint_type = joint.get("joint_type")
    if patch.get("joint_type") is not None or patch.get("type") is not None:
        joint["joint_type"] = normalize_assembly_joint_type(patch.get("joint_type") or patch.get("type"))
    scalar_results = []
    if patch.get("distance") is not None or (patch.get("value") is not None and joint.get("joint_type") == "Distance"):
        value = float(patch.get("distance") if patch.get("distance") is not None else patch.get("value"))
        scalar_results.append({"property": "Distance", "old_value": joint.get("distance"), "new_value": value})
        joint["distance"] = value
    angle = patch.get("angle_degrees")
    if angle is None and patch.get("angle_radians") is not None:
        angle = float(patch["angle_radians"]) * 180.0 / 3.141592653589793
    if angle is None and patch.get("value") is not None and joint.get("joint_type") == "Angle":
        angle = patch.get("value")
    if angle is not None:
        value = float(angle)
        scalar_results.append({"property": "Angle", "old_value": joint.get("angle"), "new_value": value})
        joint["angle"] = value
    payloads = connector_payloads_from_patch(patch)
    connector_detail = None
    if payloads is not None:
        joint["reference1"] = fallback_assembly_connector(doc, payloads[0])
        joint["reference2"] = fallback_assembly_connector(doc, payloads[1])
        connector_detail = {"reference1": joint["reference1"], "reference2": joint["reference2"]}
    solve_detail = None
    if patch.get("solve", True):
        solve_detail = solve_typed_assembly_with_native(doc, assembly)
        assembly["solver_status"] = solve_detail.get("solver_status", 0 if not solve_detail.get("ok") else solve_detail.get("solver_status"))
        assembly["solver_backend"] = solve_detail.get("solver_backend")
        assembly["solver_detail"] = solve_detail
    save_fallback_assembly(doc, state, assembly)
    return {
        "assembly": summary_ref(assembly.get("name"), assembly.get("label"), ASSEMBLY_TYPE),
        "joint": joint,
        "old_joint_type": old_joint_type,
        "new_joint_type": joint.get("joint_type"),
        "scalar_results": scalar_results,
        "connector_results": connector_detail,
        "solve_result": solve_detail if patch.get("solve", True) else None,
        "fallback": True,
    }, None


def solve_typed_assembly_with_native(doc, assembly):
    if not native_assembly_solver_available():
        return {"ok": False, "solver_backend": "typed_fallback", "error": "native Assembly/JointObject modules unavailable"}
    original_doc_name = safe_text(getattr(doc, "Name", ""))
    temp_doc = None
    try:
        temp_doc = FreeCAD.newDocument("FourYiAssemblySolve")
        native_assembly = temp_doc.addObject(ASSEMBLY_TYPE, "AssemblySolve")
        joint_group = ensure_joint_group(native_assembly)
        part_map = {}
        for part_entry in list(assembly.get("parts") or []):
            part_name = safe_text(part_entry.get("name"), 80)
            if not part_name:
                continue
            source = select_single_object(doc, {"name": part_name})
            if not hasattr(source, "Shape"):
                continue
            clone = temp_doc.addObject("Part::Feature", part_name)
            try:
                clone.Shape = source.Shape.copy()
            except Exception:
                clone.Shape = source.Shape
            try:
                clone.Placement = source.Placement
            except Exception:
                pass
            native_assembly.addObject(clone)
            part_map[part_name] = clone
        joint_types = list(getattr(JointObject, "JointTypes", []))
        created_joints = 0
        created_joint_details = []
        skipped_joints = []
        for joint_entry in list(assembly.get("joints") or []):
            name = safe_feature_name(joint_entry.get("name"), "Joint")
            if joint_entry.get("kind") == "grounded":
                target_name = safe_text((joint_entry.get("object_to_ground") or {}).get("name"), 80)
                target = part_map.get(target_name)
                if target is None:
                    skipped_joints.append({
                        "joint": name,
                        "reason": "grounded target part is not available in native solve document",
                    })
                    continue
                joint = joint_group.newObject("App::FeaturePython", name)
                JointObject.GroundedJoint(joint, target)
                created_joints += 1
                created_joint_details.append({"joint": name, "kind": "grounded", "target": target_name})
                continue
            joint_type = safe_text(joint_entry.get("joint_type"), 80)
            if joint_type not in joint_types:
                skipped_joints.append({
                    "joint": name,
                    "joint_type": joint_type,
                    "reason": "joint type is not supported by this FreeCAD Assembly runtime",
                })
                continue
            ref1 = joint_entry.get("reference1") or {}
            ref2 = joint_entry.get("reference2") or {}
            obj1 = part_map.get(safe_text((ref1.get("object") or {}).get("name"), 80))
            obj2 = part_map.get(safe_text((ref2.get("object") or {}).get("name"), 80))
            if obj1 is None or obj2 is None:
                skipped_joints.append({
                    "joint": name,
                    "joint_type": joint_type,
                    "reason": "one or both connector parts are not available in native solve document",
                })
                continue
            joint = joint_group.newObject("App::FeaturePython", name)
            JointObject.Joint(joint, joint_types.index(joint_type))
            if joint_entry.get("distance") is not None:
                try:
                    joint.Distance = float(joint_entry["distance"])
                except Exception:
                    pass
            if joint_entry.get("angle") is not None:
                try:
                    joint.Angle = float(joint_entry["angle"])
                except Exception:
                    pass
            refs = [
                [obj1, list(ref1.get("subelements") or ["", ""])[:2]],
                [obj2, list(ref2.get("subelements") or ["", ""])[:2]],
            ]
            while len(refs[0][1]) < 2:
                refs[0][1].append("")
            while len(refs[1][1]) < 2:
                refs[1][1].append("")
            try:
                joint.Proxy.setJointConnectors(joint, refs)
            except Exception as exc:
                skipped_joints.append({
                    "joint": name,
                    "joint_type": joint_type,
                    "reason": "setJointConnectors failed: " + safe_text(exc, 220),
                })
                continue
            created_joints += 1
            created_joint_details.append({
                "joint": name,
                "joint_type": joint_type,
                "reference1_lcs": (ref1.get("connector_frame") or {}).get("lcs"),
                "reference2_lcs": (ref2.get("connector_frame") or {}).get("lcs"),
            })
        temp_doc.recompute()
        try:
            status = native_assembly.solve()
        except TypeError:
            status = native_assembly.solve(False)
        diagnostics = assembly_solver_diagnostics(
            list(assembly.get("parts") or []),
            list(assembly.get("joints") or []),
            detail={"skipped_joints": skipped_joints},
            fallback=False,
        )
        return {
            "ok": not any(issue.get("severity") == "error" for issue in diagnostics.get("issues", [])),
            "solver_backend": "native_transient",
            "solver_status": safe_value(status),
            "part_count": len(part_map),
            "joint_count": created_joints,
            "created_joints": created_joint_details,
            "skipped_joints": skipped_joints,
            "solver_diagnostics": diagnostics,
        }
    except Exception as exc:
        return {"ok": False, "solver_backend": "native_transient", "error": safe_text(exc)}
    finally:
        if temp_doc is not None:
            try:
                FreeCAD.closeDocument(temp_doc.Name)
            except Exception:
                pass
        if original_doc_name:
            try:
                FreeCAD.setActiveDocument(original_doc_name)
            except Exception:
                pass


def solve_fallback_assembly(doc, patch):
    state = load_assembly_fallback_state(doc)
    assembly = select_fallback_assembly(state, patch.get("assembly_selector") or patch.get("selector") or {})
    native_result = solve_typed_assembly_with_native(doc, assembly)
    assembly["solver_status"] = native_result.get("solver_status", 0 if not native_result.get("ok") else native_result.get("solver_status"))
    assembly["solver_backend"] = native_result.get("solver_backend")
    assembly["solver_detail"] = native_result
    save_fallback_assembly(doc, state, assembly)
    summary = fallback_assembly_summary(assembly)
    return {
        "assembly": summary_ref(assembly.get("name"), assembly.get("label"), ASSEMBLY_TYPE),
        "solver_status": assembly.get("solver_status"),
        "solver_backend": assembly.get("solver_backend"),
        "solver_detail": native_result,
        "solver_diagnostics": summary.get("solver_diagnostics"),
        "assembly_summary": summary,
        "fallback": True,
    }, None


def fallback_assembly_summary(assembly):
    parts = list(assembly.get("parts") or [])
    joints = list(assembly.get("joints") or [])
    diagnostics = assembly_solver_diagnostics(
        parts,
        joints,
        detail=assembly.get("solver_detail"),
        fallback=True,
    )
    return {
        "name": safe_text(assembly.get("name"), 80),
        "label": safe_text(assembly.get("label"), 160),
        "type_id": ASSEMBLY_TYPE,
        "placement": assembly.get("placement"),
        "part_count": len(parts),
        "joint_count": len(joints),
        "parts": parts,
        "joint_groups": list(assembly.get("joint_groups") or []),
        "joints": joints,
        "solver_status": assembly.get("solver_status"),
        "solver_backend": assembly.get("solver_backend"),
        "solver_detail": assembly.get("solver_detail"),
        "solver_diagnostics": diagnostics,
        "fallback": True,
        "product_grade": False,
        "status": "typed_state_native_solver" if assembly.get("solver_backend") == "native_transient" else "headless_fallback",
    }


def assembly_fallback_summaries(doc):
    state = load_assembly_fallback_state(doc)
    return [
        fallback_assembly_summary(assembly)
        for assembly in sorted(list(state.get("assemblies", {}).values()), key=lambda item: safe_text(item.get("name", "")))
        if isinstance(assembly, dict) and assembly.get("name")
    ]


def fallback_assembly_feature_tree_nodes(doc):
    state = load_assembly_fallback_state(doc)
    nodes = []
    roots = []
    for assembly in sorted(list(state.get("assemblies", {}).values()), key=lambda item: safe_text(item.get("name", ""))):
        if not isinstance(assembly, dict) or not assembly.get("name"):
            continue
        ref = summary_ref(assembly.get("name"), assembly.get("label"), ASSEMBLY_TYPE)
        children = list(assembly.get("parts") or []) + [
            summary_ref(joint.get("name"), joint.get("label"), joint.get("type_id"))
            for joint in list(assembly.get("joints") or [])
        ]
        nodes.append({
            "object": ref,
            "kind": "assembly",
            "parents": [],
            "children": children,
            "tip": None,
            "placement": assembly.get("placement"),
        })
        roots.append(ref)
    return {"roots": roots, "nodes": nodes}


def add_techdraw_fallback_view(page, view_summary):
    views = techdraw_fallback_views(page)
    name = safe_text(view_summary.get("name"), 80)
    views = [item for item in views if safe_text(item.get("name"), 80) != name]
    views.append(view_summary)
    if TECHDRAW_FALLBACK_VIEWS_PROPERTY not in list(getattr(page, "PropertiesList", [])):
        page.addProperty(
            "App::PropertyString",
            TECHDRAW_FALLBACK_VIEWS_PROPERTY,
            "4yi",
            "Headless TechDraw fallback view metadata",
        )
    setattr(page, TECHDRAW_FALLBACK_VIEWS_PROPERTY, json.dumps(views, ensure_ascii=False))


def assign_techdraw_layout(view, patch):
    if patch.get("direction") is not None and hasattr(view, "Direction"):
        view.Direction = vector_from_value(patch["direction"], "direction")
    if patch.get("x_direction") is not None and hasattr(view, "XDirection"):
        view.XDirection = vector_from_value(patch["x_direction"], "x_direction")
    if patch.get("x") is not None:
        view.X = float(patch["x"])
    if patch.get("y") is not None:
        view.Y = float(patch["y"])
    if patch.get("scale") is not None:
        view.Scale = float(patch["scale"])
    if patch.get("rotation") is not None:
        view.Rotation = float(patch["rotation"])


def bbox_center_vector(obj):
    try:
        bbox = obj.Shape.BoundBox
        return FreeCAD.Vector(
            float((bbox.XMin + bbox.XMax) / 2.0),
            float((bbox.YMin + bbox.YMax) / 2.0),
            float((bbox.ZMin + bbox.ZMax) / 2.0),
        )
    except Exception:
        return None


def point_inside_shape_bbox(obj, point, tolerance=1e-6):
    try:
        bbox = obj.Shape.BoundBox
        return (
            float(bbox.XMin) - tolerance <= float(point.x) <= float(bbox.XMax) + tolerance
            and float(bbox.YMin) - tolerance <= float(point.y) <= float(bbox.YMax) + tolerance
            and float(bbox.ZMin) - tolerance <= float(point.z) <= float(bbox.ZMax) + tolerance
        )
    except Exception:
        return True


def techdraw_section_origin(source, patch):
    origin = None
    requested = None
    if patch.get("section_origin") is not None:
        requested = patch["section_origin"]
        origin = vector_from_value(requested, "section_origin")
    elif patch.get("origin") is not None:
        requested = patch["origin"]
        origin = vector_from_value(requested, "origin")
    else:
        origin = bbox_center_vector(source)

    adjusted = False
    if origin is not None and not point_inside_shape_bbox(source, origin):
        fallback = bbox_center_vector(source)
        if fallback is not None:
            origin = fallback
            adjusted = True
    return origin, {
        "requested": requested,
        "resolved": vector_summary(origin),
        "adjusted_to_source_bbox_center": adjusted,
    }


def techdraw_layout_summary_from_patch(patch):
    item = {}
    for key in ["x", "y", "scale", "rotation"]:
        if patch.get(key) is not None:
            item[key] = float(patch[key])
    return item


def techdraw_page_size_from_patch(patch):
    value = patch.get("page_size") or patch.get("size")
    named_sizes = {
        "A4_Landscape": [297.0, 210.0],
        "A4_Portrait": [210.0, 297.0],
        "A3_Landscape": [420.0, 297.0],
        "A3_Portrait": [297.0, 420.0],
    }
    if isinstance(value, str) and value in named_sizes:
        return value, named_sizes[value]
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return "custom", [float(value[0]), float(value[1])]
    if patch.get("page_width") is not None or patch.get("page_height") is not None:
        return "custom", [
            float(patch.get("page_width") or 297.0),
            float(patch.get("page_height") or 210.0),
        ]
    return "A4_Landscape", named_sizes["A4_Landscape"]


def techdraw_dimension_mode(patch):
    mode = safe_text(patch.get("dimension_mode") or patch.get("mode") or "single", 40).lower()
    if mode not in SUPPORTED_TECHDRAW_DIMENSION_MODES:
        raise ValueError("unsupported TechDraw dimension_mode: " + mode)
    return mode


def techdraw_source_bbox_summary(source):
    try:
        return bbox_summary(source.Shape)
    except Exception:
        return None


def techdraw_view_box_from_source(source, patch):
    bbox = techdraw_source_bbox_summary(source)
    scale = float(patch.get("scale") or 1.0)
    if not bbox:
        return {"width": 42.0, "height": 28.0, "source_bbox": None}
    size = bbox.get("size") or [42.0, 28.0, 0.0]
    width = max(18.0, min(180.0, abs(float(size[0])) * scale))
    height = max(14.0, min(140.0, abs(float(size[1])) * scale))
    return {"width": width, "height": height, "source_bbox": bbox}


def techdraw_vector_summary_from_patch(patch, key, default=None):
    value = patch.get(key)
    if value is None:
        value = default
    if value is None:
        return None
    return vector_summary(vector_from_value(value, key))


def techdraw_projection_names_from_patch(patch):
    names = patch.get("projection_names")
    if names is None:
        if patch.get("projections") is not None:
            names = patch["projections"]
        elif patch.get("projection") is not None:
            names = [patch["projection"]]
        else:
            names = ["Front", "Left", "Top"]
    if not isinstance(names, list) or not names:
        raise ValueError("TechDraw projection group requires projection_names")
    result = []
    for name in names:
        projection_name = safe_text(name, 80)
        if projection_name not in SUPPORTED_TECHDRAW_PROJECTION_NAMES:
            raise ValueError("unsupported TechDraw projection name: " + projection_name)
        if projection_name not in result:
            result.append(projection_name)
    return result


def create_techdraw_page(doc, patch):
    name = safe_feature_name(patch.get("name"), "Page")
    label = patch.get("label")
    template_name = safe_feature_name(name + "_Template", "Template")
    template_path = resolve_techdraw_template_path(patch)
    page_size_name, page_size = techdraw_page_size_from_patch(patch)
    if native_techdraw_available():
        try:
            page_obj = doc.addObject(TECHDRAW_PAGE_TYPE, name)
            if label is not None:
                page_obj.Label = safe_text(label, 160)
            template_obj = None
            template_error = None
            try:
                template_obj = doc.addObject(TECHDRAW_TEMPLATE_TYPE, template_name)
                template_obj.Template = template_path
                page_obj.Template = template_obj
            except Exception as exc:
                template_error = safe_text(exc, 220)
            if patch.get("scale") is not None and hasattr(page_obj, "Scale"):
                page_obj.Scale = float(patch["scale"])
            try:
                doc.recompute()
            except Exception:
                pass
            return {
                "page": object_ref(page_obj),
                "template": object_ref(template_obj) if template_obj is not None else None,
                "template_path": safe_text(template_path),
                "template_error": template_error,
                "fallback": False,
                "product_grade": template_error is None,
                "status": "native_techdraw",
                "native_creation_enabled": True,
            }, page_obj
        except Exception as exc:
            native_error = safe_text(exc, 300)
        else:
            native_error = None
    else:
        native_error = None
    page = {
        **summary_ref(name, label, TECHDRAW_PAGE_TYPE),
        "kind": "techdraw_page",
        "fallback": True,
        "product_grade": False,
        "status": "typed_vector_fallback",
        "native_creation_enabled": native_techdraw_available(),
        "native_error": native_error,
        "scale": float(patch.get("scale")) if patch.get("scale") is not None else None,
        "page_size": page_size_name,
        "page_width": page_size[0],
        "page_height": page_size[1],
        "template": summary_ref(template_name, template_name, TECHDRAW_TEMPLATE_TYPE),
        "template_path": safe_text(template_path),
        "title_block": patch.get("title_block") if isinstance(patch.get("title_block"), dict) else {},
        "views": [],
        "dimensions": [],
    }
    state = load_techdraw_fallback_state(doc)
    save_techdraw_page_model(doc, state, page)
    if patch.get("scale") is not None:
        page["scale"] = float(patch["scale"])
    return {
        "page": summary_ref(name, label, TECHDRAW_PAGE_TYPE),
        "template": page["template"],
        "template_path": page["template_path"],
        "fallback": True,
        "product_grade": False,
        "status": "typed_vector_fallback",
        "native_error": native_error,
    }, None


def add_techdraw_view(doc, patch):
    if native_techdraw_available():
        try:
            page_obj = select_techdraw_page(doc, patch)
            source = select_techdraw_source(doc, patch)
            name = safe_feature_name(patch.get("name"), "View")
            view_obj = doc.addObject(TECHDRAW_VIEW_PART_TYPE, name)
            if patch.get("label"):
                view_obj.Label = safe_text(patch["label"], 160)
            view_obj.Source = [source]
            assign_techdraw_native_layout(view_obj, patch)
            add_native_techdraw_view_to_page(page_obj, view_obj)
            try:
                doc.recompute()
            except Exception:
                pass
            return {
                "page": object_ref(page_obj),
                "view": object_ref(view_obj),
                "source": object_ref(source),
                "fallback": False,
                "product_grade": True,
                "status": "native_techdraw",
                "native_creation_enabled": True,
            }, view_obj
        except Exception:
            pass
    _state, page = select_techdraw_page_model(doc, patch.get("page_selector") or patch.get("selector") or {})
    source = select_techdraw_source(doc, patch)
    name = safe_feature_name(patch.get("name"), "View")
    view_box = techdraw_view_box_from_source(source, patch)
    view = {
        **summary_ref(name, patch.get("label"), TECHDRAW_VIEW_PART_TYPE),
        "kind": "techdraw_view",
        "fallback": True,
        "product_grade": False,
        "status": "typed_vector_fallback",
        "source": [object_ref(source)],
        "source_bbox": view_box.get("source_bbox"),
        "width": view_box.get("width"),
        "height": view_box.get("height"),
        "direction": techdraw_vector_summary_from_patch(patch, "direction"),
        "xDirection": techdraw_vector_summary_from_patch(patch, "x_direction"),
        "center_lines": [],
        "cosmetic_edges": [],
        "cosmetic_vertexes": [],
    }
    view.update(techdraw_layout_summary_from_patch(patch))
    _page, view = add_techdraw_fallback_view_model(doc, page, view)
    return {
        "page": summary_ref(page.get("name"), page.get("label"), TECHDRAW_PAGE_TYPE),
        "view": summary_ref(name, patch.get("label"), TECHDRAW_VIEW_PART_TYPE),
        "source": object_ref(source),
        "fallback": True,
        "product_grade": False,
        "status": "typed_vector_fallback",
    }, None


def add_techdraw_projection_group(doc, patch):
    _state, page = select_techdraw_page_model(doc, patch.get("page_selector") or patch.get("selector") or {})
    source = select_techdraw_source(doc, patch)
    name = safe_feature_name(patch.get("name"), "ProjectionGroup")
    view_box = techdraw_view_box_from_source(source, patch)
    projections = []
    for projection_name in techdraw_projection_names_from_patch(patch):
        projections.append(projection_name)
    group = {
        **summary_ref(name, patch.get("label"), TECHDRAW_PROJECTION_GROUP_TYPE),
        "kind": "techdraw_projection_group",
        "fallback": True,
        "product_grade": False,
        "status": "typed_vector_fallback",
        "source": [object_ref(source)],
        "source_bbox": view_box.get("source_bbox"),
        "width": max(42.0, float(view_box.get("width") or 42.0) * min(len(projections), 3)),
        "height": max(28.0, float(view_box.get("height") or 28.0) * (1.0 if len(projections) <= 3 else 1.8)),
        "projectionType": safe_text(patch.get("projection_type"), 80) if patch.get("projection_type") is not None else None,
        "views": [summary_ref(projection_name, projection_name, TECHDRAW_PROJECTION_GROUP_ITEM_TYPE) for projection_name in projections],
    }
    group.update(techdraw_layout_summary_from_patch(patch))
    _page, group = add_techdraw_fallback_view_model(doc, page, group)
    return {
        "page": summary_ref(page.get("name"), page.get("label"), TECHDRAW_PAGE_TYPE),
        "source": object_ref(source),
        "projection_group": group,
        "projections": projections,
        "fallback": True,
        "product_grade": False,
        "status": "typed_vector_fallback",
    }, None


def add_techdraw_section_view(doc, patch):
    _state, page = select_techdraw_page_model(doc, patch.get("page_selector") or {})
    _view_state, _view_page, base_view = select_techdraw_view_model(
        doc,
        patch.get("base_view_selector") or patch.get("view_selector") or patch.get("selector") or {},
    )
    source = None
    if patch.get("source_selector") or patch.get("part_selector") or patch.get("feature_selector"):
        source = select_techdraw_source(doc, patch)
    else:
        source_refs = list(base_view.get("source") or [])
        source_ref = source_refs[0] if source_refs else None
        if source_ref:
            source = select_single_object(doc, {"name": source_ref.get("name")})
    if source is None:
        raise ValueError("add_techdraw_section_view requires source_selector or a base view with Source")
    name = safe_feature_name(patch.get("name"), "Section")
    origin, origin_detail = techdraw_section_origin(source, patch)
    section_view = {
        "name": name,
        "label": safe_text(patch.get("label") or name, 160),
        "type_id": TECHDRAW_SECTION_VIEW_TYPE,
        "kind": "techdraw_section_view",
        "fallback": True,
        "product_grade": False,
        "status": "typed_vector_fallback",
        "source": [object_ref(source)],
        "baseView": summary_ref(base_view.get("name"), base_view.get("label"), base_view.get("type_id")),
        "sectionNormal": techdraw_vector_summary_from_patch(
            patch,
            "section_normal",
            patch.get("direction") or [0, 1, 0],
        ),
        "sectionOrigin": origin_detail.get("resolved"),
        "direction": techdraw_vector_summary_from_patch(patch, "direction"),
    }
    if patch.get("section_symbol") is not None:
        section_view["sectionSymbol"] = safe_text(patch["section_symbol"], 20)
    section_view.update(techdraw_layout_summary_from_patch(patch))
    _page, section_view = add_techdraw_fallback_view_model(doc, page, section_view)
    return {
        "page": summary_ref(page.get("name"), page.get("label"), TECHDRAW_PAGE_TYPE),
        "base_view": summary_ref(base_view.get("name"), base_view.get("label"), base_view.get("type_id")),
        "source": object_ref(source),
        "fallback": True,
        "product_grade": False,
        "status": "typed_vector_fallback",
        "section_origin": origin_detail,
        "section_view": section_view,
    }, None


def add_techdraw_detail_view(doc, patch):
    _state, page = select_techdraw_page_model(doc, patch.get("page_selector") or {})
    _view_state, _view_page, base_view = select_techdraw_view_model(
        doc,
        patch.get("base_view_selector") or patch.get("view_selector") or patch.get("selector") or {},
    )
    name = safe_feature_name(patch.get("name"), "Detail")
    source_refs = list(base_view.get("source") or [])
    detail_view = {
        "name": name,
        "label": safe_text(patch.get("label") or name, 160),
        "type_id": TECHDRAW_DETAIL_VIEW_TYPE,
        "kind": "techdraw_detail_view",
        "fallback": True,
        "product_grade": False,
        "status": "typed_vector_fallback",
        "source": source_refs,
        "baseView": summary_ref(base_view.get("name"), base_view.get("label"), base_view.get("type_id")),
        "direction": techdraw_vector_summary_from_patch(patch, "direction"),
    }
    if patch.get("anchor_point") is not None:
        detail_view["anchorPoint"] = techdraw_vector_summary_from_patch(patch, "anchor_point")
    elif patch.get("point") is not None:
        detail_view["anchorPoint"] = techdraw_vector_summary_from_patch(patch, "point")
    if patch.get("radius") is not None:
        detail_view["radius"] = float(patch["radius"])
    if patch.get("reference") is not None:
        detail_view["reference"] = safe_text(patch["reference"], 80)
    detail_view.update(techdraw_layout_summary_from_patch(patch))
    _page, detail_view = add_techdraw_fallback_view_model(doc, page, detail_view)
    return {
        "page": summary_ref(page.get("name"), page.get("label"), TECHDRAW_PAGE_TYPE),
        "base_view": summary_ref(base_view.get("name"), base_view.get("label"), base_view.get("type_id")),
        "fallback": True,
        "product_grade": False,
        "status": "headless_fallback",
        "detail_view": detail_view,
    }, None


def add_techdraw_centerline(doc, patch):
    state, page, view = select_techdraw_view_model(doc, patch.get("view_selector") or patch.get("selector") or {})
    references = patch.get("references")
    if references is None and patch.get("reference") is not None:
        references = [patch["reference"]]
    if not isinstance(references, list) or len(references) < 2:
        raise ValueError("add_techdraw_centerline requires at least two references")
    refs, reference_diagnostics = resolve_techdraw_references(doc, patch, view, references)
    mode = bool(patch.get("centerline_mode", patch.get("mode", False)))
    center_lines = list(view.get("center_lines") or [])
    tag = "centerline{}".format(len(center_lines) + 1)
    center_lines.append({
        "index": len(center_lines),
        "tag": tag,
        "references": refs,
        "mode": mode,
        "kind": "centerline",
        "reference_diagnostics": reference_diagnostics,
    })
    view["center_lines"] = center_lines
    save_techdraw_page_model(doc, state, page)
    return {
        "view": summary_ref(view.get("name"), view.get("label"), view.get("type_id")),
        "tag": tag,
        "references": refs,
        "reference_diagnostics": reference_diagnostics,
        "center_lines": center_lines,
        "fallback": True,
    }, None


def add_techdraw_cosmetic_vertex(doc, patch):
    state, page, view = select_techdraw_view_model(doc, patch.get("view_selector") or patch.get("selector") or {})
    point = vector_summary(vector_payload(patch, "point", "point", "position", "anchor_point"))
    vertexes = list(view.get("cosmetic_vertexes") or [])
    tag = "cosmeticVertex{}".format(len(vertexes) + 1)
    vertexes.append({
        "index": len(vertexes),
        "tag": tag,
        "kind": "cosmetic_vertex",
        "point": point,
    })
    view["cosmetic_vertexes"] = vertexes
    save_techdraw_page_model(doc, state, page)
    return {
        "view": summary_ref(view.get("name"), view.get("label"), view.get("type_id")),
        "tag": tag,
        "cosmetic_vertexes": vertexes,
        "fallback": True,
    }, None


def add_techdraw_cosmetic_line(doc, patch):
    state, page, view = select_techdraw_view_model(doc, patch.get("view_selector") or patch.get("selector") or {})
    start = vector_summary(vector_payload(patch, "start", "start", "p1", "from"))
    end = vector_summary(vector_payload(patch, "end", "end", "p2", "to"))
    edges = list(view.get("cosmetic_edges") or [])
    tag = "cosmeticLine{}".format(len(edges) + 1)
    edges.append({
        "index": len(edges),
        "tag": tag,
        "kind": "cosmetic_edge",
        "start": start,
        "end": end,
    })
    view["cosmetic_edges"] = edges
    save_techdraw_page_model(doc, state, page)
    return {
        "view": summary_ref(view.get("name"), view.get("label"), view.get("type_id")),
        "tag": tag,
        "cosmetic_edges": edges,
        "fallback": True,
    }, None


def request_techdraw_pdf_export(doc, patch):
    page = None
    if patch.get("page_selector") or patch.get("selector"):
        _state, page = select_techdraw_page_model(doc, patch.get("page_selector") or patch.get("selector") or {})
    return {
        "page": summary_ref(page.get("name"), page.get("label"), TECHDRAW_PAGE_TYPE) if page is not None else None,
        "export": "techdraw_pdf",
        "exporter": "rsvg-convert",
        "requested": True,
        "fallback": True,
    }, page


def selector_from_summary_ref(ref):
    if not isinstance(ref, dict):
        return {}
    if ref.get("name"):
        return {"name": ref.get("name")}
    if ref.get("label"):
        return {"label": ref.get("label")}
    if ref.get("type_id"):
        return {"type_id": ref.get("type_id")}
    return {}


def techdraw_reference_source(doc, patch, view):
    selector = (
        patch.get("source_selector")
        or patch.get("part_selector")
        or patch.get("feature_selector")
        or patch.get("object_selector")
        or {}
    )
    if not selector and isinstance(view, dict):
        source_refs = list(view.get("source") or [])
        if source_refs:
            selector = selector_from_summary_ref(source_refs[0])
    if not selector:
        return None
    try:
        return select_single_object(doc, selector)
    except Exception:
        return None


def resolve_techdraw_references(doc, patch, view, references):
    refs = [safe_text(ref, 160) for ref in list(references or [])]
    stable_ids = patch.get("stable_ids") if isinstance(patch.get("stable_ids"), list) else []
    if patch.get("stable_id") and not stable_ids:
        stable_ids = [patch.get("stable_id")]
    stable_signatures = patch.get("stable_signatures") if isinstance(patch.get("stable_signatures"), list) else []
    if patch.get("signature") and not stable_signatures:
        stable_signatures = [patch.get("signature")]
    stable_references = patch.get("stable_references") if isinstance(patch.get("stable_references"), list) else []
    if patch.get("stable_reference") and not stable_references:
        stable_references = [patch.get("stable_reference")]
    source = techdraw_reference_source(doc, patch, view)
    if source is None:
        return refs, []
    resolved = []
    diagnostics = []
    for index, reference in enumerate(refs):
        stable_id = stable_ids[index] if index < len(stable_ids) else ""
        signature = stable_signatures[index] if index < len(stable_signatures) else None
        stable_reference = stable_references[index] if index < len(stable_references) else ""
        resolved_reference, detail = resolve_subelement_reference_on_object(
            source,
            reference=reference,
            stable_id=stable_id,
            kind=patch.get("kind") or patch.get("subelement_kind") or reference,
            signature=signature,
            stable_reference=stable_reference,
        )
        resolved.append(resolved_reference)
        diagnostics.append(detail)
    return resolved, diagnostics


def add_techdraw_dimension(doc, patch):
    _state, page = select_techdraw_page_model(doc, patch.get("page_selector") or {})
    _view_state, _view_page, view = select_techdraw_view_model(doc, patch.get("view_selector") or patch.get("selector") or {})
    name = safe_feature_name(patch.get("name"), "Dimension")
    dimension_type = safe_text(patch.get("dimension_type") or patch.get("type") or "Distance", 80)
    if dimension_type not in SUPPORTED_TECHDRAW_DIMENSION_TYPES:
        raise ValueError("unsupported TechDraw dimension type: " + dimension_type)
    measure_type = patch.get("measure_type")
    if measure_type is not None:
        measure_type = safe_text(measure_type, 80)
        if measure_type not in SUPPORTED_TECHDRAW_MEASURE_TYPES:
            raise ValueError("unsupported TechDraw measure_type: " + measure_type)
    references = patch.get("references") or []
    if patch.get("reference"):
        references = [patch["reference"]]
    if not references:
        references = ["Edge1"]
    references, reference_diagnostics = resolve_techdraw_references(doc, patch, view, references)
    dimension_mode = techdraw_dimension_mode(patch)
    origin = patch.get("origin") or patch.get("base_point") or [0, 0, 0]
    chain_offsets = patch.get("chain_offsets") if isinstance(patch.get("chain_offsets"), list) else []
    coordinate_axis = safe_text(patch.get("coordinate_axis") or patch.get("axis") or "X", 8).upper()
    dimension = {
        **summary_ref(name, patch.get("label"), TECHDRAW_DIMENSION_TYPE),
        "kind": "techdraw_dimension",
        "fallback": True,
        "product_grade": False,
        "status": "typed_vector_fallback",
        "type": dimension_type,
        "dimension_mode": dimension_mode,
        "measureType": measure_type,
        "origin": safe_value(origin),
        "chain_offsets": safe_value(chain_offsets),
        "coordinate_axis": coordinate_axis if coordinate_axis in {"X", "Y"} else "X",
        "references2D": [
            {
                "object": summary_ref(view.get("name"), view.get("label"), view.get("type_id")),
                "subelement": safe_text(ref, 160),
            }
            for ref in references
        ],
        "reference_diagnostics": reference_diagnostics,
    }
    dimension.update(techdraw_layout_summary_from_patch(patch))
    _page, dimension = add_techdraw_fallback_dimension_model(doc, page, dimension)
    return {
        "page": summary_ref(page.get("name"), page.get("label"), TECHDRAW_PAGE_TYPE),
        "view": summary_ref(view.get("name"), view.get("label"), view.get("type_id")),
        "dimension": summary_ref(name, patch.get("label"), TECHDRAW_DIMENSION_TYPE),
        "dimension_type": dimension_type,
        "dimension_mode": dimension_mode,
        "references": list(references),
        "reference_diagnostics": reference_diagnostics,
        "fallback": True,
    }, None


def apply_document_patches(doc, patches):
    if doc is None:
        raise ValueError("no active FreeCAD document to patch")
    results = []
    for index, patch in enumerate(patches):
        if not isinstance(patch, dict):
            raise ValueError("patch at index {} must be an object".format(index))
        op = patch.get("op")
        obj = None
        result_object = None
        if op == "create_sketch":
            detail, obj = create_sketch(doc, patch)
            result_object = object_ref(obj)
        elif op == "attach_sketch":
            obj = select_sketch(doc, patch)
            detail = attach_sketch_to_support(doc, obj, patch)
            result_object = object_ref(obj)
        elif op == "add_external_geometry":
            obj = select_sketch(doc, patch)
            detail = add_sketch_external_geometry(doc, obj, patch)
            result_object = object_ref(obj)
        elif op == "solver_status":
            obj = select_sketch(doc, patch)
            detail = solve_sketch_status(doc, obj, patch)
            result_object = object_ref(obj)
        elif op == "validate_sketch":
            obj = select_sketch(doc, patch)
            detail = validate_sketch(doc, obj, patch)
            result_object = object_ref(obj)
        elif op == "create_feature":
            detail, obj = create_feature(doc, patch)
            result_object = object_ref(obj) if obj is not None else {
                "name": detail.get("created_name"),
                "label": detail.get("created_label"),
                "type_id": detail.get("created_type_id"),
            }
        elif op == "create_assembly":
            detail, obj = create_assembly(doc, patch)
            result_object = object_ref(obj) if obj is not None else {
                "name": detail.get("created_name"),
                "label": detail.get("created_label"),
                "type_id": detail.get("created_type_id"),
            }
        elif op == "add_part_to_assembly":
            detail, obj = add_part_to_assembly(doc, patch)
            result_object = object_ref(obj) if obj is not None else detail.get("assembly")
        elif op == "remove_part_from_assembly":
            detail, obj = remove_part_from_assembly(doc, patch)
            result_object = object_ref(obj) if obj is not None else detail.get("assembly")
        elif op == "set_assembly_part_placement":
            detail, obj = set_assembly_part_placement(doc, patch)
            result_object = object_ref(obj) if obj is not None else detail.get("assembly")
        elif op == "ground_assembly_part":
            detail, obj = ground_assembly_part(doc, patch)
            result_object = object_ref(obj) if obj is not None else detail.get("assembly")
        elif op == "create_joint":
            detail, obj = create_assembly_joint(doc, patch)
            result_object = object_ref(obj) if obj is not None else detail.get("joint")
        elif op == "update_joint":
            detail, obj = update_assembly_joint(doc, patch)
            result_object = object_ref(obj) if obj is not None else detail.get("joint")
        elif op == "solve_assembly":
            detail, obj = solve_assembly(doc, patch)
            result_object = object_ref(obj) if obj is not None else detail.get("assembly")
        elif op == "create_techdraw_page":
            detail, obj = create_techdraw_page(doc, patch)
            result_object = detail.get("page")
        elif op == "add_techdraw_view":
            detail, obj = add_techdraw_view(doc, patch)
            result_object = detail.get("view")
        elif op == "add_techdraw_projection_group":
            detail, obj = add_techdraw_projection_group(doc, patch)
            result_object = detail.get("projection_group")
        elif op == "add_techdraw_section_view":
            detail, obj = add_techdraw_section_view(doc, patch)
            result_object = detail.get("section_view")
        elif op == "add_techdraw_detail_view":
            detail, obj = add_techdraw_detail_view(doc, patch)
            result_object = detail.get("detail_view")
        elif op == "add_techdraw_centerline":
            detail, obj = add_techdraw_centerline(doc, patch)
            result_object = detail.get("view")
        elif op == "add_techdraw_cosmetic_vertex":
            detail, obj = add_techdraw_cosmetic_vertex(doc, patch)
            result_object = detail.get("view")
        elif op == "add_techdraw_cosmetic_line":
            detail, obj = add_techdraw_cosmetic_line(doc, patch)
            result_object = detail.get("view")
        elif op == "export_techdraw_pdf":
            detail, obj = request_techdraw_pdf_export(doc, patch)
            result_object = detail.get("page")
        elif op == "add_techdraw_dimension":
            detail, obj = add_techdraw_dimension(doc, patch)
            result_object = detail.get("dimension")
        elif op == "delete_feature":
            obj = select_single_object(doc, patch.get("selector") or {})
            result_object = object_ref(obj)
            detail = delete_feature(doc, obj)
        elif op == "set_body_tip":
            detail, obj = set_body_tip(doc, patch)
            result_object = object_ref(obj) if obj is not None else detail.get("body")
        else:
            obj = select_single_object(doc, patch.get("selector") or {})
            result_object = object_ref(obj)
        if op == "set_property":
            detail = assign_property_value(obj, patch.get("property"), patch.get("value"))
        elif op == "set_constraint_value":
            detail = assign_constraint_value(obj, patch)
        elif op == "set_placement":
            detail = set_object_placement(obj, patch)
        elif op == "set_expression":
            detail = set_object_expression(obj, patch)
        elif op == "add_geometry":
            detail = add_sketch_geometries(obj, patch)
        elif op == "add_constraint":
            detail = add_sketch_constraints(obj, patch)
        elif op == "remove_constraint":
            detail = remove_sketch_constraint(obj, patch)
        elif op in {
            "create_sketch",
            "attach_sketch",
            "add_external_geometry",
            "solver_status",
            "validate_sketch",
            "create_feature",
            "delete_feature",
            "set_body_tip",
            "create_assembly",
            "add_part_to_assembly",
            "remove_part_from_assembly",
            "set_assembly_part_placement",
            "ground_assembly_part",
            "create_joint",
            "update_joint",
            "solve_assembly",
            "create_techdraw_page",
            "add_techdraw_view",
            "add_techdraw_projection_group",
            "add_techdraw_section_view",
            "add_techdraw_detail_view",
            "add_techdraw_centerline",
            "add_techdraw_cosmetic_vertex",
            "add_techdraw_cosmetic_line",
            "export_techdraw_pdf",
            "add_techdraw_dimension",
        }:
            pass
        else:
            raise ValueError("unsupported FreeCAD document patch op: " + safe_text(op))
        result = {
            "index": index,
            "op": op,
            "object": result_object,
        }
        result.update(detail)
        results.append(result)
    try:
        doc.recompute()
    except Exception:
        pass
    return results


def objects_from_namespace(namespace):
    result = namespace.get("result")
    doc = namespace.get("doc") or FreeCAD.ActiveDocument

    if doc is not None:
        try:
            doc.recompute()
        except Exception:
            pass

    if result is not None:
        if isinstance(result, (list, tuple)):
            objects = list(result)
            if objects:
                return objects
        if hasattr(result, "Shape"):
            return [result]
        if hasattr(result, "exportStep"):
            return [result]

    if doc is not None:
        objects = [obj for obj in getattr(doc, "Objects", []) if hasattr(obj, "Shape")]
        if objects:
            return objects

    return []


def export_techdraw_svg(doc, out_dir):
    if TechDraw is None or doc is None:
        return export_techdraw_fallback_svg(doc, out_dir)
    fragments = []
    for page in list(getattr(doc, "Objects", []) or []):
        if not is_techdraw_page(page):
            continue
        try:
            views = list(getattr(page, "Views", []) or [])
        except Exception:
            views = []
        for view in views:
            if not is_techdraw_part_view(view):
                continue
            try:
                fragment = TechDraw.viewPartAsSvg(view)
            except Exception:
                continue
            if not fragment:
                continue
            x = quantity_summary(getattr(view, "X", 0)) or 0
            y = quantity_summary(getattr(view, "Y", 0)) or 0
            fragments.append(
                '<g data-page="{}" data-view="{}" transform="translate({} {})">{}</g>'.format(
                    safe_text(getattr(page, "Name", "")),
                    safe_text(getattr(view, "Name", "")),
                    float(x),
                    float(y),
                    fragment,
                )
            )
    if not fragments:
        return export_techdraw_fallback_svg(doc, out_dir)
    path = os.path.join(out_dir, "drawing.svg")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 297 210">\n'
            + "\n".join(fragments)
            + "\n</svg>\n"
        )
    return path


def svg_escape(value):
    return (
        safe_text(value, 200)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def export_techdraw_fallback_svg(doc, out_dir):
    pages = techdraw_fallback_pages(doc)
    if not pages:
        return None
    pages_svg = []
    for page in pages:
        width = float(page.get("page_width") or 297.0)
        height = float(page.get("page_height") or 210.0)
        title_block = page.get("title_block") if isinstance(page.get("title_block"), dict) else {}
        fragments = [
            '<rect x="6" y="6" width="{}" height="{}" fill="white" stroke="#111827" stroke-width="0.45"/>'.format(width - 12, height - 12),
            '<rect x="{}" y="{}" width="{}" height="24" fill="none" stroke="#111827" stroke-width="0.35"/>'.format(width - 106, height - 30, 100),
            '<text x="{}" y="{}" font-size="4.2" fill="#111827">{}</text>'.format(width - 102, height - 21, svg_escape(title_block.get("title") or page.get("label") or page.get("name"))),
            '<text x="{}" y="{}" font-size="3.4" fill="#374151">scale {}</text>'.format(width - 102, height - 14, svg_escape(page.get("scale") or "auto")),
            '<text x="12" y="14" font-size="5" fill="#111827">{}</text>'.format(svg_escape(page.get("name"))),
        ]
        for view in list(page.get("views") or []):
            x = float(view.get("x") or 24)
            vy = float(view.get("y") or 40)
            view_width = float(view.get("width") or 42)
            view_height = float(view.get("height") or 28)
            kind = svg_escape(view.get("kind"))
            name = svg_escape(view.get("name"))
            fragments.append(
                '<g data-view="{}"><rect x="{}" y="{}" width="{}" height="{}" fill="none" stroke="#1f2937" stroke-width="0.5"/>'
                '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#6b7280" stroke-width="0.25"/>'
                '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#6b7280" stroke-width="0.25"/>'
                '<text x="{}" y="{}" font-size="4.5" fill="#1f2937">{} {}</text></g>'.format(
                    name,
                    x,
                    vy,
                    view_width,
                    view_height,
                    x,
                    vy,
                    x + view_width,
                    vy + view_height,
                    x + view_width,
                    vy,
                    x,
                    vy + view_height,
                    x + 2,
                    vy + 8,
                    name,
                    kind,
                )
            )
            for centerline in list(view.get("center_lines") or []):
                fragments.append(
                    '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#2563eb" stroke-width="0.35" stroke-dasharray="4 2 1 2" data-centerline="{}"/>'.format(
                        x,
                        vy + view_height / 2.0,
                        x + view_width,
                        vy + view_height / 2.0,
                        svg_escape(centerline.get("tag")),
                    )
                )
            for edge in list(view.get("cosmetic_edges") or []):
                start = edge.get("start") if isinstance(edge.get("start"), list) else [0, 0, 0]
                end = edge.get("end") if isinstance(edge.get("end"), list) else [view_width, 0, 0]
                fragments.append(
                    '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#059669" stroke-width="0.35" data-cosmetic="{}"/>'.format(
                        x + float(start[0]),
                        vy + float(start[1]),
                        x + float(end[0]),
                        vy + float(end[1]),
                        svg_escape(edge.get("tag")),
                    )
                )
            for vertex in list(view.get("cosmetic_vertexes") or []):
                point = vertex.get("point") if isinstance(vertex.get("point"), list) else [0, 0, 0]
                fragments.append(
                    '<circle cx="{}" cy="{}" r="1.2" fill="#dc2626" data-cosmetic="{}"/>'.format(
                        x + float(point[0]),
                        vy + float(point[1]),
                        svg_escape(vertex.get("tag")),
                    )
                )
        for dimension in list(page.get("dimensions") or []):
            x = float(dimension.get("x") or 24)
            dy = float(dimension.get("y") or (height - 44))
            mode = safe_text(dimension.get("dimension_mode") or "single", 40)
            label = "{} {} {}".format(svg_escape(dimension.get("name")), svg_escape(dimension.get("type")), svg_escape(mode))
            refs = list(dimension.get("references2D") or [])
            step = 16.0
            count = max(1, len(refs))
            if mode == "chain":
                for index in range(count):
                    x1 = x + index * step
                    x2 = x + (index + 1) * step
                    fragments.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#1d4ed8" stroke-width="0.35"/>'.format(x1, dy, x2, dy))
                    fragments.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#1d4ed8" stroke-width="0.35"/>'.format(x1, dy - 3, x1, dy + 3))
                    fragments.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#1d4ed8" stroke-width="0.35"/>'.format(x2, dy - 3, x2, dy + 3))
            elif mode == "coordinate":
                origin_x = x
                fragments.append('<circle cx="{}" cy="{}" r="1.5" fill="#1d4ed8"/>'.format(origin_x, dy))
                for index in range(count):
                    x2 = x + (index + 1) * step
                    fragments.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#1d4ed8" stroke-width="0.35"/>'.format(origin_x, dy, x2, dy - (index + 1) * 4))
            else:
                fragments.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#1d4ed8" stroke-width="0.35"/>'.format(x, dy, x + step * count, dy))
                fragments.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#1d4ed8" stroke-width="0.35"/>'.format(x, dy - 3, x, dy + 3))
                fragments.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#1d4ed8" stroke-width="0.35"/>'.format(x + step * count, dy - 3, x + step * count, dy + 3))
            fragments.append('<text x="{}" y="{}" font-size="4" fill="#1d4ed8">{}</text>'.format(x, dy - 5, label))
        pages_svg.append(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {} {}">\n{}\n</svg>\n'.format(
                width,
                height,
                "\n".join(fragments),
            )
        )
    path = os.path.join(out_dir, "drawing.svg")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(pages_svg))
    return path


def export_techdraw_dxf(doc, out_dir):
    if TechDraw is None or doc is None or not hasattr(TechDraw, "writeDXFPage"):
        return export_techdraw_fallback_dxf(doc, out_dir)
    for page in list(getattr(doc, "Objects", []) or []):
        if not is_techdraw_page(page):
            continue
        try:
            views = list(getattr(page, "Views", []) or [])
        except Exception:
            views = []
        if not views:
            continue
        path = os.path.join(out_dir, "drawing.dxf")
        try:
            TechDraw.writeDXFPage(page, path)
        except Exception:
            continue
        try:
            if os.path.getsize(path) > 0:
                return path
        except Exception:
            continue
    return export_techdraw_fallback_dxf(doc, out_dir)


def export_techdraw_fallback_dxf(doc, out_dir):
    pages = techdraw_fallback_pages(doc)
    if not pages:
        return None
    path = os.path.join(out_dir, "drawing.dxf")
    lines = ["0", "SECTION", "2", "ENTITIES"]
    def add_text(x, y, text, height=2.5, layer="4YI_TECHDRAW"):
        lines.extend(["0", "TEXT", "8", layer, "10", str(x), "20", str(y), "40", str(height), "1", safe_text(text, 160)])

    def add_line(x1, y1, x2, y2, layer="4YI_TECHDRAW"):
        lines.extend(["0", "LINE", "8", layer, "10", str(x1), "20", str(y1), "11", str(x2), "21", str(y2)])

    def add_rect(x, y, width, height, layer="4YI_TECHDRAW"):
        add_line(x, y, x + width, y, layer)
        add_line(x + width, y, x + width, y - height, layer)
        add_line(x + width, y - height, x, y - height, layer)
        add_line(x, y - height, x, y, layer)

    page_offset = 0.0
    for page in pages:
        page_width = float(page.get("page_width") or 297.0)
        page_height = float(page.get("page_height") or 210.0)
        top_y = page_offset
        add_rect(0, top_y, page_width, page_height, "4YI_PAGE")
        add_rect(page_width - 106, top_y - page_height + 30, 100, 24, "4YI_TITLE_BLOCK")
        add_text(6, top_y - 8, page.get("name"), 3.0, "4YI_LABELS")
        title = page.get("title_block", {}).get("title") if isinstance(page.get("title_block"), dict) else None
        add_text(page_width - 102, top_y - page_height + 21, title or page.get("label") or page.get("name"), 2.4, "4YI_TITLE_BLOCK")
        for view in list(page.get("views") or []):
            x = float(view.get("x") or 24.0)
            y = top_y - float(view.get("y") or 40.0)
            width = float(view.get("width") or 42.0)
            height = float(view.get("height") or 28.0)
            add_rect(x, y, width, height, "4YI_VIEWS")
            add_line(x, y, x + width, y - height, "4YI_VIEW_DIAGONALS")
            add_line(x + width, y, x, y - height, "4YI_VIEW_DIAGONALS")
            label = "{} {}".format(safe_text(view.get("name"), 80), safe_text(view.get("kind"), 80))
            add_text(x + 2, y - 5, label, 2.2, "4YI_LABELS")
            for centerline in list(view.get("center_lines") or []):
                add_line(x, y - height / 2.0, x + width, y - height / 2.0, "4YI_CENTERLINES")
            for edge in list(view.get("cosmetic_edges") or []):
                start = edge.get("start") if isinstance(edge.get("start"), list) else [0, 0, 0]
                end = edge.get("end") if isinstance(edge.get("end"), list) else [width, 0, 0]
                add_line(x + float(start[0]), y - float(start[1]), x + float(end[0]), y - float(end[1]), "4YI_COSMETIC")
        for dimension in list(page.get("dimensions") or []):
            x = float(dimension.get("x") or 24.0)
            y = top_y - float(dimension.get("y") or (page_height - 44.0))
            refs = list(dimension.get("references2D") or [])
            count = max(1, len(refs))
            step = 16.0
            mode = safe_text(dimension.get("dimension_mode") or "single", 40)
            if mode == "chain":
                for index in range(count):
                    x1 = x + index * step
                    x2 = x + (index + 1) * step
                    add_line(x1, y, x2, y, "4YI_DIMENSIONS")
                    add_line(x1, y - 3, x1, y + 3, "4YI_DIMENSIONS")
                    add_line(x2, y - 3, x2, y + 3, "4YI_DIMENSIONS")
            elif mode == "coordinate":
                for index in range(count):
                    add_line(x, y, x + (index + 1) * step, y + (index + 1) * 4, "4YI_DIMENSIONS")
            else:
                add_line(x, y, x + step * count, y, "4YI_DIMENSIONS")
                add_line(x, y - 3, x, y + 3, "4YI_DIMENSIONS")
                add_line(x + step * count, y - 3, x + step * count, y + 3, "4YI_DIMENSIONS")
            label = "{} {} {}".format(safe_text(dimension.get("name"), 80), safe_text(dimension.get("type"), 80), mode)
            add_text(x, y + 5, label, 2.0, "4YI_DIMENSIONS")
        page_offset -= page_height + 20.0
    lines.extend(["0", "ENDSEC", "0", "EOF", ""])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def resolve_rsvg_convert():
    configured = os.environ.get("RSVG_CONVERT_BINARY")
    candidates = [configured] if configured else []
    found = shutil.which("rsvg-convert")
    if found:
        candidates.append(found)
    candidates.extend([
        "/opt/homebrew/bin/rsvg-convert",
        "/usr/local/bin/rsvg-convert",
    ])
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def export_techdraw_pdf(doc, out_dir, svg_path=None):
    if doc is None:
        return None, {"ok": False, "error": "no document"}
    if not svg_path:
        svg_path = export_techdraw_svg(doc, out_dir)
    if not svg_path or not os.path.isfile(svg_path):
        return None, {"ok": False, "error": "TechDraw SVG export unavailable"}
    converter = resolve_rsvg_convert()
    if not converter:
        return None, {"ok": False, "error": "rsvg-convert unavailable"}
    path = os.path.join(out_dir, "drawing.pdf")
    try:
        proc = subprocess.run(
            [converter, "-f", "pdf", "-o", path, svg_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        return None, {"ok": False, "error": safe_text(exc), "exporter": converter}
    if proc.returncode != 0:
        return None, {
            "ok": False,
            "error": safe_text((proc.stderr or proc.stdout or "").strip() or "rsvg-convert failed"),
            "exporter": converter,
        }
    try:
        if os.path.getsize(path) > 0:
            return path, {"ok": True, "exporter": converter, "source": safe_text(svg_path)}
    except Exception:
        pass
    return None, {"ok": False, "error": "empty PDF export", "exporter": converter}


def import_model(path, fmt):
    normalized = (fmt or "").lower()
    if normalized == "fcstd":
        doc = FreeCAD.openDocument(path)
        FreeCAD.setActiveDocument(doc.Name)
        return doc

    if normalized not in {"step", "stp", "iges", "igs", "brep"}:
        raise ValueError("unsupported import format: " + str(fmt))

    doc = FreeCAD.newDocument("Imported")
    try:
        Part.insert(path, doc.Name)
    except Exception:
        shape = Part.Shape()
        shape.read(path)
        feature = doc.addObject("Part::Feature", "Imported")
        feature.Shape = shape
    doc.recompute()
    FreeCAD.setActiveDocument(doc.Name)
    return doc


def load_input_document():
    doc_path = os.environ.get("FOURYI_FREECAD_DOCUMENT_PATH")
    if doc_path:
        doc = FreeCAD.openDocument(doc_path)
        FreeCAD.setActiveDocument(doc.Name)
        return doc

    import_path = os.environ.get("FOURYI_FREECAD_IMPORT_PATH")
    if import_path:
        return import_model(import_path, os.environ.get("FOURYI_FREECAD_IMPORT_FORMAT"))

    return FreeCAD.ActiveDocument


def document_from_namespace(namespace, objects):
    doc = namespace.get("doc") or FreeCAD.ActiveDocument
    if doc is None:
        doc = FreeCAD.newDocument("Model")

    existing = list(getattr(doc, "Objects", []))
    added = 0
    for obj in objects:
        if any(obj is existing_obj for existing_obj in existing):
            continue
        shape = getattr(obj, "Shape", obj)
        if not hasattr(shape, "ShapeType"):
            continue
        feature = doc.addObject("Part::Feature", "Result{}".format(added + 1))
        feature.Shape = shape
        added += 1
    try:
        doc.recompute()
    except Exception:
        pass
    return doc


try:
    out_dir = os.environ["FOURYI_FREECAD_OUT"]
    user_script = os.environ["FOURYI_FREECAD_SCRIPT"]
    doc = load_input_document()
    patch_results = []
    if os.environ.get("FOURYI_FREECAD_MODE") == "inspect":
        emit({
            "ok": True,
            "document_summary": document_summary(doc),
            "freecad_version": ".".join(str(part) for part in FreeCAD.Version()[:3]),
        })
        raise SystemExit(0)
    if os.environ.get("FOURYI_FREECAD_MODE") == "patch":
        patch_results = apply_document_patches(doc, load_document_patches())

    namespace = {
        "FreeCAD": FreeCAD,
        "App": FreeCAD,
        "Part": Part,
        "TechDraw": TechDraw,
        "Assembly": Assembly,
        "JointObject": JointObject,
        "Sketcher": Sketcher,
        "Mesh": Mesh,
        "doc": doc,
    }
    if os.path.getsize(user_script) > 0:
        with open(user_script, "r", encoding="utf-8") as fh:
            exec(compile(fh.read(), user_script, "exec"), namespace)

    objects = objects_from_namespace(namespace)
    if not objects:
        emit({"ok": False, "error": "script did not create a result shape or document object"})
        raise SystemExit(0)

    volume = 0.0
    saw_volume = False
    for obj in objects:
        shape = getattr(obj, "Shape", obj)
        try:
            if hasattr(shape, "isValid") and not shape.isValid():
                emit({"ok": False, "error": "resulting FreeCAD model contains invalid geometry"})
                raise SystemExit(0)
        except Exception:
            pass
        value = shape_volume(shape)
        if value is not None:
            volume += value
            saw_volume = True
    if saw_volume and volume <= 1e-9:
        emit({"ok": False, "error": "resulting FreeCAD model has ~zero volume"})
        raise SystemExit(0)

    step_path = os.path.join(out_dir, "model.step")
    stl_path = os.path.join(out_dir, "model.stl")
    if len(objects) == 1 and hasattr(objects[0], "exportStep"):
        objects[0].exportStep(step_path)
        objects[0].exportStl(stl_path)
    else:
        Part.export(objects, step_path)
        Mesh.export(objects, stl_path)

    fcstd_path = os.path.join(out_dir, "model.FCStd")
    doc = document_from_namespace(namespace, objects)
    doc.saveAs(fcstd_path)
    viewer_objects = [obj for obj in list(getattr(doc, "Objects", []) or []) if hasattr(obj, "Shape")] or objects
    viewer_scene_path = export_viewer_scene_json(doc, viewer_objects, out_dir)
    techdraw_svg_path = export_techdraw_svg(doc, out_dir)
    techdraw_dxf_path = export_techdraw_dxf(doc, out_dir)
    techdraw_pdf_path, techdraw_pdf_status = export_techdraw_pdf(doc, out_dir, techdraw_svg_path)
    fallback_page_count = len(techdraw_fallback_pages(doc))
    native_page_count = len([obj for obj in list(getattr(doc, "Objects", []) or []) if is_techdraw_page(obj)])
    native_view_count = 0
    for page in [obj for obj in list(getattr(doc, "Objects", []) or []) if is_techdraw_page(obj)]:
        try:
            native_view_count += len(list(getattr(page, "Views", []) or []))
        except Exception:
            pass
    techdraw_mode = "native_techdraw" if native_view_count and not fallback_page_count else (
        "mixed_native_typed" if native_view_count and fallback_page_count else "typed_vector_fallback"
    )
    techdraw_capabilities = techdraw_runtime_capabilities()
    techdraw_fallback_reason = None
    if fallback_page_count and not native_view_count:
        techdraw_fallback_reason = "document contains typed fallback TechDraw pages only"
    elif fallback_page_count:
        techdraw_fallback_reason = "document mixes native TechDraw views with typed fallback pages"
    elif not native_view_count:
        techdraw_fallback_reason = "document has no native TechDraw drawing views"
    techdraw_export_status = {
        "mode": techdraw_mode,
        "product_grade": bool(
            techdraw_mode == "native_techdraw"
            and techdraw_svg_path
            and techdraw_dxf_path
            and techdraw_pdf_status.get("ok")
        ),
        "native_first": True,
        "fallback_reason": techdraw_fallback_reason,
        "capabilities": techdraw_capabilities,
        "fallback_page_count": fallback_page_count,
        "native_page_count": native_page_count,
        "native_view_count": native_view_count,
        "svg": {
            "ok": bool(techdraw_svg_path),
            "exporter": "typed_vector_svg" if fallback_page_count and not native_view_count else "TechDraw.viewPartAsSvg",
            "fallback": bool(fallback_page_count and not native_view_count),
        },
        "dxf": {
            "ok": bool(techdraw_dxf_path),
            "exporter": "typed_vector_dxf" if fallback_page_count and not native_view_count else "TechDraw.writeDXFPage",
            "fallback": bool(fallback_page_count and not native_view_count),
        },
        "pdf": techdraw_pdf_status,
    }

    emit({
        "ok": True,
        "step_path": step_path,
        "stl_path": stl_path,
        "fcstd_path": fcstd_path,
        "viewer_scene_path": viewer_scene_path,
        "techdraw_svg_path": techdraw_svg_path,
        "techdraw_dxf_path": techdraw_dxf_path,
        "techdraw_pdf_path": techdraw_pdf_path,
        "techdraw_pdf_status": techdraw_pdf_status,
        "techdraw_export_status": techdraw_export_status,
        "patch_results": patch_results,
        "freecad_version": ".".join(str(part) for part in FreeCAD.Version()[:3]),
    })
except Exception:
    emit({"ok": False, "error": "freecad script error:\n" + traceback.format_exc(limit=4)})
'''


def resolve_freecadcmd() -> str | None:
    configured = os.environ.get("FREECADCMD_BINARY")
    if configured:
        return configured
    for candidate in FREECADCMD_CANDIDATES:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    for candidate in FREECADCMD_MACOS_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def run_freecad_script(
    script: str,
    *,
    freecadcmd: str | None = None,
    timeout: float = 90.0,
    workdir: str | None = None,
) -> dict:
    return _run_freecad_harness(
        script,
        freecadcmd=freecadcmd,
        timeout=timeout,
        workdir=workdir,
    )


def run_freecad_import_model(
    import_format: str,
    data_b64: str,
    *,
    freecadcmd: str | None = None,
    timeout: float = 90.0,
    workdir: str | None = None,
    filename: str | None = None,
) -> dict:
    try:
        normalized = _normalize_import_format(import_format)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    run_dir = Path(workdir or tempfile.mkdtemp())
    run_dir.mkdir(parents=True, exist_ok=True)
    suffix = _import_suffix(normalized, filename)
    import_path = run_dir / f"imported_model{suffix}"
    try:
        import_path.write_bytes(_decode_b64(data_b64, "import model"))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return _run_freecad_harness(
        "",
        freecadcmd=freecadcmd,
        timeout=timeout,
        workdir=str(run_dir),
        import_path=str(import_path),
        import_format=normalized,
    )


def run_freecad_document_script(
    script: str,
    fcstd_b64: str,
    *,
    freecadcmd: str | None = None,
    timeout: float = 90.0,
    workdir: str | None = None,
) -> dict:
    run_dir = Path(workdir or tempfile.mkdtemp())
    run_dir.mkdir(parents=True, exist_ok=True)
    document_path = run_dir / "source.FCStd"
    try:
        document_path.write_bytes(_decode_b64(fcstd_b64, "FCStd document"))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return _run_freecad_harness(
        script,
        freecadcmd=freecadcmd,
        timeout=timeout,
        workdir=str(run_dir),
        document_path=str(document_path),
    )


def run_freecad_document_inspect(
    fcstd_b64: str,
    *,
    freecadcmd: str | None = None,
    timeout: float = 90.0,
    workdir: str | None = None,
) -> dict:
    run_dir = Path(workdir or tempfile.mkdtemp())
    run_dir.mkdir(parents=True, exist_ok=True)
    document_path = run_dir / "source.FCStd"
    try:
        document_path.write_bytes(_decode_b64(fcstd_b64, "FCStd document"))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return _run_freecad_inspect_harness(
        freecadcmd=freecadcmd,
        timeout=timeout,
        workdir=str(run_dir),
        document_path=str(document_path),
    )


def run_freecad_document_patch(
    patches: list[dict],
    fcstd_b64: str,
    *,
    freecadcmd: str | None = None,
    timeout: float = 90.0,
    workdir: str | None = None,
) -> dict:
    if not isinstance(patches, list) or not patches:
        return {"ok": False, "error": "document patches must be a non-empty list"}
    run_dir = Path(workdir or tempfile.mkdtemp())
    run_dir.mkdir(parents=True, exist_ok=True)
    document_path = run_dir / "source.FCStd"
    patches_path = run_dir / "document_patches.json"
    try:
        document_path.write_bytes(_decode_b64(fcstd_b64, "FCStd document"))
        patches_path.write_text(json.dumps(patches), encoding="utf-8")
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return _run_freecad_harness(
        "",
        freecadcmd=freecadcmd,
        timeout=timeout,
        workdir=str(run_dir),
        document_path=str(document_path),
        mode="patch",
        patches_path=str(patches_path),
    )


def _run_freecad_harness(
    script: str,
    *,
    freecadcmd: str | None = None,
    timeout: float = 90.0,
    workdir: str | None = None,
    import_path: str | None = None,
    import_format: str | None = None,
    document_path: str | None = None,
    mode: str | None = None,
    patches_path: str | None = None,
) -> dict:
    binary = freecadcmd or resolve_freecadcmd()
    if not binary:
        return {
            "ok": False,
            "error": "FreeCADCmd unavailable; install FreeCAD or set FREECADCMD_BINARY",
        }

    run_dir = Path(workdir or tempfile.mkdtemp())
    run_dir.mkdir(parents=True, exist_ok=True)
    user_script = run_dir / "user_freecad_script.py"
    harness_script = run_dir / "freecad_harness.py"
    user_script.write_text(script, encoding="utf-8")
    harness_script.write_text(HARNESS, encoding="utf-8")

    env = dict(os.environ)
    env["FOURYI_FREECAD_OUT"] = str(run_dir)
    env["FOURYI_FREECAD_SCRIPT"] = str(user_script)
    if import_path:
        env["FOURYI_FREECAD_IMPORT_PATH"] = import_path
        env["FOURYI_FREECAD_IMPORT_FORMAT"] = import_format or ""
    if document_path:
        env["FOURYI_FREECAD_DOCUMENT_PATH"] = document_path
    if mode:
        env["FOURYI_FREECAD_MODE"] = mode
    if patches_path:
        env["FOURYI_FREECAD_PATCHES_PATH"] = patches_path
    try:
        proc = subprocess.run(
            [binary, str(harness_script)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(run_dir),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"FreeCADCmd exceeded {timeout}s wall-clock limit"}
    except OSError as exc:
        return {"ok": False, "error": f"FreeCADCmd failed to start: {exc}"}

    payload = _parse_freecad_result(proc.stdout)
    if payload is None:
        return {
            "ok": False,
            "error": (
                f"FreeCADCmd exited with code {proc.returncode} without a result frame\n"
                f"stdout:\n{_tail(proc.stdout)}\nstderr:\n{_tail(proc.stderr)}"
            ),
        }
    if not payload.get("ok"):
        return {"ok": False, "error": payload.get("error") or "FreeCADCmd execution failed"}

    step_path = payload.get("step_path")
    stl_path = payload.get("stl_path")
    fcstd_path = payload.get("fcstd_path")
    if not step_path or not stl_path or not fcstd_path:
        return {"ok": False, "error": "FreeCADCmd did not report STEP/STL/FCStd paths"}
    if (
        not Path(step_path).is_file()
        or not Path(stl_path).is_file()
        or not Path(fcstd_path).is_file()
    ):
        return {"ok": False, "error": "FreeCADCmd did not produce STEP/STL/FCStd exports"}

    exports = {
        "step": _b64_file(step_path),
        "stl": _b64_file(stl_path),
        "fcstd": _b64_file(fcstd_path),
    }
    viewer_scene_path = payload.get("viewer_scene_path")
    if viewer_scene_path and Path(viewer_scene_path).is_file():
        exports["viewer_scene"] = _b64_file(viewer_scene_path)
    techdraw_svg_path = payload.get("techdraw_svg_path")
    if techdraw_svg_path and Path(techdraw_svg_path).is_file():
        exports["techdraw_svg"] = _b64_file(techdraw_svg_path)
    techdraw_dxf_path = payload.get("techdraw_dxf_path")
    if techdraw_dxf_path and Path(techdraw_dxf_path).is_file():
        exports["techdraw_dxf"] = _b64_file(techdraw_dxf_path)
    techdraw_pdf_path = payload.get("techdraw_pdf_path")
    if techdraw_pdf_path and Path(techdraw_pdf_path).is_file():
        exports["techdraw_pdf"] = _b64_file(techdraw_pdf_path)
    return {
        "ok": True,
        "preview_png_b64": render_preview_isolated(stl_path),
        "exports": exports,
        "patch_results": payload.get("patch_results") or [],
        "techdraw_pdf_status": payload.get("techdraw_pdf_status"),
        "techdraw_export_status": payload.get("techdraw_export_status"),
        "freecad_exit_code": proc.returncode,
        "freecad_version": payload.get("freecad_version"),
    }


def _run_freecad_inspect_harness(
    *,
    freecadcmd: str | None = None,
    timeout: float = 90.0,
    workdir: str | None = None,
    document_path: str,
) -> dict:
    binary = freecadcmd or resolve_freecadcmd()
    if not binary:
        return {
            "ok": False,
            "error": "FreeCADCmd unavailable; install FreeCAD or set FREECADCMD_BINARY",
        }

    run_dir = Path(workdir or tempfile.mkdtemp())
    run_dir.mkdir(parents=True, exist_ok=True)
    user_script = run_dir / "user_freecad_script.py"
    harness_script = run_dir / "freecad_harness.py"
    user_script.write_text("", encoding="utf-8")
    harness_script.write_text(HARNESS, encoding="utf-8")

    env = dict(os.environ)
    env["FOURYI_FREECAD_OUT"] = str(run_dir)
    env["FOURYI_FREECAD_SCRIPT"] = str(user_script)
    env["FOURYI_FREECAD_DOCUMENT_PATH"] = document_path
    env["FOURYI_FREECAD_MODE"] = "inspect"
    try:
        proc = subprocess.run(
            [binary, str(harness_script)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(run_dir),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"FreeCADCmd exceeded {timeout}s wall-clock limit"}
    except OSError as exc:
        return {"ok": False, "error": f"FreeCADCmd failed to start: {exc}"}

    payload = _parse_freecad_result(proc.stdout)
    if payload is None:
        return {
            "ok": False,
            "error": (
                f"FreeCADCmd exited with code {proc.returncode} without a result frame\n"
                f"stdout:\n{_tail(proc.stdout)}\nstderr:\n{_tail(proc.stderr)}"
            ),
        }
    if not payload.get("ok"):
        return {"ok": False, "error": payload.get("error") or "FreeCADCmd inspection failed"}
    return {
        "ok": True,
        "document_summary": payload.get("document_summary") or {},
        "freecad_exit_code": proc.returncode,
        "freecad_version": payload.get("freecad_version"),
    }


def _normalize_import_format(value: str) -> str:
    normalized = (value or "").lower().lstrip(".")
    if normalized not in SUPPORTED_IMPORT_FORMATS:
        raise ValueError(f"unsupported import format: {value}")
    return normalized


def _import_suffix(normalized: str, filename: str | None) -> str:
    if filename and "." in filename:
        suffix = Path(filename).suffix
        if suffix:
            return suffix
    return ".FCStd" if normalized == "fcstd" else f".{normalized}"


def _decode_b64(data_b64: str, label: str) -> bytes:
    import base64
    import binascii

    try:
        data = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"invalid base64 for {label}") from exc
    if not data:
        raise ValueError(f"empty {label}")
    return data


def _parse_freecad_result(stdout: str) -> dict | None:
    for line in reversed((stdout or "").splitlines()):
        if not line.startswith(FREECAD_RESULT_PREFIX):
            continue
        try:
            return json.loads(line[len(FREECAD_RESULT_PREFIX) :])
        except json.JSONDecodeError:
            return None
    return None


def _tail(value: str, limit: int = 4000) -> str:
    value = value or ""
    return value[-limit:]


def main() -> None:
    try:
        request = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        json.dump({"ok": False, "error": f"invalid request: {exc}"}, sys.stdout)
        return
    operation = request.get("operation") or "run_script"
    if operation == "import_model":
        json.dump(
            run_freecad_import_model(
                request.get("format", ""),
                request.get("data_b64", ""),
                filename=request.get("filename"),
            ),
            sys.stdout,
        )
        return
    if operation == "edit_document":
        json.dump(
            run_freecad_document_script(
                request.get("script", ""),
                request.get("fcstd_b64", ""),
            ),
            sys.stdout,
        )
        return
    if operation == "inspect_document":
        json.dump(
            run_freecad_document_inspect(
                request.get("fcstd_b64", ""),
            ),
            sys.stdout,
        )
        return
    if operation == "patch_document":
        json.dump(
            run_freecad_document_patch(
                request.get("patches", []),
                request.get("fcstd_b64", ""),
            ),
            sys.stdout,
        )
        return
    json.dump(run_freecad_script(request.get("script", "")), sys.stdout)


if __name__ == "__main__":
    main()
