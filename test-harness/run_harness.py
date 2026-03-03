from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = Path(__file__).resolve().parent
SAMPLE_APP_ROOT = HARNESS_ROOT / "sample-app"
REPORTS_DIR = HARNESS_ROOT / "reports"
TESTING_AGENT_VALIDATOR = HARNESS_ROOT / "testing-agent" / "validator.py"

sys.path.insert(0, str(SAMPLE_APP_ROOT))
from failure_injections import inject_failure  # noqa: E402


@dataclass
class HarnessSettings:
    github_token: str
    github_owner: str
    sre_base_url: str
    sre_webhook_url: str
    sre_auth_email: str
    sre_auth_password: str
    github_api_base_url: str = "https://api.github.com"
    github_webhook_secret: str | None = None


@dataclass
class FailureExecution:
    failure_id: int
    branch: str
    workflow_run_url: str | None
    workflow_conclusion: str | None
    validator_exit_code: int
    validator_report_path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real-time SRE test harness")
    parser.add_argument("--failures", default="all", help="Comma-separated failure IDs or 'all'")
    parser.add_argument(
        "--repo-name", default=None, help="Override generated GitHub repository name"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Do not push/trigger, just print plan"
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="Delete auto-created repository at the end"
    )
    return parser.parse_args()


def load_settings() -> HarnessSettings:
    missing = []
    required = [
        "GITHUB_TOKEN",
        "SRE_BASE_URL",
        "SRE_WEBHOOK_URL",
        "SRE_AUTH_EMAIL",
        "SRE_AUTH_PASSWORD",
    ]
    for key in required:
        if not os.getenv(key):
            missing.append(key)
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    return HarnessSettings(
        github_token=os.environ["GITHUB_TOKEN"],
        github_owner=os.getenv("GITHUB_OWNER", ""),
        sre_base_url=os.environ["SRE_BASE_URL"].rstrip("/"),
        sre_webhook_url=os.environ["SRE_WEBHOOK_URL"].rstrip("/"),
        sre_auth_email=os.environ["SRE_AUTH_EMAIL"],
        sre_auth_password=os.environ["SRE_AUTH_PASSWORD"],
        github_api_base_url=os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/"),
        github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET"),
    )


def parse_failure_ids(raw: str) -> list[int]:
    if raw.strip().lower() == "all":
        return list(range(1, 10))
    ids = []
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        item = int(value)
        if item < 1 or item > 9:
            raise ValueError(f"Failure ID must be between 1 and 9. Found: {item}")
        ids.append(item)
    if not ids:
        raise ValueError("No failure IDs selected")
    return sorted(set(ids))


def run_cmd(cmd: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Command failed: "
            + " ".join(cmd)
            + "\nSTDOUT:\n"
            + result.stdout
            + "\nSTDERR:\n"
            + result.stderr
        )


class GitHubHarnessClient:
    def __init__(self, settings: HarnessSettings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=settings.github_api_base_url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {settings.github_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self.client.close()

    def create_repository(self, name: str) -> dict[str, Any]:
        response = self.client.post(
            "/user/repos",
            json={"name": name, "private": True, "auto_init": False},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected GitHub response for repository creation")
        return payload

    def delete_repository(self, full_name: str) -> None:
        response = self.client.delete(f"/repos/{full_name}")
        if response.status_code not in (204, 202, 404):
            response.raise_for_status()

    def create_webhook(self, full_name: str, webhook_url: str) -> None:
        config: dict[str, Any] = {
            "url": webhook_url,
            "content_type": "json",
        }
        if self.settings.github_webhook_secret:
            config["secret"] = self.settings.github_webhook_secret

        payload = {
            "name": "web",
            "active": True,
            "events": ["workflow_job", "workflow_run"],
            "config": config,
        }
        response = self.client.post(f"/repos/{full_name}/hooks", json=payload)
        if response.status_code == 422:
            return
        response.raise_for_status()

    def wait_for_failed_workflow(
        self, full_name: str, branch: str, timeout_seconds: int = 600
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            response = self.client.get(
                f"/repos/{full_name}/actions/runs",
                params={"branch": branch, "event": "push", "per_page": 5},
            )
            response.raise_for_status()
            payload = response.json()
            runs = payload.get("workflow_runs", [])
            if runs:
                run = runs[0]
                status = run.get("status")
                conclusion = run.get("conclusion")  # noqa: F841
                if status == "completed":
                    return run
            time.sleep(8)
        raise TimeoutError(f"Timed out waiting for workflow run completion for branch {branch}")


def prepare_repo_workdir(sample_source: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="sre-harness-"))
    sample_dest = temp_dir / "sample-app"
    shutil.copytree(sample_source, sample_dest)
    return sample_dest


def initialize_git_repo(repo_dir: Path) -> str:
    run_cmd(["git", "init"], cwd=repo_dir)
    run_cmd(["git", "config", "user.email", "sre-harness@example.com"], cwd=repo_dir)
    run_cmd(["git", "config", "user.name", "SRE Harness"], cwd=repo_dir)
    run_cmd(["git", "add", "."], cwd=repo_dir)
    run_cmd(["git", "commit", "-m", "baseline: healthy sample app"], cwd=repo_dir)
    run_cmd(["git", "branch", "-M", "main"], cwd=repo_dir)
    baseline_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True
    ).strip()
    return baseline_sha


def configure_remote(repo_dir: Path, full_name: str, token: str) -> str:
    remote_url = f"https://x-access-token:{token}@github.com/{full_name}.git"
    run_cmd(["git", "remote", "add", "origin", remote_url], cwd=repo_dir)
    return remote_url


def create_failure_branch(repo_dir: Path, baseline_sha: str, failure_id: int) -> str:
    branch = f"failure-{failure_id:02d}"
    run_cmd(["git", "checkout", "-B", branch, baseline_sha], cwd=repo_dir)
    summary = inject_failure(repo_dir, failure_id)
    run_cmd(["git", "add", "."], cwd=repo_dir)
    run_cmd(["git", "commit", "-m", f"inject failure #{failure_id}: {summary}"], cwd=repo_dir)
    return branch


def run_validator(
    *,
    failure_id: int,
    branch: str,
    repository: str,
    settings: HarnessSettings,
    output_path: Path,
) -> int:
    env = os.environ.copy()
    env.update(
        {
            "SRE_BASE_URL": settings.sre_base_url,
            "SRE_AUTH_EMAIL": settings.sre_auth_email,
            "SRE_AUTH_PASSWORD": settings.sre_auth_password,
            "GITHUB_TOKEN": settings.github_token,
            "GITHUB_OWNER": repository.split("/", 1)[0],
            "GITHUB_REPO": repository.split("/", 1)[1],
            "GITHUB_API_BASE_URL": settings.github_api_base_url,
        }
    )
    cmd = [
        sys.executable,
        str(TESTING_AGENT_VALIDATOR),
        "--failure-id",
        str(failure_id),
        "--branch",
        branch,
        "--repository",
        repository,
        "--output",
        str(output_path),
    ]
    result = subprocess.run(cmd, env=env)
    return result.returncode


def main() -> int:
    args = parse_args()
    failure_ids = parse_failure_ids(args.failures)

    if args.dry_run:
        print("Dry-run plan:")
        print(f"- failures: {failure_ids}")
        print("- create GitHub repository")
        print("- push baseline and failure branches")
        print("- wait for failed CI runs")
        print("- run testing-agent validator per branch")
        print("- write report JSON")
        return 0

    settings = load_settings()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    repo_name = args.repo_name or f"sre-harness-{int(time.time())}"
    gh = GitHubHarnessClient(settings)
    created_repo_name = ""
    workdir: Path | None = None
    executions: list[FailureExecution] = []
    try:
        repo_info = gh.create_repository(repo_name)
        created_repo_name = str(repo_info["full_name"])
        gh.create_webhook(created_repo_name, settings.sre_webhook_url)

        workdir = prepare_repo_workdir(SAMPLE_APP_ROOT)
        baseline_sha = initialize_git_repo(workdir)
        configure_remote(workdir, created_repo_name, settings.github_token)
        run_cmd(["git", "push", "-u", "origin", "main"], cwd=workdir)

        for failure_id in failure_ids:
            branch = create_failure_branch(workdir, baseline_sha, failure_id)
            run_cmd(["git", "push", "-u", "origin", branch, "--force"], cwd=workdir)
            workflow = gh.wait_for_failed_workflow(created_repo_name, branch)
            validator_report = REPORTS_DIR / f"validator-failure-{failure_id:02d}.json"
            validator_code = run_validator(
                failure_id=failure_id,
                branch=branch,
                repository=created_repo_name,
                settings=settings,
                output_path=validator_report,
            )
            executions.append(
                FailureExecution(
                    failure_id=failure_id,
                    branch=branch,
                    workflow_run_url=workflow.get("html_url"),
                    workflow_conclusion=workflow.get("conclusion"),
                    validator_exit_code=validator_code,
                    validator_report_path=str(validator_report.relative_to(REPO_ROOT)),
                )
            )

        report = {
            "generated_at": int(time.time()),
            "repository": created_repo_name,
            "settings": {
                "sre_base_url": settings.sre_base_url,
                "sre_webhook_url": settings.sre_webhook_url,
                "github_api_base_url": settings.github_api_base_url,
            },
            "executions": [asdict(item) for item in executions],
            "all_validations_passed": all(item.validator_exit_code == 0 for item in executions),
        }
        report_path = REPORTS_DIR / "harness-report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Harness report: {report_path}")
        return 0 if report["all_validations_passed"] else 1
    finally:
        gh.close()
        if args.cleanup and created_repo_name:
            gh = GitHubHarnessClient(settings)
            try:
                gh.delete_repository(created_repo_name)
            finally:
                gh.close()
        if workdir is not None and workdir.exists():
            shutil.rmtree(workdir.parent, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
