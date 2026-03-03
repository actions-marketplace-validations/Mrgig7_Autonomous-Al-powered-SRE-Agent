from __future__ import annotations

from sre_agent.safety.policy_models import PolicyDecision
from sre_agent.schemas.consensus import (
    AgentOutput,
    ConsensusDecision,
    ConsensusRejection,
    IssueGraph,
    ProposedAction,
    ReasoningEdge,
)
from sre_agent.schemas.critic import CriticDecision
from sre_agent.schemas.fix_plan import FixPlan


def _action_from_plan(plan: FixPlan) -> list[ProposedAction]:
    return [
        ProposedAction(
            type=op.type,
            file=op.file,
            rationale=op.rationale,
            evidence=op.evidence,
        )
        for op in plan.operations
    ]


class ConsensusCoordinator:
    """Deterministic coordinator for planner/critic/safety candidate agreement."""

    def build_candidates(
        self,
        *,
        plan: FixPlan,
        critic: CriticDecision,
        plan_decision: PolicyDecision,
    ) -> list[AgentOutput]:
        planner_candidate = AgentOutput(
            agent_name="planner",
            version="v1",
            confidence_score=plan.confidence,
            reasoning_graph=[
                ReasoningEdge(source="root_cause", target=plan.category, relation="explains"),
            ],
            proposed_actions=_action_from_plan(plan),
            metadata={"category": plan.category, "files": plan.files},
        )

        critic_candidate = AgentOutput(
            agent_name="critic",
            version="v1",
            confidence_score=critic.reasoning_consistency,
            reasoning_graph=[
                ReasoningEdge(source="plan", target="critic_review", relation="validated_by"),
            ],
            proposed_actions=_action_from_plan(plan),
            metadata={
                "allowed": critic.allowed,
                "hallucination_risk": critic.hallucination_risk,
                "issue_count": len(critic.issues),
            },
        )

        safety_candidate = AgentOutput(
            agent_name="safety",
            version="v1",
            confidence_score=max(0.0, min(1.0, 1.0 - (plan_decision.danger_score / 100.0))),
            reasoning_graph=[
                ReasoningEdge(source="plan", target="policy_check", relation="evaluated_by"),
            ],
            proposed_actions=[],
            metadata={
                "allowed": plan_decision.allowed,
                "danger_score": plan_decision.danger_score,
                "violation_count": len(plan_decision.violations),
            },
        )
        return [planner_candidate, critic_candidate, safety_candidate]

    def resolve(
        self,
        *,
        issue_graph: IssueGraph,
        plan: FixPlan,
        critic: CriticDecision,
        plan_decision: PolicyDecision,
        min_agreement: float,
        min_confidence: float,
    ) -> ConsensusDecision:
        candidates = self.build_candidates(plan=plan, critic=critic, plan_decision=plan_decision)
        rejections: list[ConsensusRejection] = []
        allowed_agents: set[str] = set()
        affected_set = set(issue_graph.affected_files)

        if not plan_decision.allowed:
            rejections.append(
                ConsensusRejection(
                    reason="safety_veto",
                    agent_name="safety",
                    details="Plan blocked by policy engine",
                )
            )
            return ConsensusDecision(
                state="rejected_safety_veto",
                agreement_rate=0.0,
                selected_agent=None,
                selected_plan=None,
                candidates=candidates,
                rejections=rejections,
                metadata={"candidate_count": len(candidates)},
            )

        for candidate in candidates:
            if candidate.confidence_score < min_confidence:
                rejections.append(
                    ConsensusRejection(
                        reason="low_confidence",
                        agent_name=candidate.agent_name,
                        details=f"confidence={candidate.confidence_score:.3f}",
                    )
                )
                continue

            unsupported_files = sorted(
                {
                    action.file
                    for action in candidate.proposed_actions
                    if affected_set and action.file not in affected_set
                }
            )
            if unsupported_files:
                rejections.append(
                    ConsensusRejection(
                        reason="unsupported_files",
                        agent_name=candidate.agent_name,
                        details=",".join(unsupported_files),
                    )
                )
                continue

            if candidate.agent_name == "critic" and not critic.allowed:
                rejections.append(
                    ConsensusRejection(
                        reason="critic_rejected",
                        agent_name="critic",
                        details="Critic marked plan as not allowed",
                    )
                )
                continue

            allowed_agents.add(candidate.agent_name)

        candidate_count = len(candidates)
        agreement_rate = (len(allowed_agents) / candidate_count) if candidate_count else 0.0
        if agreement_rate < min_agreement:
            return ConsensusDecision(
                state="rejected_low_agreement",
                agreement_rate=agreement_rate,
                selected_agent=None,
                selected_plan=None,
                candidates=candidates,
                rejections=rejections,
                metadata={
                    "candidate_count": candidate_count,
                    "allowed_agents": sorted(allowed_agents),
                },
            )

        if "planner" not in allowed_agents:
            return ConsensusDecision(
                state="rejected_invalid_candidates",
                agreement_rate=agreement_rate,
                selected_agent=None,
                selected_plan=None,
                candidates=candidates,
                rejections=rejections
                + [
                    ConsensusRejection(
                        reason="planner_missing",
                        agent_name="planner",
                        details="Planner candidate not accepted",
                    )
                ],
                metadata={
                    "candidate_count": candidate_count,
                    "allowed_agents": sorted(allowed_agents),
                },
            )

        return ConsensusDecision(
            state="accepted",
            agreement_rate=agreement_rate,
            selected_agent="planner",
            selected_plan=plan,
            candidates=candidates,
            rejections=rejections,
            metadata={
                "candidate_count": candidate_count,
                "allowed_agents": sorted(allowed_agents),
            },
        )
