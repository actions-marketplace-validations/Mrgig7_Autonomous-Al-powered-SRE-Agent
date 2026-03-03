from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)
from prometheus_client.exposition import generate_latest
from prometheus_client.metrics import MetricWrapperBase


def _get_or_create(registry: CollectorRegistry, metric: MetricWrapperBase) -> MetricWrapperBase:
    return metric


@dataclass(frozen=True)
class PrometheusMetrics:
    registry: CollectorRegistry
    http_requests_total: Counter
    http_request_duration_seconds: Histogram
    pipeline_runs_total: Counter
    pipeline_stage_duration_seconds: Histogram
    pipeline_retry_total: Counter
    pipeline_loop_blocked_total: Counter
    pipeline_throttled_total: Counter
    pr_created_total: Counter
    policy_violations_total: Counter
    danger_score_bucket: Counter
    scan_findings_total: Counter
    scan_fail_total: Counter
    celery_tasks_total: Counter
    queue_depth: Gauge
    oauth_login_success_total: Counter
    oauth_login_failure_total: Counter
    repo_fetch_latency_ms: Histogram
    integration_install_success_total: Counter
    repo_config_load_success_total: Counter
    repo_config_load_failure_total: Counter
    repo_config_missing_total: Counter
    build_log_ingestion_success_total: Counter
    build_log_ingestion_failure_total: Counter
    critic_decision_total: Counter
    manual_approval_total: Counter
    auto_merge_total: Counter
    retry_signature_blocked_total: Counter
    consensus_rejection_total: Counter
    consensus_candidate_total: Counter
    consensus_agreement_rate: Histogram


_REGISTRY = CollectorRegistry(auto_describe=True)

METRICS = PrometheusMetrics(
    registry=_REGISTRY,
    http_requests_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_http_requests_total",
            "Total HTTP requests by method/route/status",
            labelnames=("method", "route", "status"),
            registry=_REGISTRY,
        ),
    ),
    http_request_duration_seconds=_get_or_create(
        _REGISTRY,
        Histogram(
            "sre_agent_http_request_duration_seconds",
            "HTTP request duration in seconds by route/method",
            labelnames=("route", "method"),
            registry=_REGISTRY,
        ),
    ),
    pipeline_runs_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_pipeline_runs_total",
            "Total pipeline runs by outcome",
            labelnames=("outcome",),
            registry=_REGISTRY,
        ),
    ),
    pipeline_stage_duration_seconds=_get_or_create(
        _REGISTRY,
        Histogram(
            "sre_agent_pipeline_stage_duration_seconds",
            "Pipeline stage duration in seconds by stage",
            labelnames=("stage",),
            registry=_REGISTRY,
            buckets=(
                0.05,
                0.1,
                0.25,
                0.5,
                1.0,
                2.5,
                5.0,
                10.0,
                20.0,
                30.0,
                60.0,
                120.0,
                300.0,
                600.0,
            ),
        ),
    ),
    pipeline_retry_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_pipeline_retry_total",
            "Total pipeline retries by reason",
            labelnames=("reason",),
            registry=_REGISTRY,
        ),
    ),
    pipeline_loop_blocked_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_pipeline_loop_blocked_total",
            "Total pipeline loop blocks by reason",
            labelnames=("reason",),
            registry=_REGISTRY,
        ),
    ),
    pipeline_throttled_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_pipeline_throttled_total",
            "Total pipeline throttles by scope",
            labelnames=("scope",),
            registry=_REGISTRY,
        ),
    ),
    pr_created_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_pr_created_total",
            "Total PRs created by label",
            labelnames=("label",),
            registry=_REGISTRY,
        ),
    ),
    policy_violations_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_policy_violations_total",
            "Total safety policy violations by type",
            labelnames=("type",),
            registry=_REGISTRY,
        ),
    ),
    danger_score_bucket=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_danger_score_bucket",
            "Danger score distribution bucketed by ranges",
            labelnames=("bucket",),
            registry=_REGISTRY,
        ),
    ),
    scan_findings_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_scan_findings_total",
            "Total scan findings by scanner and severity",
            labelnames=("scanner", "severity"),
            registry=_REGISTRY,
        ),
    ),
    scan_fail_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_scan_fail_total",
            "Total scan failures by scanner and reason",
            labelnames=("scanner", "reason"),
            registry=_REGISTRY,
        ),
    ),
    celery_tasks_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_celery_tasks_total",
            "Total Celery task executions by task and status",
            labelnames=("task", "status"),
            registry=_REGISTRY,
        ),
    ),
    queue_depth=_get_or_create(
        _REGISTRY,
        Gauge(
            "sre_agent_queue_depth",
            "Queue depth as observed from broker backend",
            labelnames=("queue",),
            registry=_REGISTRY,
        ),
    ),
    oauth_login_success_total=_get_or_create(
        _REGISTRY,
        Counter(
            "oauth_login_success_total",
            "Total successful OAuth login completions",
            labelnames=("provider",),
            registry=_REGISTRY,
        ),
    ),
    oauth_login_failure_total=_get_or_create(
        _REGISTRY,
        Counter(
            "oauth_login_failure_total",
            "Total failed OAuth login attempts",
            labelnames=("provider", "reason"),
            registry=_REGISTRY,
        ),
    ),
    repo_fetch_latency_ms=_get_or_create(
        _REGISTRY,
        Histogram(
            "repo_fetch_latency_ms",
            "Latency for user repository fetch calls in milliseconds",
            buckets=(25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
            registry=_REGISTRY,
        ),
    ),
    integration_install_success_total=_get_or_create(
        _REGISTRY,
        Counter(
            "integration_install_success_total",
            "Total successful GitHub App installation confirmations",
            registry=_REGISTRY,
        ),
    ),
    repo_config_load_success_total=_get_or_create(
        _REGISTRY,
        Counter(
            "repo_config_load_success_total",
            "Total successful repository config file loads",
            registry=_REGISTRY,
        ),
    ),
    repo_config_load_failure_total=_get_or_create(
        _REGISTRY,
        Counter(
            "repo_config_load_failure_total",
            "Total failed repository config file loads",
            registry=_REGISTRY,
        ),
    ),
    repo_config_missing_total=_get_or_create(
        _REGISTRY,
        Counter(
            "repo_config_missing_total",
            "Total repository config lookups where .sre-agent.yaml was not found",
            registry=_REGISTRY,
        ),
    ),
    build_log_ingestion_success_total=_get_or_create(
        _REGISTRY,
        Counter(
            "build_log_ingestion_success_total",
            "Total successful build log ingestion operations",
            registry=_REGISTRY,
        ),
    ),
    build_log_ingestion_failure_total=_get_or_create(
        _REGISTRY,
        Counter(
            "build_log_ingestion_failure_total",
            "Total failed build log ingestion operations",
            registry=_REGISTRY,
        ),
    ),
    critic_decision_total=_get_or_create(
        _REGISTRY,
        Counter(
            "critic_decision_total",
            "Total critic decisions by outcome",
            labelnames=("outcome",),
            registry=_REGISTRY,
        ),
    ),
    manual_approval_total=_get_or_create(
        _REGISTRY,
        Counter(
            "manual_approval_total",
            "Total manual approval actions by outcome",
            labelnames=("outcome",),
            registry=_REGISTRY,
        ),
    ),
    auto_merge_total=_get_or_create(
        _REGISTRY,
        Counter(
            "auto_merge_total",
            "Total auto-merge attempts by outcome",
            labelnames=("outcome",),
            registry=_REGISTRY,
        ),
    ),
    retry_signature_blocked_total=_get_or_create(
        _REGISTRY,
        Counter(
            "retry_signature_blocked_total",
            "Total pipeline blocks due to retry signature limits",
            registry=_REGISTRY,
        ),
    ),
    consensus_rejection_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_consensus_rejection_total",
            "Total consensus rejections by reason",
            labelnames=("reason",),
            registry=_REGISTRY,
        ),
    ),
    consensus_candidate_total=_get_or_create(
        _REGISTRY,
        Counter(
            "sre_agent_consensus_candidate_total",
            "Total consensus candidate outcomes by agent",
            labelnames=("agent", "outcome"),
            registry=_REGISTRY,
        ),
    ),
    consensus_agreement_rate=_get_or_create(
        _REGISTRY,
        Histogram(
            "sre_agent_consensus_agreement_rate",
            "Consensus agreement rate distribution",
            registry=_REGISTRY,
            buckets=(0.0, 0.25, 0.5, 0.67, 0.75, 0.9, 1.0),
        ),
    ),
)


def render_prometheus() -> tuple[bytes, str]:
    return generate_latest(METRICS.registry), CONTENT_TYPE_LATEST


def observe_http_request(*, method: str, route: str, status: str, duration_seconds: float) -> None:
    METRICS.http_requests_total.labels(method=method, route=route, status=status).inc()
    METRICS.http_request_duration_seconds.labels(route=route, method=method).observe(
        duration_seconds
    )


def bucket_danger_score(score: int) -> str:
    if score < 0:
        return "lt_0"
    if score <= 10:
        return "0_10"
    if score <= 20:
        return "10_20"
    if score <= 40:
        return "20_40"
    if score <= 60:
        return "40_60"
    if score <= 80:
        return "60_80"
    if score <= 100:
        return "80_100"
    return "100_plus"


def start_worker_metrics_server(*, port: int) -> None:
    start_http_server(port, registry=METRICS.registry)


def record_oauth_login_success(*, provider: str) -> None:
    METRICS.oauth_login_success_total.labels(provider=provider).inc()


def record_oauth_login_failure(*, provider: str, reason: str) -> None:
    METRICS.oauth_login_failure_total.labels(provider=provider, reason=reason).inc()


def observe_repo_fetch_latency_ms(*, latency_ms: float) -> None:
    METRICS.repo_fetch_latency_ms.observe(max(0.0, latency_ms))


def record_integration_install_success() -> None:
    METRICS.integration_install_success_total.inc()


def record_repo_config_load_success() -> None:
    METRICS.repo_config_load_success_total.inc()


def record_repo_config_load_failure() -> None:
    METRICS.repo_config_load_failure_total.inc()


def record_repo_config_missing() -> None:
    METRICS.repo_config_missing_total.inc()


def record_build_log_ingestion_success() -> None:
    METRICS.build_log_ingestion_success_total.inc()


def record_build_log_ingestion_failure() -> None:
    METRICS.build_log_ingestion_failure_total.inc()


def record_critic_decision(*, outcome: str) -> None:
    METRICS.critic_decision_total.labels(outcome=outcome).inc()


def record_manual_approval(*, outcome: str) -> None:
    METRICS.manual_approval_total.labels(outcome=outcome).inc()


def record_auto_merge(*, outcome: str) -> None:
    METRICS.auto_merge_total.labels(outcome=outcome).inc()


def record_retry_signature_blocked() -> None:
    METRICS.retry_signature_blocked_total.inc()


def record_consensus_decision(*, state: str) -> None:
    if state.startswith("rejected_"):
        METRICS.consensus_rejection_total.labels(reason=state).inc()


def record_consensus_candidate(*, agent: str, outcome: str) -> None:
    METRICS.consensus_candidate_total.labels(agent=agent, outcome=outcome).inc()


def observe_consensus_agreement(*, rate: float) -> None:
    METRICS.consensus_agreement_rate.observe(max(0.0, min(1.0, rate)))
