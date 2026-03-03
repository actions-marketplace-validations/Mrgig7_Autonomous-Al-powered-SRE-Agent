"""Output parser for LLM-generated fixes.

Parses diff output from LLM responses.
"""

import logging
import re
from dataclasses import dataclass

from sre_agent.schemas.fix import FileDiff

logger = logging.getLogger(__name__)


@dataclass
class ParsedOutput:
    """Result of parsing LLM output."""

    diffs: list[FileDiff]
    explanation: str
    raw_response: str
    parse_errors: list[str]


class OutputParser:
    """
    Parses LLM output to extract diffs and explanations.

    Handles various output formats from LLMs including:
    - Code blocks with ```diff markers
    - Plain unified diff format
    - Multiple diff blocks for multi-file fixes
    """

    # Pattern to extract diff blocks
    DIFF_BLOCK_PATTERN = re.compile(
        r"```diff\s*\n(.*?)```",
        re.DOTALL,
    )

    # Pattern to extract file headers from diff
    FILE_HEADER_PATTERN = re.compile(
        r"^---\s+[ab]?/?(.+?)(?:\s|$)",
        re.MULTILINE,
    )

    # Pattern for hunk headers
    HUNK_PATTERN = re.compile(
        r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@",
        re.MULTILINE,
    )

    def parse(self, response: str) -> ParsedOutput:
        """
        Parse LLM response to extract diffs and explanation.

        Args:
            response: Raw LLM response text

        Returns:
            ParsedOutput with diffs and explanation
        """
        errors: list[str] = []
        diffs: list[FileDiff] = []

        # Try to extract diff blocks
        diff_blocks = self._extract_diff_blocks(response)

        if not diff_blocks:
            # Try plain diff format
            diff_blocks = self._extract_plain_diffs(response)

        if not diff_blocks:
            errors.append("No diff blocks found in response")

        # Parse each diff block
        for block in diff_blocks:
            try:
                file_diffs = self._parse_diff_block(block)
                diffs.extend(file_diffs)
            except Exception as e:
                errors.append(f"Failed to parse diff block: {e}")

        # Extract explanation (text after the last diff block)
        explanation = self._extract_explanation(response)

        logger.info(
            "Parsed LLM output",
            extra={
                "diff_count": len(diffs),
                "errors": len(errors),
                "has_explanation": bool(explanation),
            },
        )

        return ParsedOutput(
            diffs=diffs,
            explanation=explanation,
            raw_response=response,
            parse_errors=errors,
        )

    def _extract_diff_blocks(self, text: str) -> list[str]:
        """Extract diff blocks from markdown code fences."""
        matches = self.DIFF_BLOCK_PATTERN.findall(text)
        return [m.strip() for m in matches if m.strip()]

    def _extract_plain_diffs(self, text: str) -> list[str]:
        """Extract plain unified diff format without code fences."""
        blocks = []
        current_block: list[str] = []
        in_diff = False

        for line in text.split("\n"):
            if line.startswith("---") and not in_diff:
                in_diff = True
                current_block = [line]
            elif in_diff:
                if line.startswith(("---", "+++", "@@", " ", "-", "+")):
                    current_block.append(line)
                elif line.strip() == "" and current_block:
                    # Empty line might be part of diff or end
                    current_block.append(line)
                else:
                    # End of diff
                    if len(current_block) > 2:  # Minimum valid diff
                        blocks.append("\n".join(current_block))
                    in_diff = False
                    current_block = []

        # Don't forget last block
        if current_block and len(current_block) > 2:
            blocks.append("\n".join(current_block))

        return blocks

    def _parse_diff_block(self, diff_text: str) -> list[FileDiff]:
        """Parse a single diff block into FileDiff objects."""
        diffs = []

        # Split into per-file diffs if multiple files
        file_diffs = self._split_multi_file_diff(diff_text)

        for file_diff in file_diffs:
            filename = self._extract_filename(file_diff)
            lines_added, lines_removed = self._count_changes(file_diff)

            diffs.append(
                FileDiff(
                    filename=filename or "unknown",
                    diff=file_diff,
                    lines_added=lines_added,
                    lines_removed=lines_removed,
                )
            )

        return diffs

    def _split_multi_file_diff(self, diff_text: str) -> list[str]:
        """Split a diff that might contain multiple files."""
        # Find all --- a/file headers
        header_positions = []
        for match in re.finditer(r"^---\s+", diff_text, re.MULTILINE):
            header_positions.append(match.start())

        if len(header_positions) <= 1:
            return [diff_text]

        # Split at each header
        parts = []
        for i, pos in enumerate(header_positions):
            end = header_positions[i + 1] if i + 1 < len(header_positions) else len(diff_text)
            part = diff_text[pos:end].strip()
            if part:
                parts.append(part)

        return parts

    def _extract_filename(self, diff_text: str) -> str | None:
        """Extract filename from diff headers."""
        # Try --- header first
        match = self.FILE_HEADER_PATTERN.search(diff_text)
        if match:
            filename = match.group(1)
            # Clean up common prefixes
            filename = filename.lstrip("a/").lstrip("b/")
            return filename

        # Try +++ header
        for line in diff_text.split("\n"):
            if line.startswith("+++"):
                parts = line.split()
                if len(parts) >= 2:
                    filename = parts[1]
                    return filename.lstrip("a/").lstrip("b/")

        return None

    def _count_changes(self, diff_text: str) -> tuple[int, int]:
        """Count added and removed lines in a diff."""
        added = 0
        removed = 0

        for line in diff_text.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1

        return added, removed

    def _extract_explanation(self, response: str) -> str:
        """Extract explanation text from response."""
        # Remove diff blocks
        text = self.DIFF_BLOCK_PATTERN.sub("", response)

        # Clean up
        lines = []
        for line in text.strip().split("\n"):
            line = line.strip()
            # Skip headers and empty lines at start
            if line and not line.startswith("#"):
                lines.append(line)

        if lines:
            # Take last paragraph as explanation
            return " ".join(lines[-3:])

        return "Fix applied based on RCA analysis"
