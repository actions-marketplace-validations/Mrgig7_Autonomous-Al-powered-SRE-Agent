"""Test runner for sandbox execution.

Detects test frameworks and runs tests in isolated containers.
"""

import logging
import re
from pathlib import Path

from sre_agent.sandbox.docker_sandbox import DockerSandbox
from sre_agent.schemas.validation import (
    CommandResult,
    TestFramework,
    TestResult,
)

logger = logging.getLogger(__name__)


class TestRunner:
    """
    Runs tests inside sandbox containers.

    Features:
    - Auto-detect test framework
    - Parse test results
    - Support for multiple languages
    """

    # Framework detection patterns
    FRAMEWORK_DETECTION = {
        TestFramework.PYTEST: ["pytest.ini", "pyproject.toml", "setup.py", "conftest.py"],
        TestFramework.JEST: ["jest.config.js", "jest.config.ts"],
        TestFramework.MOCHA: ["mocharc.js", "mocharc.json", ".mocharc.yml"],
        TestFramework.GO_TEST: ["go.mod"],
        TestFramework.MAVEN: ["pom.xml"],
        TestFramework.GRADLE: ["build.gradle", "build.gradle.kts"],
        TestFramework.CARGO: ["Cargo.toml"],
        TestFramework.RSPEC: ["spec/spec_helper.rb", ".rspec"],
    }

    # Test commands by framework
    TEST_COMMANDS = {
        TestFramework.PYTEST: "pip install -e . 2>/dev/null; pip install pytest; pytest -v --tb=short",
        TestFramework.JEST: "npm ci && npm test",
        TestFramework.MOCHA: "npm ci && npm test",
        TestFramework.GO_TEST: "go test -v ./...",
        TestFramework.MAVEN: "mvn test -B",
        TestFramework.GRADLE: "./gradlew test",
        TestFramework.CARGO: "cargo test",
        TestFramework.RSPEC: "bundle install && bundle exec rspec",
    }

    # Install commands by framework
    INSTALL_COMMANDS = {
        TestFramework.PYTEST: "pip install -r requirements.txt 2>/dev/null || pip install -e . 2>/dev/null || true",
        TestFramework.JEST: "npm ci",
        TestFramework.MOCHA: "npm ci",
        TestFramework.GO_TEST: "go mod download",
        TestFramework.MAVEN: "mvn dependency:resolve -B",
        TestFramework.GRADLE: "./gradlew dependencies",
        TestFramework.CARGO: "cargo fetch",
        TestFramework.RSPEC: "bundle install",
    }

    def detect_framework(self, repo_path: Path) -> TestFramework:
        """
        Detect the test framework used in a repository.

        Args:
            repo_path: Path to repository

        Returns:
            Detected TestFramework
        """
        for framework, files in self.FRAMEWORK_DETECTION.items():
            for filename in files:
                if (repo_path / filename).exists():
                    logger.info(
                        f"Detected framework: {framework.value}",
                        extra={"detection_file": filename},
                    )
                    return framework

                # Check in subdirectories for some patterns
                if "/" in filename:
                    if list(repo_path.glob(filename)):
                        return framework

        # Check package.json for jest/mocha
        package_json = repo_path / "package.json"
        if package_json.exists():
            try:
                import json

                data = json.loads(package_json.read_text())
                scripts = data.get("scripts", {})
                deps = {
                    **data.get("dependencies", {}),
                    **data.get("devDependencies", {}),
                }

                if "jest" in deps or "jest" in scripts.get("test", ""):
                    return TestFramework.JEST
                if "mocha" in deps or "mocha" in scripts.get("test", ""):
                    return TestFramework.MOCHA
            except Exception:
                pass

        # Check pyproject.toml for pytest
        pyproject = repo_path / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text()
                if "pytest" in content:
                    return TestFramework.PYTEST
            except Exception:
                pass

        logger.warning("Could not detect test framework")
        return TestFramework.UNKNOWN

    def get_test_command(
        self,
        framework: TestFramework,
        test_filter: str | None = None,
    ) -> str:
        """
        Get the command to run tests.

        Args:
            framework: Test framework
            test_filter: Optional filter for specific tests

        Returns:
            Shell command string
        """
        base_cmd = self.TEST_COMMANDS.get(framework, "echo 'Unknown framework'")

        if test_filter:
            if framework == TestFramework.PYTEST:
                base_cmd = f"pytest -v --tb=short {test_filter}"
            elif framework in (TestFramework.JEST, TestFramework.MOCHA):
                base_cmd = f"npm test -- --grep '{test_filter}'"
            elif framework == TestFramework.GO_TEST:
                base_cmd = f"go test -v -run '{test_filter}' ./..."

        return base_cmd

    def get_install_command(self, framework: TestFramework) -> str:
        """Get dependency installation command."""
        return self.INSTALL_COMMANDS.get(framework, "echo 'No install needed'")

    async def run_tests(
        self,
        sandbox: DockerSandbox,
        framework: TestFramework,
        test_filter: str | None = None,
        timeout: int = 300,
    ) -> tuple[list[TestResult], CommandResult]:
        """
        Run tests in the sandbox.

        Args:
            sandbox: Docker sandbox instance
            framework: Test framework to use
            test_filter: Optional test filter
            timeout: Timeout in seconds

        Returns:
            Tuple of (test results, command result)
        """
        # Install dependencies first
        install_cmd = self.get_install_command(framework)
        logger.info(f"Installing dependencies: {install_cmd}")
        await sandbox.run_command(install_cmd, timeout=120)

        # Run tests
        test_cmd = self.get_test_command(framework, test_filter)
        logger.info(f"Running tests: {test_cmd}")

        result = await sandbox.run_command(test_cmd, timeout=timeout)

        # Parse results
        test_results = self._parse_test_output(
            result.stdout + result.stderr,
            framework,
        )

        return test_results, result

    def _parse_test_output(
        self,
        output: str,
        framework: TestFramework,
    ) -> list[TestResult]:
        """Parse test output to extract individual test results."""
        results = []

        if framework == TestFramework.PYTEST:
            results = self._parse_pytest_output(output)
        elif framework in (TestFramework.JEST, TestFramework.MOCHA):
            results = self._parse_jest_output(output)
        elif framework == TestFramework.GO_TEST:
            results = self._parse_go_test_output(output)

        return results

    def _parse_pytest_output(self, output: str) -> list[TestResult]:
        """Parse pytest verbose output."""
        results = []

        # Pattern: test_file.py::TestClass::test_name PASSED/FAILED
        pattern = re.compile(
            r"^([\w/.-]+::\w+(?:::\w+)?)\s+(PASSED|FAILED|SKIPPED|ERROR)",
            re.MULTILINE,
        )

        for match in pattern.finditer(output):
            test_name = match.group(1)
            status = match.group(2).lower()

            results.append(
                TestResult(
                    name=test_name,
                    status=status,
                )
            )

        # Also look for summary line
        summary_pattern = re.compile(
            r"(\d+) passed(?:.*?(\d+) failed)?(?:.*?(\d+) skipped)?",
            re.IGNORECASE,
        )
        summary_match = summary_pattern.search(output)

        if not results and summary_match:
            # Create summary result
            passed = int(summary_match.group(1) or 0)
            failed = int(summary_match.group(2) or 0)
            skipped = int(summary_match.group(3) or 0)

            for i in range(passed):
                results.append(TestResult(name=f"test_{i}", status="passed"))
            for i in range(failed):
                results.append(TestResult(name=f"test_failed_{i}", status="failed"))
            for i in range(skipped):
                results.append(TestResult(name=f"test_skipped_{i}", status="skipped"))

        return results

    def _parse_jest_output(self, output: str) -> list[TestResult]:
        """Parse Jest output."""
        results = []

        # Pattern: ✓ test name (Xms)
        pass_pattern = re.compile(r"✓\s+(.+?)(?:\s+\((\d+)\s*ms\))?$", re.MULTILINE)
        fail_pattern = re.compile(r"✕\s+(.+?)(?:\s+\((\d+)\s*ms\))?$", re.MULTILINE)

        for match in pass_pattern.finditer(output):
            results.append(
                TestResult(
                    name=match.group(1).strip(),
                    status="passed",
                    duration_seconds=float(match.group(2) or 0) / 1000,
                )
            )

        for match in fail_pattern.finditer(output):
            results.append(
                TestResult(
                    name=match.group(1).strip(),
                    status="failed",
                    duration_seconds=float(match.group(2) or 0) / 1000,
                )
            )

        return results

    def _parse_go_test_output(self, output: str) -> list[TestResult]:
        """Parse Go test output."""
        results = []

        # Pattern: --- PASS: TestName (0.00s)
        pattern = re.compile(
            r"---\s+(PASS|FAIL|SKIP):\s+(\w+)\s+\(([0-9.]+)s\)",
            re.MULTILINE,
        )

        for match in pattern.finditer(output):
            status_map = {"PASS": "passed", "FAIL": "failed", "SKIP": "skipped"}
            results.append(
                TestResult(
                    name=match.group(2),
                    status=status_map.get(match.group(1), "error"),
                    duration_seconds=float(match.group(3)),
                )
            )

        return results
