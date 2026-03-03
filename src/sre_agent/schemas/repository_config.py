"""Schemas for repository-scoped runtime configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AutomationMode = Literal["suggest", "auto_pr", "auto_merge"]


class RepositoryRuntimeConfig(BaseModel):
    """Runtime repository config used by the ingestion and pipeline flow."""

    automation_mode: AutomationMode = "suggest"
    protected_paths: list[str] = Field(default_factory=list)
    retry_limit: int = 3
    source: Literal[
        "installation_default",
        "repo_file",
        "repo_file_missing",
        "repo_file_invalid",
        "repo_file_unavailable",
    ] = "installation_default"
