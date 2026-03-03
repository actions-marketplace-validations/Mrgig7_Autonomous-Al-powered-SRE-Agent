from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from sre_agent.schemas.fix_plan import FixPlan


class DetectionResult(BaseModel):
    repo_language: str
    category: str
    evidence_lines: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ValidationStep(BaseModel):
    name: str
    command: str
    timeout_seconds: int | None = None
    workdir: str | None = None


class BaseAdapter(ABC):
    name: str
    supported_languages: list[str]

    @abstractmethod
    def detect(self, log_text: str, repo_files: list[str]) -> DetectionResult | None: ...

    @abstractmethod
    def build_validation_steps(self, repo_root: str) -> list[ValidationStep]: ...

    @abstractmethod
    def allowed_fix_types(self) -> set[str]: ...

    def allowed_categories(self) -> set[str]:
        return set()

    def deterministic_patch(self, plan: FixPlan, repo_root: str) -> str | None:
        return None
