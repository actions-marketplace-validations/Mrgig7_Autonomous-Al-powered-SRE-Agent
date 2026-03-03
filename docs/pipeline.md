# Pipeline Flow (Current Implementation)

This document describes what is **actually wired and executed** in the current codebase, with pointers to the relevant modules. It also notes implemented components that are not yet connected to the async pipeline.

## End-to-End (What Runs Today)

### 1) Webhook Ingestion (FastAPI)

- FastAPI app wiring: [main.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/main.py)
- GitHub webhook endpoint: [github_webhook](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/api/webhooks/github.py)
- GitLab webhook endpoint: [gitlab_webhook](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/api/webhooks/gitlab.py)

The webhook handler verifies authenticity, normalizes the provider payload into a common schema, stores it in Postgres, and enqueues async processing via Celery.

### 2) Event Storage (PostgreSQL)

- DB model: [PipelineEvent](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/models/events.py)
- Idempotent store + status updates: [EventStore](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/services/event_store.py)

Events are stored with an `idempotency_key` and are upserted to avoid duplicate processing.

### 3) Async Dispatch (Celery)

- Dispatcher task: [process_pipeline_event](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/tasks/dispatch.py)
- Context + RCA task: [build_failure_context](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/tasks/context_tasks.py)
- Fix pipeline task: [run_fix_pipeline](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/tasks/fix_pipeline_tasks.py)

After context building and RCA, the pipeline creates a `fix_pipeline_run` record and enqueues the deterministic fix pipeline.

### 4) Context + Root Cause Analysis

- Context build: [ContextBuilder](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/services/context_builder.py)
- RCA: [RCAEngine](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/intelligence/rca_engine.py)

## Phase 2: Deterministic Fix Pipeline (Plan → Patch → Validate)

- Orchestrator: [FixPipelineOrchestrator](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/fix_pipeline/orchestrator.py)
- Run persistence: [FixPipelineRun](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/models/fix_pipeline.py)
- FixPlan schema: [FixPlan](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/schemas/fix_plan.py)
- Plan generation (JSON-only): [PlanGenerator](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/ai/plan_generator.py)
- Deterministic patch generation: [PatchGenerator](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/fix_pipeline/patch_generator.py)
- Safety policy checks: [PolicyEngine](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/safety/policy_engine.py)
- Sandbox validation: [ValidationOrchestrator](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/sandbox/validator.py)
- PR creation: [PROrchestrator](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/pr/pr_orchestrator.py)
- Adapter interface + registry: [adapters](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/adapters)

### Pipeline Steps

1. ADAPTER SELECT: pick the best adapter based on logs + repo file hints; enforce adapter allow-lists.
2. PLAN: LLM returns **FixPlan JSON only**; schema validated strictly.
3. PLAN SAFETY: policy engine evaluates plan intent (files/category/op types).
4. PATCH: deterministic diff generation constrained to `plan.files` and supported ops.
5. PATCH SAFETY: policy engine evaluates the generated diff (paths, secrets, size limits, danger score).
6. SCAN: supply-chain scans run inside sandbox (gitleaks, trivy fs, syft SBOM).
7. VALIDATE: sandbox validation must pass (either framework-detected tests or adapter-provided commands).
8. PR: created only after successful validation; labeled `safe` or `needs-review`; never auto-merged.

## Phase 6: Explainability + Trust (Evidence-Backed)

Phase 6 surfaces an evidence-backed view of a pipeline run by exposing **only persisted artifacts** via `/api/v1` endpoints.

### Sources of Truth (No UI-only data)

- `pipeline_events` is the source of failure identity (`failure_id`, `repo`, `branch`, `commit_sha`, `error_message`).
- `fix_pipeline_runs` is the source of:
  - `context_json` (includes raw logs or log summary)
  - `rca_json` (RCA result)
  - `plan_json` (FixPlan JSON)
  - `plan_policy_json` / `patch_policy_json` (PolicyDecision including danger reasons and violations)
  - `patch_diff` + `patch_stats_json`
  - `validation_json` (sandbox + scans summary)
  - `artifact_json` (provenance + explainability extras)

### Explainability Persistence (Minimum Viable)

`artifact_json` includes two additional optional fields:

- `evidence`: top log lines with line indices and tags (redacted)
- `timeline`: pipeline step list with timestamps and results (when available)

### API Endpoints

- `GET /api/v1/failures/{failure_id}/analysis`
- `GET /api/v1/failures/{failure_id}/explain`
- `GET /api/v1/runs/{run_id}/artifact`
- `GET /api/v1/runs/{run_id}/diff`
- `GET /api/v1/runs/{run_id}/timeline`

## Current Gaps (Known)

- Adapters are intentionally conservative; only a small set of deterministic fix categories are supported per adapter.
