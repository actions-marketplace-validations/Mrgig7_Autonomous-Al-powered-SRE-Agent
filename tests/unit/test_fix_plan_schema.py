import json
from uuid import uuid4

import pytest
from sre_agent.ai.plan_generator import PlanGenerator
from sre_agent.schemas.context import FailureContextBundle
from sre_agent.schemas.fix_plan import FixOperation, FixPlan, FixPlanParseError
from sre_agent.schemas.intelligence import Classification, FailureCategory, RCAHypothesis, RCAResult


def _make_context() -> FailureContextBundle:
    return FailureContextBundle(
        event_id=uuid4(),
        repo="acme/repo",
        commit_sha="a" * 40,
        branch="main",
        pipeline_id="p1",
        job_name="test",
        log_summary="ERROR: something failed",
    )


def _make_rca(event_id) -> RCAResult:
    return RCAResult(
        event_id=event_id,
        classification=Classification(
            category=FailureCategory.DEPENDENCY,
            confidence=0.9,
            reasoning="missing dependency",
        ),
        primary_hypothesis=RCAHypothesis(
            description="Dependency missing at runtime",
            confidence=0.8,
            evidence=["ModuleNotFoundError: foo"],
        ),
    )


def test_fix_plan_forbids_unknown_fields() -> None:
    data = {
        "root_cause": "missing dep",
        "category": "python_missing_dependency",
        "confidence": 0.5,
        "files": ["pyproject.toml"],
        "operations": [
            {
                "type": "add_dependency",
                "file": "pyproject.toml",
                "details": {"name": "requests", "spec": "^2.31.0"},
                "rationale": "needed by import",
                "evidence": ["ModuleNotFoundError: requests"],
                "extra": "nope",
            }
        ],
    }

    with pytest.raises(Exception):
        FixPlan.model_validate(data)


def test_confidence_bounds_enforced() -> None:
    with pytest.raises(Exception):
        FixPlan.model_validate(
            {
                "root_cause": "x",
                "category": "python_missing_dependency",
                "confidence": 1.5,
                "files": ["pyproject.toml"],
                "operations": [],
            }
        )


def test_operation_file_must_be_in_files() -> None:
    with pytest.raises(Exception):
        FixPlan(
            root_cause="x",
            category="python_missing_dependency",
            confidence=0.5,
            files=["pyproject.toml"],
            operations=[
                FixOperation(
                    type="add_dependency",
                    file="README.md",
                    details={"name": "requests", "spec": "^2.31.0"},
                    rationale="x",
                    evidence=[],
                )
            ],
        )


@pytest.mark.asyncio
async def test_plan_generator_retries_and_recovers() -> None:
    class FakeProvider:
        def __init__(self, outputs: list[str]):
            self._outputs = outputs
            self._i = 0
            self.model = "fake"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @property
        def model_name(self) -> str:
            return self.model

        async def generate(
            self, prompt: str, max_tokens: int = 2000, temperature: float = 0.1
        ) -> str:
            out = self._outputs[self._i]
            self._i += 1
            return out

    context = _make_context()
    rca = _make_rca(context.event_id)

    invalid = "not json"
    valid = json.dumps(
        {
            "root_cause": "missing dep",
            "category": "python_missing_dependency",
            "confidence": 0.6,
            "files": ["pyproject.toml"],
            "operations": [
                {
                    "type": "add_dependency",
                    "file": "pyproject.toml",
                    "details": {"name": "requests", "spec": "^2.31.0"},
                    "rationale": "needed",
                    "evidence": ["ModuleNotFoundError"],
                }
            ],
        }
    )

    gen = PlanGenerator(llm_provider=FakeProvider([invalid, valid]))
    plan = await gen.generate_plan(rca_result=rca, context=context)
    assert plan.category == "python_missing_dependency"
    assert plan.files == ["pyproject.toml"]


@pytest.mark.asyncio
async def test_plan_generator_fails_after_retries() -> None:
    class FakeProvider:
        def __init__(self):
            self.model = "fake"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @property
        def model_name(self) -> str:
            return self.model

        async def generate(
            self, prompt: str, max_tokens: int = 2000, temperature: float = 0.1
        ) -> str:
            return "not json"

    context = _make_context()
    rca = _make_rca(context.event_id)
    gen = PlanGenerator(llm_provider=FakeProvider(), max_retries=1)
    with pytest.raises(FixPlanParseError):
        await gen.generate_plan(rca_result=rca, context=context)
