from app.freecad_intents import parse_freecad_intent


SUMMARY = {
    "objects": [
        {"name": "Box", "label": "Box", "type_id": "Part::Box", "shape": {"valid": True}},
        {"name": "Boss", "label": "Boss", "type_id": "Part::Cylinder", "shape": {"valid": True}},
    ],
    "sketches": [
        {"name": "Sketch", "label": "Sketch", "type_id": "Sketcher::SketchObject"},
    ],
    "assemblies": [
        {"name": "Assembly", "label": "Assembly", "type_id": "Assembly::AssemblyObject"},
    ],
    "techdraw": [
        {"name": "Page", "label": "Page", "type_id": "TechDraw::DrawPage"},
    ],
}


def test_freecad_intent_parses_exact_property_patch():
    result = parse_freecad_intent("Box.Length = 25", SUMMARY)

    assert result["ok"] is True
    assert result["intent"] == "set_property"
    assert result["patches"] == [
        {
            "op": "set_property",
            "selector": {"name": "Box"},
            "property": "Length",
            "value": 25.0,
        }
    ]


def test_freecad_intent_parses_sketch_external_geometry():
    result = parse_freecad_intent("add external geometry Edge1 from Box to sketch Sketch", SUMMARY)

    assert result["ok"] is True
    assert result["intent"] == "add_external_geometry"
    assert result["patches"][0]["selector"] == {"name": "Sketch"}
    assert result["patches"][0]["source_selector"] == {"name": "Box"}
    assert result["patches"][0]["references"] == ["Edge1"]


def test_freecad_intent_parses_sketch_rectangle_geometry():
    result = parse_freecad_intent("add rectangle 12 8 to sketch Sketch", SUMMARY)

    assert result["ok"] is True
    patch = result["patches"][0]
    assert patch["op"] == "add_geometry"
    assert patch["selector"] == {"name": "Sketch"}
    assert patch["geometry"]["type"] == "rectangle"
    assert patch["geometry"]["points"] == [[0, 0, 0], [12.0, 8.0, 0]]


def test_freecad_intent_parses_validate_sketch():
    result = parse_freecad_intent("validate sketch Sketch", SUMMARY)

    assert result["ok"] is True
    assert result["intent"] == "validate_sketch"
    assert result["patches"] == [{"op": "validate_sketch", "selector": {"name": "Sketch"}, "solve": True}]


def test_freecad_intent_parses_remove_sketch_constraint():
    result = parse_freecad_intent("remove constraint #2 from sketch Sketch", SUMMARY)

    assert result["ok"] is True
    assert result["intent"] == "remove_constraint"
    assert result["patches"][0]["constraint_index"] == 2


def test_freecad_intent_parses_assembly_joint():
    result = parse_freecad_intent(
        "create distance joint between Box and Boss in assembly Assembly at 12",
        SUMMARY,
    )

    assert result["ok"] is True
    patch = result["patches"][0]
    assert patch["op"] == "create_joint"
    assert patch["selector"] == {"name": "Assembly"}
    assert patch["joint_type"] == "distance"
    assert patch["connector1"]["selector"] == {"name": "Box"}
    assert patch["connector2"]["selector"] == {"name": "Boss"}
    assert patch["distance"] == 12.0


def test_freecad_intent_parses_techdraw_projection_group():
    result = parse_freecad_intent("TechDraw projection Front Top", SUMMARY)

    assert result["ok"] is True
    patch = result["patches"][0]
    assert patch["op"] == "add_techdraw_projection_group"
    assert patch["page_selector"] == {"name": "Page"}
    assert patch["source_selector"] == {"name": "Box"}
    assert patch["projection_names"] == ["Front", "Top"]


def test_freecad_intent_parses_techdraw_chain_dimension():
    result = parse_freecad_intent("TechDraw chain dimension Edge3", SUMMARY)

    assert result["ok"] is True
    patch = result["patches"][0]
    assert patch["op"] == "add_techdraw_dimension"
    assert patch["dimension_mode"] == "chain"
    assert patch["reference"] == "Edge3"


def test_freecad_intent_misses_unknown_command():
    result = parse_freecad_intent("make this nicer", SUMMARY)

    assert result["ok"] is False
    assert result["patches"] == []
