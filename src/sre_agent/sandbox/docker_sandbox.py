"""Docker sandbox manager.

Creates isolated Docker containers for safe fix validation.
"""

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from sre_agent.schemas.validation import CommandResult, SandboxConfig

logger = logging.getLogger(__name__)

# Check if docker SDK is available
try:
    import docker

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    logger.warning("docker SDK not installed. Install with: pip install docker")


class SandboxError(Exception):
    """Error during sandbox operations."""

    pass


class DockerSandbox:
    """
    Manages isolated Docker containers for fix validation.

    Features:
    - Network isolation
    - Resource limits (CPU, memory)
    - Automatic cleanup
    - Timeout handling
    """

    def __init__(self, config: SandboxConfig | None = None):
        """
        Initialize sandbox manager.

        Args:
            config: Sandbox configuration
        """
        self.config = config or SandboxConfig()
        self._client: Any = None
        self._container: Any = None
        self._temp_dir: Path | None = None

    async def __aenter__(self) -> "DockerSandbox":
        """Enter async context and prepare sandbox."""
        await self._init_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit and cleanup."""
        await self.cleanup()

    async def _init_client(self) -> None:
        """Initialize Docker client."""
        if not DOCKER_AVAILABLE:
            raise SandboxError("Docker SDK not available. Install with: pip install docker")

        try:
            self._client = docker.from_env()
            # Test connection
            self._client.ping()
        except Exception as e:
            raise SandboxError(f"Failed to connect to Docker: {e}")

    async def create(
        self,
        workspace_path: Path | None = None,
    ) -> None:
        """
        Create a sandbox container.

        Args:
            workspace_path: Path to mount as workspace (optional)
        """
        if self._client is None:
            await self._init_client()

        # Create temp directory if no workspace provided
        if workspace_path is None:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="sre_sandbox_"))
            workspace_path = self._temp_dir

        container_name = f"sre_sandbox_{uuid4().hex[:8]}"

        logger.info(
            "Creating sandbox container",
            extra={
                "name": container_name,
                "image": self.config.docker_image,
                "workspace": str(workspace_path),
            },
        )

        try:
            # Pull image if needed
            try:
                self._client.images.get(self.config.docker_image)
            except docker.errors.ImageNotFound:
                logger.info(f"Pulling image: {self.config.docker_image}")
                self._client.images.pull(self.config.docker_image)

            # Container configuration
            container_config = {
                "image": self.config.docker_image,
                "name": container_name,
                "detach": True,
                "tty": True,
                "working_dir": self.config.working_dir,
                "volumes": {
                    str(workspace_path): {
                        "bind": self.config.working_dir,
                        "mode": "rw",
                    }
                },
                "environment": self.config.env_vars,
                "mem_limit": self.config.memory_limit,
                "cpu_period": 100000,
                "cpu_quota": int(self.config.cpu_limit * 100000),
                "network_disabled": not self.config.network_enabled,
                # Security settings
                "security_opt": ["no-new-privileges"],
                "cap_drop": ["ALL"],
                "read_only": False,  # Need write access for applying patches
            }

            self._container = self._client.containers.create(**container_config)
            self._container.start()

            logger.info(
                "Sandbox container created",
                extra={"container_id": self._container.short_id},
            )

        except Exception as e:
            logger.error(f"Failed to create container: {e}")
            raise SandboxError(f"Failed to create sandbox: {e}")

    async def run_command(
        self,
        command: str,
        timeout: int | None = None,
        workdir: str | None = None,
    ) -> CommandResult:
        """
        Run a command in the sandbox.

        Args:
            command: Command to run
            timeout: Timeout in seconds (uses config default if not provided)
            workdir: Working directory (uses config default if not provided)

        Returns:
            CommandResult with output and exit code
        """
        if self._container is None:
            raise SandboxError("Sandbox not created. Call create() first.")

        timeout = timeout or self.config.timeout_seconds
        workdir = workdir or self.config.working_dir

        logger.debug(f"Running command in sandbox: {command}")

        import time

        start_time = time.time()
        timed_out = False

        try:
            # Run command with exec
            exec_result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._container.exec_run,
                    command,
                    workdir=workdir,
                    demux=True,
                ),
                timeout=timeout,
            )

            exit_code = exec_result.exit_code
            stdout, stderr = exec_result.output

            stdout = (stdout or b"").decode("utf-8", errors="replace")
            stderr = (stderr or b"").decode("utf-8", errors="replace")

        except asyncio.TimeoutError:
            timed_out = True
            exit_code = -1
            stdout = ""
            stderr = f"Command timed out after {timeout} seconds"
            logger.warning(
                f"Command timed out: {command[:50]}...",
                extra={"timeout": timeout},
            )

        duration = time.time() - start_time

        return CommandResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            timed_out=timed_out,
        )

    async def copy_to_container(self, src: Path, dest: str) -> None:
        """Copy a file to the container."""
        if self._container is None:
            raise SandboxError("Sandbox not created")

        import io
        import tarfile

        # Create tar archive
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(str(src), arcname=src.name)
        tar_stream.seek(0)

        # Put archive to container
        self._container.put_archive(dest, tar_stream)

    async def get_logs(self) -> str:
        """Get container logs."""
        if self._container is None:
            return ""

        logs = self._container.logs().decode("utf-8", errors="replace")
        return logs

    async def cleanup(self) -> None:
        """Clean up container and temp files."""
        if self._container:
            try:
                logger.info(
                    "Cleaning up sandbox",
                    extra={"container_id": self._container.short_id},
                )
                self._container.stop(timeout=5)
                self._container.remove(force=True)
            except Exception as e:
                logger.warning(f"Failed to cleanup container: {e}")
            finally:
                self._container = None

        if self._temp_dir and self._temp_dir.exists():
            import shutil

            try:
                shutil.rmtree(self._temp_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup temp dir: {e}")
            finally:
                self._temp_dir = None

        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            finally:
                self._client = None

    @property
    def is_running(self) -> bool:
        """Check if container is running."""
        if self._container is None:
            return False
        try:
            self._container.reload()
            return self._container.status == "running"
        except Exception:
            return False


class MockDockerSandbox:
    """Mock sandbox for testing without Docker."""

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()
        self.commands_run: list[str] = []
        self.mock_results: dict[str, CommandResult] = {}

    async def __aenter__(self) -> "MockDockerSandbox":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def create(self, workspace_path: Path | None = None) -> None:
        pass

    async def run_command(
        self,
        command: str,
        timeout: int | None = None,
        workdir: str | None = None,
    ) -> CommandResult:
        self.commands_run.append(command)

        if command in self.mock_results:
            return self.mock_results[command]

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="Mock output",
            stderr="",
            duration_seconds=0.1,
            timed_out=False,
        )

    async def cleanup(self) -> None:
        pass

    @property
    def is_running(self) -> bool:
        return True
