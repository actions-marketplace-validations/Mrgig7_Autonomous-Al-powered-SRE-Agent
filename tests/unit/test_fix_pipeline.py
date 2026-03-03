from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sre_agent.fix_pipeline.orchestrator as orchestrator_module
from sre_agent.fix_pipeline.orchestrator import FixPipelineOrchestrator
from sre_agent.fix_pipeline.store import FixPipelineRunStore
from sre_agent.models.events import CIProvider
from sre_agent.schemas.context import FailureContextBundle
from sre_agent.schemas.fix_plan import FixOperation, FixPlan
from sre_agent.schemas.intelligence import Classification, FailureCategory, RCAHypothesis, RCAResult
from sre_agent.schemas.pr import PRResult, PRStatus
from sre_agent.schemas.validation import ValidationResult, ValidationStatus


class FakeRun:
    def __init__(self, run_id: UUID, event_id: UUID, context: dict, rca: dict):
        self.id = run_id
        self.event_id = event_id
        self.context_json = context
        self.rca_json = rca
        self.run_key = None
        self.attempt_count = 0
        self.blocked_reason = None
        self.plan_json = None
        self.pr_json = None
        self.last_pr_url = None
        self.last_pr_created_at = None


class FakeStore(FixPipelineRunStore):
    def __init__(self, run: FakeRun):
        self._run = run
        self.updates: list[dict] = []

    async def create_run(self, event_id: UUID, context_json=None, rca_json=None) -> UUID:
        raise NotImplementedError

    async def get_run(self, run_id: UUID):
        return self._run if run_id == self._run.id else None

    async def update_run(self, run_id: UUID, **fields):
        self.updates.append(fields)


def _make_context(event_id: UUID) -> FailureContextBundle:
    return FailureContextBundle(
        event_id=event_id,
        repo="acme/repo",
        commit_sha="a" * 40,
        branch="main",
        pipeline_id="p1",
        job_name="test",
        log_summary="ERROR: ModuleNotFoundError: requests",
    )


def _make_rca(event_id: UUID) -> RCAResult:
    return RCAResult(
        event_id=event_id,
        classification=Classification(
            category=FailureCategory.DEPENDENCY,
            confidence=0.9,
            reasoning="missing dependency",
        ),
        primary_hypothesis=RCAHypothesis(
            description="requests is missing",
            confidence=0.8,
            evidence=["ModuleNotFoundError: requests"],
        ),
    )


@pytest.mark.asyncio
async def test_pipeline_happy_path_creates_pr_and_persists(monkeypatch, tmp_path) -> None:
    event_id = uuid4()
    run_id = uuid4()

    context = _make_context(event_id)
    rca = _make_rca(event_id)

    fake_run = FakeRun(run_id, event_id, context.model_dump(), rca.model_dump())
    store = FakeStore(fake_run)

    event = SimpleNamespace(
        id=event_id,
        repo="acme/repo",
        branch="main",
        commit_sha="a" * 40,
        ci_provider=CIProvider.GITHUB_ACTIONS,
        raw_payload={"repository": {"clone_url": "https://github.com/acme/repo.git"}},
    )

    class FakeSession:
        async def get(self, model, pk):
            return event

    @asynccontextmanager
    async def fake_get_async_session():
        yield FakeSession()

    monkeypatch.setattr(orchestrator_module, "get_async_session", fake_get_async_session)

    class FakeRepoManager:
        async def clone(
            self, repo_url: str, branch: str = "main", commit: str | None = None, depth: int = 1
        ):
            (tmp_path / "pyproject.toml").write_text(
                "\n".join(
                    [
                        "[tool.poetry]",
                        'name = "demo"',
                        "",
                        "[tool.poetry.dependencies]",
                        'python = "^3.11"',
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            return tmp_path

        def apply_patch(self, repo_path, diff: str, check_only: bool = False):
            return SimpleNamespace(success=True, error_message=None)

    class FakeValidator:
        async def validate(self, request):
            return ValidationResult(
                fix_id=request.fix_id,
                event_id=request.event_id,
                validation_id="v1",
                status=ValidationStatus.PASSED,
                tests_passed=1,
                tests_failed=0,
                tests_total=1,
            )

    class FakePlanGenerator:
        last_model_name = "fake-model"

        async def generate_plan(self, rca_result, context):
            return FixPlan(
                root_cause="requests missing",
                category="python_missing_dependency",
                confidence=0.7,
                files=["pyproject.toml"],
                operations=[
                    FixOperation(
                        type="add_dependency",
                        file="pyproject.toml",
                        details={"name": "requests", "spec": "^2.31.0"},
                        rationale="import error",
                        evidence=["ModuleNotFoundError: requests"],
                    )
                ],
            )

    created = {"called": False, "label": None}

    class FakePROrchestrator:
        async def create_pr_for_fix(
            self, fix, rca_result, validation, repo_url, base_branch="main"
        ):
            created["called"] = True
            created["label"] = fix.safety_status.pr_label if fix.safety_status else None
            return PRResult(
                status=PRStatus.CREATED,
                branch_name="fix/test",
                base_branch=base_branch,
                fix_id=fix.fix_id,
                event_id=fix.event_id,
                pr_number=1,
                pr_url="https://example/pr/1",
            )

    orch = FixPipelineOrchestrator(store=store)
    orch.repo_manager = FakeRepoManager()
    orch.validator = FakeValidator()
    orch.plan_generator = FakePlanGenerator()
    orch.pr_orchestrator = FakePROrchestrator()

    result = await orch.run(run_id)
    assert result["success"] is True
    assert created["called"] is True
    assert created["label"] in {"safe", "needs-review"}
    assert any("plan_json" in u for u in store.updates)
    assert any("patch_diff" in u for u in store.updates)
    assert any("validation_json" in u for u in store.updates)
    assert any("pr_json" in u for u in store.updates)


@pytest.mark.asyncio
async def test_pipeline_blocks_when_plan_unsafe(monkeypatch) -> None:
    event_id = uuid4()
    run_id = uuid4()
    context = _make_context(event_id)
    rca = _make_rca(event_id)
    store = FakeStore(FakeRun(run_id, event_id, context.model_dump(), rca.model_dump()))

    event = SimpleNamespace(
        id=event_id,
        repo="acme/repo",
        branch="main",
        commit_sha="a" * 40,
        ci_provider=CIProvider.GITHUB_ACTIONS,
        raw_payload={"repository": {"clone_url": "https://github.com/acme/repo.git"}},
    )

    class FakeSession:
        async def get(self, model, pk):
            return event

    @asynccontextmanager
    async def fake_get_async_session():
        yield FakeSession()

    monkeypatch.setattr(orchestrator_module, "get_async_session", fake_get_async_session)

    class FakePlanGenerator:
        last_model_name = "fake-model"

        async def generate_plan(self, rca_result, context):
            return FixPlan(
                root_cause="unsafe",
                category="python_missing_dependency",
                confidence=0.7,
                files=[".github/workflows/ci.yml"],
                operations=[
                    FixOperation(
                        type="modify_code",
                        file=".github/workflows/ci.yml",
                        details={},
                        rationale="x",
                        evidence=["x"],
                    )
                ],
            )

    class NeverCalled:
        def __getattr__(self, item):
            raise AssertionError("Should not be called")

    orch = FixPipelineOrchestrator(store=store)
    orch.plan_generator = FakePlanGenerator()
    orch.repo_manager = NeverCalled()
    orch.validator = NeverCalled()
    orch.pr_orchestrator = NeverCalled()

    result = await orch.run(run_id)
    assert result["success"] is False
    assert result["error"] == "plan_blocked"
