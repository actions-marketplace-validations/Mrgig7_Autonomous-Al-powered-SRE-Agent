from __future__ import annotations

from evals.metrics import EvalAggregateMetrics


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _seconds(x: float) -> str:
    if x < 1:
        return f"{x:.2f}s"
    if x < 60:
        return f"{x:.1f}s"
    minutes = int(x // 60)
    seconds = int(round(x - minutes * 60))
    return f"{minutes}m {seconds:02d}s"


def render_markdown_summary(metrics: EvalAggregateMetrics) -> str:
    lines: list[str] = []
    lines.append("# Offline Evals Summary")
    lines.append("")
    lines.append(
        "| Model | Cases | Fix Success | Safe Fix Rate | Regression | Hallucination Proxy | Classify Acc | Avg Danger | Avg MTTR |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(
        "| "
        + " | ".join(
            [
                metrics.model,
                str(metrics.cases),
                _pct(metrics.fix_success_rate),
                _pct(metrics.safe_fix_rate),
                _pct(metrics.regression_rate),
                _pct(metrics.hallucination_rate_proxy),
                _pct(metrics.classification_accuracy),
                f"{metrics.avg_danger_score:.1f}",
                _seconds(metrics.avg_mttr_seconds),
            ]
        )
        + " |"
    )
    lines.append("")
    return "\n".join(lines)
