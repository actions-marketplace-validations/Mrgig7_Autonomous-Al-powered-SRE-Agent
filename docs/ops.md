# Phase 7/8: Reliability and Operations

## Reliability Guarantees

- Webhook ingestion is idempotent; duplicate deliveries are ignored.
- Pipeline runs are idempotent per run key.
- Redis locks prevent parallel execution of the same run.
- Retries are bounded with exponential backoff and cooldowns.
- Loop protection blocks repeated PR churn after max attempts.

## Operational Configuration

### Webhook Pressure Controls

- `REPO_WEBHOOK_RATE_LIMIT_PER_MINUTE` (default `30`): per-repo webhook intake cap.

### Pipeline Concurrency

- `REPO_PIPELINE_CONCURRENCY_LIMIT` (default `2`): max active runs per repo.
- `REPO_PIPELINE_CONCURRENCY_TTL_SECONDS` (default `1200`): concurrency slot TTL.

### Retry and Cooldown

- `MAX_PIPELINE_ATTEMPTS` (default `3`): hard attempt cap before blocking.
- `BASE_BACKOFF_SECONDS` (default `30`): first retry delay.
- `MAX_BACKOFF_SECONDS` (default `600`): retry delay ceiling.
- `COOLDOWN_SECONDS` (default `900`): minimum spacing between attempts.

## Key Metrics

- `sre_agent_webhook_deduped_total`
- `sre_agent_pipeline_runs_total`
- `sre_agent_pipeline_retry_total`
- `sre_agent_pipeline_throttled_total`
- `sre_agent_pipeline_loop_blocked_total`
- `sre_agent_pr_create_skipped_total`
- `sre_agent_policy_violations_total`
- `sre_agent_celery_tasks_total`

## Dashboard Event Stream

The dashboard SSE endpoint (`/api/v1/dashboard/stream`) subscribes to `dashboard_events`.
Pipeline stages publish best-effort events for:

- `ingest`
- `context`
- `rca`
- `fix_pipeline`
- `adapter_select`
- `plan`
- `policy_plan`
- `clone`
- `patch`
- `policy_patch`
- `validate`
- `pr_create`
- `pipeline`

## Incident Playbook

### Webhook storm

- Temporarily raise `REPO_WEBHOOK_RATE_LIMIT_PER_MINUTE`.
- Confirm delayed enqueue behavior in logs and metrics.
- Validate duplicate delivery suppression is active.

### Queue backlog

- Check Redis health and queue depth metrics.
- Inspect per-repo concurrency limits.
- Raise `REPO_PIPELINE_CONCURRENCY_LIMIT` cautiously if safe.

### Repeated pipeline blocking

- Inspect `blocked_reason`, `attempt_count`, and policy violations.
- Review danger score and guardrail rejection reasons.
- Verify retry/cooldown values are not too aggressive.

### Scanner failures

- Treat repeated scanner errors as blocking signals.
- Verify scanner environment, binaries, and timeout thresholds.
- Review vulnerability threshold settings (`FAIL_ON_VULN_SEVERITY`).

