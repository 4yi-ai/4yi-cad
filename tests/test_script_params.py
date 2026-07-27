import pytest

from app.cad.script_params import (
    ScriptParameterPatch,
    apply_script_parameter_patches,
    extract_script_parameters,
)


def test_extracts_named_numeric_parameters_before_result_assignment():
    script = """import cadquery as cq

sofa_length, sofa_depth, seat_height = 200, 82.5, 38
arm_thickness = 14
result = cq.Workplane("XY").box(sofa_length, sofa_depth, seat_height)
ignored_after_result = 1
"""

    params = extract_script_parameters(script)

    assert [param["name"] for param in params] == [
        "sofa_length",
        "sofa_depth",
        "seat_height",
        "arm_thickness",
    ]
    assert params[1]["value"] == 82.5
    assert params[0]["unit"] == "mm"


def test_patches_single_and_tuple_assignments_without_rewriting_script():
    script = """import cadquery as cq

sofa_length, sofa_depth, seat_height = 200, 82.5, 38
arm_thickness = 14
result = cq.Workplane("XY").box(sofa_length, sofa_depth, seat_height)
"""

    patched = apply_script_parameter_patches(
        script,
        [
            ScriptParameterPatch(name="sofa_depth", value=90),
            ScriptParameterPatch(name="arm_thickness", value=16.25),
        ],
    )

    assert "sofa_length, sofa_depth, seat_height = 200, 90, 38" in patched
    assert "arm_thickness = 16.25" in patched
    assert "result = cq.Workplane" in patched


def test_rejects_unknown_script_parameter():
    with pytest.raises(ValueError, match="unknown or non-editable"):
        apply_script_parameter_patches(
            "length = 10\nresult = None\n",
            [ScriptParameterPatch(name="width", value=20)],
        )
