"""Pydantic schemas for GitHub webhook payloads.

Reference: https://docs.github.com/en/webhooks/webhook-events-and-payloads

We focus on the workflow_job event which fires when a job starts,
completes, or fails. We only process failed jobs.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class GitHubUser(BaseModel):
    """GitHub user (actor) in webhook payload."""

    login: str
    id: int
    node_id: str | None = None
    avatar_url: str | None = None
    type: str = "User"


class GitHubRepository(BaseModel):
    """Repository information from GitHub webhook."""

    id: int
    node_id: str | None = None
    name: str
    full_name: str
    private: bool = False
    owner: GitHubUser
    html_url: str
    default_branch: str = "main"


class GitHubWorkflowJobStep(BaseModel):
    """A step within a workflow job."""

    name: str
    status: Literal["queued", "in_progress", "completed"]
    conclusion: Literal["success", "failure", "cancelled", "skipped", "neutral"] | None = None
    number: int
    started_at: datetime | None = None
    completed_at: datetime | None = None


class GitHubWorkflowJob(BaseModel):
    """The workflow_job object from GitHub webhook.

    Reference: https://docs.github.com/en/webhooks/webhook-events-and-payloads#workflow_job
    """

    id: int
    run_id: int
    run_attempt: int = 1
    workflow_name: str | None = None
    head_branch: str
    head_sha: str
    status: Literal["queued", "in_progress", "completed", "waiting"]
    conclusion: (
        Literal[
            "success", "failure", "cancelled", "skipped", "neutral", "timed_out", "action_required"
        ]
        | None
    ) = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    name: str  # Job name
    steps: list[GitHubWorkflowJobStep] = Field(default_factory=list)
    runner_name: str | None = None
    runner_group_name: str | None = None
    html_url: str | None = None

    @property
    def is_failure(self) -> bool:
        """Check if this job represents a failure."""
        return self.conclusion in ("failure", "timed_out")


class GitHubWorkflowJobPayload(BaseModel):
    """
    Complete webhook payload for workflow_job events.

    This is the top-level structure received from GitHub.
    """

    action: Literal["queued", "in_progress", "completed", "waiting"]
    workflow_job: GitHubWorkflowJob
    repository: GitHubRepository
    sender: GitHubUser
    organization: dict[str, Any] | None = None

    @property
    def is_completed_failure(self) -> bool:
        """Check if this is a completed job that failed."""
        return self.action == "completed" and self.workflow_job.is_failure


class GitHubWorkflowRun(BaseModel):
    """The workflow_run object from GitHub webhook.

    Reference: https://docs.github.com/en/webhooks/webhook-events-and-payloads#workflow_run
    """

    id: int
    name: str | None = None
    head_branch: str
    head_sha: str
    status: Literal["queued", "in_progress", "completed"]
    conclusion: (
        Literal[
            "success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required"
        ]
        | None
    ) = None
    workflow_id: int
    run_number: int
    run_attempt: int = 1
    event: str
    created_at: datetime
    updated_at: datetime
    html_url: str | None = None


class GitHubWorkflowRunPayload(BaseModel):
    """
    Complete webhook payload for workflow_run events.

    Note: We primarily use workflow_job for granular failure tracking,
    but workflow_run can be used for run-level summaries.
    """

    action: Literal["requested", "completed", "in_progress"]
    workflow: dict[str, Any] | None = None
    workflow_run: GitHubWorkflowRun
    repository: GitHubRepository
    sender: GitHubUser
    organization: dict[str, Any] | None = None

    @property
    def is_completed_failure(self) -> bool:
        """Check if this is a completed run that failed."""
        return self.action == "completed" and self.workflow_run.conclusion == "failure"
