# Phase 2 — Deterministic Plan → Patch → Validate Pipeline (Production-Ready)

## A) Title + Objective
Implement a deterministic Fix Pipeline that strictly follows:
1) PLAN (LLM outputs **structured FixPlan JSON only**)
2) PATCH (generate minimal diff deterministically **from the plan**)
3) VALIDATE (sandbox validation **must pass**)
Only after validation success → PR creation (never auto-merge).

## B) Deliverables Checklist
- [ ] Introduce structured FixPlan JSON schema (strict Pydantic)
- [ ] Modify LLM interaction to return FixPlan JSON ONLY (no prose)
- [ ] Strict FixPlan validation with “repair JSON” retry (max 2)
- [ ] Plan safety evaluation hook (policy engine)
- [ ] Deterministic patch generator (minimal diffs) for 2 categories
- [ ] Patch constraints enforcement (plan files only + allowed ops)
- [ ] Patch safety evaluation hook (policy engine)
- [ ] Mandatory sandbox validation before PR creation
- [ ] Persist pipeline run metadata (plan, diff stats, danger score, validation result)
- [ ] Add tests (schema, safety, determinism, pipeline happy path, unsafe blocks)
- [ ] Update README + docs/pipeline.md with Phase 2 flow + examples

## C) Implementation Plan (Step-by-Step)

### 1) Add FixPlan Schema (strict Pydantic)
- Create `src/sre_agent/schemas/fix_plan.py`
- Implement:
  - `FixPlan` with `extra="forbid"`, max ops count, confidence range 0..1
  - `FixOperation` with `type` enum-ish string set
  - validators:
    - `operation.file` ∈ `plan.files`
    - `plan.files` unique + normalized (forward slashes)

### 2) Add JSON-only LLM plan generation
- Add `src/sre_agent/ai/plan_generator.py` (small, single responsibility)
- Build prompt using existing RCA/context inputs:
  - Update `PromptBuilder` with `build_fix_plan_prompt(...)`
  - Enforce output constraints:
    - “Return JSON ONLY. No markdown. No commentary.”
    - Provide a JSON Schema-like example for FixPlan
- Parsing/validation:
  - Parse with `json.loads`
  - Validate with `FixPlan.model_validate`
  - If invalid JSON or schema errors:
    - retry with “repair JSON” prompt (max 2 retries)
    - include the validation error and the previous output
- Determinism:
  - Keep temperature at `0.0` or `0.1` (current uses 0.1)
  - Sort any derived lists (evidence, files) post-parse to prevent nondeterministic ordering

### 3) Plan Safety Evaluation Hook
- Use Phase 1 policy engine:
  - call `evaluate_plan(PlanIntent(target_files=plan.files))`
- Also enforce Phase 2 plan constraints deterministically:
  - operation.type must be in allowed set (schema)
  - category must be one of supported Phase 2 categories (block early if not)
- If plan violates policy:
  - record failure in persistent run record (see step 7)
  - exit pipeline with a structured failure response (no patch generation)

### 4) Deterministic Patch Generation (2 categories)
Create `src/sre_agent/fix_pipeline/patch_generator.py` with **no LLM diff generation**.

Supported deterministic categories:

**Category A: Python missing dependency**
- Supported operations: `add_dependency`, `pin_dependency`
- Supported files: `pyproject.toml` (Poetry) and/or `requirements.txt`
- Implementation approach (deterministic, minimal diff):
  - For `pyproject.toml`:
    - edit only within `[tool.poetry.dependencies]` or `[tool.poetry.group.*.dependencies]` if explicitly targeted by plan
    - insert/update a single dependency line (stable alphabetical placement)
    - do not rewrite entire file (avoid TOML serializer; do targeted line edits)
  - For `requirements.txt`:
    - add or replace exact line `pkg==x.y.z` or `pkg>=...` as dictated

**Category B: Lint/format fixes (minimal template)**
- Supported operation: `remove_unused`
- Focus: Python unused import cleanup only (deterministic, small diffs)
- Implementation:
  - `details` must include enough info to locate exact line (e.g. `import`, `from`, `name`) and optionally a `line_contains` substring
  - remove only the matched import symbol from the import statement
  - if statement becomes empty, remove the whole line
  - also apply minimal whitespace normalization (rstrip trailing spaces, ensure newline at EOF) on changed files only

Patch generation outputs:
- Use `difflib.unified_diff` to produce stable unified diffs
- Stable ordering:
  - process files in sorted order
  - deterministic hunk generation

### 5) Patch Constraints + Safety Evaluation Hook
In `patch_generator` and orchestrator:
- Ensure diff touches **only** `plan.files`
- Ensure diff touches **only** the target file declared on each operation
- Enforce policy thresholds:
  - after diff creation call `policy.evaluate_patch(diff_text)`
  - if blocked → stop pipeline
- “Applies cleanly” pre-check:
  - use existing `RepoManager.apply_patch(check_only=True)` on a fresh clone snapshot

### 6) Validation Gate + Orchestrator
Create `src/sre_agent/fix_pipeline/orchestrator.py`.

Orchestrator flow for a given `PipelineEvent`:
1) Load event from DB
2) Clone repo snapshot at `commit_sha` using existing `RepoManager.clone(...)`
3) Build failure context + RCA (reuse existing `ContextBuilder` + `RCAEngine` in-process; Phase 2 keeps scope minimal)
4) PLAN:
   - call `PlanGenerator.generate_plan(...)` → `FixPlan`
   - evaluate plan safety
5) PATCH:
   - call `PatchGenerator.generate(repo_path, plan)` → `diff_text + stats`
   - evaluate patch safety
6) VALIDATE:
   - call `ValidationOrchestrator.validate(ValidationRequest(... diff_text ...))`
   - if not PASSED → stop (no PR)
7) PR:
   - convert plan+patch into existing `FixSuggestion` object (minimal fields)
   - call `PROrchestrator.create_pr_for_fix(...)`
   - PR labels include `safe`/`needs-review` based on safety status (already supported)

Key invariants:
- Validator is always executed before PR creation
- Any policy BLOCK stops the pipeline

### 7) Persistence (Production-Ready, minimal)
Add DB persistence for pipeline run artifacts.

- New SQLAlchemy model: `src/sre_agent/models/fix_pipeline.py`
  - table `fix_pipeline_runs`
  - columns:
    - `id` UUID
    - `event_id` UUID (FK to pipeline_events)
    - `status` (enum string)
    - `plan_json` JSONB
    - `plan_policy_json` JSONB
    - `patch_diff` Text
    - `patch_stats_json` JSONB (files/lines/bytes)
    - `patch_policy_json` JSONB
    - `validation_json` JSONB
    - `pr_json` JSONB
    - timestamps
- Alembic migration `alembic/versions/003_add_fix_pipeline_runs.py`
- Repository/service: `src/sre_agent/fix_pipeline/store.py` with minimal methods:
  - `create_run(event_id)`
  - `update_plan(run_id, plan, policy_decision)`
  - `update_patch(run_id, diff, stats, policy_decision)`
  - `update_validation(run_id, validation_result)`
  - `update_pr(run_id, pr_result)`
  - `mark_failed(run_id, reason)`

### 8) Wire into Celery pipeline (minimal)
- Add Celery task `run_fix_pipeline(event_id: str)` in `src/sre_agent/tasks/fix_pipeline_tasks.py`
- Modify `build_failure_context` task (after RCA) to enqueue `run_fix_pipeline.delay(event_id)` **only on failures**.

### 9) Tests (Mandatory)
Add unit tests with mocking (no real Ollama, Docker, GitHub):
- `tests/unit/test_fix_plan_schema.py`
  - invalid JSON fails
  - extra fields rejected
  - op.file not in files rejected
  - max operations enforced
- `tests/unit/test_patch_generation.py`
  - forbidden file path plan blocked (policy)
  - patch touching extra file blocked
  - deterministic output: same plan → same diff
  - dependency add/pin patch minimal
- `tests/unit/test_fix_pipeline.py`
  - pipeline happy path:
    - mock PlanGenerator to return valid plan
    - mock PatchGenerator to return diff
    - mock ValidationOrchestrator to return PASSED
    - mock PRCreator/PROrchestrator to capture PRRequest and labels
    - assert persistence store called with plan + stats + validation
  - pipeline fails when unsafe:
    - plan policy blocked OR patch policy blocked OR validation failed → no PR

### 10) Documentation updates
- Update `docs/pipeline.md`:
  - add Phase 2 flow diagram and current wiring points (Celery chain)
- Update `README.md`:
  - “Phase 2: Deterministic Fix Pipeline” section
  - sample FixPlan JSON
  - explain PLAN and PATCH policy checks + validation gate

## D) File-by-File Change List (Planned)

**New**
- `src/sre_agent/schemas/fix_plan.py`
- `src/sre_agent/ai/plan_generator.py`
- `src/sre_agent/fix_pipeline/patch_generator.py`
- `src/sre_agent/fix_pipeline/orchestrator.py`
- `src/sre_agent/fix_pipeline/store.py`
- `src/sre_agent/models/fix_pipeline.py`
- `src/sre_agent/tasks/fix_pipeline_tasks.py`
- `tests/unit/test_fix_plan_schema.py`
- `tests/unit/test_patch_generation.py`
- `tests/unit/test_fix_pipeline.py`
- `alembic/versions/003_add_fix_pipeline_runs.py`

**Updated**
- `src/sre_agent/ai/prompt_builder.py` (add FixPlan prompt)
- `src/sre_agent/tasks/context_tasks.py` (enqueue fix pipeline after RCA)
- `src/sre_agent/models/__init__.py` (export new model if needed)
- `src/sre_agent/config.py` (optional: Phase 2 knobs like plan retries/max ops)
- `README.md`
- `docs/pipeline.md`

## E) Code Snippets (Key Parts — Intended Implementation)

### FixPlan Schema (sketch)
```python
class FixOperation(BaseModel):
    type: Literal[
        "add_dependency",
        "pin_dependency",
        "update_config",
        "modify_code",
        "remove_unused",
    ]
    file: str
    details: dict[str, Any]
    rationale: str
    evidence: list[str]

class FixPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_cause: str
    category: str
    confidence: confloat(ge=0.0, le=1.0)
    files: list[str]
    operations: list[FixOperation]

    @model_validator(mode="after")
    def validate_files(self):
        file_set = set(self.files)
        for op in self.operations:
            if op.file not in file_set:
                raise ValueError("operation.file must be in plan.files")
        if len(self.operations) > 10:
            raise ValueError("too many operations")
        return self
```

### Orchestrator (sketch)
```python
run = store.create_run(event_id)
plan = plan_generator.generate_plan(context, rca)
plan_decision = policy.evaluate_plan(PlanIntent(target_files=plan.files))
if not plan_decision.allowed:
    store.update_plan(run, plan, plan_decision)
    return fail

diff = patch_generator.generate(repo_path, plan)
patch_decision = policy.evaluate_patch(diff)
if not patch_decision.allowed:
    store.update_patch(run, diff, stats, patch_decision)
    return fail

validation = validator.validate(...)
if validation.status != PASSED:
    store.update_validation(run, validation)
    return fail

pr = pr_orchestrator.create_pr_for_fix(...)
store.update_pr(run, pr)
```

### Safety Hook Points
- Plan safety: `PolicyEngine.evaluate_plan(PlanIntent(target_files=plan.files))`
- Patch safety: `PolicyEngine.evaluate_patch(diff_text)`
- PR labeling: pass `safe`/`needs-review` label into `PRRequest.labels`

## F) Verification Commands + Expected Outputs
After implementation:

```bash
poetry run ruff check .
poetry run black --check .
poetry run mypy src
poetry run pytest
```
Expected:
- ruff/black/mypy exit code 0
- pytest: all tests pass (includes new Phase 2 tests)

Runtime smoke:
```bash
docker-compose up -d postgres redis
poetry run alembic upgrade head
poetry run uvicorn sre_agent.main:app --host 0.0.0.0 --port 8000
```
Expected:
- API starts successfully
- Webhook ingest triggers Celery pipeline

Demo pipeline trigger (existing webhook endpoint):
- POST a failing workflow_job payload to `/webhooks/github`
Expected:
- Event stored
- Celery triggers context/RCA
- Celery triggers Phase 2 fix pipeline task
- Run record persisted in `fix_pipeline_runs`

Sample demo output JSON (structure to produce):
```json
{
  "event_id": "...",
  "run_id": "...",
  "plan": {"category": "python_missing_dependency", "files": ["pyproject.toml"], "operations": [...]},
  "plan_policy": {"allowed": true, "danger_score": 10, "pr_label": "safe"},
  "patch_stats": {"files": 1, "lines_added": 1, "lines_removed": 0, "bytes": 180},
  "patch_policy": {"allowed": true, "danger_score": 12, "pr_label": "safe"},
  "validation": {"status": "passed", "tests_failed": 0},
  "pr": {"status": "created", "labels": ["auto-fix", "sre-agent", "safe"]}
}
```

## G) Risks + Mitigations
- Diff editing for TOML without a serializer can be brittle.
  - Mitigation: narrow edits to a single dependency line within the exact section; add unit tests covering insertion/update.
- Repo cloning adds latency.
  - Mitigation: reuse existing clone for patch generation; validation will still clone in sandbox (acceptable for Phase 2).
- Multi-provider clone URLs vary.
  - Mitigation: Phase 2 production-ready for GitHub first; other providers return structured “unsupported clone URL” error without attempting unsafe behavior.

## H) What NOT To Do (Prevent Scope Creep)
- Do not add a workflow engine / state machine framework.
- Do not implement multi-language patching.
- Do not add eval harness/security scanning.
- Do not add auto-merge.
- Do not let LLM generate diffs directly (Phase 2 stays deterministic).

---

If you approve this plan, I will implement Phase 2 incrementally in the mandated order (schema → JSON-only plan → safety hook → deterministic patch generator → orchestrator → tests → docs), and I will provide exact diffs + verification outputs.