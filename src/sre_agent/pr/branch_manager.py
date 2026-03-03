"""Git branch manager for fix PRs.

Handles branch creation, committing, and pushing fixes.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class BranchError(Exception):
    """Error during branch operations."""

    pass


class BranchManager:
    """
    Manages git branches for fix PRs.

    Operations:
    - Create fix branches
    - Apply diffs and commit
    - Push to remote
    """

    def __init__(self, work_dir: Path | None = None):
        """
        Initialize branch manager.

        Args:
            work_dir: Working directory for git operations
        """
        self.work_dir = work_dir or Path(tempfile.gettempdir()) / "sre_branches"
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def generate_branch_name(self, fix_id: str, prefix: str = "sre-fix") -> str:
        """
        Generate a branch name for a fix.

        Args:
            fix_id: Fix identifier
            prefix: Branch name prefix

        Returns:
            Branch name like 'sre-fix/abc123'
        """
        # Sanitize fix_id for branch name
        safe_id = fix_id[:12].replace("-", "")
        return f"{prefix}/{safe_id}"

    async def clone_repo(
        self,
        repo_url: str,
        branch: str = "main",
    ) -> Path:
        """
        Clone a repository for branch operations.

        Args:
            repo_url: Repository URL
            branch: Base branch to clone

        Returns:
            Path to cloned repository
        """
        import hashlib

        repo_hash = hashlib.sha256(repo_url.encode()).hexdigest()[:8]
        repo_path = self.work_dir / repo_hash

        if repo_path.exists():
            import shutil

            shutil.rmtree(repo_path)

        logger.info(f"Cloning repository for PR: {repo_url}")

        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "-b", branch, repo_url, str(repo_path)],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                raise BranchError(f"Clone failed: {result.stderr}")

            return repo_path

        except subprocess.TimeoutExpired:
            raise BranchError("Clone timed out")

    async def create_branch(
        self,
        repo_path: Path,
        branch_name: str,
    ) -> bool:
        """
        Create a new branch.

        Args:
            repo_path: Path to repository
            branch_name: Name for new branch

        Returns:
            True if successful
        """
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise BranchError(f"Failed to create branch: {result.stderr}")

        logger.info(f"Created branch: {branch_name}")
        return True

    async def apply_diff(
        self,
        repo_path: Path,
        diff: str,
    ) -> bool:
        """
        Apply a diff to the repository.

        Args:
            repo_path: Path to repository
            diff: Unified diff to apply

        Returns:
            True if successful
        """
        diff_file = repo_path / ".fix.patch"

        try:
            diff_file.write_text(diff)

            result = subprocess.run(
                ["git", "apply", str(diff_file)],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                raise BranchError(f"Failed to apply diff: {result.stderr}")

            return True

        finally:
            if diff_file.exists():
                diff_file.unlink()

    async def commit(
        self,
        repo_path: Path,
        message: str,
        author_name: str = "SRE Agent",
        author_email: str = "sre-agent@example.com",
    ) -> str:
        """
        Commit changes.

        Args:
            repo_path: Path to repository
            message: Commit message
            author_name: Commit author name
            author_email: Commit author email

        Returns:
            Commit SHA
        """
        # Stage all changes
        subprocess.run(
            ["git", "add", "-A"],
            cwd=repo_path,
            capture_output=True,
        )

        # Commit
        env = {
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }

        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo_path,
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, **env},
        )

        if result.returncode != 0:
            raise BranchError(f"Failed to commit: {result.stderr}")

        # Get commit SHA
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )

        return sha_result.stdout.strip()

    async def push_branch(
        self,
        repo_path: Path,
        branch_name: str,
        remote: str = "origin",
    ) -> bool:
        """
        Push branch to remote.

        Args:
            repo_path: Path to repository
            branch_name: Branch to push
            remote: Remote name

        Returns:
            True if successful
        """
        result = subprocess.run(
            ["git", "push", "-u", remote, branch_name],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise BranchError(f"Failed to push: {result.stderr}")

        logger.info(f"Pushed branch: {branch_name}")
        return True

    async def cleanup(self, repo_path: Path) -> None:
        """Clean up repository."""
        import shutil

        if repo_path.exists():
            try:
                shutil.rmtree(repo_path)
            except Exception as e:
                logger.warning(f"Failed to cleanup: {e}")
