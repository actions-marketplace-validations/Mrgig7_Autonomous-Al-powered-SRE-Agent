from __future__ import annotations

import json
from pathlib import Path

from sre_agent.adapters.base import BaseAdapter, DetectionResult, ValidationStep


class NodeAdapter(BaseAdapter):
    name = "node"
    supported_languages = ["javascript", "typescript"]

    def detect(self, log_text: str, repo_files: list[str]) -> DetectionResult | None:
        has_package_json = any(p.endswith("package.json") for p in repo_files)
        looks_like_node = (
            "npm ERR!" in log_text or "Cannot find module" in log_text or "ERR_PNPM" in log_text
        )
        if not (has_package_json or looks_like_node):
            return None

        evidence: list[str] = []
        category = "node_unknown"
        confidence = 0.55 if has_package_json else 0.35

        for line in log_text.splitlines():
            s = line.strip()
            if "npm ERR!" in s or "ERR_PNPM" in s:
                evidence.append(s)
                confidence = max(confidence, 0.6)
            if "Cannot find module" in s or "ERR_MODULE_NOT_FOUND" in s:
                evidence.append(s)
                category = "node_missing_dependency"
                confidence = 0.9
                break

        if category == "node_unknown":
            for line in log_text.splitlines():
                s = line.strip()
                if "package-lock.json" in s and ("out of date" in s or "npm ci" in s):
                    evidence.append(s)
                    category = "node_lockfile_mismatch"
                    confidence = 0.75
                    break

        return DetectionResult(
            repo_language="node",
            category=category,
            evidence_lines=evidence[:8],
            confidence=confidence,
        )

    def build_validation_steps(self, repo_root: str) -> list[ValidationStep]:
        steps = [
            ValidationStep(name="npm ci", command="npm ci"),
            ValidationStep(name="npm test", command="npm test"),
        ]
        pkg = Path(repo_root) / "package.json"
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") if isinstance(data, dict) else None
            if isinstance(scripts, dict) and scripts.get("lint"):
                steps.append(ValidationStep(name="npm run lint", command="npm run lint"))
        except Exception:
            pass
        return steps

    def allowed_fix_types(self) -> set[str]:
        return {"add_dependency", "pin_dependency", "update_config"}

    def allowed_categories(self) -> set[str]:
        return {"node_missing_dependency", "node_lockfile_mismatch"}

    def deterministic_patch(self, plan, repo_root: str) -> str | None:
        return None
