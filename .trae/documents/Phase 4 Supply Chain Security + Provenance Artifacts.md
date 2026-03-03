## A) Phase 4 Title + Objective
**Phase 4 — Supply Chain Security + Validation Hardening + Provenance Artifacts**
Harden the Fix Pipeline by running deterministic, safe-by-default supply-chain scans (gitleaks, trivy, syft) inside the existing Docker sandbox, blocking PR creation on unsafe findings or scanner failures, and persisting a redacted provenance artifact per pipeline run with an API endpoint for dashboard consumption.

## B) Deliverables Checklist
- [ ] Add scanner integration into sandbox validation (gitleaks, trivy, syft)
- [ ] Define scanner result schemas (Pydantic)
- [ ] Produce provenance artifact JSON for every pipeline run (redacted, auditable)
- [ ] Store provenance artifact in Postgres per run
- [ ] Expose artifact via API endpoint
- [ ] Add unit tests for scanner parsing + artifact creation/redaction
- [ ] Update docs/README (tools, local verification, artifact structure, redaction policy)

## C) Architecture Changes (Where scans run, where artifacts stored)
- **Where scans run**: inside the existing `DockerSandbox.run_command()` container session, after patch is applied and before tests run. This is implemented by extending [validator.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/sandbox/validator.py) to execute scanners in the mounted `/workspace`.
- **Tooling determinism**: introduce a dedicated sandbox image (built by docker-compose) that contains pinned versions of:
  - gitleaks
  - trivy
  - syft
  Trivy will be run with `--skip-db-update` and the DB snapshot is baked into the image at build time.
- **Fail-safe behavior**:
  - Any scanner execution failure (non-zero exit, timeout, missing binary) → validation failure → PR blocked.
  - Secrets found → always fail.
  - Vulnerabilities → fail if severity ≥ configured threshold (default: HIGH).
- **Provenance storage**:
  - Add `artifact_json` (JSONB) + SBOM metadata columns to `fix_pipeline_runs` (existing run persistence table) so each run has a single authoritative provenance artifact.
  - Store SBOM payloads on local disk under a configured `artifacts/` directory (host filesystem) as gzipped JSON; DB stores only `path`, `sha256`, `size`.
- **API exposure**:
  - Add `GET /api/v1/runs/{run_id}/artifact` returning the persisted provenance JSON. Protected with existing RBAC permission (same as dashboard read).

## D) Implementation Plan (in the required order)
### 1) Update sandbox image for scanner tools
- Add `docker/sandbox.Dockerfile` (new) that builds from `python:3.11-slim` and installs pinned versions of gitleaks/trivy/syft.
- Bake trivy DB snapshot during image build and run trivy at runtime with `--skip-db-update`.
- Update `docker-compose.yml` to build the sandbox image on `docker-compose up -d` using a lightweight “image-builder” service that exits immediately.
- Update default `sandbox_docker_image` setting to the new image name.

### 2) Add scanner runner modules + parsers
- Create `src/sre_agent/sandbox/scanners/`:
  - `base.py` (common runner helpers: command execution, timeouts, version capture)
  - `gitleaks.py` (run + parse + redact)
  - `trivy.py` (run + parse severity counts + enforce threshold)
  - `syft.py` (generate SBOM JSON, compute sha256/size, write gz to artifacts dir)
- Parsers MUST only persist summaries + redacted fields:
  - gitleaks: store count, rule IDs, and redacted file path identifiers; never store matched secret strings.
  - trivy: store total + severity counts + top packages; no raw file content.
  - syft: store only metadata in DB; SBOM bytes stored on disk.

### 3) Integrate scan stage into validator
- Extend `ValidationResult` schema to include a `scans` field (summary) and per-tool versions/durations.
- In [validator.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/sandbox/validator.py):
  - After `sandbox.create()` and before `run_tests`, run scans when `enable_scans=true`.
  - If scans fail / findings exceed thresholds → set validation status FAILED/ERROR and return.
  - Ensure scanner outputs are not appended to `result.logs`.

### 4) Provenance artifact builder
- Add `src/sre_agent/artifacts/provenance.py`:
  - Pydantic schema for the provenance artifact
  - Builder function that merges: run fields (plan, policies, patch stats), scan summaries, validation, timestamps/durations, tool versions.
  - Redaction policy enforced centrally (no secret values stored).
- Modify `FixPipelineOrchestrator.run()` to build & store artifact in a `finally:` block so *every run* gets an artifact even on early exits.

### 5) DB persistence + migration
- Extend `FixPipelineRun` model to include:
  - `artifact_json` JSONB
  - `sbom_path` (text), `sbom_sha256` (string), `sbom_size` (int)
- Add Alembic migration `004_add_fix_pipeline_artifacts`.

### 6) API endpoint
- Add `src/sre_agent/api/artifacts.py` with:
  - `GET /api/v1/runs/{run_id}/artifact`
  - RBAC dependency consistent with dashboard permission.
- Wire router in `main.py`.

### 7) Tests
- Add:
  - `tests/unit/test_scanner_parsers.py` (mock gitleaks/trivy JSON; verify redaction + threshold logic)
  - `tests/unit/test_provenance_artifact.py` (build artifact from sample inputs; verify no secret fields; required keys present)

### 8) Docs update
- Update `README.md` + `docs/pipeline.md`:
  - How scans work, tool versions, how to build sandbox image, how to verify scanners.
  - Config flags and defaults.
  - Sample redacted provenance artifact JSON.

## E) File-by-File Change List (planned)
- **New**: `docker/sandbox.Dockerfile`
- **Update**: `docker-compose.yml` (build sandbox image on `up -d`)
- **New**: `src/sre_agent/sandbox/scanners/{__init__.py,base.py,gitleaks.py,trivy.py,syft.py}`
- **Update**: `src/sre_agent/sandbox/validator.py` (scan stage + fail-safe)
- **New**: `src/sre_agent/artifacts/provenance.py`
- **Update**: `src/sre_agent/models/fix_pipeline.py` + new Alembic migration
- **Update**: `src/sre_agent/fix_pipeline/orchestrator.py` (always persist provenance)
- **New**: `src/sre_agent/api/artifacts.py` + `main.py` router wiring
- **New tests**: `tests/unit/test_scanner_parsers.py`, `tests/unit/test_provenance_artifact.py`
- **Docs**: `README.md`, `docs/pipeline.md`

## F) Verification Steps (commands + expected outputs)
1) Bring stack up and build sandbox image:
```bash
docker-compose up -d
```
Expected:
- API/worker/postgres/redis start
- Sandbox scanner image is built locally

2) Verify scanners exist in sandbox image:
```bash
docker run --rm <sandbox-image> gitleaks version
docker run --rm <sandbox-image> trivy --version
docker run --rm <sandbox-image> syft version
```
Expected: pinned version strings.

3) Run a fix end-to-end to trigger scans:
- Trigger a normal pipeline run (webhook → context/RCA → fix pipeline) and confirm:
  - validation step runs scans
  - unsafe findings block PR creation
  - provenance artifact written to DB

4) Fetch artifact via API:
```bash
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/runs/<run_id>/artifact
```
Expected:
- JSON provenance artifact
- No raw secrets in output

5) Test suite:
```bash
poetry run pytest
```
Expected: all tests pass.

## G) Risks + Mitigations
- **Trivy DB requires network**: mitigated by baking DB into sandbox image and running with `--skip-db-update`.
- **Scanner output may contain secrets**: mitigated by never storing raw scanner logs, and enforcing redaction in parsers/provenance builder.
- **Sandbox image build time**: mitigated by pinned versions and caching; compose builds once.
- **Tool absence/misconfiguration**: mitigated by fail-safe behavior (scanner failure blocks PR).

## H) What NOT To Do (Scope Control)
- No CVE remediation automation or dependency upgrading logic.
- No multi-cloud SBOM storage; only local artifacts dir + DB metadata.
- No complex vuln policy engine; only severity threshold.
- No network scanning; only filesystem scanning in sandbox.

If you approve this plan, I will implement it end-to-end and validate the stop conditions (docker-compose up, scans run, artifacts persisted + API, pytest passes).