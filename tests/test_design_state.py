import pytest

from app.cad.design_state import (
    CadPatch,
    apply_patches,
    default_design_state,
    enabled_feature_ids,
    geometry_summary,
    render_cadquery_script,
)


def test_update_parameter_patch_preserves_feature_tree():
    state = default_design_state()
    updated = apply_patches(
        state,
        [CadPatch(op="update_parameter", name="hole_d", value=6)],
    )

    assert updated.parameters["hole_d"] == 6
    assert [feature.id for feature in updated.features] == [feature.id for feature in state.features]
    assert updated.selected_feature_id == "hole_pattern"
    assert updated.version == state.version + 1


def test_render_script_uses_updated_parameters():
    state = apply_patches(
        default_design_state(),
        [CadPatch(op="update_parameter", name="hole_d", value=6)],
    )

    script = render_cadquery_script(state)

    assert "hole_d = 6" in script
    assert ".vertices().hole(hole_d)" in script
    assert "result =" in script


def test_suppress_feature_removes_it_from_enabled_summary_and_script():
    state = apply_patches(
        default_design_state(),
        [CadPatch(op="suppress_feature", feature_id="hole_pattern")],
    )

    assert "hole_pattern" not in enabled_feature_ids(state)
    assert "hole_pattern" not in geometry_summary(state)["features_enabled"]
    assert ".vertices().hole(hole_d)" not in render_cadquery_script(state)


def test_invalid_update_parameter_patch_is_rejected():
    with pytest.raises(ValueError, match="name and value"):
        apply_patches(default_design_state(), [CadPatch(op="update_parameter")])

