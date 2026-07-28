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
        "WORKBENCH_SESSION_STORAGE_KEY",
        "4yi-cad.workbench.session.v1",
        "SERVER_SESSION_ID_STORAGE_KEY",
        "4yi-cad.server-session-id.v1",
        "persistWorkbenchSession",
        "restoreWorkbenchSession",
        "recordServerVersion",
        "restoreServerSession",
        "artifactRefs",
        "hydrateArtifactRefs",
        "rollbackToServerVersion",
        "selectedFreeCadObjectName",
        "freeCadImportInput",
        "openFreeCadImportPicker",
        "importFreeCadModelFile",
        "detectFreeCadImportFormat",
        "/api/freecad/import_model",
        "generatedCadViewer",
        "mountGeneratedCadViewer",
        "viewer_scene",
        "generatedViewerSceneData",
        "addViewerSceneFaceMeshes",
        "addViewerSceneEdgeTargets",
        "addViewerSceneVertexTargets",
        "viewerSceneHasEdgeGeometry",
        "viewerSceneHasVertexGeometry",
        "hasInteractiveViewerArtifact",
        "selectFreeCadObject",
        "selectFreeCadSubelement",
        "renderFreeCadSubelementActions",
        "createSketchOnSelectedFace",
        "attachSketchToSelectedFace",
        "addSelectedExternalGeometry",
        "addSelectedTechDrawDimension",
        "addSelectedTechDrawCenterline",
        "addSelectedTechDrawCosmeticVertex",
        "assemblyConnectorAnchor",
        "setAssemblyConnectorAnchor",
        "clearAssemblyConnectorAnchor",
        "createAssemblyJointFromSelectedConnectors",
        "assemblyConnectorPayload",
        "renderFreeCadAssemblySection",
        "renderFreeCadTechDrawSection",
        "renderFreeCadDiagnostics",
        "freeCadConnectorFrameText",
        "renderFreeCadConnectorFrameRows",
        "renderFreeCadSketchGeometryList",
        "renderFreeCadSketchConstraintList",
        "exportFreeCadTechDrawPage",
        "stable_id",
        "legacyStableId",
        "stable_reference",
        "stable_references",
        "stable_signatures",
        "signatureVersion",
        "freeCadSubelementSignature",
        "freeCadSubelementStableReference",
        "solver_diagnostics",
        "layout_diagnostics",
        "edit_mode",
        "selectFreeCadAssemblyJoint",
        "commitFreeCadJointType",
        "commitFreeCadJointScalar",
        "commitFreeCadJointConnector",
        "solveFreeCadAssembly",
        "addViewerSceneAssemblyJoints",
        "viewerSceneConnectorPoint",
        "selectedFreeCadAssemblyJoint",
        "createFixedJoint",
        "createRevoluteJoint",
        "createDistanceJoint",
        "commitFreeCadObjectProperty",
        "commitFreeCadPlacementComponent",
        "commitFreeCadConstraintValue",
        "interactiveMesh",
        "freeCadViewerSelectionMode",
        "pickFreeCadViewerSelection",
        "freeCadSubelementKindForSelectionMode",
        "toolbar.face",
        "toolbar.edge",
        "toolbar.vertex",
        "setSelectionMode",
        "shouldRequestScriptRewrite",
        "request_script_rewrite",
        "/api/sessions",
        "/rollback",
        "commitParameter",
        "submitCommand",
        "4yi-cad Workbench",
        "4yi-cad 工作台",
    ):
        assert marker in html


def test_p1_workbench_restores_browser_session_before_fetching_initial_state():
    html = _html()

    assert "if (restoreWorkbenchSession())" in html
    assert "if (await restoreServerSession())" in html
    assert "/api/design/initial" in html
    assert html.index("if (restoreWorkbenchSession())") < html.index("/api/design/initial")
    assert html.index("if (restoreWorkbenchSession())") < html.index(
        "if (await restoreServerSession())"
    )
    assert html.index("if (await restoreServerSession())") < html.index("/api/design/initial")


def test_generated_hole_edit_prefers_local_parameter_patch():
    html = _html()

    assert "generatedParameterAliasScore" in html
    assert 'name === "hole_d"' in html
    assert 'key: "chat.commandNotUnderstood"' in html
    assert "if (useAgent) {" in html


def test_p1_workbench_does_not_expose_topology_or_arbitrary_feature_history_controls():
    html = _html()

    for marker in (
        "rollbackFeature",
        "deleteFeature",
        "actions.rollback",
        "actions.deleteFeature",
    ):
        assert marker not in html
