from __future__ import annotations

import re
from pathlib import Path

from sre_agent.adapters.base import BaseAdapter, DetectionResult, ValidationStep


class JavaAdapter(BaseAdapter):
    name = "java"
    supported_languages = ["java"]

    def detect(self, log_text: str, repo_files: list[str]) -> DetectionResult | None:
        has_maven = any(p.endswith("pom.xml") for p in repo_files)
        has_gradle = any(
            p.endswith("build.gradle") or p.endswith("build.gradle.kts") for p in repo_files
        )
        looks_like_java = (
            "mvn" in log_text
            or "gradle" in log_text
            or "Could not resolve dependencies" in log_text
        )
        if not (has_maven or has_gradle or looks_like_java):
            return None

        evidence: list[str] = []
        category = "java_unknown"
        confidence = 0.6 if (has_maven or has_gradle) else 0.35

        missing_version = re.search(
            r"dependencies\.dependency\.version.*?for\s+([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)\s+is missing",
            log_text,
        )
        if missing_version:
            evidence.append(missing_version.group(0))
            category = "java_dependency_version_missing"
            confidence = 0.85
        else:
            plugin_missing = re.search(
                r"Plugin\s+([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)\s+or one of its dependencies could not be resolved",
                log_text,
            )
            if plugin_missing:
                evidence.append(plugin_missing.group(0))
                category = "java_plugin_version_missing"
                confidence = 0.75

        for line in log_text.splitlines():
            s = line.strip()
            if (
                "[ERROR]" in s and "Could not resolve dependencies" in s
            ) or "Could not find artifact" in s:
                evidence.append(s)
                confidence = max(confidence, 0.6)
                break

        return DetectionResult(
            repo_language="java",
            category=category,
            evidence_lines=evidence[:8],
            confidence=confidence,
        )

    def build_validation_steps(self, repo_root: str) -> list[ValidationStep]:
        root = Path(repo_root)
        if (root / "pom.xml").exists():
            return [ValidationStep(name="mvn test", command="mvn -q test")]
        if (root / "gradlew").exists():
            return [ValidationStep(name="gradle test", command="./gradlew test")]
        return [ValidationStep(name="gradle test", command="gradle test")]

    def allowed_fix_types(self) -> set[str]:
        return {"pin_dependency", "update_config"}

    def allowed_categories(self) -> set[str]:
        return {"java_dependency_version_missing", "java_plugin_version_missing"}
