from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select

from sre_agent.database import get_async_session
from sre_agent.explainability.evidence_extractor import (
    EvidenceLine,
    attach_operation_links,
    extract_evidence_lines,
)
from sre_agent.explainability.redactor import get_redactor
from sre_agent.models.events import PipelineEvent
from sre_agent.models.fix_pipeline import FixPipelineRun
from sre_agent.safety.policy_models import PolicyDecision


@dataclass(frozen=True)
class ConfidenceFactor:
    factor: str
    value: float
    weight: float
    note: str


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def compute_confidence_breakdown(
    *,
    detection_confidence: float | None,
    plan_confidence: float | None,
    validation_status: str | None,
) -> tuple[float, list[ConfidenceFactor]]:
    factors: list[ConfidenceFactor] = []
    total = 0.0
    weight_sum = 0.0

    if detection_confidence is not None:
        factors.append(
            ConfidenceFactor(
                factor="adapter_detection",
                value=detection_confidence,
                weight=0.4,
                note="Adapter detection confidence",
            )
        )
    if plan_confidence is not None:
        factors.append(
            ConfidenceFactor(
                factor="plan_confidence",
                value=plan_confidence,
                weight=0.4,
                note="FixPlan confidence",
            )
        )

    validation_value: float | None
    if validation_status == "validation_passed":
        validation_value = 1.0
        note = "Sandbox validation passed"
    elif validation_status == "validation_failed":
        validation_value = 0.0
        note = "Sandbox validation failed"
    else:
        validation_value = None
        note = "Sandbox validation not available"

    if validation_value is not None:
        factors.append(
            ConfidenceFactor(
                factor="validation",
                value=validation_value,
                weight=0.2,
                note=note,
            )
        )

    for f in factors:
        total += f.value * f.weight
        weight_sum += f.weight

    if weight_sum <= 0:
        return 0.0, []
    return max(0.0, min(1.0, total / weight_sum)), factors


def build_validation_summary(validation_json: dict[str, Any] | None) -> dict[str, Any]:
    if not validation_json:
        return {"sandbox": "skipped", "tests": "skipped", "lint": "skipped", "scans": None}
    status = str(validation_json.get("status") or "").lower()
    sandbox = "unknown"
    if status.endswith("passed"):
        sandbox = "passed"
    elif status.endswith("failed"):
        sandbox = "failed"
    elif status.endswith("error"):
        sandbox = "error"
    tests = "unknown"
    try:
        failed = int(validation_json.get("tests_failed") or 0)
        total = int(validation_json.get("tests_total") or 0)
        if total > 0:
            tests = "pass" if failed == 0 else "fail"
    except Exception:
        tests = "unknown"
    scans = validation_json.get("scans")
    return {"sandbox": sandbox, "tests": tests, "lint": "unknown", "scans": scans}


def build_timeline_from_artifact(artifact_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not artifact_json:
        return []
    timeline = artifact_json.get("timeline")
    if isinstance(timeline, list):
        return timeline
    return []


def _parse_policy(decision_json: dict[str, Any] | None) -> PolicyDecision | None:
    if not decision_json:
        return None
    try:
        return PolicyDecision.model_validate(decision_json)
    except Exception:
        return None


async def load_failure_and_latest_run(
    *, failure_id: UUID
) -> tuple[PipelineEvent | None, FixPipelineRun | None]:
    async with get_async_session() as session:
        event = (
            await session.execute(select(PipelineEvent).where(PipelineEvent.id == failure_id))
        ).scalar_one_or_none()
        if event is None:
            return None, None
        run = (
            (
                await session.execute(
                    select(FixPipelineRun)
                    .where(FixPipelineRun.event_id == failure_id)
                    .order_by(desc(FixPipelineRun.created_at))
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        return event, run


async def build_failure_explain_payload(*, failure_id: UUID) -> dict[str, Any] | None:
    redactor = get_redactor()
    event, run = await load_failure_and_latest_run(failure_id=failure_id)
    if event is None:
        return None

    context_json = run.context_json if run else None
    rca_json = run.rca_json if run else None
    plan_json = run.plan_json if run else None
    plan_policy = _parse_policy(run.plan_policy_json if run else None)
    patch_policy = _parse_policy(run.patch_policy_json if run else None)

    log_text = ""
    if isinstance(context_json, dict):
        log_content = context_json.get("log_content") or {}
        raw = (log_content or {}).get("raw_content")
        summary = context_json.get("log_summary")
        log_text = str(raw or summary or "")

    evidence: list[EvidenceLine] = (
        extract_evidence_lines(log_text, max_lines=30) if log_text else []
    )
    evidence = attach_operation_links(
        evidence, operations=(plan_json or {}).get("operations") if plan_json else None
    )

    category = None
    plan_confidence = None
    root_cause = None
    if isinstance(plan_json, dict):
        category = plan_json.get("category")
        plan_confidence = _safe_float(plan_json.get("confidence"))
        root_cause = plan_json.get("root_cause")
    if not root_cause and isinstance(rca_json, dict):
        primary = (rca_json.get("primary_hypothesis") or {}).get("description")
        if primary:
            root_cause = primary

    detection_confidence = None
    adapter = None
    if run and run.detection_json:
        adapter = run.adapter_name
        detection_confidence = _safe_float(run.detection_json.get("confidence"))

    confidence, breakdown = compute_confidence_breakdown(
        detection_confidence=detection_confidence,
        plan_confidence=plan_confidence,
        validation_status=run.status if run else None,
    )

    safety = {
        "label": (plan_policy.pr_label if plan_policy else None),
        "danger_score": (plan_policy.danger_score if plan_policy else None),
        "danger_breakdown": (
            [r.model_dump(mode="json") for r in plan_policy.danger_reasons] if plan_policy else []
        ),
        "violations": (
            [v.model_dump(mode="json") for v in plan_policy.violations] if plan_policy else []
        ),
        "patch_policy": (patch_policy.model_dump(mode="json") if patch_policy else None),
    }

    proposed_fix = {
        "plan": redactor.redact_obj(plan_json) if plan_json else None,
        "files": list((plan_json or {}).get("files") or []) if isinstance(plan_json, dict) else [],
        "diff_available": bool(run and run.patch_diff),
    }

    payload: dict[str, Any] = {
        "failure_id": str(event.id),
        "repo": event.repo,
        "summary": {
            "category": category,
            "root_cause": redactor.redact_text(str(root_cause)) if root_cause else None,
            "adapter": adapter,
            "confidence": confidence,
            "confidence_breakdown": [
                {"factor": f.factor, "value": f.value, "weight": f.weight, "note": f.note}
                for f in breakdown
            ],
        },
        "evidence": [
            {
                "idx": e.idx,
                "line": e.line,
                "tag": e.tag,
                "operation_idx": e.operation_idx,
            }
            for e in evidence
        ],
        "proposed_fix": proposed_fix,
        "safety": redactor.redact_obj(safety),
        "validation": redactor.redact_obj(
            build_validation_summary(run.validation_json if run else None)
        ),
        "run": {
            "run_id": str(run.id) if run else None,
            "status": (run.status if run else None),
            "created_at": (run.created_at.isoformat() if run else None),
            "updated_at": (run.updated_at.isoformat() if run and run.updated_at else None),
        },
        "timeline": redactor.redact_obj(
            build_timeline_from_artifact(run.artifact_json if run else None)
        ),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return payload
