from __future__ import annotations

from typing import Any

from opentelemetry import metrics

_meter = metrics.get_meter("sre_agent")


def _counter(name: str):
    try:
        return _meter.create_counter(name)
    except Exception:
        return None


_WEBHOOK_DEDUPED = _counter("sre_agent_webhook_deduped_total")
_PIPELINE_SKIPPED = _counter("sre_agent_pipeline_runs_skipped_total")
_PIPELINE_RETRY = _counter("sre_agent_pipeline_retry_total")
_PIPELINE_THROTTLED = _counter("sre_agent_pipeline_throttled_total")
_PIPELINE_LOOP_BLOCKED = _counter("sre_agent_pipeline_loop_blocked_total")
_PR_SKIPPED = _counter("sre_agent_pr_create_skipped_total")


def inc(counter_name: str, value: int = 1, attributes: dict[str, Any] | None = None) -> None:
    attrs = attributes or {}
    counter = {
        "webhook_deduped": _WEBHOOK_DEDUPED,
        "pipeline_skipped": _PIPELINE_SKIPPED,
        "pipeline_retry": _PIPELINE_RETRY,
        "pipeline_throttled": _PIPELINE_THROTTLED,
        "pipeline_loop_blocked": _PIPELINE_LOOP_BLOCKED,
        "pr_create_skipped": _PR_SKIPPED,
    }.get(counter_name)
    if counter is None:
        return
    try:
        counter.add(value, attrs)
    except Exception:
        return
