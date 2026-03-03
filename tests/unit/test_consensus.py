from __future__ import annotations

from uuid import uuid4

from sre_agent.consensus.coordinator import ConsensusCoordinator
from sre_agent.consensus.issue_graph import build_issue_graph
from sre_agent.safety.policy_models import PolicyDecision
from sre_agent.schemas.consensus import IssueGraph
from sre_agent.schemas.context import ErrorInfo, FailureContextBundle, Severity
from sre_agent.schemas.critic import CriticDecision, CriticIssue
from sre_agent.schemas.fix_plan import FixOperation, FixPlan
from sre_agent.schemas.intelligence import (
    AffectedFile,
    Classification,
    FailureCategory,
    RCAHypothesis,
    RCAResult,
)


def _context() -> FailureContextBundle:
    return FailureContextBundle(
        event_id=uuid4(),
        repo="acme/widgets",
        commit_sha="a" * 40,
        branch="main",
        pipeline_id="123",
        job_name="build",
        errors=[
            ErrorInfo(
                error_type="ImportError",
                message="cannot import name X",
                location="src/app.py:7",
                severity=Severity.ERROR,
                context_lines=["ImportError: cannot import name X"],
            )
        ],
    )


def _rca(event_id) -> RCAResult:
    return RCAResult(
        event_id=event_id,
        classification=Classification(
            category=FailureCategory.CODE,
            confidence=0.8,
            reasoning="Import error indicates code issue",
            indicators=["ImportError"],
        ),
        primary_hypothesis=RCAHypothesis(
            description="Missing symbol export",
            confidence=0.8,
            evidence=["ImportError in src/app.py"],
        ),
        affected_files=[
            AffectedFile(
                filename="src/app.py",
                relevance_score=0.95,
                reason="Stack trace points here",
                suggested_action="Fix import path",
            )
        ],
    )


def _plan(confidence: float = 0.8) -> FixPlan:
    return FixPlan(
        root_cause="Missing symbol export",
        category="code",
        confidence=confidence,
        files=["src/app.py"],
        operations=[
            FixOperation(
                type="modify_code",
                file="src/app.py",
                rationale="Correct import target",
                evidence=["ImportError in src/app.py"],
                details={"line_hint": 7},
            )
        ],
    )


def _critic(*, allowed: bool = True, consistency: float = 0.9) -> CriticDecision:
    return CriticDecision(
        allowed=allowed,
        hallucination_risk=0.1 if allowed else 0.9,
        reasoning_consistency=consistency,
        issues=(
            []
            if allowed
            else [
                CriticIssue(
                    code="unsupported_change",
                    severity="block",
                    message="Evidence does not support operation",
                    evidence_refs=[],
                )
            ]
        ),
        requires_manual_review=not allowed,
        recommended_label="safe" if allowed else "needs-review",
    )


def _policy(*, allowed: bool = True, danger: int = 10) -> PolicyDecision:
    return PolicyDecision(allowed=allowed, danger_score=danger, violations=[], danger_reasons=[])


def test_build_issue_graph_extracts_deterministic_files() -> None:
    context = _context()
    rca = _rca(context.event_id)

    issue_graph = build_issue_graph(context=context, rca=rca)

    assert isinstance(issue_graph, IssueGraph)
    assert "src/app.py" in issue_graph.affected_files
    assert issue_graph.severity_levels["error"] >= 1
    assert len(issue_graph.issues) >= 1


def test_consensus_accepts_when_threshold_met() -> None:
    context = _context()
    rca = _rca(context.event_id)
    issue_graph = build_issue_graph(context=context, rca=rca)
    coordinator = ConsensusCoordinator()

    decision = coordinator.resolve(
        issue_graph=issue_graph,
        plan=_plan(),
        critic=_critic(allowed=True),
        plan_decision=_policy(allowed=True),
        min_agreement=0.67,
        min_confidence=0.55,
    )

    assert decision.state == "accepted"
    assert decision.selected_agent == "planner"
    assert decision.selected_plan is not None
    assert decision.agreement_rate >= 0.67


def test_consensus_rejects_on_low_agreement() -> None:
    context = _context()
    rca = _rca(context.event_id)
    issue_graph = build_issue_graph(context=context, rca=rca)
    coordinator = ConsensusCoordinator()

    decision = coordinator.resolve(
        issue_graph=issue_graph,
        plan=_plan(confidence=0.4),
        critic=_critic(allowed=False, consistency=0.4),
        plan_decision=_policy(allowed=True),
        min_agreement=0.67,
        min_confidence=0.55,
    )

    assert decision.state == "rejected_low_agreement"
    assert decision.selected_plan is None
    assert decision.agreement_rate < 0.67


def test_consensus_rejects_on_safety_veto() -> None:
    context = _context()
    rca = _rca(context.event_id)
    issue_graph = build_issue_graph(context=context, rca=rca)
    coordinator = ConsensusCoordinator()

    decision = coordinator.resolve(
        issue_graph=issue_graph,
        plan=_plan(),
        critic=_critic(allowed=True),
        plan_decision=_policy(allowed=False, danger=95),
        min_agreement=0.67,
        min_confidence=0.55,
    )

    assert decision.state == "rejected_safety_veto"
    assert decision.selected_plan is None
    assert any(r.reason == "safety_veto" for r in decision.rejections)
