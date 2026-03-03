from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from sre_agent.auth.permissions import Permission, require_permission
from sre_agent.explainability.redactor import get_redactor
from sre_agent.fix_pipeline.store import FixPipelineRunStore

router = APIRouter(tags=["Consensus"])


def _build_response(run: Any) -> dict:
    redactor = get_redactor()
    return {
        "run_id": str(run.id),
        "failure_id": str(run.event_id),
        "consensus_state": run.consensus_state,
        "issue_graph": redactor.redact_obj(run.issue_graph_json or {}),
        "consensus": redactor.redact_obj(run.consensus_json or {}),
        "shadow": redactor.redact_obj(run.consensus_shadow_diff_json or {}),
    }


@router.get("/runs/{run_id}/consensus")
async def get_run_consensus(
    run_id: UUID,
    _: Any = Depends(require_permission(Permission.VIEW_FAILURES)),
) -> dict:
    store = FixPipelineRunStore()
    run = await store.get_run(run_id)
    if run is None or not run.consensus_json:
        raise HTTPException(status_code=404, detail="Consensus artifact not found")
    return _build_response(run)


@router.get("/failures/{failure_id}/consensus")
async def get_failure_consensus(
    failure_id: UUID,
    _: Any = Depends(require_permission(Permission.VIEW_FAILURES)),
) -> dict:
    store = FixPipelineRunStore()
    run = await store.get_run_by_event_id(failure_id)
    if run is None or not run.consensus_json:
        raise HTTPException(status_code=404, detail="Consensus artifact not found")
    return _build_response(run)
