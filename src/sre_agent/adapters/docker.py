from __future__ import annotations

import re

from sre_agent.adapters.base import BaseAdapter, DetectionResult, ValidationStep


class DockerAdapter(BaseAdapter):
    name = "docker"
    supported_languages = ["docker"]

    def detect(self, log_text: str, repo_files: list[str]) -> DetectionResult | None:
        has_dockerfile = any(p.endswith("Dockerfile") for p in repo_files)
        looks_like_docker = "failed to solve" in log_text or "docker build" in log_text
        if not (has_dockerfile or looks_like_docker):
            return None

        evidence: list[str] = []
        category = "docker_unknown"
        confidence = 0.65 if has_dockerfile else 0.35

        for line in log_text.splitlines():
            s = line.strip()
            if "failed to solve" in s or "Dockerfile" in s:
                evidence.append(s)
                confidence = max(confidence, 0.65)
            if "apt-get" in s and ("failed" in s or "Unable to locate package" in s):
                evidence.append(s)
                category = "docker_apt_get_cleanup"
                confidence = 0.75
                break
            if re.search(r"pull access denied|manifest for .* not found|not found: manifest", s):
                evidence.append(s)
                category = "docker_pin_base_image"
                confidence = 0.75
                break

        return DetectionResult(
            repo_language="docker",
            category=category,
            evidence_lines=evidence[:8],
            confidence=confidence,
        )

    def build_validation_steps(self, repo_root: str) -> list[ValidationStep]:
        return [ValidationStep(name="docker build", command="docker build -t sre-agent-validate .")]

    def allowed_fix_types(self) -> set[str]:
        return {"update_config"}

    def allowed_categories(self) -> set[str]:
        return {"docker_pin_base_image", "docker_apt_get_cleanup"}
