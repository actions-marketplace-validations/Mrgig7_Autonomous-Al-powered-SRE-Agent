from __future__ import annotations

from sre_agent.schemas.consensus import IssueDependencyLink, IssueGraph, IssueNode
from sre_agent.schemas.context import FailureContextBundle
from sre_agent.schemas.intelligence import RCAResult


def _file_from_location(location: str | None) -> str | None:
    if not location:
        return None
    file_part = str(location).split(":", 1)[0].strip()
    if not file_part:
        return None
    normalized = file_part.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def _severity_key(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"error", "warning", "info"}:
        return normalized
    return "error"


def _add_issue(
    issues: list[IssueNode],
    *,
    issue_id: str,
    message: str,
    severity: str,
    file_paths: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> None:
    issues.append(
        IssueNode(
            issue_id=issue_id,
            message=message.strip() or "unknown_issue",
            severity=_severity_key(severity),
            file_paths=file_paths or [],
            evidence_refs=evidence_refs or [],
        )
    )


def build_issue_graph(*, context: FailureContextBundle, rca: RCAResult) -> IssueGraph:
    """Build a deterministic issue graph from structured context and RCA outputs."""

    issues: list[IssueNode] = []
    severity_levels: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
    affected_files: list[str] = []
    seen_files: set[str] = set()

    def _track_files(paths: list[str]) -> None:
        for path in paths:
            normalized = path.replace("\\", "/")
            normalized = normalized[2:] if normalized.startswith("./") else normalized
            if not normalized or normalized in seen_files:
                continue
            seen_files.add(normalized)
            affected_files.append(normalized)

    for idx, err in enumerate(context.errors):
        location_file = _file_from_location(err.location)
        files = [location_file] if location_file else []
        evidence = err.context_lines[:3] if err.context_lines else [err.message]
        _add_issue(
            issues,
            issue_id=f"error_{idx}",
            message=err.message,
            severity=err.severity.value,
            file_paths=files,
            evidence_refs=evidence,
        )
        severity_levels[_severity_key(err.severity.value)] += 1
        _track_files(files)

    for idx, be in enumerate(context.build_errors):
        _add_issue(
            issues,
            issue_id=f"build_{idx}",
            message=be.message,
            severity=be.severity.value,
            file_paths=[be.file],
            evidence_refs=[f"{be.file}:{be.line or 0}:{be.column or 0}"],
        )
        severity_levels[_severity_key(be.severity.value)] += 1
        _track_files([be.file])

    for idx, tf in enumerate(context.test_failures):
        maybe_files = [p for p in [tf.test_file] if p]
        _add_issue(
            issues,
            issue_id=f"test_{idx}",
            message=tf.error_message,
            severity="error",
            file_paths=maybe_files,
            evidence_refs=[tf.test_name],
        )
        severity_levels["error"] += 1
        _track_files(maybe_files)

    for idx, sf in enumerate(context.stack_traces):
        frame_files = [frame.file for frame in sf.frames if frame.file][:2]
        _add_issue(
            issues,
            issue_id=f"stack_{idx}",
            message=sf.message,
            severity="error",
            file_paths=frame_files,
            evidence_refs=[sf.exception_type],
        )
        severity_levels["error"] += 1
        _track_files(frame_files)

    for idx, af in enumerate(rca.affected_files):
        _track_files([af.filename])
        _add_issue(
            issues,
            issue_id=f"rca_{idx}",
            message=af.reason,
            severity="info",
            file_paths=[af.filename],
            evidence_refs=[af.suggested_action or "rca_affected_file"],
        )
        severity_levels["info"] += 1

    if not issues:
        message = context.log_summary or rca.primary_hypothesis.description or "unknown_issue"
        _add_issue(
            issues,
            issue_id="fallback_0",
            message=message,
            severity="error",
            file_paths=[af.filename for af in rca.affected_files[:3]],
            evidence_refs=[str(context.event_id)],
        )
        severity_levels["error"] += 1
        _track_files([af.filename for af in rca.affected_files[:3]])

    dependency_links: list[IssueDependencyLink] = []
    if len(issues) >= 2:
        for idx in range(1, len(issues)):
            dependency_links.append(
                IssueDependencyLink(
                    source=issues[idx - 1].issue_id,
                    target=issues[idx].issue_id,
                    relation="correlates_with",
                )
            )

    return IssueGraph(
        issues=issues,
        affected_files=sorted(affected_files),
        severity_levels={k: v for k, v in severity_levels.items() if v > 0},
        dependency_links=dependency_links,
    )
