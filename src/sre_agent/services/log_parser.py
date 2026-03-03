"""Log parser for extracting errors, stack traces, and test failures.

Supports multiple programming languages and test frameworks.
"""

import logging
import re
from dataclasses import dataclass

from sre_agent.schemas.context import (
    BuildError,
    ErrorInfo,
    LogLanguage,
    Severity,
    StackFrame,
    StackTrace,
    TestFailure,
)

logger = logging.getLogger(__name__)


@dataclass
class ParsedLogResult:
    """Result of parsing a log file."""

    errors: list[ErrorInfo]
    stack_traces: list[StackTrace]
    test_failures: list[TestFailure]
    build_errors: list[BuildError]
    summary: str


class LogParser:
    """
    Parses CI/CD logs to extract actionable information.

    Supports:
    - Python tracebacks
    - JavaScript/Node.js errors
    - Java exceptions
    - Go panic traces
    - Test failures from various frameworks
    - Build/compilation errors
    """

    # Python traceback patterns
    PYTHON_TRACEBACK_START = re.compile(r"Traceback \(most recent call last\):")
    PYTHON_FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+), in (\w+)')
    PYTHON_EXCEPTION = re.compile(r"^(\w+(?:\.\w+)*(?:Error|Exception|Warning)): (.+)$")

    # JavaScript/Node.js patterns
    JS_ERROR = re.compile(r"^(\w*Error|\w*Exception|TypeError|ReferenceError|SyntaxError): (.+)$")
    JS_STACK_FRAME = re.compile(r"^\s+at (.+?) \(([^:]+):(\d+):(\d+)\)$")
    JS_STACK_FRAME_SIMPLE = re.compile(r"^\s+at ([^:]+):(\d+):(\d+)$")

    # Java patterns
    JAVA_EXCEPTION = re.compile(r"^([\w.]+(?:Exception|Error)): (.+)$")
    JAVA_FRAME = re.compile(r"^\s+at ([\w.$]+)\(([\w]+\.java):(\d+)\)$")
    JAVA_CAUSED_BY = re.compile(r"^Caused by: (.+)$")

    # Go panic patterns
    GO_PANIC = re.compile(r"^panic: (.+)$")
    GO_FRAME = re.compile(r"^\s*([^:]+):(\d+) \+0x[\da-f]+$")

    # Test failure patterns
    PYTEST_FAILURE = re.compile(r"^(FAILED|ERROR) ([\w/.-]+)::(\w+)(?:::(\w+))?")
    JEST_FAILURE = re.compile(r"^\s*●\s+(.+)$")
    JUNIT_FAILURE = re.compile(r"^(?:FAILURE|ERROR): (\w+)\(([^)]+)\)$")
    GO_TEST_FAIL = re.compile(r"^--- FAIL: (\w+) \(([^)]+)\)$")

    # Build error patterns
    GCC_ERROR = re.compile(r"^([^:]+):(\d+):(\d+): (error|warning): (.+)$")
    RUST_ERROR = re.compile(r"^error\[([^\]]+)\]: (.+)$")
    NPM_ERROR = re.compile(r"^npm ERR! (.+)$")

    def parse(self, content: str) -> ParsedLogResult:
        """
        Parse log content and extract all actionable information.

        Args:
            content: Raw log text

        Returns:
            ParsedLogResult with all extracted data
        """
        errors: list[ErrorInfo] = []
        stack_traces: list[StackTrace] = []
        test_failures: list[TestFailure] = []
        build_errors: list[BuildError] = []

        lines = content.split("\n")

        # Extract different types of information
        stack_traces.extend(self._extract_python_tracebacks(lines))
        stack_traces.extend(self._extract_js_errors(lines))
        stack_traces.extend(self._extract_java_exceptions(lines))
        stack_traces.extend(self._extract_go_panics(lines))

        test_failures.extend(self._extract_test_failures(lines))
        build_errors.extend(self._extract_build_errors(lines))
        errors.extend(self._extract_generic_errors(lines))

        # Generate summary
        summary = self._generate_summary(content, errors, stack_traces, test_failures)

        logger.info(
            "Parsed log content",
            extra={
                "errors": len(errors),
                "stack_traces": len(stack_traces),
                "test_failures": len(test_failures),
                "build_errors": len(build_errors),
            },
        )

        return ParsedLogResult(
            errors=errors,
            stack_traces=stack_traces,
            test_failures=test_failures,
            build_errors=build_errors,
            summary=summary,
        )

    def _extract_python_tracebacks(self, lines: list[str]) -> list[StackTrace]:
        """Extract Python tracebacks from log lines."""
        traces = []
        i = 0

        while i < len(lines):
            if self.PYTHON_TRACEBACK_START.match(lines[i]):
                trace_lines = [lines[i]]
                frames: list[StackFrame] = []
                i += 1

                # Collect frames
                while i < len(lines):
                    line = lines[i]
                    frame_match = self.PYTHON_FRAME.match(line)
                    if frame_match:
                        frames.append(
                            StackFrame(
                                file=frame_match.group(1),
                                line=int(frame_match.group(2)),
                                function=frame_match.group(3),
                            )
                        )
                        trace_lines.append(line)
                        # Next line is usually the code
                        if i + 1 < len(lines) and lines[i + 1].startswith("    "):
                            frames[-1].code = lines[i + 1].strip()
                            trace_lines.append(lines[i + 1])
                            i += 1
                    elif self.PYTHON_EXCEPTION.match(line):
                        trace_lines.append(line)
                        exc_match = self.PYTHON_EXCEPTION.match(line)
                        if exc_match:
                            traces.append(
                                StackTrace(
                                    language=LogLanguage.PYTHON,
                                    exception_type=exc_match.group(1),
                                    message=exc_match.group(2),
                                    frames=frames,
                                    raw_text="\n".join(trace_lines),
                                    is_root_cause=len(traces) == 0,
                                )
                            )
                        break
                    elif line.strip() == "" or not line.startswith(" "):
                        break
                    else:
                        trace_lines.append(line)
                    i += 1
            i += 1

        return traces

    def _extract_js_errors(self, lines: list[str]) -> list[StackTrace]:
        """Extract JavaScript/Node.js errors from log lines."""
        traces = []
        i = 0

        while i < len(lines):
            error_match = self.JS_ERROR.match(lines[i])
            if error_match:
                frames: list[StackFrame] = []
                trace_lines = [lines[i]]
                exception_type = error_match.group(1)
                message = error_match.group(2)
                i += 1

                # Collect stack frames
                while i < len(lines):
                    line = lines[i]
                    frame_match = self.JS_STACK_FRAME.match(line)
                    simple_match = self.JS_STACK_FRAME_SIMPLE.match(line)

                    if frame_match:
                        frames.append(
                            StackFrame(
                                function=frame_match.group(1),
                                file=frame_match.group(2),
                                line=int(frame_match.group(3)),
                                column=int(frame_match.group(4)),
                            )
                        )
                        trace_lines.append(line)
                    elif simple_match:
                        frames.append(
                            StackFrame(
                                file=simple_match.group(1),
                                line=int(simple_match.group(2)),
                                column=int(simple_match.group(3)),
                            )
                        )
                        trace_lines.append(line)
                    elif line.strip().startswith("at "):
                        trace_lines.append(line)
                    else:
                        break
                    i += 1

                if frames:
                    traces.append(
                        StackTrace(
                            language=LogLanguage.JAVASCRIPT,
                            exception_type=exception_type,
                            message=message,
                            frames=frames,
                            raw_text="\n".join(trace_lines),
                            is_root_cause=len(traces) == 0,
                        )
                    )
                continue
            i += 1

        return traces

    def _extract_java_exceptions(self, lines: list[str]) -> list[StackTrace]:
        """Extract Java exceptions from log lines."""
        traces = []
        i = 0

        while i < len(lines):
            exc_match = self.JAVA_EXCEPTION.match(lines[i])
            if exc_match:
                frames: list[StackFrame] = []
                trace_lines = [lines[i]]
                exception_type = exc_match.group(1)
                message = exc_match.group(2)
                i += 1

                while i < len(lines):
                    line = lines[i]
                    frame_match = self.JAVA_FRAME.match(line)
                    caused_by = self.JAVA_CAUSED_BY.match(line)

                    if frame_match:
                        full_method = frame_match.group(1)
                        parts = full_method.rsplit(".", 1)
                        frames.append(
                            StackFrame(
                                module=parts[0] if len(parts) > 1 else None,
                                function=parts[-1],
                                file=frame_match.group(2),
                                line=int(frame_match.group(3)),
                            )
                        )
                        trace_lines.append(line)
                    elif caused_by:
                        # New exception in chain, save current and start new
                        traces.append(
                            StackTrace(
                                language=LogLanguage.JAVA,
                                exception_type=exception_type,
                                message=message,
                                frames=frames,
                                raw_text="\n".join(trace_lines),
                            )
                        )
                        # Start new trace
                        frames = []
                        trace_lines = [line]
                        exception_type = caused_by.group(1).split(":")[0]
                        message = caused_by.group(1).split(":", 1)[-1].strip()
                    elif line.strip().startswith("at ") or line.strip().startswith("..."):
                        trace_lines.append(line)
                    else:
                        break
                    i += 1

                if frames:
                    traces.append(
                        StackTrace(
                            language=LogLanguage.JAVA,
                            exception_type=exception_type,
                            message=message,
                            frames=frames,
                            raw_text="\n".join(trace_lines),
                            is_root_cause=True,  # Last in chain is root cause
                        )
                    )
                continue
            i += 1

        return traces

    def _extract_go_panics(self, lines: list[str]) -> list[StackTrace]:
        """Extract Go panic traces from log lines."""
        traces = []
        i = 0

        while i < len(lines):
            panic_match = self.GO_PANIC.match(lines[i])
            if panic_match:
                frames: list[StackFrame] = []
                trace_lines = [lines[i]]
                message = panic_match.group(1)
                i += 1

                while i < len(lines):
                    line = lines[i]
                    frame_match = self.GO_FRAME.match(line)

                    if frame_match or "goroutine" in line.lower():
                        trace_lines.append(line)
                        if frame_match:
                            frames.append(
                                StackFrame(
                                    file=frame_match.group(1),
                                    line=int(frame_match.group(2)),
                                )
                            )
                    elif line.strip() == "":
                        break
                    else:
                        trace_lines.append(line)
                    i += 1

                traces.append(
                    StackTrace(
                        language=LogLanguage.GO,
                        exception_type="panic",
                        message=message,
                        frames=frames,
                        raw_text="\n".join(trace_lines),
                        is_root_cause=True,
                    )
                )
                continue
            i += 1

        return traces

    def _extract_test_failures(self, lines: list[str]) -> list[TestFailure]:
        """Extract test failures from various test frameworks."""
        failures = []

        for i, line in enumerate(lines):
            # pytest
            pytest_match = self.PYTEST_FAILURE.match(line)
            if pytest_match:
                failures.append(
                    TestFailure(
                        test_file=pytest_match.group(2).split("::")[0],
                        test_class=pytest_match.group(3),
                        test_name=pytest_match.group(4) or pytest_match.group(3),
                        error_message=self._get_context(lines, i, after=5),
                    )
                )
                continue

            # Go test
            go_match = self.GO_TEST_FAIL.match(line)
            if go_match:
                failures.append(
                    TestFailure(
                        test_name=go_match.group(1),
                        error_message=self._get_context(lines, i, after=5),
                        duration_seconds=self._parse_duration(go_match.group(2)),
                    )
                )
                continue

            # JUnit
            junit_match = self.JUNIT_FAILURE.match(line)
            if junit_match:
                failures.append(
                    TestFailure(
                        test_name=junit_match.group(1),
                        test_class=junit_match.group(2),
                        error_message=self._get_context(lines, i, after=5),
                    )
                )
                continue

        return failures

    def _extract_build_errors(self, lines: list[str]) -> list[BuildError]:
        """Extract build/compilation errors."""
        errors = []

        for line in lines:
            # GCC/Clang style
            gcc_match = self.GCC_ERROR.match(line)
            if gcc_match:
                errors.append(
                    BuildError(
                        file=gcc_match.group(1),
                        line=int(gcc_match.group(2)),
                        column=int(gcc_match.group(3)),
                        severity=(
                            Severity.ERROR if gcc_match.group(4) == "error" else Severity.WARNING
                        ),
                        message=gcc_match.group(5),
                    )
                )
                continue

            # Rust
            rust_match = self.RUST_ERROR.match(line)
            if rust_match:
                errors.append(
                    BuildError(
                        file="",  # Rust errors usually span multiple lines
                        error_code=rust_match.group(1),
                        message=rust_match.group(2),
                    )
                )
                continue

        return errors

    def _extract_generic_errors(self, lines: list[str]) -> list[ErrorInfo]:
        """Extract generic error patterns."""
        errors = []
        error_patterns = [
            (r"^ERROR[:\s](.+)$", Severity.ERROR),
            (r"^Error[:\s](.+)$", Severity.ERROR),
            (r"^\[ERROR\](.+)$", Severity.ERROR),
            (r"^FATAL[:\s](.+)$", Severity.ERROR),
            (r"^WARN(?:ING)?[:\s](.+)$", Severity.WARNING),
        ]

        for i, line in enumerate(lines):
            normalized = re.sub(r"^\[[^\]]+\]\s*", "", line).strip()
            for pattern, severity in error_patterns:
                match = re.match(pattern, normalized, re.IGNORECASE)
                if match:
                    errors.append(
                        ErrorInfo(
                            error_type="generic",
                            message=match.group(1).strip(),
                            severity=severity,
                            context_lines=self._get_context_lines(lines, i, before=2, after=2),
                        )
                    )
                    break

        return errors

    def _get_context(self, lines: list[str], index: int, after: int = 3) -> str:
        """Get context lines after an index."""
        end = min(index + after + 1, len(lines))
        return "\n".join(lines[index:end])

    def _get_context_lines(
        self,
        lines: list[str],
        index: int,
        before: int = 2,
        after: int = 2,
    ) -> list[str]:
        """Get context lines around an index."""
        start = max(0, index - before)
        end = min(len(lines), index + after + 1)
        return lines[start:end]

    def _parse_duration(self, duration_str: str) -> float | None:
        """Parse duration string to seconds."""
        try:
            if "s" in duration_str:
                return float(duration_str.replace("s", ""))
            return float(duration_str)
        except ValueError:
            return None

    def _generate_summary(
        self,
        content: str,
        errors: list[ErrorInfo],
        stack_traces: list[StackTrace],
        test_failures: list[TestFailure],
    ) -> str:
        """Generate a summary of the log content."""
        lines = content.split("\n")

        # Get first and last lines
        summary_parts = []

        # First 10 lines
        summary_parts.append("=== First 10 lines ===")
        summary_parts.extend(lines[:10])

        # Last 20 lines (usually contain the actual error)
        summary_parts.append("\n=== Last 20 lines ===")
        summary_parts.extend(lines[-20:])

        # Quick stats
        summary_parts.append("\n=== Stats ===")
        summary_parts.append(f"Total lines: {len(lines)}")
        summary_parts.append(f"Errors found: {len(errors)}")
        summary_parts.append(f"Stack traces: {len(stack_traces)}")
        summary_parts.append(f"Test failures: {len(test_failures)}")

        return "\n".join(summary_parts)
