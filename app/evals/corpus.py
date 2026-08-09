"""Eval corpus loader.

Corpus cases are YAML files under evals/cases/. This module is imported only by
scripts and tests — never by the production app (it needs PyYAML, a dev-only dep).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

VALID_DOMAINS = {"site_layout", "building", "mechanical"}
VALID_TIERS = {"t1", "t2", "t3"}
VALID_ROLES = {"plot", "building", "water", "play", "amenity"}
VALID_BUILDING_ELEMENTS = {
    "building",
    "storey",
    "slab",
    "wall",
    "window",
    "door",
    "core",
    "stair",
    "roof",
    "space",
}
VALID_BUILDING_TYPOLOGIES = {"residential_tower", "office_tower", "villa"}
VALID_TARGET_LODS = {"lod200", "lod300"}
DEFAULT_TIMEOUT_S = 900


class CorpusError(ValueError):
    """Raised when a corpus case file is missing or malformed."""


@dataclass(frozen=True)
class EvalCase:
    id: str
    domain: str
    tier: str
    prompt: str
    required_roles: tuple[str, ...] = ()
    required_elements: tuple[str, ...] = ()
    min_objects: int | None = None
    expected_typology: str | None = None
    expected_storeys: int | None = None
    target_lod: str | None = None
    smoke: bool = False
    timeout_s: int = DEFAULT_TIMEOUT_S


def load_case(path: Path) -> EvalCase:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CorpusError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise CorpusError(f"{path}: case must be a YAML mapping")

    def _req_str(key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise CorpusError(f"{path}: missing or empty required field {key!r}")
        return value.strip()

    case_id = _req_str("id")
    domain = _req_str("domain")
    tier = _req_str("tier")
    prompt = _req_str("prompt")
    if domain not in VALID_DOMAINS:
        raise CorpusError(f"{path}: invalid domain {domain!r}")
    if tier not in VALID_TIERS:
        raise CorpusError(f"{path}: invalid tier {tier!r}")

    roles_raw = raw.get("required_roles") or []
    if not isinstance(roles_raw, list) or any(r not in VALID_ROLES for r in roles_raw):
        raise CorpusError(f"{path}: required_roles must be a list from {sorted(VALID_ROLES)}")

    elements_raw = raw.get("required_elements") or []
    if not isinstance(elements_raw, list) or any(
        element not in VALID_BUILDING_ELEMENTS for element in elements_raw
    ):
        raise CorpusError(
            f"{path}: required_elements must be a list from {sorted(VALID_BUILDING_ELEMENTS)}"
        )

    expected_typology = raw.get("expected_typology")
    if expected_typology is not None and expected_typology not in VALID_BUILDING_TYPOLOGIES:
        raise CorpusError(
            f"{path}: expected_typology must be one of {sorted(VALID_BUILDING_TYPOLOGIES)}"
        )

    expected_storeys = raw.get("expected_storeys")
    if expected_storeys is not None and (
        not isinstance(expected_storeys, int) or not 1 <= expected_storeys <= 80
    ):
        raise CorpusError(f"{path}: expected_storeys must be an integer from 1 to 80")

    target_lod = raw.get("target_lod")
    if target_lod is not None and target_lod not in VALID_TARGET_LODS:
        raise CorpusError(f"{path}: target_lod must be one of {sorted(VALID_TARGET_LODS)}")

    if domain == "building" and not elements_raw:
        raise CorpusError(f"{path}: building cases require required_elements")

    min_objects = raw.get("min_objects")
    if min_objects is not None and (not isinstance(min_objects, int) or min_objects < 1):
        raise CorpusError(f"{path}: min_objects must be a positive integer")

    timeout_s = raw.get("timeout_s", DEFAULT_TIMEOUT_S)
    if not isinstance(timeout_s, int) or timeout_s < 1:
        raise CorpusError(f"{path}: timeout_s must be a positive integer")

    return EvalCase(
        id=case_id,
        domain=domain,
        tier=tier,
        prompt=prompt,
        required_roles=tuple(roles_raw),
        required_elements=tuple(elements_raw),
        min_objects=min_objects,
        expected_typology=expected_typology,
        expected_storeys=expected_storeys,
        target_lod=target_lod,
        smoke=bool(raw.get("smoke", False)),
        timeout_s=timeout_s,
    )


def load_corpus(root: Path) -> list[EvalCase]:
    cases = [load_case(p) for p in sorted(root.rglob("*.yaml"))]
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise CorpusError(f"duplicate case id {case.id!r}")
        seen.add(case.id)
    return sorted(cases, key=lambda c: c.id)
