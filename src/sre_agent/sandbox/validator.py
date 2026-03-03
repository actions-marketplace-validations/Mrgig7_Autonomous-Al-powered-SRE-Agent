"""Validation orchestrator.

Coordinates sandbox creation, patching, testing, and result collection.
"""

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sre_agent.config import get_settings
from sre_agent.safety.runtime import get_policy_engine
from sre_agent.sandbox.docker_sandbox import DockerSandbox, MockDockerSandbox
from sre_agent.sandbox.repo_manager import RepoManager
from sre_agent.sandbox.scanners import generate_sbom, run_gitleaks_scan, run_trivy_scan
from sre_agent.sandbox.test_runner import TestRunner
from sre_agent.schemas.fix import FixSuggestion
from sre_agent.schemas.scans import (
    GitleaksScanResult,
    SbomResult,
    ScanStatus,
    ScanSummary,
    TrivyScanResult,
)
from sre_agent.schemas.validation import (
    SandboxConfig,
    ValidationRequest,
    ValidationResult,
    ValidationStatus,
)

logger = logging.getLogger(__name__)


class ValidationOrchestrator:
    """
    Orchestrates the full validation process.

    Steps:
    1. Clone repository
    2. Apply fix diff
    3. Create sandbox container
    4. Run tests
    5. Collect and return results
    """

    def __init__(
        self,
        repo_manager: RepoManager | None = None,
        test_runner: TestRunner | None = None,
        use_mock_sandbox: bool = False,
    ):
        """
        Initialize orchestrator.

        Args:
            repo_manager: Repository manager instance
            test_runner: Test runner instance
            use_mock_sandbox: Use mock sandbox for testing
        """
        self.repo_manager = repo_manager or RepoManager()
        self.test_runner = test_runner or TestRunner()
        self.use_mock_sandbox = use_mock_sandbox

    async def validate(self, request: ValidationRequest) -> ValidationResult:
        """
        Validate a fix.

        Args:
            request: Validation request

        Returns:
            ValidationResult with test outcomes
        """
        validation_id = str(uuid4())
        start_time = time.time()
        steps: list[str] = []

        result = ValidationResult(
            fix_id=request.fix_id,
            event_id=request.event_id,
            validation_id=validation_id,
            status=ValidationStatus.PENDING,
            docker_image=request.config.docker_image,
        )

        logger.info(
            "Starting validation",
            extra={
                "validation_id": validation_id,
                "fix_id": request.fix_id,
                "repo": request.repo_url,
            },
        )

        repo_path: Path | None = None

        try:
            settings = get_settings()
            policy_engine = get_policy_engine()
            policy_decision = policy_engine.evaluate_patch(request.diff)
            if not policy_decision.allowed:
                result.status = ValidationStatus.ERROR
                violations = ", ".join(
                    f"{v.code}:{v.file_path or '*'}" for v in policy_decision.blocking_violations
                )
                result.error_message = f"Safety policy blocked patch ({violations})"
                result.steps_completed = steps
                return result

            # Step 1: Clone repository
            result.status = ValidationStatus.CLONING
            repo_path = await self.repo_manager.clone(
                repo_url=request.repo_url,
                branch=request.branch,
                commit=request.commit_sha,
            )
            steps.append("clone")

            # Step 2: Apply patch
            result.status = ValidationStatus.PATCHING
            patch_result = self.repo_manager.apply_patch(repo_path, request.diff)

            if not patch_result.success:
                result.status = ValidationStatus.ERROR
                result.error_message = f"Patch failed: {patch_result.error_message}"
                result.steps_completed = steps
                return result

            steps.append("patch")

            framework = None
            if not request.validation_steps:
                framework = self.test_runner.detect_framework(repo_path)
                result.framework_detected = framework
                steps.append("detect_framework")

            # Step 4: Create sandbox
            result.status = ValidationStatus.RUNNING

            sandbox_class = MockDockerSandbox if self.use_mock_sandbox else DockerSandbox
            sandbox = sandbox_class(config=request.config)

            async with sandbox:
                await sandbox.create(workspace_path=repo_path)
                steps.append("create_sandbox")

                if self.use_mock_sandbox:
                    result.scans = ScanSummary(
                        gitleaks=GitleaksScanResult(status=ScanStatus.SKIPPED),
                        trivy=TrivyScanResult(status=ScanStatus.SKIPPED),
                        sbom=SbomResult(status=ScanStatus.SKIPPED),
                    )
                elif settings.enable_scans:
                    steps.append("scans")
                    gitleaks = await run_gitleaks_scan(sandbox)
                    trivy = await run_trivy_scan(sandbox)
                    sbom, _sbom_path = await generate_sbom(sandbox, run_id=validation_id)

                    result.scans = ScanSummary(gitleaks=gitleaks, trivy=trivy, sbom=sbom)

                    scan_errors = [
                        s
                        for s in (gitleaks.status, trivy.status, sbom.status)
                        if s == ScanStatus.ERROR
                    ]
                    if scan_errors:
                        result.status = ValidationStatus.ERROR
                        result.error_message = "One or more scanners failed to run"
                        result.steps_completed = steps
                        return result

                    if settings.fail_on_secrets and gitleaks.findings_count > 0:
                        result.status = ValidationStatus.FAILED
                        result.error_message = "Secrets detected by gitleaks"
                        result.steps_completed = steps
                        return result

                    if trivy.status == ScanStatus.FAIL:
                        result.status = ValidationStatus.FAILED
                        result.error_message = (
                            f"Vulnerabilities detected at severity >= {trivy.threshold}"
                        )
                        result.steps_completed = steps
                        return result
                else:
                    result.scans = ScanSummary(
                        gitleaks=GitleaksScanResult(status=ScanStatus.SKIPPED),
                        trivy=TrivyScanResult(status=ScanStatus.SKIPPED),
                        sbom=SbomResult(status=ScanStatus.SKIPPED),
                    )

                # Step 5: Install and run tests
                if request.validation_steps:
                    result.status = ValidationStatus.RUNNING
                    steps.append("run_steps")
                    combined_logs: list[str] = []
                    if request.adapter_name == "docker":
                        try:
                            import docker
                            from docker.errors import BuildError, DockerException
                        except Exception:
                            result.status = ValidationStatus.ERROR
                            result.error_message = "Docker SDK not available"
                            result.logs = "Docker SDK not available"
                            return result

                        for step in request.validation_steps:
                            if step.command.startswith("docker build"):
                                start_time = time.time()
                                try:
                                    client = docker.from_env()
                                    image, build_logs = client.images.build(
                                        path=str(repo_path),
                                        tag="sre-agent-validate",
                                        rm=True,
                                    )
                                    _ = image
                                    combined_logs.extend(
                                        [
                                            (chunk.get("stream") or "").rstrip()
                                            for chunk in build_logs
                                            if isinstance(chunk, dict)
                                        ]
                                    )
                                except BuildError as e:
                                    combined_logs.append(str(e))
                                    result.status = ValidationStatus.FAILED
                                    result.error_message = f"Step failed: {step.name}"
                                    break
                                except DockerException as e:
                                    combined_logs.append(str(e))
                                    result.status = ValidationStatus.ERROR
                                    result.error_message = "Docker build unavailable"
                                    break
                                finally:
                                    duration = time.time() - start_time
                                    result.execution_time_seconds = duration
                            else:
                                combined_logs.append(f"Unsupported docker step: {step.command}")
                                result.status = ValidationStatus.ERROR
                                result.error_message = f"Unsupported step: {step.name}"
                                break
                    else:
                        for step in request.validation_steps:
                            cmd = await sandbox.run_command(
                                step.command,
                                timeout=step.timeout_seconds or request.config.timeout_seconds,
                                workdir=step.workdir,
                            )
                            combined_logs.append(
                                f"$ {cmd.command}\n{cmd.stdout}\n{cmd.stderr}".strip()
                            )
                            if cmd.timed_out:
                                result.status = ValidationStatus.TIMEOUT
                                result.error_message = f"Step timed out: {step.name}"
                                break
                            if cmd.exit_code != 0:
                                result.status = ValidationStatus.FAILED
                                result.error_message = f"Step failed: {step.name}"
                                break
                    result.logs = "\n\n".join(combined_logs)
                    if result.status == ValidationStatus.RUNNING:
                        result.status = ValidationStatus.PASSED
                else:
                    result.status = ValidationStatus.INSTALLING
                    steps.append("install")

                    result.status = ValidationStatus.RUNNING
                    test_results, cmd_result = await self.test_runner.run_tests(
                        sandbox=sandbox,
                        framework=framework,
                        test_filter=request.test_filter,
                        timeout=request.config.timeout_seconds,
                    )
                    steps.append("run_tests")

                    result.test_results = test_results
                    result.tests_passed = sum(1 for t in test_results if t.status == "passed")
                    result.tests_failed = sum(1 for t in test_results if t.status == "failed")
                    result.tests_skipped = sum(1 for t in test_results if t.status == "skipped")
                    result.tests_total = len(test_results)
                    result.logs = cmd_result.stdout + "\n" + cmd_result.stderr

                    if cmd_result.timed_out:
                        result.status = ValidationStatus.TIMEOUT
                        result.error_message = "Tests timed out"
                    elif cmd_result.exit_code == 0:
                        result.status = ValidationStatus.PASSED
                    else:
                        result.status = ValidationStatus.FAILED

        except Exception as e:
            logger.error(
                "Validation failed",
                extra={"validation_id": validation_id, "error": str(e)},
                exc_info=True,
            )
            result.status = ValidationStatus.ERROR
            result.error_message = str(e)

        finally:
            # Cleanup
            if repo_path:
                self.repo_manager.cleanup(repo_path)
                steps.append("cleanup")

            result.steps_completed = steps
            result.execution_time_seconds = time.time() - start_time
            result.completed_at = datetime.now(UTC)

        logger.info(
            "Validation complete",
            extra={
                "validation_id": validation_id,
                "status": result.status.value,
                "tests_passed": result.tests_passed,
                "tests_failed": result.tests_failed,
                "duration": result.execution_time_seconds,
            },
        )

        return result

    async def validate_fix(
        self,
        fix: FixSuggestion,
        repo_url: str,
        branch: str,
        commit_sha: str,
        config: SandboxConfig | None = None,
    ) -> ValidationResult:
        """
        Convenience method to validate a FixSuggestion directly.

        Args:
            fix: Fix suggestion to validate
            repo_url: Repository URL
            branch: Branch name
            commit_sha: Commit SHA
            config: Optional sandbox config

        Returns:
            ValidationResult
        """
        request = ValidationRequest(
            fix_id=fix.fix_id,
            event_id=fix.event_id,
            repo_url=repo_url,
            branch=branch,
            commit_sha=commit_sha,
            diff=fix.full_diff,
            config=config or SandboxConfig(),
        )
        return await self.validate(request)


async def validate_fix_for_event(
    event_id: str,
    fix_id: str,
) -> ValidationResult:
    """
    Validate a fix for a stored event.

    Loads event and fix from database and runs validation.

    Args:
        event_id: Pipeline event ID
        fix_id: Fix ID to validate

    Returns:
        ValidationResult
    """
    from uuid import UUID

    from sqlalchemy import select

    from sre_agent.database import async_session_factory
    from sre_agent.models.events import PipelineEvent

    async with async_session_factory() as session:
        # Load event
        stmt = select(PipelineEvent).where(PipelineEvent.id == UUID(event_id))
        result = await session.execute(stmt)
        event = result.scalar_one_or_none()

        if event is None:
            return ValidationResult(
                fix_id=fix_id,
                event_id=UUID(event_id),
                validation_id="error",
                status=ValidationStatus.ERROR,
                error_message="Event not found",
            )

        # TODO: Load fix from database
        # For now, return error
        return ValidationResult(
            fix_id=fix_id,
            event_id=UUID(event_id),
            validation_id="not_implemented",
            status=ValidationStatus.ERROR,
            error_message="Fix storage not yet implemented",
        )
