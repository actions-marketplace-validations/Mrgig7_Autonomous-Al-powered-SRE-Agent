# Phase 0 and Phase 1 Plan (Read-Only Research Complete)

Skill check: the only available skill is for creating new skills; this task is repo planning/implementation, so no skill invocation applies.

## Phase 0 — Baseline Audit + Minimal Refactor (No Feature Changes)

### A) Title + Objective

Phase 0: Baseline audit + minimal refactor.
Objective: accurately document current runtime flows and make the repo runnable/testable with minimal hygiene fixes, without changing product behavior.

### B) Deliverables Checklist

* [ ] Document the actual pipeline behavior and gaps vs diagram

* [ ] Confirm webhook → store → async processing steps by code references

* [ ] Add/repair “how to run locally” docs so links are not broken

* [ ] Add minimal quality gates (lint/type/test) runnable locally (and optionally CI)

* [ ] Ensure `poetry run pytest` passes in a clean env (fix only test/setup issues)

### C) Implementation Steps (Repo Paths + New Modules/Classes)

* Audit + doc:

  * Create a short pipeline/architecture doc: `docs/pipeline.md`

  * Update `README.md` architecture section to reflect what is actually wired

  * Fix broken documentation links in `QUICKSTART.md` (point to existing docs, don’t add “fake” docs)

* Tooling hygiene (minimal):

  * Add a single CI workflow `/.github/workflows/ci.yml` running Python checks (ruff/black/mypy/pytest). Assumption: CI is allowed in “no feature change” because it does not change runtime behavior.

  * Fix frontend lint script mismatch (either install eslint + config OR remove the script). Safest minimal: remove/adjust lint script to what exists.

### D) Code Changes (File List + Brief Explanation)

Planned changes (exact diffs will be shown during implementation):

* `README.md`: adjust “architecture/flow” wording to match actual chain (webhook→store→Celery→context+RCA) and explicitly note fix/validate/PR modules exist but are not chained yet.

* `QUICKSTART.md`: remove/replace links to missing `docs/*.md` files.

* `docs/pipeline.md` (new): precise, code-referenced flow documentation.

* `.github/workflows/ci.yml` (new): basic ruff/black/mypy/pytest gate.

* `frontend/package.json`: align scripts with installed deps (no new tooling unless necessary).

### E) Verification Steps (Commands + Expected Outputs)

* Python:

  * `poetry install`

  * `poetry run ruff check .` → exit code 0

  * `poetry run black --check .` → “All done! ✨ 🍰 ✨” (or equivalent) and exit code 0

  * `poetry run mypy src` → “Success: no issues found”

  * `poetry run pytest` → all tests pass

* Docker compose smoke:

  * `docker-compose up -d postgres redis` → services healthy

  * `poetry run uvicorn sre_agent.main:app --host 0.0.0.0 --port 8000`

  * `curl http://localhost:8000/health` → 200 + JSON health payload

* Frontend (if kept in Phase 0 checks):

  * `cd frontend && npm ci && npm run build` → build succeeds

### F) Risks + Mitigations

* Risk: CI/workflow additions could be seen as scope creep.

  * Mitigation: keep to one minimal workflow that runs existing tools already in `pyproject.toml`.

* Risk: frontend lint currently references eslint but it’s not installed.

  * Mitigation: choose smallest fix (align scripts; only add eslint if the repo already has configs/usage).

### G) What NOT to Do

* Don’t connect new pipeline stages (fix/validate/PR chaining) in Phase 0.

* Don’t add new infrastructure (new services, queues, etc.).

* Don’t change runtime defaults (e.g., auth behavior) except documentation clarifications.

### Explicit Assumptions (Phase 0)

* Adding CI workflows and doc fixes are “non-feature” changes.

* Pipeline “confirmation” means documenting actual wired behavior, not completing missing orchestration.

***

## Phase 1 — Fix Safety Policy Engine + Danger Score (MANDATORY)

### A) Title + Objective

Phase 1: Fix Safety Policy Engine.
Objective: add a configurable, testable policy layer that blocks forbidden changes and classifies PRs as `safe` vs `needs-review` using a deterministic danger score.

### B) Deliverables Checklist

* [ ] New `sre_agent/safety/` module with policy loading + evaluation

* [ ] YAML/JSON policy config supporting:

  * allowed paths / forbidden paths

  * forbidden secret patterns

  * patch size limits (max files, max lines added/deleted, max diff bytes)

* [ ] Deterministic danger score model + breakdown (reasons)

* [ ] Pre-flight checks on PLAN and PATCH

* [ ] Enforce blocks on forbidden changes

* [ ] PR labeling: add `safe` or `needs-review` (never auto-merge)

* [ ] Unit tests for policy enforcement

* [ ] README documentation for policy format + examples

### C) Implementation Steps (Repo Paths + New Modules/Classes)

Create `src/sre_agent/safety/`:

* `policy_models.py`: Pydantic models for the policy file + defaults

* `policy_loader.py`: load YAML/JSON from path (env or default)

* `diff_parser.py`: parse unified diff → file list + stats (added/removed/bytes)

* `policy_engine.py`:

  * `evaluate_plan(plan_intent) -> PolicyDecision`

  * `evaluate_patch(diff_text) -> PolicyDecision`

  * returns `allowed`, `violations`, `danger_score`, `danger_reasons`, `pr_label`

* `danger_score.py`: simple weighted heuristic scoring (0–100) based on rule hits

Integrations (minimal but defense-in-depth):

* Fix generation:

  * Update [fix\_generator.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/ai/fix_generator.py) to run policy evaluation on the produced patch and attach results.

  * Keep existing `FixGuardrails` behavior but either:

    * wrap it around policy engine OR

    * run both and merge results (Phase 1 can start by reusing current secret/destructive checks in policy config).

* Sandbox validation:

  * Update [validator.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/sandbox/validator.py) to re-check policy before `git apply`.

* PR creation:

  * Extend [schemas/pr.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/schemas/pr.py) to include optional `labels`.

  * Update [pr\_creator.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/pr/pr_creator.py) to apply labels from request; ensure `safe`/`needs-review` is always added.

Policy configuration:

* Add `config/safety_policy.yaml` (repo default policy).

* Add `Settings` entry in [config.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/config.py) like `safety_policy_path` (default `config/safety_policy.yaml`).

PLAN vs PATCH pre-flight (Phase 1 interpretation):

* Since Phase 2 introduces a real JSON plan, Phase 1 will treat “plan intent” as the set of target files inferred from the proposed patch + operation types.

* Implement the API now (`evaluate_plan`) and use it with this derived intent; Phase 2 will swap in real LLM plan objects.

### D) Code Changes (File List + Brief Explanation)

Planned changes:

* New:

  * `src/sre_agent/safety/*` modules (policy, diff parsing, scoring)

  * `config/safety_policy.yaml` default policy

  * `tests/unit/test_safety_policy.py` unit tests

* Updated:

  * `src/sre_agent/ai/fix_generator.py`: run policy evaluation and expose results

  * `src/sre_agent/ai/guardrails.py`: reuse or delegate to safety module (avoid duplicated secret patterns)

  * `src/sre_agent/sandbox/validator.py`: enforce policy before patch application

  * `src/sre_agent/schemas/pr.py`: add optional labels field

  * `src/sre_agent/pr/pr_creator.py`: label PRs with `safe` or `needs-review`

  * `README.md`: document policy format, examples, and how labeling works

  * `pyproject.toml`: add `PyYAML` dependency for YAML policy support

### E) Verification Steps (Commands + Expected Outputs)

* Unit tests:

  * `poetry run pytest -q` → all pass

  * `poetry run pytest -q tests/unit/test_safety_policy.py` → verifies:

    * forbidden paths block

    * allowed paths enforcement

    * secret pattern block

    * patch size limits block

    * danger score + label thresholds deterministic

* Static checks:

  * `poetry run ruff check .` → exit 0

  * `poetry run mypy src` → exit 0

* Demo (local):

  * Run a small script or API endpoint path (existing) that generates a fix; confirm that:

    * forbidden diff is rejected with policy violations

    * PR labels include `safe` or `needs-review` when PR creation is invoked

  * Evidence to capture:

    * screenshot placeholder: policy violation JSON

    * screenshot placeholder: PR labels on GitHub UI

### F) Risks + Mitigations

* Risk: diff parsing edge cases lead to false positives/negatives.

  * Mitigation: keep parser conservative; use git-style `diff --git` and `+++ b/` extraction; add unit tests for representative diffs.

* Risk: policy overlap with existing guardrails causes conflicting decisions.

  * Mitigation: define a single “source of truth” decision object; merge guardrails into policy config over time, but keep behavior stable initially.

* Risk: YAML dependency adds supply-chain surface.

  * Mitigation: use widely adopted `PyYAML`, pinned via Poetry; parsing only local config.

### G) What NOT to Do

* Don’t add auto-merge or approval automation.

* Don’t build a complex rule language; keep to allow/deny patterns + size limits + simple scoring.

* Don’t store secrets or log tokens as part of violations.

### Explicit Assumptions (Phase 1)

* “PLAN pre-flight” will be implemented now as an intent check derived from the patch until Phase 2 introduces real plan JSON.

* Default policy will be conservative about workflow/infra paths (e.g., block `.github/workflows/**`).

