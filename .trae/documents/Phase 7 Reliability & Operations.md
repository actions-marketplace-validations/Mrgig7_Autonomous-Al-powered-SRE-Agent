## A) Phase 7 Title + Objective
Harden ingestion + async processing so the system behaves predictably under duplicates, bursts, retries, and concurrency—without changing the Phase 2–6 product flow (PLAN → PATCH → SAFETY → SCANS → VALIDATION → PR).

## B) Deliverables Checklist
- [ ] Webhook deduplication (idempotent ingest)
- [ ] Task idempotency (Celery)
- [ ] Exponential backoff retry policy with max attempts
- [ ] Backpressure controls per repository/org
- [ ] Failure loop protection (cooldowns, do-not-retry)
- [ ] Explicit idempotency keys (delivery_id + run_key)
- [ ] DB constraints + migrations
- [ ] Metrics counters + structured logs
- [ ] Tests proving dedupe/retry/throttle/loop behavior
- [ ] Docs: ops knobs + incident playbook

## C) Reliability Design (Idempotency Keys + Lock Strategy)
### 1) Webhook Dedup (API)
- **Primary key (GitHub):** `X-GitHub-Delivery`.
- **DB table:** `webhook_deliveries` with `delivery_id UNIQUE`.
- **Flow:**
  - On webhook receipt, attempt insert delivery row.
  - If insert conflicts (duplicate), immediately return `200 {"status":"duplicate_ignored"}` and do not normalize/store/dispatch.
  - If new, proceed as today.
- **Non-GitHub fallback:** compute `delivery_id = sha256(provider + repo + run_id + attempt + conclusion + job_id + logs_url)` (best-effort from payload), store in same table.

### 2) Pipeline Run Idempotency (Run Key)
- **Run key:** use the already-unique `PipelineEvent.idempotency_key` as the stable run key.
- **DB changes (minimal):** add `run_key` to `fix_pipeline_runs` and make it `UNIQUE`. Also add `UNIQUE(event_id)` so we never create multiple run rows for the same failure event.
- **Store behavior:** change `FixPipelineRunStore.create_run()` to `get_or_create` by `event_id` (or run_key). If it already exists, update context/rca if empty and return existing run id.

### 3) Redis Locking (Concurrency Protection)
Use existing `RedisService.distributed_lock()`.
- **Lock key:** `pipeline:{run_key}` for fix pipeline; `context:{event_id}` for context building.
- **TTL:** 20 minutes for fix pipeline; 10 minutes for context.
- **Behavior:** if lock not acquired, task returns a “skipped_already_running” result and increments `pipeline_runs_skipped_total`.

### 4) Retry/Backoff Policy (Safe Retries Only)
- Add settings:
  - `MAX_PIPELINE_ATTEMPTS` (default 3)
  - `BASE_BACKOFF_SECONDS` (default 30)
  - `MAX_BACKOFF_SECONDS` (default 600)
  - `COOLDOWN_SECONDS` (default 900)
- Replace broad `autoretry_for=(Exception,)` on fix pipeline with explicit retry on *transient* exceptions only (DB connection errors, Redis timeouts, GitHub API transient errors).
- Implement deterministic backoff: `min(MAX_BACKOFF, BASE * 2**(attempt-1))`.
- **No retries** on definitive outcomes:
  - safety policy blocked
  - secrets detected
  - scan failures
  - validation failed (unless it was an infra timeout / transient sandbox error classified as retryable)

### 5) Backpressure / Throttling (Per-Repo)
Reuse `RedisService.check_rate_limit()`.
- **Webhook rate limit key:** `repo:{repo}:webhook:minute` with threshold (default 30/min).
- **Behavior:** if exceeded, return `200 {"status":"throttled_delayed"}` and enqueue delayed processing (Celery countdown = retry_after).
- **Concurrent pipeline cap per repo:** maintain a repo “slot” counter key `repo:{repo}:concurrency` with max N (default 2). Acquire slot before running pipeline; release in `finally`.

### 6) Failure Loop Protection (Anti-Spam)
Add fields on `fix_pipeline_runs`:
- `attempt_count` (int)
- `blocked_reason` (text)
- `last_pr_url` (text)
- `last_pr_created_at` (timestamp)

Rules:
- If `blocked_reason` set → do not run; return “blocked”.
- If `attempt_count >= MAX_PIPELINE_ATTEMPTS` → set `blocked_reason="max_attempts"`.
- If PR already created for this run (either `pr_json.status==created` or `last_pr_url` set) → skip PR creation.
- Cooldown: if last attempt was within `COOLDOWN_SECONDS`, skip and schedule delayed retry.

### 7) Side-Effect Protection (No Duplicate PRs)
- Before PR creation in orchestrator, re-check run row:
  - if `last_pr_url` exists or `pr_json.status==created` → skip
- Make PR creation idempotent in GitHub integration:
  - If GitHub returns “PR already exists” (HTTP 422), query for existing open PR by `head=owner:branch` and return it as CREATED.
  - Adjust branch creation to be retry-friendly: if branch exists remotely, checkout/reset agent branch safely and push using safe semantics.

### 8) Metrics + Logs
- Add an `ops/metrics.py` module using OpenTelemetry Metrics API (already in deps) to define counters:
  - `sre_agent_webhook_deduped_total`
  - `sre_agent_pipeline_runs_skipped_total`
  - `sre_agent_pipeline_retry_total`
  - `sre_agent_pipeline_throttled_total`
  - `sre_agent_pipeline_loop_blocked_total`
  - `sre_agent_pr_create_skipped_total`
- Emit structured logs with `delivery_id`, `run_key`, `event_id`, `run_id`, `repo`, `attempt_count`.

## D) File-by-File Change List (Planned)
### Database / Models / Migrations
- Add model: `src/sre_agent/models/webhook_deliveries.py`
- Update model: `src/sre_agent/models/fix_pipeline.py` (new columns + optional status handling)
- Alembic migration: `alembic/versions/006_phase7_reliability.py`
  - Create `webhook_deliveries` with `delivery_id UNIQUE`
  - Alter `fix_pipeline_runs` add: `run_key`, `attempt_count`, `blocked_reason`, `last_pr_url`, `last_pr_created_at`
  - Add `UNIQUE(event_id)` and `UNIQUE(run_key)`

### Webhook ingestion
- Update: `src/sre_agent/api/webhooks/github.py` to insert into `webhook_deliveries` and short-circuit duplicates.
- Apply same pattern to other webhook routers with fallback hashed delivery ids.

### Task idempotency + locks + backpressure
- Update: `src/sre_agent/tasks/dispatch.py` and `src/sre_agent/tasks/context_tasks.py` to lock per event_id.
- Update: `src/sre_agent/tasks/fix_pipeline_tasks.py` to:
  - acquire `pipeline:{run_key}` lock
  - enforce repo concurrency
  - implement retry/backoff policy and cooldown

### Stores / Orchestrator
- Update: `src/sre_agent/fix_pipeline/store.py` create_run → get_or_create (by event_id/run_key)
- Update: `src/sre_agent/fix_pipeline/orchestrator.py`:
  - enforce “PR already created” checks
  - set `last_pr_url/last_pr_created_at`
  - honor `blocked_reason`

### PR idempotency
- Update: `src/sre_agent/pr/pr_creator.py` handle 422 already-exists by fetching existing PR by head branch.
- Update: `src/sre_agent/pr/branch_manager.py` make branch creation/push retry-safe (agent branch reuse).

### Ops / Metrics
- Add: `src/sre_agent/ops/metrics.py`
- Add: `src/sre_agent/ops/retry_policy.py` (backoff + retry classification helpers)

### Tests
- Add tests covering:
  - webhook delivery dedupe uses DB uniqueness (Postgres via testcontainers)
  - fix pipeline run get_or_create is idempotent (no duplicate runs)
  - lock prevents concurrent pipeline execution (mock redis lock result + assert orchestrator called once)
  - PR idempotency under retry (mock GitHub 422 + verify existing PR returned)
  - throttling returns delayed response and does not enqueue multiple tasks

### Docs
- Add: `docs/ops.md`:
  - retry/backoff knobs
  - throttle/concurrency knobs
  - unblock procedures
  - incident playbook (webhook storm, queue backlog, scanner failures)

## E) Verification Steps (Commands + Expected Outputs)
- `poetry run ruff check .` → exit 0
- `poetry run black --check .` → unchanged
- `poetry run mypy src` → success
- `poetry run pytest` → all pass
- `docker-compose up -d --build` → api + worker healthy

## F) Risks + Mitigations
- **Race conditions on dedupe:** enforce DB unique constraints and catch IntegrityError; redis locks for worker concurrency.
- **Retry accidentally causing PR spam:** deterministic run_key + lock + DB guard (`last_pr_url`) + GitHub PR lookup on 422.
- **Over-throttling drops fixes:** use delayed enqueue + 200 response to avoid webhook sender retry storms.

## G) What NOT To Do
- Don’t rewrite into event-sourcing or introduce new workflow engines.
- Don’t retry on definitive blocks (policy/secrets/scans/validation failure).
- Don’t auto-merge.
- Don’t add heavy quota/billing/multi-tenant systems.
