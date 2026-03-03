from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from sre_agent.database import get_async_session
from sre_agent.models.fix_pipeline import FixPipelineRun


class FixPipelineRunStore:
    async def create_run(
        self,
        event_id: UUID,
        run_key: str | None = None,
        context_json: dict[str, Any] | None = None,
        rca_json: dict[str, Any] | None = None,
    ) -> UUID:
        async with get_async_session() as session:
            existing = (
                (
                    await session.execute(
                        select(FixPipelineRun).where(FixPipelineRun.event_id == event_id)
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                updated = False
                if run_key and not existing.run_key:
                    existing.run_key = run_key
                    updated = True
                if context_json is not None and existing.context_json is None:
                    existing.context_json = context_json
                    updated = True
                if rca_json is not None and existing.rca_json is None:
                    existing.rca_json = rca_json
                    updated = True
                if updated:
                    await session.commit()
                return existing.id

            run = FixPipelineRun(
                event_id=event_id, run_key=run_key, context_json=context_json, rca_json=rca_json
            )
            session.add(run)
            try:
                await session.commit()
                return run.id
            except IntegrityError:
                await session.rollback()
                result = await session.execute(
                    select(FixPipelineRun).where(FixPipelineRun.event_id == event_id)
                )
                found = result.scalar_one()
                return found.id

    async def get_run(self, run_id: UUID) -> FixPipelineRun | None:
        async with get_async_session() as session:
            result = await session.execute(
                select(FixPipelineRun).where(FixPipelineRun.id == run_id)
            )
            return result.scalar_one_or_none()

    async def get_run_by_event_id(self, event_id: UUID) -> FixPipelineRun | None:
        async with get_async_session() as session:
            result = await session.execute(
                select(FixPipelineRun).where(FixPipelineRun.event_id == event_id)
            )
            return result.scalar_one_or_none()

    async def update_run(self, run_id: UUID, **fields: Any) -> None:
        async with get_async_session() as session:
            result = await session.execute(
                select(FixPipelineRun).where(FixPipelineRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if run is None:
                return
            for k, v in fields.items():
                setattr(run, k, v)
            await session.commit()
