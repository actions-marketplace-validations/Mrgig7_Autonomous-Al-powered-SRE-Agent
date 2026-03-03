from sre_agent.safety.policy_engine import PolicyEngine
from sre_agent.safety.policy_loader import load_policy_from_file
from sre_agent.safety.policy_models import PlanIntent, SafetyPolicy


def test_policy_loader_supports_yaml(tmp_path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: 1",
                "paths:",
                "  allowed: ['**']",
                "  forbidden: ['.github/workflows/**']",
            ]
        ),
        encoding="utf-8",
    )

    policy = load_policy_from_file(policy_path)
    assert policy.version == 1
    assert ".github/workflows/**" in policy.paths.forbidden


def test_forbidden_path_blocks_patch() -> None:
    policy = SafetyPolicy()
    engine = PolicyEngine(policy)

    diff = "\n".join(
        [
            "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml",
            "index 1111111..2222222 100644",
            "--- a/.github/workflows/ci.yml",
            "+++ b/.github/workflows/ci.yml",
            "@@ -1 +1 @@",
            "-name: old",
            "+name: new",
            "",
        ]
    )

    decision = engine.evaluate_patch(diff)
    assert decision.allowed is False
    assert any(v.code == "forbidden_path" for v in decision.violations)
    assert decision.pr_label == "needs-review"


def test_allowed_paths_enforced() -> None:
    policy = SafetyPolicy.model_validate(
        {
            "version": 1,
            "paths": {"allowed": ["src/**"], "forbidden": []},
            "patch_limits": {
                "max_files": 5,
                "max_lines_added": 200,
                "max_lines_removed": 200,
                "max_diff_bytes": 200000,
            },
            "secrets": {"forbidden_patterns": []},
            "danger": {
                "safe_max": 20,
                "weights": {"per_file": 0, "per_50_lines_changed": 0, "per_10kb_diff": 0},
                "risky_paths": [],
            },
        }
    )
    engine = PolicyEngine(policy)

    diff = "\n".join(
        [
            "diff --git a/README.md b/README.md",
            "--- a/README.md",
            "+++ b/README.md",
            "@@ -1 +1 @@",
            "-old",
            "+new",
            "",
        ]
    )

    decision = engine.evaluate_patch(diff)
    assert decision.allowed is False
    assert any(v.code == "path_not_allowed" for v in decision.violations)


def test_secret_pattern_blocks_patch() -> None:
    policy = SafetyPolicy.model_validate(
        {
            "version": 1,
            "paths": {"allowed": ["**"], "forbidden": []},
            "secrets": {"forbidden_patterns": [r"(?i)password\s*[=:]"]},
            "patch_limits": {
                "max_files": 5,
                "max_lines_added": 200,
                "max_lines_removed": 200,
                "max_diff_bytes": 200000,
            },
            "danger": {
                "safe_max": 20,
                "weights": {"per_file": 0, "per_50_lines_changed": 0, "per_10kb_diff": 0},
                "risky_paths": [],
            },
        }
    )
    engine = PolicyEngine(policy)

    diff = "\n".join(
        [
            "diff --git a/src/app.py b/src/app.py",
            "--- a/src/app.py",
            "+++ b/src/app.py",
            "@@ -1 +1 @@",
            "-x = 1",
            "+password = 'leak'",
            "",
        ]
    )

    decision = engine.evaluate_patch(diff)
    assert decision.allowed is False
    assert any(v.code == "secret_pattern" for v in decision.violations)


def test_patch_size_limits_block() -> None:
    policy = SafetyPolicy.model_validate(
        {
            "version": 1,
            "paths": {"allowed": ["**"], "forbidden": []},
            "secrets": {"forbidden_patterns": []},
            "patch_limits": {
                "max_files": 1,
                "max_lines_added": 1,
                "max_lines_removed": 1,
                "max_diff_bytes": 200000,
            },
            "danger": {
                "safe_max": 20,
                "weights": {"per_file": 0, "per_50_lines_changed": 0, "per_10kb_diff": 0},
                "risky_paths": [],
            },
        }
    )
    engine = PolicyEngine(policy)

    diff = "\n".join(
        [
            "diff --git a/src/a.py b/src/a.py",
            "--- a/src/a.py",
            "+++ b/src/a.py",
            "@@ -1 +1 @@",
            "-a",
            "+b",
            "diff --git a/src/b.py b/src/b.py",
            "--- a/src/b.py",
            "+++ b/src/b.py",
            "@@ -1 +1 @@",
            "-a",
            "+b",
            "",
        ]
    )

    decision = engine.evaluate_patch(diff)
    assert decision.allowed is False
    assert any(v.code == "max_files" for v in decision.violations)


def test_danger_score_drives_label() -> None:
    policy = SafetyPolicy.model_validate(
        {
            "version": 1,
            "paths": {"allowed": ["**"], "forbidden": []},
            "secrets": {"forbidden_patterns": []},
            "patch_limits": {
                "max_files": 5,
                "max_lines_added": 200,
                "max_lines_removed": 200,
                "max_diff_bytes": 200000,
            },
            "danger": {
                "safe_max": 0,
                "weights": {"per_file": 5, "per_50_lines_changed": 0, "per_10kb_diff": 0},
                "risky_paths": [],
            },
        }
    )
    engine = PolicyEngine(policy)

    plan_decision = engine.evaluate_plan(PlanIntent(target_files=["src/app.py"]))
    assert plan_decision.pr_label == "needs-review"
