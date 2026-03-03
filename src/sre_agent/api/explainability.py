from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from sre_agent.auth.permissions import Permission, require_permission
from sre_agent.explainability.explain_service import build_failure_explain_payload
from sre_agent.schemas.explainability import FailureExplainResponse

router = APIRouter(prefix="/failures", tags=["Explainability"])


@router.get("/{failure_id}/analysis", response_model=FailureExplainResponse)
@router.get("/{failure_id}/explain", response_model=FailureExplainResponse)
async def explain_failure(
    failure_id: UUID,
    _: Any = Depends(require_permission(Permission.VIEW_FAILURES)),
) -> dict:
    payload = await build_failure_explain_payload(failure_id=failure_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Failure not found")
    return payload
