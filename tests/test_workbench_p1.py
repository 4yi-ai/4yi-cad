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
        "freeCadUploadPolicy",
        "FALLBACK_FREECAD_UPLOAD_MAX_BYTES",
        "/api/freecad/upload_policy",
        "importTooLarge",
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
        "LCS",
        "renderFreeCadSketchGeometryList",
        "renderFreeCadSketchConstraintList",
        "renderFreeCadSketchPointEditor",
        "renderFreeCadSketchConstraintEditor",
        "renderFreeCadSketchConstraintPalette",
        "renderFreeCadSketchSolverMap",
        "renderFreeCadConstraintRefChips",
        "renderFreeCadSketchGeometryConstraintRefs",
        "freeCadConstraintGlyphText",
        "freeCadConstraintGeometryRefs",
        "freeCadConstraintInternalGeometryRefs",
        "freeCadConstraintGeometryIndexSet",
        "freeCadSketchIssueConstraintIndexes",
        "freeCadSketchIssueGeometryIndexes",
        "selectFreeCadSketchConstraintReference",
        "focusFreeCadSketchConstraintReferences",
        "renderFreeCadSketchConstraintGlyphActions",
        "freeCadSketchConstraintActionButton",
        "renderFreeCadSketchSolverIssuePanel",
        "renderFreeCadSketchSolverIssueActions",
        "freeCadSketchIssueRows",
        "freeCadSketchIssueAddConstraintRows",
        "freeCadSketchIssueRowKey",
        "constraint-glyph-card",
        "constraint-ref-chip",
        "sketchSolverMap",
        "sketchSolverIssues",
        "sketchConstraintGlyph",
        "constraintReferences",
        "focusAllReferences",
        "focusConstraint",
        "focusGeometryIndex",
        "underConstrained",
        "conflictingConstraints",
        "redundantConstraints",
        "malformedConstraints",
        "enableConstraint",
        "makeDriving",
        "setFreeCadSketchTool",
        "setFreeCadSketchSnap",
        "sketchEditTool",
        "freeCadModelPointOnCameraPlane",
        "freeCadSketchPointDragTarget",
        "sketchDragPreview",
        "setFreeCadSketchDragPreview",
        "clearFreeCadSketchDragPreview",
        "updateFreeCadSketchDragPreviewGeometry",
        "freeCadSketchDragPreviewText",
        "sketchDragCommit",
        "sketchLiveSolve",
        "sketchLiveSolveChecking",
        "scheduleFreeCadSketchLiveSolve",
        "cancelFreeCadSketchLiveSolve",
        "freeCadSketchLiveSolveStatusText",
        "freeCadSketchLiveSolveSummary",
        "freeCadSketchSummaryFromPreview",
        "quiet: true",
        "liveSolve",
        "commitFreeCadSketchGeometryPointValue",
        "previewFreeCadDocumentPatch",
        "previewAndCommitFreeCadSketchGeometryPointValue",
        "renderFreeCadSketchDryRunReview",
        "commitFreeCadSketchDryRunReview",
        "resolveFreeCadSketchDryRunConstraint",
        "sketchDryRunReview",
        "sketchDryRunBlocked",
        "checkingSketchSolver",
        "dry_run",
        "would_create_version",
        "addFreeCadSketchPrimitive",
        "addFreeCadSketchConstraint",
        "addFreeCadSketchPairConstraint",
        "addFreeCadSketchPointDistanceConstraint",
        "addFreeCadSketchPointOnObjectConstraint",
        "addFreeCadSketchRadiusConstraint",
        "sketchGeometryConstraintAnchor",
        "commitFreeCadSketchConstraintState",
        "commitFreeCadSketchConstraintName",
        "addFreeCadSketchCoincidentConstraint",
        "setFreeCadSketchCoincidenceAnchor",
        "clearFreeCadSketchCoincidenceAnchor",
        "toggleFreeCadSketchGeometryConstruction",
        "sketchCoincidenceAnchor",
        "add_endpoint_coincidence",
        "set_geometry_construction",
        "set_constraint_state",
        "removeFreeCadSketchConstraint",
        "commitFreeCadSketchGeometryPoint",
        "addViewerSceneSketchEditOverlay",
        "sketch_point",
        "selectedFreeCadSketchElement",
        "selectFreeCadSketchElement",
        "freeCadSketchGeometryAnchor",
        "freeCadConstraintSeverity",
        "conflicting_constraints",
        "redundant_constraints",
        "malformed_constraints",
        "exportFreeCadTechDrawPage",
        "assembly_capabilities",
        "techdraw_capabilities",
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
        "freeCadTechDrawValidationText",
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


def test_generated_scene_description_routes_to_agent():
    html = _html()

    assert "hasSceneGenerationIntent" in html
    assert r"(?:x|×|\*)" in html
    assert "花园|园区|场地|地块" in html
    assert "房子|楼房|楼层|楼|别墅|泳池|游泳池" in html


def test_p1_workbench_does_not_expose_topology_or_arbitrary_feature_history_controls():
    html = _html()

    for marker in (
        "rollbackFeature",
        "deleteFeature",
        "actions.rollback",
        "actions.deleteFeature",
    ):
        assert marker not in html
