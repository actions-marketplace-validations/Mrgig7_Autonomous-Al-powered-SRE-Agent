# Observability (Phase 8)

This repository ships a local, production-shaped observability stack:

- Prometheus for metrics
- Grafana for dashboards + trace UI
- Tempo for trace storage
- OpenTelemetry Collector for OTLP ingestion

## Quick Start (Local)

Start everything:

```bash
docker-compose up -d --build
```

Open:

- API: http://localhost:8000
- Metrics endpoint: http://localhost:8000/metrics
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001 (admin/admin)
- Tempo: http://localhost:3200

## Verify Metrics

Hit the API a couple times:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/metrics | head
```

Expected metric families in output:

- `sre_agent_http_requests_total{method,route,status}`
- `sre_agent_http_request_duration_seconds_bucket{route,method,le}`
- `sre_agent_pipeline_runs_total{outcome}`

Prometheus scrape status:

- http://localhost:9090/targets

## Verify Dashboards

Grafana loads dashboards automatically via provisioning:

1. Open Grafana: http://localhost:3001
2. Login: admin / admin
3. Navigate to folder: `SRE Agent`

Dashboards:

- API Health
- Pipeline Overview
- Safety & Risk
- Security

## Verify Traces (End-to-End)

Tracing exports via OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (docker-compose sets it for API + worker).

To view traces:

1. Open Grafana Explore
2. Select datasource: `Tempo`
3. Search by Service Name (e.g., `sre-agent-api` or `sre-agent-worker`)

Required span names appear during a run:

- `store_event`, `enqueue_pipeline`
- `generate_plan`, `policy_check_plan`
- `generate_patch`, `policy_check_patch`
- `run_scans`, `sandbox_validate`
- `create_pr`, `persist_artifact`

## Correlation IDs (Logs ↔ Metrics ↔ Traces)

Structured JSON logs include correlation keys:

- `delivery_id`
- `run_key`
- `failure_id`
- `run_id`
- `trace_id` / `span_id` (when tracing is active)

How to use this during incident/debugging:

1. Find a failed run in the dashboard/artifact store and grab `run_id` or `failure_id`.
2. Use Grafana Tempo trace search to locate the trace.
3. Use `trace_id` (from logs) to jump directly to the trace and inspect spans.
4. Use the same IDs to filter logs and confirm pipeline stages and blockers.
