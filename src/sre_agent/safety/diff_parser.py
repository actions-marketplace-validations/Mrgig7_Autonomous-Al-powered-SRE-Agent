from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch


@dataclass(frozen=True)
class ParsedDiffFile:
    path: str
    lines_added: int
    lines_removed: int


@dataclass(frozen=True)
class ParsedDiff:
    files: list[ParsedDiffFile]
    total_files: int
    total_lines_added: int
    total_lines_removed: int
    diff_bytes: int

    def any_path_matches(self, glob_pattern: str) -> bool:
        return any(fnmatch(f.path, glob_pattern) for f in self.files)


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def parse_unified_diff(diff_text: str) -> ParsedDiff:
    diff_bytes = len(diff_text.encode("utf-8"))
    current_file: str | None = None
    per_file_added: dict[str, int] = {}
    per_file_removed: dict[str, int] = {}

    for raw_line in diff_text.splitlines():
        line = raw_line.rstrip("\n")

        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    b_path = b_path[2:]
                current_file = _normalize_path(b_path)
                per_file_added.setdefault(current_file, 0)
                per_file_removed.setdefault(current_file, 0)
            continue

        if line.startswith("+++ "):
            parts = line.split()
            if len(parts) >= 2:
                path_part = parts[1]
                if path_part.startswith("b/"):
                    path_part = path_part[2:]
                if path_part != "/dev/null":
                    current_file = _normalize_path(path_part)
                    per_file_added.setdefault(current_file, 0)
                    per_file_removed.setdefault(current_file, 0)
            continue

        if current_file is None:
            continue

        if line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@"):
            continue

        if line.startswith("+"):
            per_file_added[current_file] = per_file_added.get(current_file, 0) + 1
        elif line.startswith("-"):
            per_file_removed[current_file] = per_file_removed.get(current_file, 0) + 1

    files: list[ParsedDiffFile] = []
    for path in sorted(set(per_file_added.keys()) | set(per_file_removed.keys())):
        files.append(
            ParsedDiffFile(
                path=path,
                lines_added=per_file_added.get(path, 0),
                lines_removed=per_file_removed.get(path, 0),
            )
        )

    return ParsedDiff(
        files=files,
        total_files=len(files),
        total_lines_added=sum(f.lines_added for f in files),
        total_lines_removed=sum(f.lines_removed for f in files),
        diff_bytes=diff_bytes,
    )
