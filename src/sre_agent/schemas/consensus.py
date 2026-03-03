from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sre_agent.schemas.fix_plan import FixPlan


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


class ReasoningEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    relation: str


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    file: str
    rationale: str
    evidence: list[str] = Field(default_factory=list)

    @field_validator("file")
    @classmethod
    def normalize_file(cls, value: str) -> str:
        return _normalize_path(value)


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str
    version: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    reasoning_graph: list[ReasoningEdge] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IssueDependencyLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    relation: str


class IssueNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    message: str
    severity: Literal["error", "warning", "info"]
    file_paths: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("file_paths")
    @classmethod
    def normalize_files(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = _normalize_path(value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out


class IssueGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[IssueNode] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    severity_levels: dict[str, int] = Field(default_factory=dict)
    dependency_links: list[IssueDependencyLink] = Field(default_factory=list)

    @field_validator("affected_files")
    @classmethod
    def normalize_affected_files(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = _normalize_path(value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out


class ConsensusRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    agent_name: str | None = None
    details: str | None = None


class ConsensusDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal[
        "accepted",
        "rejected_low_agreement",
        "rejected_safety_veto",
        "rejected_invalid_candidates",
    ]
    agreement_rate: float = Field(ge=0.0, le=1.0)
    selected_agent: str | None = None
    selected_plan: FixPlan | None = None
    candidates: list[AgentOutput] = Field(default_factory=list)
    rejections: list[ConsensusRejection] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
