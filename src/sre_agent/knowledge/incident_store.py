"""Incident storage for knowledge base.

Stores resolved incidents in the database for future reference.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sre_agent.schemas.fix import FixSuggestion
from sre_agent.schemas.intelligence import RCAResult
from sre_agent.schemas.knowledge import (
    CategoryStats,
    IncidentQuery,
    IncidentRecord,
    IncidentStatus,
)
from sre_agent.schemas.pr import PRResult
from sre_agent.schemas.validation import ValidationResult

logger = logging.getLogger(__name__)


class IncidentStore:
    """
    Stores and retrieves resolved incidents.

    Provides persistence for the knowledge base, enabling
    learning from past resolutions.
    """

    def __init__(self):
        """Initialize incident store."""
        # In-memory cache for MVP (would use DB in production)
        self._incidents: dict[str, IncidentRecord] = {}

    async def store_incident(
        self,
        event_id: UUID,
        rca_result: RCAResult,
        fix: FixSuggestion | None = None,
        validation: ValidationResult | None = None,
        pr_result: PRResult | None = None,
    ) -> IncidentRecord:
        """
        Store a new incident.

        Args:
            event_id: Original pipeline event ID
            rca_result: RCA analysis result
            fix: Generated fix (if any)
            validation: Validation result (if validated)
            pr_result: PR result (if PR created)

        Returns:
            Stored IncidentRecord
        """
        incident_id = uuid4()

        # Determine status
        if pr_result and pr_result.pr_number:
            status = IncidentStatus.PR_CREATED
        elif validation and validation.is_successful:
            status = IncidentStatus.VALIDATED
        elif fix:
            status = IncidentStatus.PENDING
        else:
            status = IncidentStatus.FAILED

        record = IncidentRecord(
            id=incident_id,
            event_id=event_id,
            status=status,
            category=rca_result.classification.category.value,
            confidence=rca_result.classification.confidence,
            hypothesis=rca_result.primary_hypothesis.description,
            error_type=rca_result.classification.category.value,
            affected_files=[f.filename for f in rca_result.affected_files[:10]],
            fix_id=fix.fix_id if fix else None,
            fix_summary=fix.summary if fix else None,
            fix_diff=fix.full_diff if fix else None,
            validation_passed=validation.is_successful if validation else None,
            tests_passed=validation.tests_passed if validation else 0,
            tests_failed=validation.tests_failed if validation else 0,
            pr_number=pr_result.pr_number if pr_result else None,
            pr_url=pr_result.pr_url if pr_result else None,
        )

        self._incidents[str(incident_id)] = record

        logger.info(
            "Stored incident",
            extra={
                "incident_id": str(incident_id),
                "event_id": str(event_id),
                "status": status.value,
                "category": record.category,
            },
        )

        return record

    async def get_incident(self, incident_id: UUID) -> IncidentRecord | None:
        """Get an incident by ID."""
        return self._incidents.get(str(incident_id))

    async def update_incident(
        self,
        incident_id: UUID,
        **updates: Any,
    ) -> IncidentRecord | None:
        """Update an incident."""
        record = self._incidents.get(str(incident_id))
        if not record:
            return None

        # Update fields
        for key, value in updates.items():
            if hasattr(record, key):
                setattr(record, key, value)

        self._incidents[str(incident_id)] = record
        return record

    async def mark_merged(
        self,
        incident_id: UUID,
        pr_merged: bool = True,
    ) -> IncidentRecord | None:
        """Mark an incident's PR as merged."""
        return await self.update_incident(
            incident_id,
            status=IncidentStatus.MERGED,
            pr_merged=pr_merged,
            resolved_at=datetime.now(UTC),
            resolved_by="auto",
        )

    async def mark_failed(
        self,
        incident_id: UUID,
        resolution: str,
    ) -> IncidentRecord | None:
        """Mark an incident as failed."""
        return await self.update_incident(
            incident_id,
            status=IncidentStatus.FAILED,
            resolution=resolution,
            resolved_at=datetime.now(UTC),
        )

    async def list_incidents(
        self,
        query: IncidentQuery | None = None,
    ) -> list[IncidentRecord]:
        """List incidents matching query."""
        query = query or IncidentQuery()
        results = list(self._incidents.values())

        # Apply filters
        if query.category:
            results = [r for r in results if r.category == query.category]
        if query.status:
            results = [r for r in results if r.status == query.status]
        if query.from_date:
            results = [r for r in results if r.created_at >= query.from_date]
        if query.to_date:
            results = [r for r in results if r.created_at <= query.to_date]

        # Sort by created_at descending
        results.sort(key=lambda x: x.created_at, reverse=True)

        # Apply pagination
        return results[query.offset : query.offset + query.limit]

    async def get_category_stats(self) -> list[CategoryStats]:
        """Get statistics by category."""
        stats: dict[str, dict] = {}

        for incident in self._incidents.values():
            cat = incident.category
            if cat not in stats:
                stats[cat] = {
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "confidence_sum": 0,
                }

            stats[cat]["total"] += 1
            stats[cat]["confidence_sum"] += incident.confidence

            if incident.was_successful:
                stats[cat]["success"] += 1
            elif incident.is_resolved:
                stats[cat]["failed"] += 1

        return [
            CategoryStats(
                category=cat,
                total_incidents=data["total"],
                successful_fixes=data["success"],
                failed_fixes=data["failed"],
                avg_confidence=data["confidence_sum"] / data["total"] if data["total"] > 0 else 0,
                avg_resolution_time_hours=None,  # TODO: Calculate
            )
            for cat, data in stats.items()
        ]

    async def get_successful_fixes(
        self,
        category: str | None = None,
        limit: int = 10,
    ) -> list[IncidentRecord]:
        """Get successful fixes for learning."""
        results = [r for r in self._incidents.values() if r.was_successful and r.fix_diff]

        if category:
            results = [r for r in results if r.category == category]

        results.sort(key=lambda x: x.confidence, reverse=True)
        return results[:limit]
