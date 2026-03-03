"""Unit tests for the log parser."""

import pytest
from sre_agent.schemas.context import LogLanguage, Severity
from sre_agent.services.log_parser import LogParser


class TestLogParser:
    """Tests for log parsing functionality."""

    @pytest.fixture
    def parser(self) -> LogParser:
        return LogParser()

    def test_extract_python_traceback(self, parser: LogParser) -> None:
        """Test Python traceback extraction."""
        log = """
Starting test...
Traceback (most recent call last):
  File "/app/test_example.py", line 42, in test_something
    result = function_that_fails()
  File "/app/module.py", line 15, in function_that_fails
    raise ValueError("Something went wrong")
ValueError: Something went wrong
Test finished.
"""
        result = parser.parse(log)

        assert len(result.stack_traces) == 1
        trace = result.stack_traces[0]
        assert trace.language == LogLanguage.PYTHON
        assert trace.exception_type == "ValueError"
        assert "Something went wrong" in trace.message
        assert len(trace.frames) >= 2
        assert trace.frames[0].file == "/app/test_example.py"
        assert trace.frames[0].line == 42

    def test_extract_javascript_error(self, parser: LogParser) -> None:
        """Test JavaScript error extraction."""
        log = """
Running tests...
TypeError: Cannot read property 'foo' of undefined
    at processData (/app/src/utils.js:25:10)
    at Object.<anonymous> (/app/src/index.js:10:5)
Done.
"""
        result = parser.parse(log)

        assert len(result.stack_traces) == 1
        trace = result.stack_traces[0]
        assert trace.language == LogLanguage.JAVASCRIPT
        assert trace.exception_type == "TypeError"
        assert "undefined" in trace.message
        assert len(trace.frames) >= 2

    def test_extract_java_exception(self, parser: LogParser) -> None:
        """Test Java exception extraction."""
        log = """
java.lang.NullPointerException: Cannot invoke method on null object
    at com.example.Service.process(Service.java:45)
    at com.example.Main.run(Main.java:20)
"""
        result = parser.parse(log)

        assert len(result.stack_traces) >= 1
        trace = result.stack_traces[0]
        assert trace.language == LogLanguage.JAVA
        assert "NullPointerException" in trace.exception_type

    def test_extract_go_panic(self, parser: LogParser) -> None:
        """Test Go panic extraction."""
        log = """
panic: runtime error: index out of range [5] with length 3

goroutine 1 [running]:
main.processData(...)
	/app/main.go:42 +0x123
"""
        result = parser.parse(log)

        assert len(result.stack_traces) == 1
        trace = result.stack_traces[0]
        assert trace.language == LogLanguage.GO
        assert "panic" in trace.exception_type
        assert "index out of range" in trace.message

    def test_extract_pytest_failure(self, parser: LogParser) -> None:
        """Test pytest failure extraction."""
        log = """
============================= test session starts ==============================
FAILED tests/test_api.py::TestAPI::test_create_user
AssertionError: Expected 200 but got 500
============================= 1 failed ==============================
"""
        result = parser.parse(log)

        assert len(result.test_failures) >= 1
        failure = result.test_failures[0]
        assert "test_create_user" in failure.test_name or "TestAPI" in failure.test_name

    def test_extract_generic_errors(self, parser: LogParser) -> None:
        """Test generic error pattern extraction."""
        log = """
[2026-01-09 10:00:00] INFO Starting application
[2026-01-09 10:00:01] ERROR: Connection to database failed
[2026-01-09 10:00:02] FATAL: Unable to continue
"""
        result = parser.parse(log)

        error_messages = [e.message for e in result.errors]
        assert any("database" in m.lower() for m in error_messages)

    def test_extract_build_errors(self, parser: LogParser) -> None:
        """Test GCC-style build error extraction."""
        log = """
Compiling...
src/main.c:42:10: error: expected ';' before '}' token
src/main.c:50:5: warning: unused variable 'x'
"""
        result = parser.parse(log)

        assert len(result.build_errors) >= 1
        error = result.build_errors[0]
        assert error.file == "src/main.c"
        assert error.line == 42
        assert error.severity == Severity.ERROR

    def test_summary_generation(self, parser: LogParser) -> None:
        """Test that summary is generated."""
        log = "Line 1\nLine 2\nLine 3\nError occurred"
        result = parser.parse(log)

        assert result.summary is not None
        assert len(result.summary) > 0
        assert "Line 1" in result.summary or "Error" in result.summary

    def test_empty_log(self, parser: LogParser) -> None:
        """Test parsing empty log."""
        result = parser.parse("")

        assert result.errors == []
        assert result.stack_traces == []
        assert result.test_failures == []

    def test_multiple_tracebacks(self, parser: LogParser) -> None:
        """Test extraction of multiple Python tracebacks."""
        log = """
Traceback (most recent call last):
  File "a.py", line 1, in <module>
ValueError: First error

Some other log...

Traceback (most recent call last):
  File "b.py", line 2, in <module>
KeyError: 'missing_key'
"""
        result = parser.parse(log)

        assert len(result.stack_traces) == 2
        assert result.stack_traces[0].exception_type == "ValueError"
        assert result.stack_traces[1].exception_type == "KeyError"
