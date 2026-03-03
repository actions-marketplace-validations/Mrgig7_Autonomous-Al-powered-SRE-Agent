"""Fix generation orchestrator.

Coordinates LLM-based fix generation with guardrails and validation.
"""

import logging
import time
from uuid import uuid4

from sre_agent.ai.guardrails import FixGuardrails
from sre_agent.ai.llm_provider import LLMProvider, get_llm_provider
from sre_agent.ai.output_parser import OutputParser
from sre_agent.ai.prompt_builder import PromptBuilder
from sre_agent.safety.policy_models import PlanIntent
from sre_agent.safety.runtime import get_policy_engine
from sre_agent.schemas.context import FailureContextBundle
from sre_agent.schemas.fix import (
    FixGenerationResponse,
    FixSuggestion,
    GuardrailStatus,
    SafetyStatus,
    SafetyViolation,
)
from sre_agent.schemas.intelligence import RCAResult

logger = logging.getLogger(__name__)


class FixGenerator:
    """
    Orchestrates AI-based fix generation.

    Flow:
    1. Build prompt from RCA result and context
    2. Generate fix using LLM
    3. Parse LLM output
    4. Validate against guardrails
    5. Return fix suggestion
    """

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        prompt_builder: PromptBuilder | None = None,
        output_parser: OutputParser | None = None,
        guardrails: FixGuardrails | None = None,
    ):
        """
        Initialize fix generator.

        Args:
            llm_provider: LLM provider (uses default if not provided)
            prompt_builder: Prompt builder (uses default if not provided)
            output_parser: Output parser (uses default if not provided)
            guardrails: Guardrails (uses default if not provided)
        """
        self._llm_provider = llm_provider
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.output_parser = output_parser or OutputParser()
        self.guardrails = guardrails or FixGuardrails()

    async def generate_fix(
        self,
        rca_result: RCAResult,
        context: FailureContextBundle,
        file_contents: dict[str, str],
        max_tokens: int = 2000,
    ) -> FixGenerationResponse:
        """
        Generate a fix for a failure.

        Args:
            rca_result: Root cause analysis result
            context: Failure context bundle
            file_contents: Map of filename to content
            max_tokens: Maximum tokens for generation

        Returns:
            FixGenerationResponse with fix or error
        """
        start_time = time.time()

        logger.info(
            "Starting fix generation",
            extra={
                "event_id": str(context.event_id),
                "category": rca_result.classification.category.value,
                "files": list(file_contents.keys()),
            },
        )

        try:
            # Build prompt
            prompt = self.prompt_builder.build_fix_prompt(
                rca_result=rca_result,
                context=context,
                file_contents=file_contents,
            )

            # Get LLM provider
            provider = self._llm_provider or get_llm_provider()

            # Generate with LLM
            async with provider:
                response = await provider.generate(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=0.1,  # Low temperature for deterministic fixes
                )

            # Parse output
            parsed = self.output_parser.parse(response)

            if not parsed.diffs:
                return FixGenerationResponse(
                    success=False,
                    error="Failed to parse fix from LLM response",
                    generation_time_seconds=time.time() - start_time,
                )

            # Build fix suggestion
            fix = FixSuggestion(
                event_id=context.event_id,
                fix_id=str(uuid4()),
                diffs=parsed.diffs,
                explanation=parsed.explanation,
                summary=self._generate_summary(parsed.explanation),
                target_files=[d.filename for d in parsed.diffs],
                confidence=self._calculate_confidence(rca_result, parsed),
                total_lines_added=sum(d.lines_added for d in parsed.diffs),
                total_lines_removed=sum(d.lines_removed for d in parsed.diffs),
                guardrail_status=GuardrailStatus(passed=True, violations=[]),
                model_used=provider.model_name,
            )

            # Validate with guardrails
            guardrail_result = self.guardrails.validate(fix)
            fix.guardrail_status = guardrail_result

            policy_engine = get_policy_engine()
            plan_decision = policy_engine.evaluate_plan(PlanIntent(target_files=fix.target_files))
            patch_decision = policy_engine.evaluate_patch(fix.full_diff)

            combined_allowed = plan_decision.allowed and patch_decision.allowed
            combined_violations = plan_decision.violations + patch_decision.violations
            combined_score = max(plan_decision.danger_score, patch_decision.danger_score)
            combined_reasons = [
                r.message for r in (plan_decision.danger_reasons + patch_decision.danger_reasons)
            ]
            combined_label = (
                "safe"
                if combined_allowed and combined_score <= policy_engine.policy.danger.safe_max
                else "needs-review"
            )

            fix.safety_status = SafetyStatus(
                allowed=combined_allowed,
                pr_label=combined_label,
                danger_score=combined_score,
                violations=[
                    SafetyViolation(
                        code=v.code,
                        severity=v.severity.value,
                        message=v.message,
                        file_path=v.file_path,
                    )
                    for v in combined_violations
                ],
                danger_reasons=combined_reasons,
            )

            generation_time = time.time() - start_time

            logger.info(
                "Fix generation complete",
                extra={
                    "event_id": str(context.event_id),
                    "fix_id": fix.fix_id,
                    "files": fix.target_files,
                    "lines_changed": fix.total_lines_added + fix.total_lines_removed,
                    "guardrails_passed": guardrail_result.passed,
                    "policy_allowed": fix.safety_status.allowed if fix.safety_status else None,
                    "danger_score": fix.safety_status.danger_score if fix.safety_status else None,
                    "pr_label": fix.safety_status.pr_label if fix.safety_status else None,
                    "generation_time": generation_time,
                },
            )

            return FixGenerationResponse(
                success=True,
                fix=fix,
                generation_time_seconds=generation_time,
            )

        except Exception as e:
            logger.error(
                "Fix generation failed",
                extra={"event_id": str(context.event_id), "error": str(e)},
                exc_info=True,
            )
            return FixGenerationResponse(
                success=False,
                error=str(e),
                generation_time_seconds=time.time() - start_time,
            )

    def _generate_summary(self, explanation: str) -> str:
        """Generate one-line summary from explanation."""
        # Take first sentence
        if "." in explanation:
            return explanation.split(".")[0] + "."
        return explanation[:100] + "..." if len(explanation) > 100 else explanation

    def _calculate_confidence(self, rca_result: RCAResult, parsed) -> float:
        """Calculate confidence score for the fix."""
        # Base confidence from RCA
        confidence = rca_result.primary_hypothesis.confidence * 0.5

        # Boost if we parsed diffs successfully
        if parsed.diffs:
            confidence += 0.2

        # Boost if no parse errors
        if not parsed.parse_errors:
            confidence += 0.1

        # Boost if we have similar historical incidents
        if rca_result.similar_incidents:
            confidence += 0.1

        return min(confidence, 1.0)


async def generate_fix_for_event(
    event_id: str,
    file_contents: dict[str, str] | None = None,
) -> FixGenerationResponse:
    """
    Generate a fix for a stored event.

    Convenience function that loads event from database,
    runs RCA if needed, and generates a fix.

    Args:
        event_id: Pipeline event ID
        file_contents: Optional file contents (will try to fetch if not provided)

    Returns:
        FixGenerationResponse
    """
    from uuid import UUID

    from sqlalchemy import select

    from sre_agent.database import async_session_factory
    from sre_agent.intelligence.rca_engine import RCAEngine
    from sre_agent.models.events import PipelineEvent
    from sre_agent.services.context_builder import ContextBuilder

    async with async_session_factory() as session:
        # Load event
        stmt = select(PipelineEvent).where(PipelineEvent.id == UUID(event_id))
        result = await session.execute(stmt)
        event = result.scalar_one_or_none()

        if event is None:
            return FixGenerationResponse(
                success=False,
                error="Event not found",
            )

        # Build context
        context_builder = ContextBuilder()
        context = await context_builder.build_context(event)

        # Run RCA
        rca_engine = RCAEngine()
        rca_result = rca_engine.analyze(context)

        # Generate fix
        generator = FixGenerator()
        return await generator.generate_fix(
            rca_result=rca_result,
            context=context,
            file_contents=file_contents or {},
        )
