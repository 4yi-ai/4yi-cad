import pytest
from pydantic import ValidationError

from app.agent.building import infer_building_typology, is_building_prompt
from app.cad.building_spec import BuildingSpec, default_building_spec


@pytest.mark.parametrize(
    ("prompt", "typology"),
    [
        ("生成一栋楼房", "residential_tower"),
        ("设计一栋18层住宅塔楼", "residential_tower"),
        ("make an office building", "office_tower"),
        ("生成一座现代别墅", "villa"),
    ],
)
def test_building_intent_and_typology(prompt, typology):
    assert is_building_prompt(prompt)
    assert infer_building_typology(prompt) == typology


@pytest.mark.parametrize("typology", ["residential_tower", "office_tower", "villa"])
def test_default_building_specs_are_valid_and_round_trip(typology):
    spec = default_building_spec(typology)
    assert BuildingSpec.model_validate(spec.model_dump()) == spec
    assert spec.total_height_mm > 0
    assert spec.assumptions
    assert spec.target_lod == "lod200"


def test_building_spec_rejects_unknown_fields():
    payload = default_building_spec().model_dump()
    payload["freeform_python"] = "import os"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BuildingSpec.model_validate(payload)


def test_building_spec_rejects_impossible_core():
    payload = default_building_spec().model_dump()
    payload["structure"]["core_width_mm"] = 29_900
    with pytest.raises(ValidationError, match="leaves no usable floor area"):
        BuildingSpec.model_validate(payload)


def test_building_spec_rejects_window_taller_than_clear_storey():
    payload = default_building_spec().model_dump()
    payload["openings"]["window_height_mm"] = 2_500
    payload["openings"]["sill_height_mm"] = 900
    with pytest.raises(ValidationError, match="typical clear storey height"):
        BuildingSpec.model_validate(payload)
