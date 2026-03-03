from __future__ import annotations

import re

from sre_agent.adapters.base import BaseAdapter, DetectionResult, ValidationStep


class PythonAdapter(BaseAdapter):
    name = "python"
    supported_languages = ["python"]

    def detect(self, log_text: str, repo_files: list[str]) -> DetectionResult | None:
        has_pyproject = any(p.endswith("pyproject.toml") for p in repo_files)
        has_requirements = any(p.endswith("requirements.txt") for p in repo_files)
        looks_like_python = (
            "Traceback (most recent call last)" in log_text or "ModuleNotFoundError" in log_text
        )
        if not (has_pyproject or has_requirements or looks_like_python):
            return None

        evidence: list[str] = []
        category = "unknown"
        confidence = 0.55 if (has_pyproject or has_requirements) else 0.35

        patterns = [
            r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]",
            r"No module named ['\"]([^'\"]+)['\"]",
        ]
        for line in log_text.splitlines():
            for pat in patterns:
                if re.search(pat, line):
                    evidence.append(line.strip())
                    category = "python_missing_dependency"
                    confidence = 0.9
                    break
            if confidence >= 0.9:
                break

        if category == "unknown":
            for line in log_text.splitlines():
                if "F401:" in line and "imported but unused" in line:
                    evidence.append(line.strip())
                    category = "lint_format"
                    confidence = 0.7
                    break

        return DetectionResult(
            repo_language="python",
            category=category,
            evidence_lines=evidence[:5],
            confidence=confidence,
        )

    def build_validation_steps(self, repo_root: str) -> list[ValidationStep]:
        return []

    def allowed_fix_types(self) -> set[str]:
        return {"add_dependency", "pin_dependency", "remove_unused"}

    def allowed_categories(self) -> set[str]:
        return {"python_missing_dependency", "lint_format"}
