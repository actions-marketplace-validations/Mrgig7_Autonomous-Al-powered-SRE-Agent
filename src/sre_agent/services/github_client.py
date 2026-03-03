"""Async GitHub API client for fetching workflow data and logs.

Uses GitHub REST API v3 to:
- Fetch workflow run details
- Fetch job details
- Download job logs

Reference: https://docs.github.com/en/rest/actions
"""

import base64
import logging
import zipfile
from io import BytesIO
from typing import Any

import httpx

from sre_agent.config import get_settings

logger = logging.getLogger(__name__)


class GitHubAPIError(Exception):
    """Base exception for GitHub API errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GitHubRateLimitError(GitHubAPIError):
    """Raised when GitHub API rate limit is exceeded."""

    pass


class GitHubNotFoundError(GitHubAPIError):
    """Raised when requested resource is not found."""

    pass


class GitHubClient:
    """
    Async GitHub API client for fetching workflow data.

    Handles authentication, rate limiting, and error responses.
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
    ):
        """
        Initialize GitHub client.

        Args:
            token: GitHub personal access token (or uses GITHUB_TOKEN env var)
            base_url: GitHub API base URL (default: https://api.github.com)
        """
        settings = get_settings()
        self.token = token or settings.github_token
        self.base_url = (base_url or settings.github_api_base_url).rstrip("/")

        if not self.token:
            logger.warning("GitHub token not configured - API calls will be rate limited")

        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "GitHubClient":
        """Enter async context."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._build_headers(),
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _build_headers(self) -> dict[str, str]:
        """Build request headers with authentication."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Make an API request with error handling.

        Raises:
            GitHubRateLimitError: If rate limit exceeded
            GitHubNotFoundError: If resource not found
            GitHubAPIError: For other API errors
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use 'async with' context.")

        response = await self._client.request(method, path, **kwargs)

        # Handle rate limiting
        if response.status_code == 403:
            remaining = response.headers.get("X-RateLimit-Remaining", "0")
            if remaining == "0":
                reset_time = response.headers.get("X-RateLimit-Reset", "unknown")
                raise GitHubRateLimitError(
                    f"GitHub API rate limit exceeded. Resets at {reset_time}",
                    status_code=403,
                )

        # Handle not found
        if response.status_code == 404:
            raise GitHubNotFoundError(
                f"Resource not found: {path}",
                status_code=404,
            )

        # Handle other errors
        if response.status_code >= 400:
            raise GitHubAPIError(
                f"GitHub API error: {response.status_code} - {response.text}",
                status_code=response.status_code,
            )

        return response

    async def get_workflow_run(self, repo: str, run_id: int) -> dict[str, Any]:
        """
        Get details of a workflow run.

        Args:
            repo: Repository in "owner/repo" format
            run_id: Workflow run ID

        Returns:
            Workflow run data from GitHub API
        """
        logger.debug(f"Fetching workflow run {run_id} from {repo}")
        response = await self._request(
            "GET",
            f"/repos/{repo}/actions/runs/{run_id}",
        )
        return response.json()

    async def get_workflow_job(self, repo: str, job_id: int) -> dict[str, Any]:
        """
        Get details of a workflow job.

        Args:
            repo: Repository in "owner/repo" format
            job_id: Job ID

        Returns:
            Job data from GitHub API
        """
        logger.debug(f"Fetching workflow job {job_id} from {repo}")
        response = await self._request(
            "GET",
            f"/repos/{repo}/actions/jobs/{job_id}",
        )
        return response.json()

    async def get_workflow_run_jobs(
        self,
        repo: str,
        run_id: int,
    ) -> list[dict[str, Any]]:
        """
        Get all jobs for a workflow run.

        Args:
            repo: Repository in "owner/repo" format
            run_id: Workflow run ID

        Returns:
            List of job data
        """
        logger.debug(f"Fetching jobs for run {run_id} from {repo}")
        response = await self._request(
            "GET",
            f"/repos/{repo}/actions/runs/{run_id}/jobs",
        )
        return response.json().get("jobs", [])

    async def download_job_logs(
        self,
        repo: str,
        job_id: int,
    ) -> str:
        """
        Download logs for a specific job.

        GitHub returns logs as a compressed archive. This method
        downloads, decompresses, and returns the log content.

        Args:
            repo: Repository in "owner/repo" format
            job_id: Job ID

        Returns:
            Log content as string
        """
        logger.info(f"Downloading logs for job {job_id} from {repo}")

        # Get the download URL (GitHub redirects to a signed URL)
        response = await self._request(
            "GET",
            f"/repos/{repo}/actions/jobs/{job_id}/logs",
            follow_redirects=True,
        )

        # The response is a zip file containing log files
        log_content = await self._extract_logs_from_zip(response.content)

        logger.info(
            f"Downloaded logs for job {job_id}",
            extra={"size_bytes": len(log_content)},
        )

        return log_content

    async def download_run_logs(
        self,
        repo: str,
        run_id: int,
    ) -> dict[str, str]:
        """
        Download all logs for a workflow run.

        Args:
            repo: Repository in "owner/repo" format
            run_id: Workflow run ID

        Returns:
            Dict mapping job names to log content
        """
        logger.info(f"Downloading logs for run {run_id} from {repo}")

        response = await self._request(
            "GET",
            f"/repos/{repo}/actions/runs/{run_id}/logs",
            follow_redirects=True,
        )

        return await self._extract_run_logs_from_zip(response.content)

    async def _extract_logs_from_zip(self, zip_content: bytes) -> str:
        """Extract log content from a zip archive."""
        try:
            with zipfile.ZipFile(BytesIO(zip_content)) as zf:
                # Concatenate all log files
                logs = []
                for name in sorted(zf.namelist()):
                    if name.endswith(".txt"):
                        content = zf.read(name).decode("utf-8", errors="replace")
                        logs.append(f"=== {name} ===\n{content}")
                return "\n".join(logs)
        except zipfile.BadZipFile:
            # Sometimes GitHub returns plain text instead of zip
            return zip_content.decode("utf-8", errors="replace")

    async def _extract_run_logs_from_zip(
        self,
        zip_content: bytes,
    ) -> dict[str, str]:
        """Extract logs from run archive, organized by job."""
        result: dict[str, str] = {}

        try:
            with zipfile.ZipFile(BytesIO(zip_content)) as zf:
                for name in zf.namelist():
                    if name.endswith(".txt"):
                        # Job name is typically the directory name
                        parts = name.split("/")
                        job_name = parts[0] if len(parts) > 1 else "default"

                        content = zf.read(name).decode("utf-8", errors="replace")

                        if job_name not in result:
                            result[job_name] = ""
                        result[job_name] += f"\n=== {name} ===\n{content}"

        except zipfile.BadZipFile:
            # Plain text fallback
            result["default"] = zip_content.decode("utf-8", errors="replace")

        return result

    async def get_commit(self, repo: str, sha: str) -> dict[str, Any]:
        """
        Get commit details including changed files.

        Args:
            repo: Repository in "owner/repo" format
            sha: Commit SHA

        Returns:
            Commit data from GitHub API
        """
        logger.debug(f"Fetching commit {sha[:8]} from {repo}")
        response = await self._request(
            "GET",
            f"/repos/{repo}/commits/{sha}",
        )
        return response.json()

    async def get_rate_limit(self) -> dict[str, Any]:
        """Get current rate limit status."""
        response = await self._request("GET", "/rate_limit")
        return response.json()

    async def get_user_repositories(
        self,
        *,
        per_page: int = 100,
        sort: str = "updated",
    ) -> list[dict[str, Any]]:
        """List repositories visible to the authenticated user."""
        response = await self._request(
            "GET",
            "/user/repos",
            params={
                "per_page": per_page,
                "sort": sort,
            },
        )
        return response.json()

    async def get_repository(self, repo: str) -> dict[str, Any]:
        """Fetch repository metadata and caller permissions."""
        response = await self._request(
            "GET",
            f"/repos/{repo}",
        )
        return response.json()

    async def get_file_content(self, repo: str, path: str, ref: str | None = None) -> str | None:
        """Fetch text content for a repository file via GitHub contents API."""
        params: dict[str, str] = {}
        if ref:
            params["ref"] = ref

        try:
            response = await self._request(
                "GET",
                f"/repos/{repo}/contents/{path}",
                params=params or None,
            )
        except GitHubNotFoundError:
            return None

        payload = response.json()
        if not isinstance(payload, dict):
            raise GitHubAPIError("Unexpected GitHub contents API response shape")

        encoding = payload.get("encoding")
        content = payload.get("content")
        if not isinstance(content, str):
            raise GitHubAPIError("Repository file response missing content")

        if encoding == "base64":
            try:
                decoded = base64.b64decode(content, validate=False)
                return decoded.decode("utf-8")
            except Exception as exc:
                raise GitHubAPIError(f"Failed to decode base64 file content: {exc}") from exc

        return content
