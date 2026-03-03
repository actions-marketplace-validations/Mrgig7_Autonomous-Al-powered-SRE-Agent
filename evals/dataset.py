from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FailureMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: Literal["synthetic", "public"]
    repo_language: Literal["python", "node", "go", "java", "docker"]
    category: str
    description: str
    policy_profile: str = "default"
    created_at: str
    notes: str = ""
    public_source_url: str | None = None


class SuccessCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validation_must_pass: bool = True
    policy_violations_allowed: int = 0
    max_danger_score: int = 30


class ExpectedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success_criteria: SuccessCriteria = Field(default_factory=SuccessCriteria)
    expected_category: str
    allowed_fix_types: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    dir_path: Path
    logs_text: str
    failure: FailureMetadata
    expected: ExpectedOutcome
    repo_fixture_dir: Path | None


_CASE_DIR_RE = re.compile(r"^\d{4}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_json(path: Path) -> dict:
    return json.loads(_read_text(path))


def discover_case_dirs(dataset_path: Path) -> list[Path]:
    if not dataset_path.exists():
        raise FileNotFoundError(str(dataset_path))
    if not dataset_path.is_dir():
        raise NotADirectoryError(str(dataset_path))
    return sorted([p for p in dataset_path.iterdir() if p.is_dir() and _CASE_DIR_RE.match(p.name)])


def load_case(case_dir: Path) -> EvalCase:
    logs_path = case_dir / "logs.txt"
    failure_path = case_dir / "failure.json"
    expected_path = case_dir / "expected.json"
    fixture_dir = case_dir / "repo_fixture"

    if not logs_path.exists():
        raise FileNotFoundError(str(logs_path))
    if not failure_path.exists():
        raise FileNotFoundError(str(failure_path))
    if not expected_path.exists():
        raise FileNotFoundError(str(expected_path))

    logs_text = _read_text(logs_path)
    if not logs_text.strip():
        raise ValueError(f"{logs_path} is empty")

    failure = FailureMetadata.model_validate(_read_json(failure_path))
    expected = ExpectedOutcome.model_validate(_read_json(expected_path))

    if failure.id != case_dir.name:
        raise ValueError(f"failure.json id={failure.id!r} does not match folder {case_dir.name!r}")
    if expected.expected_category != failure.category:
        raise ValueError(
            f"expected.json expected_category={expected.expected_category!r} does not match "
            f"failure.json category={failure.category!r}"
        )
    if not _DATE_RE.match(failure.created_at):
        raise ValueError(f"failure.json created_at must be YYYY-MM-DD (got {failure.created_at!r})")
    if failure.source == "public" and not failure.public_source_url:
        raise ValueError("public cases must include public_source_url")
    if failure.source == "synthetic" and failure.public_source_url:
        raise ValueError("synthetic cases must not include public_source_url")

    repo_fixture_dir = fixture_dir if fixture_dir.exists() and fixture_dir.is_dir() else None

    return EvalCase(
        case_id=failure.id,
        dir_path=case_dir,
        logs_text=logs_text,
        failure=failure,
        expected=expected,
        repo_fixture_dir=repo_fixture_dir,
    )


def load_dataset(dataset_path: Path, limit: int | None = None) -> list[EvalCase]:
    case_dirs = discover_case_dirs(dataset_path)
    if limit is not None:
        case_dirs = case_dirs[: max(0, limit)]
    return [load_case(d) for d in case_dirs]
