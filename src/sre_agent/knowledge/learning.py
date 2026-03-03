"""Learning pipeline for improving fix quality.

Learns from resolved incidents to improve future fix generation.
"""

import logging
from collections import defaultdict
from typing import Any

from sre_agent.schemas.knowledge import (
    FixPattern,
    IncidentRecord,
)

logger = logging.getLogger(__name__)


class LearningPipeline:
    """
    Learns from resolved incidents.

    Tracks:
    - Success rates by category
    - Common fix patterns
    - Error → fix mappings
    """

    def __init__(self):
        """Initialize learning pipeline."""
        # Track statistics
        self._category_stats: dict[str, dict] = defaultdict(lambda: {"success": 0, "total": 0})
        self._fix_patterns: dict[str, FixPattern] = {}

    async def record_resolution(
        self,
        incident: IncidentRecord,
    ) -> None:
        """
        Record a resolved incident for learning.

        Args:
            incident: Resolved incident
        """
        if not incident.is_resolved:
            return

        category = incident.category

        # Update category stats
        self._category_stats[category]["total"] += 1
        if incident.was_successful:
            self._category_stats[category]["success"] += 1

        # Extract fix pattern if successful
        if incident.was_successful and incident.fix_diff:
            pattern_id = self._extract_pattern_id(incident)
            await self._update_pattern(pattern_id, incident, success=True)

        logger.info(
            "Recorded resolution",
            extra={
                "incident_id": str(incident.id),
                "category": category,
                "success": incident.was_successful,
            },
        )

    async def update_success_rate(
        self,
        category: str,
        success: bool,
    ) -> None:
        """
        Update success rate for a category.

        Args:
            category: Failure category
            success: Whether fix was successful
        """
        self._category_stats[category]["total"] += 1
        if success:
            self._category_stats[category]["success"] += 1

    def get_success_rate(self, category: str) -> float:
        """Get success rate for a category."""
        stats = self._category_stats.get(category)
        if not stats or stats["total"] == 0:
            return 0.0
        return stats["success"] / stats["total"]

    async def get_fix_patterns(
        self,
        category: str | None = None,
        min_success_rate: float = 0.5,
        limit: int = 10,
    ) -> list[FixPattern]:
        """
        Get learned fix patterns.

        Args:
            category: Filter by category
            min_success_rate: Minimum success rate
            limit: Maximum patterns to return

        Returns:
            List of fix patterns
        """
        patterns = list(self._fix_patterns.values())

        if category:
            patterns = [p for p in patterns if p.category == category]

        # Filter by success rate
        patterns = [p for p in patterns if p.success_rate >= min_success_rate]

        # Sort by success count
        patterns.sort(key=lambda p: p.success_count, reverse=True)

        return patterns[:limit]

    async def suggest_fix_approach(
        self,
        category: str,
        error_type: str | None = None,
    ) -> str | None:
        """
        Suggest a fix approach based on past successes.

        Args:
            category: Failure category
            error_type: Specific error type

        Returns:
            Suggested approach or None
        """
        patterns = await self.get_fix_patterns(category=category, limit=3)

        if not patterns:
            return None

        # Return best pattern description
        best = patterns[0]
        return best.description

    async def _update_pattern(
        self,
        pattern_id: str,
        incident: IncidentRecord,
        success: bool,
    ) -> None:
        """Update or create a fix pattern."""
        if pattern_id in self._fix_patterns:
            pattern = self._fix_patterns[pattern_id]
            pattern.total_count += 1
            if success:
                pattern.success_count += 1
        else:
            self._fix_patterns[pattern_id] = FixPattern(
                pattern_id=pattern_id,
                category=incident.category,
                description=incident.fix_summary or "Fix pattern",
                example_diff=incident.fix_diff[:1000] if incident.fix_diff else None,
                success_count=1 if success else 0,
                total_count=1,
            )

    def _extract_pattern_id(self, incident: IncidentRecord) -> str:
        """Extract a pattern ID from an incident."""
        # Simple pattern: category + first affected file extension
        ext = ""
        if incident.affected_files:
            first_file = incident.affected_files[0]
            if "." in first_file:
                ext = first_file.rsplit(".", 1)[-1]

        return f"{incident.category}_{ext or 'unknown'}"

    def get_all_stats(self) -> dict[str, Any]:
        """Get all learning statistics."""
        return {
            "category_stats": dict(self._category_stats),
            "total_patterns": len(self._fix_patterns),
            "patterns_by_category": {
                cat: len([p for p in self._fix_patterns.values() if p.category == cat])
                for cat in set(p.category for p in self._fix_patterns.values())
            },
        }
