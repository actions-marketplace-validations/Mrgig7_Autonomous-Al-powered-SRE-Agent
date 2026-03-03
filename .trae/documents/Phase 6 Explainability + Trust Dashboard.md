## A) Phase 6 Title + Objective

Implement an explainability + trust layer (API + React dashboard) that makes every agent claim verifiable via evidence links: log lines, policy decisions (including danger score breakdown), scan summaries, sandbox validation results, and exact patch diffs.

## B) Deliverables Checklist

* [ ] Add new API endpoints for explainability

  * [ ] GET /api/v1/failures/{failure\_id}/explain

  * [ ] GET /api/v1/runs/{run\_id}/artifact (upgrade to typed + redacted)

  * [ ] GET /api/v1/runs/{run\_id}/diff

  * [ ] GET /api/v1/runs/{run\_id}/timeline

* [ ] Provide Pydantic schemas for all responses

* [ ] Add log evidence extraction (top-k + line indices)

* [ ] Link evidence to FixPlan operations (best-effort mapping)

* [ ] Add danger score breakdown + policy rule matches

* [ ] Add confidence score explanation (adapter + plan + validation)

* [ ] React dashboard: Failure Detail view with 6 tabs (Overview/Evidence/Fix/Safety/Validation/Artifact)

* [ ] Add redaction logic (backend + frontend-safe rendering)

* [ ] Add tests (API contract + redaction + artifact/diff)

* [ ] Update README with screenshots placeholders + API usage

## C) Data Model: Stored vs Computed

### Stored (already in DB)

* PipelineEvent (failure) in `pipeline_events` (id, repo, branch, commit\_sha, error\_message, timestamps).

* FixPipelineRun in `fix_pipeline_runs`:

  * context\_json (FailureContextBundle dump, including raw log\_content/log\_summary, extracted errors/stack traces)

  * rca\_json (RCAResult dump)

  * plan\_json (FixPlan)

  * plan\_policy\_json / patch\_policy\_json (PolicyDecision including `danger_reasons[]`)

  * patch\_diff + patch\_stats\_json

  * validation\_json (ValidationResult incl. scan summaries)

  * artifact\_json (ProvenanceArtifact-ish)

### New (minimal persistence, no heavy DB redesign)

* Persist explainability extras into `artifact_json`:

  * `evidence`: extracted log evidence lines with `idx` (line number), `tag`, and optional `operation_idx`.

  * `timeline`: pipeline step events with timestamps and status.

### Computed at runtime (API service)

* “Failure explain” payload assembled from PipelineEvent + latest FixPipelineRun for that event.

* Confidence breakdown:

  * adapter detection confidence (from `detection_json.confidence` if present)

  * plan confidence (from `plan_json.confidence`)

  * validation outcome (from `validation_json.status`)

  * deterministic weighted aggregation + a human-readable explanation list.

* Danger score breakdown:

  * primarily from `plan_policy_json.danger_reasons` and `patch_policy_json.danger_reasons`.

## D) API Contract + Example JSON

### New schemas (new module)

Create `src/sre_agent/schemas/explainability.py` defining:

* EvidenceLine

* ConfidenceFactor

* DangerReasonItem (mirrors PolicyDecision.danger\_reasons)

* ExplainSafety (label, danger\_score, danger\_breakdown, violations)

* ExplainValidation (sandbox status + tests/lint best-effort)

* FailureExplainResponse

* RunDiffResponse

* TimelineStep + RunTimelineResponse

* RunArtifactResponse (typed wrapper around stored artifact JSON)

### Endpoints

1. `GET /api/v1/failures/{failure_id}/explain`

* Auth: `require_permission(Permission.VIEW_FAILURES)`

* Data source:

  * PipelineEvent by id

  * most recent FixPipelineRun for event\_id (query by event\_id order by created\_at desc)

* Response fields come only from persisted JSON columns (no invented steps).

Example (shape):

```json
{
  "failure_id": "...",
  "repo": "owner/repo",
  "summary": {
    "category": "python_missing_dependency",
    "root_cause": "Missing Python dependency: requests",
    "adapter": "python",
    "confidence": 0.83,
    "confidence_breakdown": [
      {"factor": "adapter_detection", "value": 0.9, "weight": 0.4, "note": "matched ModuleNotFoundError"},
      {"factor": "plan_confidence", "value": 0.7, "weight": 0.4, "note": "FixPlan confidence"},
      {"factor": "validation", "value": 1.0, "weight": 0.2, "note": "sandbox passed"}
    ]
  },
  "evidence": [
    {"idx": 102, "line": "ModuleNotFoundError: No module named 'requests'", "tag": "root-cause"},
    {"idx": 107, "line": "FAILED tests/test_api.py::test_health", "tag": "test-failure"}
  ],
  "proposed_fix": {
    "plan": {"category": "python_missing_dependency", "files": ["pyproject.toml"], "operations": [...]},
    "files": ["pyproject.toml"],
    "diff_available": true
  },
  "safety": {
    "label": "safe",
    "danger_score": 22,
    "danger_breakdown": [
      {"code": "operation_type", "weight": 10, "message": "Operation: add_dependency"},
      {"code": "file_count", "weight": 5, "message": "Files touched: 1"}
    ],
    "violations": []
  },
  "validation": {
    "sandbox": "passed",
    "tests": "pass",
    "lint": "skipped",
    "scans": {"gitleaks": {"status": "pass"}, "trivy": {"status": "pass"}, "sbom": {"status": "pass"}}
  }
}
```

1. `GET /api/v1/runs/{run_id}/artifact`

* Upgrade existing endpoint to:

  * return `RunArtifactResponse`

  * apply explainability redaction defensively on output (even if stored artifact is already redacted)

1. `GET /api/v1/runs/{run_id}/diff`

* Return:

  * `diff_text` = redacted `FixPipelineRun.patch_diff`

  * `stats` = `patch_stats_json`

1. `GET /api/v1/runs/{run_id}/timeline`

* Primary source:

  * `artifact_json.timeline` if present

* Fallback (non-hallucinatory):

  * return empty list OR minimal entries with null timestamps and `status="unknown"` when step timing data is absent.

## E) UI Components Structure (2–4 high-value pages)

### Routing

* Add a single new page: `frontend/src/pages/FailureDetails.tsx`

* Add route under dashboard shell: `/failures/:failureId`

* Link into it from:

  * Recent Failures table

  * Pipeline Events list

### FailureDetails Tabs (simple, no new heavy libs)

* OverviewTab: summary, status, repo/branch/commit, run id, buttons (copy ids)

* EvidenceTab: log evidence list (idx + tag + redacted line) with highlight

* FixTab: minimal FixPlan renderer + DiffViewer (unified diff as preformatted text with basic file headers folding)

* SafetyTab: danger score + reasons list + violations list

* ValidationTab: scan summaries + sandbox result summary + link to raw logs snippet if present

* ArtifactTab: JsonViewer (collapsible sections) + download JSON

### Components (small, local)

* `frontend/src/components/DiffViewer.tsx`

* `frontend/src/components/JsonViewer.tsx`

* `frontend/src/components/SeverityBadge.tsx`

* `frontend/src/components/Timeline.tsx` (simple list with duration)

## F) File-by-File Change List (planned)

### Backend

* Add: `src/sre_agent/explainability/evidence_extractor.py`

* Add: `src/sre_agent/explainability/redactor.py`

* Add: `src/sre_agent/explainability/explain_service.py`

* Add: `src/sre_agent/schemas/explainability.py`

* Add: `src/sre_agent/api/explainability.py` (router for /failures and /runs)

* Update: `src/sre_agent/main.py` (include new router under `/api/v1`)

* Update: `src/sre_agent/api/artifacts.py` (typed response + defensive redaction OR fold into new router)

* Update: `src/sre_agent/fix_pipeline/orchestrator.py` (persist `evidence` + `timeline` into `artifact_json` for new runs)

* Update: `src/sre_agent/artifacts/provenance.py` (extend artifact model to include optional `evidence` + `timeline` fields, and ensure redaction of those fields)

* (Optional, minimal) Update: `src/sre_agent/fix_pipeline/store.py` (helper to fetch latest run by event\_id)

### Frontend

* Add: `frontend/src/pages/FailureDetails.tsx`

* Add: `frontend/src/components/{DiffViewer,JsonViewer,SeverityBadge,Timeline}.tsx`

* Update: `frontend/src/pages/Dashboard.tsx` (add links to failure detail)

* Update: `frontend/src/App.tsx` (route wiring)

* Update: `frontend/src/api/client.ts` (new API calls + TypeScript response types)

### Tests

* Add: `tests/unit/test_redactor.py`

* Add: `tests/api/test_explain_endpoints.py` (contract + redaction asserts)

* Add/Update: artifact endpoint tests to ensure consistent output

### Docs

* Update: `README.md` (Phase 6 section, screenshots placeholders, trust workflow, example explain response)

* Update: `docs/pipeline.md` (explainability artifacts mapping)

## G) Verification Commands + Expected Results

Backend:

* `poetry run ruff check .` → exit 0

* `poetry run black --check .` → unchanged

* `poetry run mypy src` → success

* `poetry run pytest` → all pass

Frontend:

* `cd frontend && npm ci && npm run build` → build succeeds

Docker-compose (local):

* `docker-compose up -d --build` → api + worker healthy

* (If we add a frontend service) `docker-compose up -d --build frontend` → UI accessible and loads failure details

## H) Risks + Mitigations

* Missing historical timeline timestamps → store timeline for new runs; return empty/unknown for older runs (no fake data).

* Secret leakage via logs/diffs/artifacts → central redactor applied at API boundary + reused in provenance; tests assert token patterns are masked.

* UI scope creep → only one new page (FailureDetails) + small components; no new visualization libraries.

* Schema drift → typed Pydantic response models + contract tests.

## I) What NOT To Do

* Don’t introduce charting/log search frameworks.

* Don’t invent data not present in DB/artifacts.

* Don’t show raw secrets/tokens; don’t bypass backend redaction.

* Don’t implement auto-approval/auto-merge.

## Implementation Order (incremental)

1. Add explainability module: redactor + evidence extractor + explain service.
2. Add Pydantic schemas + `/api/v1` endpoints.
3. Persist `evidence` + `timeline` into `artifact_json` for new runs.
4. Build FailureDetails UI + tabs + minimal viewers.
5. Add tests (redactor + endpoint contract).
6. Update docs + verification instructions.

