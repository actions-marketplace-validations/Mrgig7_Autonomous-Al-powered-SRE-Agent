# Phase 2 Repo Integration

This document captures the GitHub-first Phase 2 behavior now enforced by the API and worker pipeline.

## Runtime Flow

1. `POST /webhooks/github` receives a verified webhook payload.
2. The handler filters for supported failure events (`workflow_job`, `workflow_run`).
3. Repository onboarding is validated against `github_app_installations`.
4. Optional installation id from payload is checked against persisted installation metadata.
5. `.sre-agent.yaml` is fetched from the repository root and merged with onboarding defaults.
6. The merged config is attached to payload metadata at `raw_payload._sre_agent`.
7. Event is normalized, stored idempotently, and queued through Celery.
8. Context stage ingests logs via deterministic GitHub log APIs (`job` first, then `run` fallback).

## Repository Config

File path: `.sre-agent.yaml`

Supported keys:

```yaml
automation_mode: suggest   # suggest | auto_pr | auto_merge
protected_paths:
  - infra/**
  - payments/**
retry_limit: 3
```

Precedence:

1. Repo file values (if valid)
2. Installation defaults from onboarding metadata
3. Safe defaults (`protected_paths=[]`, `retry_limit=3`)

Failure behavior:

- Missing file: processing continues with defaults.
- Invalid file: processing continues with defaults.
- GitHub fetch error: processing continues with defaults.

## Metrics Added

- `repo_config_load_success_total`
- `repo_config_load_failure_total`
- `repo_config_missing_total`
- `build_log_ingestion_success_total`
- `build_log_ingestion_failure_total`
