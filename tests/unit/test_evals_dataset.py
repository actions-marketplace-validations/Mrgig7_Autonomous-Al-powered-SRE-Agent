from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.dataset import load_case, load_dataset


def test_load_case_valid(tmp_path: Path) -> None:
    case_dir = tmp_path / "0001"
    fixture_dir = case_dir / "repo_fixture"
    fixture_dir.mkdir(parents=True)

    (case_dir / "logs.txt").write_text("Traceback (most recent call last):\n", encoding="utf-8")
    (case_dir / "failure.json").write_text(
        json.dumps(
            {
                "id": "0001",
                "source": "synthetic",
                "repo_language": "python",
                "category": "python_missing_dependency",
                "description": "missing dep",
                "policy_profile": "default",
                "created_at": "2026-01-20",
                "notes": "synthetic",
                "public_source_url": None,
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "success_criteria": {
                    "validation_must_pass": True,
                    "policy_violations_allowed": 0,
                    "max_danger_score": 30,
                },
                "expected_category": "python_missing_dependency",
                "allowed_fix_types": ["add_dependency"],
            }
        ),
        encoding="utf-8",
    )
    (fixture_dir / "pyproject.toml").write_text(
        "[tool.poetry]\nname='x'\n\n[tool.poetry.dependencies]\npython='^3.11'\n",
        encoding="utf-8",
    )

    case = load_case(case_dir)
    assert case.case_id == "0001"
    assert case.failure.source == "synthetic"
    assert case.expected.expected_category == "python_missing_dependency"
    assert case.repo_fixture_dir is not None


def test_load_case_requires_matching_expected_category(tmp_path: Path) -> None:
    case_dir = tmp_path / "0001"
    case_dir.mkdir(parents=True)
    (case_dir / "logs.txt").write_text("x\n", encoding="utf-8")
    (case_dir / "failure.json").write_text(
        json.dumps(
            {
                "id": "0001",
                "source": "synthetic",
                "repo_language": "python",
                "category": "python_missing_dependency",
                "description": "x",
                "policy_profile": "default",
                "created_at": "2026-01-20",
                "notes": "synthetic",
                "public_source_url": None,
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "success_criteria": {
                    "validation_must_pass": True,
                    "policy_violations_allowed": 0,
                    "max_danger_score": 30,
                },
                "expected_category": "lint_format",
                "allowed_fix_types": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_case(case_dir)


def test_repo_dataset_has_at_least_25_cases() -> None:
    dataset_path = Path("evals") / "dataset"
    cases = load_dataset(dataset_path)
    assert len(cases) >= 25
