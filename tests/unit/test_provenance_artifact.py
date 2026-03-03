from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sre_agent.artifacts.provenance import build_provenance_artifact


def test_build_provenance_redacts_secrets() -> None:
    run_id = uuid4()
    event_id = uuid4()

    artifact = build_provenance_artifact(
        run_id=run_id,
        failure_id=event_id,
        repo="acme/repo",
        status="validation_failed",
        started_at=datetime(2026, 1, 20, tzinfo=UTC),
        error_message='token="ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
        plan_json={
            "root_cause": 'token="ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
            "category": "python_missing_dependency",
            "confidence": 0.5,
            "files": ["pyproject.toml"],
            "operations": [
                {
                    "type": "add_dependency",
                    "file": "pyproject.toml",
                    "details": {"name": "requests", "spec": "^2.31.0"},
                    "rationale": 'fix token="ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
                    "evidence": ["ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
                }
            ],
        },
        plan_policy_json={
            "allowed": True,
            "danger_score": 10,
            "pr_label": "safe",
            "violations": [],
        },
        patch_stats_json={"total_files": 1, "lines_added": 1, "lines_removed": 0},
        patch_policy_json=None,
        validation_json=None,
    )

    dumped = artifact.model_dump(mode="json")
    text = str(dumped)
    assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in text
    assert "[REDACTED]" in text
