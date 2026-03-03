## Objective

Deliver production-grade, locally runnable observability: Prometheus metrics (/metrics), Grafana dashboards + provisioning, OTEL tracing end-to-end (API → Celery → pipeline stages), and correlated JSON logs with consistent identifiers.

## Current State (Repo Facts)

* Logging is structured JSON and supports a `correlation_id` ContextVar via [logging.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/core/logging.py#L16-L77), but does not include `trace_id/span_id`.

* Minimal OTEL Metrics counters exist via [ops/metrics.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/ops/metrics.py#L1-L40), but there’s no OTEL SDK/exporter configured.

* No tracing provider/exporter/instrumentation is wired in [main.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/main.py#L42-L80).

* No Prometheus `/metrics` endpoint exists; `prometheus_client` is not in [pyproject.toml](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/pyproject.toml#L9-L31).

* `docker-compose.yml` currently has Postgres/Redis/API/Worker but no Prometheus/Grafana/OTEL/Tempo: [docker-compose.yml](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/docker-compose.yml#L8-L143).

## Deliverables (Phase 8 Checklist)

* [x] Add Prometheus metrics endpoint: GET /metrics

* [ ] Implement core metrics with low-cardinality labels

* [ ] Add Grafana dashboards JSON + provisioning

* [ ] Add OpenTelemetry tracing across ingestion → pipeline → scans → validation → PR

* [ ] Propagate trace context across Celery

* [ ] Correlated logging with delivery\_id/run\_key/failure\_id/run\_id + trace\_id/span\_id

* [ ] Add docker-compose observability stack (Prometheus, Grafana, OTEL Collector, Tempo)

* [ ] Add minimal alert rules

* [ ] Add docs + verification steps

* [ ] Add minimal tests

## Design Decisions

* **Tracing backend:** Tempo (single-binary, lightweight) + OTEL Collector.

* **Metrics:**

  * Add **Prometheus-native** `/metrics` endpoint in the API using `prometheus_client` (required stop condition).

  * Export **worker-side metrics and OTEL-derived metrics** via OTEL Collector’s Prometheus exporter (Prometheus will scrape both API and collector).

* **Cardinality policy:** never label by raw repo name/PR URL; route labels use FastAPI route templates (e.g., `/api/v1/runs/{run_id}/diff`).

## Metrics Spec (Names + Labels + Rationale)

**HTTP** (from middleware)

* `sre_agent_http_requests_total{method,route,status}`

* `sre_agent_http_request_duration_seconds_bucket{route,method}`

  * Rationale: route templates keep label cardinality bounded.

**Pipeline** (from tasks + orchestrator)

* `sre_agent_pipeline_runs_total{outcome}` where outcome ∈ {success, fail, blocked, skipped}

* `sre_agent_pipeline_stage_duration_seconds_bucket{stage}` stage ∈ {ingest, plan, policy\_plan, patch, policy\_patch, scans, validate, pr\_create, persist\_artifact}

* `sre_agent_pipeline_retry_total{reason}` reason ∈ {cooldown, repo\_throttled, transient\_error, celery\_retry}

* `sre_agent_pipeline_loop_blocked_total{reason}` reason ∈ {max\_attempts, blocked\_reason\_other}

* `sre_agent_pipeline_throttled_total{scope}` scope ∈ {repo, org, webhook, repo\_concurrency}

* `sre_agent_pr_created_total{label}` label ∈ {safe, needs-review, unknown}

  * Rationale: bounded enumerations; no per-repo labels.

**Safety**

* `sre_agent_policy_violations_total{type}` type derived from violation code family (bounded set)

* `sre_agent_danger_score_bucket{bucket}` bucket ∈ {0\_10,10\_20,20\_40,40\_60,60\_80,80\_100,100\_plus}

**Security Scans**

* `sre_agent_scan_findings_total{scanner,severity}` scanner ∈ {gitleaks,trivy,sbom}, severity ∈ {LOW,MEDIUM,HIGH,CRITICAL,UNKNOWN}

* `sre_agent_scan_fail_total{scanner,reason}` reason bounded to {timeout,error,policy\_block,unknown}

**Queue/Worker** (minimal)

* `sre_agent_celery_tasks_total{task,status}` task ∈ {process\_pipeline\_event,build\_failure\_context,run\_fix\_pipeline,...}, status ∈ {started,success,fail,retry}

* Optional: `sre_agent_queue_depth{queue}` only if we can read Redis safely without overfitting to broker internals.

## Tracing Spec (Spans + Propagation)

**Initialization**

* Add `src/sre_agent/observability/tracing.py` to configure OTEL TracerProvider + OTLP exporter to collector.

* Instrument FastAPI using installed `opentelemetry-instrumentation-fastapi`.

**Span Names** (as requested)

* `ingest_webhook` (webhook handler)

* `store_event` (DB store)

* `enqueue_pipeline` (Celery dispatch)

* `generate_plan`

* `policy_check_plan`

* `generate_patch`

* `policy_check_patch`

* `run_scans`

* `sandbox_validate`

* `create_pr`

* `persist_artifact`

**Span Attributes** (safe + consistent)

* `delivery_id`, `run_key`, `failure_id`, `run_id` (strings)

* `language`, `category`, `pr_label`, `outcome`

* Never attach raw payloads, tokens, or PR URLs.

**Context propagation across Celery**

* Inject `traceparent`/`tracestate` into Celery task headers at dispatch.

* Extract headers inside worker task entry before creating task spans.

## Correlated Logging

* Extend [logging.py](file:///f:/Dev_Env/Autonomous%20AI-powered%20SRE%20Agent/src/sre_agent/core/logging.py) filter/formatter to include `trace_id` and `span_id` when a span is active.

* Ensure Celery task entry sets `correlation_id_ctx` from the `correlation_id` argument.

* Standardize consistent keys in logs: `delivery_id`, `run_key`, `failure_id`, `run_id`, `trace_id`, `span_id`.

## Docker Compose Observability Stack

Add services to docker-compose:

* `otel-collector` (OTLP receiver; Prometheus exporter)

* `tempo` (trace storage + query)

* `prometheus` (scrapes API `/metrics` and collector exporter)

* `grafana` (provision datasources + dashboards)

Add repo configs:

* `observability/prometheus/prometheus.yml`

* `observability/prometheus/alerts.yml`

* `observability/otel-collector/config.yml`

* `observability/grafana/provisioning/datasources/datasource.yml`

* `observability/grafana/provisioning/dashboards/dashboards.yml`

* `observability/grafana/dashboards/*.json`

## Dashboards (Minimum 4)

1. **API Health**

* RPS, error rate, p95 latency by route

1. **Pipeline Overview**

* success/fail/blocked/skipped rates, stage duration heatmap/quantiles, retries, loop blocks

1. **Safety & Risk**

* danger score distribution, policy violations over time, safe vs needs-review ratio

1. **Security**

* gitleaks findings trend, trivy HIGH/CRITICAL trend, scan failures

1. (Optional) **Queue/Workers**

* Celery task throughput + failures + retries

## File-by-File Change List (Planned)

**New backend modules**

* `src/sre_agent/observability/metrics.py`

* `src/sre_agent/observability/middleware.py`

* `src/sre_agent/observability/tracing.py`

* `src/sre_agent/api/metrics.py` (exposes `GET /metrics` without `/api/v1` prefix)

**Modified backend**

* `src/sre_agent/main.py` (init tracing + add middleware + mount metrics router)

* `src/sre_agent/core/logging.py` (add trace\_id/span\_id; strengthen correlation fields)

* Celery dispatch/call sites: `src/sre_agent/api/webhooks/*.py`, `src/sre_agent/tasks/dispatch.py`, `src/sre_agent/tasks/context_tasks.py`, `src/sre_agent/tasks/fix_pipeline_tasks.py` (inject/extract trace context + create spans + increment metrics)

* Pipeline stage instrumentation: `src/sre_agent/fix_pipeline/orchestrator.py` (wrap stages with spans + stage duration histograms + success/fail outcome metrics)

* `src/sre_agent/ops/metrics.py` (either redirect to Prometheus metrics or keep as compatibility wrapper but ensure export works)

**Dependencies**

* Add `prometheus_client` to `pyproject.toml`.

* Add missing OTEL exporters/instrumentations if needed (e.g., `opentelemetry-exporter-otlp`).

**Observability configs**

* `observability/**` as described above.

**Docs**

* `docs/observability.md`

* Update `README.md` to link to observability docs.

**Tests**

* `tests/api/test_metrics_endpoint.py` (GET /metrics returns Prometheus text)

* `tests/unit/test_observability_tracing_init.py` (init doesn’t crash without collector)

* `tests/unit/test_trace_propagation.py` (inject/extract helpers don’t crash)

## Verification Steps (Commands + Expected Outputs)

1. `docker-compose up -d --build`

* Expect new services up: prometheus, grafana, otel-collector, tempo

1. `curl http://localhost:8000/metrics | head`

* Expect lines like:

  * `sre_agent_http_requests_total{method="GET",route="/health",status="200"} 1`

1. Prometheus targets page

* `http://localhost:9090/targets` shows API and otel-collector scrape targets UP

1. Grafana

* `http://localhost:3001` (or chosen port) login `admin/admin`

* Dashboards auto-loaded

1. Trigger sample webhook/pipeline

* Hit webhook endpoint and/or run a controlled failure

* Confirm pipeline counters and stage durations change

1. Traces

* In Grafana Explore → Tempo, search by `run_id` or trace list

* Expect spans `ingest_webhook → enqueue_pipeline → ... → create_pr`

1. `poetry run pytest`

* Expect all tests pass

## Risks + Mitigations

* **High cardinality explosion**: route templates only; forbid repo labels; only bounded enums.

* **Collector unavailable**: tracing init must be non-fatal; exporters should degrade without crashing.

* **Secret leakage**: never attach raw payloads/diffs/tokens to span attributes; keep only IDs.

* **Perf overhead**: sampling configurable; histograms limited; avoid expensive per-request label computation.

## What NOT To Do

* Don’t label metrics by repo full name, PR URL, or commit SHA.

* Don’t build a custom observability framework.

* Don’t add Loki/log storage unless already present.

* Don’t emit raw webhook payloads into traces/logs.

If you confirm this plan, I’ll implement Phase 8 and finish with the required A–I report plus verified commands and sample outputs.
