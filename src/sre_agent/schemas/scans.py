from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ScanStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"
    GENERATED = "generated"


class GitleaksFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    file_path_hash: str


class GitleaksScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ScanStatus
    version: str | None = None
    duration_seconds: float = 0.0
    findings_count: int = 0
    findings: list[GitleaksFinding] = Field(default_factory=list)
    error_message: str | None = None


class TrivyPackageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int


class TrivyScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ScanStatus
    version: str | None = None
    duration_seconds: float = 0.0
    total_vulnerabilities: int = 0
    severity_counts: dict[str, int] = Field(default_factory=dict)
    top_packages: list[TrivyPackageSummary] = Field(default_factory=list)
    threshold: str = "HIGH"
    error_message: str | None = None


class SbomResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ScanStatus
    version: str | None = None
    duration_seconds: float = 0.0
    path: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    format: str = "syft-json"
    error_message: str | None = None


class ScanSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gitleaks: GitleaksScanResult
    trivy: TrivyScanResult
    sbom: SbomResult
