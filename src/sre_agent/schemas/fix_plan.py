from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

FixOperationType = Literal[
    "add_dependency",
    "pin_dependency",
    "update_config",
    "modify_code",
    "remove_unused",
]


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


class FixOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: FixOperationType
    file: str
    details: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    evidence: list[str] = Field(default_factory=list)

    @field_validator("file")
    @classmethod
    def normalize_file(cls, v: str) -> str:
        return _normalize_path(v)

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(cls, v: list[str]) -> list[str]:
        return [e.strip() for e in v if e.strip()]


class FixPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_cause: str
    category: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    files: list[str]
    operations: list[FixOperation]

    @field_validator("files")
    @classmethod
    def normalize_files(cls, v: list[str]) -> list[str]:
        normalized = [_normalize_path(p) for p in v]
        normalized = [p for p in normalized if p]
        seen: set[str] = set()
        out: list[str] = []
        for p in normalized:
            if p not in seen:
                out.append(p)
                seen.add(p)
        return out

    @field_validator("operations")
    @classmethod
    def enforce_max_operations(cls, ops: list[FixOperation]) -> list[FixOperation]:
        if len(ops) > 10:
            raise ValueError("FixPlan.operations exceeds max of 10")
        return ops

    @model_validator(mode="after")
    def validate_operation_files(self) -> FixPlan:
        file_set = set(self.files)
        for op in self.operations:
            if op.file not in file_set:
                raise ValueError("FixOperation.file must be included in FixPlan.files")
        return self


class FixPlanParseError(Exception):
    def __init__(
        self, message: str, raw_output: str, validation_error: ValidationError | None = None
    ):
        super().__init__(message)
        self.raw_output = raw_output
        self.validation_error = validation_error
