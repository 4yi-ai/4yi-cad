from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "index.html"


def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_p1_workbench_exposes_core_surfaces():
    html = _html()

    for marker in (
        'id="toolbar"',
        'id="localeToggle"',
        'id="featureTree"',
        'id="properties"',
        'id="commandForm"',
        "/api/design/initial",
        "/api/design/patch",
        "/api/design/render",
        "/api/generate",
        "commitParameter",
        "submitCommand",
        "4yi-cad Workbench",
        "4yi-cad 工作台",
    ):
        assert marker in html


def test_p1_workbench_does_not_expose_topology_or_arbitrary_feature_history_controls():
    html = _html()

    for marker in (
        "toolbar.face",
        "toolbar.edge",
        "modes.face",
        "modes.edge",
        "setSelectionMode('face')",
        "setSelectionMode('edge')",
        "rollbackFeature",
        "deleteFeature",
        "actions.rollback",
        "actions.deleteFeature",
    ):
        assert marker not in html
