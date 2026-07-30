# Importing 4yi-cad as a 4yi dedicated app

Follow the platform runbook `docs/runbooks/2026-07-14-next-ai-draw-io-import.md`
(in the XClaw repo). `import-proposal.reference.json` in this folder is the target
the edited wizard proposal should match.

## Steps

1. **Analyze** — `/admin/marketplace/ai-import` → **Dedicated app** → repo URL
   `https://github.com/4yi-ai/4yi-cad`, branch `main` (`public_git`). The wizard
   scans and generates a proposal.
2. **Edit the proposal** to match `import-proposal.reference.json`:
   - one **public** service, `runtime_port` **8080**, `health_path` **`/healthz`**
   - `auth_policy` **`platform_sso`**
   - `platform_runtime.gateway`: `apiBaseEnv:[OPENAI_BASE_URL, OPENAI_API_BASE]`, `apiKeyEnv:[OPENAI_API_KEY]`
   - `TEXT_MODEL` slot: real tool-calling model id as `defaultModel` + `allowedModels`
     (no vision requirement)
   - `CAD_FREECAD_UPLOAD_MAX_BYTES=104857600` for the Private Beta default
     100 MB FCStd/STEP/IGES/BREP upload cap
   - **no** native LLM key env proposed as a required secret
   - `memory_request_mb` = measured worst-case peak RSS, schedulable on one node
   - stateful behavior is explicit: SQLite session metadata and filesystem CAD
     artifacts use `${CAD_DATA_DIR}` when the platform injects it; otherwise they
     fall back to pod-local `/tmp`. Confirm the live install is PVC/object-storage
     backed before making Public Beta/GA durability claims.
3. **Release** — CodeBuild → ECR (sets `last_image_uri`). Confirm the image builds
   from the root `Dockerfile`.
4. **Publish** — needs a smoke pass + **tenant-isolation certification**. The
   certification gate is the sandbox: generated code runs with a scrubbed env (no
   gateway token / `XCLAW_*`), no network egress, non-root, read-only rootfs +
   writable `/tmp` tmpfs, seccomp, CPU/mem/wall-clock limits.
5. **Cross-org install smoke** — install into a second org; generate a model;
   confirm LLM + compute bill the **installing** org (`resolvePerOrgToken`), and
   the app is reachable within the ~70s readiness budget (SPA retries on 503).

## Pre-publish checklist

- [ ] `/healthz` returns 200 fast, independent of any render (liveness safe)
- [ ] `/api/freecad/upload_policy` reports the intended upload cap and formats
- [ ] `/api/freecad/smoke` returns `ok:true` in the built container (single-container
      FreeCADCmd path is installed and can export STEP/STL)
- [ ] `/api/production/smoke` reports `durable_storage_configured:true` for installs
      that have a PVC/object-storage backed `CAD_DATA_DIR`
- [ ] gateway calls hit the injected `${OPENAI_BASE_URL}` or `${OPENAI_API_BASE}`
      `/chat/completions` endpoint (not `/responses`, not `api.openai.com`)
- [ ] self-correction (V1) uses multiple <290s calls, never one long call
- [ ] `/tmp` is a writable tmpfs; rootfs read-only; runs as non-root
- [ ] sandbox proof: generated code cannot read `OPENAI_API_KEY` or reach the network/IMDS
- [ ] **license gate**: FreeCAD GPL components + any ported Text23D code are
      license-compatible for a public distributed image (resolve before wide release)
