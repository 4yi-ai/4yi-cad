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
        "sanitizeChatHistory",
        "generationActivity",
        "startGenerationActivity",
        "pushGenerationActivity",
        "finishGenerationActivity",
        "renderGenerationActivityOverlay",
        "renderGenerationActivityChatMessage",
        "recordServerVersion",
        "clearServerSessionReference",
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
        "freeCadViewerObjectStyle",
        "freeCadStyleColorValue",
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
        "siteLayoutAudit",
        "siteLayoutStatus",
        "siteLayoutCoverage",
        "siteLayoutReferenceDelta",
        "siteLayoutRepairStatus",
        "freeCadSiteLayoutAudit",
        "renderFreeCadSiteLayoutDelta",
        "freeCadDiagnostics",
        "semanticRole",
        "freeCadObjectSemanticRoleText",
        "freeCadSiteLayoutStatusText",
        "freeCadSiteLayoutCoverageText",
        "site_layout",
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
        "startNewWorkbenchSession",
        "resetClientWorkbenchSession",
        "sessionHistoryButton",
        "sessionHistoryDrawer",
        "openSessionHistory",
        "loadSessionHistory",
        "restoreSessionFromHistory",
        "renderSessionHistoryDrawer",
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


def test_chat_history_is_sanitized_before_restore_and_generate():
    html = _html()

    assert "function sanitizeChatHistory(history)" in html
    assert 'item.role === "user" || item.role === "assistant"' in html
    assert "state.chatHistory = sanitizeChatHistory(saved.chatHistory);" in html
    assert "history: mode === \"rewrite\" ? [] : sanitizeChatHistory(state.chatHistory)" in html


def test_missing_server_session_is_cleared_and_retried():
    html = _html()

    assert "function clearServerSessionReference(options = {})" in html
    assert "function isMissingServerSessionError(error)" in html
    assert "for (let attempt = 0; attempt < 2; attempt += 1)" in html
    assert "if (attempt === 0 && isMissingServerSessionError(saveError))" in html
    assert "clearServerSessionReference();" in html
    assert "if (isMissingServerSessionError(error)) clearServerSessionReference();" in html


def test_generate_errors_include_response_detail_in_logs():
    html = _html()

    assert "async function responseErrorMessage(resp)" in html
    assert "const text = await resp.text();" in html
    assert "json.detail || json.error || text" in html
    assert "throw new Error(await responseErrorMessage(resp));" in html


def test_generated_hole_edit_prefers_local_parameter_patch():
    html = _html()

    assert "generatedParameterAliasScore" in html
    assert 'name === "hole_d"' in html
    assert 'key: "chat.commandNotUnderstood"' in html
    assert "if (useAgent) {" in html


def test_generated_parameter_patch_requires_explicit_edit_intent():
    html = _html()
    parser_body = html[
        html.index("function shouldParseGeneratedParameterPatch"):
        html.index("function parseGeneratedParameterPatch")
    ]

    assert "shouldParseGeneratedParameterPatch" in html
    assert "generatedParameterNameMentioned" in html
    assert "if (!shouldParseGeneratedParameterPatch(text, params)) return null;" in html
    assert "层数|楼层|层|数量" in html
    assert "isCreateOrScenePrompt" in html
    assert "mechanicalAssemblyNoun" in html
    assert "landing\\s+gear" in html
    assert "起落架|机械装配|液压缸|连杆机构" in html
    assert "create|generate|model" not in parser_body
    assert "decrease|make" not in parser_body


def test_generated_unmapped_command_falls_back_to_agent():
    html = _html()

    assert "shouldUseAgentForUnmappedCommand" in html
    assert "function hasActiveGeneratedModel()" in html
    assert "const createOrScenePrompt = isCreateOrScenePrompt(text);" in html
    assert "const generatedPatch = !createOrScenePrompt && !freeCadPatch" in html
    assert "const patch = !createOrScenePrompt && !freeCadPatch" in html
    assert "const agentMode = commandModeForAgent(text, rewriteScript);" in html
    assert "const useAgent = createOrScenePrompt ||" in html
    assert 'await generateFromPrompt(text, { mode: agentMode });' in html
    assert "return hasActiveGeneratedModel();" in html
    assert 'state.previewMode === "generated"' in html


def test_generated_agent_followups_rewrite_current_model_by_default():
    html = _html()

    assert "function isExplicitNewModelPrompt(text)" in html
    assert "function commandModeForAgent(text, rewriteScript)" in html
    assert 'if (hasActiveGeneratedModel() && !isExplicitNewModelPrompt(text)) return "rewrite";' in html
    assert "Continue editing the current CAD model." in html
    assert "Current model brief:" in html
    assert "const previousGeneratedPrompt = state.generatedPrompt;" in html
    assert 'const displayPrompt = mode === "rewrite" && previousGeneratedPrompt ? previousGeneratedPrompt : prompt;' in html
    assert "state.generatedPrompt = displayPrompt;" in html
    assert 'const sawDone = await readSse(resp, prompt, { mode, instruction: prompt });' in html
    assert "const userInstruction = context.instruction || state.generatedPrompt;" in html
    assert 'recordServerVersion(ev.ok ? (rewrite ? "modify" : "create") : "repair", userInstruction' in html


def test_viewer_scene_styles_drive_frontend_materials():
    html = _html()

    assert "function freeCadViewerObjectStyle(obj)" in html
    assert "function freeCadStyleColorValue(style, hexKey, rgbKey, fallback)" in html
    assert "function freeCadSemanticRoleFromText(text)" in html
    assert "function freeCadInferredSemanticRole(obj)" in html
    assert "semantic_role" in html
    assert "人工湖|水景|水系|湖|河|池|lake|pond|water|river|pool" in html
    assert "edge_color" in html
    assert "point_color" in html
    assert "const objectStyle = freeCadViewerObjectStyle(obj);" in html
    assert "color: selected ? 0x2f80ed : objectStyle.color" in html
    assert "color: selected ? 0xf59e0b : objectStyle.edgeColor" in html
    assert "color: selected ? 0x22c55e : objectStyle.pointColor" in html
    assert "Math.max(objectStyle.opacity, 0.88)" in html


def test_generated_viewer_uses_local_three_semantic_fallback_and_camera_focus():
    html = _html()

    assert 'import("/static/vendor/three/three.module.js")' in html
    assert 'import("/static/vendor/three/STLLoader.js")' in html
    assert 'import("/static/vendor/three/OrbitControls.js")' in html
    assert "generatedViewerFitMode" in html
    assert "function addFreeCadSemanticObjectVisuals(THREE, group)" in html
    assert "const semanticTargets = addFreeCadSemanticObjectVisuals(THREE, group);" in html
    assert "opacity: hasSemanticVisuals ? 0.18 : 0.86" in html
    assert "function selectedFreeCadFocusInfo(viewerScene = null)" in html
    assert "function applyGeneratedViewerCamera(runtime, focusInfo = null)" in html
    assert "function parseFreeCadViewerSceneB64(b64)" in html
    assert "function freeCadViewerScenePresentation(scene)" in html
    assert "function freeCadApplyViewerScenePresentation(scene, { force = false } = {})" in html
    assert "applyFreeCadViewerSceneArtifactPresentation(ev.data_b64, { force: true })" in html
    assert "presentation.default_view || presentation.defaultView" in html
    assert "cameraHint.distance_multiplier ?? cameraHint.distanceMultiplier" in html
    assert "function addFreeCadBuildingDetailOverlays(THREE, group)" in html
    assert "function freeCadBuildingFloorCount(info)" in html
    assert "addFreeCadBuildingDetailOverlays(THREE, group);" in html
    assert "mode !== \"vertex\" && !objectSelected" in html
    assert "camera.up.copy(generatedCameraUpVector(THREE, state.viewMode));" in html
    assert 'state.generatedViewerFitMode = "selection";' in html
    assert 'state.generatedViewerFitMode = "all";' in html


def test_agent_generation_exposes_working_activity_feedback():
    html = _html()

    assert ".status-chip.working" in html
    assert ".generation-activity" in html
    assert "aria-live=\"polite\"" in html
    assert "activity-spinner" in html
    assert "activity-bar" in html
    assert "activitySubmitting" in html
    assert "activityThinking" in html
    assert "activityScript" in html
    assert "activityRendering" in html
    assert "activityRetry" in html
    assert "activityPreview" in html
    assert "activityArtifact" in html
    assert "startGenerationActivity(displayPrompt, mode);" in html
    assert 'setBottomTab("chat");' in html
    assert "renderGenerationActivityChatMessage()" in html
    assert "pushGenerationActivity(\"activityRendering\"" in html
    assert "node.classList.toggle(\"working\"" in html


def test_new_session_button_resets_current_workbench_session():
    html = _html()

    assert 'id="newSessionButton"' in html
    assert 'id="newSessionButtonText"' in html
    assert "function resetClientWorkbenchSession()" in html
    assert "localStorage.removeItem(WORKBENCH_SESSION_STORAGE_KEY);" in html
    assert "clearServerSessionReference({ persist: false });" in html
    assert "state.activeServerVersionId = \"\";" in html
    assert "state.lastServerVersionSignature = \"\";" in html
    assert "state.chatHistory = [];" in html
    assert 'state.chatMessages = [{ role: "assistant", key: "chat.newSessionStarted" }];' in html
    assert "window.startNewWorkbenchSession = startNewWorkbenchSession;" in html


def test_session_history_drawer_lists_and_restores_server_sessions():
    html = _html()

    assert 'id="sessionHistoryButton"' in html
    assert 'id="sessionHistoryDrawer"' in html
    assert "function renderSessionHistoryDrawer()" in html
    assert "function openSessionHistory()" in html
    assert "function loadSessionHistory()" in html
    assert "function restoreSessionFromHistory(sessionId)" in html
    assert 'fetchJson("/api/sessions?limit=30")' in html
    assert "state.sessionHistoryItems" in html
    assert "sessionSummaryPreviewUrl" in html
    assert "localStorage.setItem(SERVER_SESSION_ID_STORAGE_KEY, sessionId);" in html
    assert "await applyStoredServerVersion(active, saved.versions || []);" in html
    assert 'key: "chat.sessionRestored"' in html
    assert "window.openSessionHistory = openSessionHistory;" in html
    assert "window.restoreSessionFromHistory = restoreSessionFromHistory;" in html


def test_p1_workbench_does_not_expose_topology_or_arbitrary_feature_history_controls():
    html = _html()

    for marker in (
        "rollbackFeature",
        "deleteFeature",
        "actions.rollback",
        "actions.deleteFeature",
    ):
        assert marker not in html
