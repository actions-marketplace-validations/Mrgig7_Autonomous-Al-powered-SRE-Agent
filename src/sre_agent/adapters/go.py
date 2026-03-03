from __future__ import annotations

import re

from sre_agent.adapters.base import BaseAdapter, DetectionResult, ValidationStep


class GoAdapter(BaseAdapter):
    name = "go"
    supported_languages = ["go"]

    def detect(self, log_text: str, repo_files: list[str]) -> DetectionResult | None:
        has_go_mod = any(p.endswith("go.mod") for p in repo_files)
        looks_like_go = "go test" in log_text or "go: " in log_text or "go.mod" in log_text
        if not (has_go_mod or looks_like_go):
            return None

        evidence: list[str] = []
        category = "go_unknown"
        confidence = 0.6 if has_go_mod else 0.35

        for line in log_text.splitlines():
            s = line.strip()
            if "missing go.sum entry" in s:
                evidence.append(s)
                category = "go_mod_tidy"
                confidence = 0.85
                break

        if category == "go_unknown":
            m = re.search(r"no required module provides package\s+([^\s;]+)", log_text)
            if m:
                evidence.append(m.group(0))
                category = "go_add_missing_module"
                confidence = 0.8

        if category == "go_unknown":
            for line in log_text.splitlines():
                s = line.strip()
                if s.startswith("go: ") and "module" in s and "found" in s:
                    evidence.append(s)
                    confidence = max(confidence, 0.6)
                    break

        return DetectionResult(
            repo_language="go",
            category=category,
            evidence_lines=evidence[:8],
            confidence=confidence,
        )

    def build_validation_steps(self, repo_root: str) -> list[ValidationStep]:
        return [
            ValidationStep(name="go mod tidy", command="go mod tidy"),
            ValidationStep(name="go test", command="go test ./..."),
        ]

    def allowed_fix_types(self) -> set[str]:
        return {"update_config", "pin_dependency"}

    def allowed_categories(self) -> set[str]:
        return {"go_mod_tidy", "go_add_missing_module"}
