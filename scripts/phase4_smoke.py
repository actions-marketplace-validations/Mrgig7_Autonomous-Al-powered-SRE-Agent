import asyncio
import secrets
import subprocess
import tempfile
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path
from uuid import uuid4

from sre_agent.artifacts.provenance import build_provenance_artifact
from sre_agent.database import get_async_session
from sre_agent.fix_pipeline.store import FixPipelineRunStore
from sre_agent.models.events import PipelineEvent
from sre_agent.sandbox.validator import ValidationOrchestrator
from sre_agent.schemas.validation import ValidationRequest


def _run(cmd: list[str], cwd: Path) -> str:
    out = subprocess.check_output(cmd, cwd=cwd, text=True)
    return out.strip()


def _make_local_repo() -> tuple[Path, str, str, str]:
    repo_dir = Path(tempfile.gettempdir()) / f"sre_agent_phase4_smoke_{uuid4().hex[:8]}"
    repo_dir.mkdir(parents=True, exist_ok=True)

    _run(["git", "init"], repo_dir)
    _run(["git", "config", "user.email", "smoke@example.com"], repo_dir)
    _run(["git", "config", "user.name", "Smoke Test"], repo_dir)

    aws_access_key = "AKIA" + "".join(
        secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(16)
    )
    aws_secret_key = secrets.token_urlsafe(32)[:40]
    (repo_dir / ".env").write_text(
        f"AWS_ACCESS_KEY_ID={aws_access_key}\nAWS_SECRET_ACCESS_KEY={aws_secret_key}\n",
        encoding="utf-8",
    )

    app_py = repo_dir / "src" / "app.py"
    app_py.parent.mkdir(parents=True, exist_ok=True)
    before = "def add(a: int, b: int) -> int:\n    return a + b\n"
    after = "def add(a: int, b: int) -> int:\n    return a + b\n\n"
    app_py.write_text(before, encoding="utf-8")

    _run(["git", "add", "."], repo_dir)
    _run(["git", "commit", "-m", "init"], repo_dir)

    commit_sha = _run(["git", "rev-parse", "HEAD"], repo_dir)
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_dir)

    diff_lines = list(
        unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/src/app.py",
            tofile="b/src/app.py",
            lineterm="\n",
        )
    )
    diff = "diff --git a/src/app.py b/src/app.py\n" + "".join(diff_lines)

    return repo_dir, branch, commit_sha, diff


async def main() -> None:
    repo_dir, branch, commit_sha, diff = _make_local_repo()

    validator = ValidationOrchestrator()
    validation = await validator.validate(
        ValidationRequest(
            fix_id=str(uuid4()),
            event_id=uuid4(),
            repo_url=str(repo_dir),
            branch=branch,
            commit_sha=commit_sha,
            diff=diff,
        )
    )

    store = FixPipelineRunStore()
    async with get_async_session() as session:
        event = PipelineEvent(
            idempotency_key=f"smoke:{uuid4().hex}",
            ci_provider="github_actions",
            raw_payload={"repository": {"clone_url": str(repo_dir)}},
            pipeline_id="smoke",
            repo="local/smoke",
            commit_sha=commit_sha,
            branch=branch,
            stage="ci",
            failure_type="test",
            error_message="phase4_smoke",
            status="completed",
            event_timestamp=datetime.now(UTC),
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)

    run_id = await store.create_run(event_id=event.id, context_json=None, rca_json=None)
    await store.update_run(
        run_id,
        status="validation_failed" if not validation.is_successful else "validation_passed",
        validation_json=validation.model_dump(mode="json"),
        error_message=validation.error_message,
        plan_json={
            "root_cause": "smoke",
            "category": "lint_format",
            "confidence": 0.1,
            "files": ["src/app.py"],
            "operations": [],
        },
        patch_stats_json={
            "total_files": 1,
            "lines_added": 1,
            "lines_removed": 0,
            "files_changed": ["src/app.py"],
        },
    )

    latest = await store.get_run(run_id)
    artifact = build_provenance_artifact(
        run_id=latest.id,
        failure_id=latest.event_id,
        repo="local/smoke",
        status=str(latest.status),
        started_at=latest.created_at,
        error_message=latest.error_message,
        plan_json=latest.plan_json,
        plan_policy_json=latest.plan_policy_json,
        patch_stats_json=latest.patch_stats_json,
        patch_policy_json=latest.patch_policy_json,
        validation_json=latest.validation_json,
    )
    await store.update_run(run_id, artifact_json=artifact.model_dump(mode="json"))

    print(run_id)


if __name__ == "__main__":
    asyncio.run(main())
