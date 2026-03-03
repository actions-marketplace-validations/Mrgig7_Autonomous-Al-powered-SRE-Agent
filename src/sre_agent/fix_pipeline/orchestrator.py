from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from uuid import UUID

from sre_agent.adapters.registry import select_adapter
from sre_agent.ai.critic import PlanCritic
from sre_agent.ai.guardrails import FixGuardrails
from sre_agent.ai.plan_generator import PlanGenerator
from sre_agent.artifacts.provenance import build_provenance_artifact
from sre_agent.config import get_settings
from sre_agent.consensus.coordinator import ConsensusCoordinator
from sre_agent.consensus.issue_graph import build_issue_graph
from sre_agent.database import get_async_session
from sre_agent.explainability.evidence_extractor import (
    attach_operation_links,
    extract_evidence_lines,
)
from sre_agent.fix_pipeline.ast_guard import validate_python_ast
from sre_agent.fix_pipeline.patch_generator import PatchGenerator
from sre_agent.fix_pipeline.store import FixPipelineRunStore
from sre_agent.intelligence.rca_engine import RCAEngine
from sre_agent.models.events import PipelineEvent
from sre_agent.models.fix_pipeline import FixPipelineRunStatus
from sre_agent.observability.metrics import (
    METRICS,
    bucket_danger_score,
    observe_consensus_agreement,
    record_auto_merge,
    record_consensus_candidate,
    record_consensus_decision,
    record_critic_decision,
    record_manual_approval,
)
from sre_agent.observability.tracing import start_span
from sre_agent.pr.pr_orchestrator import PROrchestrator
from sre_agent.safety.diff_parser import parse_unified_diff
from sre_agent.safety.policy_models import PlanIntent, PolicyDecision
from sre_agent.safety.runtime import get_policy_engine
from sre_agent.sandbox.repo_manager import RepoManager
from sre_agent.sandbox.validator import ValidationOrchestrator
from sre_agent.schemas.context import FailureContextBundle
from sre_agent.schemas.critic import CriticDecision, CriticIssue
from sre_agent.schemas.fix import (
    FileDiff,
    FixSuggestion,
    GuardrailStatus,
    SafetyStatus,
    SafetyViolation,
)
from sre_agent.schemas.fix_plan import FixPlan
from sre_agent.schemas.intelligence import RCAResult
from sre_agent.schemas.pr import PRResult
from sre_agent.schemas.repository_config import RepositoryRuntimeConfig
from sre_agent.schemas.validation import ValidationRequest, ValidationResult
from sre_agent.services.context_builder import ContextBuilder
from sre_agent.services.dashboard_events import publish_dashboard_event
from sre_agent.services.post_merge_monitor import PostMergeMonitorService

logger = logging.getLogger(__name__)


def _derive_repo_url(event: PipelineEvent) -> str | None:
    repo_info = (event.raw_payload or {}).get("repository") or {}
    for key in ("clone_url", "git_url", "http_url", "http_url_to_repo"):
        if repo_info.get(key):
            return str(repo_info[key])
    if event.ci_provider.value == "github_actions":
        return f"https://github.com/{event.repo}.git"
    return None


def _list_repo_files(repo_path: Path) -> list[str]:
    root = Path(repo_path)
    out: list[str] = []
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue
        out.append(rel)
    return out


def _extract_runtime_config(event: PipelineEvent) -> RepositoryRuntimeConfig:
    raw = event.raw_payload or {}
    if isinstance(raw, dict):
        meta = raw.get("_sre_agent")
        if isinstance(meta, dict):
            cfg = meta.get("repo_config")
            if isinstance(cfg, dict):
                try:
                    return RepositoryRuntimeConfig.model_validate(cfg)
                except Exception:
                    pass
    # Preserve legacy behavior when metadata is unavailable (tests/manual runs).
    return RepositoryRuntimeConfig(automation_mode="auto_pr", protected_paths=[], retry_limit=3)


def _matches_protected_path(path: str, protected_paths: list[str]) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return any(fnmatch(normalized, pat) for pat in protected_paths)


def _default_critic_decision(reason: str) -> CriticDecision:
    return CriticDecision(
        # Critic infra failures should degrade gracefully; explicit critic rejections still block.
        allowed=True,
        hallucination_risk=0.8,
        reasoning_consistency=0.5,
        issues=[
            CriticIssue(
                code="critic_error",
                severity="warn",
                message=reason,
                evidence_refs=[],
            )
        ],
        requires_manual_review=True,
        recommended_label="needs-review",
    )


def _can_auto_merge(*, validation_passed: bool, pr_label: str | None, manual_review: bool) -> bool:
    if not validation_passed:
        return False
    if manual_review:
        return False
    return (pr_label or "").strip().lower() == "safe"


def _split_file_diffs(combined_diff: str) -> list[FileDiff]:
    diffs: list[FileDiff] = []
    current: list[str] = []
    current_file: str | None = None

    for line in combined_diff.splitlines(keepends=True):
        if line.startswith("--- a/"):
            if current_file and current:
                diff_text = "".join(current)
                added, removed = _count_diff_changes(diff_text)
                diffs.append(
                    FileDiff(
                        filename=current_file,
                        diff=diff_text,
                        lines_added=added,
                        lines_removed=removed,
                    )
                )
            current = [line]
            current_file = line[len("--- a/") :].strip()
            continue
        current.append(line)

    if current_file and current:
        diff_text = "".join(current)
        added, removed = _count_diff_changes(diff_text)
        diffs.append(
            FileDiff(
                filename=current_file,
                diff=diff_text,
                lines_added=added,
                lines_removed=removed,
            )
        )
    return diffs


def _count_diff_changes(diff_text: str) -> tuple[int, int]:
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


class FixPipelineOrchestrator:
    def __init__(
        self,
        store: FixPipelineRunStore | None = None,
        plan_generator: PlanGenerator | None = None,
        patch_generator: PatchGenerator | None = None,
        critic: PlanCritic | None = None,
    ):
        self.store = store or FixPipelineRunStore()
        self.plan_generator = plan_generator or PlanGenerator()
        self.patch_generator = patch_generator or PatchGenerator()
        self.critic = critic or PlanCritic()
        self.repo_manager = RepoManager()
        self.guardrails = FixGuardrails()
        self.validator = ValidationOrchestrator()
        self.pr_orchestrator = PROrchestrator()
        self.policy_engine = get_policy_engine()
        self.post_merge_monitor = PostMergeMonitorService()
        self.settings = get_settings()
        self.consensus = ConsensusCoordinator()

    async def run(self, run_id: UUID) -> dict:
        run = await self.store.get_run(run_id)
        if run is None:
            return {"success": False, "error": "run_not_found"}

        async with get_async_session() as session:
            event = await session.get(PipelineEvent, run.event_id)
        if event is None:
            return {"success": False, "error": "event_not_found"}

        timeline: list[dict] = []
        failure_id = str(event.id)
        run_id_str = str(run_id)
        runtime_config = _extract_runtime_config(event)
        automation_mode = runtime_config.automation_mode
        protected_paths = runtime_config.protected_paths
        retry_limit = runtime_config.retry_limit

        async def _emit(stage: str, status: str, metadata: dict | None = None) -> None:
            await publish_dashboard_event(
                event_type="pipeline_stage",
                stage=stage,
                status=status,
                failure_id=failure_id,
                run_id=run_id_str,
                metadata=metadata,
            )

        def _step_start(step: str) -> tuple[int, datetime]:
            started = datetime.now(UTC)
            timeline.append(
                {
                    "step": step,
                    "status": "running",
                    "started_at": started.isoformat(),
                    "completed_at": None,
                    "duration_ms": None,
                }
            )
            return len(timeline) - 1, started

        def _step_end(step_index: int, *, status: str, started: datetime) -> None:
            completed = datetime.now(UTC)
            duration_ms = int((completed - started).total_seconds() * 1000)
            step_name = str(timeline[step_index].get("step") or "unknown")
            timeline[step_index] = {
                **timeline[step_index],
                "status": status,
                "completed_at": completed.isoformat(),
                "duration_ms": duration_ms,
            }
            METRICS.pipeline_stage_duration_seconds.labels(stage=step_name).observe(
                max(0.0, duration_ms / 1000.0)
            )

        repo_path = None
        try:
            await _emit("pipeline", "started", {"repo": event.repo, "branch": event.branch})
            await self.store.update_run(
                run_id,
                automation_mode=automation_mode,
                retry_limit_snapshot=retry_limit,
            )
            ingest_idx, ingest_started = _step_start("ingest")
            context, rca = await self._load_or_build_context(event, run_id)
            _step_end(ingest_idx, status="ok", started=ingest_started)

            issue_graph_idx, issue_graph_started = _step_start("issue_graph")
            issue_graph = build_issue_graph(context=context, rca=rca)
            _step_end(issue_graph_idx, status="ok", started=issue_graph_started)
            await self.store.update_run(
                run_id, issue_graph_json=issue_graph.model_dump(mode="json")
            )
            await _emit(
                "issue_graph",
                "completed",
                {
                    "issues": len(issue_graph.issues),
                    "affected_files": len(issue_graph.affected_files),
                },
            )

            log_text = (
                context.log_content.raw_content
                if context.log_content is not None
                else (context.log_summary or "")
            )
            adapter_idx, adapter_started = _step_start("adapter_select")
            repo_files_hint = [f.filename for f in (context.changed_files or []) if f.filename]
            selected = select_adapter(log_text, repo_files_hint)
            if selected is None:
                _step_end(adapter_idx, status="fail", started=adapter_started)
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PLAN_BLOCKED.value,
                    error_message="No adapter matched this repository/logs",
                )
                await _emit("adapter_select", "failed")
                return {"success": False, "error": "no_adapter"}
            _step_end(adapter_idx, status="ok", started=adapter_started)
            await _emit("adapter_select", "completed", {"adapter": selected.adapter.name})

            await self.store.update_run(
                run_id,
                adapter_name=selected.adapter.name,
                detection_json=selected.detection.model_dump(mode="json"),
            )

            plan_idx, plan_started = _step_start("plan")
            with start_span(
                "generate_plan",
                attributes={
                    "run_id": str(run_id),
                    "failure_id": str(event.id),
                    "run_key": str(getattr(event, "idempotency_key", "") or ""),
                    "language": str(getattr(selected.detection, "language", "") or ""),
                },
            ):
                plan = await self._generate_plan(context, rca, run_id)
            if plan is None:
                _step_end(plan_idx, status="fail", started=plan_started)
                await _emit("plan", "failed")
                return {"success": False, "error": "plan_failed"}
            _step_end(plan_idx, status="ok", started=plan_started)
            await _emit("plan", "completed", {"category": plan.category, "files": len(plan.files)})

            if protected_paths:
                violating = sorted(
                    {path for path in plan.files if _matches_protected_path(path, protected_paths)}
                )
                if violating:
                    await self.store.update_run(
                        run_id,
                        status=FixPipelineRunStatus.PLAN_BLOCKED.value,
                        error_message=f"Plan touches protected paths: {violating}",
                    )
                    await _emit("plan", "blocked", {"reason": "protected_paths"})
                    return {"success": False, "error": "protected_paths_blocked"}

            policy_plan_idx, policy_plan_started = _step_start("policy_plan")
            with start_span(
                "policy_check_plan",
                attributes={
                    "run_id": str(run_id),
                    "failure_id": str(event.id),
                    "run_key": str(getattr(event, "idempotency_key", "") or ""),
                    "category": str(getattr(plan, "category", "") or ""),
                },
            ):
                plan_decision = self.policy_engine.evaluate_plan(
                    PlanIntent(
                        target_files=plan.files,
                        category=plan.category,
                        operation_types=[op.type for op in plan.operations],
                    )
                )
            _step_end(
                policy_plan_idx,
                status="ok" if plan_decision.allowed else "fail",
                started=policy_plan_started,
            )
            await self.store.update_run(
                run_id,
                plan_json=plan.model_dump(),
                plan_policy_json=plan_decision.model_dump(),
            )
            await _emit(
                "policy_plan",
                "completed" if plan_decision.allowed else "failed",
                {
                    "allowed": plan_decision.allowed,
                    "danger_score": plan_decision.danger_score,
                },
            )
            for v in plan_decision.violations:
                METRICS.policy_violations_total.labels(type=str(v.code).split(".")[0]).inc()

            if not plan_decision.allowed:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PLAN_BLOCKED.value,
                    error_message="Plan blocked by safety policy",
                )
                await _emit("plan", "blocked", {"reason": "policy"})
                return {
                    "success": False,
                    "error": "plan_blocked",
                    "policy": plan_decision.model_dump(),
                }

            allowed_categories = selected.adapter.allowed_categories()
            if allowed_categories and plan.category not in allowed_categories:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PLAN_BLOCKED.value,
                    error_message=f"Unsupported plan category: {plan.category}",
                )
                await _emit("plan", "blocked", {"reason": "unsupported_category"})
                return {"success": False, "error": "unsupported_category"}

            allowed_types = selected.adapter.allowed_fix_types()
            op_types = {str(op.type) for op in plan.operations}
            if not op_types.issubset(allowed_types):
                disallowed = sorted(op_types - allowed_types)
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PLAN_BLOCKED.value,
                    error_message=f"Plan used disallowed fix types: {disallowed}",
                )
                await _emit("plan", "blocked", {"reason": "disallowed_fix_types"})
                return {"success": False, "error": "disallowed_fix_types"}

            critic_idx, critic_started = _step_start("critic")
            try:
                critic_decision = await self.critic.review(
                    rca_result=rca,
                    context=context,
                    plan=plan,
                )
            except Exception as exc:
                critic_decision = _default_critic_decision(f"Critic failed: {exc}")
            _step_end(
                critic_idx,
                status="ok" if critic_decision.allowed else "fail",
                started=critic_started,
            )
            record_critic_decision(outcome="allow" if critic_decision.allowed else "block")
            manual_review_required = bool(critic_decision.requires_manual_review)
            await self.store.update_run(
                run_id,
                critic_json=critic_decision.model_dump(),
                manual_review_required=manual_review_required,
            )
            await _emit(
                "critic",
                "completed" if critic_decision.allowed else "failed",
                {
                    "allowed": critic_decision.allowed,
                    "manual_review_required": manual_review_required,
                    "issues": len(critic_decision.issues),
                },
            )
            if not critic_decision.allowed:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PLAN_BLOCKED.value,
                    error_message="Plan rejected by critic",
                )
                await _emit("plan", "blocked", {"reason": "critic_rejected"})
                return {
                    "success": False,
                    "error": "critic_rejected",
                    "critic": critic_decision.model_dump(),
                }

            if self.settings.phase4_consensus_enabled:
                consensus_idx, consensus_started = _step_start("consensus")
                consensus_decision = self.consensus.resolve(
                    issue_graph=issue_graph,
                    plan=plan,
                    critic=critic_decision,
                    plan_decision=plan_decision,
                    min_agreement=self.settings.phase4_consensus_min_agreement,
                    min_confidence=self.settings.phase4_consensus_min_confidence,
                )
                _step_end(
                    consensus_idx,
                    status="ok" if consensus_decision.state == "accepted" else "fail",
                    started=consensus_started,
                )
                for candidate in consensus_decision.candidates:
                    outcome = (
                        "rejected"
                        if any(
                            r.agent_name == candidate.agent_name
                            for r in consensus_decision.rejections
                        )
                        else "accepted"
                    )
                    record_consensus_candidate(agent=candidate.agent_name, outcome=outcome)
                record_consensus_decision(state=consensus_decision.state)
                observe_consensus_agreement(rate=consensus_decision.agreement_rate)

                selected_plan = consensus_decision.selected_plan
                shadow = {
                    "mode": self.settings.phase4_consensus_mode,
                    "executed_plan_source": (
                        "legacy_plan_generator"
                        if self.settings.phase4_consensus_mode == "dual_run"
                        else "consensus"
                    ),
                    "selected_agent": consensus_decision.selected_agent,
                    "agreement_rate": consensus_decision.agreement_rate,
                    "selected_plan_present": selected_plan is not None,
                    "same_as_executed": (
                        selected_plan is not None
                        and selected_plan.model_dump() == plan.model_dump()
                    ),
                }
                await self.store.update_run(
                    run_id,
                    consensus_json=consensus_decision.model_dump(mode="json"),
                    consensus_state=consensus_decision.state,
                    consensus_shadow_diff_json=shadow,
                )
                await _emit(
                    "consensus",
                    "completed" if consensus_decision.state == "accepted" else "failed",
                    {
                        "state": consensus_decision.state,
                        "agreement_rate": consensus_decision.agreement_rate,
                    },
                )
                if self.settings.phase4_consensus_mode == "enforced":
                    if consensus_decision.state != "accepted" or selected_plan is None:
                        await self.store.update_run(
                            run_id,
                            status=FixPipelineRunStatus.PLAN_BLOCKED.value,
                            error_message="Plan rejected by consensus coordinator",
                        )
                        await _emit("plan", "blocked", {"reason": "consensus_rejected"})
                        return {
                            "success": False,
                            "error": "consensus_rejected",
                            "consensus": consensus_decision.model_dump(mode="json"),
                        }
                    plan = selected_plan
                    await self.store.update_run(run_id, plan_json=plan.model_dump(mode="json"))

            await self.store.update_run(run_id, status=FixPipelineRunStatus.PLAN_READY.value)
            await _emit("plan", "ready")

            repo_url = _derive_repo_url(event)
            if not repo_url:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PLAN_BLOCKED.value,
                    error_message="Unsupported repository URL for cloning",
                )
                await _emit("clone", "failed", {"reason": "repo_url_missing"})
                return {"success": False, "error": "repo_url_missing"}

            clone_idx, clone_started = _step_start("clone")
            repo_path = await self.repo_manager.clone(
                repo_url=repo_url,
                branch=event.branch,
                commit=event.commit_sha,
                depth=50,
            )
            _step_end(clone_idx, status="ok", started=clone_started)
            await _emit("clone", "completed")

            repo_files = _list_repo_files(repo_path)
            selected_repo = select_adapter(log_text, repo_files) or selected
            if selected_repo.adapter.name != selected.adapter.name:
                selected = selected_repo
                await self.store.update_run(
                    run_id,
                    adapter_name=selected.adapter.name,
                    detection_json=selected.detection.model_dump(mode="json"),
                )

            patch_idx, patch_started = _step_start("patch")
            with start_span(
                "generate_patch",
                attributes={
                    "run_id": str(run_id),
                    "failure_id": str(event.id),
                    "run_key": str(getattr(event, "idempotency_key", "") or ""),
                    "category": str(getattr(plan, "category", "") or ""),
                },
            ):
                patch = self.patch_generator.generate(repo_path, plan)
            _step_end(patch_idx, status="ok", started=patch_started)
            await _emit("patch", "completed")
            parsed = parse_unified_diff(patch.diff_text)
            touched = {f.path for f in parsed.files}
            plan_files = set(plan.files)
            if touched - plan_files:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PATCH_BLOCKED.value,
                    error_message="Patch touched files outside plan.files",
                    patch_diff=patch.diff_text,
                    patch_stats_json=patch.stats.as_dict(),
                )
                await _emit("patch", "blocked", {"reason": "outside_plan"})
                return {"success": False, "error": "patch_outside_plan"}
            if protected_paths:
                blocked_files = sorted(
                    {path for path in touched if _matches_protected_path(path, protected_paths)}
                )
                if blocked_files:
                    await self.store.update_run(
                        run_id,
                        status=FixPipelineRunStatus.PATCH_BLOCKED.value,
                        error_message=f"Patch touches protected paths: {blocked_files}",
                        patch_diff=patch.diff_text,
                        patch_stats_json=patch.stats.as_dict(),
                    )
                    await _emit("patch", "blocked", {"reason": "protected_paths"})
                    return {"success": False, "error": "protected_paths_blocked"}

            policy_patch_idx, policy_patch_started = _step_start("policy_patch")
            with start_span(
                "policy_check_patch",
                attributes={
                    "run_id": str(run_id),
                    "failure_id": str(event.id),
                    "run_key": str(getattr(event, "idempotency_key", "") or ""),
                },
            ):
                patch_decision = self.policy_engine.evaluate_patch(patch.diff_text)
            _step_end(
                policy_patch_idx,
                status="ok" if patch_decision.allowed else "fail",
                started=policy_patch_started,
            )
            await self.store.update_run(
                run_id,
                patch_diff=patch.diff_text,
                patch_stats_json=patch.stats.as_dict(),
                patch_policy_json=patch_decision.model_dump(),
            )
            await _emit(
                "policy_patch",
                "completed" if patch_decision.allowed else "failed",
                {
                    "allowed": patch_decision.allowed,
                    "danger_score": patch_decision.danger_score,
                },
            )
            for v in patch_decision.violations:
                METRICS.policy_violations_total.labels(type=str(v.code).split(".")[0]).inc()
            METRICS.danger_score_bucket.labels(
                bucket=bucket_danger_score(int(patch_decision.danger_score))
            ).inc()

            if not patch_decision.allowed:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PATCH_BLOCKED.value,
                    error_message="Patch blocked by safety policy",
                )
                await _emit("patch", "blocked", {"reason": "policy"})
                return {
                    "success": False,
                    "error": "patch_blocked",
                    "policy": patch_decision.model_dump(),
                }

            patch_check = self.repo_manager.apply_patch(
                repo_path=repo_path, diff=patch.diff_text, check_only=True
            )
            if not patch_check.success:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PATCH_BLOCKED.value,
                    error_message=f"Patch does not apply cleanly: {patch_check.error_message}",
                )
                await _emit("patch", "blocked", {"reason": "not_applicable"})
                return {"success": False, "error": "patch_not_applicable"}

            patch_apply = self.repo_manager.apply_patch(
                repo_path=repo_path, diff=patch.diff_text, check_only=False
            )
            if not patch_apply.success:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PATCH_BLOCKED.value,
                    error_message=f"Patch apply failed for AST gate: {patch_apply.error_message}",
                )
                await _emit("patch", "blocked", {"reason": "patch_apply_failed"})
                return {"success": False, "error": "patch_apply_failed"}

            ast_idx, ast_started = _step_start("ast_guard")
            ast_result = validate_python_ast(
                repo_path=Path(repo_path), touched_files=sorted(touched)
            )
            _step_end(
                ast_idx,
                status="ok" if ast_result.passed else "fail",
                started=ast_started,
            )
            if not ast_result.passed:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PATCH_BLOCKED.value,
                    error_message=f"AST validation failed: {[i.message for i in ast_result.issues]}",
                )
                await _emit(
                    "ast_guard",
                    "failed",
                    {"checked_files": ast_result.checked_files, "issues": len(ast_result.issues)},
                )
                return {"success": False, "error": "ast_validation_failed"}
            await _emit(
                "ast_guard",
                "completed",
                {"checked_files": ast_result.checked_files},
            )

            fix = self._build_fix_suggestion(
                str(run_id), event.id, plan, patch.diff_text, patch_decision
            )
            guardrail_status: GuardrailStatus = self.guardrails.validate(fix)
            fix.guardrail_status = guardrail_status
            if not guardrail_status.passed:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PATCH_BLOCKED.value,
                    error_message="Patch blocked by guardrails",
                )
                await _emit("patch", "blocked", {"reason": "guardrails"})
                return {"success": False, "error": "guardrails_blocked"}

            await self.store.update_run(run_id, status=FixPipelineRunStatus.PATCH_READY.value)
            await _emit("patch", "ready")

            validate_idx, validate_started = _step_start("validate")
            with start_span(
                "sandbox_validate",
                attributes={
                    "run_id": str(run_id),
                    "failure_id": str(event.id),
                    "run_key": str(getattr(event, "idempotency_key", "") or ""),
                    "adapter": str(selected.adapter.name),
                },
            ):
                validation = await self.validator.validate(
                    ValidationRequest(
                        fix_id=str(run_id),
                        event_id=event.id,
                        repo_url=repo_url,
                        branch=event.branch,
                        commit_sha=event.commit_sha,
                        diff=patch.diff_text,
                        adapter_name=selected.adapter.name,
                        validation_steps=(
                            selected.adapter.build_validation_steps(str(repo_path)) or None
                        ),
                    )
                )
            _step_end(
                validate_idx,
                status="ok" if validation.is_successful else "fail",
                started=validate_started,
            )
            await _emit(
                "validate",
                "completed" if validation.is_successful else "failed",
                {
                    "tests_total": validation.tests_total,
                    "tests_failed": validation.tests_failed,
                    "tests_passed": validation.tests_passed,
                },
            )
            with start_span(
                "run_scans",
                attributes={
                    "run_id": str(run_id),
                    "failure_id": str(event.id),
                    "run_key": str(getattr(event, "idempotency_key", "") or ""),
                    "outcome": "ok" if validation.is_successful else "fail",
                },
            ):
                pass
            scans_status = "skipped"
            if validation.scans:
                scans_status = "ok"
                if validation.scans.gitleaks and validation.scans.gitleaks.status.value in {
                    "fail",
                    "error",
                }:
                    scans_status = "fail"
                if validation.scans.trivy and validation.scans.trivy.status.value in {
                    "fail",
                    "error",
                }:
                    scans_status = "fail"
                if validation.scans.gitleaks:
                    METRICS.scan_findings_total.labels(scanner="gitleaks", severity="UNKNOWN").inc(
                        int(validation.scans.gitleaks.findings_count or 0)
                    )
                    if validation.scans.gitleaks.status.value in {"fail", "error"}:
                        METRICS.scan_fail_total.labels(
                            scanner="gitleaks",
                            reason=(
                                "timeout"
                                if "timeout"
                                in str(validation.scans.gitleaks.error_message or "").lower()
                                else (
                                    "error"
                                    if validation.scans.gitleaks.status.value == "error"
                                    else "unknown"
                                )
                            ),
                        ).inc()
                if validation.scans.trivy:
                    for sev, count in (validation.scans.trivy.severity_counts or {}).items():
                        METRICS.scan_findings_total.labels(
                            scanner="trivy", severity=str(sev).upper() or "UNKNOWN"
                        ).inc(int(count or 0))
                    if validation.scans.trivy.status.value in {"fail", "error"}:
                        METRICS.scan_fail_total.labels(
                            scanner="trivy",
                            reason=(
                                "timeout"
                                if "timeout"
                                in str(validation.scans.trivy.error_message or "").lower()
                                else (
                                    "error"
                                    if validation.scans.trivy.status.value == "error"
                                    else "unknown"
                                )
                            ),
                        ).inc()
            timeline.append(
                {
                    "step": "scans",
                    "status": scans_status,
                    "started_at": None,
                    "completed_at": None,
                    "duration_ms": None,
                }
            )

            sbom = validation.scans.sbom if validation.scans else None
            update_fields: dict = {"validation_json": validation.model_dump(mode="json")}
            if sbom and sbom.path and sbom.sha256 and sbom.size_bytes is not None:
                update_fields.update(
                    {
                        "sbom_path": sbom.path,
                        "sbom_sha256": sbom.sha256,
                        "sbom_size_bytes": sbom.size_bytes,
                    }
                )
            await self.store.update_run(run_id, **update_fields)
            if not validation.is_successful:
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.VALIDATION_FAILED.value,
                    error_message=validation.error_message or "Validation failed",
                )
                await _emit("pipeline", "failed", {"reason": "validation_failed"})
                return {"success": False, "error": "validation_failed"}

            await self.store.update_run(run_id, status=FixPipelineRunStatus.VALIDATION_PASSED.value)
            await _emit("validate", "passed")

            if automation_mode == "suggest" or (
                automation_mode == "auto_merge" and manual_review_required
            ):
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.AWAITING_APPROVAL.value,
                    manual_review_required=manual_review_required,
                )
                await _emit(
                    "approval",
                    "required",
                    {
                        "automation_mode": automation_mode,
                        "manual_review_required": manual_review_required,
                    },
                )
                await _emit("pipeline", "completed", {"awaiting_approval": True})
                return {
                    "success": True,
                    "run_id": str(run_id),
                    "awaiting_approval": True,
                    "automation_mode": automation_mode,
                }

            existing_run = await self.store.get_run(run_id)
            if existing_run and (
                existing_run.last_pr_url
                or (
                    existing_run.pr_json
                    and str(existing_run.pr_json.get("status") or "").lower() == "created"
                )
            ):
                from sre_agent.ops.metrics import inc

                inc(
                    "pr_create_skipped",
                    attributes={"run_id": str(run_id), "reason": "already_created"},
                )
                timeline.append(
                    {
                        "step": "pr_create",
                        "status": "skipped",
                        "started_at": None,
                        "completed_at": None,
                        "duration_ms": None,
                    }
                )
                await self.store.update_run(run_id, status=FixPipelineRunStatus.PR_CREATED.value)
                await _emit("pr_create", "skipped", {"reason": "already_created"})
                await _emit("pipeline", "completed")
                return {"success": True, "run_id": str(run_id), "skipped": "pr_already_created"}

            pr_idx, pr_started = _step_start("pr_create")
            with start_span(
                "create_pr",
                attributes={
                    "run_id": str(run_id),
                    "failure_id": str(event.id),
                    "run_key": str(getattr(event, "idempotency_key", "") or ""),
                    "pr_label": str(getattr(fix.safety_status, "pr_label", "") or ""),
                },
            ):
                pr_result = await self._create_pr_for_fix(
                    fix=fix,
                    rca_result=rca,
                    validation=validation,
                    repo_url=repo_url,
                    base_branch=event.branch,
                    run_id=run_id,
                )
            _step_end(
                pr_idx,
                status="ok" if pr_result.status.value == "created" else "fail",
                started=pr_started,
            )
            await self.store.update_run(
                run_id,
                pr_json=pr_result.model_dump(),
                last_pr_url=pr_result.pr_url,
                last_pr_created_at=pr_result.created_at,
            )

            if pr_result.status.value != "created":
                await self.store.update_run(
                    run_id,
                    status=FixPipelineRunStatus.PR_FAILED.value,
                    error_message=pr_result.error_message or "PR creation failed",
                )
                await _emit("pr_create", "failed", {"reason": pr_result.error_message})
                await _emit("pipeline", "failed", {"reason": "pr_failed"})
                return {"success": False, "error": "pr_failed"}

            METRICS.pr_created_total.labels(
                label=str(getattr(fix.safety_status, "pr_label", "") or "unknown")
            ).inc()
            await self.store.update_run(run_id, status=FixPipelineRunStatus.PR_CREATED.value)
            await _emit("pr_create", "completed", {"pr_url": pr_result.pr_url})

            if automation_mode == "auto_merge":
                can_merge = _can_auto_merge(
                    validation_passed=validation.is_successful,
                    pr_label=getattr(fix.safety_status, "pr_label", None),
                    manual_review=manual_review_required,
                )
                if not can_merge:
                    await self.store.update_run(
                        run_id,
                        status=FixPipelineRunStatus.AWAITING_APPROVAL.value,
                        manual_review_required=True,
                    )
                    await _emit(
                        "approval",
                        "required",
                        {"reason": "auto_merge_gate_not_satisfied"},
                    )
                    await _emit("pipeline", "completed", {"awaiting_approval": True})
                    return {
                        "success": True,
                        "run_id": str(run_id),
                        "pr": pr_result.model_dump(),
                        "awaiting_approval": True,
                    }

                if pr_result.pr_number is None:
                    await self.store.update_run(
                        run_id,
                        status=FixPipelineRunStatus.MERGE_FAILED.value,
                        error_message="PR number missing; cannot auto-merge",
                    )
                    record_auto_merge(outcome="failed")
                    await _emit("merge", "failed", {"reason": "pr_number_missing"})
                    await _emit("pipeline", "failed", {"reason": "merge_failed"})
                    return {"success": False, "error": "merge_failed"}

                merge_ok, merge_result = await self.pr_orchestrator.merge_pr_for_fix(
                    repo_url=repo_url,
                    pr_number=pr_result.pr_number,
                )
                await self.store.update_run(run_id, merge_result_json=merge_result)
                if not merge_ok:
                    await self.store.update_run(
                        run_id,
                        status=FixPipelineRunStatus.MERGE_FAILED.value,
                        error_message=str(merge_result.get("message") or "Auto-merge failed"),
                    )
                    record_auto_merge(outcome="failed")
                    await _emit("merge", "failed", merge_result)
                    await _emit("pipeline", "failed", {"reason": "merge_failed"})
                    return {"success": False, "error": "merge_failed", "merge": merge_result}

                record_auto_merge(outcome="merged")
                await self.store.update_run(run_id, status=FixPipelineRunStatus.MERGED.value)
                await _emit("merge", "completed", merge_result)

                await self.post_merge_monitor.register(
                    run_id=run_id,
                    repo=event.repo,
                    branch=event.branch,
                    pr_number=pr_result.pr_number,
                )
                await _emit(
                    "post_merge", "monitoring", {"repo": event.repo, "branch": event.branch}
                )
                await _emit("pipeline", "completed")
                return {
                    "success": True,
                    "run_id": str(run_id),
                    "pr": pr_result.model_dump(),
                    "merge": merge_result,
                    "monitoring": True,
                }

            await _emit("pipeline", "completed")
            return {"success": True, "run_id": str(run_id), "pr": pr_result.model_dump()}
        finally:
            try:
                latest = await self.store.get_run(run_id)
                if latest is not None:
                    evidence: list[dict] = []
                    if latest.context_json:
                        log_content = (latest.context_json or {}).get("log_content") or {}
                        raw = (log_content or {}).get("raw_content")
                        summary = (latest.context_json or {}).get("log_summary")
                        log_text = str(raw or summary or "")
                        if log_text:
                            extracted = extract_evidence_lines(log_text, max_lines=30)
                            linked = attach_operation_links(
                                extracted,
                                operations=(
                                    (latest.plan_json or {}).get("operations")
                                    if latest.plan_json
                                    else None
                                ),
                            )
                            evidence = [
                                {
                                    "idx": e.idx,
                                    "line": e.line,
                                    "tag": e.tag,
                                    "operation_idx": e.operation_idx,
                                }
                                for e in linked
                            ]

                    with start_span(
                        "persist_artifact",
                        attributes={
                            "run_id": str(run_id),
                            "failure_id": str(getattr(latest, "event_id", "")),
                            "run_key": str(getattr(event, "idempotency_key", "") or ""),
                        },
                    ):
                        artifact = build_provenance_artifact(
                            run_id=latest.id,
                            failure_id=latest.event_id,
                            repo=event.repo,
                            status=str(getattr(latest, "status", "unknown")),
                            started_at=getattr(latest, "created_at", None),
                            error_message=getattr(latest, "error_message", None),
                            plan_json=getattr(latest, "plan_json", None),
                            plan_policy_json=getattr(latest, "plan_policy_json", None),
                            patch_stats_json=getattr(latest, "patch_stats_json", None),
                            patch_policy_json=getattr(latest, "patch_policy_json", None),
                            validation_json=getattr(latest, "validation_json", None),
                            adapter_name=getattr(latest, "adapter_name", None),
                            detection_json=getattr(latest, "detection_json", None),
                            evidence=evidence,
                            timeline=timeline,
                        )
                        await self.store.update_run(
                            run_id, artifact_json=artifact.model_dump(mode="json")
                        )
            except Exception:
                logger.exception("Failed to persist provenance artifact")

            if repo_path is not None:
                try:
                    if hasattr(self.repo_manager, "cleanup"):
                        self.repo_manager.cleanup(repo_path)
                except Exception:
                    logger.exception("Failed to cleanup repo")

    async def _load_or_build_context(
        self, event: PipelineEvent, run_id: UUID
    ) -> tuple[FailureContextBundle, RCAResult]:
        run = await self.store.get_run(run_id)
        if run and run.context_json and run.rca_json:
            return (
                FailureContextBundle.model_validate(run.context_json),
                RCAResult.model_validate(run.rca_json),
            )

        builder = ContextBuilder()
        context = await builder.build_context(event)
        rca_engine = RCAEngine()
        rca = rca_engine.analyze(context)

        await self.store.update_run(
            run_id, context_json=context.model_dump(), rca_json=rca.model_dump()
        )
        return context, rca

    async def _generate_plan(
        self, context: FailureContextBundle, rca: RCAResult, run_id: UUID
    ) -> FixPlan | None:
        try:
            plan = await self.plan_generator.generate_plan(rca_result=rca, context=context)
            return plan
        except Exception as e:
            await self.store.update_run(
                run_id,
                status=FixPipelineRunStatus.PLAN_BLOCKED.value,
                error_message=f"Plan generation failed: {e}",
            )
            return None

    async def approve_and_create_pr(self, run_id: UUID, *, approved_by: str | None = None) -> dict:
        """Approve a paused run and execute PR/merge flow from persisted artifacts."""
        run = await self.store.get_run(run_id)
        if run is None:
            return {"success": False, "error": "run_not_found"}
        if run.status != FixPipelineRunStatus.AWAITING_APPROVAL.value:
            return {
                "success": False,
                "error": "run_not_awaiting_approval",
                "status": run.status,
            }

        async with get_async_session() as session:
            event = await session.get(PipelineEvent, run.event_id)
        if event is None:
            return {"success": False, "error": "event_not_found"}
        repo_url = _derive_repo_url(event)
        if not repo_url:
            return {"success": False, "error": "repo_url_missing"}

        if not run.plan_json or not run.patch_diff or not run.patch_policy_json:
            return {"success": False, "error": "missing_patch_or_plan_data"}
        if not run.rca_json or not run.validation_json:
            return {"success": False, "error": "missing_context_data"}

        plan = FixPlan.model_validate(run.plan_json)
        patch_decision = PolicyDecision.model_validate(run.patch_policy_json)
        rca = RCAResult.model_validate(run.rca_json)
        validation = ValidationResult.model_validate(run.validation_json)
        fix = self._build_fix_suggestion(
            fix_id=str(run_id),
            event_id=event.id,
            plan=plan,
            diff_text=run.patch_diff,
            patch_decision=patch_decision,
        )
        if not validation.is_successful:
            await self.store.update_run(
                run_id,
                status=FixPipelineRunStatus.VALIDATION_FAILED.value,
                error_message="Cannot approve run with failing validation",
            )
            record_manual_approval(outcome="rejected_validation")
            return {"success": False, "error": "validation_failed"}

        pr_result = await self._create_pr_for_fix(
            fix=fix,
            rca_result=rca,
            validation=validation,
            repo_url=repo_url,
            base_branch=event.branch,
            run_id=run_id,
        )
        await self.store.update_run(
            run_id,
            pr_json=pr_result.model_dump(),
            last_pr_url=pr_result.pr_url,
            last_pr_created_at=pr_result.created_at,
            manual_review_required=False,
        )
        if pr_result.status.value != "created":
            await self.store.update_run(
                run_id,
                status=FixPipelineRunStatus.PR_FAILED.value,
                error_message=pr_result.error_message or "PR creation failed after approval",
            )
            record_manual_approval(outcome="failed")
            return {"success": False, "error": "pr_failed"}

        await self.store.update_run(run_id, status=FixPipelineRunStatus.PR_CREATED.value)
        record_manual_approval(outcome="approved")

        automation_mode = str(getattr(run, "automation_mode", "auto_pr") or "auto_pr")
        if automation_mode != "auto_merge":
            return {"success": True, "run_id": str(run_id), "pr": pr_result.model_dump()}

        can_merge = _can_auto_merge(
            validation_passed=validation.is_successful,
            pr_label=getattr(fix.safety_status, "pr_label", None),
            manual_review=False,
        )
        if not can_merge or pr_result.pr_number is None:
            await self.store.update_run(run_id, status=FixPipelineRunStatus.AWAITING_APPROVAL.value)
            return {
                "success": True,
                "run_id": str(run_id),
                "pr": pr_result.model_dump(),
                "awaiting_approval": True,
            }

        merge_ok, merge_result = await self.pr_orchestrator.merge_pr_for_fix(
            repo_url=repo_url, pr_number=pr_result.pr_number
        )
        await self.store.update_run(run_id, merge_result_json=merge_result)
        if not merge_ok:
            await self.store.update_run(
                run_id,
                status=FixPipelineRunStatus.MERGE_FAILED.value,
                error_message=str(merge_result.get("message") or "Auto-merge failed"),
            )
            record_auto_merge(outcome="failed")
            return {"success": False, "error": "merge_failed", "merge": merge_result}

        await self.store.update_run(run_id, status=FixPipelineRunStatus.MERGED.value)
        await self.post_merge_monitor.register(
            run_id=run_id,
            repo=event.repo,
            branch=event.branch,
            pr_number=pr_result.pr_number,
        )
        record_auto_merge(outcome="merged")
        return {
            "success": True,
            "run_id": str(run_id),
            "pr": pr_result.model_dump(),
            "merge": merge_result,
            "monitoring": True,
            "approved_by": approved_by,
        }

    async def _create_pr_for_fix(
        self,
        *,
        fix: FixSuggestion,
        rca_result: RCAResult,
        validation: ValidationResult,
        repo_url: str,
        base_branch: str = "main",
        run_id: UUID | None = None,
    ) -> PRResult:
        try:
            return await self.pr_orchestrator.create_pr_for_fix(
                fix=fix,
                rca_result=rca_result,
                validation=validation,
                repo_url=repo_url,
                base_branch=base_branch,
                run_id=run_id,
            )
        except TypeError as exc:
            if "run_id" not in str(exc):
                raise
            # Backward-compatible path for tests/stubs that don't accept run_id yet.
            return await self.pr_orchestrator.create_pr_for_fix(
                fix=fix,
                rca_result=rca_result,
                validation=validation,
                repo_url=repo_url,
                base_branch=base_branch,
            )

    def _build_fix_suggestion(
        self, fix_id: str, event_id: UUID, plan: FixPlan, diff_text: str, patch_decision
    ) -> FixSuggestion:
        file_diffs = _split_file_diffs(diff_text)
        total_added = sum(d.lines_added for d in file_diffs)
        total_removed = sum(d.lines_removed for d in file_diffs)

        safety_status = SafetyStatus(
            allowed=patch_decision.allowed,
            pr_label=patch_decision.pr_label,
            danger_score=patch_decision.danger_score,
            violations=[
                SafetyViolation(
                    code=v.code,
                    severity=v.severity.value,
                    message=v.message,
                    file_path=v.file_path,
                )
                for v in patch_decision.violations
            ],
            danger_reasons=[r.message for r in patch_decision.danger_reasons],
        )

        summary = f"{plan.category}: {plan.root_cause}".strip()
        explanation = "\n".join(
            [plan.root_cause] + [f"{op.type} {op.file}: {op.rationale}" for op in plan.operations]
        )

        return FixSuggestion(
            event_id=event_id,
            fix_id=fix_id,
            diffs=file_diffs,
            explanation=explanation,
            summary=summary[:200],
            target_files=plan.files,
            confidence=plan.confidence,
            total_lines_added=total_added,
            total_lines_removed=total_removed,
            guardrail_status=GuardrailStatus(passed=True),
            safety_status=safety_status,
            model_used=self.plan_generator.last_model_name or "unknown",
        )


def run_fix_pipeline_sync(run_id: str) -> dict:
    orchestrator = FixPipelineOrchestrator()
    return asyncio.run(orchestrator.run(UUID(run_id)))
