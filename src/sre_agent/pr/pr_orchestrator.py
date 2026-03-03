"""PR orchestrator.

Coordinates the full PR creation workflow.
"""

import logging
from pathlib import Path
from uuid import UUID

from sre_agent.config import get_settings
from sre_agent.pr.branch_manager import BranchManager
from sre_agent.pr.pr_creator import PRCreator
from sre_agent.schemas.fix import FixSuggestion
from sre_agent.schemas.intelligence import RCAResult
from sre_agent.schemas.pr import PRRequest, PRResult, PRStatus
from sre_agent.schemas.validation import ValidationResult

logger = logging.getLogger(__name__)


class PROrchestrator:
    """
    Orchestrates the full PR creation workflow.

    Steps:
    1. Clone repository
    2. Create fix branch
    3. Apply diff and commit
    4. Push branch
    5. Create PR via API
    """

    def __init__(
        self,
        branch_manager: BranchManager | None = None,
        pr_creator: PRCreator | None = None,
    ):
        """
        Initialize orchestrator.

        Args:
            branch_manager: Branch manager instance
            pr_creator: PR creator instance
        """
        self.branch_manager = branch_manager or BranchManager()
        self.pr_creator = pr_creator or PRCreator()

    async def create_pr_for_fix(
        self,
        fix: FixSuggestion,
        rca_result: RCAResult,
        validation: ValidationResult | None,
        repo_url: str,
        base_branch: str = "main",
        run_id: UUID | None = None,
    ) -> PRResult:
        """
        Create a PR for a validated fix.

        Args:
            fix: Fix suggestion
            rca_result: RCA analysis result
            validation: Validation result (if validated)
            repo_url: Repository URL
            base_branch: Target branch

        Returns:
            PRResult with PR info
        """
        # Extract repo from URL (e.g., "owner/repo" from git URL)
        repo = self._extract_repo(repo_url)

        logger.info(
            "Starting PR creation workflow",
            extra={
                "fix_id": fix.fix_id,
                "repo": repo,
                "base_branch": base_branch,
            },
        )

        # Generate branch name
        branch_name = self.branch_manager.generate_branch_name(fix.fix_id)
        if not fix.is_safe_to_apply:
            return PRResult(
                status=PRStatus.FAILED,
                branch_name=branch_name,
                base_branch=base_branch,
                fix_id=fix.fix_id,
                event_id=fix.event_id,
                error_message="Fix blocked by safety policy or guardrails",
            )

        request = self._build_pr_request(
            fix=fix,
            rca_result=rca_result,
            validation=validation,
            repo=repo,
            base_branch=base_branch,
            run_id=run_id,
        )
        existing = await self.pr_creator.find_open_pr_by_head(
            request=request,
            head_branch=branch_name,
        )
        if existing and existing.pr_url:
            return existing

        repo_path: Path | None = None

        try:
            # Clone repository
            repo_path = await self.branch_manager.clone_repo(repo_url, base_branch)

            # Create fix branch
            await self.branch_manager.create_branch(repo_path, branch_name)

            # Apply diff
            await self.branch_manager.apply_diff(repo_path, fix.full_diff)

            # Commit
            commit_msg = self._generate_commit_message(fix, rca_result)
            await self.branch_manager.commit(repo_path, commit_msg)

            # Push
            await self.branch_manager.push_branch(repo_path, branch_name)

            # Create PR
            result = await self.pr_creator.create_pr(request, branch_name)

            return result

        except Exception as e:
            logger.error(f"PR creation failed: {e}", exc_info=True)
            return PRResult(
                status=PRStatus.FAILED,
                branch_name=branch_name,
                base_branch=base_branch,
                fix_id=fix.fix_id,
                event_id=fix.event_id,
                error_message=str(e),
            )

        finally:
            if repo_path:
                await self.branch_manager.cleanup(repo_path)

    async def merge_pr_for_fix(
        self,
        *,
        repo_url: str,
        pr_number: int,
    ) -> tuple[bool, dict]:
        """Merge PR created for a fix."""
        repo = self._extract_repo(repo_url)
        settings = get_settings()
        return await self.pr_creator.merge_pr(
            repo=repo,
            pr_number=pr_number,
            merge_method=settings.phase3_auto_merge_method,
        )

    def _extract_repo(self, repo_url: str) -> str:
        """Extract owner/repo from URL."""
        # Handle various URL formats
        url = repo_url.rstrip("/").rstrip(".git")

        if "github.com" in url:
            parts = url.split("github.com")[-1].strip("/").split("/")
            if len(parts) >= 2:
                return f"{parts[0]}/{parts[1]}"

        # Fallback
        parts = url.split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"

        return url

    def _generate_commit_message(
        self,
        fix: FixSuggestion,
        rca_result: RCAResult,
    ) -> str:
        """Generate commit message."""
        category = rca_result.classification.category.value
        summary = fix.summary or "Auto-generated fix"

        return f"""fix: {summary}

Category: {category}
Confidence: {rca_result.primary_hypothesis.confidence:.0%}

{rca_result.primary_hypothesis.description}

Generated by SRE Agent
Fix ID: {fix.fix_id}
"""

    def _build_pr_request(
        self,
        fix: FixSuggestion,
        rca_result: RCAResult,
        validation: ValidationResult | None,
        repo: str,
        base_branch: str,
        run_id: UUID | None = None,
    ) -> PRRequest:
        """Build PR request from fix and context."""
        if validation is None:
            sandbox_summary = "validation unavailable"
        else:
            sandbox_summary = (
                f"status={validation.status.value}; "
                f"passed={validation.tests_passed}; failed={validation.tests_failed}"
            )
        policy_summary = None
        risk_score = None
        if fix.safety_status:
            risk_score = fix.safety_status.danger_score
            policy_summary = (
                f"label={fix.safety_status.pr_label}; "
                f"violations={len(fix.safety_status.violations)}"
            )
        return PRRequest(
            fix_id=fix.fix_id,
            event_id=fix.event_id,
            repo=repo,
            base_branch=base_branch,
            diff=fix.full_diff,
            labels=[fix.safety_status.pr_label] if fix.safety_status else ["needs-review"],
            error_type=rca_result.classification.category.value,
            hypothesis=rca_result.primary_hypothesis.description,
            confidence=rca_result.primary_hypothesis.confidence,
            affected_files=fix.target_files,
            tests_passed=validation.tests_passed if validation else 0,
            tests_failed=validation.tests_failed if validation else 0,
            validation_status=validation.status.value if validation else "not_validated",
            risk_score=risk_score,
            evidence_lines=rca_result.primary_hypothesis.evidence,
            policy_summary=policy_summary,
            sandbox_summary=sandbox_summary,
            provenance_artifact_url=(f"/api/v1/runs/{run_id}/artifact" if run_id else None),
        )


async def create_pr_for_event(
    event_id: str,
    fix_id: str,
) -> PRResult:
    """
    Create a PR for a stored event and fix.

    Convenience function that loads data and orchestrates PR creation.

    Args:
        event_id: Pipeline event ID
        fix_id: Fix ID

    Returns:
        PRResult
    """
    # TODO: Load fix, RCA result, and validation from storage
    # For now, return error indicating storage not implemented
    return PRResult(
        status=PRStatus.FAILED,
        branch_name="",
        base_branch="main",
        fix_id=fix_id,
        event_id=UUID(event_id),
        error_message="Fix storage not yet implemented",
    )
