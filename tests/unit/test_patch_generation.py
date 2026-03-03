from pathlib import Path

import pytest
from sre_agent.fix_pipeline.patch_generator import PatchGenerator
from sre_agent.schemas.fix_plan import FixOperation, FixPlan


def test_dependency_upsert_pyproject_is_deterministic(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "\n".join(
            [
                "[tool.poetry]",
                'name = "demo"',
                "",
                "[tool.poetry.dependencies]",
                'python = "^3.11"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    plan = FixPlan(
        root_cause="missing requests",
        category="python_missing_dependency",
        confidence=0.7,
        files=["pyproject.toml"],
        operations=[
            FixOperation(
                type="add_dependency",
                file="pyproject.toml",
                details={"name": "requests", "spec": "^2.31.0"},
                rationale="import error",
                evidence=["ModuleNotFoundError: requests"],
            )
        ],
    )

    gen = PatchGenerator()
    out1 = gen.generate(tmp_path, plan)
    out2 = gen.generate(tmp_path, plan)

    assert out1.diff_text == out2.diff_text
    assert "requests" in out1.diff_text
    assert out1.stats.total_files == 1
    assert out1.stats.files_changed == ["pyproject.toml"]


def test_dependency_upsert_requirements(tmp_path: Path) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text("flask==2.0.0\n", encoding="utf-8")

    plan = FixPlan(
        root_cause="missing requests",
        category="python_missing_dependency",
        confidence=0.7,
        files=["requirements.txt"],
        operations=[
            FixOperation(
                type="pin_dependency",
                file="requirements.txt",
                details={"name": "requests", "spec": "==2.31.0"},
                rationale="runtime import",
                evidence=["ModuleNotFoundError"],
            )
        ],
    )

    gen = PatchGenerator()
    out = gen.generate(tmp_path, plan)
    assert "requests==2.31.0" in out.diff_text


def test_remove_unused_import(tmp_path: Path) -> None:
    code = tmp_path / "src" / "app.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text("import os, sys\n\ndef f():\n    return sys.version\n", encoding="utf-8")

    plan = FixPlan(
        root_cause="unused import os",
        category="lint_format",
        confidence=0.6,
        files=["src/app.py"],
        operations=[
            FixOperation(
                type="remove_unused",
                file="src/app.py",
                details={"name": "os"},
                rationale="unused",
                evidence=["F401: 'os' imported but unused"],
            )
        ],
    )

    out = PatchGenerator().generate(tmp_path, plan)
    assert "import sys" in out.diff_text
    assert "+import os" not in out.diff_text


def test_operation_outside_plan_files_is_blocked(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        FixPlan(
            root_cause="x",
            category="lint_format",
            confidence=0.6,
            files=["src/app.py"],
            operations=[
                FixOperation(
                    type="remove_unused",
                    file="README.md",
                    details={"name": "os"},
                    rationale="x",
                    evidence=[],
                )
            ],
        )
