"""Prompt builder for fix generation.

Constructs structured prompts for LLMs to generate code fixes.
"""

import logging

from sre_agent.schemas.context import FailureContextBundle
from sre_agent.schemas.intelligence import RCAResult

logger = logging.getLogger(__name__)


FIX_PROMPT_TEMPLATE = """You are an expert code fix assistant. Generate a minimal, targeted fix for the following CI/CD failure.

## Error Summary
Category: {category}
Error: {error_summary}

## Stack Trace
```
{stack_trace}
```

## Root Cause Analysis
{hypothesis}

Confidence: {confidence:.0%}

## Affected File: {target_file}
```{language}
{file_content}
```

## Instructions
Generate a unified diff that fixes this error. Follow these rules:
1. Only modify what's necessary to fix the error
2. Do not add comments explaining the fix
3. Do not change code formatting or style
4. Do not modify unrelated code
5. Keep the fix minimal and focused

## Output Format
Output ONLY a unified diff in this exact format:

```diff
--- a/{target_file_basename}
+++ b/{target_file_basename}
@@ -line,count +line,count @@
 context line
-removed line
+added line
```

Then provide a brief one-line explanation of the fix.

## Generate Fix Now"""


MULTI_FILE_PROMPT_TEMPLATE = """You are an expert code fix assistant. Generate minimal fixes for the following CI/CD failure.

## Error Summary
Category: {category}
Error: {error_summary}

{stack_trace_section}

## Root Cause Analysis
{hypothesis}

Confidence: {confidence:.0%}

## Affected Files
{file_sections}

## Instructions
Generate unified diffs that fix this error across the affected files. Rules:
1. Only modify what's necessary
2. Create separate diff blocks for each file
3. Keep changes minimal and focused
4. Do not add explanatory comments in code

## Output Format
For each file, output a diff block:

```diff
--- a/path/to/file.ext
+++ b/path/to/file.ext
@@ -line,count +line,count @@
 context
-old
+new
```

Then provide a one-line explanation.

## Generate Fixes Now"""

FIX_PLAN_PROMPT_TEMPLATE = """You are a senior software engineer creating an actionable, minimal fix plan for a CI/CD failure.\n\nReturn JSON ONLY. Do not include markdown. Do not include commentary.\n\n## Inputs\nCategory: {category}\nError: {error_summary}\n\nHypothesis: {hypothesis}\nConfidence: {confidence:.3f}\n\nLog summary:\n{log_summary}\n\nTop evidence snippets:\n{evidence_snippets}\n\nChanged files:\n{changed_files}\n\n## Output Contract\nReturn a single JSON object matching this schema:\n\n{{\n  \"root_cause\": \"string\",\n  \"category\": \"string\",\n  \"confidence\": 0.0,\n  \"files\": [\"path/to/file\"],\n  \"operations\": [\n    {{\n      \"type\": \"add_dependency\" | \"pin_dependency\" | \"update_config\" | \"modify_code\" | \"remove_unused\",\n      \"file\": \"path/to/file\",\n      \"details\": {{}},\n      \"rationale\": \"short string\",\n      \"evidence\": [\"short references to log lines/snippets\"]\n    }}\n  ]\n}}\n\nRules:\n- Output MUST be valid JSON.\n- Include no unknown fields.\n- confidence MUST be between 0 and 1.\n- operations MUST be <= 10.\n- Every operation.file MUST be included in files.\n- Keep diffs minimal: prefer a single-file change when possible.\n\nReturn JSON only."""


CRITIC_PROMPT_TEMPLATE = """You are a strict software reliability critic.\n\nReturn JSON ONLY. Do not include markdown. Do not include commentary.\n\nReview the proposed plan for hallucination risk and reasoning consistency.\n\n## Inputs\nCategory: {category}\nError: {error_summary}\n\nHypothesis: {hypothesis}\nConfidence: {confidence:.3f}\n\nLog summary:\n{log_summary}\n\nTop evidence snippets:\n{evidence_snippets}\n\nPlan JSON:\n{plan_json}\n\n## Output Contract\nReturn a single JSON object:\n\n{{\n  \"allowed\": true,\n  \"hallucination_risk\": 0.0,\n  \"reasoning_consistency\": 1.0,\n  \"issues\": [\n    {{\n      \"code\": \"string_code\",\n      \"severity\": \"info|warn|block\",\n      \"message\": \"short string\",\n      \"evidence_refs\": [\"short references\"]\n    }}\n  ],\n  \"requires_manual_review\": false,\n  \"recommended_label\": \"safe|needs-review\"\n}}\n\nRules:\n- Output MUST be valid JSON.\n- Include no unknown fields.\n- hallucination_risk and reasoning_consistency MUST be between 0 and 1.\n- Set requires_manual_review=true when confidence is weak or evidence does not support operations.\n- If not allowed, provide at least one issue.\n\nReturn JSON only."""


class PromptBuilder:
    """
    Builds structured prompts for fix generation.

    Creates prompts that include:
    - Error context from RCA
    - Relevant code snippets
    - Clear instructions for output format
    """

    def __init__(self, max_file_lines: int = 100, context_lines: int = 10):
        """
        Initialize prompt builder.

        Args:
            max_file_lines: Maximum lines to include from files
            context_lines: Lines of context around error location
        """
        self.max_file_lines = max_file_lines
        self.context_lines = context_lines

    def build_fix_prompt(
        self,
        rca_result: RCAResult,
        context: FailureContextBundle,
        file_contents: dict[str, str],
    ) -> str:
        """
        Build a prompt for generating a fix.

        Args:
            rca_result: RCA analysis result
            context: Failure context bundle
            file_contents: Map of filename to content

        Returns:
            Formatted prompt string
        """
        # Determine primary target file
        target_file = self._get_primary_target(rca_result, context)

        if len(file_contents) > 1:
            return self._build_multi_file_prompt(rca_result, context, file_contents)

        return self._build_single_file_prompt(rca_result, context, target_file, file_contents)

    def build_fix_plan_prompt(
        self,
        rca_result: RCAResult,
        context: FailureContextBundle,
        max_evidence: int = 6,
    ) -> str:
        error_summary = self._get_error_summary(rca_result, context)
        log_summary = (context.log_summary or "").strip()

        evidence_lines: list[str] = []
        if context.errors:
            for e in context.errors[: max_evidence // 2]:
                evidence_lines.append(f"{e.severity.value}: {e.message}")
        if context.test_failures:
            for tf in context.test_failures[: max_evidence // 2]:
                evidence_lines.append(f"test_failure: {tf.test_name} - {tf.error_message}")
        if context.build_errors and len(evidence_lines) < max_evidence:
            for be in context.build_errors[: max_evidence - len(evidence_lines)]:
                evidence_lines.append(f"build_error: {be.file}:{be.line} - {be.message}")

        evidence_snippets = (
            "\n".join(f"- {line}" for line in evidence_lines) if evidence_lines else "- (none)"
        )

        changed_files = (
            "\n".join(f"- {cf.filename} ({cf.status})" for cf in context.changed_files[:20])
            if context.changed_files
            else "- (none)"
        )

        return FIX_PLAN_PROMPT_TEMPLATE.format(
            category=rca_result.classification.category.value,
            error_summary=error_summary,
            hypothesis=rca_result.primary_hypothesis.description,
            confidence=rca_result.primary_hypothesis.confidence,
            log_summary=log_summary or "(none)",
            evidence_snippets=evidence_snippets,
            changed_files=changed_files,
        )

    def build_critic_prompt(
        self,
        *,
        rca_result: RCAResult,
        context: FailureContextBundle,
        plan_json: str,
        max_evidence: int = 6,
    ) -> str:
        error_summary = self._get_error_summary(rca_result, context)
        log_summary = (context.log_summary or "").strip()

        evidence_lines: list[str] = []
        if context.errors:
            for e in context.errors[: max_evidence // 2]:
                evidence_lines.append(f"{e.severity.value}: {e.message}")
        if context.test_failures:
            for tf in context.test_failures[: max_evidence // 2]:
                evidence_lines.append(f"test_failure: {tf.test_name} - {tf.error_message}")
        if context.build_errors and len(evidence_lines) < max_evidence:
            for be in context.build_errors[: max_evidence - len(evidence_lines)]:
                evidence_lines.append(f"build_error: {be.file}:{be.line} - {be.message}")

        evidence_snippets = (
            "\n".join(f"- {line}" for line in evidence_lines) if evidence_lines else "- (none)"
        )

        return CRITIC_PROMPT_TEMPLATE.format(
            category=rca_result.classification.category.value,
            error_summary=error_summary,
            hypothesis=rca_result.primary_hypothesis.description,
            confidence=rca_result.primary_hypothesis.confidence,
            log_summary=log_summary or "(none)",
            evidence_snippets=evidence_snippets,
            plan_json=plan_json,
        )

    def _build_single_file_prompt(
        self,
        rca_result: RCAResult,
        context: FailureContextBundle,
        target_file: str,
        file_contents: dict[str, str],
    ) -> str:
        """Build prompt for single file fix."""
        # Get file content
        content = file_contents.get(target_file, "")
        if not content:
            # Try to find matching file
            for fname, fcontent in file_contents.items():
                if target_file in fname or fname in target_file:
                    content = fcontent
                    target_file = fname
                    break

        # Get error info
        error_summary = self._get_error_summary(rca_result, context)
        stack_trace = self._get_stack_trace(context)

        # Focus content around error location
        focused_content = self._focus_content(content, context, target_file)

        # Detect language
        language = self._detect_language(target_file)

        return FIX_PROMPT_TEMPLATE.format(
            category=rca_result.classification.category.value,
            error_summary=error_summary,
            stack_trace=stack_trace,
            hypothesis=rca_result.primary_hypothesis.description,
            confidence=rca_result.primary_hypothesis.confidence,
            target_file=target_file,
            target_file_basename=target_file.split("/")[-1].split("\\")[-1],
            language=language,
            file_content=focused_content,
        )

    def _build_multi_file_prompt(
        self,
        rca_result: RCAResult,
        context: FailureContextBundle,
        file_contents: dict[str, str],
    ) -> str:
        """Build prompt for multi-file fix."""
        error_summary = self._get_error_summary(rca_result, context)

        # Build stack trace section
        stack_trace = self._get_stack_trace(context)
        stack_trace_section = ""
        if stack_trace:
            stack_trace_section = f"## Stack Trace\n```\n{stack_trace}\n```\n"

        # Build file sections
        file_sections = []
        for filename, content in list(file_contents.items())[:3]:  # Limit to 3 files
            language = self._detect_language(filename)
            focused = self._focus_content(content, context, filename)
            file_sections.append(f"### {filename}\n```{language}\n{focused}\n```")

        return MULTI_FILE_PROMPT_TEMPLATE.format(
            category=rca_result.classification.category.value,
            error_summary=error_summary,
            stack_trace_section=stack_trace_section,
            hypothesis=rca_result.primary_hypothesis.description,
            confidence=rca_result.primary_hypothesis.confidence,
            file_sections="\n\n".join(file_sections),
        )

    def _get_primary_target(
        self,
        rca_result: RCAResult,
        context: FailureContextBundle,
    ) -> str:
        """Determine primary target file for fix."""
        # First choice: file from affected files analysis
        if rca_result.affected_files:
            return rca_result.affected_files[0].filename

        # Second choice: file from stack trace
        if context.stack_traces:
            for frame in context.stack_traces[0].frames:
                if frame.file and not self._is_library_file(frame.file):
                    return frame.file

        # Third choice: changed file
        if context.changed_files:
            return context.changed_files[0].filename

        return "unknown"

    def _get_error_summary(
        self,
        rca_result: RCAResult,
        context: FailureContextBundle,
    ) -> str:
        """Get a concise error summary."""
        parts = []

        # Primary exception
        if context.stack_traces:
            trace = context.stack_traces[0]
            parts.append(f"{trace.exception_type}: {trace.message}")

        # Add test failure info
        if context.test_failures:
            failure = context.test_failures[0]
            parts.append(f"Test: {failure.test_name}")

        if not parts:
            parts.append(rca_result.classification.reasoning)

        return " | ".join(parts[:2])

    def _get_stack_trace(self, context: FailureContextBundle) -> str:
        """Get formatted stack trace."""
        if not context.stack_traces:
            return "No stack trace available"

        trace = context.stack_traces[0]
        lines = [f"{trace.exception_type}: {trace.message}"]

        for frame in trace.frames[:5]:  # Limit frames
            location = f"  at {frame.file}"
            if frame.line:
                location += f":{frame.line}"
            if frame.function:
                location += f" in {frame.function}"
            lines.append(location)

        return "\n".join(lines)

    def _focus_content(
        self,
        content: str,
        context: FailureContextBundle,
        target_file: str,
    ) -> str:
        """Focus content around error location."""
        lines = content.split("\n")

        if len(lines) <= self.max_file_lines:
            return content

        # Find error line from stack trace
        error_line = None
        for trace in context.stack_traces:
            for frame in trace.frames:
                if frame.file and frame.file in target_file and frame.line:
                    error_line = frame.line
                    break
            if error_line:
                break

        if error_line:
            # Show context around error
            start = max(0, error_line - self.context_lines - 1)
            end = min(len(lines), error_line + self.context_lines)
            focused_lines = lines[start:end]

            # Add line numbers
            numbered = []
            for i, line in enumerate(focused_lines, start=start + 1):
                marker = ">>>" if i == error_line else "   "
                numbered.append(f"{marker} {i:4d} | {line}")

            return "\n".join(numbered)

        # No error line, show first N lines
        return "\n".join(lines[: self.max_file_lines])

    def _detect_language(self, filename: str) -> str:
        """Detect programming language from filename."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".jsx": "javascript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".rb": "ruby",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".cs": "csharp",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".json": "json",
            ".md": "markdown",
        }

        for ext, lang in ext_map.items():
            if filename.endswith(ext):
                return lang

        return "text"

    def _is_library_file(self, filepath: str) -> bool:
        """Check if file is a library/vendor file."""
        patterns = [
            "node_modules",
            "site-packages",
            "vendor",
            ".venv",
            "/usr/lib",
        ]
        return any(p in filepath for p in patterns)
