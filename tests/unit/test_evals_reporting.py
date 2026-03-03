from __future__ import annotations

from evals.metrics import EvalAggregateMetrics
from evals.reporting import render_markdown_summary


def test_render_markdown_summary_table() -> None:
    md = render_markdown_summary(
        EvalAggregateMetrics(
            model="mock",
            cases=25,
            fix_success_rate=0.64,
            safe_fix_rate=0.92,
            regression_rate=0.12,
            hallucination_rate_proxy=0.08,
            classification_accuracy=0.76,
            avg_danger_score=18.4,
            avg_mttr_seconds=185.0,
        )
    )
    assert "| Model | Cases |" in md
    assert "| mock | 25 |" in md
    assert "64.0%" in md
