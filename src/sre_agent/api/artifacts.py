from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from sre_agent.artifacts.provenance import ProvenanceArtifact
from sre_agent.auth.jwt_handler import TokenPayload
from sre_agent.auth.permissions import Permission, require_permission
from sre_agent.explainability.redactor import get_redactor
from sre_agent.fix_pipeline.store import FixPipelineRunStore
from sre_agent.models.fix_pipeline import FixPipelineRunStatus
from sre_agent.observability.metrics import record_manual_approval
from sre_agent.schemas.explainability import RunDiffResponse, RunTimelineResponse, TimelineStepOut
from sre_agent.tasks.fix_pipeline_tasks import approve_run_and_create_pr

router = APIRouter(prefix="/runs", tags=["Artifacts"])


@router.get("/{run_id}/artifact")
async def get_run_artifact(
    run_id: UUID,
    _: Any = Depends(require_permission(Permission.VIEW_DASHBOARD)),
) -> dict:
    store = FixPipelineRunStore()
    run = await store.get_run(run_id)
    if run is None or not run.artifact_json:
        raise HTTPException(status_code=404, detail="Artifact not found")
    redactor = get_redactor()
    redacted = redactor.redact_obj(run.artifact_json)
    return ProvenanceArtifact.model_validate(redacted).model_dump(mode="json")


@router.get("/{run_id}/diff", response_model=RunDiffResponse)
async def get_run_diff(
    run_id: UUID,
    _: Any = Depends(require_permission(Permission.VIEW_DASHBOARD)),
) -> dict:
    store = FixPipelineRunStore()
    run = await store.get_run(run_id)
    if run is None or not run.patch_diff:
        raise HTTPException(status_code=404, detail="Diff not found")
    redactor = get_redactor()
    return RunDiffResponse(
        run_id=run_id,
        diff_text=redactor.redact_text(run.patch_diff),
        stats=run.patch_stats_json,
        redacted=True,
    ).model_dump(mode="json")


@router.get("/{run_id}/timeline", response_model=RunTimelineResponse)
async def get_run_timeline(
    run_id: UUID,
    _: Any = Depends(require_permission(Permission.VIEW_DASHBOARD)),
) -> dict:
    store = FixPipelineRunStore()
    run = await store.get_run(run_id)
    if run is None or not run.artifact_json:
        raise HTTPException(status_code=404, detail="Timeline not found")
    timeline = run.artifact_json.get("timeline")
    if not isinstance(timeline, list):
        timeline = []
    parsed: list[TimelineStepOut] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        parsed.append(TimelineStepOut(**item))
    return RunTimelineResponse(run_id=run_id, timeline=parsed).model_dump(mode="json")


@router.post("/{run_id}/approve-pr")
async def approve_run_pr(
    run_id: UUID,
    user: TokenPayload = Depends(require_permission(Permission.APPROVE_FIX, Permission.CREATE_PR)),
) -> dict:
    """Approve an awaiting run and enqueue PR creation."""
    store = FixPipelineRunStore()
    run = await store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != FixPipelineRunStatus.AWAITING_APPROVAL.value:
        raise HTTPException(
            status_code=409,
            detail=f"Run status must be '{FixPipelineRunStatus.AWAITING_APPROVAL.value}'",
        )

    result = approve_run_and_create_pr.apply_async(
        kwargs={
            "run_id": str(run_id),
            "approved_by": str(user.user_id),
            "correlation_id": str(user.user_id),
        }
    )
    record_manual_approval(outcome="queued")
    return {
        "status": "accepted",
        "run_id": str(run_id),
        "task_id": result.id,
    }
