"""Repository manager for cloning and patching.

Handles git operations and diff application.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

from sre_agent.schemas.validation import PatchResult

logger = logging.getLogger(__name__)


class RepoError(Exception):
    """Error during repository operations."""

    pass


class RepoManager:
    """
    Manages repository cloning and patching.

    Operations:
    - Clone repository at specific commit
    - Apply unified diffs
    - Validate patches before applying
    """

    def __init__(self, base_dir: Path | None = None):
        """
        Initialize repository manager.

        Args:
            base_dir: Base directory for cloned repos (uses temp if not provided)
        """
        self.base_dir = base_dir or Path(tempfile.gettempdir()) / "sre_repos"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def clone(
        self,
        repo_url: str,
        branch: str = "main",
        commit: str | None = None,
        depth: int = 1,
    ) -> Path:
        """
        Clone a repository.

        Args:
            repo_url: Repository URL (HTTPS or SSH)
            branch: Branch to clone
            commit: Specific commit to checkout (optional)
            depth: Clone depth (1 for shallow)

        Returns:
            Path to cloned repository
        """
        import hashlib

        # Generate unique directory name
        repo_hash = hashlib.sha256(f"{repo_url}:{branch}:{commit}".encode()).hexdigest()[:12]
        repo_path = self.base_dir / repo_hash

        logger.info(
            "Cloning repository",
            extra={
                "url": repo_url,
                "branch": branch,
                "commit": commit,
                "path": str(repo_path),
            },
        )

        # Clean existing if present
        if repo_path.exists():
            import shutil

            shutil.rmtree(repo_path)

        try:
            # Clone command
            clone_cmd = [
                "git",
                "clone",
                "--depth",
                str(depth),
                "--branch",
                branch,
                repo_url,
                str(repo_path),
            ]

            result = subprocess.run(
                clone_cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                raise RepoError(f"Clone failed: {result.stderr}")

            # Checkout specific commit if provided
            if commit:
                checkout_cmd = ["git", "checkout", commit]
                result = subprocess.run(
                    checkout_cmd,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    logger.warning(f"Checkout failed, using HEAD: {result.stderr}")

            logger.info("Repository cloned successfully")
            return repo_path

        except subprocess.TimeoutExpired:
            raise RepoError("Clone timed out")
        except Exception as e:
            raise RepoError(f"Clone failed: {e}")

    def apply_patch(
        self,
        repo_path: Path,
        diff: str,
        check_only: bool = False,
    ) -> PatchResult:
        """
        Apply a unified diff to the repository.

        Args:
            repo_path: Path to repository
            diff: Unified diff content
            check_only: If True, only check if patch applies (dry run)

        Returns:
            PatchResult with application status
        """
        logger.info(
            "Applying patch",
            extra={
                "repo": str(repo_path),
                "diff_size": len(diff),
                "check_only": check_only,
            },
        )

        # Write diff to temp file
        diff_file = repo_path / ".patch"
        try:
            diff_file.write_text(diff)

            # Build patch command
            patch_cmd = ["git", "apply"]
            if check_only:
                patch_cmd.append("--check")
            patch_cmd.extend(["--verbose", str(diff_file)])

            result = subprocess.run(
                patch_cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Parse output
            files_modified = self._parse_modified_files(result.stdout + result.stderr)
            hunks_applied, hunks_failed = self._count_hunks(result.stdout + result.stderr)

            if result.returncode == 0:
                return PatchResult(
                    success=True,
                    files_modified=files_modified,
                    hunks_applied=hunks_applied,
                    hunks_failed=0,
                )
            else:
                return PatchResult(
                    success=False,
                    files_modified=files_modified,
                    hunks_applied=hunks_applied,
                    hunks_failed=hunks_failed or 1,
                    error_message=result.stderr,
                )

        except subprocess.TimeoutExpired:
            return PatchResult(
                success=False,
                error_message="Patch application timed out",
            )
        except Exception as e:
            return PatchResult(
                success=False,
                error_message=str(e),
            )
        finally:
            if diff_file.exists():
                diff_file.unlink()

    def validate_patch(self, diff: str) -> bool:
        """
        Validate that a diff has correct syntax.

        Args:
            diff: Unified diff content

        Returns:
            True if diff is valid
        """
        if not diff.strip():
            return False

        lines = diff.strip().split("\n")

        # Check for required headers
        has_old = any(line.startswith("---") for line in lines[:10])
        has_new = any(line.startswith("+++") for line in lines[:10])
        has_hunk = any(line.startswith("@@") for line in lines)

        return has_old and has_new and has_hunk

    def _parse_modified_files(self, output: str) -> list[str]:
        """Parse modified files from git apply output."""
        files = []
        for line in output.split("\n"):
            # Look for file paths in output
            if "Applying:" in line or "patching file" in line:
                parts = line.split()
                if len(parts) >= 2:
                    files.append(parts[-1])
            elif line.startswith("diff --git"):
                parts = line.split()
                if len(parts) >= 4:
                    # Get b/path
                    path = parts[-1].lstrip("b/")
                    files.append(path)
        return list(set(files))

    def _count_hunks(self, output: str) -> tuple[int, int]:
        """Count applied and failed hunks from output."""
        applied = output.lower().count("applied")
        failed = output.lower().count("failed") + output.lower().count("rejected")
        return applied, failed

    def get_test_files(self, repo_path: Path) -> list[Path]:
        """
        Find test files in the repository.

        Args:
            repo_path: Path to repository

        Returns:
            List of test file paths
        """
        test_patterns = [
            "**/test_*.py",
            "**/*_test.py",
            "**/tests/*.py",
            "**/*.test.js",
            "**/*.spec.js",
            "**/*.test.ts",
            "**/*.spec.ts",
            "**/*_test.go",
        ]

        test_files = []
        for pattern in test_patterns:
            test_files.extend(repo_path.glob(pattern))

        # Filter out __pycache__ and node_modules
        test_files = [
            f for f in test_files if "__pycache__" not in str(f) and "node_modules" not in str(f)
        ]

        return test_files

    def cleanup(self, repo_path: Path) -> None:
        """Remove cloned repository."""
        import shutil

        if repo_path.exists():
            try:
                shutil.rmtree(repo_path)
                logger.debug(f"Cleaned up repo: {repo_path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup repo: {e}")
