"""AST safety gate for source-level validation before and after patching."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AstIssue:
    file: str
    phase: str
    message: str


@dataclass(frozen=True)
class AstCheckResult:
    passed: bool
    checked_files: list[str]
    issues: list[AstIssue]


def validate_python_ast(*, repo_path: Path, touched_files: list[str]) -> AstCheckResult:
    """Validate AST parseability for all touched Python files.

    The check is intentionally conservative: any parse failure blocks the pipeline.
    """
    checked: list[str] = []
    issues: list[AstIssue] = []

    for rel_path in sorted(set(touched_files)):
        if not rel_path.endswith(".py"):
            continue
        checked.append(rel_path)
        abs_path = repo_path / rel_path
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            issues.append(
                AstIssue(
                    file=rel_path,
                    phase="post_patch_read",
                    message=f"Failed to read file for AST validation: {exc}",
                )
            )
            continue

        try:
            ast.parse(content, filename=rel_path)
        except SyntaxError as exc:
            issues.append(
                AstIssue(
                    file=rel_path,
                    phase="post_patch_parse",
                    message=f"SyntaxError at line {exc.lineno}: {exc.msg}",
                )
            )
        except Exception as exc:
            issues.append(
                AstIssue(
                    file=rel_path,
                    phase="post_patch_parse",
                    message=f"AST parsing failed: {exc}",
                )
            )

    return AstCheckResult(
        passed=not issues,
        checked_files=checked,
        issues=issues,
    )
