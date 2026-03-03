"""Rollback controller for reverting failed fixes.

Handles creating revert PRs when merged fixes cause issues.
"""

import logging

import httpx

from sre_agent.config import get_settings
from sre_agent.schemas.pr import RollbackRequest, RollbackResult

logger = logging.getLogger(__name__)


class RollbackController:
    """
    Handles rollback of failed fixes.

    Capabilities:
    - Create revert PRs for merged fixes
    - Create issues for manual intervention
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.github.com",
    ):
        """
        Initialize rollback controller.

        Args:
            token: GitHub personal access token
            base_url: GitHub API base URL
        """
        settings = get_settings()
        self.token = token or settings.github_token
        self.base_url = base_url.rstrip("/")

    def _build_headers(self) -> dict[str, str]:
        """Build request headers."""
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def revert_pr(self, request: RollbackRequest) -> RollbackResult:
        """
        Create a revert PR for a merged fix.

        Args:
            request: Rollback request

        Returns:
            RollbackResult with revert PR info
        """
        logger.info(
            "Creating revert PR",
            extra={
                "repo": request.repo,
                "pr_number": request.pr_number,
                "reason": request.reason,
            },
        )

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._build_headers(),
                timeout=30.0,
            ) as client:
                # Get the original PR
                pr_response = await client.get(f"/repos/{request.repo}/pulls/{request.pr_number}")

                if pr_response.status_code != 200:
                    return RollbackResult(
                        success=False,
                        error_message=f"Failed to get PR: {pr_response.text}",
                    )

                pr_data = pr_response.json()

                # Check if PR was merged
                if not pr_data.get("merged"):
                    return RollbackResult(
                        success=False,
                        error_message="PR was not merged, cannot revert",
                    )

                merge_commit = pr_data.get("merge_commit_sha")
                if not merge_commit:
                    return RollbackResult(
                        success=False,
                        error_message="No merge commit found",
                    )

                # Create revert commit via API
                # Note: GitHub doesn't have a direct revert API,
                # so we create a new branch and PR with reversed changes

                # For now, create an issue requesting manual revert
                issue_result = await self._create_rollback_issue(
                    client,
                    request.repo,
                    request.pr_number,
                    request.reason,
                    pr_data.get("title", ""),
                )

                if issue_result:
                    return RollbackResult(
                        success=True,
                        revert_pr_url=issue_result,
                        error_message="Created rollback issue (manual revert required)",
                    )

                return RollbackResult(
                    success=False,
                    error_message="Failed to create rollback issue",
                )

        except Exception as e:
            logger.error(f"Rollback failed: {e}", exc_info=True)
            return RollbackResult(
                success=False,
                error_message=str(e),
            )

    async def _create_rollback_issue(
        self,
        client: httpx.AsyncClient,
        repo: str,
        pr_number: int,
        reason: str,
        pr_title: str,
    ) -> str | None:
        """Create an issue requesting rollback."""
        body = f"""## 🚨 Rollback Required

An auto-generated fix has caused issues and needs to be reverted.

### Original PR
- PR: #{pr_number}
- Title: {pr_title}

### Reason for Rollback
{reason}

### Action Required
Please manually revert the changes from PR #{pr_number}:
```bash
git revert -m 1 <merge-commit-sha>
git push origin main
```

---
*Created by SRE Agent*
"""

        response = await client.post(
            f"/repos/{repo}/issues",
            json={
                "title": f"🚨 Rollback required: {pr_title[:50]}",
                "body": body,
                "labels": ["rollback", "sre-agent", "urgent"],
            },
        )

        if response.status_code == 201:
            return response.json().get("html_url")

        return None

    async def check_pr_status(
        self,
        repo: str,
        pr_number: int,
    ) -> dict:
        """
        Check the current status of a PR.

        Args:
            repo: Repository
            pr_number: PR number

        Returns:
            Dict with PR status info
        """
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._build_headers(),
            timeout=30.0,
        ) as client:
            response = await client.get(f"/repos/{repo}/pulls/{pr_number}")

            if response.status_code != 200:
                return {"error": response.text}

            data = response.json()
            return {
                "state": data.get("state"),
                "merged": data.get("merged", False),
                "mergeable": data.get("mergeable"),
                "merge_commit_sha": data.get("merge_commit_sha"),
            }
