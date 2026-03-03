from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sre_agent.safety.policy_models import SafetyPolicy
from sre_agent.schemas.scans import ScanSummary
from sre_agent.schemas.validation import ValidationResult


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


_DEFAULT_REDACT_PATTERNS = [re.compile(p) for p in SafetyPolicy().secrets.forbidden_patterns]


def redact_text(value: str) -> str:
    redacted = value
    for pat in _DEFAULT_REDACT_PATTERNS:
        redacted = pat.sub("[REDACTED]", redacted)
    return redacted


def redact_obj(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj


class ProvenanceTimestamps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at: str
    finished_at: str


class ProvenancePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    danger_score: int = 0
    label: str = "needs-review"
    violations: list[dict] = Field(default_factory=list)


class DiffStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files_changed: int = 0
    lines_added: int = 0
    lines_deleted: int = 0


class ProvenanceValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    tests_passed: int = 0
    tests_failed: int = 0
    tests_total: int = 0
    error_message: str | None = None
    execution_time_seconds: float | None = None


class ProvenanceAdapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    repo_language: str | None = None
    detected_category: str | None = None
    confidence: float | None = None
    evidence_lines: list[str] = Field(default_factory=list)


class ProvenanceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    failure_id: UUID
    repo: str
    timestamps: ProvenanceTimestamps
    status: str
    error_message: str | None = None
    adapter: ProvenanceAdapter | None = None
    plan: dict[str, Any] | None = None
    policy: ProvenancePolicy | None = None
    diff_stats: DiffStats | None = None
    scans: ScanSummary | None = None
    validation: ProvenanceValidation | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)


def _policy_from_json(policy_json: dict[str, Any] | None) -> ProvenancePolicy | None:
    if not policy_json:
        return None
    return ProvenancePolicy(
        allowed=bool(policy_json.get("allowed", False)),
        danger_score=int(policy_json.get("danger_score", 0) or 0),
        label=str(policy_json.get("pr_label") or "needs-review"),
        violations=redact_obj(policy_json.get("violations") or []),
    )


def _diff_stats_from_patch_stats_json(stats_json: dict[str, Any] | None) -> DiffStats | None:
    if not stats_json:
        return None
    return DiffStats(
        files_changed=int(stats_json.get("total_files") or 0),
        lines_added=int(stats_json.get("lines_added") or 0),
        lines_deleted=int(stats_json.get("lines_removed") or 0),
    )


def _validation_from_json(validation_json: dict[str, Any] | None) -> ProvenanceValidation | None:
    if not validation_json:
        return None
    v = ValidationResult.model_validate(validation_json)
    return ProvenanceValidation(
        status=v.status.value,
        tests_passed=v.tests_passed,
        tests_failed=v.tests_failed,
        tests_total=v.tests_total,
        error_message=redact_text(v.error_message) if v.error_message else None,
        execution_time_seconds=v.execution_time_seconds,
    )


def _scans_from_validation_json(validation_json: dict[str, Any] | None) -> ScanSummary | None:
    if not validation_json:
        return None
    try:
        v = ValidationResult.model_validate(validation_json)
    except Exception:
        return None
    return v.scans


def build_provenance_artifact(
    *,
    run_id: UUID,
    failure_id: UUID,
    repo: str,
    status: str,
    started_at: datetime | None,
    error_message: str | None,
    plan_json: dict[str, Any] | None,
    plan_policy_json: dict[str, Any] | None,
    patch_stats_json: dict[str, Any] | None,
    patch_policy_json: dict[str, Any] | None,
    validation_json: dict[str, Any] | None,
    adapter_name: str | None = None,
    detection_json: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    timeline: list[dict[str, Any]] | None = None,
) -> ProvenanceArtifact:
    policy_json = patch_policy_json or plan_policy_json
    scans = _scans_from_validation_json(validation_json)
    validation = _validation_from_json(validation_json)

    started_iso = (
        (started_at.replace(tzinfo=UTC).isoformat() if started_at else _utc_now_iso())
        if started_at
        else _utc_now_iso()
    )
    finished_iso = _utc_now_iso()

    adapter: ProvenanceAdapter | None = None
    if adapter_name:
        adapter = ProvenanceAdapter(
            name=adapter_name,
            repo_language=(str(detection_json.get("repo_language")) if detection_json else None),
            detected_category=(str(detection_json.get("category")) if detection_json else None),
            confidence=(
                float(detection_json.get("confidence"))
                if detection_json and detection_json.get("confidence") is not None
                else None
            ),
            evidence_lines=(
                [
                    redact_text(str(line_value))
                    for line_value in (detection_json.get("evidence_lines") or [])
                ]
                if detection_json
                else []
            ),
        )

    return ProvenanceArtifact(
        run_id=run_id,
        failure_id=failure_id,
        repo=repo,
        status=status,
        error_message=redact_text(error_message) if error_message else None,
        timestamps=ProvenanceTimestamps(started_at=started_iso, finished_at=finished_iso),
        adapter=adapter,
        plan=redact_obj(plan_json) if plan_json else None,
        policy=_policy_from_json(policy_json),
        diff_stats=_diff_stats_from_patch_stats_json(patch_stats_json),
        scans=scans,
        validation=validation,
        evidence=redact_obj(evidence) if evidence else [],
        timeline=redact_obj(timeline) if timeline else [],
    )
